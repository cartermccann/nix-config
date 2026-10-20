{
  lib,
  stdenvNoCC,
  makeWrapper,
  python3,
  agentTools,
  agentToolGuard,
}:
stdenvNoCC.mkDerivation {
  pname = "agent-recovery";
  version = "0.1.0";
  src = lib.cleanSourceWith {
    src = ../../scripts/agent-recovery;
    filter = path: _type: baseNameOf path != "__pycache__" && !(lib.hasSuffix ".pyc" path);
  };
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [
    python3
    agentTools
  ];
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    export PYTHONDONTWRITEBYTECODE=1
    export AGENT_RECOVERY_TOOLS=${agentTools}/bin/agent-tools
    export AGENT_RECOVERY_GUARD_ROOT=${agentToolGuard}/share/agent-tool-guard/guard
    python3 -m unittest discover -s . -p 'test_*.py' -v
    python3 agent-recovery verify
    runHook postCheck
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib/agent-recovery" "$out/bin"
    cp -r . "$out/lib/agent-recovery/"
    makeWrapper ${python3}/bin/python3 "$out/bin/agent-recovery" \
      --add-flags "-B $out/lib/agent-recovery/agent-recovery" \
      --set AGENT_RECOVERY_TOOLS ${agentTools}/bin/agent-tools \
      --set AGENT_RECOVERY_GUARD_ROOT ${agentToolGuard}/share/agent-tool-guard/guard
    runHook postInstall
  '';
  meta = {
    description = "Synthetic recovery checks and scenario contracts for coding agents";
    mainProgram = "agent-recovery";
    platforms = lib.platforms.linux;
  };
}
