"""A stand-in for systemd-logind on a private D-Bus, for test-pond-session.sh.

    fake-login1.py <control-fifo> <event-log> <session-path>

Owns org.freedesktop.login1 on the bus named by DBUS_SYSTEM_BUS_ADDRESS and
implements the three calls pond-session's guard makes: GetSession,
GetSessionByPID (both answer <session-path>) and Inhibit, which hands out the
write end of a pipe and logs when every copy of it has been closed, which is
exactly when real logind would drop the inhibitor. Lines written to the control
fifo emit the signals real logind would:

    sleep true|false     Manager.PrepareForSleep
    lock                 Session.Lock on <session-path>
    lock-other           Session.Lock on another session's path

Events go to <event-log>, one line each: owned, inhibit <what> <mode>, released.
"""
import os
import sys

from gi.repository import Gio, GLib

IFACE = """
<node>
  <interface name="org.freedesktop.login1.Manager">
    <method name="GetSession"><arg type="s" direction="in"/><arg type="o" direction="out"/></method>
    <method name="GetSessionByPID"><arg type="u" direction="in"/><arg type="o" direction="out"/></method>
    <method name="Inhibit">
      <arg type="s" direction="in"/><arg type="s" direction="in"/>
      <arg type="s" direction="in"/><arg type="s" direction="in"/>
      <arg type="h" direction="out"/>
    </method>
    <signal name="PrepareForSleep"><arg type="b"/></signal>
  </interface>
</node>
"""

control, log_path, session_path = sys.argv[1:4]
bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)


def log(line):
    with open(log_path, "a") as handle:
        handle.write(line + "\n")


def watch_release(fd):
    def closed(_fd, _cond):
        log("released")
        os.close(fd)
        return False
    GLib.io_add_watch(fd, GLib.IO_HUP | GLib.IO_IN, closed)


def method_call(conn, sender, path, iface, method, params, invocation):
    if method in ("GetSession", "GetSessionByPID"):
        invocation.return_value(GLib.Variant("(o)", (session_path,)))
    elif method == "Inhibit":
        what, _who, _why, mode = params.unpack()
        read_end, write_end = os.pipe()
        watch_release(read_end)
        fds = Gio.UnixFDList.new_from_array([write_end])
        log(f"inhibit {what} {mode}")
        invocation.return_value_with_unix_fd_list(GLib.Variant("(h)", (0,)), fds)
    else:
        invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)


node = Gio.DBusNodeInfo.new_for_xml(IFACE)
bus.register_object("/org/freedesktop/login1", node.interfaces[0], method_call, None, None)


def emit(path, iface, name, args):
    bus.emit_signal(None, path, iface, name, args)


def command(channel, _cond):
    line = channel.readline()
    if not line:
        return True
    words = line.split()
    if words[:1] == ["sleep"]:
        emit("/org/freedesktop/login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
             GLib.Variant("(b)", (words[1] == "true",)))
    elif words == ["lock"]:
        emit(session_path, "org.freedesktop.login1.Session", "Lock", None)
    elif words == ["lock-other"]:
        emit("/org/freedesktop/login1/session/_399", "org.freedesktop.login1.Session", "Lock", None)
    return True


fd = os.open(control, os.O_RDWR)  # RDWR: never sees EOF between writers
GLib.io_add_watch(GLib.IOChannel.unix_new(fd), GLib.IO_IN, command)
Gio.bus_own_name_on_connection(bus, "org.freedesktop.login1", 0, lambda *_: log("owned"), None)
GLib.MainLoop().run()
