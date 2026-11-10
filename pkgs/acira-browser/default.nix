{
  lib,
  stdenv,
  fetchurl,
  appimageTools,
  autoPatchelfHook,
  copyDesktopItems,
  makeDesktopItem,
  addDriverRunpath,
  alsa-lib,
  at-spi2-core,
  atk,
  cairo,
  cups,
  dbus,
  expat,
  gdk-pixbuf,
  glib,
  gtk3,
  krb5,
  libdrm,
  libGL,
  libgbm,
  libnotify,
  libpulseaudio,
  libsecret,
  libxkbcommon,
  mesa,
  nspr,
  nss,
  pango,
  pciutils,
  pipewire,
  systemd,
  vulkan-loader,
  libx11,
  libxcb,
  libxcomposite,
  libxdamage,
  libxext,
  libxfixes,
  libxrandr,
  libxshmfence,
  zlib,
}:

# Upstream's only Linux binary is an AppImage. Extract at build time and
# patch the payload into a regular Nix package; no FUSE or upstream installer.
# Bump version, release revision and hash together to update.
let
  # dlopen'd at runtime rather than NEEDED, so autoPatchelf cannot see them:
  # they have to be on LD_LIBRARY_PATH as well as in buildInputs.
  runtimeLibs = [
    stdenv.cc.cc.lib
    alsa-lib
    at-spi2-core
    atk
    cairo
    cups
    dbus
    expat
    gdk-pixbuf
    glib
    gtk3
    krb5 # libgssapi_krb5, for SPNEGO/Negotiate proxies
    libdrm
    libGL
    libgbm
    libnotify
    libpulseaudio
    libsecret # Electron safeStorage keyring backend
    libxkbcommon
    mesa
    nspr
    nss
    pango
    pciutils # libpci, GPU probing
    pipewire # screen capture + WebRTC
    systemd # libudev
    vulkan-loader
    zlib
    libx11
    libxcomposite
    libxdamage
    libxext
    libxfixes
    libxrandr
    libxcb
    libxshmfence
  ];
in
stdenv.mkDerivation (finalAttrs: {
  pname = "acira-browser";
  version = "0.1.0-beta.13.4";

  src = appimageTools.extract {
    inherit (finalAttrs) pname version;
    src = fetchurl {
      url = "https://github.com/Acira-dev/AciraBrowserUpdates/releases/download/v${finalAttrs.version}-linux-24e08e3c/AciraBrowser-${finalAttrs.version}-linux-x86_64.AppImage";
      hash = "sha256-WRFTuT4EOK+TKcHvf9mCtuDQUzu5JLpjTr3D1VmTSVA=";
    };
  };

  nativeBuildInputs = [
    autoPatchelfHook
    copyDesktopItems
  ];

  buildInputs = runtimeLibs;

  installPhase = ''
    runHook preInstall

    app=$out/libexec/acira-browser
    mkdir -p "$app"
    cp -r . "$app/"
    rm -rf "$app/AppRun" "$app/usr" "$app/acira-browser.desktop" "$app/AciraBrowser.png" "$app/.DirIcon"

    mkdir -p $out/share/icons/hicolor
    cp -r usr/share/icons/hicolor/. $out/share/icons/hicolor/
    for icon in "$out"/share/icons/hicolor/*/apps/AciraBrowser.png; do
      mv "$icon" "$(dirname "$icon")/acira-browser.png"
    done

    mkdir -p $out/bin
    cat > $out/bin/acira-browser <<EOF
    #!${stdenv.shell}
    export LD_LIBRARY_PATH=${lib.makeLibraryPath runtimeLibs}:${addDriverRunpath.driverLink}/lib\''${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}

    if [ -e /run/wrappers/bin/__chrome-sandbox ]; then
      export CHROME_DEVEL_SANDBOX=/run/wrappers/bin/__chrome-sandbox
    fi

    # A parent Electron app (T3 Code, an editor terminal) can leak this, which
    # makes the binary start as plain Node and reject every Chromium flag.
    unset ELECTRON_RUN_AS_NODE

    export CHROME_DESKTOP=acira-browser.desktop
    # electron-updater requires APPIMAGE to replace an image. Store-owned.
    unset APPIMAGE APPDIR

    ozone=
    if [ -n "\''${NIXOS_OZONE_WL:-}" ] || [ "\''${XDG_SESSION_TYPE:-}" = wayland ]; then
      ozone="--ozone-platform-hint=auto --enable-features=WaylandWindowDecorations,WebRTCPipeWireCapturer"
    fi

    exec "$app/AciraBrowser" --class=acira-browser \$ozone "\$@"
    EOF
    chmod +x $out/bin/acira-browser

    runHook postInstall
  '';

  desktopItems = [
    (makeDesktopItem {
      name = "acira-browser";
      exec = "acira-browser %U";
      icon = "acira-browser";
      desktopName = "Acira Browser";
      comment = "An independent browser";
      categories = [
        "Network"
        "WebBrowser"
      ];
      startupWMClass = "acira-browser";
      mimeTypes = [
        "text/html"
        "application/xhtml+xml"
        "x-scheme-handler/http"
        "x-scheme-handler/https"
      ];
    })
  ];

  meta = {
    description = "Acira Browser (upstream Linux release unpacked and patched)";
    homepage = "https://acirabrowser.com";
    downloadPage = "https://github.com/Acira-dev/AciraBrowserUpdates/releases";
    license = lib.licenses.agpl3Only;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    platforms = [ "x86_64-linux" ];
    mainProgram = "acira-browser";
  };
})
