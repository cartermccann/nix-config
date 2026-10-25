{
  config,
  pkgs,
  user,
  ...
}:
let
  c = config.lib.stylix.colors.withHashtag;
  pal = import ../lib/palette.nix;

  # Parse the config at build time with the same niri that runs the session.
  #
  # niri does not refuse to start on a bad config — it reports the error and
  # falls back to its compiled-in defaults, which is a bare grey desktop with
  # no outputs, keybinds or autostart, and looks exactly like "niri was never
  # set up". That failure is invisible from `nh os build`, invisible from
  # activation, and only shows up at the greeter. Two dead nodes sat in this
  # file from its first commit and broke the session that whole time.
  #
  # config.programs.niri.package, not pkgs.niri: those are different versions
  # here (25.08 vs nixpkgs' 25.11), and the one that matters is the one the
  # session Exec's.
  validatedNiriConfig =
    text:
    pkgs.runCommand "niri-config-noctalia.kdl"
      {
        inherit text;
        passAsFile = [ "text" ];
        nativeBuildInputs = [ config.programs.niri.package ];
      }
      ''
        cp "$textPath" config.kdl
        niri validate -c config.kdl
        cp config.kdl "$out"
      '';

  # Auseklis as a Noctalia colour scheme.
  #
  # Noctalia reads schemes from ~/.config/noctalia/colorschemes/<Name>/<Name>.json
  # (Services/Theming/ColorSchemeService.qml) and only ever reads them — it
  # writes the *selection* to settings.json and the resolved colours to
  # colors.json, never back to the scheme file. So unlike Caelestia's shell.json
  # this is a genuine read-only input and can be a store symlink. Its `find -L`
  # follows the link, so home-manager's symlink is discovered normally.
  #
  # Where Caelestia needed a Material generator to expand one seed into 120
  # roles, Noctalia wants a flat sixteen plus a terminal block — which is the
  # shape Base16 already has, so lib/palette.nix maps straight across.
  #
  # The ANSI ramp is deliberately identical to home/ghostty.nix (0=base01,
  # 7=base06, 8=base03, 15=base07) so a terminal keeps the same colours whether
  # its palette came from ghostty's own config or from a Noctalia template.
  # The "on" colours are stated per accent rather than derived. The tempting
  # rule — "the ground always contrasts with the accent, so use base00" — holds
  # for every night accent but breaks for two day ones, because base0C and
  # base08 are darkened for a light ground and then want dark text, not the
  # off-white ground. Contrast against each accent, WCAG AA wanting 4.5:1 for
  # small text and 3:1 for UI:
  #
  #             night (on=base00)   day (on=base00 / on=base05)
  #   secondary      6.52               4.83  /  3.75   → base00
  #   tertiary      10.82               3.44  /  5.26   → base05
  #   error          7.07               3.66  /  4.95   → base05
  #
  # mPrimary is the exception with no good answer: cobalt sits at tone 50, so
  # nothing clears AA against it — white is 4.44, near-black 4.41, and the
  # light-on-cobalt reading is the conventional one. Material dodges this by
  # re-toning the seed (which is why Caelestia's primary is #b6c4ff, not
  # #3b6bff); Noctalia takes the colour as given. Fine at the 3:1 UI bar it is
  # actually used at — fills, indicators, focus rings — but do not put small
  # body text on it.
  mkScheme =
    {
      v, # palette variant (pal.night / pal.day)
      onPrimary,
      onSecondary,
      onTertiary,
      onError,
      shadow,
    }:
    {
      mPrimary = v.base0D; # cobalt — the house accent
      mSecondary = v.accentBright;
      mTertiary = v.base0C; # cyan, same tertiary choice as the Caelestia scheme
      mError = v.base08;

      mOnPrimary = onPrimary;
      mOnSecondary = onSecondary;
      mOnTertiary = onTertiary;
      mOnError = onError;

      mSurface = v.base00;
      mOnSurface = v.base05;
      mSurfaceVariant = v.base01;
      mOnSurfaceVariant = v.base04;
      mOutline = v.base03;
      mShadow = shadow;
      mHover = v.base02;
      mOnHover = v.base05;

      terminal = {
        foreground = v.base05;
        background = v.base00;
        normal = {
          black = v.base01;
          red = v.base08;
          green = v.base0B;
          yellow = v.base0A;
          blue = v.base0D;
          magenta = v.base0E;
          cyan = v.base0C;
          white = v.base06;
        };
        bright = {
          black = v.base03;
          red = v.base08;
          green = v.base0B;
          yellow = v.base0A;
          blue = v.base0D;
          magenta = v.base0E;
          cyan = v.base0C;
          white = v.base07;
        };
        cursor = v.cursor;
        cursorText = v.base00;
        selectionFg = v.base05;
        selectionBg = v.base02;
      };
    };
in
{
  # Selecting this is a Noctalia setting, not a file we own: its Settings UI
  # writes colorSchemes.{predefinedScheme,useWallpaperColors,darkMode} into
  # settings.json, which is 20KB of live user state. Declaring those keys would
  # fight the UI on every rebuild, so the scheme is shipped here and picked
  # there — Settings → Color scheme → Auseklis, wallpaper colours off.
  xdg.configFile."noctalia/colorschemes/Auseklis/Auseklis.json".text = builtins.toJSON {
    dark = mkScheme {
      v = pal.night;
      onPrimary = pal.night.base07; # #f4f7fc
      # Night accents are all bright, so the near-black ground carries them.
      onSecondary = pal.night.base00;
      onTertiary = pal.night.base00;
      onError = pal.night.base00;
      shadow = "#000000";
    };
    light = mkScheme {
      v = pal.day;
      onPrimary = pal.day.base00; # #f7f7f8
      onSecondary = pal.day.base00; # #2563eb is dark enough to carry off-white
      onTertiary = pal.day.base05; # cyan and red are not — they take the text
      onError = pal.day.base05; # colour instead, see the table above
      shadow = pal.day.base03; # a black shadow reads as dirt on a light ground
    };
  };

  # Noctalia-specific Niri config — spawns noctalia-shell instead of waybar/fuzzel/mako/etc.
  xdg.configFile."niri/config-noctalia.kdl".source = validatedNiriConfig ''
    // Startup — env import + restart failed portal services, then launch noctalia-shell
    spawn-at-startup "bash" "-c" "systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP NIXOS_OZONE_WL GBM_BACKEND NVD_BACKEND LIBVA_DRIVER_NAME __GLX_VENDOR_LIBRARY_NAME && dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP NIXOS_OZONE_WL GBM_BACKEND NVD_BACKEND LIBVA_DRIVER_NAME __GLX_VENDOR_LIBRARY_NAME && systemctl --user restart xdg-desktop-portal-gtk xdg-desktop-portal 2>/dev/null; noctalia-shell &"
    spawn-at-startup "awww-daemon"
    spawn-at-startup "wl-paste" "--watch" "cliphist" "store"
    spawn-at-startup "wlsunset" "-t" "3500" "-T" "6500" "-l" "40.76" "-L" "-111.89"
    spawn-at-startup "bash" "-c" "sleep 1 && awww img /home/${user}/wallpaper.png --transition-type fade --transition-duration 1"
    spawn-at-startup "xwayland-satellite"
    // EasyEffects is gone entirely — its Arya correction now runs as a
    // PipeWire filter-chain sink declared in modules/audio.nix. It used to be
    // autostarted here and nowhere else, so whether the default sink was
    // virtual or real depended on which session you happened to boot: the EE
    // sink took the default, sat pinned at 1.00, and swallowed every volume
    // key while actual loudness stayed with the hardware device nothing was
    // touching. The replacement is ranked below both hardware sinks and so
    // can never take the default on its own.

    // Outputs — Dell U2414H landscape on the left, HP 27mq landscape to its right.
    // Dell is 1920 wide, so the HP starts at x=1920.
    output "DP-1" {
      mode "1920x1080@60"
      position x=0 y=0
    }
    output "HDMI-A-1" {
      mode "2560x1440@59.951"
      position x=1920 y=0
    }

    // Input
    input {
      keyboard {
        repeat-delay 200
        repeat-rate 35
        xkb {
          layout "us"
        }
      }
      touchpad {
        tap
        natural-scroll
        scroll-method "two-finger"
      }
    }

    // Layout
    layout {
      gaps 12
      center-focused-column "never"
      default-column-width { proportion 0.5; }
      focus-ring {
        off
      }
      border {
        width 2
        active-gradient from="${c.base0D}" to="${c.base0C}" angle=45
        inactive-gradient from="${c.base02}" to="${c.base03}" angle=45
      }
    }

    // Animations (snappy, critically damped)
    animations {
      window-open {
        duration-ms 150
        curve "ease-out-quad"
      }
      window-close {
        duration-ms 150
        curve "ease-out-quad"
      }
      workspace-switch {
        spring damping-ratio=1.0 stiffness=1000 epsilon=0.0001
      }
      window-resize {
        duration-ms 150
        curve "ease-out-quad"
      }
      horizontal-view-movement {
        spring damping-ratio=1.0 stiffness=800 epsilon=0.0001
      }
      window-movement {
        spring damping-ratio=1.0 stiffness=800 epsilon=0.0001
      }
    }

    // NO BLUR HERE. Mainline niri has none — not a `blur` node, not
    // `background-effect`, on 25.08 or 25.11 (checked with `niri validate`
    // against both, and there is no blur symbol in the binary at all). Those
    // nodes were in this file from its first commit and made the whole config
    // fail to parse, at which point niri silently falls back to its
    // compiled-in defaults: no outputs, no keybinds, no autostart, no shell.
    // That is the grey "nothing is set up" screen, and it is what this session
    // did every time it was booted. The validate step on the derivation below
    // now makes that a build failure instead of a surprise at the greeter.
    //
    // If blur is wanted in this session it has to come from a fork that
    // carries scenefx; the Hyprland sessions are where the glass idiom lives.

    // Window rules
    window-rule {
      geometry-corner-radius 20
      clip-to-geometry true
      shadow {
        on
        color "#00000064"
      }
    }

    // Overview backdrop — show wallpaper behind workspace overview
    overview {
      backdrop-color "#00000080"
    }

    // Screenshots
    screenshot-path "~/Pictures/Screenshots/%Y-%m-%d_%H-%M-%S.png"

    // Keybindings
    binds {

      // Programs
      Mod+Space { spawn "noctalia-shell" "ipc" "call" "launcher" "toggle"; }
      Mod+Return { spawn "ghostty"; }
      // canonical tmux session ("work"), same bind as the Hyprland session
      Mod+Alt+Return { spawn "ghostty" "-e" "fish" "-c" "tmux attach; or tmux new -s work"; }
      Mod+Shift+S { spawn "bash" "-c" "grim -g \"$(slurp)\" - | satty -f -"; }
      Mod+Shift+P { screenshot-screen; }
      Print { screenshot; }

      // Clipboard history
      Mod+V { spawn "bash" "-c" "cliphist list | fuzzel --dmenu --prompt='Clipboard: ' | cliphist decode | wl-copy"; }

      // Window management
      Mod+W { close-window; }
      Mod+Q { close-window; }

      // Vim-style focus
      Mod+H { focus-column-left; }
      Mod+J { focus-window-down; }
      Mod+K { focus-window-up; }
      Mod+L { focus-column-right; }

      // Arrow key focus
      Mod+Left { focus-column-left; }
      Mod+Down { focus-window-down; }
      Mod+Up { focus-window-up; }
      Mod+Right { focus-column-right; }

      // Vim-style move windows
      Mod+Shift+H { move-column-left; }
      Mod+Shift+J { move-window-down; }
      Mod+Shift+K { move-window-up; }
      Mod+Shift+L { move-column-right; }

      // Arrow key move windows
      Mod+Shift+Left { move-column-left; }
      Mod+Shift+Down { move-window-down; }
      Mod+Shift+Up { move-window-up; }
      Mod+Shift+Right { move-column-right; }

      // Resize
      Mod+Ctrl+H { set-column-width "-10%"; }
      Mod+Minus { set-column-width "-10%"; }
      Mod+Equal { set-column-width "+10%"; }

      // Layout
      Mod+F { maximize-column; }
      Mod+Shift+F { fullscreen-window; }
      Mod+C { center-column; }
      Mod+T { toggle-window-floating; }

      // Workspaces 1-10
      Mod+1 { focus-workspace 1; }
      Mod+2 { focus-workspace 2; }
      Mod+3 { focus-workspace 3; }
      Mod+4 { focus-workspace 4; }
      Mod+5 { focus-workspace 5; }
      Mod+6 { focus-workspace 6; }
      Mod+7 { focus-workspace 7; }
      Mod+8 { focus-workspace 8; }
      Mod+9 { focus-workspace 9; }
      Mod+0 { focus-workspace 10; }

      Mod+Shift+1 { move-column-to-workspace 1; }
      Mod+Shift+2 { move-column-to-workspace 2; }
      Mod+Shift+3 { move-column-to-workspace 3; }
      Mod+Shift+4 { move-column-to-workspace 4; }
      Mod+Shift+5 { move-column-to-workspace 5; }
      Mod+Shift+6 { move-column-to-workspace 6; }
      Mod+Shift+7 { move-column-to-workspace 7; }
      Mod+Shift+8 { move-column-to-workspace 8; }
      Mod+Shift+9 { move-column-to-workspace 9; }
      Mod+Shift+0 { move-column-to-workspace 10; }

      // Workspace cycling
      Mod+Tab { focus-workspace-down; }
      Mod+Shift+Tab { focus-workspace-up; }

      // Utilities
      Mod+Ctrl+N { spawn "bash" "-c" "pkill wlsunset || wlsunset -t 3500 -T 6500 -l 40.76 -L -111.89"; }

      // Dictation
      // Batch (offline Parakeet) on the plain chord; see the note in
      // home/hyprland/compositor.nix. Streaming keeps SHIFT.
      Mod+Alt+L { spawn "bash" "-c" "~/.local/bin/toggle-dictation-batch.sh"; }
      Mod+Shift+Alt+L { spawn "bash" "-c" "~/.local/bin/toggle-dictation.sh"; }

      // Control panels
      Mod+Ctrl+A { spawn "pavucontrol"; }
      Mod+Ctrl+B { spawn "bluetui"; }
      Mod+Ctrl+T { spawn "ghostty" "-e" "btop"; }
      Mod+Ctrl+D { spawn "ghostty" "-e" "rice-dashboard"; }

      // Wallpaper & Theme
      Mod+Shift+W { spawn "wallpaper-pick"; }

      // Session
      Mod+Shift+E { quit; }
      Mod+Shift+Slash { show-hotkey-overlay; }

      // Audio (media keys)
      XF86AudioRaiseVolume allow-when-locked=true { spawn "swayosd-client" "--output-volume" "raise"; }
      XF86AudioLowerVolume allow-when-locked=true { spawn "swayosd-client" "--output-volume" "lower"; }
      XF86AudioMute allow-when-locked=true { spawn "swayosd-client" "--output-volume" "mute-toggle"; }
      XF86AudioMicMute allow-when-locked=true { spawn "swayosd-client" "--input-volume" "mute-toggle"; }
      XF86AudioPlay { spawn "playerctl" "play-pause"; }
      XF86AudioNext { spawn "playerctl" "next"; }
      XF86AudioPrev { spawn "playerctl" "previous"; }

      // Brightness
      XF86MonBrightnessUp allow-when-locked=true { spawn "swayosd-client" "--brightness" "raise"; }
      XF86MonBrightnessDown allow-when-locked=true { spawn "swayosd-client" "--brightness" "lower"; }
    }
  '';
}
