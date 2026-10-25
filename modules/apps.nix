{
  config,
  lib,
  pkgs,
  pkgs-unstable,
  zen-browser,
  helium,
  claude-desktop,
  user,
  ...
}:

let
  waylandFlags = [
    "--ozone-platform-hint=auto"
    "--enable-features=TouchpadOverscrollHistoryNavigation,WebRTCPipeWireCapturer"
  ];

  googleChromeWrapped = pkgs.google-chrome.override {
    commandLineArgs = waylandFlags ++ [
      "--disable-accelerated-video-decode"
      "--disable-gpu-video-decoder"
      "--oauth2-client-id=77185425430.apps.googleusercontent.com"
      "--oauth2-client-secret=OTJgUOQcT7lO7GsGZq2G4IlT"
    ];
  };
  # `1password-mcp` on PATH for MCP clients (Cursor, Claude Code, Codex): exec
  # the setgid copy under /opt so /proc/<pid>/exe is the file the app verifies.
  onePasswordMcp = pkgs.writeShellScriptBin "1password-mcp" ''
    exec /opt/1Password/1password-mcp "$@"
  '';
in
{
  programs._1password = {
    enable = true;
    package = pkgs-unstable._1password-cli;
  };
  # 1Password's MCP server lives inside the desktop app. Before accepting a
  # connection the app checks (a) the peer's effective GID is `onepassword-mcp`
  # and (b) the server BINARY FILE is group onepassword-mcp with the setgid bit
  # ("parent process verification failed: BinaryPermissions"). A NixOS
  # security wrapper satisfies (a) but not (b): it execs the immutable store
  # file, which is root:root 0555. So mirror upstream's after-install.sh
  # exactly: a real copy at /opt/1Password/1password-mcp, group
  # onepassword-mcp, mode 2755, refreshed on every activation (C+). The group
  # gid must be > 1000 or the app rejects it as "invalid group" (nixpkgs pins
  # onepassword/onepassword-cli to 31001/31002 for the same reason).
  # 2026-09-21, kronos.
  users.groups.onepassword-mcp.gid = 31003;
  systemd.tmpfiles.rules = [
    "d  /opt/1Password 0755 root root -"
    "C+ /opt/1Password/1password-mcp 2755 root onepassword-mcp - ${config.programs._1password-gui.package}/share/1password/1password-mcp"
    "z  /opt/1Password/1password-mcp 2755 root onepassword-mcp -"
    # The MCP binary also hardcodes /opt/1Password/1password as the app it
    # would launch when the desktop app is not running.
    "L+ /opt/1Password/1password - - - - ${config.programs._1password-gui.package}/bin/1password"
  ];
  programs._1password-gui = {
    enable = true;
    package = pkgs-unstable._1password-gui;
    polkitPolicyOwners = [ "cjm" ];
  };

  environment.systemPackages =
    with pkgs;
    [
      onePasswordMcp

      # Browsers
      googleChromeWrapped
      floorp-bin
      helium.packages.${pkgs.stdenv.hostPlatform.system}.default

      # Communication
      slack
      pkgs-unstable.beeper

      # Utilities
      localsend # local file sharing
      nautilus # file manager
      gnome-disk-utility
      gnome-calculator
      fastfetch
      inxi
      blanket
      statix # nix linter

      #Graphic editor
      inkscape
      lsd
      # Notes
      obsidian

      #code editor
      # pinned ahead of nixpkgs code-cursor (built with nixpkgs' buildVscode)
      (callPackage ../pkgs/code-cursor { })
      cursor-cli
      # AI
      claude-desktop.packages.${pkgs.stdenv.hostPlatform.system}.claude-desktop-fhs
    ]
    # Zen: from home-manager where home/zen.nix manages it (perkons, so Stylix
    # can theme the profile), as a system package everywhere else. Installing
    # both put two copies in the closure.
    ++ lib.optional (
      !(config.home-manager.users.${user}.programs.zen-browser.enable or false)
    ) zen-browser.packages.${pkgs.stdenv.hostPlatform.system}.default;
}
