"""settings/session.py hands lock and suspend to pond-session, the session's
single lock owner (home/scripts/pond-session.sh), and reports its failures.

The fake pond-session below records how it was called and answers with the
exit status a test asks for; nothing here starts a locker."""
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from fakes import ROOT


def load():
    spec = importlib.util.spec_from_file_location("pond_session_helper", ROOT / "shell/settings/session.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


session = load()


class SessionHelper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.calls = self.dir / "calls"
        self.helper = self.dir / "pond-session"
        self.helper.write_text(
            "#!/bin/sh\n"
            f'echo "$*" >> "{self.calls}"\n'
            f'[ -e "{self.dir}/hang" ] && exec sleep 30\n'
            f'exit "$(cat "{self.dir}/rc" 2>/dev/null || echo 0)"\n')
        self.helper.chmod(self.helper.stat().st_mode | stat.S_IEXEC)
        self.env = mock.patch.dict(os.environ, {"POND_SESSION_BIN": str(self.helper)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def calls_made(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_lock_goes_through_the_session_owner(self):
        session.run("lock")
        self.assertEqual(self.calls_made(), ["lock shell"])

    def test_suspend_goes_through_the_session_owner(self):
        session.run("suspend")
        self.assertEqual(self.calls_made(), ["suspend"])

    def test_unconfirmed_lock_is_an_error(self):
        (self.dir / "rc").write_text("1")
        with self.assertRaisesRegex(RuntimeError, "not confirmed"):
            session.run("lock")

    def test_suspend_refused_without_a_lock(self):
        (self.dir / "rc").write_text("1")
        with self.assertRaisesRegex(RuntimeError, "refusing to suspend"):
            session.run("suspend")

    def test_a_hanging_helper_is_bounded(self):
        (self.dir / "hang").touch()
        with mock.patch.object(session, "HELPER_TIMEOUT", 0.5):
            with self.assertRaisesRegex(RuntimeError, "did not finish"):
                session.run("lock")

    def test_missing_helper_refuses(self):
        with mock.patch.dict(os.environ, {"POND_SESSION_BIN": ""}), \
                mock.patch.object(session.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "refusing"):
                session.run("suspend")

    def test_never_starts_a_locker_itself(self):
        source = (ROOT / "shell/settings/session.py").read_text()
        self.assertNotIn('"qs"', source)
        self.assertNotIn("lockAndSuspend", source)
        self.assertNotIn("/usr/share", source)

    def test_shell_has_no_logind_lock_of_its_own(self):
        # The lock guard in pond-session owns lock before sleep; a second
        # inhibitor in the shell would compete with it.
        source = (ROOT / "shell/shell.qml").read_text()
        self.assertNotIn("Logind {", source)
        self.assertNotIn("sleepInhibited", source)

    def test_command_line_exit_status(self):
        (self.dir / "rc").write_text("1")
        result = subprocess.run(["python3", str(ROOT / "shell/settings/session.py"), "lock"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not confirmed", result.stderr)


if __name__ == "__main__":
    unittest.main()
