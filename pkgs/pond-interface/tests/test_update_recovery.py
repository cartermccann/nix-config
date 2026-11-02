"""Regressions from the independent updater recheck; no real Nix commands."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from test_updates_transactions import Base, Fake, NEWER, nix
import qmlharness


class Recovery(Base):
    def test_completion_during_reconcile_is_not_overwritten(self):
        transaction = nix.Transaction(self.flake)
        transaction.acquire()
        transaction.record("switching")
        nix.Status(self.status, transaction.owner)("updating", "Switching")
        transaction.release()
        acquire = nix.Transaction.acquire

        def finish_before_probe(probe, role="update"):
            nix.Status(self.status, transaction.owner)("restart", "Restart to use the new kernel")
            return acquire(probe, role)

        with mock.patch.object(nix.Transaction, "acquire", finish_before_probe):
            result = nix.reconcile(self.status)
        self.assertEqual(result["state"], "restart")
        self.assertFalse(result["abandoned"])
        self.assertEqual(nix.read_status(self.status)[0], "restart")

    def test_declined_switch_can_resume_without_newer_revisions(self):
        first = self.interactive(["y", "n"], Fake())
        self.assertIn("switch pending", first.message)
        self.assertTrue(nix.activation_pending(self.flake))
        retry = Fake(latest=NEWER)
        second = self.interactive(["y", "y"], retry)
        self.assertEqual(second.state, "complete")
        self.assertEqual(retry.calls, [["build"], ["switch"]])
        self.assertFalse(nix.activation_pending(self.flake))

    def test_failed_switch_and_cancelled_retry_remain_actionable(self):
        self.assertEqual(self.interactive(["y", "y"], Fake(switch=1)).state, "failed")
        self.assertTrue(nix.activation_pending(self.flake))
        self.interactive(["n"], Fake(latest=NEWER))
        self.assertTrue(nix.activation_pending(self.flake))
        result = nix.check(run=Fake(latest=NEWER))
        self.assertFalse(result["available"])
        self.assertTrue(result["activationPending"])
        retry = Fake(latest=NEWER)
        self.interactive(["y", "y"], retry)
        self.assertTrue(retry.switched)

    def test_restoring_failed_build_clears_pending_activation(self):
        self.interactive(["y", "y"], Fake(build=1))
        self.assertFalse(nix.activation_pending(self.flake))

    def test_repeated_signals_cannot_release_lock_before_child_cleanup(self):
        # Real worker/child; only the private child ignores TERM. Shorten the
        # grace period so this regression needn't take ten seconds.
        ready = Path(self.tmp.name) / "child-ready"
        worker_code = r'''
import importlib.util, os, signal, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("updater", sys.argv[1])
n = importlib.util.module_from_spec(spec); spec.loader.exec_module(n)
stop = n.stop_process_tree
n.stop_process_tree = lambda p: stop(p, grace=0.8)
child = "import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); Path(%r).write_text(str(os.getpid())); time.sleep(20)" % sys.argv[3]
t = n.Transaction(Path(sys.argv[2])); t.acquire()
try:
    with n.interrupts_raise():
        n.run_child([sys.executable, "-c", child])
except n.Interrupted as error:
    sys.exit(128 + error.signum)
finally:
    t.release()
'''
        worker = subprocess.Popen([sys.executable, "-c", worker_code, nix.__file__,
                                   str(self.flake), str(ready)])
        child_pid = None
        try:
            deadline = time.monotonic() + 4
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists())
            child_pid = int(ready.read_text())
            worker.send_signal(signal.SIGINT)
            time.sleep(0.1)
            worker.send_signal(signal.SIGHUP)
            time.sleep(0.1)
            probe = nix.Transaction(self.flake)
            with self.assertRaises(nix.Busy):
                probe.acquire()
            self.assertEqual(worker.wait(timeout=4), 130)
            self.assertFalse(nix.process_alive(child_pid))
            self.free()
        finally:
            if worker.poll() is None:
                worker.kill()
            worker.wait()
            if child_pid and nix.process_alive(child_pid):
                os.kill(child_pid, signal.SIGKILL)


@unittest.skipUnless(qmlharness.QS, "QS required for real QML update-state test")
class UpdateUI(unittest.TestCase):
    def test_failed_startup_outcome_survives_no_updates_and_is_deduplicated(self):
        harness = qmlharness.Harness()
        self.addCleanup(harness.close)
        harness.copy("updates/State.qml")
        harness.write("installs/State.qml", '''pragma Singleton
import QtQuick
import Quickshell
Singleton {
  property int count: 0
  property string message: ""
  function update(id, title, status, progress, text) { count++; message = text; }
}
''')
        harness.write("settings/State.qml", '''pragma Singleton
import Quickshell
Singleton { function close() {} }
''')
        harness.write("updates/nix.py", '''import json, sys
if sys.argv[1] == "check":
    print(json.dumps({"available": False, "inputs": [], "activationPending": True}))
else:
    print(json.dumps({"state": "failed", "message": "Switch failed; system may be partially activated", "transaction": "retry-1", "alive": False, "abandoned": False}))
''')
        runtime = Path(harness.tmp.name) / "run"
        runtime.mkdir()
        (runtime / "pond-update").write_text(
            'failed\nSwitch failed; system may be partially activated\n{"txn":"retry-1"}\n')
        harness.write("shell.qml", '''import QtQuick
import Quickshell
import "updates" as Updates
import "installs" as Installs
ShellRoot {
  Timer { interval: 700; running: true; onTriggered: Updates.State.refresh() }
  Timer { interval: 1600; running: true; onTriggered: {
    console.log("RESULT status " + JSON.stringify(Updates.State.status));
    console.log("RESULT unresolved " + JSON.stringify(Updates.State.unresolved));
    console.log("RESULT count " + JSON.stringify(Installs.State.count));
    console.log("RESULT message " + JSON.stringify(Installs.State.message));
    console.log("RESULT DONE true"); Qt.quit();
  } }
}
''')
        results, _ = harness.run()
        self.assertEqual(results["status"], ["available"])
        self.assertEqual(results["unresolved"], [True])
        self.assertEqual(results["count"], [1])
        self.assertIn("partially activated", results["message"][0])
