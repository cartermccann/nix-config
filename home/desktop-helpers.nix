{ pkgs, ... }:
# Helper scripts the desktop sessions share, whichever compositor is running:
# rice-dashboard is the tmux btop/cava/yazi dashboard that Super+Ctrl+D opens in
# ghostty from the Hyprland sessions and the Niri one alike. (It used to live in
# home/niri.nix, which meant the Hyprland sessions depended on a Niri module.)
let
  riceDashboard = pkgs.writeShellScriptBin "rice-dashboard" ''
    ${pkgs.tmux}/bin/tmux new-session -d -s rice -x "$(tput cols)" -y "$(tput lines)"
    ${pkgs.tmux}/bin/tmux send-keys -t rice 'btop' Enter
    ${pkgs.tmux}/bin/tmux split-window -h -t rice -p 40
    ${pkgs.tmux}/bin/tmux send-keys -t rice 'cava' Enter
    ${pkgs.tmux}/bin/tmux split-window -v -t rice -p 50
    ${pkgs.tmux}/bin/tmux send-keys -t rice 'yazi' Enter
    ${pkgs.tmux}/bin/tmux select-pane -t rice:1.1
    ${pkgs.tmux}/bin/tmux attach -t rice
  '';
in
{
  home.packages = [ riceDashboard ];
}
