"""Display settings: planning, live apply with verification, bounded
confirm/revert, watchdog, rollback on rejected changes, persistence and
restore. Uses an in-memory compositor; no live display is touched."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from fakes import ROOT, FakeCompositor, FakeHyprland, apply_monitor_calls, monitor
import monitors
import userstate

spec = importlib.util.spec_from_file_location("display_control", ROOT / "shell/settings/display/control.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)

FAST = dict(attempts=3, delay=0, sleep=lambda _: None)
CONTROL = ROOT / "shell/settings/display/control.py"


class Clock:
    """A clock whose sleep advances it, for the watchdog loop."""

    def __init__(self, now):
        self.now = now
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def two_monitors():
    return [monitor("DP-1", 0, focused=True, modes=("1920x1080@60.00Hz", "1920x1080@50.00Hz", "1280x720@60.00Hz")),
            monitor("HDMI-A-1", 1, x=1920, width=2560, height=1440, rate=59.951,
                    modes=("2560x1440@59.95Hz", "1920x1080@60.00Hz"))]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name + "/state",
                                                "XDG_RUNTIME_DIR": self.tmp.name + "/run"})
        self.env.start()
        Path(self.tmp.name, "run").mkdir()
        self.compositor = FakeCompositor(two_monitors())
        self.patches = [mock.patch.object(control, "run_lua", self.compositor.run_lua),
                        mock.patch.object(control, "query", self.compositor.query),
                        mock.patch.object(monitors.apply, "__defaults__",
                                          (None, None, FAST["attempts"], FAST["delay"], FAST["sleep"]))]
        for patch in self.patches:
            patch.start()
        self.watchdogs = []
        self.now = 1000.0

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def apply(self, *args):
        return control.apply(*args, now=lambda: self.now, watchdog=self.watchdogs.append)

    def apply_with(self, watchdog, *args):
        return control.apply(*args, now=lambda: self.now, watchdog=watchdog)

    def live(self, name):
        return monitors.live_settings(self.compositor.monitors[name])



class DisplayTests(Base):
    def test_status_lists_advertised_modes_and_clean_scales(self):
        status = control.status()
        dp, hdmi = status["monitors"]
        self.assertEqual(dp["mode"], "1920x1080@60.00")
        self.assertEqual([r["value"] for r in dp["rates"]], ["60000", "50000"])
        self.assertEqual(hdmi["refresh"], 59950)
        self.assertEqual([r["value"] for r in hdmi["resolutions"]], ["2560x1440", "1920x1080"])
        # 1.5 would make 2560x1440 fractional; Hyprland would silently adjust it.
        self.assertEqual(hdmi["scales"], [0.5, 1, 1.25, 2, 2.5])
        self.assertEqual(status["geometry"][1]["x"], 1920)
        self.assertIsNone(status["pending"])
        self.assertFalse(status["customized"])

    def test_change_is_pending_until_confirmed(self):
        result = self.apply("resolution", "DP-1", "1280x720", self.live("DP-1")["identity"])
        self.assertEqual(self.compositor.monitors["DP-1"]["width"], 1280)
        # Reflow keeps the right-hand display touching the left one.
        self.assertEqual(self.compositor.monitors["HDMI-A-1"]["x"], 1280)
        self.assertEqual(result["pending"]["remaining"], control.CONFIRM_SECONDS)
        self.assertEqual(len(self.watchdogs), 1)
        self.assertEqual(userstate.load()["monitors"], {})
        with self.assertRaisesRegex(ValueError, "previous display change"):
            self.apply("scale", "DP-1", "2")
        status = control.confirm(result["pending"]["id"], now=lambda: self.now)
        self.assertIsNone(status["pending"])
        saved = userstate.load()["monitors"]
        self.assertEqual(saved["DP-1"]["mode"], "1280x720@60.00")
        self.assertEqual(saved["HDMI-A-1"]["position"], [1280, 0])
        self.assertTrue(control.status()["customized"])
        with self.assertRaisesRegex(ValueError, "already reverted"):
            control.confirm(result["pending"]["id"], now=lambda: self.now)

    def test_revert_restores_previous_layout(self):
        before = {n: self.live(n) for n in ("DP-1", "HDMI-A-1")}
        result = self.apply("rotate", "DP-1", "clockwise")
        self.assertEqual(self.compositor.monitors["DP-1"]["transform"], 3)
        self.assertEqual(self.compositor.monitors["HDMI-A-1"]["x"], 1080)
        control.revert(result["pending"]["id"])
        self.assertEqual({n: self.live(n) for n in before}, before)
        self.assertIsNone(userstate.read_pending())
        self.assertEqual(userstate.load()["monitors"], {})

    def test_watchdog_reverts_after_deadline_only(self):
        result = self.apply("refresh", "DP-1", "50000")
        transaction = result["pending"]["id"]
        clock = Clock(self.now + 3)
        self.assertEqual(control.watchdog(transaction, now=clock, sleep=clock.sleep), 0)
        self.assertAlmostEqual(sum(clock.slept), control.CONFIRM_SECONDS - 3)
        self.assertEqual(self.compositor.monitors["DP-1"]["refreshRate"], 60.0)
        self.assertIsNone(userstate.read_pending())
        # A confirmed transaction is not reverted by a late watchdog.
        result = self.apply("refresh", "DP-1", "50000")
        control.confirm(result["pending"]["id"], now=lambda: self.now)
        clock = Clock(self.now)
        control.watchdog(result["pending"]["id"], now=clock, sleep=clock.sleep)
        self.assertEqual(self.compositor.monitors["DP-1"]["refreshRate"], 50.0)

    def test_rejected_change_is_rolled_back_and_reported(self):
        self.compositor.reject = lambda call: call["output"] == "DP-1" and call["vrr"] == 1
        with self.assertRaisesRegex(monitors.DisplayError, "did not accept"):
            self.apply("vrr", "DP-1", "on")
        self.assertFalse(self.compositor.monitors["DP-1"]["vrr"])
        self.assertIsNone(userstate.read_pending())
        self.assertEqual(len(self.compositor.bodies), 2)  # the change, then the restore
        self.assertEqual(len(self.watchdogs), 1)  # armed before the change

    def test_disconnect_during_apply(self):
        original = self.compositor.run_lua

        def unplug(body):
            original(body)
            self.compositor.monitors.pop("HDMI-A-1", None)
        self.compositor.run_lua = unplug
        with mock.patch.object(control, "run_lua", unplug):
            with self.assertRaises(Exception):
                self.apply("resolution", "DP-1", "1280x720")
        self.assertIsNone(userstate.read_pending())
        self.assertEqual(self.compositor.monitors["DP-1"]["width"], 1920)  # the remaining display is restored

    # --- The rollback point exists before anything changes ---------------

    def test_rollback_point_and_watchdog_precede_the_change(self):
        seen = []

        def watchdog(transaction):
            pending = userstate.read_pending()
            seen.append((pending["id"] == transaction, pending["phase"], len(self.compositor.bodies),
                         pending["before"]["DP-1"]["scale"]))
        self.apply_with(watchdog, "scale", "DP-1", "2")
        self.assertEqual(seen, [(True, "applying", 0, 1.0)])
        self.assertEqual(userstate.read_pending()["phase"], "applied")

    def test_state_write_failure_changes_nothing(self):
        with mock.patch.object(userstate, "atomic_write", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(RuntimeError, "not applied: could not record it"):
                self.apply("scale", "DP-1", "2")
        self.assertEqual(self.compositor.bodies, [])
        self.assertEqual(self.watchdogs, [])
        self.assertIsNone(userstate.read_pending())

    def test_watchdog_start_failure_changes_nothing(self):
        def broken(transaction):
            raise RuntimeError("the safety timer did not start")
        with self.assertRaisesRegex(RuntimeError, "not applied: the safety timer"):
            self.apply_with(broken, "scale", "DP-1", "2")
        self.assertEqual(self.compositor.bodies, [])
        self.assertIsNone(userstate.read_pending())

    def test_helper_crash_after_change_is_reverted_by_watchdog(self):
        original = self.compositor.run_lua

        def crash(body):
            original(body)
            raise KeyboardInterrupt  # the helper dies with the change applied
        with mock.patch.object(control, "run_lua", crash):
            with self.assertRaises(KeyboardInterrupt):
                self.apply("scale", "DP-1", "2")
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 2)
        pending = userstate.read_pending()
        self.assertEqual(pending["phase"], "applying")
        clock = Clock(self.now)
        self.assertEqual(control.watchdog(pending["id"], now=clock, sleep=clock.sleep), 0)
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 1)
        self.assertIsNone(userstate.read_pending())

    def test_failed_rollback_is_not_reported_as_success(self):
        original = self.compositor.run_lua
        calls = []

        def flaky(body):
            calls.append(body)
            if len(calls) == 1:
                original(body)  # the change lands, but Hyprland reports an error
                raise control.ipc.HyprError("Hyprland did not answer in time")
            # ...and then refuses the restore.
        with mock.patch.object(control, "run_lua", flaky):
            with self.assertRaisesRegex(RuntimeError, "Could not restore the previous display settings"):
                self.apply("scale", "DP-1", "2")
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 2)
        pending = userstate.read_pending()
        self.assertEqual(pending["phase"], "failed")
        self.assertIn("did not accept", pending["error"])
        self.assertLessEqual(pending["deadline"], time.time() + 2)  # retried soon, not after a full period
        self.assertEqual(control.status(now=lambda: self.now)["pending"]["phase"], "failed")
        with self.assertRaisesRegex(ValueError, "cannot be kept"):
            control.confirm(pending["id"], now=lambda: self.now)

    def test_revert_failure_keeps_the_record_and_retries(self):
        result = self.apply("scale", "DP-1", "2")
        self.compositor.reject = lambda call: True
        with self.assertRaisesRegex(monitors.DisplayError, "Could not restore"):
            control.revert(result["pending"]["id"])
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 2)
        self.assertEqual(userstate.read_pending()["phase"], "failed")
        # The watchdog retries; once Hyprland takes the old settings it is done.
        self.compositor.reject = lambda call: False
        clock = Clock(time.time() + 60)
        self.assertEqual(control.watchdog(result["pending"]["id"], now=clock, sleep=clock.sleep), 0)
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 1)
        self.assertIsNone(userstate.read_pending())

    def test_watchdog_gives_up_without_losing_the_record(self):
        result = self.apply("scale", "DP-1", "2")
        self.compositor.reject = lambda call: True
        clock = Clock(time.time() + 60)
        self.assertEqual(control.watchdog(result["pending"]["id"], now=clock, sleep=clock.sleep, attempts=3), 1)
        self.assertEqual(userstate.read_pending()["phase"], "failed")

    def test_confirm_after_deadline_reverts(self):
        result = self.apply("scale", "DP-1", "2")
        with self.assertRaisesRegex(ValueError, "timed out and was reverted"):
            control.confirm(result["pending"]["id"], now=lambda: self.now + control.CONFIRM_SECONDS + 1)
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 1)
        self.assertEqual(userstate.load()["monitors"], {})

    def test_confirm_refuses_drifted_displays(self):
        result = self.apply("scale", "DP-1", "2")
        self.compositor.monitors["DP-1"]["scale"] = 1.25  # changed behind Pond's back
        with self.assertRaisesRegex(ValueError, "changed before"):
            control.confirm(result["pending"]["id"], now=lambda: self.now)
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 1)
        self.assertEqual(userstate.load()["monitors"], {})

    def test_revert_with_unplugged_output(self):
        result = self.apply("resolution", "DP-1", "1280x720")
        self.assertEqual(set(userstate.read_pending()["before"]), {"DP-1", "HDMI-A-1"})
        self.compositor.monitors.pop("HDMI-A-1")
        control.revert(result["pending"]["id"])
        self.assertEqual(self.compositor.monitors["DP-1"]["width"], 1920)
        self.assertIsNone(userstate.read_pending())

    def test_stale_status_reverts_when_the_watchdog_died(self):
        self.apply("scale", "DP-1", "2")
        control.expire_stale(now=lambda: self.now + control.CONFIRM_SECONDS)  # still in time
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 2)
        late = self.now + control.CONFIRM_SECONDS + control.STALE_SECONDS + 1
        control.expire_stale(now=lambda: late)
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 1)
        self.assertIsNone(userstate.read_pending())

    def test_unreadable_record_blocks_changes(self):
        userstate.pending_path().write_text("{broken")
        with self.assertRaises(userstate.InvalidState):
            self.apply("scale", "DP-1", "2")
        self.assertEqual(self.compositor.bodies, [])
        self.assertIn("cannot be read", control.status()["stateError"])

    def test_validation_errors(self):
        with self.assertRaisesRegex(ValueError, "does not advertise"):
            self.apply("resolution", "DP-1", "800x600")
        with self.assertRaisesRegex(ValueError, "Unsupported display scale"):
            self.apply("scale", "HDMI-A-1", "1.5")
        with self.assertRaisesRegex(ValueError, "no longer connected"):
            self.apply("scale", "DP-9", "1")
        with self.assertRaisesRegex(ValueError, "changed. Please select"):
            self.apply("scale", "DP-1", "2", "Other Model DP-1")

    def test_arrange(self):
        baseline = control.status()["geometry"]
        stale = [dict(baseline[0], width=1)] + baseline[1:]
        with self.assertRaisesRegex(ValueError, "Displays changed"):
            self.apply("arrange", json.dumps({"baseline": stale, "positions": []}))
        overlapping = json.dumps({"baseline": baseline, "positions": [
            {"name": "DP-1", "x": 0, "y": 0}, {"name": "HDMI-A-1", "x": 100, "y": 0}]})
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.apply("arrange", overlapping)
        stacked = json.dumps({"baseline": baseline, "positions": [
            {"name": "DP-1", "x": 300, "y": 1440}, {"name": "HDMI-A-1", "x": 0, "y": 0}]})
        self.apply("arrange", stacked)
        self.assertEqual((self.compositor.monitors["DP-1"]["x"], self.compositor.monitors["DP-1"]["y"]), (300, 1440))

    def test_monitor_lua_is_a_fixed_template(self):
        settings = self.live("DP-1")
        self.assertEqual(monitors.lua("DP-1", settings),
                         'hl.monitor({ output = "DP-1", mode = "1920x1080@60.00", position = "0x0", '
                         'scale = 1, transform = 0, vrr = 0 })')
        for bad in (dict(settings, mode='1920x1080@60"})os.exit()--'), dict(settings, position=[0, "0"]),
                    dict(settings, scale=float("inf")), dict(settings, transform=9), dict(settings, vrr=1)):
            with self.assertRaises(ValueError):
                monitors.lua("DP-1", bad)
        with self.assertRaises(ValueError):
            monitors.lua('DP-1" })', settings)


class RestoreTests(Base):
    def save(self, state):
        path = userstate.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))

    def restore(self):
        return userstate.restore(run_lua=self.compositor.run_lua, query=self.compositor.query)

    def test_restore_applies_matching_monitors_and_skips_others(self):
        dp = dict(self.live("DP-1"), mode="1920x1080@50.00")
        hdmi = dict(self.live("HDMI-A-1"), identity="Make Model HDMI-A-1 SOMEONE-ELSE")
        self.save({"version": 1, "monitors": {"DP-1": dp, "HDMI-A-1": hdmi, "DP-3": dp}, "input": {}})
        report = self.restore()
        self.assertEqual(report["applied"], ["DP-1"])
        self.assertIn("DP-3: not connected", report["skipped"])
        self.assertIn("HDMI-A-1: a different display is connected", report["skipped"])
        self.assertEqual(self.compositor.monitors["DP-1"]["refreshRate"], 50.0)

    def test_restore_skips_unoffered_mode_and_broken_layout(self):
        dp = dict(self.live("DP-1"), mode="3840x2160@60.00", position=[5000, 0])
        hdmi = dict(self.live("HDMI-A-1"), position=[9000, 0])
        self.save({"version": 1, "monitors": {"DP-1": dp, "HDMI-A-1": hdmi}, "input": {}})
        report = self.restore()
        self.assertIn("DP-1: mode 3840x2160@60.00 is not offered", report["skipped"])
        self.assertIn("saved arrangement (displays changed)", report["skipped"])
        self.assertEqual(self.compositor.monitors["HDMI-A-1"]["x"], 1920)

    def test_restore_reverts_unconfirmed_change(self):
        # Shell restart: Hyprland still shows the unconfirmed scale.
        result = self.apply("scale", "DP-1", "1.5")
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 1.5)
        report = self.restore()
        self.assertIn("unconfirmed display change (reverted)", report["skipped"])
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 1)
        self.assertEqual(self.compositor.monitors["HDMI-A-1"]["x"], 1920)
        self.assertIsNone(userstate.read_pending())
        # Its watchdog then finds nothing left to do.
        clock = Clock(self.now)
        control.watchdog(result["pending"]["id"], now=clock, sleep=clock.sleep)
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 1)

    def test_restore_reverts_before_layering_saved_settings(self):
        # A saved HDMI setting plus an unconfirmed DP change.
        self.save({"version": 1, "monitors": {"HDMI-A-1": dict(self.live("HDMI-A-1"), vrr=True)}, "input": {}})
        self.apply("scale", "DP-1", "2")
        report = self.restore()
        self.assertEqual(report["applied"], ["HDMI-A-1"])
        self.assertEqual(self.compositor.monitors["DP-1"]["scale"], 1)
        self.assertTrue(self.compositor.monitors["HDMI-A-1"]["vrr"])

    def test_restore_keeps_record_when_revert_fails(self):
        self.save({"version": 1, "monitors": {"DP-1": dict(self.live("DP-1"), vrr=True)}, "input": {}})
        self.apply("scale", "DP-1", "2")
        self.compositor.reject = lambda call: call["output"] == "DP-1"
        report = self.restore()
        self.assertTrue(any("unconfirmed display change" in e for e in report["errors"]), report)
        self.assertIn("DP-1: unconfirmed change still pending", report["skipped"])
        self.assertNotIn("unconfirmed display change (reverted)", report["skipped"])
        self.assertEqual(userstate.read_pending()["phase"], "failed")

    def test_invalid_state_is_reported_not_applied(self):
        self.save({"version": 1, "monitors": {"DP-1": dict(self.live("DP-1"), scale="2")}, "input": {}})
        report = self.restore()
        self.assertEqual(report["applied"], [])
        self.assertIn("DP-1: Display scale is invalid", report["errors"][0])
        self.assertEqual(self.compositor.bodies, [])
        userstate.state_path().write_text("{not json")
        self.assertIn("not valid JSON", self.restore()["errors"][0])
        self.assertTrue(control.status()["stateError"])

    def test_reset_removes_display_overrides_and_reloads(self):
        self.save({"version": 1, "monitors": {"DP-1": self.live("DP-1")}, "input": {}})
        requests = []
        userstate.reset("display", request=lambda command: requests.append(command) or "ok",
                        run_lua=self.compositor.run_lua, query=self.compositor.query)
        self.assertEqual(requests, ["reload"])
        self.assertEqual(userstate.load()["monitors"], {})
        with self.assertRaises(Exception):
            userstate.reset("display", request=lambda command: "error: nope",
                            run_lua=self.compositor.run_lua, query=self.compositor.query)


class WatchdogProcess(unittest.TestCase):
    """The real detached watchdog and helper processes, against a fake
    Hyprland socket whose repl applies hl.monitor calls."""

    def setUp(self):
        self.fake = FakeHyprland()
        self.fake.state["monitors"] = two_monitors()
        self.by_name = {m["name"]: m for m in self.fake.state["monitors"]}
        self.kill_on_change = None

        def repl(code):
            apply_monitor_calls(self.by_name, code)
            if self.kill_on_change and "hl.monitor" in code:
                target, self.kill_on_change = self.kill_on_change(), None
                os.kill(target, signal.SIGKILL)
            return "POND_OK"
        self.fake.repl = repl
        self.state = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, dict(self.fake.env, XDG_STATE_HOME=self.state.name))
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.fake.close()
        self.state.cleanup()

    def wait_until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_handshake_and_revert_at_deadline(self):
        before = monitors.live_settings(self.by_name["DP-1"])
        userstate.write_pending({"id": "abc", "phase": "applied", "deadline": time.time() + 0.8,
                                 "before": {"DP-1": before}, "after": {"DP-1": dict(before, scale=2)}})
        self.by_name["DP-1"]["scale"] = 2
        pid = control.spawn_watchdog("abc")
        self.assertGreater(pid, 0)
        self.assertTrue(self.wait_until(lambda: userstate.read_pending() is None))
        self.assertEqual(self.by_name["DP-1"]["scale"], 1)
        with self.assertRaisesRegex(RuntimeError, "did not start"):
            control.spawn_watchdog("someotherid")

    def test_killed_helper_is_rolled_back_by_its_watchdog(self):
        helper = subprocess.Popen([sys.executable, str(CONTROL), "scale", "DP-1", "2"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.kill_on_change = lambda: helper.pid
        helper.communicate(timeout=20)
        self.assertEqual(helper.returncode, -signal.SIGKILL)
        self.assertEqual(self.by_name["DP-1"]["scale"], 2)  # the change landed, then the helper died
        pending = userstate.read_pending()
        self.assertEqual(pending["phase"], "applying")
        # Time passes: bring the deadline forward rather than wait 30 s.
        userstate.write_pending(dict(pending, deadline=time.time()))
        self.assertTrue(self.wait_until(lambda: userstate.read_pending() is None))
        self.assertEqual(self.by_name["DP-1"]["scale"], 1)

    def test_helper_end_to_end_confirm(self):
        run = lambda *args: json.loads(subprocess.run([sys.executable, str(CONTROL), *args], capture_output=True,
                                                      text=True, timeout=20).stdout)
        result = run("scale", "DP-1", "2")
        self.assertEqual(result["pending"]["phase"], "applied")
        kept = run("confirm", result["pending"]["id"])
        self.assertIsNone(kept["pending"])
        self.assertEqual(userstate.load()["monitors"]["DP-1"]["scale"], 2)
        time.sleep(0.8)  # the watchdog exits once the record is gone
        self.assertEqual(self.by_name["DP-1"]["scale"], 2)


if __name__ == "__main__":
    unittest.main()
