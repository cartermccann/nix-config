"""Hyprland monitor settings: advertised modes, live state and verified changes."""
import math
import re
import time

import ipc

SCALES = (0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3)
# wl_output transforms; "clockwise" steps backwards through each cycle.
ROTATIONS = ((0, 1, 2, 3), (4, 5, 6, 7))
MODE = re.compile(r"^(\d{2,5})x(\d{2,5})@(\d{1,3}(?:\.\d{1,3})?)(?:Hz)?$")


class DisplayError(RuntimeError):
    pass


def identity(m):
    return " ".join(str(m.get(k) or "Unknown") for k in ("make", "model", "serial"))


def parse_mode(text):
    match = MODE.fullmatch(str(text).strip())
    if not match:
        return None
    width, height, rate = int(match[1]), int(match[2]), match[3]
    return {"id": f"{width}x{height}@{rate}", "width": width, "height": height, "refresh": float(rate)}


def modes(m):
    result, seen = [], set()
    for text in m.get("availableModes") or []:
        mode = parse_mode(text)
        if mode and mode["id"] not in seen:
            seen.add(mode["id"])
            result.append(mode)
    return result


def current_mode(m):
    width, height, rate = m.get("width", 0), m.get("height", 0), float(m.get("refreshRate") or 0)
    candidates = [x for x in modes(m) if x["width"] == width and x["height"] == height]
    if candidates:
        best = min(candidates, key=lambda x: abs(x["refresh"] - rate))
        if abs(best["refresh"] - rate) < 0.1:
            return best
    return {"id": f"{width}x{height}@{rate:.3f}".rstrip("0").rstrip("."), "width": width, "height": height,
            "refresh": rate, "custom": True}


def logical_size(width, height, scale, transform):
    w, h = width / scale, height / scale
    return (h, w) if int(transform) % 2 else (w, h)


def valid_scales(width, height, current=None):
    scales = {s for s in SCALES if abs(width / s - round(width / s)) < 1e-6 and abs(height / s - round(height / s)) < 1e-6}
    if current:
        scales.add(round(float(current), 4))
    return sorted(scales)


def rotated(transform, direction):
    cycle = ROTATIONS[1] if int(transform) >= 4 else ROTATIONS[0]
    step = {"clockwise": -1, "counterclockwise": 1}[direction]
    return cycle[(cycle.index(int(transform)) + step) % 4]


def live_settings(m):
    mode = current_mode(m)
    return {"identity": identity(m), "mode": mode["id"], "position": [int(m.get("x", 0)), int(m.get("y", 0))],
            "scale": round(float(m.get("scale", 1)), 4), "transform": int(m.get("transform", 0)),
            "vrr": bool(m.get("vrr"))}


def active(query=ipc.query):
    return {m["name"]: m for m in query("monitors") if m.get("name") and not m.get("disabled")}


def rect(name, settings):
    mode = parse_mode(settings["mode"])
    w, h = logical_size(mode["width"], mode["height"], settings["scale"], settings["transform"])
    return {"name": name, "x": settings["position"][0], "y": settings["position"][1],
            "width": math.ceil(w - 1e-6), "height": math.ceil(h - 1e-6)}


def validate(settings):
    """Raise ValueError unless `settings` is one well-formed monitor entry."""
    if not isinstance(settings, dict):
        raise ValueError("Display entry is not an object")
    if not isinstance(settings.get("identity"), str) or not 0 < len(settings["identity"]) < 300:
        raise ValueError("Display identity is invalid")
    if parse_mode(settings.get("mode", "")) is None:
        raise ValueError("Display mode is invalid")
    position = settings.get("position")
    if (not isinstance(position, list) or len(position) != 2
            or any(type(v) is not int or abs(v) > 100000 for v in position)):
        raise ValueError("Display position is invalid")
    scale = settings.get("scale")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or not 0.25 <= scale <= 4:
        raise ValueError("Display scale is invalid")
    if type(settings.get("transform")) is not int or not 0 <= settings["transform"] <= 7:
        raise ValueError("Display rotation is invalid")
    if type(settings.get("vrr")) is not bool:
        raise ValueError("Adaptive sync setting is invalid")
    return settings


def lua(name, settings):
    validate(settings)
    spec = {"output": ipc.output_name(name), "mode": parse_mode(settings["mode"])["id"],
            "position": f"{settings['position'][0]}x{settings['position'][1]}",
            "scale": settings["scale"], "transform": settings["transform"], "vrr": 1 if settings["vrr"] else 0}
    return f"hl.monitor({ipc.lua_value(spec)})"


def matches(m, settings):
    wanted, actual = parse_mode(settings["mode"]), current_mode(m)
    return (identity(m) == settings["identity"]
            and (actual["width"], actual["height"]) == (wanted["width"], wanted["height"])
            and abs(actual["refresh"] - wanted["refresh"]) < 0.1
            and [int(m.get("x", 0)), int(m.get("y", 0))] == list(settings["position"])
            and abs(float(m.get("scale", 1)) - settings["scale"]) < 0.01
            and int(m.get("transform", 0)) == settings["transform"]
            and bool(m.get("vrr")) == settings["vrr"])


def apply(wanted, run_lua=ipc.run_lua, query=ipc.query, attempts=30, delay=0.1, sleep=time.sleep):
    """Apply {connector: settings} together and wait for Hyprland to report them."""
    if not wanted:
        return active(query)
    run_lua("\n".join(lua(name, settings) for name, settings in wanted.items()))
    live = {}
    for _ in range(attempts):
        live = active(query)
        if all(name in live and matches(live[name], s) for name, s in wanted.items()):
            return live
        sleep(delay)
    missing = [name for name in wanted if name not in live]
    if missing:
        raise DisplayError(f"{missing[0]} disconnected while the change was applied")
    raise DisplayError("Hyprland did not accept the requested display settings")


def restore_previous(before, run_lua=ipc.run_lua, query=ipc.query):
    """Put each display back to `before` where that same display is still
    connected, and verify it. Returns the names left alone (unplugged or a
    different display). Raises if Hyprland does not take the old settings."""
    for attempt in range(2):
        live = active(query)
        wanted = {n: s for n, s in before.items() if n in live and identity(live[n]) == s["identity"]}
        try:
            apply(wanted, run_lua=run_lua, query=query)
            return sorted(set(before) - set(wanted))
        except DisplayError:
            # A display vanished while restoring; retry once with the rest.
            if attempt or set(wanted) <= set(active(query)):
                raise
    raise AssertionError("unreachable")
