"""Updater transaction safety: serialization, lock-file protection, switch
drift detection, signal cleanup, and recovery of abandoned updates.

Everything runs against a temporary fake flake with fake `nix`/`nh` runners.
Nothing here touches the real flake, network, sudo, or session: the
end-to-end class launches nix.py as a real process with a private PATH that
contains only the fakes.
"""
import builtins
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from unittest import mock

from fakes import ROOT

spec = importlib.util.spec_from_file_location("pond_nix_transactions", ROOT / "shell/updates/nix.py")
nix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nix)
NIX_PY = ROOT / "shell/updates/nix.py"


def lock(revs):
    nodes = {"root": {"inputs": {name: name for name in revs}}}
    for name, rev in revs.items():
        nodes[name] = {"locked": {"rev": rev, "lastModified": 1700000000}}
    return {"nodes": nodes, "root": "root", "version": 7}


ORIGINAL = json.dumps(lock({"nixpkgs": "a" * 40, "hyprland": "b" * 40}))
NEWER = lock({"nixpkgs": "c" * 40, "hyprland": "b" * 40})
OTHER = json.dumps(lock({"nixpkgs": "d" * 40, "hyprland": "e" * 40}))


class Fake:
    """Stands in for `nix flake update --output-lock-file` and the builders.

    build/switch are exit codes, or an exception to raise (a missing binary,
    a signal arriving mid-command)."""

    def __init__(self, latest=NEWER, build=0, switch=0, on_check=None, on_build=None, on_switch=None):
        self.latest, self.build, self.switch = latest, build, switch
        self.on_check, self.on_build, self.on_switch = on_check, on_build, on_switch
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ["nix", "flake", "update"]:
            if self.on_check:
                self.on_check()
            Path(argv[argv.index("--output-lock-file") + 1]).write_text(json.dumps(self.latest))
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv in (["build"], ["switch"]):
            hook, result = (self.on_build, self.build) if argv == ["build"] else (self.on_switch, self.switch)
            if hook:
                hook()
            if isinstance(result, BaseException) or (isinstance(result, type) and issubclass(result, BaseException)):
                raise result
            return subprocess.CompletedProcess(argv, result, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")  # e.g. the session helper

    @property
    def switched(self):
        return ["switch"] in self.calls

    @property
    def session_actions(self):
        return [call[-1] for call in self.calls if len(call) == 3 and call[1].endswith("session.py")]


class Result:
    def __init__(self, code, status, output, prompts):
        self.code, self.output, self.prompts = code, output, prompts
        lines = status.split("\n") if status else ["", ""]
        self.state, self.message = lines[0], lines[1] if len(lines) > 1 else ""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.flake = root / "flake"
        self.flake.mkdir()
        (self.flake / "flake.nix").write_text("{ }")
        (self.flake / "modules.nix").write_text("one")
        self.lock_file = self.flake / "flake.lock"
        self.lock_file.write_text(ORIGINAL)
        self.status = root / "run" / "pond-update"
        self.env = mock.patch.dict(os.environ, {"POND_FLAKE": str(self.flake), "HOME": str(root),
                                                "XDG_STATE_HOME": str(root / "state")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def interactive(self, answers, fake, status=None):
        replies, prompts, output = iter(answers), [], []

        def reply(prompt=""):
            prompts.append(prompt)
            try:
                value = next(replies)
            except StopIteration:
                raise AssertionError(f"unexpected question: {prompt!r}") from None
            if isinstance(value, BaseException):
                raise value
            return value() if callable(value) else value

        status = self.status if status is None else status
        with mock.patch.object(builtins, "input", reply), \
                mock.patch.object(nix, "builder", lambda flake: (["build"], ["switch"])), \
                mock.patch.object(nix, "needs_restart", lambda: False), \
                mock.patch("builtins.print", lambda *a, **k: output.append(" ".join(map(str, a)))):
            code = nix.interactive(str(status), run=fake)
        text = Path(status).read_text() if status and Path(status).exists() else ""
        return Result(code, text, "\n".join(output), prompts)

    def journal(self):
        return nix.read_json(nix.state_dir(self.flake) / "journal.json")

    def backups(self):
        return list(nix.state_dir(self.flake).glob("flake.lock.*.orig"))

    def lock_text(self):
        return self.lock_file.read_text()

    def free(self):
        transaction = nix.Transaction(self.flake)
        transaction.acquire()
        transaction.release()


def write(path, text):
    def action():
        Path(path).write_text(text)
        return "y"
    return action


# --- A7: serialization and lock-file protection -------------------------------

class Serialization(Base):
    def test_second_update_is_refused_and_leaves_the_first_ones_status_alone(self):
        first = nix.Transaction(self.flake)
        first.acquire()
        nix.Status(self.status, first.owner)("updating", "Building the new system")
        before = self.status.read_text()
        fake = Fake()
        result = self.interactive([], fake)
        self.assertEqual(result.code, 3)
        self.assertIn("already running", result.output)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.status.read_text(), before)
        self.assertEqual(self.lock_text(), ORIGINAL)
        first.release()
        self.assertEqual(self.interactive(["n"], Fake()).state, "available")  # free again

    def test_lock_is_released_by_every_exit(self):
        for answers, fake in [(["n"], Fake()), (["y", "y"], Fake()), (["y", ""], Fake(build=1))]:
            self.lock_file.write_text(ORIGINAL)
            self.interactive(answers, fake)
            self.free()

    def test_updater_waits_out_a_reconcile_probe_but_not_another_updater(self):
        probe = nix.Transaction(self.flake)
        probe.acquire(role="reconcile")
        timer = threading.Timer(0.3, probe.release)
        timer.start()
        started = time.monotonic()
        updater = nix.Transaction(self.flake)
        updater.acquire()
        self.assertGreaterEqual(time.monotonic() - started, 0.2)
        updater.release()
        timer.join()
        updater.acquire()
        with self.assertRaises(nix.Busy):
            nix.Transaction(self.flake).acquire()
        updater.release()

    def test_temporary_files_are_unique_and_never_left_behind(self):
        names = []
        real = nix.tempfile.mkstemp

        def spy(*args, **kwargs):
            descriptor, name = real(*args, **kwargs)
            names.append(Path(name).name)
            return descriptor, name
        with mock.patch.object(nix.tempfile, "mkstemp", spy):
            self.interactive(["y", "y"], Fake())
            self.lock_file.write_text(ORIGINAL)
            self.interactive(["y", ""], Fake(build=1))
        self.assertGreaterEqual(len(names), 4)  # every write went through a fresh temporary
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn(".flake.lock.pond", names)
        self.assertEqual(sorted(p.name for p in self.flake.iterdir()), ["flake.lock", "flake.nix", "modules.nix"])

    def test_status_writers_do_not_share_a_temporary(self):
        errors = []

        def hammer(index):
            try:
                for count in range(40):
                    nix.Status(self.status, {"txn": str(index)})("updating", f"step {count}")
            except Exception as error:  # noqa: BLE001
                errors.append(error)
        threads = [threading.Thread(target=hammer, args=(i,)) for i in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        state, message, owner = nix.read_status(self.status)
        self.assertEqual((state, message.split()[0]), ("updating", "step"))
        self.assertIn("txn", owner)

    def test_stale_temporaries_from_dead_transactions_are_cleared(self):
        (self.flake / ".flake.lock.pond").write_text("old")
        (self.flake / ".flake.lock.abc.tmp").write_text("old")
        self.interactive(["n"], Fake())
        self.assertFalse((self.flake / ".flake.lock.pond").exists())
        self.assertFalse((self.flake / ".flake.lock.abc.tmp").exists())

    def test_check_is_read_only_and_not_blocked_by_a_running_update(self):
        holder = nix.Transaction(self.flake)
        holder.acquire()
        before = sorted(p.name for p in self.flake.iterdir())
        state_before = sorted(p.name for p in nix.state_dir(self.flake).iterdir())
        stat = self.lock_file.stat()
        result = nix.check(run=Fake())
        self.assertTrue(result["available"])
        self.assertEqual(sorted(p.name for p in self.flake.iterdir()), before)
        self.assertEqual(sorted(p.name for p in nix.state_dir(self.flake).iterdir()), state_before)
        self.assertEqual(self.lock_file.stat().st_mtime_ns, stat.st_mtime_ns)
        holder.release()


class LockProtection(Base):
    def test_restore_is_refused_when_another_revision_was_written_during_the_build(self):
        fake = Fake(build=1, on_build=lambda: self.lock_file.write_text(OTHER))
        result = self.interactive(["y", "y"], fake)  # write, then "restore previous flake.lock?"
        self.assertEqual((result.code, result.state), (1, "failed"))
        self.assertEqual(self.lock_text(), OTHER)  # the independent edit survives
        self.assertIn("NOT restored", result.output)
        self.assertIn("not restored", result.message)
        self.assertEqual([p.read_text() for p in self.backups()], [ORIGINAL])  # the way back is kept

    def test_restore_only_when_the_lock_is_exactly_what_was_written(self):
        result = self.interactive(["y", "y"], Fake(build=1))
        self.assertEqual((result.state, result.message), ("failed", "Build failed; flake.lock restored"))
        self.assertEqual(self.lock_text(), ORIGINAL)
        self.assertEqual(self.backups(), [])

    def test_declining_the_restore_keeps_new_revisions_and_the_backup(self):
        result = self.interactive(["y", "n"], Fake(build=1))
        self.assertEqual(result.state, "failed")
        self.assertEqual(json.loads(self.lock_text()), NEWER)
        self.assertEqual([p.read_text() for p in self.backups()], [ORIGINAL])
        self.assertIn(str(self.backups()[0]), result.message)

    def test_write_is_refused_when_the_lock_changed_while_the_question_was_open(self):
        fake = Fake()
        result = self.interactive([write(self.lock_file, OTHER)], fake)
        self.assertEqual((result.code, result.state), (1, "failed"))
        self.assertEqual(self.lock_text(), OTHER)
        self.assertEqual([c for c in fake.calls if c in (["build"], ["switch"])], [])
        self.assertEqual(self.backups(), [])

    def test_missing_builder_is_a_failed_build_not_a_traceback(self):
        result = self.interactive(["y", "y"], Fake(build=FileNotFoundError("nh")))
        self.assertEqual((result.code, result.state), (1, "failed"))
        self.assertIn("Could not run build", result.output)
        self.assertEqual(self.lock_text(), ORIGINAL)

    def test_in_place_edit_during_replacement_is_preserved_and_reported(self):
        real = os.replace

        def late_edit(source, destination):
            with open(destination, "a") as handle:  # a writer that does not take our lock
                handle.write("\n# edited by someone else")
            return real(source, destination)
        with mock.patch.object(nix.os, "replace", late_edit):
            with self.assertRaisesRegex(nix.Conflict, "edit is kept in") as caught:
                nix.replace_if_unchanged(self.lock_file, ORIGINAL.encode(), b"new", "token123")
        pin = Path(str(caught.exception).rsplit(" ", 1)[1])
        self.assertTrue(pin.read_text().endswith("# edited by someone else"))

    def test_edit_while_the_replacement_is_prepared_is_not_overwritten(self):
        real = nix.stage_file

        def stage_then_edit(path, data, mode=None):
            temporary = real(path, data, mode)
            Path(path).write_text(OTHER)
            return temporary
        with mock.patch.object(nix, "stage_file", stage_then_edit):
            with self.assertRaises(nix.Conflict):
                nix.replace_if_unchanged(self.lock_file, ORIGINAL.encode(), b"new", "token123")
        self.assertEqual(self.lock_text(), OTHER)
        self.assertEqual(sorted(p.name for p in self.flake.iterdir()), ["flake.lock", "flake.nix", "modules.nix"])

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_staged_and_uncommitted_work_is_untouched(self):
        def git(*args):
            return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                                   "-C", str(self.flake), *args], capture_output=True, text=True, check=True).stdout
        git("init", "-q")
        git("add", "-A")
        git("commit", "-qm", "base")
        (self.flake / "modules.nix").write_text("staged edit")
        git("add", "modules.nix")
        (self.flake / "modules.nix").write_text("staged edit\nplus unstaged edit")
        (self.flake / "notes.txt").write_text("untracked")
        before = (git("status", "--porcelain"), git("diff", "--cached"), git("diff"))
        result = self.interactive(["y", "y"], Fake(build=1))
        self.assertEqual(result.message, "Build failed; flake.lock restored")
        self.assertEqual((git("status", "--porcelain"), git("diff", "--cached"), git("diff")), before)
        self.assertEqual((self.flake / "notes.txt").read_text(), "untracked")


# --- A7: the switch must be the build the user approved ------------------------

class SwitchDrift(Base):
    def refused(self, fake, answers):
        result = self.interactive(answers, fake)
        self.assertEqual((result.code, result.state), (1, "failed"))
        self.assertIn("Not switched", result.message)
        self.assertFalse(fake.switched)
        return result

    def test_unrelated_config_edit_during_the_build_blocks_the_switch(self):
        fake = Fake(on_build=lambda: (self.flake / "modules.nix").write_text("two"))
        result = self.refused(fake, ["y"])  # never asks "Switch to it now?"
        self.assertIn("modules.nix", result.message)
        self.assertEqual((self.flake / "modules.nix").read_text(), "two")
        self.assertEqual(json.loads(self.lock_text()), NEWER)

    def test_new_flake_file_blocks_the_switch(self):
        self.refused(Fake(on_build=lambda: (self.flake / "extra.nix").write_text("{}")), ["y"])

    def test_lock_change_during_the_build_blocks_the_switch(self):
        result = self.refused(Fake(on_build=lambda: self.lock_file.write_text(OTHER)), ["y"])
        self.assertIn("flake.lock changed", result.message)
        self.assertEqual(self.lock_text(), OTHER)

    def test_edit_while_the_switch_question_is_open_blocks_the_switch(self):
        fake = Fake()
        self.refused(fake, ["y", write(self.flake / "modules.nix", "edited at the prompt")])

    def test_unchanged_flake_switches(self):
        fake = Fake()
        self.assertEqual(self.interactive(["y", "y"], fake).state, "complete")
        self.assertTrue(fake.switched)
        self.assertEqual(self.backups(), [])

    def test_failed_switch_does_not_claim_the_system_is_unchanged(self):
        result = self.interactive(["y", "y"], Fake(switch=1))
        self.assertEqual((result.code, result.state), (1, "failed"))
        self.assertNotIn("unchanged", result.output.lower())
        self.assertIn("partially activated", result.output)
        self.assertIn("partially activated", result.message)
        self.assertEqual(json.loads(self.lock_text()), NEWER)  # a failed switch never rolls the lock back

    def test_missing_switch_command_is_a_failed_switch(self):
        result = self.interactive(["y", "y"], Fake(switch=FileNotFoundError("sudo")))
        self.assertEqual((result.code, result.state), (1, "failed"))

    def test_untracked_files_do_not_count_in_a_git_flake(self):
        if not shutil.which("git"):
            self.skipTest("needs git")
        subprocess.run(["git", "init", "-q", str(self.flake)], check=True)
        subprocess.run(["git", "-C", str(self.flake), "add", "-A"], check=True)
        fake = Fake(on_build=lambda: (self.flake / "scratch.txt").write_text("not part of the flake"))
        self.assertEqual(self.interactive(["y", "y"], fake).state, "complete")


# --- A8: signals and abandoned updates ----------------------------------------

class Interruptions(Base):
    def test_no_signal_at_any_stage_leaves_the_status_updating(self):
        stages = {
            "check": lambda sig: (Fake(on_check=self.raiser(sig)), ["y", "y"]),
            "confirm": lambda sig: (Fake(), [nix.Interrupted(sig)]),
            "build": lambda sig: (Fake(build=nix.Interrupted(sig)), ["y", "n"]),
            "switch-question": lambda sig: (Fake(), ["y", nix.Interrupted(sig)]),
            "switch": lambda sig: (Fake(switch=nix.Interrupted(sig)), ["y", "y"]),
        }
        for stage, make in stages.items():
            for sig in nix.TERMINAL_SIGNALS:
                with self.subTest(stage=stage, signal=sig.name):
                    self.lock_file.write_text(ORIGINAL)
                    fake, answers = make(sig)
                    result = self.interactive(answers, fake)
                    self.assertEqual(result.code, 128 + sig)
                    self.assertIn(result.state, ("failed", "available"))
                    self.assertTrue(result.message)
                    self.free()  # the lock is not leaked
                    self.assertFalse(self.status.with_name("pond-update.after").exists())

    @staticmethod
    def raiser(sig):
        def action():
            raise nix.Interrupted(sig)
        return action

    def test_sigterm_during_build_reports_where_the_lock_file_stands(self):
        result = self.interactive(["y"], Fake(build=nix.Interrupted(signal.SIGTERM)))
        self.assertEqual((result.code, result.state), (143, "failed"))
        self.assertIn("terminated while building", result.message)
        self.assertIn("flake.lock holds the new revisions", result.message)
        self.assertEqual(json.loads(self.lock_text()), NEWER)  # no unattended mutation
        self.assertEqual([p.read_text() for p in self.backups()], [ORIGINAL])
        self.assertEqual(self.journal()["outcome"], "interrupted")

    def test_ctrl_c_during_build_offers_the_restore_like_any_failed_build(self):
        result = self.interactive(["y", "y"], Fake(build=nix.Interrupted(signal.SIGINT)))
        self.assertEqual((result.code, result.state), (130, "failed"))
        self.assertEqual(result.message, "Build interrupted; flake.lock restored")
        self.assertEqual(self.lock_text(), ORIGINAL)

    def test_keyboard_interrupt_is_treated_as_ctrl_c(self):
        result = self.interactive(["y", "y"], Fake(build=KeyboardInterrupt()))
        self.assertEqual((result.code, result.state), (130, "failed"))
        self.assertEqual(self.lock_text(), ORIGINAL)

    def test_ctrl_c_at_the_first_question_changes_nothing(self):
        result = self.interactive([nix.Interrupted(signal.SIGINT)], Fake())
        self.assertEqual((result.code, result.state), (130, "available"))
        self.assertIn("before any change", result.message)
        self.assertEqual(self.lock_text(), ORIGINAL)
        self.assertEqual(self.backups(), [])

    def test_signal_at_the_switch_question_keeps_the_build_pending(self):
        result = self.interactive(["y", nix.Interrupted(signal.SIGHUP)], Fake())
        self.assertEqual((result.code, result.state), (129, "available"))
        self.assertIn("switch pending", result.message)

    def test_signal_during_the_switch_warns_of_partial_activation(self):
        result = self.interactive(["y", "y"], Fake(switch=nix.Interrupted(signal.SIGTERM)))
        self.assertEqual(result.state, "failed")
        self.assertIn("partially activated", result.message)

    def test_unwritable_status_does_not_stop_the_update(self):
        blocked = Path(self.tmp.name) / "file"
        blocked.write_text("")
        result = self.interactive(["y", "y"], Fake(), status=blocked / "status")
        self.assertEqual(result.code, 0)
        self.assertEqual(json.loads(self.lock_text()), NEWER)


class AbandonedUpdates(Base):
    def leave(self, stage, **journal):
        """A transaction that stopped without cleanup: lock free, status 'updating'."""
        transaction = nix.Transaction(self.flake)
        transaction.acquire()
        transaction.record(stage, flake=str(self.flake), **journal)
        owner = transaction.owner
        transaction.release()
        nix.Status(self.status, owner)("updating", "Building the new system")
        return transaction

    def test_vanished_owner_becomes_actionable(self):
        self.leave("confirm")
        result = nix.reconcile(str(self.status))
        self.assertTrue(result["abandoned"])
        self.assertFalse(result["alive"])
        self.assertEqual(result["state"], "available")
        self.assertEqual(nix.read_status(self.status)[0], "available")
        self.assertIn("before any change", result["message"])

    def test_abandoned_build_says_what_the_lock_file_holds(self):
        new = json.dumps(NEWER).encode()
        backup = Path(self.tmp.name) / "orig"
        for written, expected in [(ORIGINAL.encode(), "flake.lock is unchanged"),
                                  (new, "flake.lock holds the new revisions")]:
            self.lock_file.write_bytes(new if expected.startswith("flake.lock holds") else ORIGINAL.encode())
            self.leave("building", original_sha256=nix.sha256(ORIGINAL.encode()),
                       written_sha256=nix.sha256(new), backup=str(backup))
            result = nix.reconcile(str(self.status))
            self.assertEqual(result["state"], "failed")
            self.assertIn("while building", result["message"])
            self.assertIn(expected, result["message"])
        self.lock_file.write_text(OTHER)
        self.leave("building", original_sha256=nix.sha256(ORIGINAL.encode()),
                   written_sha256=nix.sha256(new), backup=str(backup))
        self.assertIn("changed by something else", nix.reconcile(str(self.status))["message"])

    def test_abandoned_switch_warns_of_partial_activation(self):
        self.leave("switching")
        result = nix.reconcile(str(self.status))
        self.assertEqual(result["state"], "failed")
        self.assertIn("partially activated", result["message"])

    def test_abandoned_before_a_reboot_is_reported_as_such(self):
        self.leave("building", boot="a-previous-boot")
        self.assertIn("restart of the machine", nix.reconcile(str(self.status))["message"])

    def test_reconcile_runs_once_and_never_touches_the_lock_file_or_a_stale_reboot_choice(self):
        self.leave("building")
        after = self.status.with_name("pond-update.after")
        after.write_text("reboot")
        stat = self.lock_file.stat()
        self.assertTrue(nix.reconcile(str(self.status))["abandoned"])
        self.assertFalse(after.exists())
        again = nix.reconcile(str(self.status))
        self.assertFalse(again["abandoned"])
        self.assertEqual(again["state"], "failed")
        self.assertEqual((self.lock_file.read_text(), self.lock_file.stat().st_mtime_ns),
                         (ORIGINAL, stat.st_mtime_ns))

    def test_a_reused_pid_does_not_make_a_dead_update_look_alive(self):
        transaction = self.leave("building")
        owner = dict(nix.read_status(self.status)[2], pid=os.getpid(), start=nix.process_start(os.getpid()))
        nix.Status(self.status, owner)("updating", "x")
        self.assertTrue(nix.process_alive(owner["pid"]))
        self.assertTrue(nix.reconcile(str(self.status))["abandoned"])
        self.assertTrue(transaction.id)

    def test_a_live_owner_is_alive_whatever_its_recorded_pid_says(self):
        holder = subprocess.Popen([sys.executable, "-c", (
            "import fcntl, os, sys, time\n"
            f"fd = os.open({str(nix.state_dir(self.flake) / 'lock')!r}, os.O_RDWR | os.O_CREAT)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\nprint('held', flush=True)\ntime.sleep(60)\n")], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (holder.kill(), holder.wait(), holder.stdout.close()))
        self.assertEqual(holder.stdout.readline().strip(), "held")
        nix.Status(self.status, {"txn": "t", "pid": 2 ** 22 + 12345, "lock": str(nix.state_dir(self.flake) / "lock"),
                                 "flake": str(self.flake)})("updating", "Building the new system")
        before = self.status.read_text()
        result = nix.reconcile(str(self.status))
        self.assertEqual((result["alive"], result["abandoned"]), (True, False))
        self.assertEqual(self.status.read_text(), before)

    def test_legacy_status_without_an_owner_is_recovered_through_the_flake(self):
        self.status.parent.mkdir(parents=True, exist_ok=True)
        self.status.write_text("updating\nBuilding the new system\n")
        self.assertTrue(nix.reconcile(str(self.status))["abandoned"])

    def test_a_status_with_no_flake_and_no_owner_is_recovered_not_crashed_on(self):
        self.status.parent.mkdir(parents=True, exist_ok=True)
        self.status.write_text("updating\nx\n")
        with mock.patch.dict(os.environ, {"POND_FLAKE": self.tmp.name + "/gone"}):
            self.assertTrue(nix.reconcile(str(self.status))["abandoned"])
        self.assertEqual(nix.read_status(self.status)[0], "available")

    def test_settled_states_are_left_alone(self):
        self.assertEqual(nix.reconcile(str(self.status))["state"], "")  # no file at all
        for state in ("failed", "complete", "available"):
            nix.Status(self.status)(state, "m")
            stat = self.status.stat()
            result = nix.reconcile(str(self.status))
            self.assertEqual((result["state"], result["abandoned"]), (state, False))
            self.assertEqual(self.status.stat().st_mtime_ns, stat.st_mtime_ns)

    def test_restart_state_reports_whether_a_restart_is_still_needed(self):
        nix.Status(self.status)("restart", "Restart to use the new kernel")
        with mock.patch.object(nix, "needs_restart", lambda: True):
            self.assertTrue(nix.reconcile(str(self.status))["restartNeeded"])
        with mock.patch.object(nix, "needs_restart", lambda: False):
            self.assertFalse(nix.reconcile(str(self.status))["restartNeeded"])


class StaleRebootChoice(Base):
    after = property(lambda self: self.status.with_name("pond-update.after"))

    def test_a_choice_from_an_earlier_transaction_never_reboots(self):
        self.after.parent.mkdir(parents=True, exist_ok=True)
        self.after.write_text("reboot")
        fake = Fake()
        self.assertEqual(self.interactive(["y", "y"], fake).state, "complete")
        self.assertEqual(fake.session_actions, [])
        self.assertFalse(self.after.exists())

    def test_a_choice_made_during_this_transaction_is_honored(self):
        fake = Fake(on_build=lambda: self.after.write_text("reboot"))
        self.assertEqual(self.interactive(["y", "y"], fake).state, "complete")
        self.assertEqual(fake.session_actions, ["reboot"])
        self.assertFalse(self.after.exists())

    def test_a_choice_is_dropped_when_the_update_does_not_finish(self):
        for fake, answers in [(Fake(build=1, on_build=lambda: self.after.write_text("reboot")), ["y", "n"]),
                              (Fake(switch=1, on_build=lambda: self.after.write_text("shutdown")), ["y", "y"]),
                              (Fake(on_build=lambda: self.after.write_text("reboot")), ["y", "n"])]:
            self.lock_file.write_text(ORIGINAL)
            self.interactive(answers, fake)
            self.assertEqual(fake.session_actions, [])
            self.assertFalse(self.after.exists())

    def test_only_known_actions_count(self):
        self.after.parent.mkdir(parents=True, exist_ok=True)
        for text, expected in (("rm -rf", ""), ("reboot", "reboot"), ("shutdown\n", "shutdown"), ("", "")):
            self.after.write_text(text)
            self.assertEqual(nix.pending_after(str(self.status)), expected)
        self.after.unlink()
        self.assertEqual(nix.pending_after(str(self.status)), "")
        self.assertEqual(nix.pending_after(""), "")


# --- Real processes: signals, orphans, SIGKILL, concurrency --------------------

FAKE_NIX = """#!{python}
import os, shutil, sys
args = sys.argv[1:]
if args[:2] == ["flake", "update"]:
    shutil.copy(os.environ["FAKE_LATEST"], args[args.index("--output-lock-file") + 1])
    sys.exit(0)
sys.exit(2)
"""

FAKE_NH = """#!{python}
import os, subprocess, sys, time
kind = sys.argv[2]
marks = os.environ["FAKE_MARKS"]
open(os.path.join(marks, kind + ".started"), "w").write(str(os.getpid()))
if os.environ.get("FAKE_HANG") == kind:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1000)"])
    open(os.path.join(marks, kind + ".grandchild"), "w").write(str(child.pid))
    time.sleep(1000)
sys.exit(int(os.environ.get("FAKE_" + kind.upper() + "_CODE", "0")))
"""


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.flake = root / "flake"
        self.flake.mkdir()
        (self.flake / "flake.nix").write_text("{ }")
        self.lock_file = self.flake / "flake.lock"
        self.lock_file.write_text(ORIGINAL)
        self.marks = root / "marks"
        self.marks.mkdir()
        self.status = root / "run" / "pond-update"
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name, body in (("nix", FAKE_NIX), ("nh", FAKE_NH)):
            (bin_dir / name).write_text(body.format(python=sys.executable))
            (bin_dir / name).chmod(0o755)
        no_builder = root / "nix-only"
        no_builder.mkdir()
        shutil.copy(bin_dir / "nix", no_builder / "nix")
        (root / "latest.json").write_text(json.dumps(NEWER))
        self.no_builder = str(no_builder)
        self.env = {"PATH": str(bin_dir), "HOME": str(root), "XDG_STATE_HOME": str(root / "state"),
                    "POND_FLAKE": str(self.flake), "FAKE_LATEST": str(root / "latest.json"),
                    "FAKE_MARKS": str(self.marks)}
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
            if proc.stdin:
                proc.stdin.close()
        for mark in self.marks.glob("*.grandchild"):
            try:
                os.kill(int(mark.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        self.tmp.cleanup()

    def start(self, *args, env=None, stdin=subprocess.PIPE):
        proc = subprocess.Popen([sys.executable, str(NIX_PY), *args], stdin=stdin, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env={**self.env, **(env or {})}, start_new_session=True)
        self.procs.append(proc)
        return proc

    def run_helper(self, *args, stdin=b"", env=None):
        return subprocess.run([sys.executable, str(NIX_PY), *args], input=stdin, capture_output=True,
                              env={**self.env, **(env or {})}, timeout=30, start_new_session=True)

    def wait_for(self, condition, what, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.02)
        self.fail(f"timed out waiting for {what}")

    def journal(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.env["XDG_STATE_HOME"], "HOME": self.env["HOME"]}):
            return nix.read_json(nix.state_dir(self.flake) / "journal.json")

    def at_stage(self, stage):
        return lambda: self.journal().get("stage") == stage

    def mark(self, name):
        return lambda: (self.marks / name).exists() and (self.marks / name).read_text()

    def status_state(self):
        return nix.read_status(self.status)[0]

    def gone(self, pid):
        return self.wait_for(lambda: not nix.process_alive(pid), f"process {pid} to exit", timeout=15) or True

    def free(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.env["XDG_STATE_HOME"], "HOME": self.env["HOME"]}):
            transaction = nix.Transaction(self.flake)
            transaction.acquire()
            transaction.release()

    def test_simultaneous_updates_are_serialized(self):
        first = self.start("run", str(self.status))
        self.wait_for(self.at_stage("confirm"), "the first updater to reach its question")
        before = self.status.read_text()
        second = self.run_helper("run", str(self.status))
        self.assertEqual(second.returncode, 3)
        self.assertIn(b"already running", second.stdout)
        self.assertEqual(self.status.read_text(), before)
        self.assertEqual(self.lock_file.read_text(), ORIGINAL)
        first.stdin.write(b"y\ny\n")
        first.stdin.flush()
        self.assertEqual(first.wait(timeout=30), 0)
        self.assertEqual(json.loads(self.lock_file.read_text()), NEWER)
        self.assertEqual(self.journal()["outcome"], "switched")
        self.assertIn(self.status_state(), ("complete", "restart"))
        self.assertEqual(sorted(p.name for p in self.flake.iterdir()), ["flake.lock", "flake.nix"])

    def test_terminal_signals_during_the_build_clean_up_and_stop_the_whole_build(self):
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=sig.name):
                self.lock_file.write_text(ORIGINAL)
                # Each signal exercises a fresh update, not resumption of
                # the prior subtest's deliberately unactivated revisions.
                shutil.rmtree(nix.state_dir(self.flake, self.env, create=False), ignore_errors=True)
                for leftover in self.marks.iterdir():
                    leftover.unlink()
                proc = self.start("run", str(self.status), env={"FAKE_HANG": "build"})
                proc.stdin.write(b"y\ny\n")  # write the lock; (Ctrl-C only) restore it
                proc.stdin.flush()
                build_pid = int(self.wait_for(self.mark("build.started"), "the build to start"))
                grandchild = int(self.wait_for(self.mark("build.grandchild"), "the build's own child"))
                self.assertEqual(self.status_state(), "updating")
                os.kill(proc.pid, sig)
                self.assertEqual(proc.wait(timeout=30), 128 + sig)
                self.gone(build_pid)
                self.gone(grandchild)
                state, message, _ = nix.read_status(self.status)
                self.assertEqual(state, "failed")
                if sig == signal.SIGINT:
                    self.assertEqual(message, "Build interrupted; flake.lock restored")
                    self.assertEqual(self.lock_file.read_text(), ORIGINAL)
                else:
                    self.assertIn("while building", message)
                    self.assertIn("flake.lock holds the new revisions", message)
                    self.assertEqual(json.loads(self.lock_file.read_text()), NEWER)
                self.free()

    def on_terminal(self, **env):
        """nix.py as the session leader of a real pty, like the Ghostty window."""
        master, slave = pty.openpty()
        proc = subprocess.Popen([sys.executable, str(NIX_PY), "run", str(self.status)], stdin=slave, stdout=slave,
                                stderr=slave, env={**self.env, **env}, start_new_session=True,
                                preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        os.close(slave)
        self.procs.append(proc)

        def close():
            try:
                os.close(master)
            except OSError:  # a test hung up on purpose
                pass
        self.addCleanup(close)
        return proc, master

    def read_until(self, master, needle, timeout=20):
        text, deadline = b"", time.monotonic() + timeout
        while needle.encode() not in text and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    text += os.read(master, 4096)
                except OSError:
                    break
        self.assertIn(needle.encode(), text)
        return text

    def test_ctrl_c_on_a_real_terminal_reaches_the_build_and_offers_the_restore(self):
        proc, master = self.on_terminal(FAKE_HANG="build")
        self.read_until(master, "[y/N]")
        os.write(master, b"y\n")
        grandchild = int(self.wait_for(self.mark("build.grandchild"), "the build's own child"))
        os.write(master, b"\x03")  # the tty delivers SIGINT to the whole foreground group
        self.read_until(master, "Restore the previous flake.lock? [Y/n]")
        os.write(master, b"y\n")
        self.read_until(master, "Press Enter to close")
        os.write(master, b"\n")
        self.assertEqual(proc.wait(timeout=30), 130)
        self.assertEqual(nix.read_status(self.status)[:2], ("failed", "Build interrupted; flake.lock restored"))
        self.assertEqual(self.lock_file.read_text(), ORIGINAL)
        self.gone(grandchild)
        self.free()

    def test_closing_the_terminal_window_marks_the_update_failed(self):
        proc, master = self.on_terminal(FAKE_HANG="build")
        self.read_until(master, "[y/N]")
        os.write(master, b"y\n")
        grandchild = int(self.wait_for(self.mark("build.grandchild"), "the build's own child"))
        os.close(master)  # hangup: the kernel sends SIGHUP to the foreground group
        self.assertEqual(proc.wait(timeout=30), 129)
        state, message, _ = nix.read_status(self.status)
        self.assertEqual(state, "failed")
        self.assertIn("terminal closed", message)
        self.assertIn("flake.lock holds the new revisions", message)
        self.gone(grandchild)
        self.free()

    def test_signal_at_the_first_question_changes_nothing(self):
        proc = self.start("run", str(self.status))
        self.wait_for(self.at_stage("confirm"), "the question")
        os.kill(proc.pid, signal.SIGHUP)
        self.assertEqual(proc.wait(timeout=30), 129)
        self.assertEqual(self.status_state(), "available")
        self.assertEqual(self.lock_file.read_text(), ORIGINAL)
        self.free()

    def test_killed_updater_keeps_mutex_until_its_orphaned_build_finishes(self):
        proc = self.start("run", str(self.status), env={"FAKE_HANG": "build"})
        proc.stdin.write(b"y\n")
        proc.stdin.flush()
        build_pid = int(self.wait_for(self.mark("build.started"), "the build to start"))
        self.assertEqual(self.status_state(), "updating")
        os.kill(proc.pid, signal.SIGKILL)  # no handler can run; the orphaned build lives on
        proc.wait()
        self.assertTrue(nix.process_alive(build_pid))
        result = json.loads(self.run_helper("reconcile", str(self.status)).stdout)
        self.assertEqual((result["abandoned"], result["alive"], result["state"]), (False, True, "updating"))
        retry = self.run_helper("run", str(self.status), stdin=b"n\n")
        self.assertEqual(retry.returncode, 3)
        self.assertTrue(nix.process_alive(build_pid))
        os.kill(int(self.wait_for(self.mark("build.grandchild"), "build descendant")), signal.SIGTERM)
        os.kill(build_pid, signal.SIGTERM)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = json.loads(self.run_helper("reconcile", str(self.status)).stdout)
            if not result["alive"]:
                break
            time.sleep(0.02)
        self.assertEqual((result["abandoned"], result["alive"], result["state"]), (True, False, "failed"))
        self.assertIn("flake.lock holds the new revisions", result["message"])
        self.assertEqual(self.status_state(), "failed")
        backups = list(Path(self.env["XDG_STATE_HOME"]).rglob("flake.lock.*.orig"))
        self.assertEqual([p.read_text() for p in backups], [ORIGINAL])
        again = json.loads(self.run_helper("reconcile", str(self.status)).stdout)
        self.assertFalse(again["abandoned"])
        # ...and the update is actionable again: a new one starts at once.
        self.lock_file.write_text(ORIGINAL)
        retry = self.run_helper("run", str(self.status), stdin=b"n\n")
        self.assertEqual(retry.returncode, 0)
        self.assertEqual(self.status_state(), "available")

    def test_killed_updater_cannot_overlap_an_orphaned_switch(self):
        proc = self.start("run", str(self.status), env={"FAKE_HANG": "switch"})
        proc.stdin.write(b"y\ny\n")
        proc.stdin.flush()
        switch_pid = int(self.wait_for(self.mark("switch.started"), "the switch to start"))
        grandchild = int(self.wait_for(self.mark("switch.grandchild"), "switch descendant"))
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait()
        result = json.loads(self.run_helper("reconcile", str(self.status)).stdout)
        self.assertTrue(result["alive"])
        self.assertFalse(result["abandoned"])
        self.assertEqual(self.run_helper("run", str(self.status), stdin=b"y\ny\n").returncode, 3)
        self.assertTrue(nix.process_alive(switch_pid))
        os.kill(grandchild, signal.SIGTERM)
        os.kill(switch_pid, signal.SIGTERM)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = json.loads(self.run_helper("reconcile", str(self.status)).stdout)
            if not result["alive"]:
                break
            time.sleep(0.02)
        self.assertFalse(result["alive"])
        self.assertIn("partially activated", result["message"])

    def test_reconcile_leaves_a_live_update_alone(self):
        proc = self.start("run", str(self.status))
        self.wait_for(self.at_stage("confirm"), "the question")
        before = self.status.read_text()
        result = json.loads(self.run_helper("reconcile", str(self.status)).stdout)
        self.assertEqual((result["alive"], result["abandoned"], result["state"]), (True, False, "updating"))
        self.assertEqual(self.status.read_text(), before)
        proc.stdin.write(b"n\n")
        proc.stdin.flush()
        self.assertEqual(proc.wait(timeout=30), 0)

    def test_launch_failures_leave_a_terminal_status(self):
        gone = self.run_helper("run", str(self.status), env={"POND_FLAKE": self.tmp.name + "/nope"})
        self.assertEqual(gone.returncode, 1)
        self.assertEqual(nix.read_status(self.status)[0], "failed")
        # No builder on PATH: nh and nixos-rebuild/sudo are all missing. The
        # lock is written, the build cannot start, and nothing hangs or is left running.
        missing = self.run_helper("run", str(self.status), stdin=b"y\nn\n", env={"PATH": self.no_builder})
        self.assertEqual(missing.returncode, 1)
        state, message, _ = nix.read_status(self.status)
        self.assertEqual(state, "failed")
        self.assertIn("flake.lock keeps the new revisions", message)
        self.assertNotIn(b"Traceback", missing.stderr)
        self.free()


if __name__ == "__main__":
    unittest.main()
