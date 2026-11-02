#!/usr/bin/env python3
"""Hyprland display settings: advertised modes, confirmed changes and layouts.

No custom modes. A change is a transaction whose rollback point is made
durable before anything is touched: the previous settings are written to the
pending record and a detached watchdog confirms (over a pipe) that it is
running, and only then is the change applied and verified. `confirm` saves it
to Pond's settings; `revert`, the watchdog at the deadline, a stale `status`
after the deadline, or `userstate.py restore` (shell restart, config reload)
restore and verify the previous layout before retiring the record. If the
record or the watchdog cannot be established, nothing is changed. A change
that fails to apply is rolled back before the error is reported; a rollback
that fails keeps the record so the watchdog retries it. The Nix-generated
Hyprland config is never written.
"""
import copy
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hypr"))
import ipc  # noqa: E402
import monitors  # noqa: E402
import userstate  # noqa: E402
from userstate import adjacent, overlaps, validate_layout  # noqa: E402,F401

CONFIRM_SECONDS = 15
# Extra time the watchdog allows while a change is still being applied (one
# Lua request plus verification is bounded well below this).
APPLY_SECONDS = 15
# A record this far past its deadline means the watchdog is gone; `status`
# then reverts it itself.
STALE_SECONDS = 5
WATCHDOG_START_SECONDS = 3


def run_lua(body):
    return ipc.run_lua(body)


def query(command):
    return ipc.query(command)


def outputs():
    return monitors.active(query)


def settings_of(live):
    return {name: monitors.live_settings(m) for name, m in live.items()}


def geometry(settings):
    return [dict(monitors.rect(name, s), identity=s["identity"]) for name, s in sorted(settings.items())]


def normalize(rects):
    minx, miny = min(r["x"] for r in rects), min(r["y"] for r in rects)
    return [dict(r, x=r["x"] - minx, y=r["y"] - miny) for r in rects]


def reflow(before, after):
    """Keep edge relationships when mode, scale or rotation changes display sizes."""
    old = {r["name"]: r for r in geometry(before)}
    pending = geometry(after)
    placed = [pending.pop(0)]
    while pending:
        pick = next(((i, p) for i, r in enumerate(pending) for p in placed
                     if adjacent(old[r["name"]], old[p["name"]])), (0, placed[0]))
        i, anchor = pick
        r = pending.pop(i)
        a, b = old[r["name"]], old[anchor["name"]]
        x, y = anchor["x"] + a["x"] - b["x"], anchor["y"] + a["y"] - b["y"]
        if a["x"] == b["x"] + b["width"]: x = anchor["x"] + anchor["width"]
        elif a["x"] + a["width"] == b["x"]: x = anchor["x"] - r["width"]
        if a["y"] == b["y"] + b["height"]: y = anchor["y"] + anchor["height"]
        elif a["y"] + a["height"] == b["y"]: y = anchor["y"] - r["height"]
        candidates = [dict(r, x=x, y=y)]
        for p in placed:
            cy = max(p["y"] - r["height"] + 1, min(y, p["y"] + p["height"] - 1))
            cx = max(p["x"] - r["width"] + 1, min(x, p["x"] + p["width"] - 1))
            candidates += [dict(r, x=p["x"] - r["width"], y=cy), dict(r, x=p["x"] + p["width"], y=cy),
                           dict(r, x=cx, y=p["y"] - r["height"]), dict(r, x=cx, y=p["y"] + p["height"])]
        valid = [c for c in candidates if not any(overlaps(c, p) for p in placed)
                 and any(adjacent(c, p) for p in placed)]
        placed.append(min(valid, key=lambda c: (c["x"] - x) ** 2 + (c["y"] - y) ** 2))
    return normalize(placed)


def label_of(name, m):
    if name.startswith(("eDP-", "LVDS-", "DSI-", "DPI-")):
        return "Built-in"
    return m.get("model") or m.get("make") or name


def pending_summary(now=time.time):
    pending = userstate.read_pending()
    if not pending:
        return None
    return {"id": pending["id"], "remaining": max(0, round(pending["deadline"] - now())),
            "seconds": CONFIRM_SECONDS, "phase": pending["phase"], "error": pending.get("error", "")}


def status(live=None, now=time.time):
    live = outputs() if live is None else live
    settings = settings_of(live)
    result = []
    for name, m in sorted(live.items()):
        mode = monitors.current_mode(m)
        offered = monitors.modes(m)
        resolutions = sorted({(x["width"], x["height"]) for x in offered}, key=lambda s: (-s[0] * s[1], -s[0]))
        rates = sorted({x["refresh"] for x in offered if (x["width"], x["height"]) == (mode["width"], mode["height"])},
                       reverse=True)
        result.append(dict(
            name=name, label=label_of(name, m), identity=monitors.identity(m),
            logical=monitors.rect(name, settings[name]), mode=mode["id"],
            width=mode["width"], height=mode["height"], refresh=round(mode["refresh"] * 1000),
            scale=settings[name]["scale"],
            scales=monitors.valid_scales(mode["width"], mode["height"], settings[name]["scale"]),
            resolutions=[dict(label=f"{w} x {h}", value=f"{w}x{h}") for w, h in resolutions],
            rates=[dict(label=f"{rate:.3f}".rstrip("0").rstrip(".") + " Hz", value=str(round(rate * 1000))) for rate in rates],
            # Hyprland does not report adaptive-sync capability; an
            # unsupported request fails verification and is rolled back.
            vrrSupported=True, vrrEnabled=settings[name]["vrr"]))
    labels = [m["label"] for m in result]
    for m in result:
        if labels.count(m["label"]) > 1:
            m["label"] += " (" + m["name"] + ")"
    try:
        saved = userstate.load()["monitors"]
        stateError = ""
    except userstate.InvalidState as error:
        saved, stateError = {}, str(error)
    try:
        pending = pending_summary(now)
    except userstate.InvalidState as error:
        pending, stateError = None, str(error)
    return dict(monitors=result, geometry=geometry(settings),
                pending=pending, customized=bool(saved), stateError=stateError)


def plan(action, before, offered, name="", value="", expected_identity=""):
    """Return the complete wanted {connector: settings} for a request."""
    wanted = copy.deepcopy(before)
    if action == "arrange":
        request = json.loads(name)
        if request.get("baseline") != geometry(before):
            raise ValueError("Displays changed. Reopen Arrange and try again.")
        positions = {p["name"]: p for p in request.get("positions", [])}
        if set(positions) != set(before):
            raise ValueError("Displays changed. Reopen Arrange and try again.")
        rects = normalize([dict(r, x=int(positions[r["name"]]["x"]), y=int(positions[r["name"]]["y"]))
                           for r in geometry(before)])
        validate_layout(rects)
    else:
        if name not in before:
            raise ValueError("Display is no longer connected")
        if expected_identity and before[name]["identity"] != expected_identity:
            raise ValueError("The selected display changed. Please select it again.")
        s = wanted[name]
        mode = monitors.parse_mode(s["mode"])
        if action == "scale":
            scale = float(value)
            if scale not in monitors.valid_scales(mode["width"], mode["height"], before[name]["scale"]):
                raise ValueError("Unsupported display scale")
            s["scale"] = round(scale, 4)
        elif action == "rotate":
            if value not in ("clockwise", "counterclockwise"):
                raise ValueError("Unknown rotation")
            s["transform"] = monitors.rotated(s["transform"], value)
        elif action in ("resolution", "refresh"):
            if action == "resolution":
                candidates = [x for x in offered[name] if f"{x['width']}x{x['height']}" == value]
            else:
                candidates = [x for x in offered[name] if (x["width"], x["height"]) == (mode["width"], mode["height"])
                              and str(round(x["refresh"] * 1000)) == value]
            if not candidates:
                raise ValueError("This display does not advertise that mode")
            selected = min(candidates, key=lambda x: abs(x["refresh"] - mode["refresh"]))
            s["mode"] = selected["id"]
            if action == "resolution":
                selected_scales = monitors.valid_scales(selected["width"], selected["height"])
                if s["scale"] not in selected_scales:
                    s["scale"] = min(selected_scales, key=lambda v: abs(v - s["scale"]))
        elif action == "vrr":
            if value not in ("on", "off"):
                raise ValueError("Unknown adaptive sync state")
            s["vrr"] = value == "on"
        else:
            raise ValueError("Unknown display action")
        rects = reflow(before, wanted) if action in ("scale", "resolution", "rotate") else geometry(before)
        if action in ("scale", "resolution", "rotate"):
            validate_layout(rects)
    for r in rects:
        wanted[r["name"]]["position"] = [r["x"], r["y"]]
    return wanted


def spawn_watchdog(transaction, timeout=WATCHDOG_START_SECONDS):
    """Start the detached watchdog and wait until it reports that it holds
    the transaction. Raises if it does not; the caller then aborts."""
    read_end, write_end = os.pipe()
    try:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "watchdog", transaction,
                                    str(write_end)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True, close_fds=True, pass_fds=(write_end,))
    except OSError as error:
        os.close(read_end)
        os.close(write_end)
        raise RuntimeError(f"could not start the safety timer: {error}") from error
    os.close(write_end)
    try:
        reply = b""
        deadline = time.monotonic() + timeout
        while not reply.endswith(b"\n"):
            ready, _, _ = select.select([read_end], [], [], max(0.0, deadline - time.monotonic()))
            chunk = os.read(read_end, 256) if ready else b""
            if not chunk:
                break
            reply += chunk
    finally:
        os.close(read_end)
    if reply != f"ready {transaction}\n".encode():
        process.kill()
        raise RuntimeError("the safety timer did not start")
    return process.pid


def _revert(pending):
    return userstate.revert_pending(pending, run_lua=run_lua, query=query)


def apply(action, name="", value="", expected_identity="", now=time.time, watchdog=spawn_watchdog):
    with userstate.locked():
        pending = userstate.read_pending()
        if pending and now() > pending["deadline"] + STALE_SECONDS:
            _revert(pending)  # its watchdog is gone; raises if it cannot
            pending = None
        if pending:
            raise ValueError("Keep or revert the previous display change first")
        live = outputs()
        before = settings_of(live)
        offered = {n: monitors.modes(m) for n, m in live.items()}
        wanted = plan(action, before, offered, name, value, expected_identity)
        changed = {n: s for n, s in wanted.items() if s != before[n]}
        if not changed:
            return status(live, now)
        previous = {n: before[n] for n in changed}
        record = {"id": uuid.uuid4().hex, "phase": "applying", "deadline": now() + CONFIRM_SECONDS + APPLY_SECONDS,
                  "before": previous, "after": changed}
        # Durable rollback point and a live watchdog first; no change without both.
        try:
            userstate.write_pending(record)
        except (OSError, userstate.InvalidState) as error:
            raise RuntimeError(f"Display change not applied: could not record it ({error})") from error
        try:
            watchdog(record["id"])
        except Exception as error:
            userstate.pending_path().unlink(missing_ok=True)
            raise RuntimeError(f"Display change not applied: {error}") from error
        try:
            result = monitors.apply(changed, run_lua=run_lua, query=query)
        except Exception as error:
            try:
                _revert(record)
            except monitors.DisplayError as rollback:
                raise RuntimeError(f"{error}. {rollback}. Pond will retry when the timer ends.") from error
            raise
        record.update(phase="applied", deadline=now() + CONFIRM_SECONDS,
                      after={n: monitors.live_settings(result[n]) for n in changed})
        try:
            userstate.write_pending(record)
        except (OSError, userstate.InvalidState) as error:
            try:
                _revert(record)
            except monitors.DisplayError as rollback:
                raise RuntimeError(f"Could not record the display change ({error}). {rollback}") from error
            raise RuntimeError(f"Could not record the display change, so it was reverted ({error})") from error
    return status(result, now)


def confirm(transaction, now=time.time):
    with userstate.locked():
        pending = userstate.read_pending()
        if not pending or pending["id"] != transaction:
            raise ValueError("This display change was already reverted")
        if pending["phase"] != "applied":
            raise ValueError("This display change cannot be kept; revert it")
        if now() > pending["deadline"]:
            _revert(pending)
            raise ValueError("The display change timed out and was reverted")
        live = outputs()
        drifted = [n for n, s in pending["after"].items()
                   if n in live and monitors.identity(live[n]) == s["identity"] and not monitors.matches(live[n], s)]
        if drifted:
            _revert(pending)
            raise ValueError("Displays changed before the change was kept, so it was reverted")
        state = userstate.load()
        for name, settings in pending["after"].items():
            if name in live and monitors.identity(live[name]) == settings["identity"]:
                state["monitors"][name] = monitors.validate(settings)
        userstate.save(state)
        userstate.pending_path().unlink(missing_ok=True)
        return status(live)


def revert(transaction="", only_expired=False, now=time.time):
    """Restore the previous layout (verified) and retire the transaction.
    Raises, keeping the record, if Hyprland does not take the old settings."""
    with userstate.locked():
        pending = userstate.read_pending()
        if not pending or (transaction and pending["id"] != transaction):
            return status()
        if only_expired and now() < pending["deadline"]:
            return None
        _revert(pending)
        return status()


def expire_stale(now=time.time):
    """Revert a transaction whose watchdog evidently died."""
    try:
        pending = userstate.read_pending()
    except userstate.InvalidState:
        return
    if pending and now() > pending["deadline"] + STALE_SECONDS:
        revert(pending["id"], only_expired=True, now=now)


def watchdog(transaction, ready_fd=None, now=time.time, sleep=time.sleep, poll=0.5, attempts=5):
    """Wait for the deadline (re-reading it: applying extends it, confirming
    removes the record), then revert. Retries a failed revert a few times."""
    try:
        pending = userstate.read_pending()
    except userstate.InvalidState:
        pending = None
    if not pending or pending["id"] != transaction:
        return 1
    if ready_fd is not None:
        # The rollback duty stands even if the starter is already gone.
        try:
            os.write(ready_fd, f"ready {transaction}\n".encode())
        except OSError:
            pass
        os.close(ready_fd)
    failures = 0
    while True:
        try:
            pending = userstate.read_pending()
        except userstate.InvalidState:
            return 1
        if not pending or pending["id"] != transaction:
            return 0
        remaining = pending["deadline"] - now()
        if remaining > 0:
            sleep(min(remaining, poll))
            continue
        try:
            if revert(transaction, only_expired=True, now=now) is not None:
                return 0
        except Exception:
            failures += 1
            if failures >= attempts:
                return 1
            sleep(min(2 ** failures, 10))


def main():
    try:
        action, *args = sys.argv[1:]
        if action == "status":
            try:
                expire_stale()
            except Exception as error:
                result = dict(status(), error=str(error))
            else:
                result = status()
        elif action == "confirm":
            result = confirm(*args)
        elif action == "revert":
            result = revert(*args)
        elif action == "watchdog":
            transaction, *fd = args
            sys.exit(watchdog(transaction, int(fd[0]) if fd else None))
        elif action == "reset":
            userstate.reset("display")
            result = status()
        else:
            result = apply(action, *args)
        print(json.dumps(result))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
