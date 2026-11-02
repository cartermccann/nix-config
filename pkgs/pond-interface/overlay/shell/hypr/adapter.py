#!/usr/bin/env python3
"""Pond's Hyprland adapter process (the backend of shell/NiriMsg.qml).

Writes JSON lines to stdout:
  {"type": "status", "connected": bool, "error": str}
  {"type": "snapshot", "monitors": [...], "workspaces": [...], "windows": [...],
   "focusedOutput": str, "keyboard": {"names": [...], "index": int}}
  {"type": "title", "id": "0x...", "title": str}
  {"type": "keyboard", "names": [...], "index": int, "switched": bool}
  {"type": "configreloaded"}
  {"type": "reply", "id": int, "ok": bool, "error": str}
and reads one JSON request per stdin line: {"id": int, "action": str, ...}.

State comes from an initial snapshot plus Hyprland's event socket. Structural
events schedule a coalesced re-snapshot (no periodic polling); title changes
are forwarded incrementally. The event socket reconnects with backoff, and
a failed snapshot is retried with its own bounded backoff while the socket
stays open, so a transient request failure cannot leave the model stale or
the status disconnected until some unrelated event happens to arrive.
"""
import json
import os
from pathlib import Path
import selectors
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ipc  # noqa: E402
import xkb  # noqa: E402

DEBOUNCE = 0.03
MAX_DELAY = 0.15
RETRY_MIN = 0.25
RETRY_MAX = 3.0
IGNORED_EVENTS = {"submap", "openlayer", "closelayer", "screencast", "screencastv2", "custom", "bell",
                  "lockgroups", "activespecial", "workspace", "focusedmon", "activewindow", "movewindow",
                  "moveworkspace", "createworkspace", "destroyworkspace", "monitoradded", "monitorremoved",
                  "windowtitle", "ignoregrouplock"}


class CommandError(Exception):
    def __init__(self, message, partial=False):
        super().__init__(message)
        # The action changed something before it failed (a window moved to
        # its workspace but could not be put at the requested position).
        self.partial = partial


# --- Snapshot normalisation -------------------------------------------------

def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def is_special(workspace):
    return str(workspace.get("name", "")).startswith("special:")


def build_snapshot(monitors, workspaces, clients, active, keyboard=None, geometry_cache=None, urgent=()):
    """Normalise hyprctl JSON into the adapter model.

    `geometry_cache` keeps each window's last non-fullscreen geometry, so a
    fullscreen window keeps its tiled position in the minimap ordering.
    """
    geometry_cache = {} if geometry_cache is None else geometry_cache
    monitor_names = {m.get("id"): m.get("name", "") for m in monitors}
    focused_monitor = next((m for m in monitors if m.get("focused")), monitors[0] if monitors else None)
    focused_output = focused_monitor.get("name", "") if focused_monitor else ""
    active_ids = {m.get("activeWorkspace", {}).get("id") for m in monitors}
    focused_ws = focused_monitor.get("activeWorkspace", {}).get("id") if focused_monitor else None
    active_address = ipc.normalize_address(active.get("address")) if active and active.get("address") else ""

    windows = []
    live = set()
    for client in clients:
        if client.get("mapped") is False:
            continue
        address = ipc.normalize_address(client.get("address"))
        live.add(address)
        at, size = client.get("at") or [0, 0], client.get("size") or [0, 0]
        fullscreen = _int(client.get("fullscreen"))
        if fullscreen == 0 and not client.get("floating"):
            geometry_cache[address] = (at[0], at[1], size[0], size[1])
        ox, oy, _, _ = geometry_cache.get(address, (at[0], at[1], size[0], size[1])) if fullscreen else (at[0], at[1], 0, 0)
        stable = str(client.get("stableId") or "")
        try:
            serial = int(stable, 16)
        except ValueError:
            serial = int(address, 16)
        workspace = client.get("workspace") or {}
        windows.append({
            "id": address,
            "workspaceId": _int(workspace.get("id"), -999999),
            "output": monitor_names.get(client.get("monitor"), ""),
            "title": str(client.get("title") or ""),
            "appId": str(client.get("class") or client.get("initialClass") or ""),
            "isFocused": address == active_address,
            "isFloating": bool(client.get("floating")),
            "fullscreen": fullscreen,
            "pinned": bool(client.get("pinned")),
            "hidden": bool(client.get("hidden")),
            "isUrgent": address in urgent,
            "x": at[0], "y": at[1], "width": size[0], "height": size[1],
            "serial": serial,
            "_ox": ox, "_oy": oy,
        })
    for address in list(geometry_cache):
        if address not in live:
            del geometry_cache[address]

    # Minimap order: tiled windows in reading order of their (dwindle) tiles,
    # left to right then top to bottom; floating windows after them.
    by_workspace = {}
    for window in windows:
        by_workspace.setdefault(window["workspaceId"], []).append(window)
    for group in by_workspace.values():
        group.sort(key=lambda w: (w["isFloating"], w["_ox"], w["_oy"], w["serial"]))
        for index, window in enumerate(group):
            window["order"] = index
    for window in windows:
        del window["_ox"], window["_oy"]
    windows.sort(key=lambda w: (w["workspaceId"], w["order"]))

    normalized_workspaces = []
    for ws in workspaces:
        wid = _int(ws.get("id"))
        normalized_workspaces.append({
            "id": wid,
            "name": str(ws.get("name") or ""),
            "output": str(ws.get("monitor") or ""),
            "special": is_special(ws),
            "isActive": wid in active_ids,
            "isFocused": wid == focused_ws,
            "isUrgent": any(w["isUrgent"] for w in by_workspace.get(wid, [])),
            "windows": len(by_workspace.get(wid, [])),
            "hasFullscreen": bool(ws.get("hasfullscreen")),
            "persistent": bool(ws.get("ispersistent")),
            "lastWindow": "" if str(ws.get("lastwindow") or "0x0") in ("", "0x0", "0")
            else ipc.normalize_address(ws.get("lastwindow")),
        })
    normalized_workspaces.sort(key=lambda w: (w["output"], w["id"]))

    normalized_monitors = [{
        "name": m.get("name", ""), "id": m.get("id"), "focused": bool(m.get("focused")),
        "activeWorkspaceId": _int((m.get("activeWorkspace") or {}).get("id")),
        "specialWorkspaceId": _int((m.get("specialWorkspace") or {}).get("id")),
        "x": m.get("x", 0), "y": m.get("y", 0), "width": m.get("width", 0), "height": m.get("height", 0),
        "scale": m.get("scale", 1), "transform": m.get("transform", 0), "dpms": bool(m.get("dpmsStatus", True)),
    } for m in monitors if not m.get("disabled")]

    return {"monitors": normalized_monitors, "workspaces": normalized_workspaces, "windows": windows,
            "focusedOutput": focused_output, "keyboard": keyboard or {"names": [], "index": 0}}


def keyboard_state(devices):
    keyboards = [k for k in devices.get("keyboards", []) if k.get("name")]
    main = next((k for k in keyboards if k.get("main")), None) or next(
        (k for k in keyboards if "virtual" not in k["name"]), None)
    if not main:
        return {"names": [], "index": 0, "ids": []}
    ids = xkb.identifiers(main.get("layout", ""), main.get("variant", ""))
    names = [xkb.label(identifier) for identifier in ids]
    index = _int(main.get("active_layout_index"))
    if names and 0 <= index < len(names) and main.get("active_keymap"):
        names[index] = main["active_keymap"]
    return {"names": names, "index": max(0, min(index, len(names) - 1)) if names else 0, "ids": ids}


# --- Action planning (pure) -------------------------------------------------

def find_window(snapshot, address):
    return next((w for w in snapshot["windows"] if w["id"] == address), None)


def find_workspace(snapshot, wid):
    return next((w for w in snapshot["workspaces"] if w["id"] == wid), None)


def workspace_selector(workspace, wid):
    if workspace and workspace["special"]:
        raise CommandError("Special workspaces are not shown in the minimap")
    if wid > 0:
        return str(wid)
    if workspace and workspace["name"]:
        return "name:" + workspace["name"]
    raise CommandError("Workspace no longer exists")


def plan_focus_window(address):
    selector = ipc.window_selector(address)
    return f"W({ipc.lua_string(selector)})\nD(hl.dsp.focus({{ window = {ipc.lua_string(selector)} }}))"


def output_exists(snapshot, output):
    return any(m["name"] == output for m in snapshot["monitors"])


def allocate_workspace(snapshot, output):
    """A fresh numeric ID for a new workspace on `output`, as Model.js picks one."""
    used = {w["id"] for w in snapshot["workspaces"]}
    candidate = max([w["id"] for w in snapshot["workspaces"] if w["output"] == output and w["id"] > 0] + [0]) + 1
    while candidate in used:
        candidate += 1
    return candidate


def creation_target(snapshot, wid, output, moving=""):
    """Resolve a creation row's intent: a new workspace on `output`.

    Row IDs are chosen when the sidebar is drawn; by the time a drop or click
    commits, that ID may belong to a workspace on another display or one that
    gained windows. Only an empty workspace already on `output` still means
    "new workspace here"; anything else gets a fresh ID on `output`.
    """
    if wid <= 0:
        raise CommandError("Invalid new workspace")
    if not output:
        raise CommandError("A new workspace needs a display")
    if not output_exists(snapshot, output):
        raise CommandError("Display is no longer connected")
    ipc.output_name(output)
    workspace = find_workspace(snapshot, wid)
    if workspace is None:
        return wid
    occupied = any(w["workspaceId"] == wid and w["id"] != moving for w in snapshot["windows"])
    if workspace["output"] == output and not workspace["special"] and not occupied:
        return wid
    return allocate_workspace(snapshot, output)


def creation_guard(selector, output, wid):
    """Lua that re-checks a creation target on the compositor thread."""
    return (f"local target = hl.get_workspace({ipc.lua_string(selector)})\n"
            f"if target ~= nil and (target.monitor == nil or target.monitor.name ~= "
            f"{ipc.lua_string(ipc.output_name(output))} or target.windows > 0) then "
            f"error({ipc.lua_string(f'Workspace {wid} was just taken on another display; try again')}, 0) end")


def plan_focus_workspace(snapshot, wid, output="", create=False):
    """Focus a workspace; a creation row (`create`) makes one on `output`."""
    wid = ipc.workspace_id(wid)
    if create:
        wid = creation_target(snapshot, wid, output)
    workspace = find_workspace(snapshot, wid)
    selector = workspace_selector(workspace, wid)
    if not create:
        if workspace is None:
            raise CommandError("Workspace no longer exists")
        return f"D(hl.dsp.focus({{ workspace = {ipc.lua_string(selector)} }}))"
    # Hyprland creates a workspace on the focused monitor; focusing the one
    # that is already focused is a successful no-op.
    lines = [creation_guard(selector, output, wid),
             f"if target == nil then D(hl.dsp.focus({{ monitor = {ipc.lua_string(output)} }})) end"]
    lines.append(f"D(hl.dsp.focus({{ workspace = {ipc.lua_string(selector)} }}))")
    return "\n".join(lines)


def plan_move_window(snapshot, address, wid, output="", create=False):
    """Phase one: put the window on the workspace (a new one on `output` for
    a creation row). Returns the Lua body and the effective workspace ID."""
    wid = ipc.workspace_id(wid)
    window = find_window(snapshot, address)
    if window is None:
        raise CommandError("Window no longer exists")
    if create:
        wid = creation_target(snapshot, wid, output, moving=address)
    workspace = find_workspace(snapshot, wid)
    selector = workspace_selector(workspace, wid)
    if window["workspaceId"] == wid:
        return "", wid
    if not create and workspace is None:
        raise CommandError("Workspace no longer exists")
    sel = ipc.lua_string(ipc.window_selector(address))
    target = ipc.lua_string(selector)
    lines = [f"W({sel})"]
    if create:
        lines.append(creation_guard(selector, output, wid))
    else:
        lines.append(f"if hl.get_workspace({target}) == nil then "
                     f"error({ipc.lua_string(f'Workspace {workspace_label(workspace, wid)} no longer exists')}, 0) end")
    lines.append(f"D(hl.dsp.window.move({{ window = {sel}, workspace = {target}, follow = false }}))")
    if create:
        lines.append(f"if target == nil then D(hl.dsp.workspace.move({{ workspace = {target}, "
                     f"monitor = {ipc.lua_string(output)} }})) end")
    return "\n".join(lines), wid


def workspace_label(workspace, wid):
    return workspace["name"] if workspace and workspace["name"] else str(wid)


# Hyprland 0.56 Actions::swapWith refuses when either window is fullscreen
# (any internal mode, maximized included).
FULLSCREEN_REORDER = "Windows can't be reordered while one of them is fullscreen"


def plan_reorder(snapshot, address, wid, slot):
    """Phase two: walk a tiled window to `slot` by swapping with neighbours.

    Each swap exchanges two tiles, so successive swaps shift the windows in
    between by one tile: the dwindle equivalent of inserting at a position.
    Floating windows keep their free position. Swaps involving a fullscreen
    window are refused before anything moves; if a swap still fails on the
    compositor, the swaps already made are undone in reverse order.
    """
    window = find_window(snapshot, address)
    if window is None:
        raise CommandError("Window no longer exists")
    if window["workspaceId"] != wid or window["isFloating"] or slot is None:
        return ""
    tiled = [w for w in snapshot["windows"] if w["workspaceId"] == wid and not w["isFloating"]]
    current = next(i for i, w in enumerate(tiled) if w["id"] == address)
    target = max(0, min(int(slot), len(tiled) - 1))
    if target == current:
        return ""
    step = 1 if target > current else -1
    partners = [tiled[index] for index in range(current + step, target + step, step)]
    if window["fullscreen"] or any(w["fullscreen"] for w in partners):
        raise CommandError(FULLSCREEN_REORDER)
    sel = ipc.lua_string(ipc.window_selector(address))
    swaps = "\n".join(f"S({sel}, {ipc.lua_string(ipc.window_selector(w['id']))})" for w in partners)
    return ("local done = {}\n"
            "local function S(a, b)\n"
            "  if W(a).fullscreen ~= 0 or W(b).fullscreen ~= 0 then "
            f"error({ipc.lua_string(FULLSCREEN_REORDER)}, 0) end\n"
            "  D(hl.dsp.window.swap({ window = a, with = b }))\n"
            "  done[#done + 1] = { a, b }\n"
            "end\n"
            f"local ok, err = pcall(function()\n{swaps}\nend)\n"
            "if not ok then\n"
            "  for i = #done, 1, -1 do pcall(D, hl.dsp.window.swap({ window = done[i][1], with = done[i][2] })) end\n"
            "  error(err, 0)\n"
            "end")


def plan_move_workspace(snapshot, source, target):
    """Reorder workspaces on one output by permuting their numeric IDs.

    Workspace objects are captured first, so the permutation is applied to
    the same workspaces even as their IDs change; any failure restores the
    original IDs before the error is reported.
    """
    source, target = ipc.workspace_id(source), ipc.workspace_id(target)
    src, dst = find_workspace(snapshot, source), find_workspace(snapshot, target)
    if src is None or dst is None or src["special"] or dst["special"]:
        raise CommandError("Workspace no longer exists")
    if src["output"] != dst["output"]:
        raise CommandError("Workspaces can only be reordered on the same display")
    if source <= 0 or target <= 0:
        raise CommandError("Named workspaces keep their position")
    if source == target:
        return ""
    ids = sorted(w["id"] for w in snapshot["workspaces"]
                 if w["output"] == src["output"] and not w["special"] and w["id"] > 0)
    order = [i for i in ids if i != source]
    order.insert(ids.index(target), source)
    changes = [(old, new) for old, new in zip(order, ids) if old != new]
    base = max([w["id"] for w in snapshot["workspaces"]] + [0]) + 1000
    names = {old: f"o{n}" for n, (old, _) in enumerate(changes)}
    lines = [f"local {names[old]} = hl.get_workspace({ipc.lua_string(str(old))})"
             + f" if {names[old]} == nil then error(\"workspace {old} no longer exists\", 0) end"
             for old, _ in changes]
    phase = []
    for n, (old, _) in enumerate(changes):
        phase.append(f"D(hl.dsp.workspace.change_id({{ workspace = {names[old]}, id = {base + n} }}))")
    for old, new in changes:
        phase.append(f"D(hl.dsp.workspace.change_id({{ workspace = {names[old]}, id = {new} }}))")
    rollback = []
    for n, (old, _) in enumerate(changes):
        rollback.append(f"pcall(D, hl.dsp.workspace.change_id({{ workspace = {names[old]}, id = {base + len(changes) + n} }}))")
    for old, _ in changes:
        rollback.append(f"pcall(D, hl.dsp.workspace.change_id({{ workspace = {names[old]}, id = {old} }}))")
    lines.append("local ok, err = pcall(function()\n" + "\n".join(phase) + "\nend)")
    lines.append("if not ok then\n" + "\n".join(rollback) + "\nerror(err, 0)\nend")
    return "\n".join(lines)


def plan_dpms(state):
    if state not in ("on", "off"):
        raise CommandError("Unknown display power state")
    return f'D(hl.dsp.dpms({{ action = "{"enable" if state == "on" else "disable"}" }}))'


# --- Process ----------------------------------------------------------------

class Adapter:
    def __init__(self, out=None, env=None, clock=time.monotonic):
        self.out = out or sys.stdout
        self.env = os.environ if env is None else env
        self.clock = clock
        self.selector = selectors.DefaultSelector()
        self.events = None
        self.event_buffer = b""
        self.stdin_buffer = b""
        self.retry_at = 0.0
        self.backoff = RETRY_MIN
        self.refresh_retry_at = None
        self.refresh_backoff = RETRY_MIN
        self.dirty_since = None
        self.due = None
        self.snapshot = None
        self.last_emitted = ""
        self.geometry = {}
        self.urgent = set()
        self.keyboard = {"names": [], "index": 0, "ids": []}
        self.connected = None
        self.last_error = None
        self.running = True

    # Output
    def emit(self, message):
        try:
            self.out.write(json.dumps(message, separators=(",", ":")) + "\n")
            self.out.flush()
        except BrokenPipeError:
            self.running = False

    def status(self, connected, error=""):
        # Report changes only; a retry loop must not repeat the same error.
        if connected != self.connected or error != self.last_error:
            self.connected, self.last_error = connected, error
            self.emit({"type": "status", "connected": connected, "error": error})

    # Queries
    def query(self, command):
        return ipc.query(command, env=self.env)

    def refresh_keyboard(self):
        try:
            self.keyboard = keyboard_state(self.query("devices"))
        except (ipc.HyprError, OSError, ValueError) as error:
            self.status(False, str(error))
        return self.keyboard

    def refresh(self, force=False):
        self.due = self.dirty_since = None
        try:
            monitors, workspaces, clients = self.query("monitors"), self.query("workspaces"), self.query("clients")
            active = self.query("activewindow")
        except ipc.HyprError as error:
            self.status(False, str(error))
            if self.events is not None:
                self.refresh_retry_at = self.clock() + self.refresh_backoff
                self.refresh_backoff = min(self.refresh_backoff * 2, RETRY_MAX)
            return None
        self.refresh_retry_at, self.refresh_backoff = None, RETRY_MIN
        if active and active.get("address"):
            self.urgent.discard(ipc.normalize_address(active["address"]))
        self.snapshot = build_snapshot(monitors, workspaces, clients, active,
                                       {k: self.keyboard[k] for k in ("names", "index")},
                                       self.geometry, self.urgent)
        text = json.dumps(self.snapshot, sort_keys=True)
        if force or text != self.last_emitted:
            self.last_emitted = text
            self.emit(dict(self.snapshot, type="snapshot"))
        if self.events is not None:
            self.status(True)
        return self.snapshot

    def schedule(self):
        now = self.clock()
        if self.dirty_since is None:
            self.dirty_since = now
        self.due = min(now + DEBOUNCE, self.dirty_since + MAX_DELAY)

    # Event socket
    def connect_events(self):
        try:
            path = ipc.socket_dir(self.env) / ".socket2.sock"
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(2)
            connection.connect(str(path))
            connection.setblocking(False)
        except (ipc.HyprError, OSError) as error:
            self.status(False, f"Waiting for Hyprland: {error}")
            self.retry_at = self.clock() + self.backoff
            self.backoff = min(self.backoff * 2, 3.0)
            return False
        self.events, self.event_buffer, self.backoff = connection, b"", RETRY_MIN
        self.selector.register(connection, selectors.EVENT_READ, "events")
        self.refresh_keyboard()
        self.refresh(force=True)
        return True

    def drop_events(self, error=""):
        if self.events is not None:
            self.selector.unregister(self.events)
            self.events.close()
            self.events = None
        self.refresh_retry_at, self.refresh_backoff = None, RETRY_MIN
        self.status(False, error or "Lost the Hyprland event stream")
        self.retry_at = self.clock() + self.backoff

    def read_events(self):
        try:
            data = self.events.recv(65536)
        except BlockingIOError:
            return
        except OSError as error:
            self.drop_events(str(error))
            return
        if not data:
            self.drop_events()
            return
        self.event_buffer += data
        *lines, self.event_buffer = self.event_buffer.split(b"\n")
        for line in lines:
            self.handle_event(line.decode("utf-8", "replace"))

    def handle_event(self, line):
        name, _, data = line.partition(">>")
        if not name or name in IGNORED_EVENTS:
            return
        if name == "windowtitlev2":
            address, _, title = data.partition(",")
            address = ipc.normalize_address(address)
            window = find_window(self.snapshot, address) if self.snapshot else None
            if window is None:
                self.schedule()
            elif window["title"] != title:
                window["title"] = title
                self.last_emitted = json.dumps(self.snapshot, sort_keys=True)
                self.emit({"type": "title", "id": address, "title": title})
        elif name == "activelayout":
            before = dict(self.keyboard)
            after = self.refresh_keyboard()
            switched = after["names"] == before["names"] and after["index"] != before["index"]
            if after != before:
                self.emit({"type": "keyboard", "names": after["names"], "index": after["index"], "switched": switched})
                if self.snapshot is not None:
                    self.snapshot["keyboard"] = {"names": after["names"], "index": after["index"]}
        elif name == "configreloaded":
            self.refresh_keyboard()
            self.emit({"type": "configreloaded"})
            self.schedule()
        elif name == "urgent":
            self.urgent.add(ipc.normalize_address(data))
            self.schedule()
        else:
            self.schedule()

    # Requests
    def read_stdin(self, stream):
        data = os.read(stream.fileno(), 65536)
        if not data:
            self.running = False
            return
        self.stdin_buffer += data
        *lines, self.stdin_buffer = self.stdin_buffer.split(b"\n")
        for line in lines:
            if line.strip():
                self.handle_request(line.decode("utf-8", "replace"))

    def handle_request(self, line):
        try:
            request = json.loads(line)
            rid = request.get("id")
        except (ValueError, AttributeError):
            self.emit({"type": "reply", "id": None, "ok": False, "error": "Malformed request"})
            return
        try:
            self.perform(request)
            self.emit({"type": "reply", "id": rid, "ok": True, "error": ""})
        except (CommandError, ipc.HyprError, ValueError, TypeError, KeyError) as error:
            self.emit({"type": "reply", "id": rid, "ok": False, "error": str(error) or type(error).__name__,
                       "partial": bool(getattr(error, "partial", False))})
        self.refresh()

    def perform(self, request):
        action = request.get("action")
        if action == "refresh":
            self.refresh_keyboard()
            self.refresh(force=True)
            return
        snapshot = self.refresh()
        if snapshot is None:
            raise CommandError("Hyprland is unavailable")
        if action == "focus-window":
            body = plan_focus_window(ipc.normalize_address(request["window"]))
        elif action == "focus-workspace":
            body = plan_focus_workspace(snapshot, request["workspace"], request.get("output", ""),
                                        request.get("create") is True)
        elif action == "move-window":
            address = ipc.normalize_address(request["window"])
            body, wid = plan_move_window(snapshot, address, ipc.workspace_id(request["workspace"]),
                                         request.get("output", ""), request.get("create") is True)
            if body:
                try:
                    ipc.run_lua(body, env=self.env)
                except ipc.HyprError as error:
                    # A creation-row move has two dispatches: move the
                    # window, then place its workspace on the output. The
                    # first may have succeeded when the second fails.
                    after = self.refresh()
                    before_window = find_window(snapshot, address)
                    after_window = find_window(after, address) if after else None
                    if after is None:
                        raise CommandError(f"Move failed; the final window location could not be confirmed: {error}",
                                           partial=True) from error
                    if after_window and before_window and after_window["workspaceId"] != before_window["workspaceId"]:
                        workspace = find_workspace(after, after_window["workspaceId"])
                        label = workspace_label(workspace, after_window["workspaceId"])
                        output = f" on {workspace['output']}" if workspace else ""
                        raise CommandError(f"Window moved to workspace {label}{output}, but the requested move "
                                           f"could not be completed: {error}", partial=True) from error
                    raise
                self.place_moved(address, wid, request.get("slot"))
                return
            body = plan_reorder(snapshot, address, wid, request.get("slot"))
        elif action == "move-workspace":
            body = plan_move_workspace(snapshot, request["workspace"], request["target"])
        elif action == "dpms":
            body = plan_dpms(request.get("state"))
        else:
            raise CommandError(f"Unknown action {action!r}")
        if body:
            ipc.run_lua(body, env=self.env)

    def place_moved(self, address, wid, slot):
        """Reorder after a completed cross-workspace move; a failure here is
        reported as partial, naming where the window actually is."""
        snapshot = self.refresh()
        label = str(wid)
        try:
            if snapshot is None:
                raise CommandError("Hyprland stopped answering")
            label = workspace_label(find_workspace(snapshot, wid), wid)
            body = plan_reorder(snapshot, address, wid, slot)
            if body:
                ipc.run_lua(body, env=self.env)
        except (CommandError, ipc.HyprError, ValueError) as error:
            raise CommandError(f"Moved the window to workspace {label}, but could not put it in that position: "
                               f"{error}", partial=True) from error

    def run(self, stdin=None):
        stdin = stdin or sys.stdin
        os.set_blocking(stdin.fileno(), False)
        self.selector.register(stdin, selectors.EVENT_READ, "stdin")
        self.connect_events()
        while self.running:
            now = self.clock()
            deadlines = [d for d in (self.due, self.refresh_retry_at if self.events else self.retry_at)
                         if d is not None]
            timeout = max(0.0, min(deadlines) - now) if deadlines else None
            for key, _ in self.selector.select(timeout):
                if key.data == "stdin":
                    try:
                        self.read_stdin(key.fileobj)
                    except BlockingIOError:
                        pass
                elif key.data == "events" and self.events is not None:
                    self.read_events()
            now = self.clock()
            if self.events is None and now >= self.retry_at:
                self.connect_events()
            if self.events is not None and any(d is not None and now >= d for d in (self.due, self.refresh_retry_at)):
                self.refresh()


if __name__ == "__main__":
    try:
        Adapter().run()
    except KeyboardInterrupt:
        pass
