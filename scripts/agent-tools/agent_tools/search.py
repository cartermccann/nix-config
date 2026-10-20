"""Scoped literal ripgrep searches with explicit coverage limits."""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
from pathlib import Path
import shutil
import time

from .runner import run_command


def search(root, pattern, *, limit=40, max_files=2000, max_bytes=33_554_432,
           max_file_bytes=1_048_576, max_output_bytes=65_536, timeout=10,
           hidden=False, follow=False, globs=None):
    result = {"status": "error", "results": [], "partial": False,
              "partial_reasons": [], "selected_files": 0, "selected_bytes": 0,
              "submitted_files": 0, "enumeration_exit_code": None,
              "rg_exit_codes": [], "diagnostics": ""}
    integers = ((limit, 1, 1000), (max_files, 1, 10000),
                (max_bytes, 1, 1_073_741_824), (max_file_bytes, 1, 16_777_216),
                (max_output_bytes, 1024, 1_048_576))
    if (any(isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi
            for v, lo, hi in integers)
            or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 300
            or not isinstance(pattern, str) or not pattern or "\0" in pattern
            or not isinstance(root, (str, os.PathLike))
            or not isinstance(hidden, bool) or not isinstance(follow, bool)
            or (globs is not None and (not isinstance(globs, list)
                or any(not isinstance(g, str) or not g or "\0" in g for g in globs)))):
        result["error_code"] = "invalid_input"
        return result, 2
    try:
        directory = Path(root).expanduser().resolve(strict=True)
        if not directory.is_dir():
            raise ValueError()
    except (OSError, ValueError, RuntimeError):
        result["error_code"] = "invalid_root"
        return result, 2
    result["root"] = str(directory)
    if directory in (Path(directory.anchor), Path.home().resolve()):
        result["error_code"] = "root_too_broad"
        return result, 2
    rg = shutil.which("rg")
    if rg is None:
        result["error_code"] = "executable_not_found"
        return result, 127
    deadline = time.monotonic() + timeout

    def partial(reason):
        result["partial"] = True
        if reason not in result["partial_reasons"]:
            result["partial_reasons"].append(reason)

    args = [rg, "--no-config", "--files", "--null", "--glob", "!.git/**"]
    if hidden:
        args.append("--hidden")
    if follow:
        args.append("--follow")
    for glob in globs or []:
        args.extend(["--glob", glob])
    args.extend(["--", "."])
    enumeration, code = run_command(args, cwd=str(directory),
                                    timeout=max(.001, deadline - time.monotonic()),
                                    max_output_bytes=262_144)
    result["enumeration_exit_code"] = enumeration.get("exit_code")
    if enumeration.get("status") == "error":
        result["error_code"] = enumeration.get("error_code", "enumeration_failed")
        return result, code or 2
    if enumeration.get("timed_out") or enumeration.get("status") == "interrupted":
        partial("enumeration_incomplete")
    if enumeration.get("stdout_truncated"):
        partial("enumeration_output_limit")
    if enumeration.get("stderr_truncated"):
        partial("diagnostics_limit")
    if enumeration.get("exit_code") not in (0, 1, None):
        partial("enumeration_error")
    result["diagnostics"] = enumeration.get("stderr", "")[:4096]
    files = []
    # A truncated final path must never be treated as a complete filename.
    paths = enumeration.get("stdout", "").split("\0")[:-1]
    for name in paths:
        if len(files) >= max_files:
            partial("file_limit")
            break
        if "\ufffd" in name:
            partial("non_utf8_filename")
            continue
        path = directory / name
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(directory):
                partial("outside_root_symlink")
                continue
            if not follow and (path.is_symlink() or resolved != path.absolute()):
                partial("symlink_skipped")
                continue
            stat = path.stat()
            if not path.is_file():
                partial("nonregular_file")
                continue
            if stat.st_size > max_file_bytes:
                partial("file_size_limit")
                continue
            if result["selected_bytes"] + stat.st_size > max_bytes:
                partial("selected_bytes_limit")
                break
        except (OSError, ValueError, RuntimeError):
            partial("file_unavailable")
            continue
        files.append(name)
        result["selected_bytes"] += stat.st_size
    result["selected_files"] = len(files)
    remaining_output = max_output_bytes
    for offset in range(0, len(files), 64):
        left = deadline - time.monotonic()
        if left <= 0:
            partial("timeout")
            break
        batch = files[offset:offset + 64]
        result["submitted_files"] += len(batch)
        args = [rg, "--no-config", "--json", "--fixed-strings", "--line-number",
                "--max-filesize", str(max_file_bytes), "--", pattern, *batch]
        captured, code = run_command(args, cwd=str(directory), timeout=left,
                                     max_output_bytes=max(1, remaining_output))
        result["rg_exit_codes"].append(captured.get("exit_code"))
        if captured.get("status") == "error":
            result["error_code"] = captured.get("error_code", "search_failed")
            return result, code or 2
        if captured.get("timed_out") or captured.get("status") == "interrupted":
            partial("timeout")
        if captured.get("exit_code") not in (0, 1, None):
            partial("search_error")
        if captured.get("stderr"):
            result["diagnostics"] = (result["diagnostics"] + captured["stderr"])[:4096]
        if captured.get("stdout_truncated") or captured.get("stderr_truncated"):
            partial("output_limit")
        stdout = captured.get("stdout", "")
        remaining_output -= len(stdout.encode("utf-8"))
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
                data = event.get("data", {})
                if event.get("type") != "match":
                    continue
                path = _decode(data["path"])
                text = _decode(data["lines"])
                if len(result["results"]) >= limit:
                    partial("result_limit")
                    break
                result["results"].append({"path": path.removeprefix("./"),
                                          "line_number": data["line_number"],
                                          "preview": text.rstrip("\r\n")[:400],
                                          "preview_truncated": len(text.rstrip("\r\n")) > 400})
            except (ValueError, KeyError, TypeError, binascii.Error):
                partial("incomplete_output_record")
        if remaining_output <= 0 or "output_limit" in result["partial_reasons"]:
            partial("output_limit")
            break
        if "result_limit" in result["partial_reasons"] or "timeout" in result["partial_reasons"]:
            break
    result["status"] = "partial" if result["partial"] else "matched" if result["results"] else "no_matches"
    return result, 3 if result["partial"] else 0


def _decode(value):
    if "text" in value:
        return value["text"]
    return base64.b64decode(value["bytes"], validate=True).decode("utf-8", errors="replace")
