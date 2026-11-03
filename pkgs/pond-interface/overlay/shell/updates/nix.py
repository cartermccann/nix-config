#!/usr/bin/env python3
"""Nix-aware system updates for Pond's update badge (replaces Arch's `pm`).

  nix.py check
      Resolve the newest revisions of the system flake's inputs into a
      temporary lock file and compare them with flake.lock. Read-only: the
      flake's own lock file is never written, and no update state is touched.
      Prints JSON.
  nix.py run STATUS_FILE [APP]
      Interactive, in a terminal the user watches: show the pending input
      updates, and only after confirmation write the new flake.lock, refresh
      the selected app pins (APP=all includes inputs), and build the
      system without privileges, and then, after a second confirmation, run
      the authenticated switch (nh or nixos-rebuild prompts for the password).
      Progress goes to STATUS_FILE ("status\\nmessage\\nowner-json") for the
      shell's cards.
  nix.py reconcile STATUS_FILE
      Read-only liveness check for the shell. If STATUS_FILE says "updating"
      but no live updater holds the flake's transaction lock (killed, crashed,
      machine restarted), record what was left behind as a terminal state so
      the update becomes actionable again. Prints JSON.

The flake is $POND_FLAKE, else ~/nix-config. Package lists are never edited:
adding software stays a declarative change reviewed in the flake.

Transactions
------------
An update is one transaction per flake, serialized by an advisory flock(2) on
a state file (~/.local/state/pond-interface/updates/<flake>/lock). The kernel
drops the lock when its last holder exits. Build/switch guardians retain an
inherited reference if the terminal-facing updater is killed, so a surviving
activation cannot overlap another transaction. Liveness is "is the lock held":
no PID or start-time comparison decides that. The owner (pid, process
start, boot id) is recorded only for messages. A journal beside the lock
records the stage and the lock-file hashes, and the original flake.lock is
saved there before the first write, so an abandoned transaction can be
explained and undone by hand.

The flock only orders cooperating Pond updaters. Against editors and other
tools, flake.lock is replaced only if it still holds the exact bytes this
transaction expects (see replace_if_unchanged), and the switch is refused if
the lock or any flake source file changed since the build began. Those checks
narrow the race window; they cannot close it against a writer that does not
take the lock.
"""
import contextlib
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

TERMINAL_SIGNALS = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")
                         if hasattr(signal, name))
REASONS = {signal.SIGINT: "interrupted (Ctrl-C)", signal.SIGTERM: "terminated",
           signal.SIGHUP: "stopped (its terminal closed)", signal.SIGQUIT: "quit"}
KEPT_BACKUPS = 5
FINGERPRINT_LIMIT = 50000


def flake_dir(env=None):
    env = os.environ if env is None else env
    path = Path(env.get("POND_FLAKE") or Path(env.get("HOME", str(Path.home()))) / "nix-config").expanduser()
    if not (path / "flake.nix").is_file():
        raise RuntimeError(f"No system flake at {path} (set POND_FLAKE)")
    if not (path / "flake.lock").is_file():
        raise RuntimeError(f"{path} has no flake.lock")
    return path


def root_inputs(lock):
    nodes = lock.get("nodes", {})
    root = nodes.get(lock.get("root", "root"), {})
    result = {}
    for name, ref in root.get("inputs", {}).items():
        if not isinstance(ref, str):
            continue  # "follows" another input
        locked = nodes.get(ref, {}).get("locked", {})
        result[name] = {"rev": locked.get("rev") or locked.get("narHash", ""),
                        "lastModified": locked.get("lastModified", 0)}
    return result


def compare(old, new):
    before, after = root_inputs(old), root_inputs(new)
    changes = []
    for name in sorted(after):
        if name in before and before[name]["rev"] != after[name]["rev"]:
            changes.append({"input": name, "from": before[name]["rev"][:12], "to": after[name]["rev"][:12],
                            "fromDate": before[name]["lastModified"], "toDate": after[name]["lastModified"]})
    return changes


def resolve_latest(flake, output, run=subprocess.run, inputs=()):
    result = run(["nix", "flake", "update", "--flake", str(flake), "--output-lock-file", str(output), *inputs],
                 capture_output=True, text=True, timeout=600)
    if result.returncode:
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        raise RuntimeError(lines[-1].strip() if lines else "nix flake update failed")


def check(env=None, run=subprocess.run, keep=False, inputs=()):
    flake = flake_dir(env)
    lock_file = flake / "flake.lock"
    original = lock_file.read_bytes()
    with tempfile.TemporaryDirectory(prefix="pond-update-") as directory:
        latest = Path(directory) / "flake.lock"
        resolve_latest(flake, latest, run, inputs)
        if lock_file.read_bytes() != original:
            raise RuntimeError("flake.lock changed during the check")
        resolved = latest.read_bytes()
        changes = compare(json.loads(original), json.loads(resolved))
    result = {"available": bool(changes), "flake": str(flake), "inputs": changes,
              "activationPending": activation_pending(flake, env)}
    if keep:
        result["_lock"], result["_original"] = resolved, original
    return result


# --- Files -------------------------------------------------------------------

def sha256(data):
    return hashlib.sha256(data).hexdigest()


def stage_file(path, data, mode=None):
    """Write *data* to a fresh, uniquely named temporary file beside *path*."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            if mode is not None:
                os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    return temporary


def write_atomic(path, data, mode=None):
    temporary = stage_file(path, data, mode)
    try:
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


class Conflict(RuntimeError):
    """flake.lock is not what this transaction expected."""


def replace_if_unchanged(path, expected, new, token):
    """Atomically replace *path* with *new*, but only if it still holds *expected*.

    The exact inode being replaced is pinned with a hard link, so an in-place
    edit that lands between the final comparison and the rename is still
    detected afterwards, and the other writer's text is kept in the pin file
    rather than discarded. A writer that atomically swaps in its own new file
    in that last instant cannot be detected; no userspace compare-and-swap on
    a path exists. Raises Conflict (never silently overwrites) otherwise.
    """
    path = Path(path)
    if path.read_bytes() != expected:
        raise Conflict("flake.lock no longer has the contents this update expects")
    pin = path.with_name(f".{path.name}.{token[:8]}.prev")
    try:
        os.link(path, pin)
    except OSError:
        pin = None  # no hard links here: compare only
    keep_pin = False
    try:
        temporary = stage_file(path, new, stat.S_IMODE(path.stat().st_mode))
        try:
            if path.read_bytes() != expected or (pin and not os.path.samefile(path, pin)):
                raise Conflict("flake.lock was changed while this update was preparing to replace it")
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        if pin and pin.read_bytes() != expected:
            keep_pin = True
            raise Conflict(f"flake.lock was edited while it was being replaced; that edit is kept in {pin}")
    finally:
        if pin and not keep_pin:
            with contextlib.suppress(OSError):
                os.unlink(pin)


def listed_files(flake):
    """Files that make up the flake source: git's index, else a pruned walk."""
    if (flake / ".git").exists() and shutil.which("git"):
        try:
            result = subprocess.run(["git", "-C", str(flake), "ls-files", "-z", "--cached"],
                                    capture_output=True, timeout=30,
                                    env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
            if result.returncode == 0:
                return [name for name in os.fsdecode(result.stdout).split("\0") if name]
        except (OSError, subprocess.SubprocessError):
            pass
    names = []
    for directory, subdirectories, files in os.walk(flake):
        relative = Path(directory).relative_to(flake)
        subdirectories[:] = [name for name in subdirectories
                             if name not in (".git", ".direnv", "node_modules", "__pycache__")
                             and not (relative == Path(".") and name.startswith("result"))]
        names += [str(relative / name) for name in files
                  if not (relative == Path(".") and name.startswith("result"))]
        if len(names) > FINGERPRINT_LIMIT:
            break
    return names


def tree_fingerprint(flake):
    """{relative path: (size, mtime_ns)} for the flake sources, except flake.lock.

    None when the tree is too large to fingerprint, in which case only
    flake.lock is guarded."""
    names = listed_files(flake)
    if len(names) > FINGERPRINT_LIMIT:
        return None
    result = {}
    for name in names:
        if name == "flake.lock":
            continue
        try:
            info = os.lstat(flake / name)
            result[name] = (info.st_size, info.st_mtime_ns)
        except OSError:
            result[name] = None
    return result


def changed_paths(before, after):
    if before is None or after is None:
        return []
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


# --- Transactions ------------------------------------------------------------

def boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def process_start(pid):
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return 0


def state_dir(flake, env=None, create=True):
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or str(Path(env.get("HOME", str(Path.home()))) / ".local/state")
    key = hashlib.sha256(os.path.realpath(flake).encode()).hexdigest()[:16]
    path = Path(base) / "pond-interface" / "updates" / key
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def read_json(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def activation_pending(flake, env=None):
    """A written flake is not an activated system; retain this across retries."""
    journal = read_json(state_dir(flake, env, create=False) / "journal.json")
    return bool(journal.get("activation_pending", False))


class Busy(RuntimeError):
    def __init__(self, owner):
        self.owner = owner

    def describe(self):
        pid = self.owner.get("pid")
        started = self.owner.get("started")
        when = time.strftime("%H:%M:%S", time.localtime(started)) if started else "unknown time"
        return f"pid {pid}, started {when}" if pid else "owner unknown"


class Transaction:
    """The exclusive, per-flake right to change flake.lock and switch."""

    def __init__(self, flake, env=None, directory=None):
        self.flake = Path(flake)
        self.dir = Path(directory) if directory else state_dir(flake, env)
        self.lock_path = self.dir / "lock"
        self.journal_path = self.dir / "journal.json"
        self.id = uuid.uuid4().hex
        self.descriptor = None
        self.owner = {}
        self.journal = {}
        self.stage = "start"
        self.started = 0.0
        self.backup = None

    def acquire(self, role="update"):
        # An update that collides with a reconcile probe waits it out briefly
        # instead of reporting a phantom "already running".
        deadline = time.monotonic() + (3 if role == "update" else 0)
        while True:
            try:
                return self._acquire_once(role)
            except Busy as busy:
                if busy.owner.get("role", "reconcile") == "update" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def _acquire_once(self, role):
        for _ in range(3):
            descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                os.close(descriptor)
                if error.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    raise Busy(read_json(self.lock_path)) from None
                raise
            try:
                same = os.fstat(descriptor).st_ino == os.stat(self.lock_path).st_ino
            except OSError:
                same = False
            if same:
                break
            os.close(descriptor)  # the lock file was replaced under us: lock the current one
        else:
            raise RuntimeError("could not lock the update state")
        self.descriptor = descriptor
        self.started = time.time()
        self.owner = {"txn": self.id, "role": role, "pid": os.getpid(), "start": process_start(os.getpid()),
                      "boot": boot_id(), "started": self.started, "flake": str(self.flake),
                      "lock": str(self.lock_path), "journal": str(self.journal_path)}
        self.journal = dict(self.owner)
        os.ftruncate(descriptor, 0)
        os.pwrite(descriptor, json.dumps(self.owner).encode(), 0)
        return self

    def release(self):
        if self.descriptor is None:
            return
        # Build/switch guardians inherit this open file description. Closing
        # our reference releases the lock only after they have finished too;
        # explicit LOCK_UN would invalidate their protection as well.
        os.close(self.descriptor)
        self.descriptor = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()

    def record(self, stage, **extra):
        self.stage = stage
        self.journal.update(extra, stage=stage, updated=time.time())
        try:
            write_atomic(self.journal_path, json.dumps(self.journal, sort_keys=True).encode())
        except OSError as error:
            print(f"warning: cannot record update progress ({error})", file=sys.stderr)

    def save_backup(self, original):
        self.backup = self.dir / f"flake.lock.{self.id}.orig"
        write_atomic(self.backup, original, 0o600)
        old = sorted(self.dir.glob("flake.lock.*.orig"), key=lambda path: path.stat().st_mtime)
        for path in old[:-KEPT_BACKUPS]:
            with contextlib.suppress(OSError):
                path.unlink()

    def drop_backup(self):
        if self.backup:
            with contextlib.suppress(OSError):
                self.backup.unlink()
            self.backup = None

    def clean_stale_temporaries(self):
        # Only ever called while holding the lock, so these belong to dead transactions.
        for path in [*self.flake.glob(".flake.lock.*.tmp"), self.flake / ".flake.lock.pond"]:
            with contextlib.suppress(OSError):
                path.unlink()


def after_path(status_path):
    return Path(str(status_path) + ".after")


def read_status(path):
    """(state, message, owner) from a status file; ("", "", {}) when absent."""
    try:
        lines = Path(path).read_text().split("\n")
    except OSError:
        return "", "", {}
    owner = {}
    if len(lines) > 2 and lines[2].strip():
        try:
            parsed = json.loads(lines[2])
            owner = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            pass
    return lines[0].strip(), (lines[1] if len(lines) > 1 else "").strip(), owner


class Status:
    """Progress for the shell: "state\\nmessage\\nowner-json", replaced atomically.

    Only the live transaction owner (or a reconciler holding the lock) writes
    it, so a status file never goes backwards."""

    def __init__(self, path, owner=None):
        self.path = Path(path) if path else None
        self.owner = owner or {}

    def __call__(self, state, message=""):
        if self.path is None:
            return
        text = f"{state}\n{' '.join(str(message).split())}\n"
        if self.owner:
            text += json.dumps(self.owner) + "\n"
        try:
            write_atomic(self.path, text.encode())
        except OSError as error:
            print(f"warning: cannot write update status ({error})", file=sys.stderr)


def lock_note(journal, flake):
    original, written = journal.get("original_sha256"), journal.get("written_sha256")
    try:
        current = sha256((Path(flake) / "flake.lock").read_bytes())
    except OSError:
        return "flake.lock is unreadable"
    if not written or current == original:
        return "flake.lock is unchanged"
    saved = f" (original saved at {journal['backup']})" if journal.get("backup") else ""
    if current == written:
        return f"flake.lock holds the new revisions{saved}"
    return f"flake.lock was changed by something else{saved}"


def outcome(stage, reason, journal=None, flake=None):
    """(state, message) for a transaction that stopped in *stage* without finishing."""
    note = lock_note(journal, flake) if journal and flake else ""
    if stage == "finished":
        return "failed", f"Update {reason} just as it finished. Check flake.lock and the running system"
    if stage in ("start", "checking", "confirm", ""):
        return "available", f"Update {reason} before any change"
    if stage == "apps":
        return "failed", f"Update {reason} while updating app pins. Review the pins and rebuild before switching"
    if stage in ("lock-write", "building", "restore"):
        return "failed", f"Update {reason} while building. {note or 'flake.lock may hold the new revisions'}"
    if stage == "built":
        return "available", f"Built; switch pending. Update {reason}"
    return "failed", (f"Update {reason} while switching. The system may be partially activated. "
                      f"{note}").strip()


def reconcile(status_path, env=None):
    """Turn an "updating" status with no live owner into a terminal state."""
    state, message, owner = read_status(status_path)
    result = {"state": state, "message": message, "alive": False, "abandoned": False,
              "transaction": owner.get("txn", ""),
              "restartNeeded": needs_restart() if state == "restart" else False}
    if state != "updating":
        return result
    flake = directory = transaction = None
    try:
        if owner.get("lock"):
            flake, directory = Path(owner.get("flake", "")), Path(owner["lock"]).parent
        else:
            flake = flake_dir(env)
            directory = state_dir(flake, env)
        directory.mkdir(parents=True, exist_ok=True)
        transaction = Transaction(flake, directory=directory)
        transaction.acquire(role="reconcile")
    except Busy:
        result.update(alive=True)
        return result
    except (RuntimeError, OSError):
        transaction = None  # no flake or state at all: nothing can be running
    try:
        # Completion may have happened while we waited for the transaction
        # lock. Never overwrite that newer result with an abandonment report.
        current_state, current_message, current_owner = read_status(status_path)
        if current_state != "updating" or current_owner != owner:
            result.update(state=current_state, message=current_message,
                          transaction=current_owner.get("txn", ""),
                          restartNeeded=needs_restart() if current_state == "restart" else False)
            return result
        journal = read_json(directory / "journal.json") if transaction else {}
        if owner.get("txn") and journal.get("txn") != owner["txn"]:
            journal = {}
        reason = "stopped unexpectedly (killed, or the machine restarted)"
        if journal.get("boot") and journal["boot"] != boot_id():
            reason = "stopped by a restart of the machine"
        state, message = outcome(journal.get("stage", ""), reason, journal, journal.get("flake") or flake)
        Status(status_path)(state, message)
        with contextlib.suppress(OSError):
            after_path(status_path).unlink()
        if journal and transaction:
            journal.update(stage="finished", outcome="abandoned")
            with contextlib.suppress(OSError):
                write_atomic(transaction.journal_path, json.dumps(journal, sort_keys=True).encode())
        result.update(state=state, message=message, abandoned=True)
    finally:
        if transaction:
            transaction.release()
    return result


# --- Interactive update ------------------------------------------------------

class Interrupted(BaseException):
    """A terminal signal arrived. BaseException so no handler swallows it."""

    def __init__(self, signum=signal.SIGINT):
        super().__init__(signum)
        self.signum = signum


@contextlib.contextmanager
def interrupts_raise():
    """Turn SIGINT/SIGTERM/SIGHUP/SIGQUIT into Interrupted so cleanup runs."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum, frame):
        raise Interrupted(signum)
    previous = {signum: signal.signal(signum, handler) for signum in TERMINAL_SIGNALS}
    try:
        yield
    finally:
        for signum, old in previous.items():
            signal.signal(signum, old)


def ignore_interrupts():
    if threading.current_thread() is threading.main_thread():
        for signum in TERMINAL_SIGNALS:
            signal.signal(signum, signal.SIG_IGN)


@contextlib.contextmanager
def cleanup_interrupts():
    """A second terminal signal must not interrupt child reaping/unlocking."""
    previous = {}
    if threading.current_thread() is threading.main_thread():
        previous = {sig: signal.signal(sig, signal.SIG_IGN) for sig in TERMINAL_SIGNALS}
    try:
        yield
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)


def say(text):
    with contextlib.suppress(OSError):
        print(text)


def ask(question, default=False):
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(question + suffix).strip().lower()
    except EOFError:
        return False
    return default if not answer else answer in ("y", "yes")


def builder(flake):
    host = socket.gethostname()
    if shutil.which("nh"):
        return (["nh", "os", "build", str(flake)], ["nh", "os", "switch", str(flake)])
    target = f"{flake}#{host}"
    return (["nixos-rebuild", "build", "--flake", target],
            ["sudo", "nixos-rebuild", "switch", "--flake", target])


def needs_restart():
    try:
        return any(os.path.realpath(f"/run/booted-system/{part}") != os.path.realpath(f"/run/current-system/{part}")
                   for part in ("kernel", "initrd", "kernel-modules"))
    except OSError:
        return False


def descendants(pid):
    children = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            fields = Path(f"/proc/{entry}/stat").read_text().rsplit(")", 1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(entry))
        except (OSError, IndexError, ValueError):
            continue
    found, stack = [], [pid]
    while stack:
        for child in children.get(stack.pop(), []):
            found.append(child)
            stack.append(child)
    return found


def process_alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def stop_process_tree(proc, grace=10.0):
    """TERM the child and everything it started, KILL what ignores that."""
    pids = [proc.pid, *descendants(proc.pid)]
    for pid in pids:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and (proc.poll() is None or any(process_alive(p) for p in pids[1:])):
        time.sleep(0.05)
    for pid in pids:
        if process_alive(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.SubprocessError):
        proc.wait(timeout=5)


def run_child(argv, lock_fd=None, **kwargs):
    """Run a build/switch in the terminal's process group (sudo needs the tty).

    subprocess.run would SIGKILL the child on an interrupt, which can orphan
    sudo's command mid-switch. Stop it gracefully instead."""
    if lock_fd is not None:
        # The terminal-facing updater may be SIGKILLed. This independent
        # guardian retains the flock while it waits/reaps the actual command,
        # including commands such as sudo that close inherited descriptors.
        argv = [sys.executable, str(Path(__file__).resolve()), "_run-child", str(lock_fd), *argv]
        kwargs["pass_fds"] = (lock_fd,)
    proc = subprocess.Popen(argv, **kwargs)
    try:
        return subprocess.CompletedProcess(argv, proc.wait())
    except BaseException:
        with cleanup_interrupts():
            stop_process_tree(proc)
        raise


def run_command(run, argv):
    try:
        return run(argv).returncode
    except OSError as error:
        print(f"Could not run {argv[0]}: {error}")
        return 127


def pending_after(status_path):
    """The reboot/shutdown the user chose while this transaction ran, if any.

    The file is discarded when a transaction starts (under the lock) and when
    it ends, so anything present now was chosen during this one."""
    if not status_path:
        return ""
    try:
        action = after_path(status_path).read_text().strip()
    except OSError:
        return ""
    return action if action in ("reboot", "shutdown") else ""


def discard_after(status_path):
    if status_path:
        with contextlib.suppress(OSError):
            after_path(status_path).unlink()


def after_action(action, run=subprocess.run):
    if action:
        session = Path(__file__).resolve().parents[1] / "settings/session.py"
        run([sys.executable, str(session), action])


def signal_code(stop):
    return 128 + (stop.signum if isinstance(stop, Interrupted) else signal.SIGINT)


APP_TARGETS = ("all", "codex", "t3", "cursor", "granola", "grok", "opencode1", "delta")


def interactive(status_path, run=None, env=None, app=None):
    if app is not None and app not in APP_TARGETS:
        raise ValueError(f"Unknown app target: {app}")
    check_run = run or subprocess.run
    status = Status(status_path)
    try:
        flake = flake_dir(env)
        transaction = Transaction(flake, env)
        transaction.acquire()
    except Busy as busy:
        print(f"Another Pond system update is already running ({busy.describe()}); not starting a second one.")
        return 3
    except (RuntimeError, OSError) as error:
        print(error)
        if not reconcile(status_path, env)["alive"]:
            status("failed", str(error))
        return 1
    status.owner = transaction.owner
    exec_run = run or (lambda argv: run_child(argv, lock_fd=transaction.descriptor))
    try:
        discard_after(status_path)  # a choice from an earlier transaction is not consent for this one
        transaction.clean_stale_temporaries()
        previous = read_json(transaction.journal_path)
        transaction.prior_pending = bool(previous.get("activation_pending", False))
        previous_app = previous.get("app_target")
        if transaction.prior_pending and app and app != "all" and app != previous_app:
            status("available", "Resume the pending update with Update all before selecting another app")
            return 1
        if transaction.prior_pending and previous_app:
            app = previous_app
        transaction.apps_complete = bool(previous.get("apps_complete", False)) if transaction.prior_pending else False
        transaction.record("start", flake=str(flake), backup=None, app_target=app,
                           apps_complete=transaction.apps_complete,
                           activation_pending=transaction.prior_pending)
        return transact(transaction, flake, status, status_path, check_run, exec_run, env, app)
    except (Interrupted, KeyboardInterrupt) as stop:
        ignore_interrupts()
        reason = REASONS.get(getattr(stop, "signum", signal.SIGINT), "interrupted")
        state, message = outcome(transaction.stage, reason, transaction.journal, flake)
        discard_after(status_path)
        status(state, message)
        transaction.record("finished", outcome="interrupted")
        say(f"\n{message}.")  # last: after SIGHUP there may be no terminal left to print to
        return signal_code(stop)
    finally:
        discard_after(status_path)  # a reboot/shutdown choice never outlives its transaction
        transaction.release()


def transact(transaction, flake, status, status_path, check_run, exec_run, env, app=None):
    resume = transaction.prior_pending
    if resume:
        # Retry the current flake independently of newer upstream revisions.
        # Rebuild it instead of reusing a potentially stale build artifact.
        current = (flake / "flake.lock").read_bytes()
        result = {"_original": current, "_lock": current, "inputs": []}
        status("updating", "Activation pending for the current flake")
        print(f"System flake: {flake}\nA previous update has not been successfully switched.\n")
    elif app and app not in ("all", "delta"):
        current = (flake / "flake.lock").read_bytes()
        result = {"_original": current, "_lock": current, "inputs": []}
        print(f"Update selected app: {app} (other app pins and flake inputs are unchanged)")
    else:
        status("updating", "Checking flake inputs")
        transaction.record("checking")
        print(f"System flake: {flake}\nChecking for newer inputs (flake.lock is not modified)…\n")
        try:
            result = check(env=env, run=check_run, keep=True,
                           inputs=("zed-delta",) if app == "delta" else ())
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            print(f"Check failed: {error}")
            status("failed", f"Check failed: {error}")
            return 1
        if not result["available"] and not app:
            print("Everything is up to date.")
            status("complete", "Up to date")
            return 0
    for change in result["inputs"]:
        when = time.strftime("%Y-%m-%d", time.localtime(change["toDate"])) if change["toDate"] else "?"
        print(f"  {change['input']:<28} {change['from']} → {change['to']}  ({when})")
    if app and not resume:
        print(f"  App updates: {app}. Downloads and package checks run before the system build.")
    print()
    transaction.record("confirm")
    question = ("Rebuild the current flake and retry switching it?" if resume else
                "Update the selected apps and inputs, then build the system?" if app else
                "Write these revisions to flake.lock and build the new system?")
    if not ask(question):
        status("available", "Update cancelled")
        return 0

    lock_file = flake / "flake.lock"
    original, written = result["_original"], result["_lock"]
    # Write exactly the revisions that were shown, not a fresh resolution, and
    # only over exactly the lock file they were resolved against.
    try:
        if not resume:
            transaction.save_backup(original)
    except OSError as error:
        print(f"Cannot save a backup of flake.lock ({error}); nothing was changed.")
        status("failed", f"Cannot save a backup of flake.lock ({error}); nothing was changed")
        return 1
    baseline = tree_fingerprint(flake)
    status("updating", "Updating flake.lock")
    transaction.record("lock-write", original_sha256=sha256(original), written_sha256=sha256(written),
                       backup=str(transaction.backup) if transaction.backup else None,
                       activation_pending=True)
    try:
        if not resume:
            replace_if_unchanged(lock_file, original, written, transaction.id)
    except (Conflict, OSError) as error:
        print(f"flake.lock was not updated: {error}")
        status("failed", f"flake.lock was not updated: {error}")
        transaction.drop_backup()
        transaction.record("finished", outcome="not-written", activation_pending=resume)
        return 1

    if app and app != "delta" and (not resume or not transaction.apps_complete):
        transaction.record("apps", activation_pending=True)
        status("updating", "Updating app pins")
        updater = flake / "scripts/apps-update.py"
        command = [sys.executable, str(updater), "--repo", str(flake), "--app", app,
                   "--user", os.environ.get("USER", ""), "--host", socket.gethostname()]
        if run_command(exec_run, command):
            status("failed", "App update failed; Update all will retry unfinished apps")
            transaction.record("finished", outcome="apps-failed")
            return 1
        transaction.record("apps-complete", apps_complete=True)
    # Accept only intentional pin writes into the pre-update fingerprint.
    # Edits to any other source during downloads must still block switching.
    if app and baseline is not None:
        current_tree = tree_fingerprint(flake)
        pins = {
            "codex": "pkgs/codex/sources.json", "t3": "pkgs/t3code/sources.json",
            "cursor": "pkgs/code-cursor/sources.json", "granola": "pkgs/granola/default.nix",
            "grok": "pkgs/grok-cli/default.nix", "opencode1": "pkgs/opencode1/sources.json",
        }
        if current_tree is not None:
            for target, path in pins.items():
                if app in ("all", target):
                    baseline[path] = current_tree.get(path)

    build, switch = builder(flake)
    transaction.record("building")
    status("updating", "Building the new system")
    print("\n$ " + " ".join(build))
    interrupted = None
    try:
        code = run_command(exec_run, build)
    except (Interrupted, KeyboardInterrupt) as stop:
        # Ctrl-C reaches the build too and the terminal is still here to answer
        # the restore question; any other signal means nobody is.
        if getattr(stop, "signum", signal.SIGINT) != signal.SIGINT:
            raise
        interrupted, code = stop, 130
    if code:
        print("\nThe build was interrupted." if interrupted else "\nThe build failed.")
        if resume:
            status("failed", "Rebuild failed; activation remains pending")
            transaction.record("finished", outcome="build-failed")
            return signal_code(interrupted) if interrupted else 1
        return build_failed(transaction, lock_file, original, written, status, interrupted)

    transaction.record("built")
    print("\nThe new system built successfully. Switching needs your password.")
    problem = drift(lock_file, written, flake, baseline)
    if not problem and not ask("Switch to it now?"):
        print("flake.lock is updated; switch later with your usual rebuild command.")
        status("available", "Built; switch pending")
        transaction.record("finished", outcome="switch-declined")
        return 0
    problem = problem or drift(lock_file, written, flake, baseline)
    if problem:
        print(f"\nNot switching: {problem}.\nThe build no longer matches the flake. flake.lock holds the new "
              "revisions; review the changes, then run the update again.")
        status("failed", f"Not switched: {problem}")
        transaction.record("finished", outcome="drifted")
        return 1

    transaction.record("switching")
    status("updating", "Switching (authenticating)")
    print("\n$ " + " ".join(switch))
    if run_command(exec_run, switch):
        print("\nThe switch failed. A failed switch can leave the system partially activated (services "
              "restarted, boot entry written), so check the output above and `systemctl --failed` before "
              "relying on it. flake.lock still holds the new revisions.")
        status("failed", "Switch failed; the system may be partially activated (see the terminal)")
        transaction.record("finished", outcome="switch-failed")
        return 1
    print("\nDone. Review and commit the updated app pins and flake.lock.")
    if app and shutil.which("auseklis-updates"):
        run_command(exec_run, ["auseklis-updates", "check"])
    action = pending_after(status_path)  # read before the shell can react to the status
    restart = needs_restart()
    transaction.drop_backup()
    transaction.record("finished", outcome="switched", activation_pending=False)
    status("restart" if restart else "complete", "Restart to use the new kernel" if restart else "")
    discard_after(status_path)
    after_action(action, exec_run)
    return 0


def drift(lock_file, written, flake, baseline):
    """Why the build no longer matches the flake, or "" if nothing changed."""
    try:
        if lock_file.read_bytes() != written:
            return "flake.lock changed after this update wrote it"
    except OSError as error:
        return f"flake.lock is unreadable ({error})"
    changed = changed_paths(baseline, tree_fingerprint(flake))
    if changed:
        shown = ", ".join(changed[:4]) + (f" and {len(changed) - 4} more" if len(changed) > 4 else "")
        return f"flake files changed since the build began ({shown})"
    return ""


def build_failed(transaction, lock_file, original, written, status, interrupted):
    if transaction.journal.get("app_target"):
        # Dependency hashes belong to the input/toolchain used to produce them.
        # Keep the pair together instead of restoring only the old toolchain.
        message = "Build failed; app pins and matching flake inputs kept together. Resume with Update all"
        print(message + ". Original lock backup: " + str(transaction.backup))
        status("failed", message)
        transaction.record("finished", outcome="build-failed", activation_pending=True)
        return signal_code(interrupted) if interrupted else 1
    transaction.record("restore")
    saved = f" (original saved at {transaction.backup})"
    if not ask("Restore the previous flake.lock?", default=True):
        print(f"flake.lock keeps the new revisions{saved}.")
        message = f"Build failed; flake.lock keeps the new revisions{saved}"
    else:
        try:
            replace_if_unchanged(lock_file, written, original, transaction.id)
        except (Conflict, OSError) as error:
            print(f"flake.lock was NOT restored: {error}.\nIts current contents are left exactly as they are; "
                  f"the original is saved at {transaction.backup}.")
            message = f"Build failed; flake.lock was not restored ({error}){saved}"
        else:
            print("flake.lock restored.")
            transaction.drop_backup()
            transaction.journal["activation_pending"] = transaction.prior_pending
            message = "Build failed; flake.lock restored"
    status("failed", "Build interrupted; " + message.removeprefix("Build failed; ") if interrupted else message)
    transaction.record("finished", outcome="build-failed")
    return signal_code(interrupted) if interrupted else 1


def main():
    action, *args = sys.argv[1:] or ["check"]
    if action == "_run-child":
        # Internal guardian: keep the inherited flock open until child
        # cleanup is complete. Pass it onward as another lifetime reference.
        descriptor = int(args[0])
        try:
            with interrupts_raise():
                result = run_child(args[1:], pass_fds=(descriptor,))
                sys.exit(result.returncode if result.returncode >= 0 else 128 - result.returncode)
        except (Interrupted, KeyboardInterrupt) as stop:
            sys.exit(signal_code(stop))
        except OSError as error:
            say(f"Could not run {args[1]}: {error}")
            sys.exit(127)
        finally:
            os.close(descriptor)
    elif action == "check":
        try:
            print(json.dumps(check()))
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            print(json.dumps({"error": str(error)}))
            sys.exit(1)
    elif action == "reconcile":
        print(json.dumps(reconcile(args[0] if args else "")))
    elif action == "run":
        try:
            with interrupts_raise():
                code = interactive(args[0] if args else "", app=args[1] if len(args) > 1 else None)
        except Interrupted as stop:
            sys.exit(signal_code(stop))
        try:
            sys.stdout.flush()
        except OSError:  # the terminal went away; don't turn that into a flush error at exit
            os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
        if sys.stdin.isatty():
            try:
                input("\nPress Enter to close.")
            except EOFError:
                pass
        sys.exit(code)
    else:
        print(json.dumps({"error": "Unknown action"}))
        sys.exit(2)


if __name__ == "__main__":
    main()
