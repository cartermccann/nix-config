{
  lib,
  stdenv,
  fetchurl,
  _7zz,
  electron,
  nodejs,
  node-gyp,
  patchelf,
  python3,
  copyDesktopItems,
  makeDesktopItem,
  dconf,
  gsettings-desktop-schemas,
  gtk3,
  librsvg,
}:

# Granola — AI meeting notepad. Upstream ships macOS and Windows only, but the
# app is Electron, so the macOS .dmg already contains the entire app (JS in
# app.asar, C++ source for its one load-bearing native module). This derivation
# does what tirtha4/Granola-for-Linux's shell script does, declaratively, and
# swaps that script's downloaded Electron prebuilt for nixpkgs' electron_42 —
# which is the whole reason this is a derivation and not a run of the script:
# the upstream prebuilt cannot execute on NixOS at all.
#
# Three non-obvious steps, all of which the app fails without:
#
#  1. app.asar's platform string is patched to report Windows. api.granola.ai
#     answers 500 to any request carrying platform=linux, sign-in included. The
#     replacement is byte-for-byte the same length because an asar header
#     records file offsets; Electron does not verify asar integrity on Linux.
#  2. better-sqlite3-multiple-ciphers is rebuilt from the C++ source inside the
#     bundle. Granola ships a *fork* carrying an updateHook() that no published
#     build has, so a stock prebuilt gets you a window that dies on
#     "r.updateHook is not a function". Only binding.gyp is missing from the
#     bundle, hence bs3Src below.
#  3. nixpkgs' electron dist is *copied* into a private dist directory whose
#     resources/ holds Granola's payload, rather than symlinked to.
#     process.resourcesPath derives from /proc/self/exe, which resolves
#     symlinks, so a symlinked executable would send the app looking for its
#     asar and icons inside nixpkgs' electron store path. Loading the payload
#     from resources/ (instead of passing it as an argv path) is also what
#     keeps app.isPackaged true, which every resource path in the app branches
#     on. Copying the whole dist rather than farming symlinks around a copied
#     binary is the cheaper of the two: it drops electron out of the runtime
#     closure instead of paying for both.
#  4. Linux system-audio capture is narrowed from Chromium's special
#     loopbackAllDevices source to the monitor of the meeting-only PipeWire sink
#     declared in modules/audio.nix. Both identifiers are 18 bytes, preserving
#     the asar offsets. The exact-one guard intentionally fails an upgrade if
#     Granola changes this code path instead of silently recording every app.
#
# Unfree, and the .dmg is Granola's proprietary build fetched from their own
# CDN. Bumping: set version + hash from
#   curl -sI https://api.granola.ai/v1/download-latest | grep -i location
# and re-check that the platform patterns below still match. Granola's own
# auto-updater cannot write to the store, so updates are declarative only.
let
  # The bundled fork's version. binding.gyp is the one file the app bundle
  # omits, so it comes from the matching npm release.
  bs3Version = "12.9.0";

  bs3Src = fetchurl {
    url = "https://registry.npmjs.org/better-sqlite3-multiple-ciphers/-/better-sqlite3-multiple-ciphers-${bs3Version}.tgz";
    hash = "sha512-471izuwJKCgvCGJ0VP10Z0IN7NWFW+pk/A8KXItZXVJ0V2AEAB5HZfmc9U3VLMmJ46bRaPbdgaBbgWpBJ/biUg==";
  };

  # Mirrors what nixpkgs' own electron wrapper sets. We bypass that wrapper
  # (see note 3 above), so these have to be set here or GTK file dialogs and
  # SVG icon loading break.
  gsettingsSchemas = lib.concatStringsSep ":" [
    "${gsettings-desktop-schemas}/share/gsettings-schemas/${gsettings-desktop-schemas.name}"
    "${gtk3}/share/gsettings-schemas/${gtk3.name}"
  ];
in
stdenv.mkDerivation (finalAttrs: {
  pname = "granola";
  version = "7.668.2";

  src = fetchurl {
    url = "https://dr2v7l5emb758.cloudfront.net/${finalAttrs.version}/Granola-${finalAttrs.version}-mac-universal.dmg";
    hash = "sha256-uj7S6kAgL4TGA4Z2nitD8rhozCxF2AzySLtIdruAQkI=";
  };

  nativeBuildInputs = [
    _7zz
    copyDesktopItems
    node-gyp
    nodejs
    patchelf
    python3
  ];

  # stdenv's fixup runs `patchelf --shrink-rpath`, which treats an RPATH entry
  # as unused when no DT_NEEDED library lives there — and everything Chromium
  # dlopens (libglvnd, vulkan-loader, pipewire, libsecret, libnotify,
  # pulseaudio, speech-dispatcher, pciutils) is exactly that. Letting it run
  # strips 13 of electron's 36 RPATH entries and empties libGLESv2.so's
  # outright, and the app then starts with "Could not dlopen native EGL:
  # libEGL.so.1" and no GPU at all. Keep the RPATH as nixpkgs built it.
  # Skipping strip also keeps the copied binary byte-identical to electron's,
  # so `nix store optimise` can hardlink the 219 MB back down to nothing.
  dontPatchELF = true;
  dontStrip = true;

  # p7zip cannot read the dmg's LZFSE compression; 7zz can. Only the payload is
  # extracted: every Mac binary in the bundle sits behind a darwin check.
  unpackPhase = ''
    runHook preUnpack

    res=Granola/Granola.app/Contents/Resources
    7zz x $src "$res/app.asar" "$res/app.asar.unpacked" "$res/icons" -o. -y > /dev/null
    mv "$res" payload
    chmod -R u+w payload

    runHook postUnpack
  '';

  buildPhase = ''
    runHook preBuild

    echo "patching the platform and Linux audio capture in app.asar"
    python3 - payload/app.asar <<'PYEOF'
    import pathlib, sys

    asar = pathlib.Path(sys.argv[1])
    data = asar.read_bytes()
    platform_patched = 0
    # Same-length replacements: the asar header records file offsets.
    for pat in (b'?`Windows`:window.electron.platform', b'?`Windows`:process.platform'):
        rep = b'?`Windows`:`Windows`'.ljust(len(pat))
        platform_patched += data.count(pat)
        data = data.replace(pat, rep)
    if platform_patched == 0:
        sys.exit("no platform fallback found; Granola's bundler output changed")

    # Anchored on the non-win32/non-darwin `:t(...)` else branch: since 7.6xx
    # the identifier also appears in the explicit process.platform==='win32'
    # branch and a Windows mode selector, which Linux never reaches.
    capture_pat = b':t({audio:{name:`All loopback devices`,id:`loopbackAllDevices`}})'
    capture_rep = capture_pat.replace(b'loopbackAllDevices', b'granola_mt.monitor')
    if len(capture_pat) != len(capture_rep):
        sys.exit("Granola audio capture replacement changed length")
    capture_matches = data.count(capture_pat)
    if capture_matches != 1:
        sys.exit(
            "expected exactly one Linux loopbackAllDevices capture branch; "
            f"found {capture_matches}; Granola's audio capture path changed"
        )
    data = data.replace(capture_pat, capture_rep)

    asar.write_bytes(data)
    print(
        f"  rewrote {platform_patched} platform fallback(s) and "
        "pinned Linux audio capture to granola_mt.monitor"
    )
    PYEOF

    echo "rebuilding better-sqlite3-multiple-ciphers for Electron ${electron.version}"
    bs3=payload/app.asar.unpacked/node_modules/better-sqlite3-multiple-ciphers
    tar xzf ${bs3Src} package/binding.gyp
    cp package/binding.gyp "$bs3/"

    export HOME=$TMPDIR

    # node-gyp is invoked through its own entrypoint rather than the nixpkgs
    # `node-gyp` wrapper, because that wrapper hard-exports
    # npm_config_nodedir=<its nodejs> and wins over --nodedir. Building against
    # Node's headers instead of Electron's breaks this two ways: wrong
    # NODE_MODULE_VERSION, and Node ships its own codec-less sqlite3.h that
    # shadows the SQLite3-Multiple-Ciphers amalgamation vendored in the bundle,
    # so sqlite3_key/sqlite3_rekey vanish and the compile fails.
    export npm_config_nodedir=${electron.headers}
    ( cd "$bs3" && node ${node-gyp}/lib/node_modules/node-gyp/bin/node-gyp.js \
        rebuild --release --arch=x64 \
        --nodedir=${electron.headers} --devdir=$TMPDIR/.node-gyp )

    # Ship the two loadable modules, not node-gyp's object files and static lib.
    find "$bs3/build" -mindepth 1 -maxdepth 1 ! -name Release -exec rm -rf {} +
    find "$bs3/build/Release" -mindepth 1 ! -name '*.node' -prune -exec rm -rf {} +

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    dist=$out/libexec/granola
    mkdir -p $out/libexec
    cp -r ${electron}/libexec/electron "$dist"
    chmod -R u+w "$dist"

    # Named for the app so the process, and the WM class Chromium derives from
    # it, read as granola rather than electron.
    mv "$dist/electron" "$dist/granola"

    # nixpkgs' electron binaries carry their own dist directory in RPATH, to
    # find the libffmpeg/libEGL/libGLESv2 that sit beside them. Left alone that
    # would keep the whole electron package in this closure *and* load those
    # libraries from there rather than from the copy right next to us, so point
    # those entries at $ORIGIN instead.
    electronDist=${electron}/libexec/electron
    origin='$ORIGIN'
    for elf in "$dist"/granola "$dist"/chrome_crashpad_handler "$dist"/chrome-sandbox "$dist"/*.so*; do
      rpath=$(patchelf --print-rpath "$elf" 2>/dev/null) || continue
      case "$rpath" in
        *"$electronDist"*) patchelf --set-rpath "''${rpath//$electronDist/$origin}" "$elf" ;;
      esac
    done

    rm "$dist/resources/default_app.asar" # the "welcome to Electron" demo
    cp -r payload/app.asar payload/app.asar.unpacked payload/icons "$dist/resources/"
    install -Dm644 payload/icons/icon.png $out/share/icons/hicolor/512x512/apps/granola.png

    mkdir -p $out/bin
    cat > $out/bin/granola <<EOF
    #!${stdenv.shell}
    export GDK_PIXBUF_MODULE_FILE="\''${GDK_PIXBUF_MODULE_FILE:-${librsvg}/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache}"
    export GIO_EXTRA_MODULES="${dconf.lib}/lib/gio/modules\''${GIO_EXTRA_MODULES:+:\$GIO_EXTRA_MODULES}"
    export XDG_DATA_DIRS="${gsettingsSchemas}\''${XDG_DATA_DIRS:+:\$XDG_DATA_DIRS}"

    # Chromium's unprivileged-userns sandbox is preferred and works here; the
    # setuid helper is only reachable if security.chromiumSuidSandbox.enable is
    # on, and a store path can never be setuid.
    if [ -e /run/wrappers/bin/__chrome-sandbox ]; then
      export CHROME_DEVEL_SANDBOX=/run/wrappers/bin/__chrome-sandbox
    fi

    ozone=
    if [ -n "\''${NIXOS_OZONE_WL:-}" ] || [ "\''${XDG_SESSION_TYPE:-}" = wayland ]; then
      ozone="--ozone-platform-hint=auto --enable-features=WaylandWindowDecorations,WebRTCPipeWireCapturer"
    fi

    exec "$dist/granola" \$ozone "\$@"
    EOF
    chmod +x $out/bin/granola

    runHook postInstall
  '';

  desktopItems = [
    (makeDesktopItem {
      name = "granola";
      exec = "granola %U";
      icon = "granola";
      desktopName = "Granola";
      comment = "AI notepad for meetings";
      categories = [
        "Office"
        "Utility"
      ];
      startupWMClass = "granola";
      # Sign-in bounces through granola://, so the handler has to be registered.
      mimeTypes = [ "x-scheme-handler/granola" ];
    })
  ];

  meta = {
    description = "AI notepad for meetings (macOS build repacked onto nixpkgs Electron)";
    homepage = "https://www.granola.ai";
    downloadPage = "https://notes.granola.ai/download";
    license = lib.licenses.unfree;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    platforms = [ "x86_64-linux" ];
    mainProgram = "granola";
  };
})
