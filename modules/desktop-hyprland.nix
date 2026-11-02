{
  config,
  lib,
  pkgs,
  user,
  hyprland,
  ...
}:

let
  hyprPkgs = hyprland.packages.${pkgs.stdenv.hostPlatform.system};
in
{
  # Hyprland compositor — pinned 0.55.x from the upstream flake (Lua config era).
  # portalPackage is taken from the same flake eval so the compositor and its
  # portal stay version-matched; both come prebuilt from hyprland.cachix.org.
  programs.hyprland = {
    enable = true;
    package = hyprPkgs.hyprland;
    portalPackage = hyprPkgs.xdg-desktop-portal-hyprland;
    xwayland.enable = true;
  };

  # Hyprland's portal is already installed via programs.hyprland.portalPackage above;
  # here we only declare the portal routing. Keyed under `hyprland` (the
  # XDG_CURRENT_DESKTOP the compositor sets) so it does NOT collide with the
  # `common` portal config the niri module already defines.
  xdg.portal = {
    config.hyprland = {
      default = [
        "hyprland"
        "gtk"
      ];
      "org.freedesktop.impl.portal.ScreenCast" = [ "hyprland" ];
      "org.freedesktop.impl.portal.Screenshot" = [ "hyprland" ];
    };
  };

  # PAM services for the two lockers: hyprlock (Waybar session, and the
  # fallback `pond-session lock` uses if Pond's locker does not engage) and
  # Pond's own. Pond's locker reads its service name from POND_PAM_SERVICE,
  # which the Pond session sets to pond-lock; without a dedicated service it
  # would authenticate through `login`.
  security.pam.services.hyprlock = { };
  security.pam.services.pond-lock = { };

  # How long logind waits for sleep delay inhibitors before suspending anyway
  # (default 5s). Pond's lock guard holds one until the screen is confirmed
  # locked, and `pond-session lock`, hyprlock fallback included, is bounded at
  # 12s (home/scripts/pond-session.sh, LOCK_BUDGET); 5s would let a slow
  # fallback lose the race and resume unlocked. hypridle's lock-before-sleep in
  # the Waybar session only ever finishes sooner.
  services.logind.settings.Login.InhibitDelayMaxSec = 15;

  # Services Pond's shell talks to over D-Bus, plus the ones Plasma used to
  # switch on for this host and that outlive it (see the cleanup note below).
  #   upower               battery and "on battery" state (Pond's idle timeouts
  #                        and battery card; Noctalia's module used to enable it)
  #   power-profiles-daemon the battery card's power-profile selector
  #   udisks2, accounts-daemon, geoclue2, fwupd, dconf
  #                        desktop plumbing Plasma enabled; GTK/Zen/nautilus,
  #                        the GTK portal and home-manager's dconf.settings use it
  services.upower.enable = true;
  services.power-profiles-daemon.enable = true;
  services.udisks2.enable = true;
  services.accounts-daemon.enable = true;
  services.geoclue2.enable = true;
  services.fwupd.enable = true;
  programs.dconf.enable = true;

  # Pond's shell talks to NetworkManager (wifi card, lock-screen network
  # state), BlueZ (bluetooth card) and PipeWire's PulseAudio server (`pactl`
  # for the audio card). All three are declared elsewhere (modules/common.nix,
  # bluetooth.nix, audio.nix); this fails the build if one is switched off
  # rather than leaving a card that quietly shows nothing.
  assertions = [
    {
      assertion = config.networking.networkmanager.enable;
      message = "Pond's wifi card and lock screen need NetworkManager (networking.networkmanager.enable).";
    }
    {
      assertion = config.hardware.bluetooth.enable;
      message = "Pond's bluetooth card needs BlueZ (hardware.bluetooth.enable).";
    }
    {
      assertion = config.services.pipewire.pulse.enable;
      message = "Pond's audio card runs pactl, which needs PipeWire's PulseAudio server (services.pipewire.pulse.enable).";
    }
  ];

  # Pond's brightness control drives the external monitors over DDC/CI
  # (settings/appearance/control.py calls ddcutil), which needs the i2c-dev
  # module and a group that may open /dev/i2c-*.
  hardware.i2c.enable = true;
  users.users.${user}.extraGroups = [ "i2c" ];

  # Pond validates wallpapers with GdkPixbuf and offers WebP and AVIF images, and
  # its icons come from the system icon dirs. GDK_PIXBUF_MODULE_FILE is built
  # from this list for the whole session environment (librsvg is what Plasma
  # contributed here before; the other two add the WebP and AVIF loaders), and
  # Adwaita sits beside hicolor on XDG_DATA_DIRS (Papirus comes from
  # home-manager's gtk.iconTheme).
  programs.gdk-pixbuf.modulePackages = with pkgs; [
    librsvg
    webp-pixbuf-loader
    libavif
  ];

  # Session-specific packages (waybar / fuzzel / awww / grim / slurp / satty / cliphist /
  # wl-clipboard / brightnessctl / playerctl / wlsunset are already pulled in by desktop-wayland.nix).
  environment.systemPackages = with pkgs; [
    hyprlock
    hypridle
    swaynotificationcenter
    adwaita-icon-theme
    hicolor-icon-theme # the icon-theme fallback every GTK/Qt app resolves through; Plasma pulled it in

    # The general-purpose KDE apps Plasma's module installed that stand on their
    # own: document viewer, image viewer, archive manager. The rest of its
    # bundle (Dolphin and Konsole next to Nautilus and Ghostty, Kate, Elisa,
    # Spectacle, KInfoCenter, System Settings, the KWin/Plasma shell itself, the
    # KDE portal and kwallet next to gnome-keyring) is not used here and went
    # with it. They are themed through the qtct/Kvantum choice above.
    kdePackages.okular
    kdePackages.gwenview
    kdePackages.ark
  ];

  # Pond's packages and its session helper (pond-session) are perkons-only, so
  # they join Home Manager here rather than through home/hyprland, which usins
  # evaluates too.
  home-manager.users.${user} = {
    imports = [ ../home/hyprland/pond.nix ];

    # The home-manager half of the Qt choice above.
    stylix.targets.qt = {
      enable = true;
      platform = "qtct";
    };
  };

  # Plasma's Qt integration used to style every Qt app on this host and is gone,
  # so the choice is made here instead of by whatever the default happens to be:
  # Stylix's Qt target on its qtct platform (qt6ct, themed with Auseklis through
  # Kvantum), the same arrangement usins has. Both layers are set because the
  # NixOS one exports QT_QPA_PLATFORMTHEME and the home-manager one writes the
  # qt6ct/Kvantum config the apps read. (desktop-plasma.nix used to switch the
  # home-manager target off.)
  stylix.targets.qt = {
    enable = true;
    platform = "qtct";
  };

  # Ly's chooser opens on the Waybar session unless the last one used was saved.
  services.displayManager.defaultSession = "hyprland";

  # No custom login tile for the waybar variant: programs.hyprland registers a
  # plain "Hyprland" session, and its theming lives in
  # ~/.config/hypr/hyprland.lua, so that stock tile already loads this exact
  # look. The Caelestia and Pond variants need their own tiles since they point
  # at different Lua configs (~/.config/hypr/hyprland-caelestia.lua,
  # ~/.config/hypr/hyprland-pond.lua): same compositor settings, different
  # shell layer. All three files come from home/hyprland/compositor.nix via
  # mkHyprlandLua.
  services.displayManager.sessionPackages = [
    (
      (pkgs.writeTextDir "share/wayland-sessions/hyprland-caelestia.desktop" ''
        [Desktop Entry]
        Name=Hyprland (Caelestia)
        Comment=Hyprland with the Caelestia shell (Quickshell)
        # start-hyprland is 0.55's watchdog launcher (bare Hyprland shows a
        # "started without start-hyprland" error overlay in-session, and that
        # overlay is itself a top layer that shoves other surfaces down).
        # Args after `--` are forwarded to Hyprland.
        Exec=start-hyprland -- --config /home/${user}/.config/hypr/hyprland-caelestia.lua
        Type=Application
      '').overrideAttrs
        (_: {
          passthru.providedSessions = [ "hyprland-caelestia" ];
        })
    )
    (
      (pkgs.writeTextDir "share/wayland-sessions/hyprland-pond.desktop" ''
        [Desktop Entry]
        Name=Hyprland (Pond)
        Comment=Hyprland with the Pond shell (Quickshell) and Vicinae
        # start-hyprland, as for the Caelestia tile; args after `--` go to Hyprland.
        Exec=start-hyprland -- --config /home/${user}/.config/hypr/hyprland-pond.lua
        Type=Application
      '').overrideAttrs
        (_: {
          passthru.providedSessions = [ "hyprland-pond" ];
        })
    )
  ];
}
