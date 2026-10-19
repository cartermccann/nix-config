{
  config,
  lib,
  pkgs,
  hyprland,
  ...
}:
# The waybar session's live modules: the mode indicators, screen recording,
# Tailscale and the update badge. Each script answers `status` with waybar
# JSON (empty text hides the module) and handles its own clicks; after any
# toggle it sends SIGRTMIN+8 so every indicator refreshes at once instead of
# waiting for its interval.
let
  cfgHome = config.xdg.configHome;
  home = config.home.homeDirectory;
  hyprctl = "${hyprland.packages.${pkgs.stdenv.hostPlatform.system}.hyprland}/bin/hyprctl";
  jq = "${pkgs.jq}/bin/jq";
  fuzzel = "${pkgs.fuzzel}/bin/fuzzel --dmenu --config ${cfgHome}/fuzzel/hypr.ini";
  notify = "${pkgs.libnotify}/bin/notify-send";
  # waybar runs as .waybar-wrapped (nixpkgs wrapper), so -x waybar never matches;
  # a bare substring does (and stays under pgrep's 15-char pattern warning).
  refresh = "${pkgs.procps}/bin/pkill -RTMIN+8 waybar";
  dictationState = "${home}/.local/state/parakeet-dictation";

  # Screen recording of the focused monitor, toggled. gpu-screen-recorder
  # comes from programs.gpu-screen-recorder (modules/media.nix), whose setcap
  # wrapper is what lets monitor capture run without a prompt.
  hyprRecord = pkgs.writeShellScriptBin "hypr-record" ''
    if ${pkgs.procps}/bin/pgrep -f "(^|/)gpu-screen-recorder( |$)" >/dev/null; then
      ${pkgs.procps}/bin/pkill -INT -f "(^|/)gpu-screen-recorder( |$)"
      ${notify} -a hypr-record "Recording saved" "$HOME/Videos/Recordings"
    else
      mkdir -p ~/Videos/Recordings
      MON=$(${hyprctl} monitors -j | ${jq} -r '.[] | select(.focused) | .name')
      OUT=~/Videos/Recordings/$(date +%Y-%m-%d_%H-%M-%S).mp4
      gpu-screen-recorder -w "$MON" -f 60 -a default_output -o "$OUT" >/dev/null 2>&1 &
      disown
    fi
    sleep 0.3
    ${refresh}
  '';

  # ouranos-mode status <mode> on|peek   waybar JSON for one indicator
  # ouranos-mode toggle <mode>            flip it
  # ouranos-mode is <mode>                exit 0 if on
  #
  # `on` modules show only while active; `peek` modules show only while
  # inactive and live in the hover drawer, so the bar stays quiet until a mode
  # is on but every mode is still one hover and one click away.
  ouranosMode = pkgs.writeShellScriptBin "ouranos-mode" ''
    CAFF="''${XDG_RUNTIME_DIR:-/tmp}/ouranos-caffeine.pid"
    alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

    active() {
      case "$1" in
        dictation) alive ${dictationState}/recorder.pid || alive ${dictationState}/batch-recorder.pid ;;
        record)    ${pkgs.procps}/bin/pgrep -f "(^|/)gpu-screen-recorder( |$)" >/dev/null ;;
        night)     T=$(${hyprctl} hyprsunset temperature 2>/dev/null | tr -dc 0-9); [ -n "$T" ] && [ "$T" -lt 6000 ] ;;
        caffeine)  alive "$CAFF" ;;
        dnd)       [ "$(${pkgs.swaynotificationcenter}/bin/swaync-client -D 2>/dev/null)" = true ] ;;
        heavy)     systemctl is-active --quiet podman-llama-heavy ;;
        *) return 1 ;;
      esac
    }

    glyph() {
      case "$1" in
        dictation) echo "󰍬" ;; record) echo "󰑊" ;; night) echo "󰖔" ;;
        caffeine) echo "󰅶" ;; dnd) echo "󰂛" ;; heavy) echo "󰍛" ;;
      esac
    }
    label() {
      case "$1" in
        dictation) echo "Dictation · Super+Alt+L" ;; record) echo "Screen recording · Super+Alt+R" ;;
        night) echo "Night light · Super+Ctrl+N" ;; caffeine) echo "Caffeine: screen stays on" ;;
        dnd) echo "Do not disturb" ;; heavy) echo "Heavy mode: llama-server owns the GPU" ;;
      esac
    }

    case "$1" in
      # Exit status only: lets the Ouranos menu tick active modes.
      is) active "$2" ;;
      status)
        MODE=$2; VIEW=$3
        if active "$MODE"; then ON=1; else ON=0; fi
        CLASS=on; [ "$MODE" = dictation ] || [ "$MODE" = record ] && CLASS=live
        if [ "$VIEW" = on ] && [ $ON = 1 ]; then
          printf '{"text":"%s","tooltip":"%s","class":"%s"}\n' "$(glyph "$MODE")" "$(label "$MODE")" "$CLASS"
        elif [ "$VIEW" = peek ] && [ $ON = 0 ]; then
          printf '{"text":"%s","tooltip":"%s (off)","class":"peek"}\n' "$(glyph "$MODE")" "$(label "$MODE")"
        else
          echo '{"text":""}'
        fi
        ;;
      toggle)
        case "$2" in
          dictation) ~/.local/bin/toggle-dictation-batch.sh ;;
          record)    hypr-record ;;
          night)     hypr-night-toggle ;;
          caffeine)
            if alive "$CAFF"; then
              kill "$(cat "$CAFF")"; rm -f "$CAFF"
            else
              systemd-inhibit --what=idle --who=ouranos --why="caffeine (bar)" sleep infinity &
              echo $! > "$CAFF"; disown
            fi ;;
          dnd)   ${pkgs.swaynotificationcenter}/bin/swaync-client -d -sw >/dev/null ;;
          # Needs sudo (podman units), so it opens a terminal instead of
          # failing silently from a click.
          heavy)
            if active heavy; then CMD=heavy-stop; else CMD=heavy; fi
            ghostty --class=TUI.float -e fish -ic "$CMD; read -P 'enter to close '" & ;;
        esac
        sleep 0.2
        ${refresh}
        ;;
    esac
  '';

  # ouranos-tailscale status | menu | toggle. Works unprivileged because
  # tailscale's operator is set to this user.
  ouranosTailscale = pkgs.writeShellScriptBin "ouranos-tailscale" ''
    TS=${pkgs.tailscale}/bin/tailscale
    case "$1" in
      status)
        J=$($TS status --json 2>/dev/null) || { echo '{"text":"TS OFF","class":"off","tooltip":"tailscaled unreachable"}'; exit; }
        STATE=$(printf '%s' "$J" | ${jq} -r .BackendState)
        if [ "$STATE" != Running ]; then
          echo '{"text":"TS OFF","class":"off","tooltip":"Tailscale is down · right-click to connect"}'; exit
        fi
        # jq builds the JSON so hostnames with quotes can't break it. Online
        # peers are listed; offline ones are only counted (a tailnet collects
        # dozens of dormant devices).
        printf '%s' "$J" | ${jq} -c '
          [(.Peer // {})[]] as $p
          | ([$p[] | select(.ExitNode) | .HostName][0]) as $exit
          | ([$p[] | select(.Online) | "● \(.HostName)"] | sort) as $on
          | ([$p[] | select(.Online | not)] | length) as $off
          | (if any($p[]; .HostName == "atlas" and .Online) then "atlas: online" else "atlas: offline" end) as $atlas
          | { text: (if $exit then "TS EXIT" else "TS ON" end),
              class: (if $exit then "exit" else "on" end),
              # atlas is the other machine this flake builds; name it outright.
              tooltip: ((if $exit then ["exit node: \($exit)"] else [] end)
                        + [$atlas, ""] + $on + ["\($off) offline"] | join("\n")) }'
        ;;
      toggle)
        if [ "$($TS status --json 2>/dev/null | ${jq} -r .BackendState)" = Running ]; then $TS down; else $TS up; fi
        ;;
      menu)
        J=$($TS status --json 2>/dev/null) || exit 1
        SELF=$(printf '%s' "$J" | ${jq} -r .Self.HostName)
        EXIT=$(printf '%s' "$J" | ${jq} -r '[(.Peer // {})[] | select(.ExitNode) | .HostName][0] // "none"')
        PEERS=$(printf '%s' "$J" | ${jq} -r '[(.Peer // {})[]] | sort_by((.Online | not), .HostName) | .[] | "\(if .Online then "●" else "○" end) \(.HostName)"')
        PICK=$(printf 'Exit node: %s\nCopy IP · %s\nSend file (Taildrop)\n%s\nDisconnect\n' "$EXIT" "$SELF" "$PEERS" \
          | ${fuzzel} --prompt="tailscale › ")
        case "$PICK" in
          "Exit node:"*)
            NODE=$( (echo none; printf '%s' "$J" | ${jq} -r '(.Peer // {})[] | select(.ExitNodeOption and .Online) | .HostName') \
              | ${fuzzel} --prompt="exit node › ")
            [ -n "$NODE" ] || exit 0
            [ "$NODE" = none ] && NODE=""
            $TS set --exit-node="$NODE" && ${notify} -a tailscale "Exit node" "''${NODE:-none}" ;;
          "Copy IP"*)
            $TS ip -4 | head -1 | ${pkgs.wl-clipboard}/bin/wl-copy && ${notify} -a tailscale "Copied" "$SELF IP" ;;
          "Send file"*)
            TO=$(printf '%s' "$J" | ${jq} -r '(.Peer // {})[] | select(.Online) | .HostName' | ${fuzzel} --prompt="send to › ")
            [ -n "$TO" ] || exit 0
            FILE=$(${pkgs.fd}/bin/fd -t f . ~/Downloads ~/Pictures/Screenshots ~/Videos/Recordings --changed-within 30d 2>/dev/null \
              | head -200 | ${fuzzel} --prompt="file › ")
            [ -n "$FILE" ] || exit 0
            $TS file cp "$FILE" "$TO:" && ${notify} -a tailscale "Sent to $TO" "$(basename "$FILE")" ;;
          "● "*|"○ "*)
            HOST=''${PICK#* }
            IP=$(printf '%s' "$J" | ${jq} -r --arg h "$HOST" '(.Peer // {})[] | select(.HostName == $h) | .TailscaleIPs[0]')
            printf '%s' "$IP" | ${pkgs.wl-clipboard}/bin/wl-copy && ${notify} -a tailscale "Copied $HOST" "$IP" ;;
          Disconnect) $TS down ;;
        esac
        ;;
    esac
  '';

  # jq filter: newest stable v1.x tag from the OpenCode releases list. 2.x
  # releases share the repo, so releases/latest would be the v2 line.
  opencode1Latest = ''[.[] | select(.draft == false and .prerelease == false) | .tag_name | select(startswith("v1.")) | ltrimstr("v")] | sort_by(split(".") | map(tonumber? // 0)) | last // empty'';

  # ouranos-updates check | status | menu. `check` (daily timer) compares the
  # hand-pinned apps against upstream and the flake's nixpkgs age, writing a
  # cache the bar reads; `status` never touches the network.
  ouranosUpdates = pkgs.writeShellScriptBin "ouranos-updates" ''
    REPO=${home}/nix-config
    CACHE=''${XDG_CACHE_HOME:-$HOME/.cache}/ouranos/updates
    mkdir -p "$(dirname "$CACHE")"
    CURL="${pkgs.curl}/bin/curl -fsSL --max-time 20"
    case "$1" in
      check)
        OUT=$(mktemp)
        add() { [ -n "$3" ] && [ "$3" != null ] && [ "$2" != "$3" ] && echo "$1  $2 → $3" >> "$OUT"; }
        add "Codex CLI" "$(${jq} -r .version $REPO/pkgs/codex/sources.json)" \
          "$($CURL https://api.github.com/repos/openai/codex/releases/latest | ${jq} -r .tag_name | sed 's/^rust-v//')"
        add "T3 Code" "$(${jq} -r .version "$REPO/pkgs/t3code/sources.json")" \
          "$($CURL https://api.github.com/repos/pingdotgg/t3code/releases/latest | ${jq} -r 'select(.draft == false and .prerelease == false) | .tag_name | ltrimstr("v")')"
        add "OpenCode 1" "$(${jq} -r .version $REPO/pkgs/opencode1/sources.json)" \
          "$($CURL 'https://api.github.com/repos/anomalyco/opencode/releases?per_page=50' | ${jq} -r '${opencode1Latest}')"
        add "Cursor" "$(${jq} -r .version $REPO/pkgs/code-cursor/sources.json)" \
          "$($CURL 'https://www.cursor.com/api/download?platform=linux-x64&releaseTrack=stable' | ${jq} -r .version)"
        add "Granola" "$(grep -oP 'version = "\K[0-9.]+' $REPO/pkgs/granola/default.nix | head -1)" \
          "$(${pkgs.curl}/bin/curl -sI --max-time 20 https://api.granola.ai/v1/download-latest | grep -i '^location' | grep -oP '/\K[0-9]+\.[0-9]+\.[0-9]+(?=/)')"
        add "grok-cli" "$(grep -oP 'version = "\K[0-9.]+' $REPO/pkgs/grok-cli/default.nix | head -1)" \
          "$($CURL https://storage.googleapis.com/grok-build-public-artifacts/cli/stable | tr -d '[:space:]')"
        # Staleness is when the lock was last refreshed, not how old each
        # input's commit is: an input already at upstream HEAD can still carry
        # a months-old commit date. Two weeks without `nix flake update` counts.
        LOCKED=$(${pkgs.git}/bin/git -C $REPO log -1 --format=%ct -- flake.lock 2>/dev/null)
        if [ -n "$LOCKED" ]; then
          AGE=$(( ( $(date +%s) - LOCKED ) / 86400 ))
          [ "$AGE" -ge 14 ] && echo "Flake inputs  lock last updated $AGE days ago" >> "$OUT"
        fi
        mv "$OUT" "$CACHE"
        ${refresh}
        ;;
      status)
        N=$(grep -c . "$CACHE" 2>/dev/null); N=''${N:-0}
        if [ "$N" -gt 0 ]; then
          ${jq} -cRs --arg n "$N" '{text: "󰁝 UPD \($n)", tooltip: rtrimstr("\n"), class: "pending"}' "$CACHE"
        else
          echo '{"text":""}'
        fi
        ;;
      menu)
        PICK=$( (cat "$CACHE" 2>/dev/null; echo "Update everything + test build"; echo "Re-check now") | ${fuzzel} --prompt="updates › ")
        case "$PICK" in
          "Update everything + test build") ouranos-present ouranos-update & ;;
          "Re-check now") "$0" check ;;
        esac
        ;;
    esac
  '';

  # ouranos-update: the one-click update, run from the badge or the menu.
  # Omarchy's plugin-update loop (fetch -> diff -> build -> rollback) for this
  # flake: codex first (codex-update owns its pins and its own test build),
  # then the hand-pinned apps, then the flake inputs, then one test build.
  # A failed build rolls the lock back and retries with the pin bumps alone,
  # so one broken input doesn't throw away everything else.
  ouranosUpdate = pkgs.writeShellScriptBin "ouranos-update" ''
    set -u
    REPO=${home}/nix-config
    cd "$REPO" || exit 1
    CURL="${pkgs.curl}/bin/curl -fsSL --max-time 30"
    GUM=${pkgs.gum}/bin/gum
    CHANGED=()
    say() { printf '\e[34m›\e[0m %s\n' "$*"; }
    edited() { ! git diff --quiet -- "$1"; }
    prefetch() { nix store prefetch-file --json "$1" 2>/dev/null | ${jq} -r .hash; }
    setline() { # setline <file> <key> <value>: replace the first `  key = "..."` line
      sed -i "0,/^  $2 = \".*\";/s||  $2 = \"$3\";|" "$1"
    }

    say "codex"
    codex-update --cli-only || say "codex-update failed or had nothing to do; continuing"

    T3_CURRENT=$(${jq} -r .version pkgs/t3code/sources.json)
    T3_LATEST=$($CURL https://api.github.com/repos/pingdotgg/t3code/releases/latest | ${jq} -r 'select(.draft == false and .prerelease == false) | .tag_name | ltrimstr("v")')
    if [ -z "$T3_LATEST" ] || [ "$T3_LATEST" = null ]; then
      say "T3 Code: release check failed"
    elif [ "$T3_CURRENT" = "$T3_LATEST" ]; then
      say "T3 Code $T3_CURRENT ✓"
    else
      say "T3 Code $T3_CURRENT → $T3_LATEST available; refresh pkgs/t3code source and dependency hashes"
    fi

    bump() { # bump <name> <file> <current> <latest> <url>
      local name=$1 file=$2 cur=$3 new=$4 url=$5
      if [ -z "$new" ] || [ "$new" = null ] || [ "$new" = "$cur" ]; then say "$name $cur ✓"; return; fi
      if edited "$file"; then say "$name: $file has local edits, skipping"; return; fi
      local h; h=$(prefetch "$url") || true
      if [ -z "$h" ]; then say "$name: download failed, skipping"; return; fi
      case "$file" in
        *.json) ${jq} --arg v "$new" --arg u "$url" --arg h "$h" \
                  '.version = $v | .sources["x86_64-linux"].url = $u | .sources["x86_64-linux"].hash = $h' \
                  "$file" > "$file.tmp" && mv "$file.tmp" "$file" ;;
        *) setline "$file" version "$new"; sed -i "0,/hash = \"sha256-[^\"]*\"/s||hash = \"$h\"|" "$file" ;;
      esac
      CHANGED+=("$file"); say "$name $cur → $new"
    }

    CJ=$($CURL 'https://www.cursor.com/api/download?platform=linux-x64&releaseTrack=stable' || echo '{}')
    bump Cursor pkgs/code-cursor/sources.json "$(${jq} -r .version pkgs/code-cursor/sources.json)" \
      "$(${jq} -r .version <<<"$CJ")" "$(${jq} -r .downloadUrl <<<"$CJ")"
    GV=$(${pkgs.curl}/bin/curl -sI --max-time 30 https://api.granola.ai/v1/download-latest | grep -i '^location' | grep -oP '/\K[0-9]+\.[0-9]+\.[0-9]+(?=/)')
    bump Granola pkgs/granola/default.nix "$(grep -oP '^  version = "\K[0-9.]+' pkgs/granola/default.nix)" \
      "$GV" "https://dr2v7l5emb758.cloudfront.net/$GV/Granola-$GV-mac-universal.dmg"
    XV=$($CURL https://storage.googleapis.com/grok-build-public-artifacts/cli/stable | tr -d '[:space:]')
    bump grok-cli pkgs/grok-cli/default.nix "$(grep -oP '^  version = "\K[0-9.]+' pkgs/grok-cli/default.nix)" \
      "$XV" "https://storage.googleapis.com/grok-build-public-artifacts/cli/grok-$XV-linux-x86_64"

    # OpenCode 1 (T3's provider binary) is a source build, so a bump needs the
    # tag's src hash and, when its bun lockfile moved, a new node_modules hash.
    # Test-build it alone and take node_modules' hash from the mismatch error.
    O1=pkgs/opencode1/sources.json
    O1_CUR=$(${jq} -r .version $O1)
    O1_NEW=$($CURL 'https://api.github.com/repos/anomalyco/opencode/releases?per_page=50' | ${jq} -r '${opencode1Latest}')
    o1build() {
      local drv
      drv=$(nix eval --raw "$REPO#nixosConfigurations.$(uname -n).config.home-manager.users.${config.home.username}.home.packages" \
        --apply 'ps: (builtins.head (builtins.filter (p: (p.name or "") == "opencode1") ps)).drvPath') || return 1
      nix build --no-link "$drv^*" 2>&1
    }
    if [ -z "$O1_NEW" ] || [ "$O1_NEW" = "$O1_CUR" ] || [ "$(printf '%s\n' "$O1_CUR" "$O1_NEW" | sort -V | tail -n1)" != "$O1_NEW" ]; then
      say "OpenCode 1 $O1_CUR ✓"
    elif edited "$O1"; then
      say "OpenCode 1: $O1 has local edits, skipping"
    else
      O1_SRC=$(nix store prefetch-file --unpack --json "https://github.com/anomalyco/opencode/archive/refs/tags/v$O1_NEW.tar.gz" 2>/dev/null | ${jq} -r .hash)
      if [ -z "$O1_SRC" ]; then
        say "OpenCode 1: source download failed, skipping"
      else
        ${jq} --arg v "$O1_NEW" --arg h "$O1_SRC" '.version = $v | .srcHash = $h' $O1 > $O1.tmp && mv $O1.tmp $O1
        say "OpenCode 1 $O1_CUR → $O1_NEW; test-building"
        if ! O1_OUT=$(o1build); then
          O1_NM=$(grep -A3 'hash mismatch.*node_modules' <<<"$O1_OUT" | grep -oP 'got:\s+\Ksha256-\S+' | head -n1)
          if [ -n "$O1_NM" ]; then
            ${jq} --arg h "$O1_NM" '.nodeModulesHash = $h' $O1 > $O1.tmp && mv $O1.tmp $O1
            say "OpenCode 1: node_modules hash changed; rebuilding"
            O1_OUT=$(o1build) || O1_NM=
          fi
          if [ -z "$O1_NM" ]; then
            tail -n 15 <<<"$O1_OUT"
            git checkout -- $O1
            say "OpenCode 1 $O1_NEW failed to build; kept $O1_CUR"
          fi
        fi
        edited "$O1" && CHANGED+=("$O1")
      fi
    fi

    say "flake inputs"
    cp flake.lock /tmp/ouranos-update-flake.lock
    nix flake update 2>&1 | grep -E "Updated input '[^/']+'" || say "inputs already current"

    git --no-pager diff --stat
    build() { nh os build "$REPO" --no-nom 2>&1 | tail -n 25; return "''${PIPESTATUS[0]}"; }
    if build; then
      say "test build passed"
    else
      say "build failed with the new inputs; retrying with the app pins only"
      cp /tmp/ouranos-update-flake.lock flake.lock
      if ! build; then
        say "build failed with the app pins too; restoring everything"
        [ ''${#CHANGED[@]} -gt 0 ] && git checkout -- "''${CHANGED[@]}"
        ouranos-updates check
        exit 1
      fi
      say "test build passed without the input refresh (an input is broken upstream)"
    fi
    ouranos-updates check
    if $GUM confirm "Apply now (nrs)?"; then nh os switch "$REPO"; fi
    if ! git diff --quiet && $GUM confirm "Commit this update?"; then
      git add -A flake.lock pkgs/code-cursor pkgs/granola pkgs/grok-cli pkgs/opencode1 pkgs/codex flake.nix
      git commit -q -m "Update pinned apps and flake inputs" && git log --oneline -1
    fi
  '';
in
{
  home.packages = [
    ouranosUpdate
    hyprRecord
    ouranosMode
    ouranosTailscale
    ouranosUpdates
  ];

  systemd.user.services.ouranos-updates = {
    Unit.Description = "Check pinned apps and flake inputs for updates (waybar badge)";
    Service = {
      Type = "oneshot";
      Environment = "PATH=${
        lib.makeBinPath [
          pkgs.coreutils
          pkgs.gnugrep
          pkgs.gnused
          pkgs.procps
        ]
      }";
      ExecStart = "${ouranosUpdates}/bin/ouranos-updates check";
    };
  };
  systemd.user.timers.ouranos-updates = {
    Unit.Description = "Daily update check for the waybar badge";
    Timer = {
      OnCalendar = "daily";
      OnStartupSec = "3min";
      Persistent = true;
    };
    Install.WantedBy = [ "timers.target" ];
  };
}
