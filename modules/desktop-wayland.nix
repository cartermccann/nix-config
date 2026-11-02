{
  config,
  lib,
  pkgs,
  ...
}:

# Shared Wayland base for EVERY session on every host, not a niri-only module
# (it was called desktop-niri.nix, which badly undersold it). Owns the shared
# display manager (Ly), XDG portals, polkit, gnome-keyring, and the terminal /
# launcher / screenshot / clipboard / wallpaper packages that the Hyprland
# sessions depend on just as much as the niri one. desktop-hyprland.nix (perkons)
# and desktop-niri-noctalia.nix (usins) layer session-specific bits on top of this.
let
  cfg = config.local.ly;

  # The Ly greeter's monogram animation and slash-pair language file.
  lyAuseklis = pkgs.callPackage ../pkgs/ly-auseklis { };
  pal = import ../lib/palette.nix;

  # Ly colours are 0x00RRGGBB. The most significant byte is a *styling* flag,
  # not alpha, so a plain colour keeps it at 00. pal.raw.* is the palette slot
  # without its leading '#'.
  lyColor = raw: "0x00${raw}";

  # Ly reads a flat directory of .desktop files. Start from the one NixOS
  # generates from sessionPackages, then drop unintentional tiles.
  #
  # Why filter here instead of at the source: programs.niri.enable hard-sets
  # `services.displayManager.sessionPackages = [ cfg.package ]` (niri-flake
  # flake.nix:496), and NixOS rejects a session package whose
  # passthru.providedSessions is empty, so the tile cannot be removed by
  # overriding the package without also rebuilding niri from source. Ly exposes
  # a supported session-directory setting, so point it at the filtered copy.
  #
  # niri.desktop (usins only) runs `niri-session` against
  # ~/.config/niri/config.kdl, a file this config has never written — the
  # standalone niri session was retired in favour of Niri (Noctalia). Choosing
  # it lands you in niri's compiled-in defaults: no output layout, no keybinds,
  # no shell. hyprland-uwsm.desktop is the UWSM variant of the stock Hyprland
  # tile; nothing here starts Hyprland through UWSM.
  visibleWaylandSessions = pkgs.runCommand "visible-wayland-sessions" { } ''
    mkdir -p "$out"
    cp ${config.services.displayManager.sessionData.desktops}/share/wayland-sessions/*.desktop "$out"/
    chmod u+w "$out"/*.desktop
    rm -f "$out/niri.desktop" "$out/hyprland-uwsm.desktop"
  '';

  # What the greeter animates behind the login box. Both read the same layout,
  # language file and palette; only the animation differs.
  animations = {
    # The CM/26 monogram resolving coarse to sharp: Ly's .dur animation, drawn
    # from the file pkgs/ly-auseklis generates.
    monogram = {
      animation = "dur_file";
      dur_file_path = "/etc/ly/auseklis.dur";
      dur_offset_alignment = "topcenter";
      dur_y_offset = 1;
      animation_timeout_sec = 0; # the .dur loops: resolve, hold 45s, resolve
      animation_frame_delay = 24; # ms per event-loop pass
    };

    # Ly's colour-mixing shader in the house cobalt, deep navy and black, running
    # continuously. This is what perkons ran before a4c1e79 replaced it with the
    # monogram (its settings then were min_refresh_delta = 24, which Ly 1.4
    # renamed animation_frame_delay; the other keys below were checked against
    # the installed 1.4.1's etc/config.ini). Colours are Ly colour words: the
    # top byte is a styling flag, so 0x20000000 is a black that blends rather
    # than the plain-colour 0x00 prefix of the rest of this file.
    colormix = {
      animation = "colormix";
      colormix_col1 = lyColor pal.raw.base0D; # cobalt accent
      colormix_col2 = "0x00102a66"; # deep navy
      colormix_col3 = "0x20000000"; # black
      animation_timeout_sec = 0; # run forever
      animation_frame_delay = 24; # ms per event-loop pass
    };
  };
in
{
  options.local.ly.animation = lib.mkOption {
    type = lib.types.enum [
      "monogram"
      "colormix"
    ];
    default = "monogram";
    description = ''
      Greeter animation behind Ly's login box. `monogram` (the default, usins)
      draws the CM/26 monogram from a generated .dur file; `colormix` is Ly's
      built-in colour mixer in the cobalt/navy of the palette (perkons) and
      needs no .dur file, so none is installed.
    '';
  };

  config = {
    # Ly is the display manager on every host, permanently: a terminal greeter
    # with no graphical stack of its own. (SDDM and a graphical greeter were
    # only ever pulled in by Plasma, and that session is gone.)
    services.displayManager.ly = {
      enable = true;
      # The greeter is ported from the atlas one (cartermccann/gentoo-dotfiles,
      # system/ly/config.ini), retinted from lib/palette.nix, and carries
      # Portfolio2's language as far as a console allows: an animation
      # (local.ly.animation), slash-pair labels from a custom language file
      # ("01 / USER"), a hairline box and one cobalt accent. Assets are
      # generated in pkgs/ly-auseklis.
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
        margin_box_v = 1; # keeps the box clear of the animation above it
        input_len = 34;
        edge_margin = 2;

        clock = "%H:%M · %^a %^b %d"; # 22:42 · MON OCT 05
        bigclock = "none"; # the animation is the hero; a big clock would fight it

        asterisk = "0x2022"; # bullet instead of *
        hide_version_string = true;
        hide_key_hints = false; # "F1 / SHUTDOWN" etc. from the auseklis lang file
        sleep_cmd = "/run/current-system/systemd/bin/systemctl suspend"; # F3 / SLEEP
        hide_keyboard_locks = true;
        lang = "auseklis"; # /etc/ly/lang/auseklis.ini
        initial_info_text = "AUTH / READY";

        allow_empty_password = false;
        clear_password = true;
        numlock = false;
        save = true;
      }
      // animations.${cfg.animation};
    };
    # Ly reads its language from <config dir>/lang/<lang>.ini, so the assets sit
    # beside /etc/ly/config.ini. The .dur file is only installed when the
    # monogram is the animation.
    environment.etc = {
      "ly/lang/auseklis.ini".source = "${lyAuseklis}/auseklis.ini";
    }
    // lib.optionalAttrs (cfg.animation == "monogram") {
      "ly/auseklis.dur".source = "${lyAuseklis}/auseklis.dur";
    };

    # XDG portals for Wayland. The GTK backend is the file chooser and the
    # Settings portal for every session; the Hyprland sessions add their own
    # backend (desktop-hyprland.nix) and the niri session its wlroots one
    # (desktop-niri-noctalia.nix).
    xdg.portal = {
      enable = true;
      extraPortals = [
        pkgs.xdg-desktop-portal-gtk # file picker, etc. (works with any WM, unlike -gnome which needs Mutter)
      ];
      config.common.default = [ "gtk" ];
    };

    # Pieces `programs.niri.enable` used to switch on as a side effect, which
    # every host got while the niri module was shared. They are the base of any
    # desktop session, so they are stated here rather than left to whichever
    # compositor happens to be enabled: xdg-open and friends, the XDG
    # autostart/menu/mime/icon plumbing (xdg-utils is in the package list below),
    # dconf, and the default font set.
    xdg = {
      autostart.enable = lib.mkDefault true;
      menus.enable = lib.mkDefault true;
      mime.enable = lib.mkDefault true;
      icons.enable = lib.mkDefault true;
    };
    programs.dconf.enable = lib.mkDefault true;
    fonts.enableDefaultPackages = lib.mkDefault true;

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
      # noctalia (niri), swaync (waybar Hyprland), Caelestia and Pond (their own
      # tiles).
      waybar
      brightnessctl
      cliphist
      satty
      swayosd
      xdg-utils
    ];
  };
}
