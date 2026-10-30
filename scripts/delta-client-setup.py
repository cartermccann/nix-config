#!/usr/bin/env python3
"""Merge the approved Claude capture hooks without replacing other settings."""

import argparse
import json
import os
from pathlib import Path
import shlex
import tempfile


def is_delta_hook(hook):
    if not isinstance(hook, dict) or not isinstance(hook.get("command"), str):
        return False
    try:
        command = shlex.split(hook["command"])
    except ValueError:
        return False
    if not command or Path(command[0]).name != "delta-history":
        return False
    arguments = command[1:] or hook.get("args", [])
    return arguments[:2] == ["hook", "claude"]


def merge(settings, executable, instance):
    result = json.loads(json.dumps(settings))
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Claude hooks must be an object")
    # Claude interprets exit 2 as a request to block the prompt. Delta uses it
    # for ordinary capture failures too (including unapproved scratch folders).
    # Enforce best-effort capture in the shell rather than relying on the
    # unsupported onFailure setting. Keep sanitized errors on stderr and keep
    # capture receipts out of Claude's prompt context.
    command = shlex.join([executable, "hook", "claude", "--instance", instance])
    handler = {
        "type": "command", "command": command + " >/dev/null || :",
        "timeout": 5,
    }
    for event in ("UserPromptSubmit", "Stop", "SessionEnd"):
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise ValueError("Claude hook entries must be an array")
        found = False
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                raise ValueError("Invalid existing Claude hook entry")
            for index, prior in enumerate(entry["hooks"]):
                if is_delta_hook(prior):
                    entry["hooks"][index] = handler
                    found = True
        if not found:
            entries.append({"hooks": [handler.copy()]})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    path = args.home / ".claude/settings.json"
    if path.is_symlink():
        raise ValueError("Claude settings are managed by a symlink; preserve their owner")
    before = path.read_text() if path.exists() else "{}"
    settings = json.loads(before)
    if not isinstance(settings, dict):
        raise ValueError("Claude settings must be an object")
    updated = merge(settings, args.executable, args.instance)
    if updated == settings:
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".delta-hooks-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(updated, stream, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print("Merged Delta Claude prompt/final-response hooks; unrelated settings preserved.")


if __name__ == "__main__":
    main()
