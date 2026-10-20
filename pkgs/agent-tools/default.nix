{
  lib,
  stdenvNoCC,
  makeWrapper,
  python3,
  git,
  ripgrep,
  fish,
}:
stdenvNoCC.mkDerivation {
  pname = "agent-tools";
  version = "0.1.0";
  src = lib.cleanSourceWith {
    src = ../../scripts/agent-tools;
    filter = path: _type: baseNameOf path != "__pycache__" && !(lib.hasSuffix ".pyc" path);
  };
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [
    python3
    git
    ripgrep
    fish
  ];
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    export PYTHONDONTWRITEBYTECODE=1
    export HOME="$TMPDIR/test-home"
    mkdir -p "$HOME"
    patchShebangs agent-tools
    python3 -m unittest discover -s tests -v
    runHook postCheck
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib/agent-tools" "$out/bin"
    cp -r agent_tools "$out/lib/agent-tools/"
    cp agent-tools "$out/lib/agent-tools/agent-tools"
    makeWrapper ${python3}/bin/python3 "$out/bin/agent-tools" \
      --add-flags "-B $out/lib/agent-tools/agent-tools" \
      --prefix PATH : ${
        lib.makeBinPath [
          git
          ripgrep
        ]
      }
    runHook postInstall
  '';
  meta = {
    description = "Bounded command execution, repo diagnostics and search for coding agents";
    mainProgram = "agent-tools";
    platforms = lib.platforms.linux;
  };
}
