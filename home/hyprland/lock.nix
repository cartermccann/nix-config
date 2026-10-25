{
  config,
  lib,
  pkgs,
  hyprland,
  ...
}:
# hyprlock (lock screen) and hypridle (idle -> lock -> dpms chain).
let
  pal = import ../../lib/palette.nix;
  auseklis = import ../../lib/auseklis.nix;
  hyprctl = "${hyprland.packages.${pkgs.stdenv.hostPlatform.system}.hyprland}/bin/hyprctl";
in
{
  xdg.configFile."hypr/hyprlock.conf".text = ''
    background {
      monitor =
      path = ${config.home.homeDirectory}/wallpaper.png
      blur_passes = ${toString auseklis.blur.passes}
      blur_size = ${toString auseklis.blur.size}
      noise = 0.035
      contrast = 0.9
      brightness = 0.7
      vibrancy = 0.25
    }

    input-field {
      monitor =
      size = 280, 50
      outline_thickness = 2
      dots_size = 0.25
      dots_spacing = 0.3
      outer_color = rgba(${pal.raw.base0D}ee)
      inner_color = rgba(${pal.raw.base01}cc)
      font_color = rgba(${pal.raw.base05}ff)
      check_color = rgba(${pal.raw.base0C}ee)
      fail_color = rgba(${pal.raw.base08}ee)
      placeholder_text = <span foreground="##${pal.raw.base04}">password</span>
      rounding = ${toString auseklis.radius.surface}
      fade_on_empty = false
      position = 0, -40
      halign = center
      valign = center
    }

    label {
      monitor =
      text = $TIME
      font_size = 64
      font_family = ${auseklis.font.mono}
      color = rgba(${pal.raw.base05}ff)
      position = 0, 120
      halign = center
      valign = center
    }

    label {
      monitor =
      text = cmd[update:60000] date +"%A, %B %d"
      font_size = 18
      font_family = ${auseklis.font.mono}
      color = rgba(${pal.raw.base06}cc)
      position = 0, 60
      halign = center
      valign = center
    }
  '';

  # hypridle: lock then display off, but never auto-suspend — the box stays
  # reachable over the network while the screen is locked; manual suspend still works from
  # the power menu. hypridle/hyprlock keep their own hyprlang configs, but their
  # dpms calls go through hyprctl to Hyprland 0.55, which takes the Lua
  # dispatcher form: `hyprctl dispatch 'hl.dsp.dpms(...)'`.
  xdg.configFile."hypr/hypridle.conf".text = ''
    general {
      lock_cmd = pidof hyprlock || hyprlock
      before_sleep_cmd = loginctl lock-session
      after_sleep_cmd = ${hyprctl} dispatch 'hl.dsp.dpms({ action = "on" })'
    }

    listener {
      timeout = 300
      on-timeout = loginctl lock-session
    }

    listener {
      timeout = 360
      on-timeout = ${hyprctl} dispatch 'hl.dsp.dpms({ action = "off" })'
      on-resume = ${hyprctl} dispatch 'hl.dsp.dpms({ action = "on" })'
    }
  '';
}
