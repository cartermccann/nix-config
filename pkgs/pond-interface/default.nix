# Pond interface (bflycomputer/pond-interface): the Quickshell desktop shell,
# session lock, wallpaper and screenshot QML modules, and their native plugins.
# Interface, overlay contract and update steps: ./README.md.
{
  lib,
  stdenv,
  callPackage,
  fetchFromGitea,
  cmake,
  ninja,
  pkg-config,
  makeWrapper,
  patchelf,
  qt6,
  kdePackages,
  wayland,
  quickshell,
  python3,
  glib,
  gdk-pixbuf,
  gobject-introspection,
  # Runtime commands.
  coreutils,
  bash,
  gnugrep,
  gnused,
  findutils,
  networkmanager,
  iw,
  getent,
  systemd,
  fastfetch,
  brightnessctl,
  ddcutil,
  pulseaudio,
  wl-clipboard,
  zenity,
  vicinae,
  grimblast,
  ghostty,
  xkeyboard-config,
  tzdata,
  # Extra packages for the wrappers' PATH (adapter helpers, launch targets).
  extraRuntimeInputs ? [ ],
  # See ./fonts.nix.
  gramercyFont ? null,
  gramercyFallbackFamily ? "Onest",
}:
let
  src = callPackage ./source.nix { };

  # Pond's lockscreen package requires quickshell >= 0.3.1 (session-lock
  # crash fixes on sleep, wake, DPMS and unlock); 0.3.2 adds Hyprland's new
  # workspace address format. Build it against this nixpkgs' Qt so the shell
  # and Pond's native plugins share one Qt closure. Once nixpkgs' own
  # quickshell is new enough, it is used unchanged.
  pondQuickshell =
    if lib.versionAtLeast quickshell.version "0.3.2" then
      quickshell
    else
      quickshell.overrideAttrs (_: {
        version = "0.3.2";
        src = fetchFromGitea {
          domain = "git.outfoxxed.me";
          owner = "quickshell";
          repo = "quickshell";
          tag = "v0.3.2";
          hash = "sha256-lPbqsyAD5ZoGFMZ1eqAlTULfeP5Nwhy+XvOowF7BE+E=";
        };
      });

  fonts = callPackage ./fonts.nix { inherit src gramercyFont gramercyFallbackFamily; };

  # The helpers use only PyQt6.QtCore (time zone and locale formatting); the
  # optional modules would add QtWebEngine and friends (~1.7 GiB).
  pythonEnv = python3.withPackages (ps: [
    (ps.pyqt6.override {
      withMultimedia = false;
      withWebSockets = false;
      withLocation = false;
      withPrintSupport = false;
      withSpeech = false;
      withPdf = false;
    })
    ps.pygobject3
  ]);

  typelibPath = lib.makeSearchPath "lib/girepository-1.0" [
    glib.out
    gdk-pixbuf.out
    gobject-introspection.out
  ];

  runtimeInputs = [
    coreutils
    bash
    gnugrep
    gnused
    findutils
    networkmanager
    iw
    getent
    systemd
    fastfetch
    brightnessctl
    ddcutil
    pulseaudio
    wl-clipboard
    zenity
    vicinae
    grimblast
    ghostty
  ]
  ++ extraRuntimeInputs;

  # Present only while the adapter port exists; a missing directory builds
  # the unmodified upstream shell.
  overlaySrc = if builtins.pathExists ./overlay then lib.cleanSource ./overlay else null;
in
stdenv.mkDerivation (finalAttrs: {
  pname = "pond-interface";
  version = "0-unstable-2026-11-01";

  inherit src;

  nativeBuildInputs = [
    cmake
    ninja
    pkg-config
    makeWrapper
    patchelf
    qt6.qtshadertools
    qt6.wrapQtAppsHook
  ];

  buildInputs = [
    qt6.qtbase
    qt6.qtdeclarative
    kdePackages.networkmanager-qt
    wayland
  ];

  # The wrappers below are built by hand: Pond's own plugins must be on the
  # QML path, which wrapQtAppsHook knows nothing about.
  dontWrapQtApps = true;

  cmakeFlags = [
    (lib.cmakeBool "BUILD_SHARED_LIBS" true)
    (lib.cmakeFeature "POND_QML_INSTALL_DIR" qt6.qtbase.qtQmlPrefix)
  ];

  postPatch = ''
    ${lib.optionalString (overlaySrc != null) ''
      # Hyprland adapter and settings port (./overlay mirrors the upstream tree).
      cp -rT ${overlaySrc} .
      chmod -R u+w .
    ''}

    find . -name __pycache__ -type d -prune -exec rm -rf {} +

    # Native modules: upstream's root project has no install rules and
    # Pond.Screenshot.Native is a separate project.
    cat ${./install-rules.cmake} >> CMakeLists.txt
    substituteInPlace screenshot/native/CMakeLists.txt \
      --replace-fail "lib/qt6/qml" "\''${POND_QML_INSTALL_DIR}"

    # Rebuild every shader with this Qt, replacing upstream's committed .qsb.
    while IFS= read -r -d "" shader; do
      qsb --glsl "100 es,120,150" --hlsl 50 --msl 12 -o "$shader.qsb" "$shader"
    done < <(find shell lockscreen wallpaper screenshot \( -name '*.frag' -o -name '*.vert' \) -print0)

    # Runtime paths. Python is called by absolute path rather than placed on
    # PATH, so programs launched from the shell keep the user's python3.
    pondPython=$out/libexec/pond-interface/bin/pond-python3
    while IFS= read -r -d "" file; do
      sed -E -i \
        -e "s|/usr/share/pond-interface|$out/share/pond-interface|g" \
        -e "s|/usr/share/X11/xkb|${xkeyboard-config}/share/X11/xkb|g" \
        -e "s|/usr/share/zoneinfo|${tzdata}/share/zoneinfo|g" \
        -e "s|/usr/bin/nmcli\b|${networkmanager}/bin/nmcli|g" \
        -e "s|/usr/bin/iw\b|${iw}/bin/iw|g" \
        -e "s|/usr/bin/id\b|${coreutils}/bin/id|g" \
        -e "s|/usr/bin/getent\b|${getent}/bin/getent|g" \
        -e "s|/usr/bin/systemctl\b|${systemd}/bin/systemctl|g" \
        -e "s|/usr/bin/timedatectl\b|${systemd}/bin/timedatectl|g" \
        -e "s|/usr/bin/vicinae\b|${vicinae}/bin/vicinae|g" \
        -e "s|/usr/bin/qs\b|$out/libexec/pond-interface/bin/qs|g" \
        -e "s|^#!/usr/bin/(env )?python3$|#!$pondPython|" \
        -e "s|qs -c pond-shell|$out/bin/pond-shell|g" \
        "$file"
      case "$file" in
        *.qml|*.js) sed -E -i "s#(^|[\"'(;&| ])python3([\"' ]|$)#\1$pondPython\2#g" "$file" ;;
      esac
    done < <(find shell lockscreen wallpaper screenshot config -type f \
      \( -name '*.qml' -o -name '*.js' -o -name '*.py' -o -name '*.desktop' \
         -o -name '*.jsonc' -o -name '*.json' -o -name '*.sh' \) -print0)
  '';

  postInstall = ''
    root=$NIX_BUILD_TOP/$sourceRoot
    share=$out/share/pond-interface
    qml=$out/${qt6.qtbase.qtQmlPrefix}/Pond

    mkdir -p $share
    cp -r $root/shell $share/shell
    # The greeter is Pond's SDDM-on-niri login screen; this package provides
    # only the in-session locker. Its C++ sources live in Pond.System.
    cp -r $root/lockscreen $share/lockscreen
    rm -rf $share/lockscreen/greeter $share/lockscreen/frontend/system

    mkdir -p $qml/Screenshot $qml/Wallpaper $qml/Daylight
    cp -r $root/wallpaper/daylight/. $qml/Daylight/
    find $root/wallpaper -mindepth 1 -maxdepth 1 ! -name daylight -exec cp -r {} $qml/Wallpaper/ \;
    find $root/screenshot -mindepth 1 -maxdepth 1 ! -name native -exec cp -r {} $qml/Screenshot/ \;
    find $share $qml \( -name '*.frag' -o -name '*.vert' \) -delete

    # Desktop-environment defaults, for session modules to adopt as wanted.
    mkdir -p $share/config
    for dir in fastfetch gtk-3.0 gtk-4.0 glib-2.0; do
      cp -r $root/config/$dir $share/config/
    done
    rm $share/config/fastfetch/fastfetch-flowers.ttf
    install -Dm644 $root/config/applications/pond-manual.desktop \
      $out/share/applications/pond-manual.desktop

    # The original lock-screen dusk artwork (1920x1080). Upstream ships it in
    # pond-lockscreen but no longer references it; it is offered as a static
    # wallpaper. Pond's live background is the procedural Pond.Daylight sky.
    install -Dm644 $root/lockscreen/frontend/assets/lock-background.png \
      $out/share/wallpapers/pond/pond-dusk.png
    cat > $out/share/wallpapers/pond/ATTRIBUTION <<EOF
    pond-dusk.png: lockscreen/frontend/assets/lock-background.png from
    https://github.com/bflycomputer/pond-interface at ${src.rev}.
    Copyright (c) 2026, Butterfly Interaction Inc. BSD-3-Clause; see
    $out/share/licenses/pond-interface/LICENSE.
    EOF
    install -Dm644 $root/LICENSE $out/share/licenses/pond-interface/LICENSE

    libexec=$out/libexec/pond-interface/bin
    makeWrapper ${pythonEnv}/bin/python3 $libexec/pond-python3 \
      --prefix GI_TYPELIB_PATH : ${typelibPath}
    makeWrapper ${pondQuickshell}/bin/qs $libexec/qs \
      --prefix PATH : $libexec:${lib.makeBinPath runtimeInputs} \
      --prefix NIXPKGS_QT6_QML_IMPORT_PATH : $out/${qt6.qtbase.qtQmlPrefix} \
      --prefix QT_PLUGIN_PATH : ${qt6.qtimageformats}/${qt6.qtbase.qtPluginPrefix} \
      --set FONTCONFIG_FILE ${fonts.fontsConf} \
      --set-default POND_PAM_SERVICE pond-lock \
      --set POND_INTERFACE_DATA $share
    ln -s qs $libexec/quickshell
    makeWrapper $libexec/qs $out/bin/pond-shell --add-flags "-p $share/shell"
    makeWrapper $libexec/qs $out/bin/pond-lock --add-flags "-p $share/lockscreen"
  '';

  # The QML plugins load their backing libraries from the same directory.
  postFixup = ''
    for plugin in $out/${qt6.qtbase.qtQmlPrefix}/Pond/System/libpond_systemplugin.so \
                  $out/${qt6.qtbase.qtQmlPrefix}/Pond/Screenshot/Native/libpond_screenshot_dragplugin.so; do
      patchelf --add-rpath '$ORIGIN' "$plugin"
    done
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    qml=$out/${qt6.qtbase.qtQmlPrefix}/Pond

    leftovers=$(grep -rnIE '/usr/(bin|sbin|lib|share)/[A-Za-z]|(^|[^A-Za-z0-9_./-])python3([^A-Za-z0-9_.-]|$)' \
      $out/share/pond-interface $qml $out/share/applications \
      | grep -vE 'pond-python3|/nix/store/[^ ]*python3' || true)
    if [ -n "$leftovers" ]; then
      echo "unresolved distribution paths or bare commands:" >&2
      echo "$leftovers" >&2
      exit 1
    fi
    # Pond's Arch package manager is replaced by the adapter overlay; until it
    # is, the update card's backend is reported here rather than hidden.
    if grep -rnIF '"pm"' $out/share/pond-interface; then
      echo "warning: Pond's Arch 'pm' updater is still referenced (see README)" >&2
    fi

    for file in System/qmldir System/libpond_system.so System/libpond_systemplugin.so \
                Screenshot/qmldir Screenshot/Native/qmldir Screenshot/Native/libpond_screenshot_dragplugin.so \
                Wallpaper/qmldir Daylight/qmldir Daylight/sky.frag.qsb; do
      test -e $qml/$file || { echo "missing $qml/$file" >&2; exit 1; }
    done
    test -e $out/share/pond-interface/lockscreen/frontend/shaders/soft-light.frag.qsb
    test -e $out/share/pond-interface/shell/shell.qml
    test -e $out/share/pond-interface/lockscreen/shell.qml

    for plugin in $qml/System/libpond_systemplugin.so $qml/Screenshot/Native/libpond_screenshot_dragplugin.so; do
      if ldd "$plugin" | grep 'not found'; then
        echo "$plugin has unresolved libraries" >&2
        exit 1
      fi
    done

    $out/libexec/pond-interface/bin/pond-python3 -c '
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gio, GLib, GdkPixbuf
    from PyQt6.QtCore import QDateTime, QLocale, QTimeZone
    '
    for helper in $(find $out/share/pond-interface $qml -name '*.py'); do
      $out/libexec/pond-interface/bin/pond-python3 -m py_compile "$helper"
    done
    find $out -name __pycache__ -type d -prune -exec rm -rf {} +

    $out/libexec/pond-interface/bin/qs --version | grep -F ${pondQuickshell.version}
    runHook postInstallCheck
  '';

  passthru = {
    quickshell = pondQuickshell;
    fonts = fonts.packages;
    inherit (fonts) fontsConf gramercyAvailable;
    inherit gramercyFallbackFamily;
    inherit pythonEnv runtimeInputs;
    upstreamRev = src.rev;
    tests.smoke = callPackage ./smoke.nix { pond-interface = finalAttrs.finalPackage; };
  };

  meta = {
    description = "Pond desktop shell and session lock for Quickshell";
    homepage = "https://github.com/bflycomputer/pond-interface";
    license = lib.licenses.bsd3;
    platforms = lib.platforms.linux;
    mainProgram = "pond-shell";
  };
})
