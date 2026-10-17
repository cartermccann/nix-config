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
  '';
})
