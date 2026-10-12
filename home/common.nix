{
  config,
  pkgs,
  user,
  ...
}:

{
  imports = [
    ./shell.nix
    ./fastfetch.nix
    ./git.nix
    ./neovim.nix
    ./niri.nix
    ./niri-noctalia.nix
    ./hyprland
    ./crash-watch.nix
    ./tmux.nix
    ./dictation.nix
    ./tools.nix
    ./qmd.nix
    ./brain.nix
    ./webctx-tunnel.nix
    ./cliproxyapi.nix
    ./micro-bridge.nix
    ./cursor-worker.nix
    # Claude Code loops retired: subscription is gone. Keep the Nix files
    # so they can be re-imported; do not schedule `claude`.
    ./codex-self-improve-loop.nix
    ./codex-desktop.nix
    ./mcp-reaper.nix
    ./ghostty.nix
    ./spotify.nix
  ];

  home.username = user;
  home.homeDirectory = "/home/${user}";

  # XDG user directories — keep the ones we use, redirect the rest
  xdg.userDirs = {
    enable = true;
    # Keep exporting XDG_*_DIR (the pre-26.05 default) for scripts that read them.
    setSessionVariables = true;
    desktop = "$HOME/Desktop";
    documents = "$HOME/Documents";
    download = "$HOME/Downloads";
    pictures = "$HOME/Pictures";
    videos = "$HOME/Videos";
    music = "$HOME/.local/share/Music";
    publicShare = "$HOME/.local/share/Public";
    templates = "$HOME/.local/share/Templates";
  };

  gtk.iconTheme = {
    name = "Papirus-Dark";
    package = pkgs.papirus-icon-theme;
  };

  dconf.settings = {
    "org/gnome/desktop/interface" = {
      color-scheme = "prefer-dark";
    };
  };

  home.file."wallpapers".source = ../wallpaper;

  # Figma (Chrome web app)
  xdg.desktopEntries.figma = {
    name = "Figma";
    comment = "Figma Design";
    exec = "google-chrome-stable --app=https://figma.com/ %U";
    terminal = false;
    icon = ./icons/Figma.png;
    categories = [
      "Graphics"
      "Development"
    ];
  };

  # Comcreate Desktop (flake package ships the binary, but not a .desktop file)
  xdg.desktopEntries.comcreate-desktop = {
    name = "Comcreate Dashboard";
    genericName = "Client Delivery Dashboard";
    comment = "Comcreate client delivery dashboard, reports, and portal";
    exec = "comcreate-desktop %U";
    terminal = false;
    icon = ./icons/Comcreate.png;
    categories = [ "Office" ];
    mimeType = [ "x-scheme-handler/comcreate" ];
  };

  # Custom TUI launchers
  xdg.desktopEntries.disk-usage = {
    name = "Disk Usage";
    comment = "View disk usage with dust";
    exec = toString (
      pkgs.writeShellScript "disk-usage-tui" ''
        ghostty --class=TUI.float -e bash -c 'dust -r; read -n 1 -s'
      ''
    );
    terminal = false;
    icon = "disk-usage-analyzer";
    categories = [ "System" ];
  };

  xdg.desktopEntries.docker = {
    name = "Docker";
    comment = "Docker";
    exec = "ghostty --class=TUI.tile -e lazydocker";
    terminal = false;
    icon = "docker-desktop";
    categories = [ "System" ];
  };

  home.stateVersion = "25.11";
}
