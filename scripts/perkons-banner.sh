#!/usr/bin/env bash
# Perkons terminal banner — the host wordmark, drawn once per interactive shell.
#
# Replaces the old `ff` cascade, which replayed fastfetch line-by-line with a
# sleep between each one: every new terminal paid an animation plus a full
# system probe before it was usable. This probes nothing.
#
# The art is a literal heredoc rather than a figlet call, so there is no
# startup dependency and the output cannot drift when a font package updates.
# Colours are injected from lib/palette.nix at build time (@fill0@ / @fill1@ /
# @shadow@ are "r;g;b" triplets), so the banner follows Auseklis without a
# second source of truth.
#
# The letterforms are solid blocks (█) with box-drawing glyphs standing in for
# the drop shadow. Those two classes are coloured differently — the blocks get
# a left-to-right ramp across the cobalt, the shadow gets a single dark cobalt
# — which is what makes the wordmark read as lit from the left and raised off
# the background rather than as flat ASCII.
set -u

# Nothing to draw when piped, redirected, or on a dumb terminal.
[ -t 1 ] || exit 0
[ "${TERM:-dumb}" != "dumb" ] || exit 0

pad="  "

# The art is multibyte, and awk's substr() is byte-based under the C locale —
# it splits these glyphs mid-sequence, producing mojibake plus an "Invalid
# multibyte data" warning on every shell start. Forcing a UTF-8 locale makes
# gawk's string functions character-based, which is what the per-character
# colouring below depends on.
export LC_ALL=C.UTF-8

paint() {
  awk -v fill0="@fill0@" -v fill1="@fill1@" -v shadow="@shadow@" -v pad="$pad" '
  function part(s, i,   a) { split(s, a, ";"); return a[i] + 0 }
  function lerp(a, b, t) { return a + (b - a) * t }
  { lines[NR] = $0; if (length($0) > maxw) maxw = length($0) }
  END {
    for (i = 1; i <= NR; i++) {
      n = length(lines[i]); out = pad; prev = ""
      for (j = 1; j <= n; j++) {
        ch = substr(lines[i], j, 1)
        if (ch == " ") { out = out " "; prev = ""; continue }
        if (ch == "\342\226\210" || ch == "█") {
          f = (maxw > 1) ? (j - 1) / (maxw - 1) : 0
          col = sprintf("%d;%d;%d",
            int(lerp(part(fill0,1), part(fill1,1), f) + 0.5),
            int(lerp(part(fill0,2), part(fill1,2), f) + 0.5),
            int(lerp(part(fill0,3), part(fill1,3), f) + 0.5))
          style = "1;38;2;" col
        } else {
          style = "38;2;" shadow
        }
        # Only re-emit the escape when the colour actually changes; a solid run
        # of blocks is one sequence instead of one per column.
        if (style != prev) { out = out "\033[" style "m"; prev = style }
        out = out ch
      }
      printf "%s\033[0m\n", out
    }
  }'
}

echo
paint <<'ART'
██████╗ ███████╗██████╗ ██╗  ██╗ ██████╗ ███╗   ██╗███████╗
██╔══██╗██╔════╝██╔══██╗██║ ██╔╝██╔═══██╗████╗  ██║██╔════╝
██████╔╝█████╗  ██████╔╝█████╔╝ ██║   ██║██╔██╗ ██║███████╗
██╔═══╝ ██╔══╝  ██╔══██╗██╔═██╗ ██║   ██║██║╚██╗██║╚════██║
██║     ███████╗██║  ██║██║  ██╗╚██████╔╝██║ ╚████║███████║
╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚══════╝
ART

# Hairline rule under the wordmark, stepping along the same ramp. Coloured in
# whole segments rather than per character — cheap, and the width matches the
# art above it.
rule() {
  local width=59 segs=13 i n seg f r g b f0r f0g f0b f1r f1g f1b
  IFS=';' read -r f0r f0g f0b <<<"@fill0@"
  IFS=';' read -r f1r f1g f1b <<<"@fill1@"
  printf '%s' "$pad"
  for ((i = 0; i < segs; i++)); do
    f=$(((i * 100) / (segs - 1)))
    r=$((f0r + (f1r - f0r) * f / 100))
    g=$((f0g + (f1g - f0g) * f / 100))
    b=$((f0b + (f1b - f0b) * f / 100))
    # The last segment takes the remainder so the rule spans the art exactly.
    n=$((width / segs))
    ((i == segs - 1)) && n=$((width - n * (segs - 1)))
    seg=$(printf '%*s' "$n" '')
    printf '\033[38;2;%d;%d;%dm%s' "$r" "$g" "$b" "${seg// /─}"
  done
  printf '\033[0m\n'
}
rule
echo
