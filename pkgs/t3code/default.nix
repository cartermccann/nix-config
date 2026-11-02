{
  lib,
  t3code,
  fetchFromGitHub,
  fetchPnpmDeps,
  pnpm_11,
  stdenv,
}:
let
  sources = lib.importJSON ./sources.json;
  unwrapped = t3code.unwrapped.overrideAttrs (old: rec {
    inherit (sources) version;
    src = fetchFromGitHub {
      owner = "pingdotgg";
      repo = "t3code";
      tag = "v${version}";
      hash = sources.srcHash;
    };
    pnpmDeps = fetchPnpmDeps {
      inherit (old) pname pnpmWorkspaces;
      inherit version src;
      pnpm = pnpm_11;
      fetcherVersion = 4;
      hash = sources.pnpmHash;
    };
  });
in
(t3code.override { t3code-unwrapped = unwrapped; }).overrideAttrs (old: {
  # Electron does not load libstdc++ before node-pty's native addon.
  buildCommand = old.buildCommand + ''
    wrapProgram "$out/bin/t3code-desktop" \
      --prefix LD_LIBRARY_PATH : "${lib.makeLibraryPath [ stdenv.cc.cc.lib ]}"

    # Native Wayland windows use this app ID. Match it in the desktop entry
    # so Pond/Quickshell can associate the window with the installed icon.
    # Replace symlinkJoin's link before editing; the unwrapped output is immutable.
    desktopFile="$out/share/applications/t3code.desktop"
    rm "$desktopFile"
    cp "${unwrapped}/share/applications/t3code.desktop" "$desktopFile"
    chmod u+w "$desktopFile"
    substituteInPlace "$desktopFile" \
      --replace-fail 'StartupWMClass=t3code' 'StartupWMClass=com.t3tools.T3Code'
  '';
})
