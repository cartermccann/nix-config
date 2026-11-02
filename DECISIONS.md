# Project decisions

## 2026-11-01 — Pond on Hyprland

- Add Pond as a third Hyprland shell on perkons; preserve Waybar and Caelestia.
  Reuse the upstream QML interface with a Hyprland adapter rather than recreating
  the visuals or switching to Arch/Niri.
- Keep Ly permanently and restore its cobalt/navy colormix on perkons; leave
  usins's desktop and login behavior intact. Remove perkons's Niri/Noctalia and
  Plasma configuration after dependencies have been moved to appropriate modules.
- Pond owns its animated background in its session. Existing sessions retain awww
  and the shared static wallpaper contract. Ghostty and Zen remain app defaults.
- Package API: `pkgs.pond-interface`, `pond-shell` and `pond-lock` wrappers forwarding
  Quickshell arguments, `pond-lock` PAM service, `hyprland-pond` login tile, and
  `hypr/hyprland-pond.lua`. Keep runtime settings as validated data, not edits to
  immutable Nix files. Preserve the upstream adapter surface while replacing its
  implementation, minimizing QML consumer churn.
- Use the stable Qt 6.11 package family first, including both native plugins and
  shader compilation. Resolve any Quickshell API incompatibility before accepting
  the port; avoid mixing Qt closures.
- Packaging verification found Pond's locker requires Quickshell >= 0.3.1. Build
  private Quickshell 0.3.2 against stable Qt 6.11.2 for Pond; leave Caelestia's
  package unchanged. Both native plugins and shaders use the same Qt family.
- Pond Gramercy is proprietary and not distributed by upstream. Use the disclosed
  Onest fallback, with an override for a legitimately supplied Gramercy package.
  Ship the procedural sky and original dusk artwork; no separate upstream photo
  wallpaper collection was found in the official package repository.
- Parallel implementation ownership: package/runtime in `pkgs/pond-interface/`
  except `overlay/` and `tests/`, plus `lib/overlays.nix`; compositor/settings port
  in `pkgs/pond-interface/overlay/` and `tests/`; session/host integration in existing
  home and system modules. Parent owns integration documentation and final review.
  Shared checkout, no delegate commits or live desktop changes. Codex review follows
  implementation, then full build validation and user-applied switching.

## 2026-11-01 — fail safely on unknown lock ownership

Do not enable Hyprland's session-lock restore option for automatic recovery.
Pinned Hyprland cannot non-destructively identify a lock's current owner, and a
replaced live client can later unlock beneath its replacement. Require exact
client confirmation (Pond IPC or hyprlock's locked callback), preserve an
unconfirmed holder, and report failure. An authenticated alternate VT can end
an unusable session. Rejected process-survival heuristics and historical PID
records as proof of protocol ownership.
