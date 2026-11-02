"""Executes the adapter's generated Lua against a mock of Hyprland's `hl`
API (fixtures/hl_mock.lua) under Lua 5.5, Hyprland 0.56's interpreter: the
plans must parse, guard vanished windows, and roll back failed workspace
reorders. Set LUA to the interpreter; skipped when none is available."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import fakes  # noqa: F401  (puts overlay/shell/hypr on sys.path)
import adapter
import ipc
from test_adapter import base_snapshot

MOCK = Path(__file__).parent / "fixtures/hl_mock.lua"
LUA = os.environ.get("LUA") or shutil.which("lua5.5") or shutil.which("lua")


@unittest.skipUnless(LUA, "no Lua interpreter (set LUA)")
class LuaPlans(unittest.TestCase):
    def run_plan(self, setup, body, fail_at=0):
        chunk = (f"FAIL_AT = {fail_at}\n" + f"dofile({ipc.lua_string(str(MOCK))})\n" + setup + "\n"
                 + "local ok, value = pcall(function()\n" + ipc.PRELUDE + body
                 + f"\nreturn {ipc.lua_string(ipc.OK)}\nend)\nreport(ok, value)\n")
        with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as handle:
            handle.write(chunk)
        try:
            result = subprocess.run([LUA, handle.name], capture_output=True, text=True, timeout=10)
        finally:
            os.unlink(handle.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(line.split("=", 1) for line in result.stdout.splitlines())

    def test_interpreter_is_lua_5_5(self):
        version = subprocess.run([LUA, "-v"], capture_output=True, text=True).stdout
        self.assertIn("Lua 5.5", version)

    def test_workspace_reorder_applies_permutation(self):
        setup = "add_workspace(1) add_workspace(2) add_workspace(4) add_workspace(3)"
        out = self.run_plan(setup, adapter.plan_move_workspace(base_snapshot(), 1, 4))
        self.assertEqual(out["ok"], "true", out)
        self.assertEqual(out["value"], ipc.OK)
        self.assertEqual(out["ids"], "1=4,2=1,4=2,3=3")

    def test_workspace_reorder_rolls_back_on_any_failure(self):
        setup = "add_workspace(1) add_workspace(2) add_workspace(4) add_workspace(3)"
        plan = adapter.plan_move_workspace(base_snapshot(), 1, 4)
        for fail_at in range(1, 7):
            with self.subTest(fail_at=fail_at):
                out = self.run_plan(setup, plan, fail_at)
                self.assertEqual(out["ok"], "false")
                self.assertIn("injected failure", out["value"])
                self.assertEqual(out["ids"], "1=1,2=2,4=4,3=3")

    def test_workspace_reorder_missing_workspace(self):
        out = self.run_plan("add_workspace(2) add_workspace(4)", adapter.plan_move_workspace(base_snapshot(), 1, 4))
        self.assertEqual(out["ok"], "false")
        self.assertIn("workspace 1 no longer exists", out["value"])
        self.assertEqual(out["ids"], "2=2,4=4")

    def test_move_window_refuses_vanished_window(self):
        plan, _ = adapter.plan_move_window(base_snapshot(), "0x2", 8, "HDMI-A-1", create=True)
        out = self.run_plan("add_workspace(1)", plan)
        self.assertEqual(out["ok"], "false")
        self.assertIn("address:0x2 no longer exists", out["value"])
        self.assertEqual(out["log"], "")

    def test_move_window_creates_workspace_on_target_output(self):
        plan, wid = adapter.plan_move_window(base_snapshot(), "0x2", 8, "HDMI-A-1", create=True)
        self.assertEqual(wid, 8)
        out = self.run_plan('add_workspace(1) add_window("0x2", 1) focus_monitor("DP-1")', plan)
        self.assertEqual(out["ok"], "true", out)
        self.assertEqual(out["log"], "window.move address:0x2 8;workspace.move 8 HDMI-A-1")
        self.assertEqual(out["places"], "1@DP-1#0,8@HDMI-A-1#1")

    def test_creation_id_taken_elsewhere_after_planning_moves_nothing(self):
        # Planned while 8 did not exist; by execution 8 exists on DP-1.
        plan, _ = adapter.plan_move_window(base_snapshot(), "0x2", 8, "HDMI-A-1", create=True)
        out = self.run_plan('add_workspace(1) add_window("0x2", 1) add_workspace(8, nil, "DP-1")', plan)
        self.assertEqual(out["ok"], "false")
        self.assertIn("Workspace 8 was just taken", out["value"])
        self.assertEqual(out["log"], "")
        self.assertEqual(out["places"], "1@DP-1#1,8@DP-1#0")

    def test_creation_second_dispatch_can_leave_a_partial_move(self):
        plan, _ = adapter.plan_move_window(base_snapshot(), "0x2", 8, "HDMI-A-1", create=True)
        out = self.run_plan('add_workspace(1) add_window("0x2", 1) focus_monitor("DP-1")', plan, fail_at=2)
        self.assertEqual(out["ok"], "false")
        self.assertEqual(out["places"], "1@DP-1#0,8@DP-1#1")
        self.assertIn("injected failure", out["value"])

    def test_creation_focus_targets_intended_display(self):
        plan = adapter.plan_focus_workspace(base_snapshot(), 8, "HDMI-A-1", create=True)
        out = self.run_plan('focus_monitor("DP-1")', plan)
        self.assertEqual(out["ok"], "true", out)
        self.assertEqual(out["log"], "focus HDMI-A-1;focus 8")
        taken = self.run_plan('add_workspace(8, nil, "DP-1") add_window("0x9", 8)', plan)
        self.assertEqual(taken["ok"], "false")
        self.assertEqual(taken["log"], "")

    def test_move_window_into_existing_workspace_keeps_its_output(self):
        plan, _ = adapter.plan_move_window(base_snapshot(), "0x2", 2)
        out = self.run_plan('add_workspace(1) add_workspace(2) add_window("0x2", 1)', plan)
        self.assertEqual(out["log"], "window.move address:0x2 2")
        gone = self.run_plan('add_workspace(1) add_window("0x2", 1)', plan)
        self.assertEqual(gone["ok"], "false")
        self.assertIn("Workspace 2 no longer exists", gone["value"])
        self.assertEqual(gone["log"], "")

    def test_swaps_stop_when_a_window_closes(self):
        plan = adapter.plan_reorder(base_snapshot(), "0x1", 1, 2)
        out = self.run_plan('add_workspace(1) add_window("0x1", 1) add_window("0x2", 1)', plan)
        self.assertEqual(out["ok"], "false")
        self.assertIn("address:0x3 no longer exists", out["value"])
        # The swap already made is undone: the original order is back.
        self.assertEqual(out["log"], "window.swap address:0x1 address:0x2;window.swap address:0x1 address:0x2")
        self.assertEqual(out["tiles"], "0x1,0x2")

    def test_reorder_applies_and_rolls_back_injected_failures(self):
        setup = 'add_workspace(1) add_window("0x1", 1) add_window("0x2", 1) add_window("0x3", 1)'
        plan = adapter.plan_reorder(base_snapshot(), "0x1", 1, 2)
        self.assertEqual(self.run_plan(setup, plan)["tiles"], "0x2,0x3,0x1")
        for fail_at in (1, 2):
            with self.subTest(fail_at=fail_at):
                out = self.run_plan(setup, plan, fail_at)
                self.assertEqual(out["ok"], "false")
                self.assertIn("injected failure", out["value"])
                self.assertEqual(out["tiles"], "0x1,0x2,0x3")

    def test_reorder_refuses_fullscreen_source_and_neighbour(self):
        setup = 'add_workspace(1) add_window("0x1", 1) add_window("0x2", 1) add_window("0x3", 1)'
        plan = adapter.plan_reorder(base_snapshot(), "0x1", 1, 2)
        # Fullscreen at execution time (it changed after planning).
        for who in ("0x1", "0x3"):
            with self.subTest(fullscreen=who):
                out = self.run_plan(setup + f' set_fullscreen("{who}", 2)', plan)
                self.assertEqual(out["ok"], "false")
                self.assertEqual(out["value"], adapter.FULLSCREEN_REORDER)
                self.assertEqual(out["tiles"], "0x1,0x2,0x3")
        # The mock itself matches Hyprland: swapWith refuses either operand.
        raw = 'D(hl.dsp.window.swap({ window = "address:0x2", with = "address:0x1" }))'
        out = self.run_plan(setup + ' set_fullscreen("0x1", 1)', raw)
        self.assertEqual(out["value"], "Can't swap fullscreen window")

    def test_focus_and_dpms_plans_execute(self):
        s = base_snapshot()
        for plan in (adapter.plan_focus_workspace(s, 7, "HDMI-A-1", create=True), adapter.plan_dpms("off"),
                     adapter.plan_focus_workspace(s, -1337)):
            self.assertEqual(self.run_plan("", plan)["ok"], "true", plan)
        out = self.run_plan("", adapter.plan_focus_window("0x9"))
        self.assertIn("address:0x9 no longer exists", out["value"])

    def test_invalid_dispatcher_is_an_error(self):
        out = self.run_plan("", "D(hl.dsp.focus({ nothing = 1 }))")
        self.assertEqual((out["ok"], out["value"]), ("false", "invalid dispatcher"))


if __name__ == "__main__":
    unittest.main()
