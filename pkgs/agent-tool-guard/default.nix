{
  lib,
  stdenvNoCC,
  makeWrapper,
  python3,
  nodejs_24,
}:
stdenvNoCC.mkDerivation {
  pname = "agent-tool-guard";
  version = "0.1.0";
  src = ../../scripts/agent-tool-guard;
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [
    python3
    nodejs_24
  ];
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    export PYTHONDONTWRITEBYTECODE=1
    python3 -m unittest discover -s guard -p 'test_*.py'
    GUARD_PYTHON=${python3}/bin/python3 node guard/test_plugin.mjs
    runHook postCheck
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/share/agent-tool-guard/guard" "$out/bin"
    cp guard/core.py guard/hook.py guard/install.py guard/opencode-plugin.template.js "$out/share/agent-tool-guard/guard/"
    makeWrapper ${python3}/bin/python3 "$out/bin/agent-tool-guard" \
      --add-flags "$out/share/agent-tool-guard/guard/hook.py"
    makeWrapper ${python3}/bin/python3 "$out/bin/agent-tool-guard-install" \
      --add-flags "$out/share/agent-tool-guard/guard/install.py"
    runHook postInstall
  '';
  meta = {
    description = "Shared Gmail pacing and targeted MCP argument validation for coding agents";
    platforms = lib.platforms.linux;
    mainProgram = "agent-tool-guard";
  };
}
