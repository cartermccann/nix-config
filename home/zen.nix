{ zen-browser, ... }:
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
}
