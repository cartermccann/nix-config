"""Synthetic helper checks and a strict, evidence-referencing behavioral rubric.

Mechanical checks do not measure model performance. The behavioral scorer checks
submitted observations, not the authenticity or correctness of a captured trace.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
MAX_JSON = 1_048_576
sys.dont_write_bytecode = True


def manifest():
    return json.loads((ROOT / "manifest.json").read_text())


def emit(value):
    print(json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")))


def alive(pid):
    try:
        # Linux/NixOS: zombies are dead, even if the adopter has not reaped them.
        return Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def stop_groups(pids):
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def stopped(pids):
    until = time.monotonic() + 1
    while any(alive(pid) for pid in pids) and time.monotonic() < until:
        time.sleep(0.01)
    return not any(alive(pid) for pid in pids)


def decode(file):
    file.seek(0)
    raw = file.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("helper_output_limit")
    return json.loads(raw)


def invoke(command, args, *, cwd, interrupt_file=None):
    # Files keep even a broken helper's output out of memory. The outer deadline
    # bounds helper failures; fixture process groups receive separate cleanup.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command + args, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True,
                                   preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_JSON, MAX_JSON)))
        try:
            if interrupt_file is not None:
                deadline = time.monotonic() + 3
                while not interrupt_file.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not interrupt_file.exists():
                    raise ValueError("fixture_not_started")
                process.send_signal(signal.SIGINT)
            process.wait(timeout=8)
            return process.returncode, decode(stdout)
        finally:
            stop_groups([process.pid])
            process.wait(timeout=2)


def load_guard(path):
    source = Path(path) / "core.py"
    spec = importlib.util.spec_from_file_location("recovery_guard_core", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def mechanical(case, command, guard_root, directory):
    root = Path(directory)
    run = lambda args: invoke(command, args, cwd=directory)
    py = [sys.executable, "-c"]
    if case == "missing_executable":
        code, result = run(["run", "--", str(root / "SECRET_ABSENT"), "SECRET_ARGUMENT"])
        return {"missing_status_preserved": code == 127 and result.get("error_code") == "executable_not_found",
                "no_execution": result.get("exit_code") is None,
                "no_sensitive_echo": "SECRET" not in json.dumps(result)}
    if case == "invalid_cwd":
        code, result = run(["run", "--cwd", str(root / "absent"), "--", *py, "raise SystemExit(0)"])
        return {"setup_error_preserved": code == 2 and result.get("error_code") == "invalid_cwd",
                "no_execution": result.get("exit_code") is None}
    if case in {"literal_no_match", "partial_search"}:
        (root / "fixture.txt").write_text("hit\nhit\nhit\n")
        args = ["search", "--root", directory, "--literal", "[missing].*" if case == "literal_no_match" else "hit"]
        if case == "partial_search":
            args += ["--limit", "1"]
        code, result = run(args)
        if case == "literal_no_match":
            return {"literal_no_match": code == 0 and result.get("status") == "no_matches",
                    "coverage_complete": result.get("partial") is False and result.get("results") == []}
        return {"partial_reported": code == 3 and result.get("status") == "partial",
                "limit_honored": len(result.get("results", [])) == 1}
    if case == "failed_test":
        code, result = run(["run", "--", *py, "print('synthetic failure');raise SystemExit(2)"])
        return {"nonzero_preserved": code == 2 and result.get("exit_code") == 2,
                "completed_is_not_success": result.get("status") == "completed" and result.get("exit_code") != 0,
                "different_from_setup_failure": "error_code" not in result}
    if case == "hostile_argv":
        marker = root / "INJECTED"
        arguments = ["", "a b", "'quoted'", '"double"', "one\ntwo", "*.txt", "$HOME",
                     f"; touch {marker}", f"$(touch {marker})", f"`touch {marker}`"]
        code, result = run(["run", "--", *py, "import json,sys;print(json.dumps(sys.argv[1:]))", *arguments])
        return {"argv_exact": code == 0 and json.loads(result.get("stdout", "null")) == arguments,
                "no_injection": not marker.exists()}
    if case == "bounded_output":
        code, result = run(["run", "--max-output-bytes", "64", "--", *py,
                            "import os;os.write(1,b'A'*4096);os.write(2,b'B'*4096)"])
        return {"both_streams_bounded": code == 0 and result.get("stdout") == "A" * 64 and result.get("stderr") == "B" * 64,
                "both_truncations_reported": result.get("stdout_truncated") is True and result.get("stderr_truncated") is True}
    if case in {"timeout_cleanup", "interrupt_cleanup"}:
        pidfile = root / "pids.json"
        fixture = ("import json,os,pathlib,subprocess,sys,time;"
                   "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
                   f"f=pathlib.Path({str(pidfile)!r});f.with_suffix('.tmp').write_text(json.dumps([os.getpid(),p.pid]));"
                   "f.with_suffix('.tmp').replace(f);"
                   "time.sleep(30)")
        args = ["run", "--timeout", "0.4" if case == "timeout_cleanup" else "5", "--", *py, fixture]
        pids = []
        started = time.monotonic()
        try:
            code, result = invoke(command, args, cwd=directory,
                                  interrupt_file=pidfile if case == "interrupt_cleanup" else None)
            pids = json.loads(pidfile.read_text())
            gone = stopped(pids)
            if case == "timeout_cleanup":
                return {"timeout_reported": code == 124 and result.get("status") == "timeout" and result.get("timed_out") is True,
                        "deadline_bounded": time.monotonic() - started < 3, "processes_stopped": gone}
            return {"interruption_reported": code == 130 and result.get("status") == "interrupted", "processes_stopped": gone}
        finally:
            if pidfile.exists():
                pids = json.loads(pidfile.read_text())
            # Runs even if a broken implementation fails before assertions.
            stop_groups(pids[:1])
    if case == "guard_cooldown_schema":
        core = load_guard(guard_root)
        now = [1000.0]
        guard = core.Guard(root / "state.sqlite", clock=lambda: now[0], random_value=lambda: 0)
        tool = "mcp__codex_apps__gmail_read_email"
        args = {"link_id": "synthetic-account", "message_id": "synthetic-message"}
        malformed = guard.reserve(tool, "session", "invalid", {"link_id": "synthetic-account"})
        first = guard.reserve(tool, "session", "first", args)
        if first.get("allowed") is not True:
            raise ValueError("synthetic_reservation_failed")
        finish = guard.finish(tool, "session", "first", outcome="error", status_code=429, lease_id=first["lease_id"])
        read = guard.reserve(tool, "session", "retry", args)
        cross = guard.reserve(tool, "session", "cross", {**args, "link_id": "other-synthetic-account"})
        write = guard.reserve("mcp__codex_apps__gmail_send_email", "session", "write", {})
        lease_absent = guard.active_lease() is None
        now[0] += 61
        later = guard.reserve(tool, "session", "later", args)
        if later.get("allowed"):
            guard.finish(tool, "session", "later", outcome="success", lease_id=later["lease_id"])
        denied = lambda item: item.get("allowed") is False and item.get("reason") == "rate_limit_cooldown" and item.get("retry_after_seconds", 0) >= 60
        return {"schema_rejected": malformed.get("allowed") is False and malformed.get("reason") == "invalid_arguments",
                "read_retry_denied": finish.get("reason") == "rate_limit_recorded" and denied(read),
                "cross_account_denied": denied(cross),
                "write_denied_no_replay": denied(write) and lease_absent,
                "readmitted_after_cooldown": later.get("allowed") is True}
    raise ValueError("unknown_case")


def verify(command, guard_root):
    results = []
    for case in manifest()["cases"]:
        if not case["mechanical"]:
            results.append({"case_id": case["id"], "status": "not_run", "reason": "requires_actual_agent_trace"})
            continue
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="agent-recovery-") as directory:
                observations = mechanical(case["id"], command, guard_root, directory)
            passed = set(observations) == set(case["assertions"]) and all(value is True for value in observations.values())
            result = {"case_id": case["id"], "status": "passed" if passed else "failed", "assertions": observations}
        except Exception as exc:
            # Never echo helper output, argv, environment, or exception values.
            result = {"case_id": case["id"], "status": "failed", "error_type": type(exc).__name__}
        result["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
        results.append(result)
    failed = sum(r["status"] == "failed" for r in results)
    return {"schema_version": 1, "suite_id": "agent-recovery-v1", "result_type": "deterministic_tooling",
            "status": "failed" if failed else "passed", "behavior_status": "not_run",
            "counts": {s: sum(r["status"] == s for r in results) for s in ("passed", "failed", "not_run")},
            "results": results}, 1 if failed else 0


def score(submission):
    """Strict rubric validation only; a human must audit the referenced traces."""
    spec = manifest()
    invalid = lambda: ({"schema_version": 1, "result_type": "reported_agent_behavior", "status": "invalid", "error_code": "invalid_submission"}, 2)
    if not isinstance(submission, dict) or type(submission.get("schema_version")) is not int or submission.get("schema_version") != 1 or submission.get("suite_id") != spec["suite_id"] or submission.get("result_type") != "agent_behavior":
        return invalid()
    if submission.get("harness") not in {"claude", "codex", "opencode"} or not isinstance(submission.get("model"), str) or not submission["model"].strip():
        return invalid()
    records = submission.get("results")
    if not isinstance(records, list) or len(records) != len(spec["cases"]):
        return invalid()
    by_id = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("case_id"), str) or record["case_id"] in by_id:
            return invalid()
        by_id[record["case_id"]] = record
    if set(by_id) != {c["id"] for c in spec["cases"]}:
        return invalid()
    results = []
    for case in spec["cases"]:
        record = by_id[case["id"]]
        if record.get("status") == "not_run":
            results.append({"case_id": case["id"], "status": "not_run"})
            continue
        observations = record.get("observations")
        evidence = record.get("evidence")
        if record.get("status") != "completed" or not isinstance(observations, dict) or set(observations) != set(case["assertions"]) or any(type(v) is not bool for v in observations.values()):
            return invalid()
        if not isinstance(evidence, list) or not evidence or any(not isinstance(e, dict) or e.get("kind") != "captured_tool_trace" or not isinstance(e.get("sha256"), str) or not re.fullmatch("[a-f0-9]{64}", e["sha256"]) or not isinstance(e.get("path"), str) or not e["path"].strip() for e in evidence):
            return invalid()
        results.append({"case_id": case["id"], "status": "passed" if all(observations.values()) else "failed"})
    counts = {s: sum(r["status"] == s for r in results) for s in ("passed", "failed", "not_run")}
    status = "failed" if counts["failed"] else "incomplete" if counts["not_run"] else "passed"
    return {"schema_version": 1, "result_type": "reported_agent_behavior", "status": status,
            "trace_validation": "not_performed", "counts": counts, "results": results}, 0 if status == "passed" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("verify", help="Run real helper checks using synthetic local fixtures")
    check.add_argument("--tools", default=os.environ.get("AGENT_RECOVERY_TOOLS"), help="Shell-quoted argv prefix, parsed without a shell")
    check.add_argument("--guard-root", default=os.environ.get("AGENT_RECOVERY_GUARD_ROOT", str(ROOT.parent / "agent-tool-guard" / "guard")))
    sub.add_parser("manifest", help="Print portable cases and prompts")
    grade = sub.add_parser("score", help="Validate reported observations; does not authenticate traces")
    grade.add_argument("submission", type=Path)
    args = parser.parse_args(argv)
    if args.command == "manifest":
        emit(manifest())
        return 0
    if args.command == "score":
        try:
            metadata = args.submission.stat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_JSON:
                raise ValueError("input_limit")
            with args.submission.open("rb") as stream:
                raw = stream.read(MAX_JSON + 1)
            if len(raw) > MAX_JSON:
                raise ValueError("input_limit")
            value = json.loads(raw)
            result, code = score(value)
        except (OSError, ValueError, RecursionError, TypeError):
            result, code = {"schema_version": 1, "status": "invalid", "error_code": "invalid_submission"}, 2
    else:
        try:
            command = shlex.split(args.tools) if args.tools is not None else ([shutil.which("agent-tools")] if shutil.which("agent-tools") else [sys.executable, str(ROOT.parent / "agent-tools" / "agent-tools")])
        except ValueError:
            command = []
        if not command:
            result, code = {"schema_version": 1, "status": "invalid", "error_code": "empty_tools_command"}, 2
        else:
            result, code = verify(command, args.guard_root)
    emit(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
