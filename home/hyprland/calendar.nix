{
  config,
  lib,
  pkgs,
  hyprland,
  ...
}:
# The waybar clock's calendar dropdown (click the date). calendar.py is a GTK4
# layer-shell popup; events come from each Google account's secret iCal links
# in ~/.config/credentials/calendars.toml (read-only, no OAuth). The stylesheet
# is real CSS in config/hyprland/calendar.css with the palette emitted next to
# it, the same arrangement as waybar and swaync.
let
  pal = import ../../lib/palette.nix;

  python = pkgs.python3.withPackages (ps: [
    ps.pygobject3
    ps.icalendar
    ps.recurring-ical-events
  ]);

  typelibs = lib.makeSearchPath "lib/girepository-1.0" [
    pkgs.gtk4
    pkgs.gtk4-layer-shell
    pkgs.glib.out
    pkgs.gobject-introspection
    pkgs.pango.out
    pkgs.gdk-pixbuf
    pkgs.graphene
    pkgs.harfbuzz
    pkgs.cairo
  ];

  script = ./calendar.py;
  hyprctl = "${hyprland.packages.${pkgs.stdenv.hostPlatform.system}.hyprland}/bin/hyprctl";

  # One command for the bar's on-click: a running popup is closed, otherwise
  # one opens. pkill matches the script's store path, which only the popup's
  # python process carries on its command line.
  calendarPopup = pkgs.writeShellScriptBin "auseklis-calendar" ''
    ${pkgs.procps}/bin/pkill -f -- '${script}' && exit 0
    # gtk4-layer-shell has to load before libwayland-client; for a Python
    # client that means preloading it.
    export LD_PRELOAD=${pkgs.gtk4-layer-shell}/lib/libgtk4-layer-shell.so''${LD_PRELOAD:+:$LD_PRELOAD}
    export GI_TYPELIB_PATH=${typelibs}''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}
    export AUSEKLIS_HYPRCTL=${hyprctl}
    exec ${python}/bin/python3 ${script} "$@"
  '';
in
{
  home.packages = [ calendarPopup ];

  xdg.configFile."auseklis-calendar/style.css".source = ../../config/hyprland/calendar.css;
  xdg.configFile."auseklis-calendar/_auseklis.css".text = import ./palette-css.nix lib pal;

  # Seed the accounts file once, private, with a commented template. It holds
  # secret URLs, so it lives with the other credentials and never in the store.
  home.activation.seedCalendarAccounts = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    f="${config.home.homeDirectory}/.config/credentials/calendars.toml"
    if [ ! -e "$f" ]; then
      [ -d "$(dirname "$f")" ] || run install -d -m 700 "$(dirname "$f")"
      run install -m 600 ${pkgs.writeText "calendars.toml" ''
        # Accounts for the waybar calendar dropdown (auseklis-calendar).
        #
        # For each Google account, open calendar.google.com > Settings, pick a
        # calendar under "Settings for my calendars", scroll to "Integrate
        # calendar", and copy "Secret address in iCal format". One URL per
        # calendar you want to see; add as many as you like to `urls`.
        # Treat these like passwords: anyone with the link can read the calendar.

        week_start = "sunday" # or "monday"

        # [[account]]
        # name  = "ComCreate"
        # email = "tech@comcreate.org"
        # urls  = [
        #   "https://calendar.google.com/calendar/ical/.../private-.../basic.ics",
        # ]

        # [[account]]
        # name  = "Personal"
        # email = ""
        # urls  = []

        # [[account]]
        # name  = "Third"
        # email = ""
        # urls  = []
      ''} "$f"
    fi
  '';
}
