# Emits the Auseklis palette as GTK `@define-color` declarations.
#
# This is what lets the stylesheets under config/hyprland/ be plain, static
# .css files: no Nix string interpolation, no `@placeholder@` substitution
# pass, and real syntax highlighting in an editor. Each consumer writes this
# next to its stylesheet as `_auseklis.css` and pulls it in with
# `@import url("_auseklis.css");` — GTK resolves the URL relative to the
# importing file, so the two always travel together.
#
# Solids use `@base05`. Translucency uses `alpha(@base01, 0.45)` rather than
# `rgba(15, 18, 24, 0.45)`: GTK's alpha() *multiplies* the colour's existing
# alpha, and every slot defined here is fully opaque, so alpha(@x, a) is
# exactly the rgba() form it replaces — verified against every translucent
# colour in both stylesheets via GTK's computed style.
#
# Every hex-valued attribute is exported, so the semantic aliases (@accent,
# @surface, @border, @text, ...) are available alongside the base16 slots.
#
# The Auseklis glass tiers (lib/auseklis.nix) follow the palette as derived
# @define-color tokens: @frost_bg, @frost_rim, @lens_glow, @lens_fringe_l, ...
#
# Usage:  auseklisCss = import ./palette-css.nix lib pal;
lib: pal:
let
  isHex = v: builtins.isString v && builtins.match "#[0-9a-fA-F]{6}" v != null;
  colors = lib.filterAttrs (_: isHex) pal;
in
lib.concatStrings (lib.mapAttrsToList (name: hex: "@define-color ${name} ${hex};\n") colors)
+ (import ../../lib/auseklis.nix).gtkColors
