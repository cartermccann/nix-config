# Pond on Hyprland — implementation plan

Status: implemented; both system builds, regression tests and final focused
security review passed. Ready for interactive acceptance. No live configuration
has been activated. See [verification and acceptance](pond-hyprland.md).

Source reviewed: [bflycomputer/pond-interface](https://github.com/bflycomputer/pond-interface),
commit `f77883588cf0b27bc4c5fa5c0ee2dc1814a448a6` (2026-11-01).

## Intended result

On perkons, expose exactly these desktop sessions:

- **Hyprland** — existing Waybar daily driver; retain its behavior and appearance.
- **Hyprland (Caelestia)** — retain the existing session.
- **Hyprland (Pond)** — port the real Pond shell, retaining as much of its UI and functionality as possible.

Ghostty is the terminal and Zen is the browser, including launcher actions,
terminal desktop entries, links, and keyboard shortcuts. Preserve existing profiles.
Keep Ly as the permanent terminal-style login manager. Restore its previous
`colormix` animation in place of the current CM/26 monogram animation, retaining
the current login layout and palette. History before commit `a4c1e79` records
cobalt (`base0D`), deep navy (`0x00102a66`), and black (`0x20000000`), running
continuously. Verify the installed Ly version's animation timing keys before
restoring the pacing; historical settings used `min_refresh_delta = 24`.
Ly is the login greeter, not the in-session locker; retain the separate session
locking work described below.
Keep the current Hyprland version and dwindle layout initially. Preserve shared
monitor configuration, NVIDIA setup, Arya EQ, dictation, project/agent shortcuts,
and Codex desktop preservation patches.

Scope assumption: session cleanup is for perkons. usins currently depends on
Niri/Noctalia and does not import the system Hyprland module; leave it functional.
Consequently Niri/Noctalia flake inputs cannot yet be deleted globally.

## 1. Package Pond and its assets reproducibly

- Add a pinned source and `pkgs/pond-interface/` derivation, with focused patches
  rather than a wholesale vendored copy. Record upstream revision and update steps.
- Check actual package/options availability with nixos-mcp during implementation.
  Build a compatible Qt/Quickshell closure: upstream native plugins request Qt 6.11.
  Do not mix plugins built against a different Qt from the shell runtime.
- Package `Pond.System`, screenshot native plugin, QML import directories, Python
  dependencies, helpers, icons, shaders, and runtime commands. Replace hardcoded
  `/usr/bin` and `/usr/share` locations with store paths or wrapped dependencies.
- Compile shader assets with the selected Qt toolchain where necessary; validate
  the daylight shader on the NVIDIA host.
- Locate upstream Onest and Pond Gramercy font sources and any separately shipped
  wallpapers. Verify asset licenses and pin sources/hashes. Do not silently replace
  missing fonts or claim the wallpaper collection is complete before checking it.
- The source includes a procedural daylight sky and lock-screen artwork; its
  appearance picker offers dynamic sky or a custom image, not a confirmed bundled
  photo collection. Include those known assets and inventory external assets.

Gate: shell and native QML modules build and load without missing imports/assets.

## 2. Add a properly isolated Hyprland session

- Add `home/hyprland/pond.nix` and a Pond session entry in
  `modules/desktop-hyprland.nix`, using `start-hyprland` and a generated
  `hyprland-pond.lua`.
- Extend `mkHyprlandLua` in `home/hyprland/compositor.nix` from its two-way selector
  to explicit shell profiles. Reuse common compositor configuration; select shell
  startup, key actions, layer rules, wallpaper ownership, and lock/idle handling.
- Use Pond styling in the new session, including its typography, light/dark behavior,
  cards, animation, sidebar, and settings. Avoid globally retheming existing sessions.
- Start only the selected shell's notification server, OSD, launcher, locker, and
  idle handling. Ensure logout/relogin terminates session-owned processes, including
  failed starts and transitions between the three shells.
- Integrate Vicinae for Pond's launcher/clipboard experience. Inspect whether the
  upstream fork is needed; retain only compatible functionality and replace Pond's
  package-manager extension as part of phase 6. Ghostty and Zen remain defaults.

Gate: all three sessions launch independently with working terminal/browser,
portals, notifications, clipboard, and no competing shell daemons.

## 3. Port compositor integration and the minimap

- Introduce a compositor adapter rather than spreading `hyprctl` calls through QML.
  Replace `NiriMsg.qml` dependencies with a stable model of monitors, workspaces,
  windows, focus, keyboard layout, and actions.
- Use an initial Hyprland state snapshot plus its event stream; handle reconnects,
  stale window addresses, destroyed workspaces, monitor hotplug, and shell restarts.
- Preserve workspace/window selection, application activation from notifications,
  launch-on-workspace, window movement, and drag/drop between workspaces/outputs.
- Adapt the minimap to Hyprland's actual window geometry and dwindle behavior.
  Niri column insertion, column stacks, workspace ordering, and overview transitions
  are not direct equivalents. Design explicit Hyprland interactions instead of
  pretending those commands translate one-to-one.
- Keep Pond's overview/preview intent through shell UI where feasible; validate
  capture protocol support before promising live window thumbnails. Do not require
  a compositor plugin or switch layouts just to imitate Niri columns.
- Adapt tutorials and shortcut labels to the actual Hyprland bindings.

Gate: multi-monitor minimap and drag/drop agree with actual compositor state,
including empty workspaces, floating/fullscreen windows, and windows closing mid-drag.

## 4. Bring over wallpapers and appearance controls

- Ship Pond's live daylight background as a first-class feature, including its
  reveal effects and matching lock-screen rendering.
- In Pond, let Pond own the background and do not start awww. In Waybar and
  Caelestia, retain awww and the existing `~/wallpaper.png` contract.
- Include confirmed upstream static assets in the shared wallpaper library alongside
  existing images. Build a combined managed directory: `~/wallpapers` is currently
  a Home Manager symlink, so do not try to copy new files into it at runtime.
- Point Pond's custom-image picker at that library. Keep Pond's selected wallpaper
  and dynamic mode in its own persistent state so switching sessions does not reset
  the other desktops' wallpaper. Provide static sky exports for the shared library
  if feasible; these supplement rather than replace the animated background.
- Verify mixed-resolution monitors, scaling, resume, and shader resource use.

Gate: dynamic sky and custom wallpapers work after relogin; existing sessions
retain their backgrounds and lock-screen images.

## 5. Finish hardware, settings, screenshots, and locking

- Port monitor arrangement, resolution/refresh/scale, keyboard layouts, mouse,
  brightness, and display power control to the pinned Hyprland API.
- Keep Nix-generated configuration immutable. Store validated Pond-only UI settings
  as data in a writable user state file, applied by a controlled adapter after the
  common configuration. Offer reset-to-declared-defaults. Do not generate executable
  Lua from arbitrary input or rewrite Home Manager store symlinks.
- Give display changes a confirmation timeout and automatic rollback; test invalid
  modes, unplugged monitors, reload, and restart behavior.
- Preserve PipeWire/Arya routing. Pond may change the default sink only on an explicit
  user selection, never as a startup side effect. Port Wi-Fi/Bluetooth pairing,
  battery/media controls, and DDC brightness with clear unsupported-device states.
- Connect existing screenshot capture to Pond's preview/copy/drag UI and verify
  screen sharing and file dialogs under the Hyprland portal.
- Port Pond's session locker with a dedicated NixOS PAM service, lock-before-sleep,
  resume handling, and a clear secure-lock readiness check. Test failure paths;
  keep hyprlock as the development fallback until the Pond locker passes.

Gate: settings persist without affecting the other sessions; lock/unlock and
suspend/resume succeed; capture, sharing, audio, and hardware controls work.

## 6. Replace distro-specific integration

- Replace `pm -a` / `pm -u` with a Nix-aware update backend. Define checks explicitly
  around pinned flake inputs; checking for updates must not silently change the lock.
- Connect progress and errors to Pond's existing update/install cards. Build without
  sudo and launch authenticated switching in Ghostty; do not assume passwordless sudo.
- Package installation must follow this repo's declarative workflow (home packages
  in `home/tools.nix`) and show proposed changes before applying them. Keep uv tools
  separate. Do not leave Arch/Pond package commands behind in launcher actions.
- Use NixOS/host information in system details and adapt platform-specific help.
- Keep Ly permanently; do not introduce SDDM or Pond's graphical greeter.
  Restore the historical colormix animation described above, retaining the current
  terminal-style login layout. Remove unused monogram generation/assets only after
  checking other hosts' consumers; scope the animation change to perkons.
- Retain the existing bootloader. Pond's GRUB assets are not a reason to replace
  this machine's boot stack; boot splash styling is a separate follow-up.

## 7. Remove obsolete perkons sessions and dependencies

- Remove perkons imports for Niri/Noctalia and Plasma. Move the currently shared
  `programs.niri.enable` declaration into the usins/Niri-specific module.
- Make Home Manager's Niri/Noctalia configuration host-specific instead of importing
  it unconditionally through `home/common.nix`.
- Move reusable helpers from `home/niri.nix` to a neutral module before removing its
  perkons import. Preserve `rice-dashboard` and any still-used wallpaper helpers.
- Preserve UPower when removing Noctalia's system module. Explicitly configure Qt
  styling and any desktop services previously supplied indirectly by Plasma.
- Remove unnecessary perkons-only packages and portal backends after tracing their
  consumers. Retain GTK file chooser/keyring/polkit functionality.
- Make the perkons chooser contain exactly the three intended desktop sessions,
  including removal of Plasma X11 entries if present. Update comments and AGENTS.md
  to describe the new session and wallpaper ownership rules.
- Keep Niri/Noctalia inputs, overlays, and modules still required by usins. Evaluate
  both hosts so cleanup cannot quietly break the laptop.

## Validation and rollout

Implement in reviewable phases: packaging; isolated session and core controls;
compositor/minimap; wallpaper/settings/locker; Nix integration; session cleanup.
The early shell is a milestone, not the definition of a completed port.

- Format changed Nix files with `nixfmt`.
- Test meaningful backend boundaries: IPC model updates/reconnects, drag/drop
  failure recovery, settings validation/persistence, and display rollback.
- Evaluate both hosts and build perkons with `nh os build ~/nix-config` (or the
  appropriate explicit host build). Verify session entries and runtime closures.
- After a successful build, ask the user to apply with `nrs`, as required by the
  repository workflow; the agent must not assume rebuild sudo access.
- Perform actual login/logout tests for all three shells, including switching from
  Pond back to Waybar/Caelestia; test NVIDIA/multi-monitor behavior, locking, suspend,
  notifications, app defaults, capture/sharing, hardware, and wallpaper persistence.
- Record tested features and remaining limitations in `docs/pond-hyprland.md`.
  Do not call the port complete based only on a Nix build or a rendered QML panel.

Main uncertainties: Qt/Quickshell compatibility, availability/licensing of external
fonts/assets, mapping Niri overview interactions to dwindle, and graphics/runtime
behavior on NVIDIA. Resolve packaging compatibility first before substantial UI work.
