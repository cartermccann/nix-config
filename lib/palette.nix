# Auseklis — the house palette. Cobalt on near-black (night) and cobalt on
# near-white (day), named for the Latvian morning star: one cold blue light
# over a dark field, the same thing the night variant draws. The machines
# under it carry the rest of the pantheon (perkons, usins, rukis).
#
# Colours are lifted from ~/projects/gentoo-dotfiles/themes/{cobalt,cobalt-light}
# and remapped onto Base16 slots, which is the shape this repo's consumers
# already expect.
#
# Consumed by the Hyprland session components (hyprland.lua and its waybar,
# fuzzel, swaync, swayosd, hyprlock), by home/ghostty.nix, and through
# lib/auseklis.nix by Stylix (every other themed app) and by nvim's
# colors/palette.lua (generated ~/.config/auseklis/palette.lua). Change a slot
# here and one rebuild carries it everywhere.
#
# Caelestia consumes base0D only, and indirectly: it is Material 3 end to end,
# so home/hyprland/caelestia.nix feeds the cobalt in as the seed of a generated
# M3 scheme rather than mapping these slots onto its roles. Changing base0D
# therefore restyles that session too, via a rebuild.
#
#   let pal = import ../lib/palette.nix;
#   pal.base00        # active variant (night)
#   pal.raw.base00    # same, no leading "#"
#   pal.day.base00    # explicit variant access
#   pal.night.base00
#
# Base16 slot meanings, for the remap below:
#   base00 bg · base01 surface · base02 raised/selection · base03 muted
#   base04 dark fg · base05 default fg · base06 light fg · base07 lightest
#   base08 red · base09 orange · base0A yellow · base0B green
#   base0C cyan · base0D blue (PRIMARY accent) · base0E magenta · base0F brown

let
  # Strip the leading "#" for fuzzel (RRGGBBAA) and Hyprland rgba(...).
  mkRaw = slots: builtins.mapAttrs (_: v: builtins.substring 1 6 v) slots;

  # ── Night (cobalt) ────────────────────────────────────────────────────────
  # GROUND/BASE/SURFACE map to bg/surface/raised; SURFACE2 is kept as `overlay`
  # since Base16 has no fourth background step. base09 and base0F have no
  # source equivalent (the cobalt 16-colour set has no orange or brown), so
  # they are derived in-family rather than borrowed from another palette.
  nightSlots = {
    base00 = "#0a0c11"; # GROUND   — near-black ground
    base01 = "#0f1218"; # BASE     — panels / status
    base02 = "#171b23"; # SURFACE  — raised / selection
    base03 = "#3a4152"; # T8       — muted (comments / disabled)
    base04 = "#8b93a4"; # SUBTEXT  — dark fg
    base05 = "#e7ebf2"; # TEXT     — default fg
    base06 = "#c7cdd8"; # T7       — light fg
    base07 = "#f4f7fc"; # derived  — lightest
    base08 = "#f87171"; # T1  red
    base09 = "#fb923c"; # derived  orange (between T1 red and T3 amber)
    base0A = "#fbbf24"; # T3  yellow
    base0B = "#34d399"; # T2  green
    base0C = "#22d3ee"; # T6  cyan
    base0D = "#3b6bff"; # ACCENT — true cobalt, the primary accent
    base0E = "#a78bfa"; # T5  magenta
    base0F = "#2a4bbd"; # derived  deep cobalt (Base16's odd brown slot)
  };

  # ── Day (cobalt-light) ────────────────────────────────────────────────────
  # Same accent, inverted ground. base05..base07 walk darker rather than
  # lighter, which is how Base16 light schemes keep "default fg" readable.
  daySlots = {
    base00 = "#f7f7f8"; # GROUND   — off-white ground
    base01 = "#ffffff"; # BASE     — panels / cards
    base02 = "#f3f4f6"; # SURFACE  — raised / selection
    base03 = "#d1d5db"; # T8       — muted
    base04 = "#6b7280"; # SUBTEXT  — dark fg
    base05 = "#0d0d12"; # TEXT     — default fg
    base06 = "#1f2937"; # T7       — heavier fg
    base07 = "#000000"; # darkest
    base08 = "#e5484d"; # T1  red
    base09 = "#d97706"; # T3  amber → orange
    base0A = "#b45309"; # T11 yellow (darkened for contrast on white)
    base0B = "#30a46c"; # T2  green
    base0C = "#0891b2"; # T6  cyan
    base0D = "#3b6bff"; # ACCENT — same cobalt in both variants
    base0E = "#8e4ec6"; # T5  magenta
    base0F = "#1e4fd8"; # ACCENT_INK — deep cobalt
  };

  mkVariant =
    {
      slots,
      mode,
      overlay,
      textDim,
      accentInk,
      accentBright,
      border,
    }:
    slots
    // {
      inherit mode;
      raw = mkRaw slots;

      # Semantic aliases — what the session components actually reference.
      bg = slots.base00;
      surface = slots.base01;
      raised = slots.base02;
      inherit overlay; # fourth background step (SURFACE2)
      text = slots.base05;
      inherit textDim;
      accent = slots.base0D; # cobalt
      inherit accentBright accentInk border;
      cursor = slots.base0D;

      # Retro green-phosphor motif, carried over from the previous palette so
      # the components that reference it keep rendering.
      phosphorBg = "#0a1a0a";
      phosphorText = "#33ff33";
    };

  night = mkVariant {
    slots = nightSlots;
    mode = "dark";
    overlay = "#212734"; # SURFACE2
    textDim = "#8b93a4"; # SUBTEXT
    accentInk = "#cfe0ff"; # ACCENT_INK — text on cobalt
    accentBright = "#6b8fff"; # T12
    border = "rgba(255,255,255,0.08)";
  };

  day = mkVariant {
    slots = daySlots;
    mode = "light";
    overlay = "#e8eaee"; # SURFACE2
    textDim = "#6b7280"; # SUBTEXT
    accentInk = "#1e4fd8"; # ACCENT_INK
    accentBright = "#2563eb"; # T12
    border = "rgba(0,0,0,0.09)";
  };

  # The variant every consumer gets by default. Flip to `day` to invert the
  # whole Hyprland session and ghostty in one edit; both variants are always
  # available as pal.night / pal.day regardless.
  active = night;
in
active
// {
  inherit night day;
  variants = {
    inherit night day;
  };
}
