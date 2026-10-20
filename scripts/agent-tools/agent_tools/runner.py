"""Run an argv vector with bounded output and ownership of its process group.

Output limits apply to captured raw bytes independently for stdout and stderr.
Only each stream's prefix is retained, decoded with UTF-8 replacement. The
deadline includes waiting for inherited output pipes after the leader exits.
This is a foreground runner, not a persistent background-daemon launcher.
All processes that remain in the owned group are terminated before returning;
this is not a sandbox for programs that deliberately escape their process group.
"""

from __future__ import annotations

import errno
import math
import os
import selectors
import signal
import subprocess
import threading
import time
from typing import Any


_READ_SIZE = 65_536
_TERM_GRACE = 0.2
_REAP_GRACE = 0.5
_DRAIN_GRACE = 0.2


def _valid_input(argv: Any, cwd: Any, timeout: Any, output_limit: Any) -> str | None:
    if (not isinstance(argv, list) or not argv or not argv[0]
            or any(not isinstance(value, str) or "\0" in value for value in argv)):
        return "invalid_argv"
    if (isinstance(timeout, bool) or not isinstance(timeout, (float, int))
            or not math.isfinite(timeout) or not 0 < timeout <= 86_400):
        return "invalid_timeout"
    if (isinstance(output_limit, bool) or not isinstance(output_limit, int)
            or not 1 <= output_limit <= 1_048_576):
        return "invalid_output_limit"
    if cwd is not None:
        if not isinstance(cwd, str) or not cwd or "\0" in cwd:
            return "invalid_cwd"
        try:
            if not os.path.isdir(cwd):
                return "invalid_cwd"
        except (OSError, ValueError):
            return "invalid_cwd"
    return None


def _read_ready(selector: selectors.BaseSelector, buffers: dict[str, bytearray],
                truncated: dict[str, bool], limit: int, wait: float) -> None:
    for key, _ in selector.select(max(0.0, wait)):
        try:
            chunk = os.read(key.fd, _READ_SIZE)
        except BlockingIOError:
            continue
        if not chunk:
            selector.unregister(key.fileobj)
            key.fileobj.close()
            continue
        remaining = limit - len(buffers[key.data])
        buffers[key.data].extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated[key.data] = True


def _signal_group(process: subprocess.Popen[bytes], sig: int) -> bool:
    try:
        os.killpg(process.pid, sig)
        return True
    except ProcessLookupError:
        return False


def _group_exists(process: subprocess.Popen[bytes]) -> bool:
    return _signal_group(process, 0)


def _cleanup(process: subprocess.Popen[bytes], selector: selectors.BaseSelector,
             buffers: dict[str, bytearray], truncated: dict[str, bool], limit: int) -> bool:
    """Bound termination and pipe draining, including after the leader exits."""
    try:
        if _signal_group(process, signal.SIGTERM):
            grace_end = time.monotonic() + _TERM_GRACE
            while time.monotonic() < grace_end:
                process.poll()
                if not _group_exists(process):
                    break
                _read_ready(selector, buffers, truncated, limit,
                            min(0.02, max(0.0, grace_end - time.monotonic())))
    except KeyboardInterrupt:
        # A second interrupt must not skip the unconditional group kill below.
        pass
    finally:
        _signal_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=_REAP_GRACE)
    except subprocess.TimeoutExpired:
        return False
    drain_end = time.monotonic() + _DRAIN_GRACE
    while selector.get_map() and time.monotonic() < drain_end:
        try:
            _read_ready(selector, buffers, truncated, limit,
                        min(0.02, max(0.0, drain_end - time.monotonic())))
        except KeyboardInterrupt:
            continue
    return True


def run_command(argv: list[str], *, cwd: str | None = None, timeout: float = 120,
                max_output_bytes: int = 65_536) -> tuple[dict[str, Any], int]:
    """Return a bounded execution result and the wrapper's conventional status.

``completed`` means the child exited, even when its status is nonzero. Launch
and validation failures use ``error`` without exposing command/environment or
OS exception text. SIGINT/KeyboardInterrupt maps to ``interrupted`` and 130.
Main-thread callers also receive cleanup on SIGTERM; worker-thread calls cannot
replace the application's process-wide signal handlers.
"""
    started = time.monotonic()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    status = "completed"
    error_code = None
    wrapper_status = 0
    timed_out = False
    owns_handlers = threading.current_thread() is threading.main_thread()
    previous_term = signal.getsignal(signal.SIGTERM) if owns_handlers else None
    previous_int = signal.getsignal(signal.SIGINT) if owns_handlers else None
    interrupted_during_cleanup = False

    def interrupt(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    def note_interrupt(_signum: int, _frame: Any) -> None:
        nonlocal interrupted_during_cleanup
        interrupted_during_cleanup = True

    try:
        if owns_handlers:
            signal.signal(signal.SIGTERM, interrupt)
        error_code = _valid_input(argv, cwd, timeout, max_output_bytes)
        if error_code is not None:
            status, wrapper_status = "error", 2
        else:
            try:
                process = subprocess.Popen(
                    argv, cwd=cwd, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    shell=False, start_new_session=True, close_fds=True,
                )
            except (OSError, ValueError, UnicodeError) as exc:
                status = "error"
                missing = isinstance(exc, OSError) and exc.errno == errno.ENOENT
                error_code = "executable_not_found" if missing else "launch_failed"
                wrapper_status = 127 if missing else 2
            if process is not None:
                for name in buffers:
                    stream = getattr(process, name)
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, name)
                deadline = started + timeout
                while selector.get_map() or process.poll() is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        status, wrapper_status = "timeout", 124
                        timed_out = True
                        break
                    _read_ready(selector, buffers, truncated, max_output_bytes, min(0.05, remaining))
    except KeyboardInterrupt:
        status, wrapper_status = "interrupted", 130
    except (OSError, ValueError, OverflowError):
        status, wrapper_status, error_code = "error", 2, "runner_failed"
    finally:
        try:
            if owns_handlers:
                # Repeated cancellation records intent without skipping reaping.
                signal.signal(signal.SIGTERM, note_interrupt)
                signal.signal(signal.SIGINT, note_interrupt)
            if process is not None:
                try:
                    reaped = _cleanup(process, selector, buffers, truncated, max_output_bytes)
                    if not reaped:
                        status, wrapper_status, error_code = "error", 2, "cleanup_failed"
                except (OSError, ValueError):
                    status, wrapper_status, error_code = "error", 2, "cleanup_failed"
            for key in list(selector.get_map().values()):
                key.fileobj.close()
            selector.close()
            if process is not None:
                for name in buffers:
                    stream = getattr(process, name)
                    if stream is not None:
                        stream.close()
        finally:
            if owns_handlers:
                signal.signal(signal.SIGINT, previous_int)
                signal.signal(signal.SIGTERM, previous_term)
        if interrupted_during_cleanup and error_code != "cleanup_failed":
            status, wrapper_status = "interrupted", 130
    exit_code = process.returncode if process is not None else None
    child_signal = -exit_code if exit_code is not None and exit_code < 0 else None
    if status == "completed" and exit_code is not None:
        wrapper_status = min(255, 128 + child_signal) if child_signal else min(255, exit_code)
    result = {
        "status": status,
        "exit_code": exit_code,
        "signal": child_signal,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "timed_out": timed_out,
        "stdout": bytes(buffers["stdout"]).decode("utf-8", errors="replace"),
        "stderr": bytes(buffers["stderr"]).decode("utf-8", errors="replace"),
        "stdout_truncated": truncated["stdout"],
        "stderr_truncated": truncated["stderr"],
    }
    if error_code is not None:
        result["error_code"] = error_code
    return result, wrapper_status
