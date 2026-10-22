{
  lib,
  stdenvNoCC,
  fetchurl,
  makeWrapper,
  installShellFiles,
}:

# Official xAI Grok CLI — the agentic "Grok Build" TUI installed via
# `curl -fsSL https://x.ai/cli/install.sh | bash`. It is NOT the community
# superagent-ai/grok-cli that ships in nixpkgs as `grok-cli`; this is xAI's own
# tool, distributed only as a self-contained, statically-linked release binary
# (no dynamic interpreter → no autoPatchelf needed).
#
# We wrap it with --no-auto-update because the Nix store binary is read-only:
# grok's built-in self-update would fail on every launch. Updates are done
# declaratively — bump `version` below, then update `hash` (nix will print the
# expected value on a mismatch). The stable channel's current version is at
# https://storage.googleapis.com/grok-build-public-artifacts/cli/stable
stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "grok-cli";
  version = "1.0.67";

  src = fetchurl {
    url = "https://storage.googleapis.com/grok-build-public-artifacts/cli/grok-${finalAttrs.version}-linux-x86_64";
    hash = "sha256-zjOxop43kB9W/lmRbNp6EUnm/TdxGPkY6Q2J+au1KlE=";
  };

  dontUnpack = true;

  nativeBuildInputs = [
    makeWrapper
    installShellFiles
  ];

  installPhase = ''
    runHook preInstall

    install -Dm755 $src $out/bin/.grok-unwrapped

    # `grok` and `agent` are the two entrypoints upstream creates; they resolve
    # to the same binary and differ only cosmetically in the usage line.
    makeWrapper $out/bin/.grok-unwrapped $out/bin/grok  --add-flags --no-auto-update
    makeWrapper $out/bin/.grok-unwrapped $out/bin/agent --add-flags --no-auto-update --argv0 agent

    # Completions are produced by the binary itself, so they always match the
    # pinned version. Give it a writable HOME so it doesn't fault in the sandbox.
    export HOME=$(mktemp -d)
    installShellCompletion --cmd grok \
      --bash <($out/bin/.grok-unwrapped completions bash) \
      --zsh  <($out/bin/.grok-unwrapped completions zsh) \
      --fish <($out/bin/.grok-unwrapped completions fish)

    runHook postInstall
  '';

  meta = {
    description = "Official xAI Grok agentic CLI (prebuilt static binary)";
    homepage = "https://x.ai/cli";
    license = lib.licenses.unfree;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    platforms = [ "x86_64-linux" ];
    mainProgram = "grok";
  };
})
