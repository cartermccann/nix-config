#!/usr/bin/env python3
"""Keyboard languages, pointer settings and timezone for Pond on Hyprland.

Keyboard and pointer changes are applied to the running compositor, verified,
and only then saved to Pond's validated settings (hypr/userstate.py). A
change Hyprland does not take is rolled back to the previous values.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from PyQt6.QtCore import QDateTime, QLocale, QTimeZone

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hypr"))
import ipc  # noqa: E402
import userstate  # noqa: E402
import xkb  # noqa: E402

ENGLISH = QLocale("en_US")
ZONEINFO = "/usr/share/zoneinfo"


def run(*args, timeout=15):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or "Could not change system settings")
    return p.stdout


def languages(selected=()):
    result = [item for item in xkb.layouts().values()
              if item.get("listed") or item["id"] == "us(colemak_dh)" or item["id"] in selected]
    return sorted(({"id": i["id"], "label": i["label"]} for i in result), key=lambda item: item["label"].casefold())


def main_keyboard():
    keyboards = [k for k in ipc.query("devices").get("keyboards", []) if k.get("name")]
    keyboard = next((k for k in keyboards if k.get("main")), None) or next(
        (k for k in keyboards if "virtual" not in k["name"]), None)
    if keyboard is None:
        raise RuntimeError("No keyboard is connected")
    return keyboard


def keyboard():
    k = main_keyboard()
    ids = xkb.identifiers(k.get("layout", ""), k.get("variant", ""))
    return {"ids": ids, "names": [xkb.label(i) for i in ids], "index": int(k.get("active_layout_index") or 0)}


def option(name):
    reply = ipc.query("getoption " + name)
    for key in ("float", "int", "str"):
        if key in reply:
            value = reply[key]
            return "" if value == "[[EMPTY]]" else value
    if "bool" in reply:
        return bool(reply["bool"])
    raise RuntimeError(f"Hyprland has no option {name}")


def current_input():
    return {"kb_layout": option("input:kb_layout"), "kb_variant": option("input:kb_variant"),
            "sensitivity": float(option("input:sensitivity")), "accel_profile": option("input:accel_profile") or "adaptive",
            "scroll_factor": float(option("input:scroll_factor")), "natural_scroll": bool(option("input:natural_scroll")),
            "touchpad_natural_scroll": bool(option("input:touchpad:natural_scroll"))}


def zone_dir():
    for directory in (os.environ.get("TZDIR"), ZONEINFO, "/etc/zoneinfo"):
        if directory and (Path(directory) / "zone.tab").is_file():
            return Path(directory)
    raise RuntimeError("The timezone database is unavailable")


def zone(identifier, now):
    tz = QTimeZone(identifier.encode())
    offset = tz.offsetFromUtc(now)
    label = tz.displayName(now, QTimeZone.NameType.LongName, ENGLISH)
    return {"id": identifier, "label": label, "minutes": offset // 60,
            "offset": f'GMT{"-" if offset < 0 else "+"}{abs(offset) // 3600:02}:{abs(offset) // 60 % 60:02}',
            "search": label + " " + tz.abbreviation(now)}


def timezones(current):
    with open(zone_dir() / "zone.tab") as table:
        identifiers = sorted(line.split("\t")[2].strip() for line in table if not line.startswith("#"))
    now = QDateTime.currentDateTimeUtc()
    groups = {}
    for i in [current, "UTC", *identifiers]:
        z = zone(i, now)
        groups.setdefault((z["label"], z["minutes"], QTimeZone(i.encode()).offsetFromUtc(now.addMonths(6))), z)
    labels = [z["label"] for z in groups.values()]
    for z in groups.values():
        if labels.count(z["label"]) > 1:
            z["label"] += " – " + z["id"].rpartition("/")[2].replace("_", " ")
    return sorted(groups.values(), key=lambda z: (z["minutes"], z["label"]))


def timezone_declared():
    """NixOS owns /etc/localtime when time.timeZone is set."""
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return False
    return target.startswith(("/etc/static/", "/nix/store/")) or "/nix/store/" in os.path.realpath("/etc/localtime")


def mouse(values):
    return {"speed": (values["sensitivity"] + 1) / 2, "scroll": values["scroll_factor"],
            "acceleration": values["accel_profile"] != "flat",
            "naturalTouchpad": values["touchpad_natural_scroll"], "naturalMouse": values["natural_scroll"]}


def status():
    k = keyboard()
    values = current_input()
    timezone = bytes(QTimeZone.systemTimeZoneId()).decode()
    try:
        customized = bool(userstate.load()["input"])
    except userstate.InvalidState:
        customized = True
    return {"selectedLanguages": k["ids"], "languageNames": k["names"],
            "timezone": timezone, "timezoneLabel": zone(timezone, QDateTime.currentDateTimeUtc())["label"],
            "timezoneManaged": timezone_declared(), "mouse": mouse(values), "customized": customized}


def load():
    result = status()
    return dict(result, languages=languages(result["selectedLanguages"]), timezones=timezones(result["timezone"]))


def apply_input(changes, verify, attempts=30):
    """Apply input options live, verify, then persist; restore on failure."""
    with userstate.locked():
        state = userstate.load()
        previous = current_input()
        wanted = dict(state["input"], **changes)
        userstate.validate_input(wanted)
        try:
            ipc.run_lua(userstate.input_lua(changes))
            for _ in range(attempts):
                if verify():
                    break
                time.sleep(.1)
            else:
                raise RuntimeError("Hyprland did not apply the setting")
        except Exception:
            ipc.run_lua(userstate.input_lua({k: previous[k] for k in changes}
                                            | ({"kb_variant": previous["kb_variant"]} if "kb_layout" in changes else {})))
            raise
        state["input"] = wanted
        userstate.save(state)


def toggle_language(identifier):
    catalog = xkb.layouts()
    if identifier not in catalog:
        raise ValueError("Unknown keyboard language")
    current = keyboard()
    unknown = [i for i in current["ids"] if i not in catalog]
    if unknown:
        raise ValueError("Unknown keyboard language: " + unknown[0])
    chosen = list(current["ids"])
    if identifier in chosen:
        chosen.remove(identifier)
    else:
        chosen.append(identifier)
    if not chosen:
        raise ValueError("Keep at least one keyboard language")
    if len(chosen) > 8:
        raise ValueError("Hyprland supports at most eight keyboard languages")
    layout, variant = xkb.option_values(chosen)
    apply_input({"kb_layout": layout, "kb_variant": variant}, lambda: keyboard()["ids"] == chosen)
    active = current["ids"][current["index"]] if 0 <= current["index"] < len(current["ids"]) else None
    if active in chosen and chosen.index(active) != 0:
        ipc.request(f"switchxkblayout all {chosen.index(active)}")
    return status()


def set_timezone(identifier):
    if timezone_declared():
        raise ValueError("The timezone is set by NixOS (time.timeZone). Change it in your system "
                         "configuration and rebuild.")
    if not (zone_dir() / identifier).is_file() or ".." in identifier:
        raise ValueError("Unknown timezone")
    # timedated asks polkit; Pond never assumes passwordless sudo.
    run("timedatectl", "set-timezone", identifier, timeout=60)
    return status()


def set_mouse(speed, scroll, acceleration, natural_touchpad, natural_mouse):
    changes = {"sensitivity": round(max(0.0, min(1.0, float(speed))) * 2 - 1, 2),
               "scroll_factor": round(max(0.1, min(2.0, float(scroll))), 2),
               "accel_profile": "adaptive" if acceleration == "true" else "flat",
               "natural_scroll": natural_mouse == "true",
               "touchpad_natural_scroll": natural_touchpad == "true"}

    def verify():
        values = current_input()
        return all(abs(values[k] - v) < 0.005 if isinstance(v, float) else values[k] == v for k, v in changes.items())

    apply_input(changes, verify)
    return status()


def reset():
    userstate.reset("input")
    return status()


if __name__ == "__main__":
    try:
        action, *args = sys.argv[1:]
        commands = {"load": load, "keyboard": toggle_language, "timezone": set_timezone, "mouse": set_mouse,
                    "reset": reset}
        print(json.dumps(commands[action](*args)))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)
