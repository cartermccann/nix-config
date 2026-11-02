"""Test doubles: a fake Hyprland instance (request + event sockets) and a fake
compositor model for display settings. Nothing here touches a live session."""
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1] / "overlay"
HYPR = ROOT / "shell" / "hypr"
sys.path.insert(0, str(HYPR))


def monitor(name, mid, x=0, width=1920, height=1080, focused=False, active=1, rate=60.0,
            modes=("1920x1080@60.00Hz", "1920x1080@50.00Hz", "1280x720@60.00Hz"), serial="S", scale=1.0,
            transform=0, vrr=False):
    return {"id": mid, "name": name, "make": "Make", "model": "Model " + name, "serial": serial + name,
            "width": width, "height": height, "refreshRate": rate, "x": x, "y": 0, "scale": scale,
            "transform": transform, "vrr": vrr, "focused": focused, "disabled": False, "dpmsStatus": True,
            "activeWorkspace": {"id": active, "name": str(active)}, "specialWorkspace": {"id": 0, "name": ""},
            "availableModes": list(modes)}


def client(address, ws, x, y, w=900, h=1000, floating=False, cls="app", title="", fullscreen=0, mon=0,
           stable="1", mapped=True):
    return {"address": address, "mapped": mapped, "hidden": False, "at": [x, y], "size": [w, h],
            "workspace": {"id": ws, "name": str(ws)}, "floating": floating, "monitor": mon, "class": cls,
            "title": title, "initialClass": cls, "pinned": False, "fullscreen": fullscreen, "stableId": stable}


def workspace(wid, monitor_name, windows=0, name=None, last="0x0"):
    return {"id": wid, "name": name or str(wid), "monitor": monitor_name, "windows": windows,
            "hasfullscreen": False, "lastwindow": last, "ispersistent": False}


class FakeHyprland:
    """Serves hyprctl-style requests and broadcasts events from a temp dir."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pond-fake-hypr-")
        self.runtime = Path(self.tmp.name)
        self.signature = "fake_instance"
        self.dir = self.runtime / "hypr" / self.signature
        self.dir.mkdir(parents=True)
        self.env = dict(os.environ, XDG_RUNTIME_DIR=str(self.runtime), HYPRLAND_INSTANCE_SIGNATURE=self.signature)
        self.requests = []
        self.state = {
            "monitors": [monitor("DP-1", 0, focused=True, active=1), monitor("HDMI-A-1", 1, x=1920, active=2)],
            "workspaces": [workspace(1, "DP-1", 1, last="0xa1"), workspace(2, "HDMI-A-1", 0)],
            "clients": [client("0xa1", 1, 10, 10, cls="com.mitchellh.ghostty", title="fish")],
            "activewindow": {"address": "0xa1"},
            "devices": {"keyboards": [{"name": "kbd", "main": True, "layout": "us,de", "variant": ",",
                                        "active_layout_index": 0, "active_keymap": "English (US)"}]},
        }
        self.repl = lambda code: "POND_OK"
        # Query names answered with an unreadable reply (a transient failure).
        self.broken = set()
        self.lock = threading.Lock()
        self.event_clients = []
        self.stopping = False
        self.request_socket = self._listen(".socket.sock", self._serve_requests)
        self.event_socket = self._listen(".socket2.sock", self._serve_events)

    def _listen(self, name, target):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.dir / name))
        server.listen(16)
        threading.Thread(target=target, args=(server,), daemon=True).start()
        return server

    def _serve_requests(self, server):
        while not self.stopping:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            with connection:
                data = b""
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                request = data.decode()
                with self.lock:
                    self.requests.append(request)
                connection.sendall(self.reply(request).encode())

    def reply(self, request):
        flags, _, command = request.partition("/")
        name, _, arg = command.partition(" ")
        if name == "repl":
            return self.repl(arg)
        if name in self.broken:
            return "Hyprland is busy"
        if name in self.state:
            return json.dumps(self.state[name])
        if name == "reload":
            return "ok"
        return "unknown request"

    def _serve_events(self, server):
        while not self.stopping:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            with self.lock:
                self.event_clients.append(connection)

    def send(self, *lines):
        payload = "".join(line + "\n" for line in lines).encode()
        with self.lock:
            for connection in list(self.event_clients):
                try:
                    connection.sendall(payload)
                except OSError:
                    self.event_clients.remove(connection)

    def drop_event_clients(self):
        with self.lock:
            for connection in self.event_clients:
                connection.shutdown(socket.SHUT_RDWR)
                connection.close()
            self.event_clients.clear()

    def event_client_count(self):
        with self.lock:
            return len(self.event_clients)

    def close(self):
        self.stopping = True
        self.request_socket.close()
        self.event_socket.close()
        self.drop_event_clients()
        self.tmp.cleanup()


MONITOR_CALL = re.compile(r"hl\.monitor\(\{ (.*?) \}\)")
FIELD = re.compile(r'(\w+) = ("(?:[^"\\]|\\.)*"|[-0-9.]+|true|false)')


def parse_monitor_calls(body):
    calls = []
    for match in MONITOR_CALL.finditer(body):
        fields = {}
        for key, value in FIELD.findall(match[1]):
            fields[key] = json.loads(value) if value[0] == '"' or value in ("true", "false") else float(value)
        calls.append(fields)
    return calls


def apply_monitor_calls(monitors_by_name, body, reject=lambda call: False):
    """Apply hl.monitor templates in `body` the way Hyprland would."""
    for call in parse_monitor_calls(body):
        if reject(call):
            continue  # Hyprland silently keeps the old value
        m = monitors_by_name[call["output"]]
        width, height, rate = re.fullmatch(r"(\d+)x(\d+)@([\d.]+)", call["mode"]).groups()
        x, y = map(int, call["position"].split("x"))
        m.update(width=int(width), height=int(height), refreshRate=float(rate), x=x, y=y,
                 scale=call["scale"], transform=int(call["transform"]), vrr=bool(call["vrr"]))


class FakeCompositor:
    """In-memory monitors that apply hl.monitor templates like Hyprland would."""

    def __init__(self, monitors, reject=None):
        self.monitors = {m["name"]: m for m in monitors}
        self.reject = reject or (lambda call: False)
        self.bodies = []

    def query(self, command):
        if command == "monitors":
            return [dict(m) for m in self.monitors.values()]
        raise AssertionError(command)

    def run_lua(self, body):
        self.bodies.append(body)
        apply_monitor_calls(self.monitors, body, self.reject)
