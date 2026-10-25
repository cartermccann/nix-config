{
  config,
  lib,
  pkgs,
  ...
}:

# Shared Wayland base for EVERY session on this host, not a niri-only module
# (it was called desktop-niri.nix, which badly undersold it). Owns the shared
# display-manager selection, XDG portals, polkit, gnome-keyring, and the terminal /
# launcher / screenshot / clipboard / wallpaper packages that the Hyprland
# sessions depend on just as much as the niri one. desktop-hyprland.nix and
# desktop-niri-noctalia.nix layer session-specific bits on top of this.
let
  # The Ly greeter's monogram animation and slash-pair language file.
  lyAuseklis = pkgs.callPackage ../pkgs/ly-auseklis { };
  pal = import ../lib/palette.nix;

  # Ly colours are 0x00RRGGBB. The most significant byte is a *styling* flag,
  # not alpha, so a plain colour keeps it at 00. pal.raw.* is the palette slot
  # without its leading '#'.
  lyColor = raw: "0x00${raw}";

  # Both Ly and SDDM can read a flat directory of .desktop files. Start from
  # the one NixOS generates from sessionPackages, then drop unintentional tiles.
  #
  # Why filter here instead of at the source: programs.niri.enable hard-sets
  # `services.displayManager.sessionPackages = [ cfg.package ]` (niri-flake
  # flake.nix:496), and NixOS rejects a session package whose
  # passthru.providedSessions is empty, so the tile cannot be removed by
  # overriding the package without also rebuilding niri from source. Both
  # display managers expose a supported session-directory setting, so point
  # them at the same filtered copy.
  #
  # The plain tile runs `niri-session` against ~/.config/niri/config.kdl, a file
  # this config has never written — the standalone niri session was retired in
  # favour of Niri (Noctalia). Choosing it lands you in niri's compiled-in
  # defaults: no output layout, no keybinds, no shell.
  visibleWaylandSessions = pkgs.runCommand "visible-wayland-sessions" { } ''
    mkdir -p "$out"
    cp ${config.services.displayManager.sessionData.desktops}/share/wayland-sessions/*.desktop "$out"/
    chmod u+w "$out"/*.desktop
    rm -f "$out/niri.desktop" "$out/hyprland-uwsm.desktop"
  '';
in
{
  # Niri compositor. Hyprland is the daily driver; niri is kept as a working
  # fallback session (Niri (Noctalia)), so this stays enabled.
  programs.niri.enable = true;

  # Ly remains the default TUI display manager unless a host explicitly enables
  # SDDM. This keeps usins unchanged while perkons opts into the graphical login.
  services.displayManager.ly = lib.mkIf (!config.services.displayManager.sddm.enable) {
    enable = true;
    # The greeter is ported from the atlas one (cartermccann/gentoo-dotfiles,
    # system/ly/config.ini), retinted from lib/palette.nix, and carries
    # Portfolio2's language as far as a console allows: the CM/26 monogram
    # resolving coarse to sharp (Ly's .dur animation), slash-pair labels from
    # a custom language file ("01 / USER"), a hairline box and one cobalt
    # accent. Assets are generated in pkgs/ly-auseklis.
    #
    # Ly renders on the framebuffer console: colours snap to the 16 VT slots
    # (Stylix's console target sets them from Auseklis) and text is a bitmap font.
    settings = {
      waylandsessions = "${visibleWaylandSessions}";
      full_color = true;

      bg = lyColor pal.raw.base02; # #171b23 — dark and blue-cast, not black
      fg = lyColor pal.raw.base07; # #f4f7fc — the atlas config's "white"
      border_fg = lyColor pal.raw.base0D; # cobalt accent, on the box border
      error_fg = lyColor pal.raw.base08;

      # The box keeps its border here: that cobalt line IS the accent, which is
      # why hide_borders flips false relative to the config this replaces.
      box_title = "${lib.toUpper config.networking.hostName} / ${config.system.nixos.release}";
      hide_borders = false;
      blank_box = true;
      text_in_center = true;
      margin_box_h = 4;
      margin_box_v = 1; # keeps the box clear of the monogram above it
      input_len = 34;
      edge_margin = 2;

      clock = "%H:%M · %^a %^b %d"; # 22:42 · MON OCT 05
      bigclock = "none"; # the monogram is the hero; a big clock would fight it

      asterisk = "0x2022"; # bullet instead of *
      hide_version_string = true;
      hide_key_hints = false; # "F1 / SHUTDOWN" etc. from the auseklis lang file
      sleep_cmd = "/run/current-system/systemd/bin/systemctl suspend"; # F3 / SLEEP
      hide_keyboard_locks = true;
      lang = "auseklis"; # /etc/ly/lang/auseklis.ini
      initial_info_text = "AUTH / READY";

      animation = "dur_file";
      dur_file_path = "/etc/ly/auseklis.dur";
      dur_offset_alignment = "topcenter";
      dur_y_offset = 1;
      animation_timeout_sec = 0; # the .dur loops: resolve, hold 45s, resolve
      animation_frame_delay = 24; # ms per event-loop pass

      allow_empty_password = false;
      clear_password = true;
      numlock = false;
      save = true;
    };
  };
  # Ly reads its language from <config dir>/lang/<lang>.ini, so the assets sit
  # beside /etc/ly/config.ini.
  environment.etc = lib.mkIf (!config.services.displayManager.sddm.enable) {
    "ly/auseklis.dur".source = "${lyAuseklis}/auseklis.dur";
    "ly/lang/auseklis.ini".source = "${lyAuseklis}/auseklis.ini";
  };

  # SDDM's package, Breeze theme and Wayland greeter are supplied by Plasma's
  # NixOS module. Keep its chooser on the same curated session list as Ly.
  services.displayManager.sddm = lib.mkIf config.services.displayManager.sddm.enable {
    settings.Wayland.SessionDir = "${visibleWaylandSessions}";
  };

  # XDG portals for Wayland
  xdg.portal = {
    enable = true;
    extraPortals = [
      pkgs.xdg-desktop-portal-gtk # file picker, etc. (works with any WM, unlike -gnome which needs Mutter)
      pkgs.xdg-desktop-portal-wlr # screen/window capture for wlroots compositors
    ];
    config.common = {
      default = [ "gtk" ];
      "org.freedesktop.impl.portal.ScreenCast" = [ "wlr" ];
      "org.freedesktop.impl.portal.Screenshot" = [ "wlr" ];
    };
  };

  # Polkit for privilege escalation
  security.polkit.enable = true;

  # GNOME Keyring
  services.gnome.gnome-keyring.enable = true;

  # Desktop packages
  environment.systemPackages = with pkgs; [
    ghostty
    fuzzel
    awww
    grim
    slurp
    wl-clipboard
    # mako removed: its package ships a dbus-activatable user service that
    # grabs org.freedesktop.Notifications in ANY session (it hijacked another
    # session's notification server). Every session has its own daemon now:
    # noctalia (niri), swaync (waybar Hyprland), Caelestia (its own tile).
    waybar
    xwayland-satellite
    brightnessctl
    wlsunset
    cliphist
    satty
    swayosd
  ];
}
