{
  pkgs,
  noctalia,
  user,
  ...
}:

# The Niri (Noctalia) session: usins only. perkons dropped it when Pond arrived,
# so everything that exists for Niri and nothing else lives here: the compositor,
# its Home Manager configuration, the Noctalia shell, and the packages and portal
# backend only a wlroots-style compositor needs. The Niri flake input, overlay
# and nixosModule stay in the flake for every host, which is what keeps this
# importable by either.
{
  # Niri compositor — the session usins runs.
  programs.niri.enable = true;

  # Home Manager side: the Niri (Noctalia) config and Noctalia's colour scheme,
  # and the Niri-only helper (wallpaper-pick). The helpers both desktops share
  # (rice-dashboard) are in home/desktop-helpers.nix, imported by everyone.
  home-manager.users.${user}.imports = [
    ../home/niri.nix
    ../home/niri-noctalia.nix
  ];

  environment.systemPackages = [
    noctalia.packages.${pkgs.stdenv.hostPlatform.system}.default
    pkgs.xwayland-satellite # niri has no XWayland of its own
    pkgs.wlsunset # night light (niri-noctalia.nix autostart and Mod+Ctrl+N); Hyprland uses hyprsunset
  ];

  # Screen and window capture through the wlroots portal, which niri's sessions
  # (and no Hyprland one) route to. Layered on the GTK default in
  # desktop-wayland.nix.
  xdg.portal = {
    extraPortals = [ pkgs.xdg-desktop-portal-wlr ];
    config.common = {
      "org.freedesktop.impl.portal.ScreenCast" = [ "wlr" ];
      "org.freedesktop.impl.portal.Screenshot" = [ "wlr" ];
    };
  };

  # Battery / power management (needed by Noctalia's built-in widgets)
  services.upower.enable = true;

  # Register a "Niri (Noctalia)" session in the display manager
  services.displayManager.sessionPackages = [
    (
      (pkgs.writeTextDir "share/wayland-sessions/niri-noctalia.desktop" ''
        [Desktop Entry]
        Name=Niri (Noctalia)
        Comment=Niri compositor with Noctalia shell
        Exec=niri --config /home/${user}/.config/niri/config-noctalia.kdl
        Type=Application
      '').overrideAttrs
        (_: {
          passthru.providedSessions = [ "niri-noctalia" ];
        })
    )
  ];
}
