{
  config,
  lib,
  pkgs,
  codex-desktop-linux,
  ...
}:

let
  homeDir = config.home.homeDirectory;
  legacyOverlayDir = "${homeDir}/projects/input-linux/codex-desktop-overlay";
  codexDesktop = import ../pkgs/codex-desktop {
    inherit pkgs;
    upstream = codex-desktop-linux.packages.${pkgs.stdenv.hostPlatform.system}.codex-desktop;
  };

  codexHooksRepair = pkgs.writeShellApplication {
    name = "codex-hooks-repair";
    runtimeInputs = [ pkgs.python3 ];
    text = ''
      exec python3 ${../scripts/codex-hook-compat.py} "$@"
    '';
  };

  codexDesktopGuard = pkgs.writeShellApplication {
    name = "codex-desktop-guard";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.libnotify
      pkgs.procps
      pkgs.systemd
    ];
    text = ''
      legacy_overlay=${lib.escapeShellArg legacyOverlayDir}

      # The running overlay can own terminals and active work. Never stop its
      # scope during an upgrade, or let two releases write the same profile.
      legacy_primary_is_running() {
        local pid executable command_line
        while IFS= read -r pid; do
          executable="$(readlink "/proc/$pid/exe" 2>/dev/null || true)"
          case "$executable" in
            "$legacy_overlay/electron"|"$legacy_overlay/electron (deleted)") ;;
            *) continue ;;
          esac
          [ -r "/proc/$pid/cmdline" ] || continue
          command_line="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
          case "$command_line" in
            *--type=*) continue ;;
          esac
          return 0
        done < <(pgrep -f "$legacy_overlay/electron" || true)
        return 1
      }

      if legacy_primary_is_running; then
        message="Quit the existing ChatGPT / Codex app before opening the updated version. Its running session has been left intact."
        echo "$message" >&2
        notify-send --app-name="ChatGPT / Codex" --urgency=normal \
          "Codex update is ready" "$message" || true
        exit 1
      fi

      # App terminals inherit this scope. These limits cover their builds as
      # well as Electron: a lower MemoryHigh previously caused reclaim and an
      # oomd kill despite ample free RAM; TasksMax=2500 exhausted build threads.
      memory_high="''${CODEX_LINUX_MEMORY_HIGH:-28G}"
      memory_max="''${CODEX_LINUX_MEMORY_MAX:-32G}"
      tasks_max="''${CODEX_LINUX_TASKS_MAX:-8000}"
      if ! [[ "$memory_high" =~ ^([0-9]+([KMGTPE])?|infinity)$ ]] ||
         ! [[ "$memory_max" =~ ^([0-9]+([KMGTPE])?|infinity)$ ]] ||
         ! [[ "$tasks_max" =~ ^[0-9]+$ ]]; then
        echo "Invalid Codex memory or process limits; refusing to launch without the configured limits." >&2
        exit 1
      fi
      export CODEX_LINUX_MEMORY_HIGH="$memory_high"
      export CODEX_LINUX_MEMORY_MAX="$memory_max"
      export CODEX_LINUX_TASKS_MAX="$tasks_max"

      # Both releases naturally select ~/.config/Codex. Do not synthesize an
      # explicit profile override: upstream gives that override additional
      # behavior beyond choosing a directory. Existing user overrides survive.
      export CODEX_HOME="''${CODEX_HOME:-${homeDir}/.codex}"
      # Some Claude plugins pass argv separately; Codex executes only command.
      # Reapply after plugin refreshes without granting trust to changed hooks.
      if ! ${codexHooksRepair}/bin/codex-hooks-repair --codex-home "$CODEX_HOME"; then
        echo "WARN: Stripe hook repair failed; continuing Codex launch." >&2
      fi
      export CODEX_LINUX_DISABLE_USAGE_REPORTING="''${CODEX_LINUX_DISABLE_USAGE_REPORTING:-1}"
      # Reuse the input daemon already configured for system dictation.
      # Its NixOS socket is outside the backend's default search locations.
      export YDOTOOL_SOCKET="''${YDOTOOL_SOCKET:-/run/ydotoold/socket}"
      unset ELECTRON_RENDERER_URL

      # A UUID prevents PID reuse from making an old scope look owned by this
      # invocation. A forwarding launch gets its own scope, never the primary's.
      read -r scope_id < /proc/sys/kernel/random/uuid
      scope_unit="app-codex-desktop-$$-$scope_id.scope"

      cleanup_owned_scope() {
        local control_group process_file pid executable command_line
        control_group="$(systemctl --user show "$scope_unit" -p ControlGroup --value 2>/dev/null || true)"
        case "$control_group" in
          */app.slice/"$scope_unit") ;;
          *) return 0 ;;
        esac
        process_file="/sys/fs/cgroup$control_group/cgroup.procs"
        [ -r "$process_file" ] || return 0
        while IFS= read -r pid; do
          executable="$(readlink "/proc/$pid/exe" 2>/dev/null || true)"
          case "$executable" in
            "${codexDesktop}/opt/codex-desktop/ChatGPT"|"${codexDesktop}/opt/codex-desktop/ChatGPT (deleted)") ;;
            *) continue ;;
          esac
          # If a live primary outlasted the launcher, leave its entire scope
          # alone. An unreadable command line is also grounds to leave it.
          [ -r "/proc/$pid/cmdline" ] || return 0
          command_line="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
          case "$command_line" in
            *--type=*) ;;
            *) return 0 ;;
          esac
        done < "$process_file"
        # Match the previous guard: clean up leftover MCPs and terminals only
        # after this invocation's primary has exited, never on a guard signal.
        systemctl --user stop --no-block "$scope_unit" ||
          echo "WARN: could not clean up exited Codex scope $scope_unit" >&2
      }

      systemd-run --user --scope --collect \
        --unit="$scope_unit" \
        --description="ChatGPT / Codex" \
        --property="MemoryHigh=$memory_high" \
        --property="MemoryMax=$memory_max" \
        --property="TasksMax=$tasks_max" \
        -- ${codexDesktop}/bin/codex-desktop --class=codex-desktop "$@" &
      launcher_pid=$!
      if wait "$launcher_pid"; then
        status=0
      else
        status=$?
      fi
      if ! kill -0 "$launcher_pid" 2>/dev/null; then
        cleanup_owned_scope
      fi
      exit "$status"
    '';
  };
in
lib.mkIf (config.home.username == "cjm") {
  home.packages = [
    codexDesktopGuard
    codexHooksRepair
  ];

  # Plugins can refresh while the desktop stays open for days. A path unit
  # cannot watch all future version directories recursively, so check only
  # Stripe's small manifest set periodically as well as at desktop launch.
  systemd.user.services.codex-hooks-repair = {
    Unit.Description = "Repair Stripe hook command compatibility for Codex";
    Service = {
      Type = "oneshot";
      ExecStart = "${codexHooksRepair}/bin/codex-hooks-repair --codex-home ${homeDir}/.codex";
      TimeoutStartSec = "15s";
    };
  };

  systemd.user.timers.codex-hooks-repair = {
    Unit.Description = "Repair Codex hooks after background plugin refreshes";
    Timer = {
      OnStartupSec = "30s";
      OnUnitInactiveSec = "1min";
      AccuracySec = "5s";
    };
    Install.WantedBy = [ "timers.target" ];
  };

  home.file.".local/share/applications/codex-desktop.desktop" = {
    force = true;
    text = ''
      [Desktop Entry]
      Type=Application
      Version=1.0
      Name=ChatGPT / Codex
      GenericName=AI coding assistant
      Comment=Chat with ChatGPT and work with Codex
      Exec=${codexDesktopGuard}/bin/codex-desktop-guard %U
      Icon=${codexDesktop}/share/icons/hicolor/256x256/apps/codex-desktop.png
      Terminal=false
      Categories=Development;Utility;
      Keywords=ChatGPT;Codex;OpenAI;AI;Code;
      StartupNotify=true
      StartupWMClass=codex-desktop
      MimeType=x-scheme-handler/codex;x-scheme-handler/codex-browser-sidebar;
    '';
  };
}
