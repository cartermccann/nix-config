{
  pkgs,
  ...
}:
# The standalone "plain Niri" session was retired; only Niri-Noctalia remains of
# the Niri sessions, and only on usins (modules/desktop-niri-noctalia.nix imports
# this). It provides the helper that session's keybind calls: wallpaper-pick on
# Mod+Shift+W. rice-dashboard, which the Hyprland sessions call too, is in
# home/desktop-helpers.nix.
let
  wallpaperPick = pkgs.writeShellScriptBin "wallpaper-pick" ''
    PICK=$(find -L ~/wallpapers -type f \( -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' \) | ${pkgs.fuzzel}/bin/fuzzel --dmenu --prompt="Wallpaper: ")
    # ~/wallpaper.png is a cross-session contract (hyprlock reads it too) — `magick`
    # writes real PNG bytes regardless of the source format, where `cp` lied about
    # the extension for jpg picks.
    [ -n "$PICK" ] || exit 0
    ${pkgs.imagemagick}/bin/magick "$PICK" ~/wallpaper.png
    awww img ~/wallpaper.png --transition-type fade --transition-duration 1
  '';
in
{
  home.packages = [ wallpaperPick ];
}
