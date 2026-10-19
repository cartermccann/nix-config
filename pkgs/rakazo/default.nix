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

# Rakazo desktop client (open-source Grok Bot alternative, elie222/rakazo).
#
# Upstream only ships Linux as an AppImage, so this unpacks it at build time
# with appimageTools.extract and treats the payload like ../grok-bot: the
# vendored Electron is autoPatchelf'd and the launcher supplies the dlopen'd
# libs. Nothing runs as an AppImage. Hash is the release SHA256SUMS entry.
# In-app auto-update cannot replace a store path; bump `version` + `hash`.
# Point it at the self-hosted stack with "Use an existing server".
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
  pname = "rakazo";
  version = "0.2.0";

  src = appimageTools.extract {
    inherit (finalAttrs) pname version;
    src = fetchurl {
      url = "https://github.com/elie222/rakazo/releases/download/v${finalAttrs.version}/Rakazo-${finalAttrs.version}.AppImage";
      hash = "sha256-3g0/V7zsKJvAzEqN44QvES0pDfFFmMXYOnyn4dNk5mk=";
    };
  };

  nativeBuildInputs = [
    autoPatchelfHook
    copyDesktopItems
  ];

  buildInputs = runtimeLibs;

  autoPatchelfIgnoreMissingDeps = true;

  installPhase = ''
    runHook preInstall

    app=$out/libexec/rakazo
    mkdir -p "$app"
    cp -r . "$app/"
    rm -rf "$app/AppRun" "$app/usr" "$app/rakazo.desktop" "$app/rakazo.png" "$app/.DirIcon"

    mkdir -p $out/share/icons/hicolor
    cp -r usr/share/icons/hicolor/. $out/share/icons/hicolor/

    mkdir -p $out/bin
    cat > $out/bin/rakazo <<EOF
    #!${stdenv.shell}
    export LD_LIBRARY_PATH=${lib.makeLibraryPath runtimeLibs}:${addDriverRunpath.driverLink}/lib\''${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}

    if [ -e /run/wrappers/bin/__chrome-sandbox ]; then
      export CHROME_DEVEL_SANDBOX=/run/wrappers/bin/__chrome-sandbox
    fi

    # A parent Electron app (T3 Code, an editor terminal) can leak this, which
    # makes the binary start as plain Node and reject every Chromium flag.
    unset ELECTRON_RUN_AS_NODE

    export CHROME_DESKTOP=rakazo.desktop

    ozone=
    if [ -n "\''${NIXOS_OZONE_WL:-}" ] || [ "\''${XDG_SESSION_TYPE:-}" = wayland ]; then
      ozone="--ozone-platform-hint=auto --enable-features=WaylandWindowDecorations,WebRTCPipeWireCapturer"
    fi

    exec "$app/rakazo" --class=rakazo \$ozone "\$@"
    EOF
    chmod +x $out/bin/rakazo

    runHook postInstall
  '';

  desktopItems = [
    (makeDesktopItem {
      name = "rakazo";
      exec = "rakazo %U";
      icon = "rakazo";
      desktopName = "Rakazo";
      comment = "Rakazo desktop client";
      categories = [
        "Development"
        "Utility"
      ];
      startupWMClass = "Rakazo";
    })
  ];

  meta = {
    description = "Rakazo desktop client (upstream Electron AppImage unpacked and patched)";
    homepage = "https://github.com/elie222/rakazo";
    downloadPage = "https://github.com/elie222/rakazo/releases";
    license = lib.licenses.asl20;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    platforms = [ "x86_64-linux" ];
    mainProgram = "rakazo";
  };
})
