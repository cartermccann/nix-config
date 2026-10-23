{
  config,
  lib,
  pkgs,
  user,
  zed-delta,
  ...
}:
let
  recorder = pkgs.callPackage ../pkgs/t3-delta-recorder { };
  homeDir = config.home.homeDirectory;
  projectScope = builtins.fromJSON (builtins.readFile ../config/t3-delta-projects.json);
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
  };
in
lib.mkIf (user == "cjm") {
  home.packages = [ recorder ];
  xdg.configFile."t3-delta-recorder/config.json".text = builtins.toJSON configuration;
  home.file.".agents/skills/t3-delta-history".source = ../skills/t3-delta-history;
  home.file.".codex/skills/t3-delta-history".source = ../skills/t3-delta-history;
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
      MemoryMax = "512M";
      CPUWeight = 20;
      WorkingDirectory = homeDir;
      Environment = [ "PATH=/etc/profiles/per-user/${user}/bin:/run/current-system/sw/bin" ];
    };
    Install.WantedBy = [ "graphical-session.target" ];
  };
}
