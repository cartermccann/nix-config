{
  config,
  pkgs,
  zen-browser,
  ...
}:
# Zen through its home-manager module, so Stylix can theme it with Auseklis
# (stylix.targets.zen-browser needs an HM-managed profile). perkons only: the
# profile below is perkons's existing one, and usins has its own.
#
# `path` points HM at the profile that already exists, so taking over
# profiles.ini keeps every tab, login and setting; the old profiles.ini is
# kept as profiles.ini.hm-bak.
{
  imports = [ zen-browser.homeModules.beta ];

  programs.zen-browser = {
    enable = true;
    profiles.default = {
      id = 0;
      name = "Default Profile";
      path = "nm1yp8rk.Default Profile";
      isDefault = true;
    };
  };

  stylix.targets.zen-browser.profileNames = [ "default" ];

  # Zen opens links and HTML files in every Hyprland session (Waybar,
  # Caelestia, Pond: XDG_CURRENT_DESKTOP=Hyprland). The defaults go in the
  # desktop-specific hyprland-mimeapps.list, which GLib, xdg-open and Vicinae
  # read before ~/.config/mimeapps.list, so the hand-edited mimeapps.list and
  # its other associations stay as they are. The desktop ID is read from the
  # package; the build fails if it ever stops shipping it.
  xdg.configFile."hyprland-mimeapps.list".source =
    let
      zen = config.programs.zen-browser.finalPackage;
      id = "zen-beta.desktop";
    in
    pkgs.runCommand "hyprland-mimeapps.list" { } ''
      test -f ${zen}/share/applications/${id}
      cat > "$out" <<EOF
      [Default Applications]
      x-scheme-handler/http=${id}
      x-scheme-handler/https=${id}
      text/html=${id}
      EOF
    '';
}
