# pond-interface

Nix package for [bflycomputer/pond-interface](https://github.com/bflycomputer/pond-interface),
exposed as `pkgs.pond-interface` by `lib/overlays.nix`. Upstream pin: `./source.nix`.

## Outputs

| Path | Contents |
|------|----------|
| `bin/pond-shell` | `qs -p <out>/share/pond-interface/shell "$@"` |
| `bin/pond-lock` | `qs -p <out>/share/pond-interface/lockscreen "$@"` |
| `libexec/pond-interface/bin/qs` | Quickshell with Pond's environment; `quickshell` links to it |
| `libexec/pond-interface/bin/pond-python3` | Python with PyQt6 (QtCore only) and PyGObject, used by every helper |
| `lib/qt-6/qml/Pond/{System,Screenshot,Screenshot/Native,Wallpaper,Daylight}` | QML modules and native plugins |
| `share/pond-interface/{shell,lockscreen,config}` | Shell and locker QML; fastfetch, GTK and GSettings defaults |
| `share/applications/pond-manual.desktop` | Opens the manual through `pond-shell ipc call manual open` |
| `share/wallpapers/pond/pond-dusk.png` | Upstream lock-screen dusk artwork, with `ATTRIBUTION` |

Both wrappers forward every argument, so IPC and daemon mode work as upstream:
`pond-shell ipc call settings open display`, `pond-lock -d`,
`pond-lock ipc call lockscreen isSecure`. The port routes manual, idle, settings and logind lock requests through
`pond-session lock`. Its separate supervised guard owns lock-before-sleep and
resume recovery; the shell does not start a competing locker or inhibitor.
The session helper checks the exact locker PID for compositor-confirmed
`isSecure`, with bounded IPC and serialized requests. Fallback readiness uses
hyprlock's compositor-confirmation log event. Unknown or dead lock owners are
never taken over automatically; see `docs/pond-hyprland.md` for recovery limits.

The wrapper environment, identical for both entry points:

- `PATH` prefix: the libexec directory (so upstream's `qs` calls use this
  Quickshell), then `nmcli`, `iw`, `getent`, `systemctl`, `fastfetch`,
  `brightnessctl`, `ddcutil`, `pactl`, `wl-copy`/`wl-paste`, `zenity`,
  `vicinae`, coreutils, grep, sed, findutils, bash, and `extraRuntimeInputs`.
  Python is not put on `PATH`; helpers call `pond-python3` by absolute path so
  terminals launched from the shell keep the user's `python3`.
- `NIXPKGS_QT6_QML_IMPORT_PATH` prefix: the Pond QML modules.
- `QT_PLUGIN_PATH` prefix: `qtimageformats` (WebP/TIFF wallpapers).
- `FONTCONFIG_FILE`: Pond's fonts, then `/etc/fonts/fonts.conf` (user config
  still applies through it).
- `POND_PAM_SERVICE` defaults to `pond-lock` (upstream's own variable).
- `POND_INTERFACE_DATA`: `<out>/share/pond-interface`, for adapter code.

Hard-coded `/usr/bin` and `/usr/share` paths are rewritten to store paths, and
the install check fails the build if any remain in installed QML, JS, Python,
desktop or JSON files, or if a helper still runs a bare `python3`.

## Qt and Quickshell

Everything is built from this flake's stable nixpkgs: Qt 6.11.2,
KF6 NetworkManagerQt 6.26.0, and one Qt closure for the shell and Pond's
plugins. Pond's own packages require `quickshell >= 0.3.1` (session-lock
crash fixes for sleep, wake, DPMS and unlock), while stable ships 0.3.0. The
package therefore builds Quickshell 0.3.2 from source against the same Qt
(`passthru.quickshell`); 0.3.2 also adds Hyprland's new workspace address
format. When nixpkgs' `quickshell` reaches 0.3.2, it is used unchanged.
This Quickshell is private to the wrappers and does not replace `pkgs.quickshell`
(Caelestia).

Shaders (`wallpaper/daylight/sky.frag`, `lockscreen/frontend/shaders/soft-light.frag`,
and any `.frag`/`.vert` the overlay adds) are recompiled with this Qt's `qsb`
(GLSL 100 es/120/150, HLSL 50, MSL 12, as upstream).

## Sidebar controls

`overlay/shell/Sidebar.qml` integrates `controls/` with upstream Card/CardStack
components. Four custom popouts share those primitives; Bluetooth reuses the
upstream panel. `controls/State.qml` is a singleton across outputs: one telemetry
watcher, one dictation action at a time and one stdin-lifetime logind idle
inhibitor. Only confirmed inhibition suppresses the automatic idle monitors in
`shell.qml`; the independent session lock guard is unchanged.

`controls/control.py` reads `/proc`, disk usage and bounded `nvidia-smi` output.
It never changes settings. `watch` exits on stdin EOF; `hold`, launched beneath
`systemd-inhibit --what=idle`, exits on a stop line or EOF. GPU telemetry and btop
come from the host PATH; dictation scripts come from `~/.local/bin`. Pinned app
updates use the existing `auseklis-updates` cache/user service. Missing services
and hardware show unavailable/error states.

`tests/test_controls.py` covers telemetry edge cases, inhibitor lifetime and
real offscreen QML presentation/actions (`QS` as below). The sandbox smoke opens
all five popouts in expanded and compact modes and saves screenshots; it never
starts recording, inhibition, pairing or an update.

Window icons prefer an explicit desktop-entry icon. If an Electron-generated
hidden protocol handler has no icon, `NiriMsg.qml` falls back to a visible entry
with the same `StartupWMClass` (notably T3 Code). Desktop-entry changes invalidate
the cache; incomplete matches are not cached. `tests/test_icons.py` reproduces
the hidden-handler override with real Quickshell lookup in a temporary data dir.

## Adapter overlay

`./overlay/` (owned by the Hyprland adapter port, with its tests in `./tests/`)
mirrors the upstream tree. In `postPatch`, before anything else, it is copied
over the source recursively; new and replaced files are both picked up without
being listed here. Then the native build, shader compilation and path
rewriting run over the combined tree. If `./overlay/` is absent, the unmodified
upstream shell is built.

Overlay files go through the same rewriting and checks: use `/usr/bin/...` or
`"python3"` as upstream does, or `pond-python3`/`$POND_INTERFACE_DATA`
directly. A command not in the wrapper `PATH` must come from the session
(compositor CLIs) or be added through `extraRuntimeInputs`.

The overlay replaces Pond's Arch updater with `shell/updates/nix.py`. `check`
resolves inputs into a temporary lock file without changing the system flake.
`run STATUS_FILE` opens the interactive check/build/switch workflow in Ghostty;
writing revisions and authenticated switching each require user confirmation.
`reconcile STATUS_FILE` repairs abandoned status after interruption or shell
restart. Transaction locks and backups live under
`$XDG_STATE_HOME/pond-interface/updates/` (normally `~/.local/state/…`). Concurrent
lock-file edits prevent restoration/switching rather than being overwritten.
Build/switch guardians keep the transaction mutex held if the terminal-facing
updater is killed. A declined or failed switch remains actionable; retrying
rebuilds and offers switching the current flake even when no newer inputs exist.
The original upstream-only package still warns if `pm` remains referenced.

## Fonts

`passthru.fonts` is the list of font packages (also available to Home Manager
`home.packages` or `fonts.packages` so other applications can use them);
`passthru.fontsConf` is the wrapper's fontconfig file.

| Family | Source | License |
|--------|--------|---------|
| Onest | google/fonts `ofl/onest` at the revision nixpkgs' google-fonts pins | OFL-1.1 |
| Advent Pro (clock) | google/fonts `ofl/adventpro`, same revision | OFL-1.1 |
| IBM Plex Mono | nixpkgs `ibm-plex` (`families = [ "mono" ]`) | OFL-1.1 |
| JetBrains Mono (setting pickers) | nixpkgs `jetbrains-mono` | OFL-1.1 |
| Fastfetch Flowers | upstream `config/fastfetch/fastfetch-flowers.ttf` | BSD-3-Clause |
| Pond Gramercy (titles, lock screen) | **not available**, see below | proprietary |

**Pond Gramercy is missing.** Upstream states it is proprietary and not
distributed (bflycomputer/iso-installer README: "Pond Gramercy is proprietary
and is not included in this repository"). It is not in pond-interface, in any
package of Pond's pacman repository (`repo.butterfly.so`, checked 2026-11-01),
in nixpkgs, or installed locally. Temporary fallback: fontconfig substitutes
**Onest** for "Pond Gramercy" (`<accept>` rule, so a real Gramercy installed
anywhere still wins). Upstream's `font.styleName: "Book"` has no Onest
equivalent and falls back to Onest's default style. To use the real font:

```nix
pkgs.pond-interface.override { gramercyFont = <package with share/fonts/...>; }
```

`gramercyFallbackFamily` changes the fallback; `passthru.gramercyAvailable`
reports which case was built.

## Artwork and wallpapers

- Live background: `Pond.Daylight` (procedural sky shader) via `Pond.Wallpaper`.
- Lock screen: `lockscreen/frontend/assets/moon.png` and its SVG glyphs, as upstream.
- `share/wallpapers/pond/pond-dusk.png`: upstream's 1920x1080
  `lock-background.png`, shipped by Pond's lockscreen package but no longer
  referenced by its QML.
- No separately distributed wallpaper collection exists: Pond's
  `pond-wallpaper` package contains only the QML modules, and the appearance
  picker offers the dynamic sky or a user-chosen image. GRUB, Plymouth and
  installer illustrations are not wallpapers and are not installed.
- Not installed: the SDDM/niri greeter (`lockscreen/greeter`), GRUB and
  Plymouth themes, kitty and niri configuration.

## Host integration requirements

- `security.pam.services.pond-lock = { };` (otherwise set `POND_PAM_SERVICE`).
- Compositor CLIs (`hyprctl`) and launch targets come from the session `PATH`.
- System services used over D-Bus: NetworkManager, BlueZ, UPower,
  power-profiles-daemon, logind, PipeWire with its PulseAudio server (`pactl`).
- DDC brightness needs `/dev/i2c-*` access (`hardware.i2c.enable` and the `i2c`
  group); backlight writes need brightnessctl's udev rules.
- GdkPixbuf loaders for WebP/AVIF wallpaper validation come from the session's
  `GDK_PIXBUF_MODULE_FILE`.
- An icon theme (Adwaita/hicolor) on `XDG_DATA_DIRS` for application icons.
- `vicinae` on the wrapper `PATH` is nixpkgs' (not Pond's fork); a Vicinae
  server started by the session should use the same `pkgs.vicinae`.
- The timezone setter uses polkit when the setting is mutable. When NixOS owns
  the timezone, the panel explains that it must be changed in the configuration.

## Checks

`tests/run.sh` discovers all Python regression suites and JavaScript model tests.
For a complete run, set `PY` to the package's `pythonEnv` interpreter, `LUA` to
Lua 5.5, `POND_LUA_CONFIG` to the generated `hyprland-pond.lua`, and `QS` to the built package's
`libexec/pond-interface/bin/qs` wrapper. The wrapper is required for the native
Pond QML imports used by the two-sidebar tests. Without these dependencies some
suites skip; a skipped run is not the final verification gate. QML harnesses use
offscreen rendering and fake helpers, never the live compositor. The minimap
overlay includes a shared `DragCoordinator.qml` and `DragModel.js` in addition
to the modified upstream `Map.qml` and `DragSession.qml`.

- Build (install check runs automatically): path rewrites, missing modules,
  `ldd` of both plugins, Python imports (`Gio`, `GLib`, `GdkPixbuf`,
  `PyQt6.QtCore`), byte-compiling every helper, Quickshell version.
- `passthru.tests.smoke`: inside the build sandbox, a headless sway (pixman,
  no GPU), private D-Bus session and temporary runtime directory. Checks the
  font families resolve (and the Gramercy fallback), starts `pond-lock` with
  `POND_LOCK_NO_START=1` and `pond-shell`, requires "Configuration Loaded",
  checks IPC targets through both wrappers, opens every settings page, and
  fails on QML import/type/plugin errors, Python import errors, or commands
  missing from `PATH` (except `niri`, `hyprctl` and `pm`). Screenshots and
  logs are kept in the output. Rendering uses Qt's software backend, so it
  does not exercise the shaders on a GPU.

Standalone build from a checkout (untracked files included):

```sh
nix build --impure --expr 'let p = (builtins.getFlake (toString ~/nix-config)).inputs.nixpkgs.legacyPackages.x86_64-linux; in p.callPackage ~/nix-config/pkgs/pond-interface { }'
nix build --impure --expr 'let p = (builtins.getFlake (toString ~/nix-config)).inputs.nixpkgs.legacyPackages.x86_64-linux; in (p.callPackage ~/nix-config/pkgs/pond-interface { }).tests.smoke'
```

## Updating

1. Set `rev` in `./source.nix`, use `lib.fakeHash`, build, paste the hash.
2. Diff upstream's `CMakeLists.txt` and `screenshot/native/CMakeLists.txt`
   against `./install-rules.cmake`; check for new QML modules, `/usr` paths,
   commands, Python imports and fonts (`grep -rn 'font.family\|/usr/' shell lockscreen`).
3. Check upstream's package dependencies (`repo.butterfly.so/x86_64/pond.db`,
   `pond-shell`/`pond-lockscreen` entries) for a new Quickshell minimum.
4. Rebuild the overlay port against the new tree, then run the smoke test.
