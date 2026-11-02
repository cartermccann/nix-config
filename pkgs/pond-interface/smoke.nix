# Loads pond-shell and pond-lock inside the build sandbox: a headless sway
# (pixman renderer, no GPU), a private D-Bus session and a temporary
# XDG_RUNTIME_DIR. Nothing reaches the live desktop or its buses. It checks
# that every QML import resolves, the configuration loads, and IPC works
# through the wrappers; the session lock is never engaged (POND_LOCK_NO_START).
{
  lib,
  runCommand,
  sway,
  dbus,
  fontconfig,
  coreutils,
  gnugrep,
  procps,
  grim,
  pond-interface,
}:
runCommand "pond-interface-smoke"
  {
    nativeBuildInputs = [
      sway
      dbus
      fontconfig
      coreutils
      gnugrep
      procps
      grim
    ];
  }
  ''
    export HOME=$TMPDIR/home XDG_RUNTIME_DIR=$TMPDIR/run
    export XDG_CONFIG_HOME=$HOME/.config XDG_STATE_HOME=$HOME/.local/state XDG_CACHE_HOME=$HOME/.cache
    mkdir -p $HOME $XDG_RUNTIME_DIR $XDG_CONFIG_HOME
    chmod 700 $XDG_RUNTIME_DIR

    # One private session bus for the compositor and both shells.
    export DBUS_SESSION_BUS_ADDRESS=$(dbus-daemon --fork --print-address \
      --config-file=${dbus.out}/share/dbus-1/session.conf)

    export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1
    echo 'output HEADLESS-1 resolution 1920x1080' > $TMPDIR/sway.conf
    sway -c $TMPDIR/sway.conf > $TMPDIR/sway.log 2>&1 &
    for _ in $(seq 100); do
      socket=$(ls $XDG_RUNTIME_DIR | grep -E '^wayland-[0-9]+$' | head -n1 || true)
      [ -n "$socket" ] && break
      sleep 0.1
    done
    [ -n "$socket" ] || { cat $TMPDIR/sway.log; echo "sway did not start" >&2; exit 1; }
    export WAYLAND_DISPLAY=$socket QT_QPA_PLATFORM=wayland QT_QUICK_BACKEND=software

    # Same fontconfig as the wrappers, for the font assertions below.
    export FONTCONFIG_FILE=${pond-interface.fontsConf}
    for family in Onest "IBM Plex Mono" "Advent Pro" "JetBrains Mono" "Fastfetch Flowers"; do
      matched=$(fc-match -f '%{family[0]}' "$family")
      echo "font $family -> $matched"
      [ "$matched" = "$family" ] || { echo "font $family is not available" >&2; exit 1; }
    done
    gramercy=$(fc-match -f '%{family[0]}' "Pond Gramercy")
    echo "font Pond Gramercy -> $gramercy (gramercyAvailable=${lib.boolToString pond-interface.gramercyAvailable})"
    ${lib.optionalString (!pond-interface.gramercyAvailable) ''
      expected=${lib.escapeShellArg pond-interface.gramercyFallbackFamily}
      [ "$gramercy" = "$expected" ] || { echo "Gramercy fallback did not resolve to $expected" >&2; exit 1; }
    ''}
    unset FONTCONFIG_FILE

    run() {
      name=$1; shift
      "$@" > $TMPDIR/$name.log 2>&1 &
    }

    loaded() {
      for _ in $(seq 150); do
        grep -q 'Configuration Loaded' $TMPDIR/$1.log && return 0
        sleep 0.1
      done
      cat $TMPDIR/$1.log
      echo "$1: configuration did not load" >&2
      exit 1
    }

    ipc() {
      name=$1; target=$2; shift 2
      "$@" ipc show > $TMPDIR/$name.ipc 2>&1 || { cat $TMPDIR/$name.ipc; echo "$name: ipc failed" >&2; exit 1; }
      grep -q "target $target" $TMPDIR/$name.ipc || { cat $TMPDIR/$name.ipc; echo "$name: ipc target $target missing" >&2; exit 1; }
    }

    POND_LOCK_NO_START=1 run lock ${pond-interface}/bin/pond-lock
    loaded lock
    ipc lock lockscreen ${pond-interface}/bin/pond-lock

    run shell ${pond-interface}/bin/pond-shell
    loaded shell
    ipc shell sidebar ${pond-interface}/bin/pond-shell
    # Settings pages are created on demand; open each so its QML and helper
    # scripts load too.
    sleep 2
    grim $TMPDIR/desktop.png
    for page in appearance information manual display arrange system mouse lock keyboard timezone; do
      ${pond-interface}/bin/pond-shell ipc call settings open $page
      sleep 1
      grim $TMPDIR/settings-$page.png
    done
    ${pond-interface}/bin/pond-shell ipc call settings close
    ${pond-interface}/bin/pond-shell ipc call sidebar toggle
    sleep 1
    ${pond-interface}/bin/pond-shell ipc call sidebar toggle
    sleep 2

    cat $TMPDIR/lock.log $TMPDIR/shell.log $TMPDIR/*.ipc
    # Load failures: missing QML modules/types/files, plugins, Python imports,
    # or a command absent from the wrappers' PATH. Compositor CLIs (niri,
    # hyprctl) come from the session and Pond's Arch updater (pm) is replaced by
    # the adapter overlay, so those are reported separately.
    fatal='is not installed|is not a type|Type [A-Za-z.]+ unavailable|Cannot load library|Could not find the Qt platform plugin|failed to load component|Cannot open: |non-existent property|SyntaxError|ModuleNotFoundError|ImportError|binary could not be found|/nix/store[^ ]*: No such file|No such file or directory: .?/nix/store'
    session='"(niri|hyprctl|pm)"|JSON\.parse'
    if grep -hE "$fatal" $TMPDIR/lock.log $TMPDIR/shell.log | grep -vE "$session"; then
      echo "QML/runtime load errors (above)" >&2
      exit 1
    fi
    echo "expected outside a session (compositor/updater/pactl without servers):"
    grep -hE "$fatal" $TMPDIR/lock.log $TMPDIR/shell.log | grep -E "$session" || true

    pkill -P $$ || true
    pkill dbus-daemon || true
    mkdir -p $out
    cp $TMPDIR/*.log $TMPDIR/*.ipc $TMPDIR/*.png $out/
  ''
