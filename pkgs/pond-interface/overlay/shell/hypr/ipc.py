"""Hyprland 0.56 IPC for Pond's helpers.

Requests go straight to Hyprland's request socket (the protocol hyprctl uses:
"<flags>/<command>", reply until EOF). With the Lua config manager, actions are
Lua: every compound action runs as one `repl` request, so it executes
atomically on the compositor thread, raises on the first failed dispatcher and
returns a marker only when everything succeeded. Values reach Lua solely
through `lua_string`/validated numbers; nothing user-supplied is spliced in raw.
"""
import json
import os
from pathlib import Path
import re
import socket

OK = "POND_OK"
ADDRESS = re.compile(r"^0x[0-9a-f]{1,16}$")
OUTPUT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")

# Shared by every compound action. A dispatcher reports failure through its
# result table; argument errors return no table at all. Both must stop the
# action instead of being reported as success.
PRELUDE = (
    'local function D(d) if d == nil then error("invalid dispatcher", 0) end '
    'local r = hl.dispatch(d) '
    'if type(r) ~= "table" or r.ok ~= true then error((type(r) == "table" and r.error) or "dispatcher failed", 0) end end '
    'local function W(a) local w = hl.get_window(a) '
    'if w == nil then error("window " .. a .. " no longer exists", 0) end return w end\n'
)


class HyprError(RuntimeError):
    pass


def socket_dir(env=None):
    env = os.environ if env is None else env
    signature = env.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    if not signature or "/" in signature:
        raise HyprError("Hyprland is not running (HYPRLAND_INSTANCE_SIGNATURE is unset)")
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "hypr" / signature


def request(command, json_output=False, timeout=5.0, env=None):
    payload = (("j" if json_output else "") + "/" + command).encode()
    if b"\0" in payload:
        raise HyprError("Invalid Hyprland request")
    path = socket_dir(env) / ".socket.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        try:
            connection.connect(str(path))
            connection.sendall(payload)
            # Hyprland reads until a short read; half-closing ends a request
            # whose length is an exact multiple of its 1023-byte buffer.
            connection.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        except socket.timeout as error:
            raise HyprError("Hyprland did not answer in time") from error
        except OSError as error:
            raise HyprError(f"Cannot reach Hyprland: {error.strerror or error}") from error
    return b"".join(chunks).decode("utf-8", "replace")


def query(command, env=None):
    text = request(command, json_output=True, env=env)
    try:
        return json.loads(text)
    except ValueError as error:
        raise HyprError(f"Hyprland returned an unreadable reply to {command.split()[0]}: {text.strip()[:200]}") from error


def lua_string(value):
    value = str(value)
    if "\0" in value:
        raise ValueError("Text contains a NUL byte")
    out = ['"']
    for char in value:
        code = ord(char)
        if char in '"\\':
            out.append("\\" + char)
        elif code < 32 or code == 127:
            out.append(f"\\{code:03d}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def lua_number(value):
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("Number is not finite")
    return str(int(number)) if number.is_integer() else repr(number)


def lua_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return lua_number(value)
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"Invalid Lua key {key!r}")
            parts.append(f"{key} = {lua_value(item)}")
        return "{ " + ", ".join(parts) + " }"
    raise ValueError(f"Unsupported Lua value {value!r}")


def run_lua(body, env=None, timeout=5.0):
    """Run statements that raise on failure; succeed only on the marker."""
    reply = request("repl " + PRELUDE + body + f"\nreturn {lua_string(OK)}", timeout=timeout, env=env).strip()
    if reply == OK:
        return
    message = reply
    if message.startswith("error:"):
        message = message[len("error:"):].strip()
    message = re.sub(r'^(\[string "[^"]*"\]|=\[C\]):-?\d+:\s*', "", message)
    raise HyprError(message or "Hyprland rejected the request")


def window_selector(address):
    address = str(address).lower()
    if not ADDRESS.fullmatch(address):
        raise ValueError("Invalid window address")
    return "address:" + address


def output_name(name):
    name = str(name)
    if not OUTPUT.fullmatch(name):
        raise ValueError("Invalid output name")
    return name


def workspace_id(value):
    if isinstance(value, bool):
        raise ValueError("Invalid workspace")
    number = int(value)
    if number != float(value) or abs(number) > 2**31 - 1:
        raise ValueError("Invalid workspace")
    return number


def normalize_address(address):
    address = str(address or "").lower()
    if not address.startswith("0x"):
        address = "0x" + address
    return address
