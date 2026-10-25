# Auseklis-styled fastfetch. Colours come from lib/palette.nix — same source as
# the Perkons banner, Starship prompt, and Hyprland session — so the on-demand
# system readout matches the rest of the desktop instead of the old neon-rainbow
# arcade boxes.
{ lib, ... }:
let
  pal = import ../lib/palette.nix;

  rgb =
    hex:
    let
      h = lib.removePrefix "#" hex;
    in
    lib.concatStringsSep ";" (
      map (i: toString (lib.fromHexString (builtins.substring i 2 h))) [
        0
        2
        4
      ]
    );

  fg = c: "38;2;${rgb c}";
  bold = c: "1;38;2;${rgb c}";

  keyColor = fg pal.base0F;
  hairline = "────────────────────────────────────────────────";
  esc = builtins.fromJSON ''"\u001b"'';

  mkMod = type: key: {
    inherit type key;
    inherit keyColor;
  };

  # A compact Auseklis ramp: ground → surface → accent → tertiary accents.
  paletteStrip =
    let
      colors = [
        pal.base00
        pal.base01
        pal.base02
        pal.base03
        pal.base0D
        pal.accentBright
        pal.base0C
        pal.base0B
      ];
      blocks = lib.concatMapStrings (c: "${esc}[${fg c}m█") colors;
    in
    "  ${blocks}${esc}[0m";

  config = {
    "$schema" = "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json";

    logo = {
      source = "NixOS";
      color = {
        "1" = fg pal.base0D;
        "2" = fg pal.accentBright;
      };
      padding = {
        top = 1;
        left = 2;
        right = 4;
      };
    };

    display = {
      separator = " ";
      constants = [ hairline ];
      color = {
        keys = keyColor;
        title = bold pal.base05;
        output = fg pal.base06;
      };
    };

    modules = [
      {
        type = "title";
        format = "{user-name-colored}{at-symbol-colored}{host-name-colored}";
      }
      {
        type = "custom";
        format = "  {#keys}{\$1}{#}";
      }
      "break"
      {
        type = "custom";
        format = "  {#keys}▸ system{#}";
      }
      (mkMod "os" "  os")
      (mkMod "kernel" "  kernel")
      (mkMod "initsystem" "  init")
      (mkMod "wm" "  wm")
      (mkMod "shell" "  shell")
      (mkMod "terminal" "  term")
      (
        mkMod "packages" "  packages"
        // {
          format = "{all}";
        }
      )
      "break"
      {
        type = "custom";
        format = "  {#keys}▸ hardware{#}";
      }
      (mkMod "board" "  board")
      (
        mkMod "cpu" "  cpu"
        // {
          showPeCoreCount = true;
        }
      )
      (mkMod "cpuusage" "  cpu%")
      (
        mkMod "gpu" "  gpu"
        // {
          detectionMethod = "pci";
        }
      )
      (mkMod "memory" "  memory")
      (mkMod "disk" "  disk")
      (mkMod "uptime" "  uptime")
      (
        mkMod "localip" "  ip"
        // {
          showIpv6 = false;
          compact = true;
        }
      )
      "break"
      {
        type = "custom";
        format = paletteStrip;
      }
    ];
  };
in
{
  xdg.configFile."fastfetch/config.jsonc".text = builtins.toJSON config;
}
