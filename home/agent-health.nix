{ config, pkgs, ... }:
let
  health = pkgs.callPackage ../pkgs/agent-health { };
  recovery = pkgs.callPackage ../pkgs/agent-recovery {
    agentTools = pkgs.callPackage ../pkgs/agent-tools { };
    agentToolGuard = pkgs.callPackage ../pkgs/agent-tool-guard { };
  };
  homeDir = config.home.homeDirectory;
in
{
  home.packages = [
    health
    recovery
  ];

  systemd.user.services.agent-health-collect = {
    Unit.Description = "Collect private daily tool-health aggregates";
    Service = {
      Type = "oneshot";
      ExecStart = "${health}/bin/agent-health collect --home ${homeDir} --state-dir ${homeDir}/.local/state/agent-health";
      WorkingDirectory = homeDir;
      Environment = [ "PYTHONDONTWRITEBYTECODE=1" ];
      UMask = "0077";
      Nice = 10;
      CPUWeight = 10;
      IOWeight = 10;
      MemoryMax = "1G";
      TimeoutStartSec = "6min";
      NoNewPrivileges = true;
    };
  };

  systemd.user.timers.agent-health-collect = {
    Unit.Description = "Collect the last two closed UTC days of tool health daily";
    Timer = {
      # Host timezone is America/Denver (modules/common.nix). Local calendar
      # time also works in shells without Nix's named-zone lookup environment.
      OnCalendar = "*-*-* 08:15:00";
      Persistent = true;
      RandomizedDelaySec = "3min";
    };
    Install.WantedBy = [ "timers.target" ];
  };
}
