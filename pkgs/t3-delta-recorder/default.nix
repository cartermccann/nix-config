{
  lib,
  stdenvNoCC,
  makeWrapper,
  python3,
  git,
}:
stdenvNoCC.mkDerivation {
  pname = "t3-delta-recorder";
  version = "0.1.0";
  src = lib.cleanSourceWith {
    src = ../../scripts/t3-delta-recorder;
    filter = path: _type: baseNameOf path != "__pycache__" && !(lib.hasSuffix ".pyc" path);
  };
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [
    python3
    git
  ];
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    export PYTHONDONTWRITEBYTECODE=1
    export PYTHONPATH="$PWD"
    python3 -m unittest discover -s tests -v
    runHook postCheck
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib/t3-delta-recorder" "$out/bin"
    cp -r t3_delta "$out/lib/t3-delta-recorder/"
    makeWrapper ${python3}/bin/python3 "$out/bin/t3-delta" \
      --add-flags "-B -m t3_delta" \
      --set PYTHONPATH "$out/lib/t3-delta-recorder" \
      --prefix PATH : ${lib.makeBinPath [ git ]}
    runHook postInstall
  '';
  meta = {
    description = "Prospective private Delta recording for approved T3 checkouts";
    mainProgram = "t3-delta";
    platforms = lib.platforms.linux;
  };
}
