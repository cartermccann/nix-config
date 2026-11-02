"""The manual lists real shortcuts.

Runs the generated Pond Hyprland config (hypr/hyprland-pond.lua) under Lua with
a stub `hl` that records every bind, then checks each row of
shell/settings/manual/shortcuts.js against it: every listed key combination is
bound at top level and its command or description contains the row's
`expect`. Needs
  LUA               a Lua 5.5 interpreter
  POND_LUA_CONFIG   the generated hyprland-pond.lua, e.g. from
                    nix eval --raw '.#nixosConfigurations.perkons.config.home-manager.users.cjm.xdg.configFile."hypr/hyprland-pond.lua".text'
and is skipped without them.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from fakes import ROOT

STUB = r"""
local binds = {}
local submap = nil
local function value(v)
  if type(v) == "table" then return v end
  return { name = tostring(v) }
end
local function dsp_proxy(path)
  return setmetatable({}, {
    __index = function(_, key) return dsp_proxy(path == "" and key or path .. "." .. key) end,
    __call = function(_, arg)
      if path == "exec_cmd" then return { cmd = arg } end
      return { dsp = path, arg = arg }
    end,
  })
end
local function noop() return setmetatable({}, { __call = function() return {} end, __index = function() return noop() end }) end
hl = setmetatable({
  dsp = dsp_proxy(""),
  bind = function(keys, action, opts)
    opts = opts or {}
    binds[#binds + 1] = { keys = keys, cmd = (type(action) == "table" and action.cmd) or "",
                          description = opts.description or "", submap = submap or "" }
  end,
  define_submap = function(name, fn) local prev = submap; submap = name; fn(); submap = prev end,
}, { __index = function() return noop() end })
local chunk = assert(loadfile(arg[1]))
chunk()
local out = {}
for _, b in ipairs(binds) do
  out[#out + 1] = string.format("%s\t%s\t%s\t%s", b.submap, b.keys, b.cmd:gsub("[\t\n]", " "), b.description)
end
print(table.concat(out, "\n"))
"""


def manual_sections():
    source = (ROOT / "shell/settings/manual/shortcuts.js").read_text()
    body = source[source.index("var sections = ") + len("var sections = "):].strip().rstrip(";")
    return json.loads(body)


@unittest.skipUnless(os.environ.get("LUA") and os.environ.get("POND_LUA_CONFIG"),
                     "needs LUA and POND_LUA_CONFIG")
class ManualMatchesBinds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as stub:
            stub.write(STUB)
        try:
            out = subprocess.run([os.environ["LUA"], stub.name, os.environ["POND_LUA_CONFIG"]],
                                 capture_output=True, text=True, check=True, timeout=30).stdout
        finally:
            os.unlink(stub.name)
        cls.binds = {}
        for line in out.splitlines():
            submap, keys, cmd, description = (line.split("\t") + ["", "", "", ""])[:4]
            if not submap:
                cls.binds[keys] = (cmd, description)

    def test_every_listed_shortcut_is_bound(self):
        rows = [row for section in manual_sections() for row in section["rows"]]
        self.assertGreater(len(rows), 20)
        for row in rows:
            for keys in row["binds"]:
                with self.subTest(label=row["label"], keys=keys):
                    self.assertIn(keys, self.binds, f"{row['label']}: {keys} is not bound")
                    cmd, description = self.binds[keys]
                    self.assertTrue(row["expect"] in cmd or row["expect"] in description,
                                    f"{keys} runs {cmd!r} ({description!r}), not {row['expect']!r}")

    def test_numbers_follow_the_sidebar(self):
        for n in range(1, 11):
            key = "0" if n == 10 else str(n)
            self.assertEqual(self.binds[f"SUPER + {key}"][0], f"pond-shell ipc call workspaces focus {n}")
            self.assertEqual(self.binds[f"SUPER + SHIFT + {key}"][0], f"pond-shell ipc call workspaces moveWindow {n}")

    def test_no_niri_or_app_store_claims(self):
        text = (ROOT / "shell/settings/manual/shortcuts.js").read_text().lower()
        for claim in ("scroll", "niri", "install the apps", "column"):
            self.assertNotIn(claim, text)


if __name__ == "__main__":
    unittest.main()
