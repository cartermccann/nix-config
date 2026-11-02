"""Sidebar controls: measured telemetry, scoped inhibition and real QML cards.
No live desktop, dictation, login1 or update actions are exercised.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import qmlharness

SPEC = importlib.util.spec_from_file_location('pond_controls', qmlharness.OVERLAY_SHELL / 'controls/control.py')
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)


class Telemetry(unittest.TestCase):
    def test_cpu_delta_does_not_count_guest_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / 'stat').write_text('cpu  100 10 20 400 50 2 3 0 99 10\n')
            self.assertEqual(control.cpu_sample(proc), (585, 450))
        self.assertIsNone(control.cpu_usage(None, (100, 70)))
        self.assertEqual(control.cpu_usage((100, 70), (200, 120)), 50)
        self.assertIsNone(control.cpu_usage((100, 70), (50, 20)))

    def test_memory_uses_available_including_reclaimable_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / 'meminfo').write_text('MemTotal: 1000 kB\nMemFree: 50 kB\nMemAvailable: 600 kB\n')
            self.assertEqual(control.memory(proc), {'used': 409600, 'total': 1024000, 'percent': 40})

    def test_stale_recorder_pid_does_not_report_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid = root / 'recorder.pid'
            pid.write_text('42')
            (root / '42').mkdir()
            (root / '42/comm').write_text('unrelated\n')
            self.assertFalse(control.recorder_active(pid, root))
            (root / '42/comm').write_text('pw-record\n')
            self.assertTrue(control.recorder_active(pid, root))
            pid.write_text('-1')
            self.assertFalse(control.recorder_active(pid, root))

    def test_gpu_unavailable_and_unsupported_fields_are_not_zero(self):
        with mock.patch.object(control.shutil, 'which', return_value='/fake/nvidia-smi'):
            result = control.gpu(lambda *a, **kw: subprocess.CompletedProcess(a, 0, 'GPU, 15, [N/A], 1024, 8192\n'))
            self.assertEqual(result['percent'], 15)
            self.assertIsNone(result['temperature'])
            def timeout(*a, **kw):
                raise subprocess.TimeoutExpired(a, 2)
            self.assertIsNone(control.gpu(timeout))

    def test_hold_releases_on_stop_and_shell_eof(self):
        for stop in ('stop\n', ''):
            with subprocess.Popen([sys.executable, str(SPEC.origin), 'hold'], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True) as process:
                self.assertEqual(json.loads(process.stdout.readline()), {'ready': True})
                process.communicate(stop, timeout=2)
                self.assertEqual(process.returncode, 0)


@unittest.skipUnless(qmlharness.QS, 'set QS for real QML control tests')
class ControlsQml(unittest.TestCase):
    def harness(self):
        harness = qmlharness.Harness()
        self.addCleanup(harness.close)
        package_shell = Path(qmlharness.QS).resolve().parents[3] / 'share/pond-interface/shell'
        harness.root.rmdir()
        shutil.copytree(package_shell, harness.root)
        harness.root.chmod(0o755)
        for path in harness.root.rglob('*'):
            path.chmod(path.stat().st_mode | 0o200)
        shutil.copytree(qmlharness.OVERLAY_SHELL / 'controls', harness.root / 'controls', dirs_exist_ok=True)
        # Replace the default services too, so no eager singleton initialization
        # can reach the live system. The real controls are bound to these fixtures.
        harness.write('controls/State.qml', '''pragma Singleton
import QtQuick
import Quickshell
Singleton {
 property var hardware: ({cpu: 37, memory: {used: 17179869184, total: 68719476736, percent: 25},
    gpu: {name: "NVIDIA GeForce", percent: 14, temperature: 52, usedMiB: 4096, totalMiB: 24576},
    disk: {used: 549755813888, total: 1099511627776, percent: 50}})
 property var dictation: ({live:false, batch:true, liveAvailable:true, batchAvailable:true})
 property bool telemetryReady: true
 property bool dictationBusy: false
 property string dictationError: ""
 property bool keepAwake: true
 property bool awakeBusy: false
 property string awakeSummary: "30 min remaining"
 property string awakeError: ""
 property var appUpdates: ["T3 Code  0.1.5 → 0.1.6"]
 property bool appUpdatesKnown: true
 property string appUpdateError: ""
 property bool appChecking: false
 property int choice: -999
 property string mode: ""
 function setAwake(minutes) { choice=minutes; }
 function toggleDictation(value) { mode=value; }
 function refreshApps() {}
 function percent(value) { return typeof value === "number" ? Math.round(value)+"%" : "—"; }
 function gib(value) { return typeof value === "number" ? (value/1073741824).toFixed(1)+" GiB" : "—"; }
}''')
        harness.write('updates/State.qml', '''pragma Singleton
import QtQuick
import Quickshell
Singleton {
 property var inputs: [{input:"nixpkgs", from:"124a12", to:"acb123"}]
 property string status: "available"
 property string error: ""
 property string message: ""
 property string lastChecked: "10:35"
 property bool checking: false
 property bool launching: false
 property bool unresolved: false
 function start() {}
 function refresh() {}
}''')
        harness.write('bluetooth/State.qml', '''pragma Singleton
import QtQuick
import Quickshell
Singleton {
 property var devices: [{name:"Headphones",connected:true}]
 property bool available: true
 property bool enabled: true
 property bool blocked: false
 property string errorText: ""
}''')
        return harness

    def test_cards_and_all_custom_popouts_in_both_sizes(self):
        harness = self.harness()
        harness.write('shell.qml', '''import QtQuick
import QtQuick.Window
import Quickshell
import "controls" as Controls
ShellRoot {
 Window {
  id: win; visible:true; width:720; height:640; color:"#1b2227"
  Controls.Rail { id: rail; x:10; y:10; maximumHeight:300; onRequested: kind => {panel.kind=kind; panel.open();} }
  Controls.Panel { id: panel; x:190; y:30; maximumHeight:540 }
 }
 Timer {
  id: steps; interval:350; running:true; repeat:true
  property int step:0
  property var kinds:["dictation","awake","hardware","updates"]
  onTriggered: {
   if (step === 0) {
    console.log("RESULT rail", JSON.stringify({width:rail.width,height:rail.height,count:rail.cards.length}));
    rail.requested(kinds[0]);
   } else if (step <= 4) {
    console.log("RESULT panel", JSON.stringify({kind:panel.kind,opened:panel.opened,height:panel.height,depth:panel.depth}));
    if (step < 4) { panel.closeAll(); panel.kind=kinds[step]; panel.open(); }
    else { rail.collapseProgress=1; panel.closeAll(); }
   } else if (step === 5) {
    console.log("RESULT compact", JSON.stringify({width:rail.width,height:rail.height,count:rail.cards.length}));
    rail.requested("hardware");
   } else {
    console.log("RESULT compactPanel", JSON.stringify({opened:panel.opened,height:panel.height}));
    console.log("RESULT DONE", "true"); Qt.quit();
   }
   if (Quickshell.env("POND_CONTROLS_CAPTURE"))
     win.contentItem.grabToImage(result => result.saveToFile(Quickshell.env("POND_CONTROLS_CAPTURE") + "/controls-" + step + ".png"));
   step++;
  }
 }
}''')
        results, log = harness.run(env={'QT_QUICK_BACKEND': 'software'})
        self.assertEqual(results['rail'][0]['count'], 5)
        self.assertEqual(results['rail'][0]['width'], 156)
        self.assertLessEqual(results['rail'][0]['height'], 300)
        self.assertEqual(results['compact'][0]['width'], 48)
        self.assertEqual(len(results['panel']), 4)
        for panel in results['panel'] + results['compactPanel']:
            self.assertTrue(panel['opened'])
            self.assertGreater(panel['height'], 160)
            self.assertLessEqual(panel['height'], 540)
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Cannot assign', 'Unable to assign', 'failed to load', 'is not a type'):
            self.assertNotIn(error, log, log)

    def test_keep_awake_confirmation_expiry_failure_and_dictation_command(self):
        harness = self.harness()
        harness.copy('controls/State.qml')
        harness.write('controls/control.py', '''import json, sys
if sys.argv[1] == 'hold':
 print('{"ready":true}', flush=True)
else:
 print(json.dumps({'type':'dictation','live':False,'batch':False,'liveAvailable':True,'batchAvailable':True}), flush=True)
sys.stdin.readline()
''')
        base = Path(harness.tmp.name)
        bindir = base / 'bin'
        bindir.mkdir()
        counter = base / 'inhibitors'
        wrapper = bindir / 'systemd-inhibit'
        wrapper.write_text(f'''#!{sys.executable}
import os, sys
from pathlib import Path
counter=Path({str(counter)!r})
n=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(n))
if n >= 3: sys.exit(1)
os.execv({sys.executable!r}, [{sys.executable!r}, sys.argv[-2], sys.argv[-1]])
''')
        wrapper.chmod(0o755)
        # Package wrapper prepends real systemd to PATH. Use an absolute fake
        # only for this test, without changing how production resolves it.
        state = harness.root / 'controls/State.qml'
        state.write_text(state.read_text().replace('"systemd-inhibit",', json.dumps(str(wrapper)) + ','))
        home = base / 'home'
        scripts = home / '.local/bin'
        scripts.mkdir(parents=True)
        marker = base / 'dictation-called'
        script = scripts / 'toggle-dictation.sh'
        script.write_text(f'#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text("live")\n')
        script.chmod(0o755)
        harness.write('shell.qml', '''import QtQuick
import Quickshell
import "controls" as Controls
ShellRoot {
 Component.onCompleted: console.log("Initialized", Controls.State.keepAwake)
 Timer {
  interval:350; running:true; repeat:true
  property int step:0
  onTriggered: {
   if (step === 1) Controls.State.setAwake(30);
   if (step === 2) {
    console.log("RESULT confirmed", JSON.stringify(Controls.State.keepAwake));
    Controls.State.setAwake(-1);
   }
   if (step === 3) {
    console.log("RESULT stopped", JSON.stringify(Controls.State.keepAwake));
    Controls.State.setAwake(0);
    Controls.State.awakeUntil = Date.now()-1;
   }
   if (step === 7) console.log("RESULT expired", JSON.stringify(Controls.State.keepAwake));
   if (step === 8) Controls.State.setAwake(30);
   if (step === 9) {
    console.log("RESULT failed", JSON.stringify({active:Controls.State.keepAwake,error:Controls.State.awakeError}));
    Controls.State.toggleDictation("live");
   }
   if (step === 11) {
    console.log("RESULT busy", JSON.stringify(Controls.State.dictationBusy));
    console.log("RESULT DONE", "true"); Qt.quit();
   }
   step++;
  }
 }
}''')
        results, log = harness.run(env={'HOME': str(home), 'QT_QUICK_BACKEND': 'software'})
        self.assertEqual(results['confirmed'], [True], log)
        self.assertEqual(results['stopped'], [False], log)
        self.assertEqual(results['expired'], [False], log)
        self.assertFalse(results['failed'][0]['active'], log)
        self.assertTrue(results['failed'][0]['error'], log)
        self.assertEqual(results['busy'], [False], log)
        self.assertEqual(marker.read_text(), 'live')
        self.assertEqual(counter.read_text(), '3')
