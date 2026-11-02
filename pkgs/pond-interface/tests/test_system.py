"""Keyboard languages and pointer settings: applied live, verified, then
persisted; rolled back when Hyprland does not take them. Needs PyQt6 (the
packaged pond-python3 has it); skipped otherwise."""
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from fakes import ROOT
import qmlharness
import userstate

try:
    import PyQt6.QtCore  # noqa: F401
except ImportError:
    PyQt6 = None

FIXTURES = Path(__file__).parent / "fixtures"


class FakeInput:
    def __init__(self):
        self.options = {"kb_layout": "us", "kb_variant": "", "sensitivity": 0.0, "accel_profile": "flat",
                        "scroll_factor": 1.0, "natural_scroll": False, "touchpad_natural_scroll": True}
        self.index = 0
        self.ignore = False
        self.requests = []

    def query(self, command):
        if command == "devices":
            return {"keyboards": [{"name": "kbd", "main": True, "layout": self.options["kb_layout"],
                                   "variant": self.options["kb_variant"], "active_layout_index": self.index}]}
        name = command.split(" ", 1)[1].replace("input:", "").replace("touchpad:", "touchpad_")
        value = self.options[name]
        kind = "bool" if isinstance(value, bool) else "float" if isinstance(value, float) else "str"
        return {"option": command, kind: value if value != "" else "[[EMPTY]]"}

    def run_lua(self, body):
        if self.ignore:
            return
        match = re.fullmatch(r"hl\.config\(\{ input = \{ (.*) \} \}\)", body)
        assert match, body
        text = match[1]
        touchpad = re.search(r"touchpad = \{ natural_scroll = (true|false) \}", text)
        if touchpad:
            self.options["touchpad_natural_scroll"] = touchpad[1] == "true"
            text = text.replace(touchpad[0], "")
        for key, value in re.findall(r'(\w+) = ("(?:[^"\\]|\\.)*"|[-0-9.]+|true|false)', text):
            self.options[key] = json.loads(value) if value[0] == '"' or value in ("true", "false") else float(value)

    def request(self, command):
        self.requests.append(command)
        return "ok"


@unittest.skipIf(PyQt6 is None, "PyQt6 unavailable (run with the packaged pond-python3)")
class SystemTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("system_control", ROOT / "shell/settings/system/control.py")
        self.control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.control)
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = FakeInput()
        self.patches = [
            mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name, "POND_XKB_ROOT": str(FIXTURES / "xkb")}),
            mock.patch.object(self.control.ipc, "query", self.fake.query),
            mock.patch.object(self.control.ipc, "run_lua", self.fake.run_lua),
            mock.patch.object(self.control.ipc, "request", self.fake.request),
            mock.patch("time.sleep", lambda _: None),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def test_add_and_remove_language(self):
        result = self.control.toggle_language("de(nodeadkeys)")
        self.assertEqual(result["selectedLanguages"], ["us", "de(nodeadkeys)"])
        self.assertEqual(result["languageNames"], ["English (US)", "German (no dead keys)"])
        self.assertEqual(userstate.load()["input"], {"kb_layout": "us,de", "kb_variant": ",nodeadkeys"})
        self.fake.index = 1
        result = self.control.toggle_language("us")
        self.assertEqual(result["selectedLanguages"], ["de(nodeadkeys)"])
        self.assertEqual(userstate.load()["input"]["kb_layout"], "de")
        self.assertTrue(result["customized"])

    def test_active_layout_is_kept(self):
        # Hyprland rebuilds the keymap on a layout change and selects the
        # first group, so the previously active language is reselected.
        self.control.toggle_language("de")
        self.fake.index = 1
        self.control.toggle_language("us(colemak_dh)")
        self.assertEqual(self.fake.requests, ["switchxkblayout all 1"])
        self.fake.index = 0
        self.control.toggle_language("us(colemak_dh)")
        self.assertEqual(self.fake.requests, ["switchxkblayout all 1"])

    def test_rejections(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.control.toggle_language("us")
        with self.assertRaisesRegex(ValueError, "Unknown keyboard language"):
            self.control.toggle_language('us" } }) os.exit() --')
        with self.assertRaisesRegex(ValueError, "Unknown keyboard language"):
            self.control.toggle_language("custom")

    def test_unapplied_change_rolls_back_and_is_not_saved(self):
        self.fake.ignore = True
        with self.assertRaisesRegex(RuntimeError, "did not apply"):
            self.control.toggle_language("de")
        self.assertEqual(userstate.load()["input"], {})

    def test_mouse_settings_persist(self):
        result = self.control.set_mouse("0.75", "1.5", "true", "false", "true")
        self.assertEqual(result["mouse"], {"speed": 0.75, "scroll": 1.5, "acceleration": True,
                                           "naturalTouchpad": False, "naturalMouse": True})
        saved = userstate.load()["input"]
        self.assertEqual(saved["sensitivity"], 0.5)
        self.assertEqual(saved["accel_profile"], "adaptive")
        self.assertIn("touchpad = { natural_scroll = false }", userstate.input_lua(saved))

    def test_timezone_declared_by_nixos(self):
        with mock.patch.object(self.control, "timezone_declared", lambda: True):
            with self.assertRaisesRegex(ValueError, "time.timeZone"):
                self.control.set_timezone("Europe/Riga")

    def test_reset_returns_to_declared_defaults(self):
        self.control.toggle_language("de")
        with mock.patch.object(userstate.ipc, "request", self.fake.request), \
                mock.patch.object(userstate.ipc, "run_lua", self.fake.run_lua), \
                mock.patch.object(userstate.ipc, "query", lambda c: []):
            userstate.reset("input", request=self.fake.request, run_lua=self.fake.run_lua, query=lambda c: [])
        self.assertEqual(self.fake.requests, ["reload"])
        self.assertEqual(userstate.load()["input"], {})


class InputValidation(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"POND_XKB_ROOT": str(FIXTURES / "xkb")})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_rejects_out_of_range_and_unknown(self):
        for bad in ({"sensitivity": 2}, {"scroll_factor": True}, {"accel_profile": "custom"},
                    {"kb_layout": "xx"}, {"kb_variant": "intl"}, {"kb_layout": "us", "kb_variant": "nope"},
                    {"natural_scroll": "yes"}, {"something": 1}):
            with self.subTest(bad=bad), self.assertRaises(userstate.InvalidState):
                userstate.validate_input(bad)
        self.assertEqual(userstate.input_lua({"kb_layout": "us,de", "kb_variant": ",nodeadkeys"}),
                         'hl.config({ input = { kb_layout = "us,de", kb_variant = ",nodeadkeys" } })')


FAKE_SYSTEM_HELPER = r"""
import json, os, sys, time
from pathlib import Path
state, log = Path(os.environ["FAKE_STATE"]), Path(os.environ["FAKE_LOG"])
def note(text):
    with log.open("a") as handle:
        handle.write(text + "\n")
def result(c):
    return {"selectedLanguages": c["languages"], "languageNames": c["languages"], "timezone": "UTC",
            "timezoneLabel": "UTC", "timezoneManaged": False, "mouse": c["mouse"], "customized": False}
action, *args = sys.argv[1:]
config = json.loads(state.read_text())
note("start " + " ".join([action] + args))
if action == "load":
    snapshot = result(config)           # read now, reported later: a slow load
    time.sleep(config["loadDelay"])
    out = dict(snapshot, languages=[], timezones=[])
else:
    time.sleep(config["actionDelay"])
    config = json.loads(state.read_text())
    if action == "mouse":
        speed, scroll, acceleration, touchpad, natural = args
        config["mouse"] = {"speed": float(speed), "scroll": float(scroll), "acceleration": acceleration == "true",
                           "naturalTouchpad": touchpad == "true", "naturalMouse": natural == "true"}
    elif action == "keyboard":
        languages = config["languages"]
        config["languages"] = [l for l in languages if l != args[0]] if args[0] in languages else languages + args
    state.write_text(json.dumps(config))
    out = result(config) if action != "fail" else {"error": "Hyprland did not apply the setting"}
note("end " + action)
print(json.dumps(out))
"""

SETTINGS_STUB = """pragma Singleton
import QtQuick
import Quickshell
Singleton { property bool opened: false; property string page: "mouse" }
"""


@unittest.skipUnless(qmlharness.QS, "set QS to the packaged Quickshell to run QML tests")
class SystemStateQml(unittest.TestCase):
    """The real settings/system/State.qml and its Process callbacks."""

    def run_scenario(self, body, load_delay=1.0, action_delay=0.1):
        harness = qmlharness.Harness()
        self.addCleanup(harness.close)
        harness.copy("settings/system/State.qml")
        harness.write("settings/State.qml", SETTINGS_STUB)
        harness.write("settings/system/control.py", FAKE_SYSTEM_HELPER)
        state = harness.write("fake.json", json.dumps({
            "loadDelay": load_delay, "actionDelay": action_delay, "languages": ["us"],
            "mouse": {"speed": 0.5, "scroll": 1, "acceleration": True, "naturalTouchpad": True,
                      "naturalMouse": False}}))
        log = Path(harness.tmp.name) / "helper.log"
        log.touch()
        harness.write("shell.qml", """import QtQuick
import Quickshell
import "settings" as Settings
import "settings/system" as System
ShellRoot {
  function out(key, value) { console.log("RESULT " + key + " " + JSON.stringify(value)); }
  function step(ms, fn) { const t = Qt.createQmlObject("import QtQuick; Timer {}", this); t.interval = ms;
                          t.triggered.connect(fn); t.start(); }
  Component.onCompleted: {
""" + body + """
  }
}
""")
        results, output = harness.run(env={"FAKE_STATE": str(state), "FAKE_LOG": str(log)})
        return results, log.read_text().split("\n"), json.loads(state.read_text())

    def test_stale_load_cannot_reset_controls_or_feed_the_next_change(self):
        # Opening starts a slow load that reads the old speed; the user
        # changes the speed meanwhile; then flips natural scrolling.
        results, log, final = self.run_scenario("""
    step(50, () => Settings.State.opened = true);
    step(200, () => System.State.setMouse("speed", 0.9));
    step(1600, () => { out("afterStale", System.State.mouse); System.State.setMouse("naturalMouse", true); });
    step(3200, () => { out("final", System.State.mouse); out("busy", System.State.busy); out("DONE", 1); Qt.quit(); });
""")
        self.assertEqual(results["afterStale"][0]["speed"], 0.9)
        self.assertEqual(final["mouse"]["speed"], 0.9)        # the second change kept the first
        self.assertTrue(final["mouse"]["naturalMouse"])
        self.assertEqual(results["final"][0]["speed"], 0.9)
        self.assertFalse(results["busy"][0])
        # The stale load was discarded and followed by a fresh one.
        self.assertGreaterEqual(sum(1 for line in log if line == "start load"), 2)

    def test_actions_are_serialized_and_reopen_waits_for_them(self):
        results, log, final = self.run_scenario("""
    step(50, () => { System.State.toggleLanguage("de"); System.State.toggleLanguage("fr");
                     Settings.State.opened = true; });
    step(2500, () => { out("languages", System.State.selectedLanguages); out("DONE", 1); Qt.quit(); });
""", load_delay=0.1, action_delay=0.4)
        events = [line for line in log if line]
        self.assertEqual(events[:4], ["start keyboard de", "end keyboard", "start keyboard fr", "end keyboard"])
        self.assertIn("start load", events[4:])  # reopening loaded only after both changes
        self.assertEqual(final["languages"], ["us", "de", "fr"])
        self.assertEqual(results["languages"][-1], ["us", "de", "fr"])

    def test_action_error_survives_the_follow_up_load(self):
        results, log, final = self.run_scenario("""
    step(50, () => System.State.enqueue(["fail"]));
    step(1500, () => { out("error", System.State.error); out("DONE", 1); Qt.quit(); });
""", load_delay=0.1, action_delay=0.1)
        self.assertEqual(results["error"][0], "Hyprland did not apply the setting")
        self.assertIn("start load", log)


if __name__ == "__main__":
    unittest.main()
