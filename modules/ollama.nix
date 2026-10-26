{
  config,
  lib,
  pkgs,
  ...
}:

# Ollama in an OCI container (podman). GPU hosts get CUDA through
# nvidia-container-toolkit; CPU hosts run without passthrough. Containerized
# because the nixpkgs CUDA build (cicc) crashes on Blackwell (RTX 5070).

let
  # Model tags + tier lists live in lib/llm-models.nix (shared with the
  # shell.nix chat aliases).
  llm = import ../lib/llm-models.nix;

  tier = config.local.ollamaTier;
  models = llm.tiers.${tier};
  hasGpu = config.hardware.nvidia-container-toolkit.enable or false;

  tailnetBridgeScript = pkgs.writeShellScript "ollama-tailnet-bridge" ''
    set -euo pipefail
    tailnet_ip=$(${pkgs.iproute2}/bin/ip -j -4 address show dev tailscale0 | \
      ${pkgs.jq}/bin/jq -er '
        .[0].addr_info | map(select(.scope == "global")) |
        if length == 1 then .[0].local
        else error("Expected one IPv4 address on tailscale0") end
      ')
    exec ${pkgs.socat}/bin/socat "TCP-LISTEN:11434,bind=$tailnet_ip,fork,reuseaddr" TCP:127.0.0.1:11434
  '';

  preloadScript = pkgs.writeShellScript "ollama-preload-models" ''
    set -euo pipefail
    echo "Ollama model preload — tier: ${tier}"
    echo "Waiting for Ollama to be ready..."

    for i in $(seq 1 30); do
      if ${pkgs.curl}/bin/curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
        break
      fi
      sleep 2
    done

    if ! ${pkgs.curl}/bin/curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
      echo "Ollama not reachable, skipping model preload"
      exit 0
    fi

    INSTALLED=$(${pkgs.curl}/bin/curl -sf http://localhost:11434/api/tags | ${pkgs.jq}/bin/jq -r '.models[].name' 2>/dev/null || echo "")

    ${lib.concatMapStringsSep "\n" (model: ''
      if echo "$INSTALLED" | grep -qxF "${model}"; then
        echo "Already have: ${model}"
      else
        echo "Pulling: ${model}..."
        if ! RESPONSE=$(${pkgs.curl}/bin/curl -sf http://localhost:11434/api/pull -d '{"name": "${model}", "stream": false}'); then
          echo "Failed to pull ${model}: curl request failed"
          continue
        fi
        if echo "$RESPONSE" | ${pkgs.jq}/bin/jq -e '.error? // empty' > /dev/null; then
          echo "Failed to pull ${model}:"
          echo "$RESPONSE" | ${pkgs.jq}/bin/jq -r '.error'
          continue
        fi
        echo "Done: ${model}"
      fi
    '') models}

    echo "Model preload complete."
  '';
in
{
  options.local.ollamaTier = lib.mkOption {
    type = lib.types.enum [
      "low"
      "medium"
      "high"
    ];
    default = "medium";
    description = "Hardware tier for Ollama model selection";
  };

  config = {
    virtualisation.oci-containers.containers.ollama = {
      # Pin the engine: unpinned :latest let a stale cached image (0.17.7) come
      # back on restart and fail to load gemma4 ("unknown model architecture").
      # Bump this deliberately when a new model needs a newer engine.
      image = "ollama/ollama:0.30.10";
      ports = [ "127.0.0.1:11434:11434" ]; # loopback only — nothing remote calls it
      volumes = [ "ollama:/root/.ollama" ];
      environment = {
        OLLAMA_FLASH_ATTENTION = "1"; # prerequisite for KV quant; free VRAM+speed
        OLLAMA_KV_CACHE_TYPE = "q8_0"; # halves KV memory, negligible quality cost
        OLLAMA_CONTEXT_LENGTH = "16384"; # default is only 4096
        OLLAMA_KEEP_ALIVE = "30m"; # default 5m unloads mid-session
        OLLAMA_MAX_LOADED_MODELS = "1"; # one 7–9 GB model resident; avoid 12 GB VRAM clown-car mode
      };
      extraOptions = lib.optionals hasGpu [ "--device=nvidia.com/gpu=all" ];
    };

    # The container publish stays loopback-only; this bridges the API onto the
    # tailnet IP so tailnet agents (mini's Agent Zero local-model lane) can call
    # it. Resolve this host's address on each start; Restart=always rides out
    # the tailscale0-IP-not-yet-assigned boot race without a wildcard listener.
    systemd.services.ollama-tailnet-bridge = {
      description = "Bridge Ollama API onto the tailnet for remote agents";
      after = [
        "tailscaled.service"
        "podman-ollama.service"
      ];
      wants = [ "tailscaled.service" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        ExecStart = tailnetBridgeScript;
        Restart = "always";
        RestartSec = 5;
        DynamicUser = true;
      };
    };

    systemd.services.ollama-preload = {
      description = "Preload Ollama models for this machine's tier";
      after = [
        "podman-ollama.service"
        "network-online.target"
      ];
      wants = [
        "podman-ollama.service"
        "network-online.target"
      ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = preloadScript;
        RemainAfterExit = true;
      };
    };
  };
}
