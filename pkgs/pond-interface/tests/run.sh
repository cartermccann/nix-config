#!/usr/bin/env bash
# Runs the Hyprland adapter/settings tests. Nothing touches the live session:
# Hyprland, compositor, Nix and the lock screen are fakes.
#   PY   python with PyQt6 for the system-settings suite (else it is skipped),
#        e.g. the package's passthru.pythonEnv
#   LUA  Lua 5.5 for executing generated Hyprland Lua (else skipped),
#        e.g. $(nix build --print-out-paths nixpkgs#lua5_5)/bin/lua
#   QS   packaged Pond qs wrapper for real offscreen QML tests
#   POND_LUA_CONFIG generated hyprland-pond.lua for manual/binding checks
set -euo pipefail
cd "$(dirname "$0")"
export POND_XKB_ROOT="$PWD/fixtures/xkb"
"${PY:-python3}" -m unittest discover -s . -p 'test_*.py' "$@"
node --test ./*.test.mjs
