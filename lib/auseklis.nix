# Auseklis — the house design system. lib/palette.nix owns the colours; this
# file owns everything else that has to agree across surfaces (glass, shape,
# type, motion) and the derived forms consumers need (a Stylix base16 scheme,
# GTK @define-color tokens, a Lua palette for nvim).
#
# The glass model is three tiers, chosen by how long a surface stays up:
#
#   pane   windows: content first, near-opaque, light blur
#   frost  persistent chrome (waybar, swaync cards, hyprlock): heavy frost
#   lens   summoned surfaces (fuzzel, swayosd, control centre): the most
#          light-play: specular sheen, inner cobalt glow, cyan/violet fringe
#
# The rule that keeps it legible: every text-bearing surface keeps at least
# `tintFloor` of base01 under the blur, so text holds contrast over any
# wallpaper. Light effects live on the edges, never across content.
#
# Hyprland blur is global (one strength for every blurred surface), so the
# tiers differ by tint and edge treatment, not blur radius.
#
#   let auseklis = import ../lib/auseklis.nix;
#   auseklis.glass.frost.tint   # 0.42
#   auseklis.base16             # Stylix base16Scheme attrset
let
  pal = import ./palette.nix;
  slots = [
    "base00"
    "base01"
    "base02"
    "base03"
    "base04"
    "base05"
    "base06"
    "base07"
    "base08"
    "base09"
    "base0A"
    "base0B"
    "base0C"
    "base0D"
    "base0E"
    "base0F"
  ];
  pick =
    src:
    builtins.listToAttrs (
      map (s: {
        name = s;
        value = src.${s};
      }) slots
    );
in
rec {
  inherit pal;

  font = {
    mono = "JetBrainsMono Nerd Font";
  };

  radius = {
    surface = 14; # windows, bars, menus, cards
    row = 10; # menu rows, buttons
    chip = 9; # bar modules, small controls
    squirclePower = 2.5; # Hyprland rounding_power
  };

  # cubic-bezier(.2,.8,.2,1): fast in, long soft settle. No bounce anywhere.
  # The GTK stylesheets carry these as literals (no interpolation there);
  # waybar's drawer reads openMs.
  motion = {
    curve = "0.2, 0.8, 0.2, 1";
    hoverMs = 180;
    openMs = 260;
  };

  glass = {
    tintFloor = 0.42;
    # Hyprland's layer-rule ignore_alpha for glass surfaces: below the tint
    # floor so every tinted pixel blurs, above zero so empty gaps don't haze.
    blurThreshold = 0.3;
    # Advisory for windows: their opacity stays per-app in compositor.nix
    # (ghostty is the deliberate glass terminal at 0.68).
    pane = {
      tint = 0.92;
      hairline = 0.06;
      rim = 0.05;
    };
    frost = {
      tint = 0.42;
      hairline = 0.10;
      rim = 0.16; # 1px specular highlight on the top edge
      caustic = 0.28; # 1px shade on the bottom edge
    };
    lens = {
      tint = 0.50;
      hairline = 0.14;
      rim = 0.24;
      caustic = 0.32;
      sheen = 0.09; # top-down light falloff across the first ~40%
      glow = 0.14; # inner cobalt glow pooling in the bevel
      fringe = 0.11; # cyan left edge, violet right edge
    };
  };

  # Hyprland's Kawase blur. Heavier than the scenefx-era numbers (size 14,
  # passes 4): one more pass is what turns translucency into frost.
  blur = {
    size = 12;
    passes = 5;
    noise = 0.055;
    contrast = 0.94;
    brightness = 1.12;
    vibrancy = 0.7;
  };

  # Stylix wants bare hex per slot. Night variant: the house runs dark.
  base16 = pick pal.raw // {
    scheme = "Auseklis";
    author = "perkons nix-config";
  };

  # GTK3/4 CSS has no custom properties, only @define-color, so the glass
  # tiers are emitted as named colours built from the palette slots. Radii
  # stay literal in the stylesheets (GTK cannot parametrise lengths).
  gtkColors = ''
    @define-color pane_bg alpha(@base01, ${toString glass.pane.tint});
    @define-color pane_hairline alpha(@base05, ${toString glass.pane.hairline});
    @define-color pane_rim alpha(white, ${toString glass.pane.rim});
    @define-color frost_bg alpha(@base01, ${toString glass.frost.tint});
    @define-color frost_hairline alpha(@base05, ${toString glass.frost.hairline});
    @define-color frost_rim alpha(white, ${toString glass.frost.rim});
    @define-color frost_caustic alpha(black, ${toString glass.frost.caustic});
    @define-color lens_bg alpha(@base01, ${toString glass.lens.tint});
    @define-color lens_hairline alpha(@base05, ${toString glass.lens.hairline});
    @define-color lens_rim alpha(white, ${toString glass.lens.rim});
    @define-color lens_caustic alpha(black, ${toString glass.lens.caustic});
    @define-color lens_sheen alpha(white, ${toString glass.lens.sheen});
    @define-color lens_glow alpha(@base0D, ${toString glass.lens.glow});
    @define-color lens_fringe_l alpha(@base0C, ${toString glass.lens.fringe});
    @define-color lens_fringe_r alpha(@base0E, ${toString glass.lens.fringe});
    @define-color drop alpha(black, 0.28);
    @define-color select_bg alpha(@base0D, 0.20);
  '';

  # nvim's colorscheme reads the base16 slots from this instead of mirroring
  # them by hand. Derived tints stay in palette.lua: they are design calls.
  luaPalette =
    "-- Generated from lib/palette.nix by lib/auseklis.nix. Do not edit.\nreturn {\n"
    + builtins.concatStringsSep "" (map (s: "  ${s} = \"${pal.${s}}\",\n") slots)
    + "}\n";
}
