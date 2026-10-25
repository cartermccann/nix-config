{
  config,
  lib,
  pkgs,
  options,
  ...
}:
# Hyprland session. Split by the artifact each part produces rather than kept
# as one file — this was a single 1169-line hyprland.nix generating ten config
# files for six programs.
#
#   scripts.nix     the hypr-* helper scripts
#   bar-scripts.nix waybar's live modules: modes, recording, Tailscale, updates
#   menu.nix        the Auseklis menu (Super+Alt+Space), focus-or-launch, keybinding list
#   projects.nix    project launcher (Super+O): repo picker -> herdr workspace layout
#   agents.nix      agent cockpit: waybar module + switcher (Super+Shift+A) over herdr
#   calendar.nix    the clock's calendar dropdown (calendar.py, Google iCal feeds)
#   compositor.nix  hyprland.lua + hyprsunset.conf
#   waybar.nix      waybar config + stylesheet
#   swaync.nix      notification daemon + stylesheet
#   menus.nix       fuzzel + swayosd
#   lock.nix        hyprlock + hypridle
#   caelestia.nix   the Caelestia shell (second session tile)
#   palette-css.nix palette + glass tokens -> GTK @define-color block (not a module)
#
# Colours come from lib/palette.nix; glass, shape and blur from
# lib/auseklis.nix. Each module imports them directly rather than threading
# them through, so any one of them can be read on its own. The large stylesheets (waybar, swaync, calendar) are plain .css
# files under config/hyprland/ with no interpolation; palette-css.nix emits
# the palette and glass tokens next to each (and next to swayosd's inline
# one) as _auseklis.css, which they pull in with @import. fuzzel, hyprlock and
# hypridle stay inline.
{
  imports = [
    ./scripts.nix
    ./bar-scripts.nix
    ./menu.nix
    ./projects.nix
    ./agents.nix
    ./calendar.nix
    ./compositor.nix
    ./waybar.nix
    ./swaync.nix
    ./menus.nix
    ./lock.nix
    ./caelestia.nix
  ];

  # The hypr-* scripts add themselves in scripts.nix; these are the plain
  # packages the session needs.
  home.packages = [
    pkgs.hyprsunset # night light daemon (autostarted by compositor.nix; hyprland session only)
    pkgs.grimblast # screenshot wrapper (adds --freeze; wraps grim+slurp)
    pkgs.hyprpicker # color picker → clipboard (SUPER+SHIFT+C)
  ];

  # Print-screen target must exist or grimblast silently fails.
  home.file."Pictures/Screenshots/.keep".text = "";

  # Seed ~/wallpaper.png on first activation — *converted*, not copied, so the
  # PNG-named file actually contains PNG bytes (hyprlock requires it).
  home.activation.seedWallpaper = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    [ -f "$HOME/wallpaper.png" ] || run ${pkgs.imagemagick}/bin/magick ${../../wallpaper/fam.jpg} "$HOME/wallpaper.png"
  '';

  # Hyprland 0.55 dropped hyprlang in favor of Lua (`~/.config/hypr/hyprland.lua`).
  # The home-manager `wayland.windowManager.hyprland` module only emits the old
  # hyprlang `.conf` (which 0.55 ignores), so we bypass it and write the Lua
  # config directly. The compositor + portal come from modules/desktop-hyprland.nix.
  # API reference: $hyprland/share/hypr/stubs/hl.meta.lua

  # Opt the Hyprland session out of global Stylix so it carries the Auseklis
  # palette (same idea as stylix.targets.niri.enable=false in home/shell.nix).
  # Guarded: stylix 25.11 may not declare a `hyprland` target, and if it
  # doesn't, Stylix never touches hypr/ anyway.
  stylix = lib.optionalAttrs (options.stylix.targets ? hyprland) {
    targets.hyprland.enable = false;
  };
}
