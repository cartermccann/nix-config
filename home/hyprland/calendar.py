"""The clock's calendar dropdown: a month grid and a day agenda, pinned under
the waybar clock as a layer-shell surface (namespace `auseklis-calendar`).

Events come from Google Calendar's "secret address in iCal format" links,
listed per account in ~/.config/credentials/calendars.toml:

    week_start = "sunday"            # or "monday"

    [[account]]
    name  = "ComCreate"
    email = "tech@comcreate.org"     # picks the account when opening Google
    urls  = ["https://calendar.google.com/calendar/ical/.../basic.ics"]

Read-only by construction: no OAuth, so no Google sign-in flow on perkons.
Each link is cached under ~/.cache/auseklis-calendar and refetched when older
than ten minutes, so the dropdown opens on cached data and fills in after.

Opening and closing is the wrapper's job (`auseklis-calendar` kills a running
instance instead of starting a second). From inside, Esc, a click anywhere
outside it, or opening an event in the browser closes it. Click-away is a
transparent layer over every monitor rather than a focus-out handler:
Hyprland's follow_mouse refocuses whatever window the pointer crosses, which
closed a focus-based popup before you could reach it.
"""

import datetime as dt
import hashlib
import os
import signal
import subprocess
import threading
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

# The wrapper preloads gtk4-layer-shell for this process only; keep it out of
# everything spawned from here (hyprctl, the browser).
os.environ.pop("LD_PRELOAD", None)
HYPRCTL = os.environ.pop("AUSEKLIS_HYPRCTL", "hyprctl")

HOME = Path.home()
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
CREDS = CONFIG / "credentials" / "calendars.toml"
STYLE = CONFIG / "auseklis-calendar" / "style.css"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "auseklis-calendar"

STALE = dt.timedelta(minutes=10)
ACCOUNT_SLOTS = 5  # .acct0 .. .acct4 in style.css; accounts past that reuse them
# Gap under the bar. Layer-shell margins count from the bar's exclusive zone,
# not the screen edge, so this is just the hang-off distance.
TOP_MARGIN = 8


# ---------- data ----------


def load_config():
    """(first weekday, accounts, error) from the credentials file. No file
    means no accounts and no error: the popup then says how to add them."""
    try:
        raw = tomllib.loads(CREDS.read_text())
    except FileNotFoundError:
        return 6, [], None
    except (OSError, tomllib.TOMLDecodeError) as e:
        return 6, [], f"Can't read {CREDS.name}: {e}"
    first = 0 if str(raw.get("week_start", "sunday")).lower() == "monday" else 6
    accounts = []
    for i, a in enumerate(raw.get("account", [])):
        urls = [u for u in a.get("urls", []) if isinstance(u, str) and u.startswith("http")]
        accounts.append(
            {
                "name": a.get("name") or a.get("email") or f"Account {i + 1}",
                "email": a.get("email", ""),
                "urls": urls,
                "slot": i % ACCOUNT_SLOTS,
            }
        )
    return first, accounts, None


def cache_file(url):
    return CACHE / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".ics")


def fetch(url):
    """Refresh one link's cache file. True if the feed changed."""
    req = urllib.request.Request(url, headers={"User-Agent": "auseklis-calendar"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
    if not body.lstrip().startswith(b"BEGIN:VCALENDAR"):
        raise ValueError("not an iCal feed")
    # Private calendar data: the directory and every file in it are owner-only
    # from the moment they exist.
    CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    CACHE.chmod(0o700)  # mkdir's mode only applies when it creates the directory
    path = cache_file(url)
    try:
        if path.read_bytes() == body:
            path.touch()  # unchanged, but it still counts as synced now
            return False
    except FileNotFoundError:
        pass
    tmp = path.with_suffix(".tmp")
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        f.write(body)
    tmp.replace(path)
    return True


def is_stale(url):
    try:
        age = dt.datetime.now().timestamp() - cache_file(url).stat().st_mtime
    except FileNotFoundError:
        return True
    return age > STALE.total_seconds()


def local(value):
    """A DTSTART/DTEND value as a local datetime, or a date for all-day."""
    if isinstance(value, dt.datetime):
        return value.astimezone() if value.tzinfo else value.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return value


def occurrence(ev, acct):
    """One expanded VEVENT as an agenda item, or None if it's cancelled."""
    if str(ev.get("STATUS", "")).upper() == "CANCELLED":
        return None
    s = local(ev.decoded("DTSTART"))
    all_day = not isinstance(s, dt.datetime)
    if "DTEND" in ev:
        e = local(ev.decoded("DTEND"))
    elif "DURATION" in ev:
        e = s + ev.decoded("DURATION")
    else:
        e = s + dt.timedelta(days=1) if all_day else s
    # Feeds occasionally pair a date start with a datetime end, or the reverse.
    if all_day and isinstance(e, dt.datetime):
        e = e.date()
    elif not all_day and not isinstance(e, dt.datetime):
        e = local(dt.datetime.combine(e, dt.time()))
    if all_day:
        first, last = s, (e - dt.timedelta(days=1) if e > s else s)
    else:
        first = s.date()
        last = (e - dt.timedelta(microseconds=1)).date() if e > s else first
    return {
        "title": str(ev.get("SUMMARY", "")).strip() or "(No title)",
        "uid": str(ev.get("UID", "")),
        "start": s,
        "end": e,
        "all_day": all_day,
        "acct": acct,
        "first": first,
        "last": last,
    }


class Store:
    """Parsed feeds plus their occurrences, expanded one grid window at a time."""

    def __init__(self, accounts):
        self.accounts = accounts
        self.calendars = []  # (account index, icalendar.Calendar)
        self.lock = threading.Lock()

    def parse(self):
        import icalendar

        parsed = []
        for i, acct in enumerate(self.accounts):
            for url in acct["urls"]:
                try:
                    parsed.append((i, icalendar.Calendar.from_ical(cache_file(url).read_bytes())))
                except (OSError, ValueError):
                    continue
        with self.lock:
            self.calendars = parsed

    def expand(self, start, end):
        """{date: [event]} for every day in [start, end)."""
        import recurring_ical_events

        with self.lock:
            calendars = list(self.calendars)
        days, seen = {}, set()
        for acct, calendar in calendars:
            try:
                occurrences = recurring_ical_events.of(calendar).between(start, end)
            except Exception:  # one malformed feed shouldn't blank the others
                continue
            for ev in occurrences:
                try:
                    item = occurrence(ev, acct)
                except Exception:  # one malformed event shouldn't blank the month
                    continue
                if item is None:
                    continue
                # The same invite on two linked accounts shows once.
                key = (item["uid"], str(item["start"]))
                if key in seen:
                    continue
                seen.add(key)
                d = max(item["first"], start)
                while d <= item["last"] and d < end and (d - item["first"]).days < 62:
                    days.setdefault(d, []).append(item)
                    d += dt.timedelta(days=1)
        return days


# ---------- UI ----------


def cursor_monitor():
    """The monitor under the pointer, i.e. the bar that was clicked. Left to
    the compositor, the popup lands on whichever output was last focused."""
    try:
        out = subprocess.run([HYPRCTL, "cursorpos"], capture_output=True, text=True, timeout=1).stdout
        x, y = (int(v) for v in out.split(","))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    for monitor in Gdk.Display.get_default().get_monitors():
        g = monitor.get_geometry()
        if g.x <= x < g.x + g.width and g.y <= y < g.y + g.height:
            return monitor
    return None


def label(text="", css=(), xalign=None, markup=False, **kw):
    w = Gtk.Label(**kw)
    (w.set_markup if markup else w.set_text)(text)
    for c in css:
        w.add_css_class(c)
    if xalign is not None:
        w.set_xalign(xalign)
    return w


class Popup(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.auseklis.Calendar", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.first_weekday, self.accounts, self.config_error = load_config()
        self.store = Store(self.accounts)
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.today = dt.date.today()
        self.view = self.today.replace(day=1)
        self.selected = self.today
        self.months = {}  # first-of-month -> {date: [event]}
        self.loaded = False
        self.failed = 0
        # Accounts hidden via the legend chips. Deliberately not persisted: a
        # stray click that stuck across opens would read as a broken sync.
        self.hidden = set()
        self.win = None
        self.last_scroll = 0

    # ----- window -----

    def do_activate(self):
        provider = Gtk.CssProvider()
        try:
            provider.load_from_path(str(STYLE))
        except GLib.Error:
            pass
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 100
        )

        # Click-away catchers: a transparent TOP-layer sheet per monitor, over
        # the bar too (exclusive zone -1), so clicking the clock again closes.
        for monitor in Gdk.Display.get_default().get_monitors():
            sheet = Gtk.Window(application=self, css_classes=["auseklis-calendar-dismiss"])
            LayerShell.init_for_window(sheet)
            LayerShell.set_namespace(sheet, "auseklis-calendar-dismiss")
            LayerShell.set_layer(sheet, LayerShell.Layer.TOP)
            LayerShell.set_monitor(sheet, monitor)
            LayerShell.set_exclusive_zone(sheet, -1)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(sheet, edge, True)
            LayerShell.set_keyboard_mode(sheet, LayerShell.KeyboardMode.NONE)
            sheet.set_child(Gtk.Box(hexpand=True, vexpand=True))
            click = Gtk.GestureClick(button=0)
            click.connect("pressed", lambda *_: self.quit())
            sheet.add_controller(click)
            sheet.present()

        win = Gtk.ApplicationWindow(application=self)
        win.add_css_class("auseklis-calendar")
        LayerShell.init_for_window(win)
        LayerShell.set_namespace(win, "auseklis-calendar")
        LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
        if (monitor := cursor_monitor()) is not None:
            LayerShell.set_monitor(win, monitor)
        LayerShell.set_anchor(win, LayerShell.Edge.TOP, True)
        LayerShell.set_margin(win, LayerShell.Edge.TOP, TOP_MARGIN)
        # Exclusive: the sheets make the popup modal anyway, and it keeps Esc
        # working wherever the pointer drifts.
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.EXCLUSIVE)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        win.add_controller(keys)

        pop = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, css_classes=["pop"])
        pop.append(self.build_month())
        pop.append(self.build_agenda())
        win.set_child(pop)
        self.win = win

        self.render_grid()
        self.render_agenda()
        self.render_legend()
        win.present()

        if self.accounts:
            self.pool.submit(self.load)

    def on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.quit()
        elif keyval == Gdk.KEY_Left:
            self.shift(-1)
        elif keyval == Gdk.KEY_Right:
            self.shift(1)
        else:
            return False
        return True

    # ----- month side -----

    def build_month(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["cal"])

        head = Gtk.Box(css_classes=["head"])
        self.title = label(css=["title"], xalign=0, hexpand=True, markup=True)
        head.append(self.title)
        today = Gtk.Button(label="Today", css_classes=["today-btn"])
        today.connect("clicked", lambda *_: self.go_today())
        head.append(today)
        for glyph, step, tip in (("‹", -1, "Previous month"), ("›", 1, "Next month")):
            b = Gtk.Button(label=glyph, css_classes=["icon"], tooltip_text=tip)
            b.connect("clicked", lambda _b, s=step: self.shift(s))
            head.append(b)
        box.append(head)

        self.stack = Gtk.Stack(
            transition_duration=200, vhomogeneous=True, hhomogeneous=True, css_classes=["grid-stack"]
        )
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.on_scroll)
        self.stack.add_controller(scroll)
        box.append(self.stack)

        self.legend = Gtk.Box(css_classes=["legend"])
        box.append(self.legend)
        return box

    def on_scroll(self, _ctl, _dx, dy):
        now = GLib.get_monotonic_time()
        if now - self.last_scroll > 220_000 and dy:
            self.last_scroll = now
            self.shift(1 if dy > 0 else -1)
        return True

    def grid_window(self, first_of_month):
        """First and one-past-last day of the six-week grid for a month."""
        lead = (first_of_month.weekday() - self.first_weekday) % 7
        start = first_of_month - dt.timedelta(days=lead)
        return start, start + dt.timedelta(days=42)

    def render_grid(self, direction=0):
        view = self.view
        self.title.set_markup(
            f"{view.strftime('%B')} <span alpha='60%' weight='normal'>{view.year}</span>"
        )
        grid = Gtk.Grid(css_classes=["grid"], row_spacing=2, column_spacing=2)
        names = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
        for col in range(7):
            grid.attach(label(names[(self.first_weekday + col) % 7], css=["dow"]), col, 0, 1, 1)
        grid.attach(label("wk", css=["dow"]), 7, 0, 1, 1)

        start, _ = self.grid_window(view)
        events = self.months.get(view, {})
        for row in range(6):
            for col in range(7):
                day = start + dt.timedelta(days=row * 7 + col)
                grid.attach(self.day_cell(day, events.get(day, [])), col, row + 1, 1, 1)
            mid = start + dt.timedelta(days=row * 7 + 3)
            grid.attach(label(str(mid.isocalendar().week), css=["wk"]), 7, row + 1, 1, 1)

        name = f"{view.isoformat()}-{GLib.get_monotonic_time()}"
        old = self.stack.get_visible_child()
        self.stack.add_named(grid, name)
        self.stack.set_transition_type(
            Gtk.StackTransitionType.SLIDE_LEFT
            if direction > 0
            else Gtk.StackTransitionType.SLIDE_RIGHT
            if direction < 0
            else Gtk.StackTransitionType.NONE
        )
        self.stack.set_visible_child_name(name)
        if old is not None:
            GLib.timeout_add(260, lambda: (self.stack.remove(old), False)[1])

        if self.loaded and view not in self.months:
            self.pool.submit(self.expand_month, view)

    def visible(self, events):
        return [e for e in events if self.accounts[e["acct"]]["name"] not in self.hidden]

    def day_cell(self, day, events):
        b = Gtk.Button(css_classes=["day"])
        if day.month != self.view.month:
            b.add_css_class("out")
        if day == self.today:
            b.add_css_class("today")
        if day == self.selected:
            b.add_css_class("sel")
        inner = Gtk.Overlay()
        inner.set_child(label(str(day.day), css=["num"]))
        slots = sorted({self.accounts[e["acct"]]["slot"] for e in self.visible(events)})[:3]
        if slots:
            dots = Gtk.Box(css_classes=["dots"], halign=Gtk.Align.CENTER, valign=Gtk.Align.END)
            for s in slots:
                dots.append(Gtk.Box(css_classes=["dot", f"acct{s}"]))
            inner.add_overlay(dots)
        b.set_child(inner)
        b.connect("clicked", lambda *_: self.select(day))
        return b

    def render_legend(self):
        while (c := self.legend.get_first_child()) is not None:
            self.legend.remove(c)
        for acct in self.accounts:
            chip = Gtk.Button(css_classes=["chip"], tooltip_text="Show or hide this account")
            if acct["name"] in self.hidden:
                chip.add_css_class("off")
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Box(css_classes=["dot", f"acct{acct['slot']}"], valign=Gtk.Align.CENTER))
            row.append(label(acct["name"]))
            chip.set_child(row)
            chip.connect("clicked", lambda _b, n=acct["name"]: self.toggle_account(n))
            self.legend.append(chip)

    # ----- agenda side -----

    def build_agenda(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["agenda"])
        self.ag_head = label(css=["ag-head"], xalign=0, markup=True)
        box.append(self.ag_head)
        sw = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            max_content_height=420,
            vexpand=True,
        )
        self.evs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["evs"])
        sw.set_child(self.evs)
        box.append(sw)

        foot = Gtk.Box(css_classes=["foot"])
        self.status = label(css=["status"], xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        foot.append(self.status)
        open_btn = Gtk.Button(label="Open in Google ↗", css_classes=["link"])
        open_btn.connect("clicked", lambda *_: self.open_google(self.selected, None))
        foot.append(open_btn)
        box.append(foot)
        return box

    def render_agenda(self):
        sel = self.selected
        when = f"{sel.strftime('%A')} · {sel.strftime('%b')} {sel.day}"
        self.ag_head.set_markup(f"<b>Today</b> · {when}" if sel == self.today else f"<b>{when}</b>")
        while (c := self.evs.get_first_child()) is not None:
            self.evs.remove(c)

        if self.config_error or not self.accounts:
            msg = self.config_error or f"No calendars linked yet. Add them to ~/.config/credentials/{CREDS.name}"
            self.evs.append(label(msg, css=["empty"], xalign=0, wrap=True, max_width_chars=34))
        elif not self.loaded:
            self.evs.append(label("Loading…", css=["empty"], xalign=0))
        else:
            month = sel.replace(day=1)
            # The selected day can sit in a neighbouring month's grid edge.
            events = self.months.get(self.view, {}).get(sel) or self.months.get(month, {}).get(sel, [])
            events = sorted(
                self.visible(events),
                key=lambda e: (not e["all_day"], e["start"] if not e["all_day"] else dt.datetime.min.time()),
            )
            if not events:
                self.evs.append(label("Nothing scheduled", css=["empty"], xalign=0))
            now = dt.datetime.now().astimezone()
            for e in events:
                self.evs.append(self.event_row(e, sel, now))
        self.render_status()

    def event_row(self, e, day, now):
        if e["all_day"]:
            when = "All day"
        elif e["start"].date() < day:
            when = "Cont."
        else:
            when = e["start"].strftime("%H:%M")
        acct = self.accounts[e["acct"]]
        b = Gtk.Button(css_classes=["ev"])
        if not e["all_day"] and day == self.today and e["start"] <= now < e["end"]:
            b.add_css_class("now")
        row = Gtk.Box(spacing=10)
        row.append(label(when, css=["time"], xalign=0, valign=Gtk.Align.START, width_chars=7))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.append(label(e["title"], css=["t"], xalign=0, wrap=True, max_width_chars=30))
        meta = Gtk.Box(spacing=6, css_classes=["m"])
        meta.append(Gtk.Box(css_classes=["dot", f"acct{acct['slot']}"], valign=Gtk.Align.CENTER))
        meta.append(label(acct["name"], xalign=0))
        text.append(meta)
        row.append(text)
        b.set_child(row)
        b.connect("clicked", lambda *_: self.open_google(day, acct))
        return b

    def render_status(self):
        if not self.accounts:
            self.status.set_text("")
            return
        if self.failed:
            self.status.set_text(f"Couldn't reach {self.failed} calendar{'s' if self.failed > 1 else ''}")
            return
        mtimes = [cache_file(u).stat().st_mtime for a in self.accounts for u in a["urls"] if cache_file(u).exists()]
        if not mtimes:
            self.status.set_text("Syncing…")
            return
        mins = int((dt.datetime.now().timestamp() - min(mtimes)) // 60)
        self.status.set_text("Synced just now" if mins < 1 else f"Synced {mins} min ago")

    # ----- actions -----

    def shift(self, step):
        y, m = divmod(self.view.year * 12 + self.view.month - 1 + step, 12)
        self.view = dt.date(y, m + 1, 1)
        self.render_grid(step)

    def go_today(self):
        direction = (self.today.replace(day=1) > self.view) - (self.today.replace(day=1) < self.view)
        self.view, self.selected = self.today.replace(day=1), self.today
        self.render_grid(direction)
        self.render_agenda()

    def select(self, day):
        self.selected = day
        target = day.replace(day=1)
        direction = (target > self.view) - (target < self.view)
        self.view = target
        self.render_grid(direction)
        self.render_agenda()

    def toggle_account(self, name):
        self.hidden ^= {name}
        self.render_legend()
        self.render_grid()
        self.render_agenda()

    def open_google(self, day, acct):
        url = f"https://calendar.google.com/calendar/r/day/{day.year}/{day.month}/{day.day}"
        email = (acct or (self.accounts[0] if self.accounts else {})).get("email")
        if email:
            url += f"?authuser={email}"
        # GI_TYPELIB_PATH points at this popup's GTK4; a GTK3 browser shouldn't see it.
        ctx = Gdk.Display.get_default().get_app_launch_context()
        ctx.unsetenv("GI_TYPELIB_PATH")
        Gio.AppInfo.launch_default_for_uri(url, ctx)
        self.quit()

    # ----- background work (pool thread; UI only via idle_add) -----

    # The pool swallows exceptions, so every path here ends in an idle_add:
    # a bad feed must still clear "Loading…".

    def load(self):
        try:
            self.store.parse()
        except Exception:
            pass
        self.expand_month(self.view, mark_loaded=True)
        failed, changed = 0, False
        for acct in self.accounts:
            for url in acct["urls"]:
                if not is_stale(url):
                    continue
                try:
                    changed |= fetch(url)
                except Exception:
                    failed += 1
        if changed:
            try:
                self.store.parse()
            except Exception:
                pass
        GLib.idle_add(self.apply_sync, failed, changed)

    def apply_sync(self, failed, changed):
        self.failed = failed
        if changed:
            self.months.clear()
            self.pool.submit(self.expand_month, self.view)
        self.refresh()
        return False

    def expand_month(self, month, mark_loaded=False):
        start, end = self.grid_window(month)
        try:
            days = self.store.expand(start, end)
        except Exception:
            days = {}
        GLib.idle_add(self.apply_month, month, days, mark_loaded)

    def apply_month(self, month, days, mark_loaded):
        self.months[month] = days
        if mark_loaded:
            self.loaded = True
        self.refresh()
        return False

    def refresh(self):
        if self.win is None:
            return False
        self.render_grid()
        self.render_agenda()
        return False


def main():
    app = Popup()
    # The wrapper closes a running popup with SIGTERM; let GTK exit cleanly so
    # Hyprland plays the layer's exit animation.
    try:
        from gi.repository import GLibUnix

        signal_add = GLibUnix.signal_add
    except ImportError:
        signal_add = GLib.unix_signal_add
    signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (app.quit(), False)[1])
    app.run(None)
    os._exit(0)  # don't wait on an in-flight fetch


if __name__ == "__main__":
    main()
