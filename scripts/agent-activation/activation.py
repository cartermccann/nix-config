"""Read-only registration/trust checks. Never authorize hooks or call providers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

MARKER = " --installation-id agent-tool-guard-v1"
LIMIT = 1024 * 1024


def read_json(path):
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("configuration_size_limit")
    return json.loads(raw)


def registrations(path, runtime):
    """Project only our hook handlers; unrelated settings never leave memory."""
    result = {"runtime": runtime, "configured_events": [], "synthetic_checks": []}
    try:
        doc = read_json(path)
        result["all_hooks_disabled"] = doc.get("disableAllHooks") is True
        for event, groups in doc.get("hooks", {}).items():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for hook in group.get("hooks", []):
                    if not isinstance(hook, dict):
                        continue
                    command = hook.get("command", "")
                    if isinstance(command, str) and MARKER in command:
                        result["configured_events"].append(event)
                        result["synthetic_checks"].append(probe_handler(command, runtime, event))
        result["status"] = "configured_reload_unverified" if result["configured_events"] else "missing"
    except (OSError, ValueError, TypeError, AttributeError):
        result["status"] = "configuration_unavailable"
    return result


def probe_handler(command, runtime, event):
    """Invoke an exact known local adapter with synthetic data and temporary state."""
    result = {"event": event, "status": "unverified"}
    try:
        argv = shlex.split(command)
        # Never run an arbitrary hook command or a shell from configuration.
        expected_tail = ["--installation-id", "agent-tool-guard-v1", "--runtime", runtime, "--event"]
        allowed_guards = {(Path.home() / "nix-config/scripts/agent-tool-guard/guard/hook.py").resolve()}
        if os.environ.get("AGENT_ACTIVATION_GUARD_ROOT"):
            allowed_guards.add((Path(os.environ["AGENT_ACTIVATION_GUARD_ROOT"]) / "hook.py").resolve())
        allowed_python = {Path(sys.executable).resolve()}
        if shutil.which("python3"):
            allowed_python.add(Path(shutil.which("python3")).resolve())
        if (len(argv) != 8 or argv[2:7] != expected_tail or
                Path(argv[1]).resolve() not in allowed_guards or Path(argv[0]).resolve() not in allowed_python or
                Path(argv[1]).name != "hook.py" or
                Path(argv[1]).parent.name != "guard" or
                Path(argv[1]).parent.parent.name != "agent-tool-guard" or
                not re.fullmatch(r"python3(?:\.\d+)?", Path(argv[0]).name)):
            result["status"] = "command_shape_unverified"
            return result
        phase = argv[-1]
        expected_phase = {"PreToolUse": "pre", "PostToolUse": "post", "PostToolUseFailure": "failure"}.get(event)
        if expected_phase is None or phase != expected_phase:
            result["status"] = "event_phase_mismatch"
            return result
        data = {"session_id": "synthetic-activation", "tool_use_id": "synthetic-call",
                "tool_name": "mcp__codex_apps__gmail_search_email_ids", "tool_input": {}}
        with tempfile.TemporaryDirectory(prefix="agent-activation-") as root:
            process = subprocess.run(argv, input=json.dumps(data), capture_output=True, text=True,
                                     timeout=12, env={**os.environ, "AGENT_TOOL_GUARD_STATE": root + "/state/state.sqlite"})
        if phase == "pre":
            response = json.loads(process.stdout)
            denied = response.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
            result["status"] = "passed" if process.returncode == 0 and denied else "failed"
        else:
            result["status"] = "passed" if process.returncode == 0 else "failed"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        result["status"] = "failed"
    return result


def summarize_native(response):
    entries = response.get("data", [])
    if not isinstance(entries, list) or not entries:
        return {"status": "unavailable", "hooks": []}
    hooks = []
    issues = 0
    for entry in entries:
        issues += len(entry.get("errors", [])) + len(entry.get("warnings", []))
        for hook in entry.get("hooks", []):
            if MARKER in hook.get("command", ""):
                hooks.append({"event": hook.get("eventName"), "enabled": hook.get("enabled") is True,
                              "trust": hook.get("trustStatus")})
    events = {h["event"] for h in hooks}
    trusted = hooks and all(h["enabled"] and h["trust"] in {"trusted", "managed"} for h in hooks)
    ready = trusted and events == {"preToolUse", "postToolUse"} and issues == 0
    return {"status": "trusted_registered" if ready else "approval_or_repair_required", "hooks": hooks,
            "loader_issue_count": issues, "live_provider_enforcement": "not_tested"}


def native_status(binary, home, cwd, timeout=20):
    """Bounded local app-server hooks/list; does not open a thread or start MCP."""
    process = None
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen([binary, "app-server", "--stdio"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   cwd=cwd, env={**os.environ, "CODEX_HOME": str(home)}, start_new_session=True)
        selector.register(process.stdout, selectors.EVENT_READ)
        buffer = b""
        received = 0
        deadline = time.monotonic() + timeout

        def request(ident, method, params):
            nonlocal buffer, received
            process.stdin.write((json.dumps({"id": ident, "method": method, "params": params}) + "\n").encode())
            process.stdin.flush()
            while time.monotonic() < deadline:
                if not selector.select(min(.2, max(0, deadline - time.monotonic()))):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise ValueError("server_ended")
                buffer += chunk
                received += len(chunk)
                if received > 4 * LIMIT:
                    raise ValueError("response_limit")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    response = json.loads(line)
                    if response.get("id") == ident:
                        if "error" in response:
                            raise ValueError("protocol_error")
                        return response["result"]
            raise TimeoutError()

        request(1, "initialize", {"clientInfo": {"name": "agent_activation", "version": "1.0"},
                                  "capabilities": {"experimentalApi": True}})
        process.stdin.write(b'{"method":"initialized"}\n')
        process.stdin.flush()
        return summarize_native(request(2, "hooks/list", {"cwds": [str(cwd)]}))
    except (OSError, ValueError, KeyError, TypeError, TimeoutError):
        return {"status": "unavailable", "hooks": [], "live_provider_enforcement": "not_tested"}
    finally:
        selector.close()
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            for stream in (process.stdin, process.stdout):
                stream.close()


def plugin_status(home):
    path = home / ".config/opencode/plugins/tool-guard.js"
    result = {"runtime": "opencode", "status": "missing", "live_provider_enforcement": "not_tested"}
    try:
        raw = path.read_text() if path.stat().st_size <= LIMIT else ""
        match = re.search(r"^const command = (\[.*\]);$", raw, re.M)
        if not match:
            return result
        command = json.loads(match[1])
        if not isinstance(command, list) or len(command) != 2 or not all(isinstance(s, str) for s in command):
            return result
        template = Path(command[1]).with_name("opencode-plugin.template.js").read_text()
        exact = raw == template.replace("__GUARD_COMMAND__", json.dumps(command))
        result["template_matches"] = exact
        result["status"] = "configured_reload_unverified" if exact else "template_mismatch"
    except (OSError, ValueError):
        result["status"] = "configuration_unavailable"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--codex", default="/etc/profiles/per-user/cjm/bin/codex")
    parser.add_argument("--live-codex", action="store_true", help="Read trust via a temporary local app-server")
    args = parser.parse_args()
    reports = {}
    for label, directory in (("codex", ".codex"), ("codex_cliproxy", ".codex_cliproxy")):
        reports[label] = registrations(args.home / directory / "hooks.json", "codex")
        if args.live_codex:
            reports[label]["native"] = native_status(args.codex, args.home / directory, args.home)
    reports["claude"] = registrations(args.home / ".claude/settings.json", "claude")
    reports["opencode"] = plugin_status(args.home)
    problems = []
    for label, report in reports.items():
        expected = {"PreToolUse", "PostToolUse", "PostToolUseFailure"} if label == "claude" else {"PreToolUse", "PostToolUse"}
        if label != "opencode" and (set(report["configured_events"]) != expected or report.get("all_hooks_disabled") or
                                       any(check["status"] != "passed" for check in report["synthetic_checks"])):
            problems.append(label + ": registration_or_handler_check")
        if label == "opencode" and report["status"] != "configured_reload_unverified":
            problems.append(label + ": registration")
        if "native" in report and report["native"]["status"] != "trusted_registered":
            problems.append(label + ": hook_approval_or_repair_required")
    print(json.dumps({"schema_version": 1, "reports": reports, "action_required": problems,
                      "limitations": ["Synthetic handler checks do not prove hooks loaded in an existing session.",
                                      "No provider calls, trust changes, or credential changes were made.",
                                      "Reload Claude/OpenCode sessions after registration; live enforcement remains unverified."]}, indent=2))
    return 2 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
