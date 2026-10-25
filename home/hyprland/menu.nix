{
  config,
  lib,
  pkgs,
  hyprland,
  ...
}:
# The Auseklis menu: one keybind (Super+Alt+Space) for apps, capture, modes,
# sharing, style, updates, keybindings and system, after Omarchy's menu tree
# (default/omarchy/omarchy-menu.jsonc) but drawn by fuzzel.
#
# The tree is the `entries` list below, compiled to ~/.config/auseklis/menu.json.
# Ids are dotted paths; an entry with `action` runs it, one without is a
# submenu of the entries under it. `when` hides a row unless its shell test
# passes and `checked` adds a tick; each level's tests run in one batched bash
# before fuzzel opens. Every submenu is reachable directly too:
# `auseklis-menu capture` is what Super+Ctrl+C runs.
let
  cfgHome = config.xdg.configHome;
  hyprctl = "${hyprland.packages.${pkgs.stdenv.hostPlatform.system}.hyprland}/bin/hyprctl";
  jq = "${pkgs.jq}/bin/jq";
  fuzzel = "${pkgs.fuzzel}/bin/fuzzel --dmenu --config ${cfgHome}/fuzzel/hypr.ini";

  # Focus the app if a window of it exists, launch it otherwise (Omarchy's
  # omarchy-launch-or-focus). Usage: auseklis-focus-or-launch <class-regex> <cmd...>
  focusOrLaunch = pkgs.writeShellScriptBin "auseklis-focus-or-launch" ''
    RE=$1; shift
    ADDR=$(${hyprctl} clients -j | ${jq} -r --arg re "$RE" \
      '[.[] | select(.class | test($re; "i"))] | sort_by(.focusHistoryID) | .[0].address // empty')
    if [ -n "$ADDR" ]; then
      ${hyprctl} eval "hl.dispatch(hl.dsp.focus({ window = 'address:$ADDR' }))" >/dev/null
    else
      setsid -f "$@" >/dev/null 2>&1
    fi
  '';

  # Long-running menu actions run in a floating terminal that ends on a clear
  # Done/Failed line instead of vanishing (Omarchy's presentation wrapper).
  present = pkgs.writeShellScriptBin "auseklis-present" ''
    exec ghostty --class=TUI.float -e bash -c '
      eval "$1"; code=$?
      echo
      if [ $code -eq 0 ]; then printf "\e[34m●\e[0m done"; else printf "\e[31m●\e[0m failed (%s)" "$code"; fi
      printf "  ·  any key to close"; read -rsn1' _ "$*"
  '';

  # The keybinding cheatsheet (Super+/): desktop, nvim, tmux, herdr, Ly and
  # bar bindings, each read live by cheatsheet.py, searchable in fuzzel.
  keys = pkgs.writeShellScriptBin "auseklis-keys" ''
    ${pkgs.python3}/bin/python3 ${./cheatsheet.py} \
      | ${fuzzel} --prompt="keys › " --width 100 --lines 24 >/dev/null
  '';

  # Capture helpers that need more than one line.
  # English only: the default tesseract pulls every language (~1 GiB).
  tesseract = pkgs.tesseract.override { enableLanguages = [ "eng" ]; };
  captureText = ''grim -g "$(slurp)" - | ${tesseract}/bin/tesseract stdin stdout 2>/dev/null | wl-copy && notify-send -a capture "Text copied"'';
  captureQr = ''grim -g "$(slurp)" - | ${pkgs.zbar}/bin/zbarimg -q --raw - | wl-copy && notify-send -a capture "QR decoded and copied"'';
  recordRegion = ''mkdir -p ~/Videos/Recordings && gpu-screen-recorder -w region -region "$(slurp -f "%wx%h+%x+%y")" -f 60 -a default_output -o ~/Videos/Recordings/$(date +%Y-%m-%d_%H-%M-%S).mp4 >/dev/null 2>&1 & pkill -RTMIN+8 waybar'';
  recording = ''pgrep -f "(^|/)gpu-screen-recorder( |$)" >/dev/null'';
  shotFile = "~/Pictures/Screenshots/$(date +%Y%m%d-%H%M%S).png";

  entries = [
    {
      id = "apps";
      glyph = "󰀻";
      label = "Apps";
      hint = "Super+Space";
    }
    {
      id = "apps.all";
      glyph = "󰀻";
      label = "All apps";
      hint = "launcher";
      action = "fuzzel --config ${cfgHome}/fuzzel/hypr.ini";
    }
    {
      id = "apps.zen";
      glyph = "󰈹";
      label = "Zen";
      hint = "focus or open";
      action = "auseklis-focus-or-launch '^zen' zen-beta";
    }
    {
      id = "apps.codex";
      glyph = "󰚩";
      label = "Codex";
      hint = "focus or open";
      action = "auseklis-focus-or-launch '^codex-desktop$' codex-desktop-guard";
    }
    {
      id = "apps.claude";
      glyph = "󰚩";
      label = "Claude";
      hint = "focus or open";
      action = "auseklis-focus-or-launch '^com.anthropic.claude$' claude-desktop";
    }
    {
      id = "apps.cursor";
      glyph = "󰨞";
      label = "Cursor";
      hint = "focus or open";
      action = "auseklis-focus-or-launch '^cursor$' cursor";
    }
    {
      id = "apps.obsidian";
      glyph = "󰠮";
      label = "Obsidian";
      hint = "focus or open";
      action = "auseklis-focus-or-launch '^(obsidian|md.obsidian)$' obsidian";
    }
    {
      id = "apps.granola";
      glyph = "󰍬";
      label = "Granola";
      hint = "focus or open";
      action = "auseklis-focus-or-launch '^granola$' granola";
    }

    {
      id = "agents";
      glyph = "󰚩";
      label = "Agents";
      hint = "Super+Shift+A";
      action = "auseklis-agents menu";
    }
    {
      id = "projects";
      glyph = "󰉋";
      label = "Projects";
      hint = "Super+O";
      action = "auseklis-project";
    }
    {
      id = "herdr";
      glyph = "󰆍";
      label = "Herdr";
      hint = "agent workspaces";
      action = "auseklis-herdr";
    }
    {
      id = "capture";
      glyph = "󰄀";
      label = "Capture";
      hint = "Super+Ctrl+C";
    }
    {
      id = "capture.region";
      label = "Screenshot · region";
      hint = "Super+Shift+S";
      action = "grimblast --freeze save area - | satty -f - --output-filename ${shotFile}";
    }
    {
      id = "capture.window";
      label = "Screenshot · window";
      hint = "annotate in satty";
      action = "grimblast save active - | satty -f - --output-filename ${shotFile}";
    }
    {
      id = "capture.screen";
      label = "Screenshot · screen";
      hint = "focused monitor";
      action = "grimblast save output ${shotFile} && notify-send -a capture 'Screenshot saved'";
    }
    {
      id = "capture.record";
      label = "Record · focused monitor";
      hint = "Super+Alt+R";
      action = "hypr-record";
      when = "! ${recording}";
    }
    {
      id = "capture.region-record";
      label = "Record · region";
      hint = "select an area";
      action = recordRegion;
      when = "! ${recording}";
    }
    {
      id = "capture.stop";
      label = "Stop recording";
      hint = "save to ~/Videos/Recordings";
      action = "hypr-record";
      when = recording;
    }
    {
      id = "capture.text";
      label = "Text from screen";
      hint = "OCR → clipboard";
      action = captureText;
    }
    {
      id = "capture.qr";
      label = "QR code from screen";
      hint = "decode → clipboard";
      action = captureQr;
    }
    {
      id = "capture.color";
      label = "Color picker";
      hint = "Super+Shift+C";
      action = "hyprpicker -a";
    }

    {
      id = "toggle";
      glyph = "󰔡";
      label = "Toggle";
      hint = "Super+Ctrl+O";
    }
    {
      id = "toggle.night";
      label = "Night light";
      hint = "Super+Ctrl+N";
      action = "auseklis-mode toggle night";
      checked = "auseklis-mode is night";
    }
    {
      id = "toggle.caffeine";
      label = "Caffeine";
      hint = "screen stays on";
      action = "auseklis-mode toggle caffeine";
      checked = "auseklis-mode is caffeine";
    }
    {
      id = "toggle.dnd";
      label = "Do not disturb";
      hint = "notifications";
      action = "auseklis-mode toggle dnd";
      checked = "auseklis-mode is dnd";
    }
    {
      id = "toggle.dictation";
      label = "Dictation";
      hint = "Super+Alt+L";
      action = "auseklis-mode toggle dictation";
      checked = "auseklis-mode is dictation";
    }
    {
      id = "toggle.bar";
      label = "Bar";
      hint = "Super+B";
      action = "pkill -USR1 waybar";
    }

    {
      id = "share";
      glyph = "󰒖";
      label = "Share";
      hint = "clipboard, Taildrop, LocalSend";
    }
    {
      id = "share.clipboard";
      label = "Clipboard history";
      hint = "Super+V";
      action = "cliphist list | ${fuzzel} --prompt='clipboard › ' | cliphist decode | wl-copy";
    }
    {
      id = "share.taildrop";
      label = "Taildrop a file";
      hint = "to a tailnet machine";
      action = "auseklis-tailscale menu";
    }
    {
      id = "share.localsend";
      label = "LocalSend";
      hint = "focus or open";
      action = "auseklis-focus-or-launch '^localsend_app$' localsend_app";
    }

    {
      id = "style";
      glyph = "󰏘";
      label = "Style";
      hint = "wallpaper, night light";
    }
    {
      id = "style.wallpaper";
      label = "Wallpaper";
      hint = "Super+Shift+W";
      action = "hypr-wallpaper-pick";
    }
    {
      id = "style.night";
      label = "Night light";
      hint = "Super+Ctrl+N";
      action = "auseklis-mode toggle night";
      checked = "auseklis-mode is night";
    }

    {
      id = "update";
      glyph = "󰚰";
      label = "Update";
      hint = "pins and flake";
      action = "auseklis-updates menu";
    }
    {
      id = "keys";
      glyph = "󰌌";
      label = "Keybindings";
      hint = "Super+/";
      action = "auseklis-keys";
    }

    {
      id = "system";
      glyph = "⏻";
      label = "System";
      hint = "Super+Escape";
    }
    {
      id = "system.lock";
      label = "Lock";
      action = "hyprlock";
    }
    {
      id = "system.logout";
      label = "Log out";
      action = "${hyprctl} dispatch 'hl.dsp.exit()'";
    }
    {
      id = "system.suspend";
      label = "Suspend";
      action = "systemctl suspend";
    }
    {
      id = "system.reboot";
      label = "Reboot";
      action = "systemctl reboot";
    }
    {
      id = "system.shutdown";
      label = "Shut down";
      action = "systemctl poweroff";
    }
  ];

  menu = pkgs.writeShellScriptBin "auseklis-menu" ''
    MENU=${cfgHome}/auseklis/menu.json
    ROUTE=''${1:-}
    while :; do
      ITEMS=$(${jq} -c --arg r "$ROUTE" \
        '[.[] | select((.id | split(".") | .[:-1] | join(".")) == $r)]' "$MENU")
      # One bash for every guard on this level: prints "index shown checked".
      STATE=$(${jq} -r 'to_entries[] |
        "if \(.value.when // "true"); then w=1; else w=0; fi; " +
        "if \(.value.checked // "false"); then c=1; else c=0; fi; echo \(.key) $w $c"' <<<"$ITEMS" \
        | bash 2>/dev/null)
      LINES=(); MAP=()
      if [ -n "$ROUTE" ]; then LINES+=("‹  back"); MAP+=(back); fi
      while read -r i w c; do
        [ "$w" = 1 ] || continue
        ROW=$(${jq} -r --argjson i "$i" --arg c "$c" '.[$i] |
          "\(.glyph // " ") \(if $c == "1" then "✓ " else "" end)\(.label)" as $l |
          "\($l)\(" " * ([2, 34 - ($l | length)] | max))\(.hint // "")"' <<<"$ITEMS")
        LINES+=("$ROW"); MAP+=("$i")
      done <<<"$STATE"
      PROMPT="auseklis''${ROUTE:+ / ''${ROUTE//./ / }} › "
      SEL=$(printf '%s\n' "''${LINES[@]}" | ${fuzzel} --index --prompt="$PROMPT" --width 52) || exit 0
      PICK=''${MAP[$SEL]}
      if [ "$PICK" = back ]; then
        if [[ "$ROUTE" == *.* ]]; then ROUTE=''${ROUTE%.*}; else ROUTE=""; fi
        continue
      fi
      ID=$(${jq} -r --argjson i "$PICK" '.[$i].id' <<<"$ITEMS")
      ACTION=$(${jq} -r --argjson i "$PICK" '.[$i].action // empty' <<<"$ITEMS")
      if [ -n "$ACTION" ]; then
        setsid -f bash -c "$ACTION" >/dev/null 2>&1
        exit 0
      fi
      ROUTE=$ID
    done
  '';
in
{
  xdg.configFile."auseklis/menu.json".text = builtins.toJSON entries;

  home.packages = [
    menu
    focusOrLaunch
    present
    keys
    tesseract
    pkgs.zbar
  ];
}
