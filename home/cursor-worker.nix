{
  config,
  lib,
  pkgs,
  ...
}:

# Cursor self-hosted worker with computer use + desktop sharing.
#
# Cursor documents this as an Ubuntu apt line. On NixOS three of those packages
# are either named differently, built without the feature Cursor needs, or
# actively broken against a VNC X server. What the worker really probes for is a
# list of binaries, printed by `cursor-agent worker debug`.
#
# Display strategy: let Cursor create and own a single agent desktop, and make
# sure it cannot land on top of the compositor. This machine runs a Wayland
# session whose :0 is *rootless* Xwayland, which has no root window content, so
# x11grab against it captures nothing and xdotool cannot see Wayland-native
# windows. The worker must therefore never reuse :0, which means DISPLAY has to
# be unset (the graphical session pushes it into the systemd user manager) and
# the VNC server has to be kept off low display numbers - see tigervncCompat.
#
# An earlier revision pinned a separate Xvnc on :20 and passed --display. That
# worked, but --share-desktop always creates its own desktop regardless, so the
# agent typed on :20 while "Show desktop" rendered a different, empty desktop.
# One worker-owned desktop serves both.

let
  homeDir = config.home.homeDirectory;

  # The self-updating Cursor install, NOT the nixpkgs `cursor-cli` in
  # home/tools.nix: that build (2026.07.23, unstable at 2026-08-31) has no
  # --computer-use or --share-desktop flags at all, verified against both
  # binaries. Move back to pkgs.cursor-cli once nixpkgs catches up.
  cursorAgent = "${homeDir}/.local/bin/cursor-agent";

  # nixpkgs names TigerVNC's server Xvnc; Debian ships the same binary as
  # Xtigervnc, which is the name Cursor's preflight looks for.
  #
  # This is a wrapper rather than a symlink because it is the only place we can
  # influence where Cursor puts its desktop. Cursor invokes it as
  # `Xtigervnc -displayfd N ...`, and Xvnc's -displayfd scan begins at :0 and
  # does take it, even though Hyprland holds /tmp/.X0-lock with its own live
  # pid: observed twice replacing the compositor's /tmp/.X11-unix/X0 socket,
  # which silently redirects every new X11 client on the machine into the
  # agent's private desktop. Xvnc honours an explicit display *and* still
  # writes it back on -displayfd (verified), so pick a high free one here.
  # Racy in principle between the check and the exec; in practice nothing else
  # on this machine allocates displays in :50-:99.
  tigervncCompat = pkgs.writeShellScriptBin "Xtigervnc" ''
    for n in $(${pkgs.coreutils}/bin/seq 50 99); do
      if [ ! -e "/tmp/.X$n-lock" ] && [ ! -e "/tmp/.X11-unix/X$n" ]; then
        exec ${pkgs.tigervnc}/bin/Xvnc ":$n" "$@"
      fi
    done
    echo "Xtigervnc wrapper: no free display in :50-:99" >&2
    exit 1
  '';

  # Cursor waits for a window manager to claim _NET_SUPPORTING_WM_CHECK on the
  # display, and gets there by running `startxfce4`. Do not use the real one.
  # xfce4-session drags in xfconf, a session manager and a panel, and the whole
  # stack dies within seconds on Xvnc: xfwm4's compositor needs
  # GLX_EXT_texture_from_pixmap, which Xvnc does not provide, so it aborts with
  # "No provider of glXBindTexImageEXT found" and takes the ICE session down
  # with it. xfwm4 with compositing off is stable, needs neither D-Bus nor
  # xfconf, and is all Cursor is actually waiting for.
  startxfce4Shim = pkgs.writeShellScriptBin "startxfce4" ''
    exec ${pkgs.xfwm4}/bin/xfwm4 --compositor=off
  '';

  # nixpkgs' default ffmpeg is built without xcb, so it has no x11grab muxer and
  # every screenshot fails. The preflight only checks that an `ffmpeg` binary
  # exists, so this failure is invisible until an agent tries to take a picture.
  # ffmpeg-full carries "x11grab  X11 screen capture, using XCB", and is already
  # in this host's closure, so it costs nothing here.
  ffmpegX11 = pkgs.ffmpeg-full;

  workerPackages = [
    startxfce4Shim # must precede anything else providing startxfce4
    ffmpegX11
    tigervncCompat
  ]
  ++ (with pkgs; [
    coreutils # Cursor's desktop bring-up shells out to basic utilities
    xdotool # synthesises clicks and keystrokes
    dbus # dbus-launch, the `dbus-x11` half of the apt line
    tigervnc # Xvnc, reached through the wrapper above
    xfwm4 # the window manager Cursor waits for
  ])
  ++ (with pkgs; [
    xdpyinfo # x11-utils
    xprop # x11-utils
    xrandr # x11-xserver-utils
    xsetroot # x11-xserver-utils
    xauth # MIT-MAGIC-COOKIE handling
  ]);

  # System path last so the worker finds the Chrome wrapper from
  # modules/apps.nix without pulling a second Chrome closure in here.
  workerPath = "${lib.makeBinPath workerPackages}:/run/current-system/sw/bin";

  # xfwm4 reads its default theme out of share/.
  workerDataDirs = "${lib.makeSearchPath "share" workerPackages}:/run/current-system/sw/share";

  # The graphical session pushes all three of these into the systemd user
  # manager. DISPLAY must go or the worker reuses rootless Xwayland instead of
  # building its own desktop. WAYLAND_DISPLAY and XDG_SESSION_TYPE must go
  # because the Chrome wrapper in modules/apps.nix carries
  # --ozone-platform-hint=auto, which resolves to Wayland whenever they are
  # present; agent-launched Chrome then opens on the real desktop, where
  # x11grab and xdotool on the agent display cannot see it.
  sessionVars = [
    "DISPLAY"
    "WAYLAND_DISPLAY"
    "XDG_SESSION_TYPE"
  ];

  # `cursor-agent worker debug` from an ordinary shell reports everything
  # missing, because these packages are deliberately kept off the interactive
  # PATH. This runs the same preflight with the service's environment.
  preflight = pkgs.writeShellScriptBin "cursor-worker-preflight" ''
    export PATH="${workerPath}"
    export XDG_DATA_DIRS="${workerDataDirs}"
    unset ${lib.concatStringsSep " " sessionVars}
    exec ${cursorAgent} worker --worker-dir "${homeDir}/projects" debug "$@"
  '';
in
{
  home.packages = [ preflight ];

  systemd.user.services.cursor-worker = {
    Unit = {
      Description = "Cursor self-hosted worker (computer use + desktop sharing)";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
      ConditionPathExists = cursorAgent;
    };

    Service = {
      Type = "simple";

      # Worker flags go before `start`. No --display: the worker creates and
      # owns the agent desktop, which --share-desktop then shares, so the
      # desktop being watched is the one the agent is driving.
      ExecStart = lib.concatStringsSep " " [
        cursorAgent
        "worker"
        "--computer-use"
        "--share-desktop view"
        "--worker-dir ${homeDir}/projects"
        "--name perkons"
        "start"
      ];

      Environment = [
        "PATH=${workerPath}"
        "XDG_DATA_DIRS=${workerDataDirs}"
      ];
      UnsetEnvironment = sessionVars;

      Restart = "always";
      RestartSec = 10;
    };

    Install = {
      WantedBy = [ "default.target" ];
    };
  };
}
