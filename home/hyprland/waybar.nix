{
  config,
  lib,
  ...
}:
# Waybar: the bar config and its stylesheet.
#
# Two bars from one module set. The HP (2560) carries everything; the Dell
# (1920, landscape) carries the workspaces, clock, modes, media, Tailscale and
# audio, leaving system stats to the HP. The live modules (modes, Tailscale,
# updates) are scripts from bar-scripts.nix; they share signal 8 so any toggle
# refreshes them all at once.
let
  pal = import ../../lib/palette.nix;
  auseklis = import ../../lib/auseklis.nix;

  # Mode indicators. Each mode is two custom modules from one script: the
  # `mode-*` one shows only while the mode is on (bar stays quiet otherwise),
  # the `peek-*` one only while it is off, inside a hover drawer. So an active
  # mode is always visible and an inactive one is one hover + click away.
  modes = [
    "dictation"
    "record"
    "night"
    "caffeine"
    "dnd"
    "heavy"
  ];
  modeModule = view: name: {
    name = "custom/${if view == "on" then "mode" else "peek"}-${name}";
    value = {
      exec = "auseklis-mode status ${name} ${view}";
      return-type = "json";
      interval = 5;
      signal = 8;
      on-click = "auseklis-mode toggle ${name}";
      escape = true;
    };
  };
  modeModules = builtins.listToAttrs (map (modeModule "on") modes ++ map (modeModule "peek") modes);
in
{
  xdg.configFile."waybar/config".text =
    let
      barBase = modeModules // {
        layer = "top";
        position = "top";
        height = 34;
        spacing = 6;
        margin-top = 8;
        margin-left = 12;
        margin-right = 12;

        "hyprland/workspaces" = {
          format = "{name}";
          on-click = "activate";
          all-outputs = true;
        };
        # Visible only while a submap is active (tmux prefix mode) — the
        # uppercase tracking matches the other HUD micro-labels.
        "hyprland/submap" = {
          format = "{}";
          tooltip = false;
        };

        "group/active" = {
          orientation = "horizontal";
          modules = map (m: "custom/mode-${m}") modes;
        };
        # The leader glyph is the only always-visible piece; hovering it slides
        # the inactive modes out to its left.
        "group/modes" = {
          orientation = "horizontal";
          drawer = {
            transition-duration = auseklis.motion.openMs;
            transition-left-to-right = false;
            children-class = "peek";
          };
          modules = [ "custom/modes" ] ++ map (m: "custom/peek-${m}") modes;
        };
        "custom/modes" = {
          format = "󰇘";
          tooltip-format = "modes · hover to show the inactive ones";
        };

        # Left-click drops the calendar (calendar.nix) and closes it again,
        # right-click flips to the ISO/week format, middle steps through the
        # timezones. No hover tooltip: the dropdown is the calendar now.
        clock = {
          format = "{:%H:%M  ·  %a %b %d}";
          format-alt = "{:%Y-%m-%d  ·  week %V}";
          format-alt-click = 3;
          timezones = [
            ""
            "Etc/UTC"
          ];
          tooltip = false;
          on-click = "auseklis-calendar";
          actions.on-click-middle = "tz_up";
        };
        # Every agent herdr knows about; amber when one is waiting on you.
        "custom/agents" = {
          exec = "auseklis-agents status";
          return-type = "json";
          interval = 3;
          signal = 8;
          on-click = "auseklis-agents menu";
          escape = true;
        };
        "custom/updates" = {
          exec = "auseklis-updates status";
          return-type = "json";
          interval = 300;
          signal = 8;
          on-click = "auseklis-updates menu";
          escape = true;
        };

        mpris = {
          format = "{status_icon} {dynamic}";
          dynamic-order = [
            "title"
            "artist"
          ];
          dynamic-len = 28;
          status-icons = {
            playing = "󰐊";
            paused = "󰏤";
            stopped = "󰓛";
          };
          tooltip-format = "{player}: {title} — {artist}";
          # click = play/pause, right-click = next are mpris module defaults
          on-click-middle = "playerctl next";
          on-scroll-up = "playerctl next";
          on-scroll-down = "playerctl previous";
        };
        "custom/gpu" = {
          exec = "nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits";
          format = "GPU {}°";
          interval = 10;
          "min-length" = 8;
          on-click = "ghostty --class=TUI.float -e btop";
        };
        cpu = {
          format = "CPU {usage}%";
          interval = 5;
          "min-length" = 8;
          on-click = "ghostty --class=TUI.float -e btop";
        };
        memory = {
          format = "MEM {percentage}%";
          interval = 5;
          "min-length" = 8;
          on-click = "ghostty --class=TUI.float -e btop";
        };
        "custom/tailscale" = {
          exec = "auseklis-tailscale status";
          return-type = "json";
          interval = 15;
          signal = 8;
          on-click = "auseklis-tailscale menu";
          on-click-right = "auseklis-tailscale toggle";
          escape = true;
        };
        network = {
          format-wifi = "WIFI {signalStrength}%";
          format-ethernet = "ETH";
          format-disconnected = "OFFLINE";
          tooltip-format-ethernet = "{ifname}: {ipaddr}/{cidr}\n↓ {bandwidthDownBytes}  ↑ {bandwidthUpBytes}";
          tooltip-format-wifi = "{essid} ({signalStrength}%): {ipaddr}";
          interval = 10;
          "min-length" = 9;
          on-click = "ghostty --class=TUI.float -e nmtui";
        };
        bluetooth = {
          format = "󰂯";
          format-disabled = "󰂲";
          format-connected = "󰂱 {num_connections}";
          tooltip-format = "{controller_alias}\t{controller_address}";
          tooltip-format-connected = "{device_enumerate}";
          tooltip-format-enumerate-connected = "{device_alias}";
          on-click = "ghostty --class=TUI.float -e bluetui";
        };
        # The microphone as its own module: click mutes, scroll sets input
        # gain, middle opens pavucontrol on its Input Devices tab.
        "pulseaudio#mic" = {
          format = "{format_source}";
          format-source = "MIC {volume}%";
          format-source-muted = "MIC OFF";
          on-click = "wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle";
          on-click-middle = "pavucontrol -t 4";
          on-scroll-up = "wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SOURCE@ 3%+";
          on-scroll-down = "wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 3%-";
          tooltip = false;
        };
        # Left-click is the output picker rather than pavucontrol: switching
        # between the desk speakers, the Schiit and Bluetooth headphones is
        # the frequent action, and the full mixer is rarely what's wanted.
        # Middle-click still opens pavucontrol for per-app routing.
        pulseaudio = {
          format = "VOL {volume}%";
          format-muted = "MUTED";
          scroll-step = 3;
          on-click = "hypr-audio-sink";
          on-click-middle = "pavucontrol";
          on-click-right = "swayosd-client --output-volume mute-toggle";
          tooltip-format = "{desc} — {volume}%";
        };
        "custom/notifications" = {
          format = "{icon}";
          format-icons = {
            notification = "󱅫";
            none = "󰂚";
            dnd-notification = "󰂛";
            dnd-none = "󰂛";
            inhibited-notification = "󱅫";
            inhibited-none = "󰂚";
          };
          return-type = "json";
          exec = "swaync-client -swb";
          on-click = "swaync-client -t -sw";
          on-click-right = "swaync-client -d -sw";
          escape = true;
          tooltip = false;
        };
        tray = {
          spacing = 10;
          icon-size = 16;
        };
        "custom/power" = {
          format = "⏻";
          on-click = "hypr-power-menu";
          tooltip = false;
        };
      };
      center = [
        "group/modes"
        "group/active"
        "clock"
        "custom/agents"
      ];
    in
    builtins.toJSON [
      (
        barBase
        // {
          output = [ "HDMI-A-1" ];
          modules-left = [
            "hyprland/workspaces"
            "hyprland/submap"
          ];
          modules-center = center ++ [ "custom/updates" ];
          modules-right = [
            "mpris"
            "custom/gpu"
            "cpu"
            "memory"
            "custom/tailscale"
            "network"
            "bluetooth"
            "pulseaudio#mic"
            "pulseaudio"
            "custom/notifications"
            "tray"
            "custom/power"
          ];
        }
      )
      (
        barBase
        // {
          output = [ "DP-1" ];
          modules-left = [
            "hyprland/workspaces"
            "hyprland/submap"
          ];
          modules-center = center;
          modules-right = [
            "mpris"
            "custom/tailscale"
            "pulseaudio#mic"
            "pulseaudio"
            "custom/notifications"
          ];
        }
      )
    ];

  # The stylesheet lives in config/hyprland/waybar.css as real CSS. It carries
  # no Nix interpolation at all: the palette and the Auseklis glass tokens are
  # emitted alongside it as _auseklis.css and pulled in with @import, so the two
  # travel together and the stylesheet stays editable with normal CSS tooling.
  xdg.configFile."waybar/style.css".source = ../../config/hyprland/waybar.css;
  xdg.configFile."waybar/_auseklis.css".text = import ./palette-css.nix lib pal;

  # fuzzel: session launcher (separate config path; launched via --config)
}
