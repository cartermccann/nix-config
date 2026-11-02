#!/usr/bin/env python3
"""Run session actions through the Pond session's single lock owner.

`lock` and `suspend` are `pond-session lock` and `pond-session suspend`
(home/scripts/pond-session.sh): that is the one place a locker is started, so
the shell never starts one itself. Its locker is registered as the session's
lock role and survives a shell restart, and every entry point (this, the idle
timer, Super+Ctrl+L, logind's Lock and lock before sleep) shares the same
serialized startup, readiness bound and hyprlock fallback. `lock` succeeds
only once the session is confirmed locked, and `suspend` suspends only then;
both fail (exit non-zero) instead of claiming success.
"""
import os
import shutil
import subprocess
import sys

# pond-session bounds its own work (12s by default); this only guards against
# the helper itself hanging.
HELPER_TIMEOUT = 20


def helper():
    path = os.environ.get("POND_SESSION_BIN") or shutil.which("pond-session")
    if not path:
        raise RuntimeError("pond-session is not available; refusing to lock or suspend")
    return path


def run(action):
    if action in ("reboot", "shutdown"):
        subprocess.run(["systemctl", "reboot" if action == "reboot" else "poweroff"], check=True)
        return
    if action not in ("lock", "suspend"):
        raise ValueError("Unsupported session action")
    argv = [helper(), "lock", "shell"] if action == "lock" else [helper(), "suspend"]
    try:
        result = subprocess.run(argv, timeout=HELPER_TIMEOUT, start_new_session=True)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"pond-session {action} did not finish") from None
    if result.returncode != 0:
        raise RuntimeError("The session lock was not confirmed"
                           + ("; refusing to suspend" if action == "suspend" else ""))


if __name__ == "__main__":
    try:
        run(sys.argv[1])
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, IndexError) as error:
        print(f"Pond session: {error}", file=sys.stderr)
        sys.exit(1)
