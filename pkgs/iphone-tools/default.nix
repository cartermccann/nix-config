{
  lib,
  callPackage,
  python313,
  runCommand,
  pyproject-nix,
  uv2nix,
  pyproject-build-systems,
}:
let
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
  overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
  buildSystemOverrides = final: prev: {
    lzfse = prev.lzfse.overrideAttrs (old: {
      nativeBuildInputs =
        (old.nativeBuildInputs or [ ]) ++ final.resolveBuildSystem { setuptools = [ ]; };
    });
  };
  pythonSet = (callPackage pyproject-nix.build.packages { python = python313; }).overrideScope (
    lib.composeManyExtensions [
      pyproject-build-systems.overlays.wheel
      overlay
      buildSystemOverrides
    ]
  );
  environment = pythonSet.mkVirtualEnv "iphone-tools-python" workspace.deps.default;
in
runCommand "iphone-tools-11.50.3" { } ''
  mkdir -p "$out/bin"
  ln -s ${environment}/bin/pymobiledevice3 "$out/bin/pymobiledevice3"
''
