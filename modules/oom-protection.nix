{
  config,
  lib,
  pkgs,
  ...
}:

{
  zramSwap = {
    enable = true;
    memoryPercent = 50;
    algorithm = "zstd";
    priority = 100;
  };

  boot.kernel.sysctl = {
    "vm.swappiness" = 180;
    "vm.watermark_boost_factor" = 0;
    "vm.watermark_scale_factor" = 125;
    "vm.page-cluster" = 0;
  };

  services.earlyoom = {
    enable = true;
    freeMemThreshold = 5;
    freeMemKillThreshold = 3;
    freeSwapThreshold = 10;
    freeSwapKillThreshold = 5;
    # Kill the largest process, not the highest oom_score. Ranked by oom_score,
    # the +300 prefer bonus (plus Chrome's own oom_score_adj) let ~400 tiny
    # renderers/node workers outrank a ~40G hog on 2026-10-06; the box froze.
    # Under --sort-by-rss, prefer/avoid shift RSS by +/-3GiB instead.
    extraArgs = [
      "--sort-by-rss"
      "--prefer"
      "^(2\\.1\\.150|claude|node|next-server.*|bfs|chromium|chrome|firefox|zen|ffmpeg|HandBrake)$"
      "--avoid"
      "^(systemd|systemd-.*|niri|\\.Hyprland-wrapp|Xwayland|wireplumber|pipewire|pipewire-pulse|sshd|dbus-daemon|gnome-keyring|gpg-agent)$"
    ];
  };

  systemd.oomd = {
    enable = true;
    enableUserSlices = true;
    enableRootSlice = true;
    enableSystemSlice = false;
  };

  # earlyoom / oomd kill with SIGKILL, which never dumps core. User-session
  # crashes that *do* dump are followed by home/crash-watch.nix (journal
  # MESSAGE_ID fc2e22bc6ee647b6b90729ab34a250b1) and never toasted as OOM.
  systemd.coredump.enable = true;
}
