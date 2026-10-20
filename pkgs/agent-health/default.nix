{
  lib,
  stdenvNoCC,
  makeWrapper,
  python3,
}:
stdenvNoCC.mkDerivation {
  pname = "agent-health";
  version = "0.1.0";
  src = lib.cleanSourceWith {
    src = ../../scripts/agent-health;
    filter = path: _type: baseNameOf path != "__pycache__" && !(lib.hasSuffix ".pyc" path);
  };
  activation = lib.cleanSourceWith {
    src = ../../scripts/agent-activation;
    filter = path: _type: baseNameOf path != "__pycache__" && !(lib.hasSuffix ".pyc" path);
  };
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ python3 ];
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    export PYTHONDONTWRITEBYTECODE=1
    export PYTHONPATH="$PWD/adapters"
    python3 -m unittest discover -s tests -v
    python3 adapters/claude_health.py --self-test
    python3 -m unittest discover -s "$activation" -p 'test_*.py' -v
    runHook postCheck
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib/agent-health" "$out/lib/agent-activation" "$out/bin"
    cp -r . "$out/lib/agent-health/"
    cp -r "$activation"/. "$out/lib/agent-activation/"
    makeWrapper ${python3}/bin/python3 "$out/bin/agent-health" \
      --add-flags "-B $out/lib/agent-health/agent-health"
    makeWrapper ${python3}/bin/python3 "$out/bin/agent-activation" \
      --add-flags "-B $out/lib/agent-activation/activation.py"
    runHook postInstall
  '';
  meta = {
    description = "Private daily aggregate tool health and read-only guard activation diagnostics";
    mainProgram = "agent-health";
    platforms = lib.platforms.linux;
  };
}
