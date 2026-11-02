{
  lib,
  pkgs,
  osConfig,
  ...
}:
# Pond: the Quickshell shell behind the "Hyprland (Pond)" login tile
# (github.com/bflycomputer/pond-interface, packaged as pkgs.pond-interface in
# pkgs/pond-interface). It replaces waybar, swaync, swayosd, hyprlock, hypridle
# and awww wholesale for that session: the sidebar and minimap, notifications,
# the volume and brightness OSD, the live sky background, the lock screen and
# idle handling all come from it, and Vicinae is the launcher and clipboard
# history. The seams where compositor.nix diverges for Pond are in
# mkHyprlandLua (shellKind = "pond").
#
# What this module owns is the part that has to be right for three shells to
# share one machine: starting Pond's processes for the Pond session only,
# making sure they are gone when it ends, and one owner for locking (the
# shell's idle lock, Super+Ctrl+L, logind's Lock and lock before sleep). The
# session helper below does that; the reasoning is in scripts/pond-session.sh.
let
  # Package wrappers: `pond-shell` is `qs -p <packaged shell>` and `pond-lock`
  # the same for the lock screen, with every argument forwarded, so
  # `pond-shell ipc call volume increase` is Quickshell's IPC client against
  # the packaged shell.
  pondShell = lib.getExe' pkgs.pond-interface "pond-shell";
  pondLock = lib.getExe' pkgs.pond-interface "pond-lock";

  # The wrapper is the only place store paths are fixed; the script reads them
  # from the environment so the same file can be exercised against stand-in
  # binaries (home/scripts/test-pond-session.sh).
  pondSession = pkgs.writeShellApplication {
    name = "pond-session";
    # No errexit: the supervisor loops and signal helpers use non-zero statuses.
    bashOptions = [
      "nounset"
      "pipefail"
    ];
    runtimeInputs = with pkgs; [
      coreutils
      util-linux # logger, setsid, flock
      systemd # systemctl, systemd-inhibit (the guard's sleep delay inhibitor)
      glib.bin # gdbus: the guard's logind signals
      libnotify # notify-send: lock and launcher failures
      hyprlock # the fallback locker
      osConfig.programs.hyprland.package # hyprctl: query global lock state
    ];
    text = ''
      POND_SHELL=''${POND_SHELL:-${pondShell}}
      POND_LOCK=''${POND_LOCK:-${pondLock}}
      POND_LAUNCHER=''${POND_LAUNCHER:-${lib.getExe pkgs.vicinae}}
      # The dedicated PAM service from modules/desktop-hyprland.nix. Pond's
      # locker reads it from POND_PAM_SERVICE and otherwise uses `login`.
      POND_PAM_SERVICE=''${POND_PAM_SERVICE:-pond-lock}
      ${builtins.readFile ../scripts/pond-session.sh}
    '';
  };
in
{
  # One wallpaper library for all three desktops: the images already in
  # ../../wallpaper, untouched, with Pond's own artwork joined in under
  # wallpapers/pond/ (pond-dusk.png and its ATTRIBUTION; the package ships no
  # other images, see its README). ~/wallpapers is a Home Manager symlink into
  # the store, so new files cannot be copied in at runtime; this builds the
  # merged directory instead. The Waybar and Caelestia pickers `find -L` it, and
  # Pond's custom-image picker is pointed at the same place. Picking an image in
  # Pond writes Pond's own state and leaves ~/wallpaper.png, which the other two
  # sessions and hyprlock share, alone.
  home.file."wallpapers".source = pkgs.symlinkJoin {
    name = "wallpapers";
    paths = [
      ../../wallpaper
      "${pkgs.pond-interface}/share/wallpapers"
    ];
  };

  # The package installs pond-manual.desktop globally. It runs
  # `pond-shell ipc call manual open`, which only does something while Pond's
  # shell is running, so in the Waybar and Caelestia sessions every launcher
  # (fuzzel, Caelestia's) would list a "Manual" entry that does nothing. A copy
  # in ~/.local/share/applications takes precedence over the package's and
  # carries NoDisplay=true; the manual stays reachable from Pond itself (it opens
  # on first run, Super+Shift+/ and the settings Manual page).
  xdg.dataFile."applications/pond-manual.desktop".source =
    pkgs.runCommand "pond-manual.desktop" { nativeBuildInputs = [ pkgs.gnused ]; }
      ''
        sed '/^NoDisplay=/d; /^\[Desktop Entry\]$/a NoDisplay=true' \
          ${pkgs.pond-interface}/share/applications/pond-manual.desktop > "$out"
        grep -qx 'NoDisplay=true' "$out"
        grep -q '^Exec=.*pond-shell ipc call manual open$' "$out"
      '';

  # swaync is D-Bus-activated for org.freedesktop.Notifications. While a live
  # Pond session has claimed the name, its unit skips itself (a skipped
  # ExecCondition is not a failure), so a notification sent while Pond's shell
  # (re)starts cannot hand the name to swaync for the rest of the session. With
  # no live Pond session the condition passes and the Waybar session is as
  # before. The bus is shared by every session of the user: while Pond owns the
  # name, a Waybar session started on another VT has no swaync.
  xdg.configFile."systemd/user/swaync.service.d/pond.conf".text = ''
    [Service]
    ExecCondition=${lib.getExe pondSession} notifications-allowed
  '';

  home.packages = [
    pkgs.pond-interface
    pkgs.vicinae
    pondSession
  ];

  # Terminal desktop entries launched from Vicinae (and anything else that asks
  # xdg-terminal-exec for "the terminal") open in Ghostty. It is the terminal
  # in every session already; this only makes the launcher agree. Adds
  # ~/.config/xdg-terminals.list, which did not exist, and touches no profile.
  xdg.terminal-exec = {
    enable = true;
    settings.default = [ "com.mitchellh.ghostty.desktop" ];
  };
}
