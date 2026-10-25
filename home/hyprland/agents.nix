{
  config,
  pkgs,
  ...
}:
# Agent cockpit: the waybar module and the switcher (Super+Shift+A) for every
# coding agent running under herdr. herdr already recognises the agents in its
# panes and classifies them (idle, working, blocked, done), so this reads
# `herdr agent list` rather than inventing a state channel. Omarchy's agents
# panel only tracks usage; the live view is new here.
#
# "Waiting" means the agent needs you: blocked on an approval or question, or
# done with a turn nobody has looked at yet.
let
  cfgHome = config.xdg.configHome;
  jq = "${pkgs.jq}/bin/jq";
  fuzzel = "${pkgs.fuzzel}/bin/fuzzel --dmenu --config ${cfgHome}/fuzzel/hypr.ini";

  agents = pkgs.writeShellScriptBin "auseklis-agents" ''
    LIST=$(herdr agent list 2>/dev/null | ${jq} -c '.result.agents // []') || LIST='[]'
    [ -n "$LIST" ] || LIST='[]'

    case "''${1:-menu}" in
      status)
        ${jq} -c '
          (map(select(.agent_status == "blocked" or .agent_status == "done")) | length) as $w
          | if length == 0 then {text: ""}
            else {
              text: ("󰚩 AGENTS \(length)" + (if $w > 0 then " · \($w) WAITING" else "" end)),
              class: (if $w > 0 then "waiting" else "on" end),
              tooltip: (map("\(.agent) · \(.cwd | split("/") | last) · \(.agent_status)") | join("\n"))
            } end' <<<"$LIST"
        ;;

      menu)
        # Waiting first, then working, then the rest.
        SORTED=$(${jq} -c 'sort_by({blocked: 0, done: 1, working: 2, idle: 3}[.agent_status] // 4)' <<<"$LIST")
        ROWS=$(${jq} -r '.[] |
          ({blocked: "needs approval", done: "done · unseen", working: "working",
            idle: "idle", unknown: "?"}[.agent_status] // .agent_status) as $s
          | "\(.agent)\t\(.cwd | split("/") | last)\t\($s)"' <<<"$SORTED" \
          | while IFS=$'\t' read -r a p s; do printf '%-10s %-30s %s\n' "$a" "$p" "$s"; done)
        N=$(${jq} length <<<"$SORTED")
        EXTRA=$'+  new Claude Code in a project\n+  new Codex in a project\n󰆍  open herdr'
        pgrep -f codex-desktop >/dev/null && EXTRA+=$'\n󰚩  Codex Desktop'
        SEL=$(printf '%s\n%s\n' "$ROWS" "$EXTRA" | sed '/^$/d' \
          | ${fuzzel} --index --prompt="agents › " --width 64) || exit 0
        if [ "$SEL" -lt "$N" ]; then
          auseklis-herdr
          herdr agent focus "$(${jq} -r --argjson i "$SEL" '.[$i].pane_id' <<<"$SORTED")" >/dev/null
          exit 0
        fi
        case $((SEL - N)) in
          0) KIND=claude ;;
          1) KIND=codex ;;
          2) exec auseklis-herdr ;;
          3) exec auseklis-focus-or-launch '^codex-desktop$' codex-desktop-guard ;;
        esac
        DIR=$(PROMPT="$KIND in › " auseklis-project --pick) || exit 0
        WS=$(herdr workspace list | ${jq} -r --arg l "$(basename "$DIR")" \
          '[.result.workspaces[] | select(.label == $l)][0].workspace_id // empty')
        if [ -z "$WS" ]; then
          # No workspace yet: the project launcher's layout already starts both.
          exec auseklis-project --open "$DIR"
        fi
        # Existing workspace: a new pane beside its first one, running the agent.
        FIRST=$(herdr pane list --workspace "$WS" | ${jq} -r '.result.panes[0].pane_id')
        NEW=$(herdr pane split "$FIRST" --direction right --cwd "$DIR" --no-focus | ${jq} -r .result.pane.pane_id)
        herdr pane run "$NEW" "$KIND" >/dev/null
        auseklis-herdr
        herdr workspace focus "$WS" >/dev/null
        ;;
    esac
  '';
in
{
  home.packages = [ agents ];
}
