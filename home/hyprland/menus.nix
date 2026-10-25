{
  config,
  lib,
  ...
}:
# fuzzel (launcher + every dmenu script) and swayosd (volume/brightness OSD).
let
  pal = import ../../lib/palette.nix;
  auseklis = import ../../lib/auseklis.nix;
  lens = auseklis.glass.lens;
  cfgHome = config.xdg.configHome;
  # 0.5 -> "80": fuzzel takes RRGGBBAA, so glass alphas become a hex byte.
  alphaHex =
    a:
    let
      h = lib.toLower (lib.toHexString (builtins.floor (a * 255 + 0.5)));
    in
    if builtins.stringLength h == 1 then "0${h}" else h;
in
{
  xdg.configFile."fuzzel/hypr.ini".text = ''
    [main]
    font=${auseklis.font.mono}:size=12
    prompt=>
    icon-theme=Papirus-Dark
    lines=10
    width=36
    horizontal-pad=20
    vertical-pad=16
    inner-pad=10
    layer=overlay

    # Lens tier (lib/auseklis.nix): Hyprland's layer blur behind a 50% tint,
    # a neutral hairline rather than a cobalt frame, and cobalt only on the
    # selected row. fuzzel draws no inner shadows, so the edge light that
    # waybar/swaync get is carried here by the hairline alone.
    [colors]
    background=${pal.raw.base01}${alphaHex lens.tint}
    text=${pal.raw.base05}ff
    prompt=${pal.raw.base04}ff
    placeholder=${pal.raw.base03}ff
    input=${pal.raw.base05}ff
    match=${pal.raw.base0D}ff
    selection=${pal.raw.base0D}33
    selection-text=${pal.raw.base07}ff
    selection-match=${pal.raw.base0C}ff
    border=${pal.raw.base05}${alphaHex lens.hairline}

    [border]
    width=1
    radius=${toString auseklis.radius.surface}
    selection-radius=${toString auseklis.radius.row}
  '';

  # swayosd: lens tier. GTK4 resolves the @import next to this file, so the
  # OSD reads the same generated tokens as waybar and swaync.
  xdg.configFile."swayosd/_auseklis.css".text = import ./palette-css.nix lib pal;
  xdg.configFile."swayosd/style.css".text = ''
    @import url("_auseklis.css");
    window {
      background-color: @lens_bg;
      background-image: linear-gradient(to bottom, @lens_sheen, transparent 40%);
      border: 1px solid @lens_hairline;
      border-radius: ${toString auseklis.radius.surface}px;
      box-shadow: inset 0 1px 0 @lens_rim, inset 1px 0 0 @lens_fringe_l,
        inset -1px 0 0 @lens_fringe_r, inset 0 -1px 0 @lens_caustic,
        inset 0 0 18px @lens_glow;
    }
    label {
      color: @base05;
      font-family: "${auseklis.font.mono}", monospace;
    }
    progressbar trough {
      background: alpha(@base05, 0.08);
      border-radius: 4px;
    }
    progressbar progress {
      background: @base0D;
      border-radius: 4px;
      box-shadow: inset 0 1px 0 alpha(white, 0.3), 0 0 12px alpha(@base0D, 0.45);
    }
    image {
      color: @base06;
    }
  '';

  # swaync: glassy notification center
}
