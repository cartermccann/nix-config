"""Hyprland adapter: IPC framing, snapshot model, action plans and the event
stream lifecycle (initial snapshot, debounced refresh, titles, keyboard,
reconnects and request replies), all against a fake Hyprland instance."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import unittest

from fakes import HYPR, FakeHyprland, client, monitor, workspace
import qmlharness

os.environ["POND_XKB_ROOT"] = str(Path(__file__).parent / "fixtures/xkb")
import adapter  # noqa: E402
import ipc  # noqa: E402


class LuaQuoting(unittest.TestCase):
    def test_string_escapes_cannot_break_out(self):
        text = 'x" ) os.execute("rm -rf ~") --\n\\'
        quoted = ipc.lua_string(text)
        self.assertTrue(quoted.startswith('"') and quoted.endswith('"'))
        inner = quoted[1:-1]
        # Every quote is escaped and no raw newline survives.
        self.assertNotIn("\n", inner)
        self.assertEqual(inner.count('"'), inner.count('\\"'))
        with self.assertRaises(ValueError):
            ipc.lua_string("a\0b")

    def test_value_rejects_bad_keys_and_numbers(self):
        self.assertEqual(ipc.lua_value({"a": 1, "b": True, "c": "d"}), '{ a = 1, b = true, c = "d" }')
        with self.assertRaises(ValueError):
            ipc.lua_value({"a b": 1})
        with self.assertRaises(ValueError):
            ipc.lua_value(float("nan"))

    def test_selectors_are_validated(self):
        self.assertEqual(ipc.window_selector("0xABC"), "address:0xabc")
        for bad in ("0xzz", "address:0x1", "0x1 ; error()", ""):
            with self.assertRaises(ValueError):
                ipc.window_selector(bad)
        with self.assertRaises(ValueError):
            ipc.output_name('DP-1") error("x')
        with self.assertRaises(ValueError):
            ipc.workspace_id(1.5)


class RequestSocket(unittest.TestCase):
    def setUp(self):
        self.fake = FakeHyprland()

    def tearDown(self):
        self.fake.close()

    def test_json_query_and_framing(self):
        self.assertEqual(ipc.query("monitors", env=self.fake.env)[0]["name"], "DP-1")
        self.assertEqual(self.fake.requests[-1], "j/monitors")

    def test_run_lua_requires_marker(self):
        ipc.run_lua("local x = 1", env=self.fake.env)
        self.assertTrue(self.fake.requests[-1].startswith("/repl local function D"))
        self.assertTrue(self.fake.requests[-1].endswith('return "POND_OK"'))
        self.fake.repl = lambda code: 'error: [string "x"]:3: window address:0x1 no longer exists'
        with self.assertRaisesRegex(ipc.HyprError, "^window address:0x1 no longer exists$"):
            ipc.run_lua("W('address:0x1')", env=self.fake.env)
        # Warnings without the marker are failures, never silent success.
        self.fake.repl = lambda code: "warning: =[C]:-1: hl.focus: window not found"
        with self.assertRaisesRegex(ipc.HyprError, "hl.focus: window not found"):
            ipc.run_lua("x", env=self.fake.env)

    def test_large_request_is_complete(self):
        seen = []
        self.fake.repl = lambda code: (seen.append(code), "POND_OK")[1]
        body = "-- " + "x" * (1023 * 3 - 200)
        ipc.run_lua(body, env=self.fake.env)
        self.assertIn(body, seen[0])

    def test_missing_instance(self):
        with self.assertRaisesRegex(ipc.HyprError, "not running"):
            ipc.request("monitors", env={"XDG_RUNTIME_DIR": "/nonexistent"})
        with self.assertRaisesRegex(ipc.HyprError, "Cannot reach Hyprland"):
            ipc.request("monitors", env={"XDG_RUNTIME_DIR": "/nonexistent", "HYPRLAND_INSTANCE_SIGNATURE": "x"})


def snap(monitors, workspaces, clients, active="", cache=None):
    return adapter.build_snapshot(monitors, workspaces, clients, {"address": active} if active else {},
                                  geometry_cache=cache)


class Snapshot(unittest.TestCase):
    def test_dwindle_reading_order_floating_last(self):
        clients = [client("0x3", 1, 960, 540), client("0x1", 1, 0, 0), client("0x2", 1, 960, 0),
                   client("0x4", 1, 100, 100, floating=True), client("0x5", 1, 50, 900, floating=True)]
        s = snap([monitor("DP-1", 0, focused=True)], [workspace(1, "DP-1", 5)], clients, "0x2")
        self.assertEqual([w["id"] for w in s["windows"]], ["0x1", "0x2", "0x3", "0x5", "0x4"])
        self.assertEqual([w["order"] for w in s["windows"]], [0, 1, 2, 3, 4])
        self.assertTrue(next(w for w in s["windows"] if w["id"] == "0x2")["isFocused"])

    def test_fullscreen_keeps_tile_position(self):
        cache = {}
        tiles = [client("0x1", 1, 0, 0), client("0x2", 1, 960, 0)]
        snap([monitor("DP-1", 0, focused=True)], [workspace(1, "DP-1", 2)], tiles, cache=cache)
        # 0x2 goes fullscreen: Hyprland reports the monitor origin.
        tiles = [client("0x1", 1, 0, 0), client("0x2", 1, 0, 0, w=1920, h=1080, fullscreen=2)]
        s = snap([monitor("DP-1", 0, focused=True)], [workspace(1, "DP-1", 2)], tiles, cache=cache)
        self.assertEqual([w["id"] for w in s["windows"]], ["0x1", "0x2"])
        self.assertEqual(s["windows"][1]["fullscreen"], 2)
        # Closed windows leave the cache.
        snap([monitor("DP-1", 0, focused=True)], [workspace(1, "DP-1", 1)], tiles[:1], cache=cache)
        self.assertEqual(set(cache), {"0x1"})

    def test_workspaces_monitors_special_and_unmapped(self):
        monitors = [monitor("DP-1", 0, active=1), monitor("HDMI-A-1", 1, x=1920, focused=True, active=5)]
        workspaces = [workspace(5, "HDMI-A-1"), workspace(1, "DP-1", 1, last="0xa"),
                      workspace(-98, "DP-1", 1, name="special:scratch"), workspace(-1337, "DP-1", 0, name="notes")]
        clients = [client("0xa", 1, 0, 0), client("0xb", -98, 0, 0, mon=0),
                   client("0xc", 1, 0, 0, mapped=False)]
        s = snap(monitors, workspaces, clients)
        self.assertEqual(s["focusedOutput"], "HDMI-A-1")
        by_id = {w["id"]: w for w in s["workspaces"]}
        self.assertTrue(by_id[-98]["special"])
        self.assertFalse(by_id[-1337]["special"])
        self.assertTrue(by_id[5]["isFocused"] and by_id[5]["isActive"] and by_id[5]["windows"] == 0)
        self.assertTrue(by_id[1]["isActive"] and not by_id[1]["isFocused"])
        self.assertEqual(by_id[1]["lastWindow"], "0xa")
        self.assertEqual(by_id[5]["lastWindow"], "")
        self.assertNotIn("0xc", [w["id"] for w in s["windows"]])

    def test_keyboard_names_from_catalog(self):
        devices = {"keyboards": [{"name": "virtual-kbd", "layout": "us"},
                                 {"name": "real", "main": True, "layout": "us,de", "variant": "colemak_dh,",
                                  "active_layout_index": 1, "active_keymap": "German"}]}
        state = adapter.keyboard_state(devices)
        self.assertEqual(state["ids"], ["us(colemak_dh)", "de"])
        self.assertEqual(state["names"], ["English (Colemak-DH)", "German"])
        self.assertEqual(state["index"], 1)


def base_snapshot():
    monitors = [monitor("DP-1", 0, focused=True, active=1), monitor("HDMI-A-1", 1, x=1920, active=3)]
    workspaces = [workspace(1, "DP-1", 3), workspace(2, "DP-1", 1), workspace(4, "DP-1", 1),
                  workspace(3, "HDMI-A-1", 1), workspace(-98, "DP-1", 0, name="special:s"),
                  workspace(-1337, "DP-1", 0, name="notes")]
    clients = [client("0x1", 1, 0, 0), client("0x2", 1, 960, 0), client("0x3", 1, 960, 540),
               client("0x4", 2, 0, 0), client("0x5", 4, 0, 0), client("0x6", 3, 1920, 0, mon=1),
               client("0x7", 1, 50, 50, floating=True)]
    return snap(monitors, workspaces, clients, "0x1")


class Plans(unittest.TestCase):
    def test_focus_existing_and_creation_workspace(self):
        s = base_snapshot()
        self.assertEqual(adapter.plan_focus_workspace(s, 2), 'D(hl.dsp.focus({ workspace = "2" }))')
        self.assertEqual(adapter.plan_focus_workspace(s, -1337), 'D(hl.dsp.focus({ workspace = "name:notes" }))')
        plan = adapter.plan_focus_workspace(s, 7, "HDMI-A-1", create=True)
        self.assertIn('target.monitor.name ~= "HDMI-A-1"', plan)
        self.assertEqual(plan.splitlines()[-2:], ['if target == nil then D(hl.dsp.focus({ monitor = "HDMI-A-1" })) end',
                                                  'D(hl.dsp.focus({ workspace = "7" }))'])
        with self.assertRaises(adapter.CommandError):
            adapter.plan_focus_workspace(s, -98)
        with self.assertRaises(adapter.CommandError):
            adapter.plan_focus_workspace(s, 9, "DP-9", create=True)
        with self.assertRaises(adapter.CommandError):
            adapter.plan_focus_workspace(s, -5)
        # Without creation intent a missing workspace is an error, not a
        # workspace silently created on whichever display has focus.
        with self.assertRaisesRegex(adapter.CommandError, "no longer exists"):
            adapter.plan_focus_workspace(s, 7, "HDMI-A-1")

    def test_focus_window_guards_existence(self):
        plan = adapter.plan_focus_window("0x1")
        self.assertIn('W("address:0x1")', plan)
        with self.assertRaises(ValueError):
            adapter.plan_focus_window("0x1); os.exit()")

    def test_move_window_to_creation_row_on_other_output(self):
        s = base_snapshot()
        plan, wid = adapter.plan_move_window(s, "0x2", 8, "HDMI-A-1", create=True)
        self.assertEqual(wid, 8)
        self.assertIn('D(hl.dsp.window.move({ window = "address:0x2", workspace = "8", follow = false }))', plan)
        self.assertIn('if target == nil then D(hl.dsp.workspace.move({ workspace = "8", monitor = "HDMI-A-1" })) end',
                      plan)
        self.assertEqual(adapter.plan_move_window(s, "0x2", 1), ("", 1))  # already there
        with self.assertRaises(adapter.CommandError):
            adapter.plan_move_window(s, "0xdead", 2)
        with self.assertRaisesRegex(adapter.CommandError, "no longer exists"):
            adapter.plan_move_window(s, "0x2", 8, "HDMI-A-1")  # not a creation row

    def test_stale_creation_id_keeps_intended_output(self):
        # The sidebar offered 3 as "new workspace on HDMI-A-1"; meanwhile 3
        # was created on DP-1. The drop must still create on HDMI-A-1.
        s = base_snapshot()
        s["workspaces"] = [w for w in s["workspaces"] if w["id"] != 3]
        s["windows"] = [w for w in s["windows"] if w["workspaceId"] != 3]
        s["workspaces"].append({"id": 3, "name": "3", "output": "DP-1", "special": False, "isActive": False,
                                "isFocused": False, "isUrgent": False, "windows": 0, "hasFullscreen": False,
                                "persistent": False, "lastWindow": ""})
        plan, wid = adapter.plan_move_window(s, "0x2", 3, "HDMI-A-1", create=True)
        self.assertEqual(wid, 5)  # first free ID, never a special/named one
        self.assertIn('workspace = "5", monitor = "HDMI-A-1"', plan)
        # An empty workspace already on the intended display is used as is.
        s["workspaces"][-1]["output"] = "HDMI-A-1"
        self.assertEqual(adapter.plan_move_window(s, "0x2", 3, "HDMI-A-1", create=True)[1], 3)
        # One that gained windows meanwhile is not "new": reallocate.
        s["windows"].append(dict(s["windows"][0], id="0x99", workspaceId=3))
        self.assertEqual(adapter.plan_move_window(s, "0x2", 3, "HDMI-A-1", create=True)[1], 5)
        # The intended display was unplugged mid-drag.
        with self.assertRaisesRegex(adapter.CommandError, "no longer connected"):
            adapter.plan_move_window(s, "0x2", 3, "DP-9", create=True)
        with self.assertRaises(adapter.CommandError):
            adapter.plan_move_window(s, "0x2", -98, "DP-1", create=True)
        self.assertEqual(adapter.allocate_workspace(s, "DP-1"), 5)

    def test_reorder_walks_tiles(self):
        s = base_snapshot()
        # Tiles on workspace 1: 0x1, 0x2, 0x3 (0x7 floats).
        plan = adapter.plan_reorder(s, "0x1", 1, 2)
        swaps = [line for line in plan.splitlines() if line.startswith("S(")]
        self.assertEqual(swaps, ['S("address:0x1", "address:0x2")', 'S("address:0x1", "address:0x3")'])
        backwards = [l for l in adapter.plan_reorder(s, "0x3", 1, 0).splitlines() if l.startswith("S(")]
        self.assertEqual(backwards, ['S("address:0x3", "address:0x2")', 'S("address:0x3", "address:0x1")'])
        self.assertEqual(adapter.plan_reorder(s, "0x1", 1, 0), "")
        self.assertEqual(adapter.plan_reorder(s, "0x1", 1, 99).count("S("), 3)  # clamped: two swaps + helper
        self.assertEqual(adapter.plan_reorder(s, "0x7", 1, 0), "")  # floating keeps its place
        self.assertEqual(adapter.plan_reorder(s, "0x1", 1, None), "")

    def test_reorder_refuses_fullscreen_before_planning_swaps(self):
        for who in ("0x1", "0x2", "0x3"):
            s = base_snapshot()
            next(w for w in s["windows"] if w["id"] == who)["fullscreen"] = 1
            with self.subTest(fullscreen=who):
                with self.assertRaisesRegex(adapter.CommandError, "fullscreen"):
                    adapter.plan_reorder(s, "0x1", 1, 2)
        # A fullscreen window outside the walked range does not matter.
        s = base_snapshot()
        next(w for w in s["windows"] if w["id"] == "0x3")["fullscreen"] = 2
        self.assertIn('S("address:0x1", "address:0x2")', adapter.plan_reorder(s, "0x1", 1, 1))

    def test_workspace_reorder_permutes_ids_with_rollback(self):
        s = base_snapshot()
        plan = adapter.plan_move_workspace(s, 1, 4)
        # Order 1,2,4 -> 2,4,1: workspace 2 becomes 1, 4 becomes 2, 1 becomes 4.
        finals = [l for l in plan.splitlines() if "change_id" in l and not l.startswith("pcall")]
        self.assertEqual(len(finals), 6)
        self.assertIn('hl.get_workspace("1")', plan)
        self.assertIn("pcall(function()", plan)
        self.assertIn("error(err, 0)", plan)
        with self.assertRaises(adapter.CommandError):
            adapter.plan_move_workspace(s, 1, 3)  # other output
        with self.assertRaises(adapter.CommandError):
            adapter.plan_move_workspace(s, -1337, 1)
        self.assertEqual(adapter.plan_move_workspace(s, 2, 2), "")


class Lifecycle(unittest.TestCase):
    """Runs adapter.py as Pond does: a subprocess on stdin/stdout."""

    def setUp(self):
        self.fake = FakeHyprland()
        env = dict(self.fake.env, POND_XKB_ROOT=str(Path(__file__).parent / "fixtures/xkb"))
        self.proc = subprocess.Popen([sys.executable, str(HYPR / "adapter.py")], env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.lines = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(json.loads(line))

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        self.proc.stdout.close()
        self.proc.stderr.close()
        self.proc.stdin.close()
        self.fake.close()

    def expect(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.lines.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if predicate(message):
                return message
        self.fail("expected message not received")

    def wait_for_events_client(self, count=1):
        deadline = time.monotonic() + 3
        while self.fake.event_client_count() < count and time.monotonic() < deadline:
            time.sleep(0.02)

    def request(self, payload):
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def test_initial_snapshot_then_events(self):
        first = self.expect(lambda m: m["type"] == "snapshot")
        self.assertEqual(first["focusedOutput"], "DP-1")
        self.assertEqual(first["keyboard"], {"names": ["English (US)", "German"], "index": 0})
        self.expect(lambda m: m["type"] == "status" and m["connected"])
        self.wait_for_events_client()

        # A burst of structural events produces one coalesced snapshot.
        self.fake.state["clients"].append(client("0xb2", 2, 1920, 0, mon=1))
        self.fake.state["workspaces"][1]["windows"] = 1
        before = sum(1 for r in self.fake.requests if r == "j/clients")
        self.fake.send("openwindow>>b2,2,app,title", "activewindowv2>>b2", "focusedmonv2>>HDMI-A-1,2")
        snapshot = self.expect(lambda m: m["type"] == "snapshot")
        self.assertIn("0xb2", [w["id"] for w in snapshot["windows"]])
        time.sleep(0.3)
        self.assertEqual(sum(1 for r in self.fake.requests if r == "j/clients") - before, 1)

        # Titles are forwarded incrementally, with commas intact.
        self.fake.send("windowtitlev2>>a1,⠋ codex, working")
        title = self.expect(lambda m: m["type"] == "title")
        self.assertEqual(title, {"type": "title", "id": "0xa1", "title": "⠋ codex, working"})

        # Layout switch.
        self.fake.state["devices"]["keyboards"][0]["active_layout_index"] = 1
        self.fake.state["devices"]["keyboards"][0]["active_keymap"] = "German"
        self.fake.send("activelayout>>kbd,German")
        keyboard = self.expect(lambda m: m["type"] == "keyboard")
        self.assertTrue(keyboard["switched"])
        self.assertEqual(keyboard["index"], 1)

        self.fake.send("configreloaded>>")
        self.expect(lambda m: m["type"] == "configreloaded")

    def test_reconnects_after_event_socket_loss(self):
        self.expect(lambda m: m["type"] == "status" and m["connected"])
        self.wait_for_events_client()
        self.fake.drop_event_clients()
        self.expect(lambda m: m["type"] == "status" and not m["connected"])
        self.fake.state["clients"] = []
        reconnected = self.expect(lambda m: m["type"] == "snapshot", timeout=4)
        self.assertEqual(reconnected["windows"], [])
        self.expect(lambda m: m["type"] == "status" and m["connected"])

    def test_requests_reply_with_errors(self):
        self.expect(lambda m: m["type"] == "status" and m["connected"])
        bodies = []
        self.fake.repl = lambda code: (bodies.append(code), "POND_OK")[1]
        self.request({"id": 1, "action": "focus-workspace", "workspace": 9, "output": "HDMI-A-1", "create": True})
        self.assertEqual(self.expect(lambda m: m["type"] == "reply"), {"type": "reply", "id": 1, "ok": True, "error": ""})
        self.assertIn('focus({ monitor = "HDMI-A-1" })', bodies[-1])
        self.assertIn('monitor = "HDMI-A-1"', bodies[-1])

        self.fake.repl = lambda code: "error: window address:0xa1 no longer exists"
        self.request({"id": 2, "action": "move-window", "window": "0xa1", "workspace": 2, "slot": 0})
        reply = self.expect(lambda m: m["type"] == "reply")
        self.assertEqual((reply["id"], reply["ok"]), (2, False))
        self.assertIn("no longer exists", reply["error"])

        self.request({"id": 3, "action": "move-window", "window": "0xgone", "workspace": 2})
        self.assertFalse(self.expect(lambda m: m["type"] == "reply")["ok"])
        self.request({"id": 4, "action": "bogus"})
        self.assertIn("Unknown action", self.expect(lambda m: m["type"] == "reply")["error"])
        self.proc.stdin.write("not json\n")
        self.proc.stdin.flush()
        self.assertEqual(self.expect(lambda m: m["type"] == "reply")["error"], "Malformed request")

    def test_move_window_two_phases(self):
        self.expect(lambda m: m["type"] == "status" and m["connected"])
        self.fake.state["clients"] = [client("0x1", 1, 0, 0), client("0x2", 2, 1920, 0, mon=1),
                                      client("0x3", 2, 2880, 0, mon=1)]
        bodies = []

        def repl(code):
            bodies.append(code)
            if "window.move" in code:  # Hyprland places it as the last tile
                self.fake.state["clients"][0].update(workspace={"id": 2, "name": "2"}, at=[2880, 540], monitor=1)
            return "POND_OK"
        self.fake.repl = repl
        self.request({"id": 5, "action": "move-window", "window": "0x1", "workspace": 2, "slot": 0})
        self.assertTrue(self.expect(lambda m: m["type"] == "reply")["ok"])
        self.assertIn("window.move", bodies[0])
        self.assertEqual(bodies[1].count("window.swap"), 2)

    def test_failed_snapshot_retries_without_new_events(self):
        self.expect(lambda m: m["type"] == "status" and m["connected"])
        self.wait_for_events_client()
        self.fake.broken.add("clients")
        self.fake.state["clients"].append(client("0xb2", 2, 1920, 0, mon=1))
        self.fake.send("openwindow>>b2,2,app,title")
        down = self.expect(lambda m: m["type"] == "status" and not m["connected"])
        self.assertIn("unreadable reply", down["error"])
        # Bounded backoff: a handful of attempts, not a busy loop.
        time.sleep(1.2)
        attempts = sum(1 for r in self.fake.requests if r == "j/clients")
        self.assertLess(attempts, 10)
        self.fake.broken.clear()
        # No further compositor event arrives; the retry alone recovers.
        snapshot = self.expect(lambda m: m["type"] == "snapshot", timeout=5)
        self.assertIn("0xb2", [w["id"] for w in snapshot["windows"]])
        self.expect(lambda m: m["type"] == "status" and m["connected"])

    def test_snapshot_failure_across_reconnect_recovers_once(self):
        self.expect(lambda m: m["type"] == "status" and m["connected"])
        self.wait_for_events_client()
        self.fake.broken.add("monitors")
        self.fake.drop_event_clients()
        self.expect(lambda m: m["type"] == "status" and not m["connected"])
        # The event stream reconnects while snapshots still fail: stays down.
        self.wait_for_events_client()
        time.sleep(0.6)
        statuses = []
        while not self.lines.empty():
            message = self.lines.get()
            if message["type"] == "status":
                statuses.append(message["connected"])
        self.assertNotIn(True, statuses)
        self.fake.broken.clear()
        self.expect(lambda m: m["type"] == "status" and m["connected"], timeout=5)
        time.sleep(0.5)
        later = []
        while not self.lines.empty():
            later.append(self.lines.get())
        self.assertEqual([m for m in later if m["type"] == "status"], [])

    def test_creation_output_failure_reports_actual_partial_location(self):
        self.expect(lambda m: m["type"] == "status" and m["connected"])
        self.fake.state["clients"] = [client("0x1", 1, 0, 0)]

        def repl(code):
            self.assertIn("window.move", code)
            self.assertIn("workspace.move", code)
            self.fake.state["clients"][0]["workspace"] = {"id": 8, "name": "8"}
            original = dict(self.fake.state["workspaces"][0])
            original.update(id=8, name="8", monitor="DP-1", windows=1)
            self.fake.state["workspaces"].append(original)
            return "error: Monitor not found"

        self.fake.repl = repl
        self.request({"id": 81, "action": "move-window", "window": "0x1", "workspace": 8,
                      "output": "HDMI-A-1", "create": True})
        reply = self.expect(lambda m: m["type"] == "reply")
        self.assertFalse(reply["ok"])
        self.assertTrue(reply["partial"])
        self.assertIn("workspace 8 on DP-1", reply["error"])
        self.assertIn("Monitor not found", reply["error"])

    def test_move_then_fullscreen_reorder_reports_partial_state(self):
        self.expect(lambda m: m["type"] == "status" and m["connected"])
        for fullscreen in ("source", "neighbour"):
            with self.subTest(fullscreen=fullscreen):
                self.fake.state["clients"] = [client("0x1", 1, 0, 0), client("0x2", 2, 1920, 0, mon=1),
                                              client("0x3", 2, 2880, 0, mon=1)]
                if fullscreen == "neighbour":
                    self.fake.state["clients"][1]["fullscreen"] = 1
                bodies = []

                def repl(code, fullscreen=fullscreen):
                    bodies.append(code)
                    if "window.move" in code:
                        self.fake.state["clients"][0].update(workspace={"id": 2, "name": "2"}, at=[2880, 540],
                                                             monitor=1, fullscreen=2 if fullscreen == "source" else 0)
                    return "POND_OK"
                self.fake.repl = repl
                # A fullscreen window keeps its last tiled position in the
                # ordering, so pick a slot that needs swaps in either case.
                slot = 2 if fullscreen == "source" else 0
                self.request({"id": 7, "action": "move-window", "window": "0x1", "workspace": 2, "slot": slot})
                reply = self.expect(lambda m: m["type"] == "reply")
                self.assertFalse(reply["ok"])
                self.assertTrue(reply["partial"])
                self.assertIn("Moved the window to workspace 2", reply["error"])
                self.assertIn("fullscreen", reply["error"])
                # The move happened; no swap was attempted.
                self.assertEqual(len(bodies), 1)
                self.assertNotIn("window.swap", bodies[0])

    def test_stale_creation_row_is_created_on_intended_output(self):
        self.expect(lambda m: m["type"] == "status" and m["connected"])
        # Row "3 on HDMI-A-1" was drawn, then 3 appeared on DP-1.
        self.fake.state["workspaces"].append(workspace(3, "DP-1", 0))
        bodies = []
        self.fake.repl = lambda code: (bodies.append(code), "POND_OK")[1]
        self.request({"id": 8, "action": "move-window", "window": "0xa1", "workspace": 3, "output": "HDMI-A-1",
                      "create": True, "slot": 0})
        self.assertTrue(self.expect(lambda m: m["type"] == "reply")["ok"])
        self.assertIn('workspace = "4", follow = false', bodies[0])
        self.assertIn('hl.dsp.workspace.move({ workspace = "4", monitor = "HDMI-A-1" })', bodies[0])

    def test_exits_on_stdin_eof(self):
        self.expect(lambda m: m["type"] == "status")
        self.proc.stdin.close()
        self.assertEqual(self.proc.wait(timeout=3), 0)


DRAG_SHELL = """import QtQuick
import Quickshell
import "minimap" as Minimap
ShellRoot {
  id: shell
  property var dp: ({name: "DP-1", x: 0, y: 0, width: 1920, height: 1080})
  property var hdmi: ({name: "HDMI-A-1", x: 1920, y: 0, width: 1920, height: 1080})
  FloatingWindow { implicitWidth: 400; implicitHeight: 700; visible: true; color: "black"
    Minimap.Map { id: map1; screen: shell.dp; x: 20; y: 20 } }
  FloatingWindow { implicitWidth: 400; implicitHeight: 700; visible: true; color: "black"
    Minimap.Map { id: map2; screen: shell.hdmi; x: 20; y: 20 } }
  function out(key, value) { console.log("RESULT " + key + " " + JSON.stringify(value)); }
  property var steps: []
  function run(list) { steps = list; next(); }
  function next() { if (!steps.length) return; const s = steps[0]; steps = steps.slice(1);
                    timer.interval = s[0]; timer.action = s[1]; timer.start(); }
  Timer { id: timer; property var action; onTriggered: { action(); shell.next(); } }
  function rowY(map, globalX, predicate) {
    for (let y = 0; y < 600; y += 4) { const t = map.windowTargetAt(globalX, y); if (t && predicate(t)) return y; }
    return -1;
  }
  property real creationY: -1
  function begin() {
    map1.dragSession.begin({ kind: "window", windowData: { winId: "0xa1", iconName: "app", iconSource: "",
        dotState: "", loaderVariant: 1 }, slot: 0, workspaceId: 1 }, map1, Qt.point(60, 60), Qt.point(62, 62));
  }
  Component.onCompleted: run([
    [1500, () => { creationY = rowY(map2, 2000, t => t.creation); out("creationY", creationY); begin(); }],
    // Scenario 1: across to HDMI-A-1's creation row; workspace 4 then appears on DP-1.
    [200, () => map1.dragSession.move(Qt.point(2000, creationY))],
    [200, () => {
      const d = map2.windowDrop;
      out("onRemote", { pointerOnHdmi: Minimap.DragCoordinator.pointerMap === map2,
                        hdmiDrop: d ? { output: d.output, workspaceId: d.workspaceId, slot: d.slot,
                                        creation: d.creation } : null,
                        dpDrop: map1.windowDrop,
                        sourceIconHidden: !map1.dragSession.visible });
      Quickshell.execDetached(["touch", Quickshell.env("POND_TEST_MARK")]);
    }],
    [1200, () => map1.dragSession.release()],
    [2000, () => { out("afterDrop", { active: Minimap.DragCoordinator.active, dragOrigin: NiriMsg.dragOrigin });
                   begin(); }],
    // Scenario 2: over a row, then off every row (between displays), release.
    [200, () => map1.dragSession.move(Qt.point(2000, creationY))],
    [200, () => map1.dragSession.move(Qt.point(1900, 900))],
    [200, () => { out("left", { destination: Minimap.DragCoordinator.destination === null ? null : "set" }); map1.dragSession.release(); }],
    [1500, () => { out("afterLeave", { active: Minimap.DragCoordinator.active }); begin(); }],
    // Scenario 3: cancel while over a remote row.
    [200, () => map1.dragSession.move(Qt.point(2000, creationY))],
    [200, () => map1.dragSession.cancel()],
    [1500, () => { out("afterCancel", { active: Minimap.DragCoordinator.active, dragOrigin: NiriMsg.dragOrigin });
                   out("DONE", 1); Qt.quit(); }],
  ])
}
"""


NOTICE_SHELL = """import QtQuick
import Quickshell
import "minimap" as Minimap
ShellRoot {
  id: shell
  property var dp: ({name: "DP-1", x: 0, y: 0, width: 1920, height: 1080})
  property var hdmi: ({name: "HDMI-A-1", x: 1920, y: 0, width: 1920, height: 1080})
  FloatingWindow { implicitWidth: 400; implicitHeight: 700; visible: true; color: "black"
    Minimap.Map { id: map1; screen: shell.dp; x: 20; y: 20 } }
  FloatingWindow { implicitWidth: 400; implicitHeight: 700; visible: true; color: "black"
    Minimap.Map { id: map2; screen: shell.hdmi; x: 20; y: 20 } }
  function find(item, name) {
    if (item.objectName === name) return item;
    for (const child of item.children) { const found = find(child, name); if (found) return found; }
    return null;
  }
  function out(key, value) { console.log("RESULT " + key + " " + JSON.stringify(value)); }
  Timer { interval: 1500; running: true; onTriggered: NiriMsg.moveWindow("0xa1", 2, 0, ok => {}) }
  Timer { interval: 2600; running: true; onTriggered: {
    for (const [key, map] of [["dp", map1], ["hdmi", map2]]) {
      const notice = find(map, "minimapActionNotice");
      out(key, { visible: notice.visible && notice.opacity > 0.5, text: notice.text });
    }
    out("DONE", 1); Qt.quit();
  } }
}
"""


@unittest.skipUnless(qmlharness.QS, "set QS to the packaged Quickshell to run QML tests")
class MinimapDragQml(unittest.TestCase):
    """Two sidebar minimaps (two windows, two displays) running the real
    Map/DragSession/DragCoordinator, NiriMsg and adapter.py against a fake
    Hyprland: the drag starts in DP-1's minimap and ends in HDMI-A-1's."""

    def harness(self):
        harness = qmlharness.Harness()
        self.addCleanup(harness.close)
        package_shell = Path(qmlharness.QS).resolve().parents[3] / "share/pond-interface/shell"
        harness.root.rmdir()
        shutil.copytree(package_shell, harness.root)
        for path in harness.root.rglob("*"):
            path.chmod(path.stat().st_mode | 0o200)
        for relative in ("NiriMsg.qml", "hypr/adapter.py", "hypr/ipc.py", "hypr/Model.js", "hypr/xkb.py",
                         "minimap/Map.qml", "minimap/DragSession.qml", "minimap/DragCoordinator.qml",
                         "minimap/DragModel.js"):
            harness.copy(relative)
        return harness

    def env(self, fake, **extra):
        return dict({"XDG_RUNTIME_DIR": str(fake.runtime), "HYPRLAND_INSTANCE_SIGNATURE": fake.signature,
                     "POND_XKB_ROOT": str(Path(__file__).parent / "fixtures/xkb")}, **extra)

    def test_refused_action_is_shown_on_its_display(self):
        fake = FakeHyprland()
        self.addCleanup(fake.close)
        fake.state["workspaces"] = [workspace(1, "DP-1", 1, last="0xa1"), workspace(2, "HDMI-A-1", 1, last="0xb1")]
        fake.state["clients"] = [client("0xa1", 1, 10, 10), client("0xb1", 2, 1930, 10, mon=1)]
        fake.repl = lambda code: "error: Can't swap fullscreen window" if "window.move" in code else "POND_OK"
        harness = self.harness()
        harness.write("shell.qml", NOTICE_SHELL)
        results, log = harness.run(env=self.env(fake), timeout=60)
        self.assertEqual(results["hdmi"][0], {"visible": True, "text": "Can't swap fullscreen window"}, log[-2000:])
        self.assertFalse(results["dp"][0]["visible"])

    def test_cross_display_drop_leave_and_cancel(self):
        fake = FakeHyprland()
        self.addCleanup(fake.close)
        fake.state["workspaces"] = [workspace(1, "DP-1", 1, last="0xa1"), workspace(2, "HDMI-A-1", 1, last="0xb1")]
        fake.state["clients"] = [client("0xa1", 1, 10, 10), client("0xb1", 2, 1930, 10, mon=1)]
        moved = []

        def repl(code):
            if "window.move" in code:
                moved.append(code)
            return "POND_OK"
        fake.repl = repl
        harness = self.harness()
        harness.write("shell.qml", DRAG_SHELL)
        mark = Path(harness.tmp.name) / "mark"

        def collide():
            # Mid-drag, workspace 4 (the row's hint) is created on DP-1.
            deadline = time.monotonic() + 15
            while not mark.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            fake.state["workspaces"].append(workspace(4, "DP-1", 0))
            fake.send("createworkspacev2>>4,4")
        threading.Thread(target=collide, daemon=True).start()
        results, log = harness.run(env=self.env(fake, POND_TEST_MARK=str(mark)), timeout=60)
        self.assertGreater(results["creationY"][0], 0, log[-3000:])
        remote = results["onRemote"][0]
        self.assertTrue(remote["pointerOnHdmi"])
        self.assertEqual(remote["hdmiDrop"]["output"], "HDMI-A-1")
        self.assertEqual(remote["hdmiDrop"]["workspaceId"], 4)
        self.assertTrue(remote["hdmiDrop"]["creation"])
        self.assertIsNone(remote["dpDrop"])
        self.assertTrue(remote["sourceIconHidden"])
        # Exactly one move, committed by the source minimap: a new workspace
        # on HDMI-A-1 (3, since 4 now exists on DP-1), not DP-1's workspace 4.
        self.assertEqual(len(moved), 1, moved)
        self.assertIn('window.move({ window = "address:0xa1", workspace = "3", follow = false })', moved[0])
        self.assertIn('hl.dsp.workspace.move({ workspace = "3", monitor = "HDMI-A-1" })', moved[0])
        self.assertEqual(results["afterDrop"][0], {"active": False, "dragOrigin": None})
        self.assertIsNone(results["left"][0]["destination"])
        self.assertFalse(results["afterLeave"][0]["active"])
        self.assertEqual(results["afterCancel"][0], {"active": False, "dragOrigin": None})


if __name__ == "__main__":
    unittest.main()
