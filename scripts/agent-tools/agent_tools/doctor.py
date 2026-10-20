"""Read-only, bounded repository and toolchain preflight."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time

GIT_TIMEOUT = 5.0
TOTAL_TIMEOUT = 20.0
STATUS_MAX_BYTES = 65536
STATUS_MAX_ENTRIES = 1000
PACKAGE_MAX_BYTES = 1024 * 1024
SCRIPT_MAX_NAMES = 100
LOCKFILES = {
    "package-lock.json": "npm", "npm-shrinkwrap.json": "npm",
    "pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn",
    "bun.lock": "bun", "bun.lockb": "bun", "uv.lock": "uv",
    "poetry.lock": "poetry", "Cargo.lock": "cargo", "go.sum": "go",
    "Gemfile.lock": "bundler", "composer.lock": "composer", "flake.lock": "nix",
}


def _git(executable: str, directory: Path, arguments: list[str], deadline: float,
         cap: int = STATUS_MAX_BYTES) -> dict:
    """Collect bounded output; never expose Git stderr or invoke a shell."""
    remaining = min(GIT_TIMEOUT, deadline - time.monotonic())
    if remaining <= 0:
        return {"code": None, "output": b"", "partial": True, "reason": "timeout"}
    environment = os.environ.copy()
    for key in list(environment):
        if key in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                   "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                   "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"} or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            environment.pop(key, None)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    command = [executable, "--no-optional-locks", "-c", "core.fsmonitor=false",
               "-c", "core.untrackedCache=false", "-c", "core.hooksPath=/dev/null",
               "-C", str(directory), *arguments]
    output = bytearray()
    failure = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=environment, shell=False, start_new_session=True)
    except OSError:
        return {"code": None, "output": b"", "partial": True, "reason": "git_unavailable"}
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    stop = time.monotonic() + remaining
    try:
        while selector.get_map():
            wait = stop - time.monotonic()
            if wait <= 0:
                failure = "timeout"
                break
            events = selector.select(wait)
            if not events:
                failure = "timeout"
                break
            chunk = os.read(process.stdout.fileno(), min(8192, cap + 1 - len(output)))
            if not chunk:
                selector.unregister(process.stdout)
                break
            output.extend(chunk)
            if len(output) > cap:
                failure = "output_limit"
                break
        if failure is None:
            try:
                process.wait(timeout=max(0.001, stop - time.monotonic()))
            except subprocess.TimeoutExpired:
                failure = "timeout"
    finally:
        if failure is not None or process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        selector.close()
        process.stdout.close()
    return {"code": process.returncode, "output": bytes(output[:cap]),
            "partial": failure is not None, "reason": failure}


def _status(raw: bytes, incomplete: bool) -> dict:
    records = raw.split(b"\0")
    if records[-1]:
        incomplete = True
    records = records[:-1]
    tracked = untracked = conflicts = entries = 0
    index = 0
    while index < len(records):
        if entries >= STATUS_MAX_ENTRIES:
            incomplete = True
            break
        record = records[index]
        index += 1
        prefix = record[:2]
        if prefix == b"? ":
            untracked += 1
        elif prefix in {b"1 ", b"2 ", b"u "}:
            tracked += 1
            if prefix == b"u ":
                conflicts += 1
            if prefix == b"2 ":
                if index >= len(records):
                    incomplete = True
                else:
                    index += 1  # Renames have a second, NUL-separated path.
        elif prefix == b"! ":
            continue
        else:
            incomplete = True
            continue
        entries += 1
    return {"dirty": True if entries else (None if incomplete else False),
            "conflict": True if conflicts else (None if incomplete else False),
            "entries": entries, "tracked_changes": tracked, "untracked": untracked,
            "conflicts": conflicts, "partial": incomplete,
            "counts_are_lower_bounds": incomplete,
            "max_entries": STATUS_MAX_ENTRIES, "max_bytes": STATUS_MAX_BYTES}


def _package(root: Path) -> dict:
    path = root / "package.json"
    result = {"present": os.path.lexists(path), "status": "absent", "scripts": [],
              "scripts_omitted": 0, "package_manager": None}
    if not result["present"]:
        return result
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            result["status"] = "not_regular"
            return result
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(PACKAGE_MAX_BYTES + 1)
        if len(raw) > PACKAGE_MAX_BYTES:
            result["status"] = "oversized"
            return result
        data = json.loads(raw)
        if not isinstance(data, dict):
            result["status"] = "invalid_object"
            return result
        scripts = data.get("scripts", {})
        if not isinstance(scripts, dict):
            result["status"] = "invalid_scripts"
            return result
        names = sorted(key for key in scripts if re.fullmatch(r"[A-Za-z0-9_.:@/+ -]{1,128}", key))
        result["scripts"] = names[:SCRIPT_MAX_NAMES]
        result["scripts_omitted"] = len(scripts) - len(result["scripts"])
        manager = data.get("packageManager")
        if isinstance(manager, str):
            match = re.fullmatch(r"(npm|pnpm|yarn|bun)@[^\s]{1,128}", manager)
            if match:
                result["package_manager"] = match.group(1)
        result["status"] = "ok"
    except (OSError, ValueError, UnicodeError, RecursionError):
        result["status"] = "unreadable_or_invalid"
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return result


def diagnose(repo: str = ".", requires: list[str] | None = None) -> tuple[dict, int]:
    """Inspect one existing Git worktree without fetching or modifying its index."""
    result = {"status": "error", "path": None, "errors": [],
              "python": {"executable": sys.executable, "version": platform.python_version()},
              "required_executables": [], "repository": None, "worktree_status": None,
              "package": None, "lockfiles": [], "package_manager_clues": [],
              "dev_environment": {"envrc": False, "flake_nix": False,
                                  "in_nix_shell": "IN_NIX_SHELL" in os.environ,
                                  "direnv_present": any(k.startswith("DIRENV_") for k in os.environ)}}
    if requires is not None and (not isinstance(requires, list) or len(requires) > 64 or
        any(not isinstance(item, str) or not item or len(item) > 256 or
            any(ord(char) < 32 for char in item) for item in requires)):
        result["errors"].append("invalid_required_executables")
        return result, 2
    try:
        directory = Path(repo).expanduser().resolve(strict=True)
        result["path"] = str(directory)
        if not directory.is_dir():
            raise ValueError("not_directory")
    except (OSError, ValueError, TypeError, RuntimeError):
        result["errors"].append("invalid_directory")
        return result, 2
    for executable in dict.fromkeys(["git", *(requires or [])]):
        available = shutil.which(executable) is not None
        result["required_executables"].append({"name": executable, "available": available})
        if not available:
            result["errors"].append("missing_required_executable")
    git = shutil.which("git")
    if git is None:
        return result, 2
    deadline = time.monotonic() + TOTAL_TIMEOUT

    def query(arguments: list[str]) -> dict:
        return _git(git, directory, arguments, deadline)

    inside = query(["rev-parse", "--is-inside-work-tree"])
    if inside["partial"] or inside["code"] != 0 or inside["output"].strip() != b"true":
        result["errors"].append("git_probe_incomplete" if inside["partial"] else "not_git_worktree")
        return result, 2
    paths = []
    for argument in ("--show-toplevel", "--absolute-git-dir", "--git-common-dir"):
        value = query(["rev-parse", "--path-format=absolute", argument])
        if value["partial"] or value["code"] != 0:
            result["errors"].append("git_metadata_incomplete")
            return result, 2
        paths.append(os.fsdecode(value["output"].removesuffix(b"\n")))
    root = Path(paths[0])
    branch = query(["symbolic-ref", "--quiet", "--short", "HEAD"])
    head = query(["rev-parse", "--verify", "--quiet", "HEAD"])
    if branch["partial"] or head["partial"] or branch["code"] not in (0, 1) or head["code"] not in (0, 1):
        result["errors"].append("git_head_incomplete")
        return result, 2
    branch_name = os.fsdecode(branch["output"].removesuffix(b"\n")) if branch["code"] == 0 else None
    sha = head["output"].decode("ascii", errors="replace").strip() if head["code"] == 0 else None
    if branch_name is None and sha is None:
        result["errors"].append("git_head_incomplete")
        return result, 2
    if sha is not None and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
        result["errors"].append("invalid_head")
        return result, 2
    result["repository"] = {"root": str(root), "worktree": str(root), "git_dir": paths[1],
                            "common_dir": paths[2], "branch": branch_name, "head": sha,
                            "detached": branch_name is None, "unborn": sha is None}
    status = _git(git, directory, ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
                  deadline, cap=STATUS_MAX_BYTES)
    result["worktree_status"] = _status(status["output"], status["partial"] or status["code"] != 0)
    if result["worktree_status"]["partial"]:
        result["errors"].append("status_incomplete")
    result["package"] = _package(root)
    if result["package"]["status"] not in {"ok", "absent"}:
        result["errors"].append("package_inspection_failed")
    result["lockfiles"] = sorted(name for name in LOCKFILES if os.path.lexists(root / name))
    clues = {LOCKFILES[name] for name in result["lockfiles"]}
    if result["package"]["package_manager"]:
        clues.add(result["package"]["package_manager"])
    result["package_manager_clues"] = sorted(clues)
    result["dev_environment"].update(envrc=os.path.lexists(root / ".envrc"),
                                     flake_nix=os.path.lexists(root / "flake.nix"))
    result["status"] = "error" if result["errors"] else "ok"
    return result, 2 if result["errors"] else 0
