#!/usr/bin/env python3
"""Small JSON bridge for the Appearance panel's hardware and saved settings."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hypr"))
import ipc  # noqa: E402

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
IMAGE_TYPES = "*.png *.jpg *.jpeg *.webp *.avif *.bmp *.gif *.svg *.PNG *.JPG *.JPEG *.WEBP"


def wallpaper_dirs():
    """Pond's picker opens the shared wallpaper library first."""
    configured = os.environ.get("POND_WALLPAPER_DIR")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates += [Path.home() / "wallpapers", Path.home() / "Pictures/Wallpapers", Path.home() / "Pictures"]
    return [path for path in candidates if path.is_dir()] or [Path.home()]


def run(*args, timeout=12):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"{args[0]} failed")
    return result.stdout


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(text)
    try:
        if path.exists():
            temporary.chmod(path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def brightness_device(output, cached=""):
    device = json.loads(cached) if cached else None
    # Match brightness control to the selected connector, not detection order.
    if not device and not output.startswith(("eDP", "LVDS", "DSI")):
        # The targeted brightness read below also checks DDC support.
        detected = run("ddcutil", "detect", "--brief", "--skip-ddc-checks")
        for block in re.split(r"(?=Display \d+)", detected):
            connector = re.search(r"DRM connector:\s+\S*card\d+-(\S+)", block)
            bus = re.search(r"I2C bus:\s+/dev/i2c-(\d+)", block)
            if connector and bus and connector[1] == output:
                device = {"kind": "ddc", "device": bus[1]}
                break
    elif not device:
        devices = sorted(Path("/sys/class/backlight").glob("*"))
        if devices:
            device = {"kind": "backlight", "device": devices[0].name}
    try:
        if device and device["kind"] == "ddc":
            value = run("ddcutil", "-b", device["device"], "getvcp", "10", "--brief",
                        *(["--skip-ddc-checks"] if cached else []))
            match = re.search(r"VCP 10 C (\d+) (\d+)", value)
            if match and int(match[2]) > 0:
                return dict(device, current=int(match[1]), max=int(match[2]))
        elif device:
            path = Path("/sys/class/backlight") / device["device"]
            return dict(device, current=int((path / "brightness").read_text()),
                        max=int((path / "max_brightness").read_text()))
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        if not cached:
            raise
    if cached:
        return brightness_device(output)
    raise RuntimeError("Brightness control is unavailable for this display")


def status(output, cached=""):
    monitor = next((m for m in ipc.query("monitors") if m.get("name") == output and not m.get("disabled")), None)
    result = {"output": output, "connected": monitor is not None,
              "brightness": None, "brightnessDevice": None, "brightnessError": ""}
    if result["connected"]:
        result.update(width=monitor["width"], height=monitor["height"], scale=monitor["scale"])
        try:
            device = brightness_device(output, cached)
            result["brightness"] = device["current"] / device["max"]
            result["brightnessDevice"] = device
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            result["brightnessError"] = str(error)
    return result


def set_brightness(output, value, cached=""):
    value = max(0.01, min(1, float(value)))
    device = json.loads(cached) if cached else brightness_device(output)
    if device["kind"] == "ddc":
        run("ddcutil", "-b", device["device"], "--noverify", "--enable-dynamic-sleep",
            "--sleep-multiplier=0.05", "setvcp", "10", str(round(value * device["max"])))
    else:
        run("brightnessctl", "-d", device["device"], "set", f"{round(value * 100)}%")
    return {"brightness": value}


def step_brightness(output, delta):
    device = brightness_device(output)
    value = device["current"] / device["max"] + float(delta)
    return set_brightness(output, value, json.dumps(device))


def wallpaper(mode, path=""):
    state = {"mode": mode, "path": "", "url": ""}
    if mode == "custom":
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        # Keep the path as chosen: ~/wallpapers is a Home Manager link into
        # the store, and resolving it would pin an old generation's file.
        image = Path(os.path.abspath(Path(path).expanduser()))
        if not image.is_file() or GdkPixbuf.Pixbuf.get_file_info(str(image))[0] is None:
            raise ValueError("Choose a readable image file")
        state.update(path=str(image), url=f"{image.as_uri()}?{image.stat().st_mtime_ns}")
    elif mode != "dynamic":
        raise ValueError("Unknown wallpaper mode")
    atomic_write(CONFIG / "pond-interface/wallpaper.json", json.dumps(state) + "\n")
    return state


def pick_wallpaper():
    result = subprocess.run(["zenity", "--file-selection", "--title=Choose a wallpaper",
                             f"--filename={wallpaper_dirs()[0]}/",
                             f"--file-filter=Images | {IMAGE_TYPES}",
                             "--file-filter=All files | *"], capture_output=True, text=True)
    if result.returncode == 1:
        return {"cancelled": True}
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Could not open the image picker")
    return wallpaper("custom", result.stdout.rstrip("\n"))


def theme(name):
    if name not in ("daylight", "unthemed"):
        raise ValueError("Unknown theme")
    atomic_write(CONFIG / "pond-interface/theme.json", json.dumps({"theme": name}) + "\n")
    return {"theme": name}


def main():
    try:
        action, *args = sys.argv[1:]
        commands = {"status": status, "brightness": set_brightness, "brightness-step": step_brightness,
                    "wallpaper": wallpaper, "pick-wallpaper": pick_wallpaper, "theme": theme}
        print(json.dumps(commands[action](*args)))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
