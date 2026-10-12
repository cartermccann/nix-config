{
  config,
  lib,
  pkgs,
  pkgs-unstable,
  ...
}:

# CLIProxyAPI — local hub that pools the Claude / Codex subscription OAuth
# accounts behind one OpenAI/Claude/Codex-compatible endpoint at
# http://127.0.0.1:8317, plus the /v0/management API that T3 Code reads for
# its "CLIProxyAPI hub" usage source (Settings → Providers → Usage providers).
#
# Secrets stay out of the store: ~/.config/credentials/cliproxyapi.env holds
#   MANAGEMENT_PASSWORD=...   management API key (read natively by cliproxyapi;
#                             also pasted into T3's usageLimitSources entry)
#   CLIPROXY_API_KEY=...      client key, substituted into api-keys below
# The store template is rendered to a writable ~/.config/cliproxyapi/config.yaml
# on every start, because cliproxyapi writes management-API edits back to its
# config; those edits do not survive a restart, this file is the truth.
#
# Accounts are OAuth tokens in ~/.cli-proxy-api, added interactively:
#   cliproxyapi -config ~/.config/cliproxyapi/config.yaml -claude-login
#   cliproxyapi -config ~/.config/cliproxyapi/config.yaml -codex-login
# (the watcher hot-loads new auth files, no restart needed).
let
  homeDir = config.home.homeDirectory;
  cliproxyapi = pkgs-unstable.cliproxyapi;
  configDir = "${homeDir}/.config/cliproxyapi";
  envFile = "${homeDir}/.config/credentials/cliproxyapi.env";

  configTemplate = pkgs.writeText "cliproxyapi-config.yaml" ''
    # Rendered from ~/nix-config/home/cliproxyapi.nix; edits here are lost on restart.
    host: "127.0.0.1"
    port: 8317
    auth-dir: "${homeDir}/.cli-proxy-api"

    remote-management:
      allow-remote: false
      # Empty on purpose: the key comes from MANAGEMENT_PASSWORD, so it never
      # lands in the store and cliproxyapi never rewrites this file to hash it.
      secret-key: ""
      disable-control-panel: false

    api-keys:
      - "@CLIPROXY_API_KEY@"

    debug: false
    logging-to-file: false
    usage-statistics-enabled: true
    request-retry: 3
    max-retry-interval: 30

    routing:
      strategy: "round-robin"
      session-affinity: true
  '';

  renderConfig = pkgs.writeShellApplication {
    name = "cliproxyapi-render-config";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.gnused
    ];
    text = ''
      : "''${CLIPROXY_API_KEY:?CLIPROXY_API_KEY missing from ${envFile}}"
      : "''${MANAGEMENT_PASSWORD:?MANAGEMENT_PASSWORD missing from ${envFile}}"
      umask 077
      mkdir -p ${configDir} ${homeDir}/.cli-proxy-api
      sed "s|@CLIPROXY_API_KEY@|$CLIPROXY_API_KEY|" ${configTemplate} > ${configDir}/config.yaml.tmp
      mv -f ${configDir}/config.yaml.tmp ${configDir}/config.yaml
    '';
  };
in
{
  systemd.user.services.cliproxyapi = {
    Unit = {
      Description = "CLIProxyAPI subscription hub (127.0.0.1:8317)";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
      ConditionPathExists = envFile;
    };

    Service = {
      Type = "simple";
      WorkingDirectory = homeDir;
      EnvironmentFile = [ envFile ];
      ExecStartPre = "${renderConfig}/bin/cliproxyapi-render-config";
      ExecStart = "${lib.getExe cliproxyapi} -config ${configDir}/config.yaml";
      Restart = "on-failure";
      RestartSec = 5;
    };

    Install = {
      WantedBy = [ "default.target" ];
    };
  };
}
