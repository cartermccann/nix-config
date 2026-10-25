{
  lib,
  ...
}:
# swaync: notification daemon config and stylesheet.
let
  pal = import ../../lib/palette.nix;
in
{
  xdg.configFile."swaync/config.json".text = builtins.toJSON {
    positionX = "right";
    positionY = "top";
    layer = "overlay";
    control-center-layer = "top";
    control-center-width = 380;
    control-center-margin-top = 10;
    control-center-margin-bottom = 10;
    control-center-margin-right = 10;
    control-center-margin-left = 10;
    notification-window-width = 380;
    timeout = 8;
    timeout-low = 5;
    timeout-critical = 0;
    fit-to-screen = true;
    widgets = [
      "title"
      "dnd"
      "notifications"
    ];
    widget-config = {
      title = {
        text = "notifications";
        clear-all-button = true;
        button-text = "clear";
      };
      dnd = {
        text = "do not disturb";
      };
    };
  };

  # Same arrangement as waybar: real CSS in config/hyprland/swaync.css, with
  # the palette emitted next to it as _auseklis.css and pulled in via @import.
  xdg.configFile."swaync/style.css".source = ../../config/hyprland/swaync.css;
  xdg.configFile."swaync/_auseklis.css".text = import ./palette-css.nix lib pal;

  # hyprlock: frosted blur-over-wallpaper lock
}
