{
  config,
  lib,
  pkgs,
  user,
  zed-delta,
  delta-agent-history,
  osConfig,
  ...
}:
let
  recorder = import ../pkgs/delta-agent-history {
    upstream = delta-agent-history.packages.${pkgs.stdenv.hostPlatform.system}.default;
  };
  homeDir = config.home.homeDirectory;
  projectScope = builtins.fromJSON (builtins.readFile ../config/t3-delta-projects.json);
  enrollmentFile = "${homeDir}/.config/delta-agent-history/projects.json";
  configuration = {
    environment_id = "37822268-6f93-4b12-9766-5e2083778902";
    t3_runtime_file = "${homeDir}/.t3/userdata/server-runtime.json";
    t3_base_dir = "${homeDir}/.t3";
    t3_binary = "/etc/profiles/per-user/${user}/bin/t3";
    delta_binary = "${zed-delta.packages.${pkgs.stdenv.hostPlatform.system}.delta}/bin/delta";
    projects_root = "${homeDir}/projects";
    additional_project_paths = projectScope.additional_project_paths or [ ];
    allowed_projects = projectScope.projects;
    allowed_remotes = lib.unique (map (project: project.remote) projectScope.projects);
    poll_seconds = 10;
    project_enrollment_file = enrollmentFile;
  };
  legacyConfiguration = pkgs.writeText "t3-delta-recorder-config.json" (
    builtins.toJSON configuration
  );
  genericConfiguration = {
    schema_version = 2;
    sources = [
      {
        kind = "generic";
        instance = "local";
      }
      {
        kind = "codex";
        instance = osConfig.networking.hostName;
      }
      {
        kind = "claude-code";
        instance = osConfig.networking.hostName;
      }
    ];
    inherit (configuration)
      projects_root
      additional_project_paths
      allowed_projects
      allowed_remotes
      delta_binary
      poll_seconds
      project_enrollment_file
      ;
    legacy_t3 = {
      config = toString legacyConfiguration;
      state = "${homeDir}/.local/state/t3-delta-recorder";
    };
  };
  skillFiles = lib.listToAttrs (
    lib.concatMap
      (
        base:
        map
          (skill: {
            name = "${base}/skills/${skill}";
            value.source = "${delta-agent-history}/skills/${skill}";
          })
          [
            "delta-history"
            "delta-enroll"
          ]
      )
      [
        ".agents"
        ".codex"
        ".claude"
      ]
  );
in
lib.mkIf (user == "cjm") {
  xdg.configFile."t3-delta-recorder/config.json".source = legacyConfiguration;
  xdg.configFile."delta-agent-history/config.json".text = builtins.toJSON genericConfiguration;
  home.file = skillFiles // {
    ".agents/skills/t3-delta-history".source = ../skills/t3-delta-history;
    ".codex/skills/t3-delta-history".source = ../skills/t3-delta-history;
  };
  home.activation.deltaAgentEnrollment = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    run ${pkgs.python3}/bin/python3 ${../scripts/delta-agent-guidance.py} \
      --home ${lib.escapeShellArg homeDir} \
      --instruction ${delta-agent-history}/instructions/project-enrollment.md
  '';
  home.activation.deltaClaudeHooks = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    run ${pkgs.python3}/bin/python3 ${../scripts/delta-client-setup.py} \
      --home ${lib.escapeShellArg homeDir} \
      --executable /etc/profiles/per-user/${user}/bin/delta-history \
      --instance ${lib.escapeShellArg osConfig.networking.hostName}
  '';
  home.activation.deltaRepairRuntimeCleanup = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    deltaRepairRuntime="''${XDG_RUNTIME_DIR:-/run/user/$(${pkgs.coreutils}/bin/id -u)}"
    for deltaRepairUnit in t3-delta-recorder delta-agent-history; do
      run ${pkgs.coreutils}/bin/rm -f -- \
        "$deltaRepairRuntime/systemd/user/$deltaRepairUnit.service.d/90-contract8-repair.conf"
    done
  '';
  systemd.user.services.delta-agent-history = {
    Unit = {
      Description = "Drain accepted coding conversation events privately to Delta";
      After = [ "graphical-session.target" ];
      PartOf = [ "graphical-session.target" ];
      StartLimitIntervalSec = 300;
      StartLimitBurst = 5;
    };
    Service = {
      Type = "simple";
      ExecStart = "${recorder}/bin/delta-history --config ${homeDir}/.config/delta-agent-history/config.json --state-dir ${homeDir}/.local/state/delta-agent-history run";
      Restart = "on-failure";
      RestartSec = 15;
      UMask = "0077";
      NoNewPrivileges = true;
      # Delta's background app and connection-owned mounts share this cgroup.
      # 512 MiB repeatedly OOM-killed the recorder even before contract 8.
      MemoryMax = "2G";
      CPUWeight = 20;
      WorkingDirectory = homeDir;
      Environment = [ "PATH=/etc/profiles/per-user/${user}/bin:/run/current-system/sw/bin" ];
    };
    Install.WantedBy = [ "graphical-session.target" ];
  };
  systemd.user.services.t3-delta-recorder = {
    Unit = {
      Description = "Record new approved project conversations from T3 privately to Delta";
      After = [ "graphical-session.target" ];
      PartOf = [ "graphical-session.target" ];
      StartLimitIntervalSec = 300;
      StartLimitBurst = 5;
    };
    Service = {
      Type = "simple";
      ExecStart = "${recorder}/bin/t3-delta run";
      Restart = "on-failure";
      RestartSec = 15;
      UMask = "0077";
      NoNewPrivileges = true;
      MemoryMax = "2G";
      CPUWeight = 20;
      WorkingDirectory = homeDir;
      Environment = [ "PATH=/etc/profiles/per-user/${user}/bin:/run/current-system/sw/bin" ];
    };
    Install.WantedBy = [ "graphical-session.target" ];
  };
}
