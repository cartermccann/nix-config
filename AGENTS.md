# nix-config — NixOS flake

Flake-parts layout. This repo defines the entire system (perkons, plus the usins laptop) + home-manager config.

## Layout

- `flake.nix` / `parts/` — flake outputs (hosts in `parts/hosts.nix`, templates in `parts/templates.nix`)
- `hosts/` — per-machine config
- `modules/` — NixOS system modules (desktop, nvidia, ollama, oom-protection, ...)
- `home/` — home-manager modules (tools, shell, neovim, tmux, niri, hyprland, ...)
- `templates/` — `nix flake init -t ~/nix-config#<lang>` dev-shell templates
- `scripts/`, `home/scripts/`, `config/`, `wallpaper/` — non-Nix assets referenced by modules

## Workflow

1. Edit the relevant module (`home/` for user-level, `modules/` for system-level).
2. Validate without sudo: `nh os build ~/nix-config` (or `nix flake check ~/nix-config`).
3. Apply: user runs `nrs` (nh os switch). There is NO passwordless sudo for rebuilds. After a successful `nh os build`, ask the user to apply (e.g. `! sudo nixos-rebuild switch --flake ~/nix-config#perkons`).

## Conventions

- Format Nix files with `nixfmt` (`nixfmt <file>`; `nixfmt-rfc-style` is its pre-26.05 name).
- New home packages go in `home/tools.nix`; system build deps in `modules/dev.nix`.
- Shell aliases/functions live in `home/shell.nix` — keep `~/.claude/rules/nix-environment.md` in sync when they change.
- Never commit `result` symlinks.
- Look up packages/options with the nixos-mcp tools, not memory — nixpkgs moves fast.

## Gotchas

- Desktop sessions: Hyprland is the daily driver; niri+noctalia is kept as a working fallback. The Hyprland session lives in `home/hyprland/` (a directory, not a single module) and has two login tiles built from one compositor config: "Hyprland" (waybar + swaync + fuzzel + swayosd + hyprlock, `hypr/hyprland.lua`) and "Hyprland (Caelestia)" (Caelestia/Quickshell provides all of those itself, `hypr/hyprland-caelestia.lua`). Both come from `mkHyprlandLua` in `home/hyprland/compositor.nix`, which takes a `shellKind` and differs at only eight seams — change anything else there and it lands in both sessions.
- awww (swww, renamed in nixpkgs 26.05) owns the wallpaper in every session; Noctalia's and Caelestia's own wallpaper modules are both deliberately disabled, and `~/wallpaper.png` is a cross-session contract (niri autostart, hyprlock background, both pickers).
- Audio: the Arya EQ is a PipeWire filter-chain sink declared in `modules/audio.nix`, not EasyEffects (removed). Sinks are ranked by `priority.session` there, but a `default.configured.audio.sink` entry in `~/.local/state/wireplumber/default-nodes` — written by `wpctl set-default`, pavucontrol, or `hypr-audio-sink` — **overrides that ranking permanently**. If the default sink looks wrong, check that file before touching the priorities.
- Big generated blobs live as real files under `config/`, not inline strings: `config/hyprland/*.css` (waybar + swaync, with the palette emitted alongside as `_auseklis.css` and pulled in via `@import`) and `config/audio/` (the EasyEffects presets the EQ curve was ported from). `hyprland.lua` stays inline — it interpolates ~40 Nix values and templating it would relocate the complexity, not remove it.
- First rebuild after adding a cachix substituter may prompt for trust — expected.
- uv-tool installs are NOT in this flake; manage them with `uv tool upgrade <name>`.
- The ChatGPT/Codex desktop app (`home/codex-desktop.nix`) now uses the pinned `codex-desktop-linux` Nix input plus local preservation patches in `pkgs/codex-desktop/`. Keep Codex Micro, composer dictation, bounded watchers, the native Codex profile, and scope limits intact when updating. The old `~/projects/input-linux/codex-desktop-overlay/` remains as rollback material; do not overwrite it. See `docs/codex-desktop.md` for update verification, the Watchbound post-patchelf metadata repair, and the upstream wrapper diagnostic trap.
