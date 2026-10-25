{
  config,
  lib,
  pkgs,
  hyprland,
  ...
}:
# The compositor config itself (Hyprland 0.55+ Lua) plus hyprsunset's profile.
# Everything the session *shows* lives in the sibling modules: waybar.nix,
# swaync.nix, menus.nix, lock.nix.
let
  pal = import ../../lib/palette.nix;
  auseklis = import ../../lib/auseklis.nix;
  cfgHome = config.xdg.configHome;
  hyprctl = "${hyprland.packages.${pkgs.stdenv.hostPlatform.system}.hyprland}/bin/hyprctl";

  # this module owns the default waybar config (~/.config/waybar/{config,style.css}),
  # so launch it bare — no -c/-s needed.
  waybarCmd = "waybar";

  # The compositor config is shared between the two session tiles; only the
  # shell layer differs. `shellKind` selects it:
  #
  #   "waybar"    waybar + swaync + fuzzel + swayosd + hyprlock + hypridle
  #   "caelestia" Caelestia, which provides all of the above itself
  #
  # Everything else — monitors, animations, window rules, window management,
  # workspaces, screenshots — is identical, which is the whole point of
  # parameterising rather than forking the file. The seams below are the only
  # places the two diverge.
  #
  # Caelestia's own keybinds arrive over Hyprland's D-Bus global shortcuts
  # (appid "caelestia"), hence hl.dsp.global("caelestia:<name>"). The names come
  # from the CustomShortcut declarations in its QML source.
  mkHyprlandLua =
    shellKind:
    let
      caelestia = shellKind == "caelestia";
      # Pick between two lists of Lua lines at one seam. `sep` carries the
      # indentation of the seam's surroundings: at top level that is just a
      # newline, but inside the autostart function body the continuation lines
      # need the block's two spaces to line up with the first.
      shellSep =
        sep: waybarLines: caelestiaLines:
        lib.concatStringsSep sep (if caelestia then caelestiaLines else waybarLines);
      shell = shellSep "\n";
    in
    ''
      -- Hyprland session (Hyprland 0.55+ Lua config)

      local mod = "SUPER"

      -- Monitors
      -- Dell U2414H landscape on the left, HP 27mq landscape to its right.
      -- Dell is 1920 wide, so the HP starts at x=1920.
      hl.monitor({ output = "DP-1", mode = "1920x1080@60", position = "0x0", scale = 1 })
      hl.monitor({ output = "HDMI-A-1", mode = "2560x1440@60", position = "1920x0", scale = 1 })
      hl.monitor({ output = "", mode = "preferred", position = "auto", scale = 1 })

      -- Environment
      -- NVIDIA GBM_BACKEND/__GLX_VENDOR_LIBRARY_NAME etc. are already global
      -- (modules/nvidia.nix → environment.sessionVariables); only cursor sizes here.
      hl.env("XCURSOR_SIZE", "24")
      hl.env("HYPRCURSOR_SIZE", "24")

      -- Core options
      hl.config({
        cursor = {
          no_hardware_cursors = 1, -- int in 0.55 (1 = never use hw cursors)
        },
        general = {
          gaps_in = 6,
          gaps_out = 12,
          -- Japandi hairline: 1px, near-invisible. The pane is defined by its
          -- glass + shadow, not its outline; focus reads from the azure glow below.
          border_size = 1,
          ["col.active_border"] = "rgba(${pal.raw.base07}50)",
          ["col.inactive_border"] = "rgba(${pal.raw.base07}0d)",
          layout = "dwindle",
          allow_tearing = false,
          resize_on_border = true,
        },
        dwindle = {
          -- `pseudotile` is no longer a dwindle config option in 0.55 — it's a
          -- per-window state via the `pseudo` dispatcher (bound to SUPER+P below).
          preserve_split = true,
        },
        binds = {
          scroll_event_delay = 0, -- no artificial latency on scroll binds
        },
        -- Glassy blur + rounding + soft shadows + glow
        decoration = {
          rounding = ${toString auseklis.radius.surface},
          rounding_power = ${toString auseklis.radius.squirclePower}, -- subtle squircle — smoother corner flow than pure circle
          -- Opacity strategy: globals stay 1.0; translucency is granted per-app via
          -- window rules below (the terminal is the only glass pane by default).
          active_opacity = 1.0,
          inactive_opacity = 0.95,
          fullscreen_opacity = 1.0,
          dim_inactive = true,
          dim_strength = 0.08, -- carries more focus signal now that borders are hairlines
          blur = {
            enabled = true,
            -- Numbers carried over from the gentoo dotfiles' mango/scenefx
            -- block (config/mango/config.conf), where the glass idiom was
            -- worked out against a real wallpaper and the findings written
            -- down. Two of them are load-bearing:
            --
            --   brightness BELOW 1.0 darkens the backdrop toward invisibility.
            --   At 0.86 there it pulled the blurred wallpaper down ~40%, which
            --   was most of why the glass could not be seen. Above 1.0 lifts.
            --
            --   radius is what makes a surface read as glass rather than grey
            --   paint. At 26 the desktop looked solid; 14 kept enough shape
            --   to see through.
            --
            -- Now driven by lib/auseklis.nix (blur.*): one extra pass at a
            -- slightly smaller size is the Auseklis frost: heavier diffusion
            -- with the same shape, and the tint floor keeps text legible.
            size = ${toString auseklis.blur.size},
            passes = ${toString auseklis.blur.passes},
            new_optimizations = true,
            xray = true, -- biggest NVIDIA perf lever: blur samples the wallpaper, not stacked windows
            ignore_opacity = true,
            noise = ${toString auseklis.blur.noise}, -- coarser grain = the diffusion through the frost
            contrast = ${toString auseklis.blur.contrast},
            brightness = ${toString auseklis.blur.brightness},
            -- The one value without a 1:1 mapping: scenefx takes a saturation
            -- multiplier (1.7 there), Hyprland takes a 0–1 vibrancy. 0.5 was
            -- the eyeball equivalent; Auseklis runs it at 0.7 for the heavier
            -- frost. Tune it first if the backdrop reads too grey or too lurid.
            vibrancy = ${toString auseklis.blur.vibrancy},
            vibrancy_darkness = 0.0,
            popups = true, -- blurred right-click menus
            popups_ignorealpha = 0.2,
            special = false, -- expensive; scratchpad gets opacity instead
          },
          shadow = {
            enabled = true,
            range = 20, -- big radius, fast falloff = "lifted pane"
            render_power = 4,
            color = "rgba(00000033)", -- heavy shadow kills glass; keep it soft
            color_inactive = "rgba(0000001a)",
            offset = { 0, 4 }, -- light from above
          },
          -- Inner glow (new in 0.55): light-through-glass edge on the active pane.
          -- With the border reduced to a hairline this IS the focus indicator,
          -- so it breathes a little wider/brighter than before.
          glow = {
            enabled = true,
            range = 12,
            render_power = 3,
            color = "rgba(${pal.raw.base0D}30)", -- soft azure halo
            color_inactive = "rgba(00000000)",
          },
        },
        animations = {
          enabled = true,
        },
        input = {
          kb_layout = "us",
          -- kb_options = "caps:escape", -- opt-in: uncomment to map CapsLock→Esc (vim hands)
          repeat_delay = 250,
          repeat_rate = 40, -- snappy held-key repeat for vim motions
          accel_profile = "flat", -- desktop mouse: no accel
          follow_mouse = 1,
          sensitivity = 0,
          touchpad = {
            natural_scroll = true,
            tap_to_click = true, -- 0.55 lua name (was `tap-to-click` in hyprlang)
          },
        },
        gestures = {
          workspace_swipe_distance = 300,
        },
        misc = {
          disable_hyprland_logo = true,
          disable_splash_rendering = true,
          focus_on_activate = true,
          -- `vfr` moved to debug.vfr in newer versions and defaults to true; dropped.
        },
        xwayland = {
          force_zero_scaling = true, -- no blurry XWayland on scaled outputs
        },
        ecosystem = {
          no_update_news = true,
        },
      })

      -- Animation curves + tree
      -- Philosophy: decel-heavy In, fast-linear Out, In > Out asymmetry, spring for
      -- windows, nothing over 4.5ds. (1ds = 100ms)
      hl.curve("emphasizedDecel", { type = "bezier", points = { { 0.05, 0.7 },  { 0.1, 1 }    } }) -- MD3 decel (scratchpad drop-in)
      hl.curve("menuDecel",       { type = "bezier", points = { { 0.1, 1 },     { 0, 1 }      } }) -- instant-feel layer fades
      hl.curve("menuAccel",       { type = "bezier", points = { { 0.52, 0.03 }, { 0.72, 0.08 } } }) -- layer exit
      hl.curve("almostLinear",    { type = "bezier", points = { { 0.5, 0.5 },   { 0.75, 1 }   } }) -- upstream fade default
      -- House easing tokens (reveal / snap / stage).
      hl.curve("easeReveal", { type = "bezier", points = { { 0.19, 1 },    { 0.22, 1 }  } }) -- --ease-reveal: aggressive expo-like decel
      hl.curve("easeSnap",   { type = "bezier", points = { { 0.65, 0.05 }, { 0, 1 }     } }) -- --ease-snap: late accel, hard settle
      hl.curve("easeStage",  { type = "bezier", points = { { 0.77, 0 },    { 0.175, 1 } } }) -- --ease-stage: deliberate in-out
      -- Upstream default spring, near-critically damped.
      hl.curve("easy", { type = "spring", mass = 1, stiffness = 71.2633, dampening = 15.8273644 })

      hl.animation({ leaf = "windows",     enabled = true, speed = 3.5, spring = "easy" })
      hl.animation({ leaf = "windowsIn",   enabled = true, speed = 3,   bezier = "easeReveal", style = "popin 85%" })
      hl.animation({ leaf = "windowsOut",  enabled = true, speed = 1.8, bezier = "almostLinear", style = "popin 90%" }) -- close gets out of the way
      hl.animation({ leaf = "windowsMove", enabled = true, speed = 3,   bezier = "easeSnap",   style = "slide" })
      hl.animation({ leaf = "border",      enabled = true, speed = 3,   bezier = "easeReveal" })
      hl.animation({ leaf = "fade",        enabled = true, speed = 2,   bezier = "almostLinear" })
      hl.animation({ leaf = "fadeIn",      enabled = true, speed = 1.7, bezier = "almostLinear" })
      hl.animation({ leaf = "fadeOut",     enabled = true, speed = 1.5, bezier = "almostLinear" })
      hl.animation({ leaf = "fadeDim",     enabled = true, speed = 2,   bezier = "almostLinear" })
      -- Layers: bar / fuzzel / swaync / swayosd get real entrances instead of defaults.
      hl.animation({ leaf = "layersIn",      enabled = true, speed = 2.5, bezier = "easeReveal", style = "popin 93%" })
      hl.animation({ leaf = "layersOut",     enabled = true, speed = 1.6, bezier = "menuAccel",       style = "popin 94%" })
      hl.animation({ leaf = "fadeLayersIn",  enabled = true, speed = 1.6, bezier = "menuDecel" })
      hl.animation({ leaf = "fadeLayersOut", enabled = true, speed = 1.8, bezier = "menuAccel" })
      -- Workspaces: the site's "stage transition" in-out — deliberate, cinematic slide.
      hl.animation({ leaf = "workspaces", enabled = true, speed = 4, bezier = "easeStage", style = "slide" })
      -- Scratchpad drop-in/out (see SUPER+grave below).
      hl.animation({ leaf = "specialWorkspaceIn",  enabled = true, speed = 2.8, bezier = "emphasizedDecel", style = "slidefadevert 15%" })
      hl.animation({ leaf = "specialWorkspaceOut", enabled = true, speed = 1.5, bezier = "menuAccel",       style = "slidefadevert 15%" })
      -- NOTE: never add `borderangle` with style="loop" — it forces full-refresh-rate
      -- rendering permanently (~2x idle power on NVIDIA).

      -- Touchpad gesture: 3-finger horizontal swipe switches workspace
      hl.gesture({ fingers = 3, direction = "horizontal", action = "workspace" })

      -- Layer rules: frost the bar / launcher / notifications / OSD
      -- ignore_alpha must sit below each surface's tint or Hyprland skips the blur
      -- entirely (a 0.42 frost card under a 0.5 threshold renders as flat
      -- tint), and above zero so transparent gaps don't haze. Glass surfaces
      -- take auseklis.glass.blurThreshold, which sits under the tint floor.
      ${shell
        [
          # xray: the bar's blur samples only the wallpaper. Without it Hyprland
          # re-blurs whatever window sits under the bar and the whole bar blinks
          # when that window's colours change, until a hover forces a redraw
          # (hyprwm/Hyprland discussion #10363; seen here 2026-10-06).
          ''hl.layer_rule({ match = { namespace = "waybar" },                     blur = true, ignore_alpha = 0.2, xray = true })''
          ''hl.layer_rule({ match = { namespace = "launcher" },                   blur = true, ignore_alpha = ${toString auseklis.glass.blurThreshold}, dim_around = true }) -- fuzzel: spotlight dim''
          ''hl.layer_rule({ match = { namespace = "swaync-control-center" },      blur = true, ignore_alpha = ${toString auseklis.glass.blurThreshold} })''
          ''hl.layer_rule({ match = { namespace = "swaync-notification-window" }, blur = true, ignore_alpha = ${toString auseklis.glass.blurThreshold} })''
          ''hl.layer_rule({ match = { namespace = "swayosd" },                    blur = true, ignore_alpha = ${toString auseklis.glass.blurThreshold} })''
          ''hl.layer_rule({ match = { namespace = "^auseklis-calendar$" },         blur = true, ignore_alpha = ${toString auseklis.glass.blurThreshold} })''
        ]
        [
          # Every Caelestia surface is namespaced caelestia-<name>
          # (components/containers/StyledWindow.qml), so one regex covers the bar,
          # launcher, dashboard, sidebar, OSD and notifications.
          ''hl.layer_rule({ match = { namespace = "^caelestia-" }, blur = true, ignore_alpha = 0.2 })''
        ]
      }

      -- Window rules
      -- The terminal is the glass centerpiece. Ghostty owns its background alpha
      -- (home/ghostty.nix) so glyphs stay fully opaque over the frost; this rule
      -- only adds the inactive dim step on top.
      hl.window_rule({ match = { class = "^(com.mitchellh.ghostty)$" }, opacity = "1.0 0.94" })
      -- Content surfaces: opaque + unblurred (readability + perf; no video shimmer).
      hl.window_rule({ match = { class = "^(firefox|chromium|zen|Brave-browser)$" }, opacity = "1.0 override 1.0 override" })
      hl.window_rule({ match = { class = "^(mpv|vlc|imv)$" }, opaque = true, no_blur = true })
      -- Video shouldn't lock the screen mid-movie (hypridle fires at 300s otherwise).
      hl.window_rule({ match = { class = "^(mpv|vlc)$" }, idle_inhibit = "always" })
      hl.window_rule({ match = { fullscreen = true }, idle_inhibit = "fullscreen" })
      -- Floats get deterministic geometry. Keep BOTH pavucontrol class patterns until
      -- `hyprctl clients` confirms which one the packaged build reports.
      hl.window_rule({ match = { class = "^(pavucontrol|org.pulseaudio.pavucontrol)$" }, float = true, size = "900 600", center = true })
      hl.window_rule({ match = { class = "^(TUI.float)$" }, float = true, size = "1100 700", center = true })
      hl.window_rule({ match = { title = "^(Picture-in-Picture)$" }, float = true })
      hl.window_rule({ match = { title = "^(Open File|Save File|File Upload)" }, float = true, center = true })
      -- XWayland legacy junk: no shadow artifacts on undecorated floats.
      hl.window_rule({ match = { xwayland = true, float = true }, no_shadow = true })
      -- Scratchpad: slightly translucent so it reads as an overlay.
      hl.window_rule({ match = { workspace = "special:scratch" }, opacity = "0.92 0.92" })
      hl.window_rule({ match = { class = ".*" }, suppress_event = "maximize" })

      -- Keybinds
      -- Programs
      hl.bind(mod .. " + RETURN", hl.dsp.exec_cmd("ghostty"), { description = "Terminal" })
      -- canonical tmux session ("work"), same bind as the niri session
      hl.bind(mod .. " + ALT + RETURN", hl.dsp.exec_cmd("ghostty -e fish -c 'tmux attach; or tmux new -s work'"), { description = "Terminal in tmux session work" })
      ${shell
        [
          ''hl.bind(mod .. " + SPACE",  hl.dsp.exec_cmd("fuzzel --config ${cfgHome}/fuzzel/hypr.ini"), { description = "App launcher" })''
          ''hl.bind(mod .. " + V",      hl.dsp.exec_cmd("cliphist list | fuzzel --dmenu --config ${cfgHome}/fuzzel/hypr.ini | cliphist decode | wl-copy"), { description = "Clipboard history" })''
          # The Auseklis menu (home/hyprland/menu.nix) and direct jumps into it.
          ''hl.bind(mod .. " + ALT + SPACE", hl.dsp.exec_cmd("auseklis-menu"), { description = "Auseklis menu" })''
          ''hl.bind(mod .. " + CTRL + C",    hl.dsp.exec_cmd("auseklis-menu capture"), { description = "Menu › Capture" })''
          ''hl.bind(mod .. " + CTRL + O",    hl.dsp.exec_cmd("auseklis-menu toggle"), { description = "Menu › Toggle" })''
          ''hl.bind(mod .. " + ESCAPE",      hl.dsp.exec_cmd("auseklis-menu system"), { description = "Menu › System" })''
          ''hl.bind(mod .. " + slash",       hl.dsp.exec_cmd("auseklis-keys"), { description = "Keybinding cheatsheet" })''
          ''hl.bind(mod .. " + O",           hl.dsp.exec_cmd("auseklis-project"), { description = "Open a project" })''
          ''hl.bind(mod .. " + SHIFT + A",   hl.dsp.exec_cmd("auseklis-agents menu"), { description = "Agent switcher" })''
        ]
        [
          ''hl.bind(mod .. " + SPACE",  hl.dsp.global("caelestia:launcher"), { description = "App launcher" })''
          # Caelestia's CLI wraps the same cliphist store the waybar session
          # writes to, so history carries across both sessions.
          ''hl.bind(mod .. " + V",      hl.dsp.exec_cmd("caelestia clipboard"), { description = "Clipboard history" })''
        ]
      }

      -- Screenshots (grimblast adds --freeze: the screen stops while you aim)
      hl.bind(mod .. " + SHIFT + S", hl.dsp.exec_cmd([[grimblast --freeze save area - | satty -f - --output-filename ~/Pictures/Screenshots/satty-$(date +%Y%m%d-%H%M%S).png]]), { description = "Screenshot area → annotate" })
      hl.bind(mod .. " + SHIFT + P", hl.dsp.exec_cmd("grimblast --freeze copy output"), { description = "Screenshot monitor → clipboard" })
      hl.bind("Print",               hl.dsp.exec_cmd([[grimblast save output ~/Pictures/Screenshots/$(date +%Y%m%d-%H%M%S).png]]), { description = "Screenshot monitor → ~/Pictures/Screenshots" })

      -- Color picker → clipboard
      hl.bind(mod .. " + SHIFT + C", hl.dsp.exec_cmd("hyprpicker -a"), { description = "Color picker → clipboard" })

      -- Window management
      hl.bind(mod .. " + Q", hl.dsp.window.close(), { description = "Close window" })
      hl.bind(mod .. " + W", hl.dsp.window.close(), { description = "Close window" })
      hl.bind(mod .. " + F",         hl.dsp.window.fullscreen({ mode = "maximized" }), { description = "Maximize (keeps gaps and bar)" })
      hl.bind(mod .. " + SHIFT + F", hl.dsp.window.fullscreen({ mode = "fullscreen" }), { description = "Fullscreen" })
      hl.bind(mod .. " + T", hl.dsp.window.float(), { description = "Toggle floating" })
      hl.bind(mod .. " + C", hl.dsp.window.center(), { description = "Center window" })
      hl.bind(mod .. " + P", hl.dsp.window.pseudo(), { description = "Pseudo-tile" })
      hl.bind(mod .. " + S", hl.dsp.layout("togglesplit"), { description = "Toggle split direction (dwindle)" })

      -- Scratchpad (special workspace) — drops in with slidefadevert; keep a
      -- persistent ghostty+tmux session here.
      hl.bind(mod .. " + grave",         hl.dsp.workspace.toggle_special({ name = "scratch" }), { description = "Scratchpad" })
      hl.bind(mod .. " + SHIFT + grave", hl.dsp.window.move({ workspace = "special:scratch", follow = false }), { description = "Send window to scratchpad" })

      -- Vim + arrow focus
      hl.bind(mod .. " + H", hl.dsp.focus({ direction = "l" }), { description = "Focus left" })
      hl.bind(mod .. " + J", hl.dsp.focus({ direction = "d" }), { description = "Focus down" })
      hl.bind(mod .. " + K", hl.dsp.focus({ direction = "u" }), { description = "Focus up" })
      hl.bind(mod .. " + L", hl.dsp.focus({ direction = "r" }), { description = "Focus right" })
      hl.bind(mod .. " + left",  hl.dsp.focus({ direction = "l" }), { description = "Focus left" })
      hl.bind(mod .. " + down",  hl.dsp.focus({ direction = "d" }), { description = "Focus down" })
      hl.bind(mod .. " + up",    hl.dsp.focus({ direction = "u" }), { description = "Focus up" })
      hl.bind(mod .. " + right", hl.dsp.focus({ direction = "r" }), { description = "Focus right" })

      -- Vim + arrow move
      hl.bind(mod .. " + SHIFT + H", hl.dsp.window.move({ direction = "l" }), { description = "Move window left" })
      hl.bind(mod .. " + SHIFT + J", hl.dsp.window.move({ direction = "d" }), { description = "Move window down" })
      hl.bind(mod .. " + SHIFT + K", hl.dsp.window.move({ direction = "u" }), { description = "Move window up" })
      hl.bind(mod .. " + SHIFT + L", hl.dsp.window.move({ direction = "r" }), { description = "Move window right" })
      hl.bind(mod .. " + SHIFT + left",  hl.dsp.window.move({ direction = "l" }), { description = "Move window left" })
      hl.bind(mod .. " + SHIFT + down",  hl.dsp.window.move({ direction = "d" }), { description = "Move window down" })
      hl.bind(mod .. " + SHIFT + up",    hl.dsp.window.move({ direction = "u" }), { description = "Move window up" })
      hl.bind(mod .. " + SHIFT + right", hl.dsp.window.move({ direction = "r" }), { description = "Move window right" })

      -- Resize: numeric pixel deltas (x/y).
      hl.bind(mod .. " + minus", hl.dsp.window.resize({ x = -100, y = 0, relative = true }), { description = "Narrow window" })
      hl.bind(mod .. " + equal", hl.dsp.window.resize({ x = 100,  y = 0, relative = true }), { description = "Widen window" })

      -- tmux prefix mode: SUPER+A ≈ C-a (mirrors home/tmux.nix)
      -- One-shot like tmux: each action drops back to the root keymap.
      -- Resize (SHIFT+HJKL) repeats and stays in the mode, like tmux `bind -r`;
      -- catchall swallows unknown keys and exits, so a mistyped prefix command
      -- never leaks into the focused app. Waybar shows the mode while active.
      local function oneshot(...)
        local ds = { ... }
        return function()
          for _, d in ipairs(ds) do hl.dispatch(d) end
          hl.dispatch(hl.dsp.submap("reset"))
        end
      end
      hl.define_submap("tmux", function()
        -- prefix | / - : preselect the dwindle direction, spawn the terminal there
        hl.bind("backslash",         oneshot(hl.dsp.layout("preselect r"), hl.dsp.exec_cmd("ghostty")), { description = "Split right (new terminal)" })
        hl.bind("SHIFT + backslash", oneshot(hl.dsp.layout("preselect r"), hl.dsp.exec_cmd("ghostty")), { description = "Split right (new terminal)" })
        hl.bind("minus",             oneshot(hl.dsp.layout("preselect d"), hl.dsp.exec_cmd("ghostty")), { description = "Split down (new terminal)" })
        -- prefix c : new "window" → first empty workspace + terminal
        hl.bind("C", oneshot(hl.dsp.focus({ workspace = "empty" }), hl.dsp.exec_cmd("ghostty")), { description = "New workspace with a terminal" })
        -- prefix hjkl : pane navigation
        hl.bind("H", oneshot(hl.dsp.focus({ direction = "l" })), { description = "Focus left" })
        hl.bind("J", oneshot(hl.dsp.focus({ direction = "d" })), { description = "Focus down" })
        hl.bind("K", oneshot(hl.dsp.focus({ direction = "u" })), { description = "Focus up" })
        hl.bind("L", oneshot(hl.dsp.focus({ direction = "r" })), { description = "Focus right" })
        -- prefix HJKL : repeatable resize (stays in the mode; ESC to leave)
        hl.bind("SHIFT + H", hl.dsp.window.resize({ x = -80, y = 0,   relative = true }), { description = "Shrink width (repeats)", repeating = true })
        hl.bind("SHIFT + J", hl.dsp.window.resize({ x = 0,   y = 80,  relative = true }), { description = "Grow height (repeats)", repeating = true })
        hl.bind("SHIFT + K", hl.dsp.window.resize({ x = 0,   y = -80, relative = true }), { description = "Shrink height (repeats)", repeating = true })
        hl.bind("SHIFT + L", hl.dsp.window.resize({ x = 80,  y = 0,   relative = true }), { description = "Grow width (repeats)", repeating = true })
        -- prefix z / x : zoom pane, kill pane
        hl.bind("Z", oneshot(hl.dsp.window.fullscreen({ mode = "maximized" })), { description = "Zoom window" })
        hl.bind("X", oneshot(hl.dsp.window.close()), { description = "Close window" })
        -- prefix n / p : next / previous "window" (workspace)
        hl.bind("N", oneshot(hl.dsp.focus({ workspace = "e+1" })), { description = "Next workspace" })
        hl.bind("P", oneshot(hl.dsp.focus({ workspace = "e-1" })), { description = "Previous workspace" })
        -- prefix 1-9,0 : jump to workspace N (tmux select-window parity)
        for i = 1, 10 do
          local key = (i == 10) and "0" or tostring(i)
          hl.bind(key, oneshot(hl.dsp.focus({ workspace = i })), { description = "Workspace " .. i })
        end
        hl.bind("ESCAPE", hl.dsp.submap("reset"), { description = "Leave tmux mode" })
        hl.bind("catchall", hl.dsp.submap("reset"), { description = "Leave tmux mode" })
      end)
      hl.bind(mod .. " + A", hl.dsp.submap("tmux"), { description = "tmux mode (prefix)" })

      -- Workspaces 1-10 (focus + move-window-to)
      for i = 1, 10 do
        local key = (i == 10) and "0" or tostring(i)
        hl.bind(mod .. " + " .. key,           hl.dsp.focus({ workspace = i }), { description = "Workspace " .. i })
        hl.bind(mod .. " + SHIFT + " .. key,   hl.dsp.window.move({ workspace = i, follow = true }), { description = "Move window to workspace " .. i })
      end
      hl.bind(mod .. " + TAB",         hl.dsp.focus({ workspace = "e+1" }), { description = "Next workspace" })
      hl.bind(mod .. " + SHIFT + TAB", hl.dsp.focus({ workspace = "e-1" }), { description = "Previous workspace" })

      -- Notifications ${if caelestia then "(caelestia)" else "(swaync)"}
      ${shell
        [
          ''hl.bind(mod .. " + comma",         hl.dsp.exec_cmd("swaync-client --close-latest -sw"), { description = "Dismiss latest notification" })''
          ''hl.bind(mod .. " + SHIFT + comma", hl.dsp.exec_cmd("swaync-client -C -sw"), { description = "Clear notifications" })''
          ''hl.bind(mod .. " + N",             hl.dsp.exec_cmd("swaync-client -t -sw"), { description = "Notification center" })''
        ]
        [
          # Caelestia exposes no per-notification dismiss, so SUPER+comma is left
          # unbound here rather than mapped to something that means something else.
          ''hl.bind(mod .. " + SHIFT + comma", hl.dsp.global("caelestia:clearNotifs"), { description = "Clear notifications" })''
          ''hl.bind(mod .. " + N",             hl.dsp.global("caelestia:sidebar"), { description = "Notification center" })''
          ''hl.bind(mod .. " + D",             hl.dsp.global("caelestia:dashboard"), { description = "Dashboard" })''
        ]
      }

      -- Control panels
      -- CTRL+A is the full mixer; CTRL+S is the quick output picker (three
      -- outputs are in regular rotation, so switching shouldn't need a GUI).
      hl.bind(mod .. " + CTRL + A", hl.dsp.exec_cmd("pavucontrol"), { description = "Audio mixer" })
      hl.bind(mod .. " + CTRL + S", hl.dsp.exec_cmd("hypr-audio-sink"), { description = "Audio output picker" })
      hl.bind(mod .. " + CTRL + B", hl.dsp.exec_cmd("ghostty --class=TUI.float -e bluetui"), { description = "Bluetooth" })
      hl.bind(mod .. " + CTRL + T", hl.dsp.exec_cmd("ghostty -e btop"), { description = "System monitor" })
      hl.bind(mod .. " + CTRL + D", hl.dsp.exec_cmd("ghostty -e rice-dashboard"), { description = "Rice dashboard" })

      -- Utilities
      ${shell
        [
          ''hl.bind(mod .. " + CTRL + L",  hl.dsp.exec_cmd("hyprlock"), { description = "Lock screen" })''
          ''hl.bind(mod .. " + SHIFT + X", hl.dsp.exec_cmd("hypr-power-menu"), { description = "Power menu" })''
        ]
        [
          ''hl.bind(mod .. " + CTRL + L",  hl.dsp.global("caelestia:lock"), { description = "Lock screen" })''
          ''hl.bind(mod .. " + SHIFT + X", hl.dsp.global("caelestia:session"), { description = "Power menu" })''
        ]
      }
      hl.bind(mod .. " + SHIFT + W", hl.dsp.exec_cmd("hypr-wallpaper-pick"), { description = "Wallpaper picker" })
      ${shell
        [
          # Waybar handles SIGUSR2 as an in-process reload, avoiding a layer-shell
          # teardown/recreate flash. Start it only when no process is running.
          # The bare pattern matches .waybar-wrapped, the name nixpkgs' wrapper runs
          # it under; `-x waybar` never matched, so this always started a second bar.
          ''hl.bind(mod .. " + SHIFT + SPACE", hl.dsp.exec_cmd("pkill -USR2 waybar || ${waybarCmd}"), { description = "Reload the bar" })''
          # SIGUSR1 is waybar's show/hide toggle: the screen back, panels and binds intact.
          ''hl.bind(mod .. " + B", hl.dsp.exec_cmd("pkill -USR1 waybar"), { description = "Show/hide the bar" })''
        ]
        [
          # Caelestia has no reload signal; -k then -d is the supported restart.
          ''hl.bind(mod .. " + SHIFT + SPACE", hl.dsp.exec_cmd("bash -c 'caelestia shell -k; caelestia shell -d'"), { description = "Restart the shell" })''
        ]
      }
      hl.bind(mod .. " + SHIFT + R", hl.dsp.exec_cmd("hyprctl reload"), { description = "Reload Hyprland" })
      hl.bind(mod .. " + CTRL + N",  hl.dsp.exec_cmd("hypr-night-toggle"), { description = "Night light" })
      hl.bind(mod .. " + ALT + R",   hl.dsp.exec_cmd("hypr-record"), { description = "Record focused monitor → ~/Videos/Recordings" })
      -- Batch (offline Parakeet) is the primary binding. The streaming model
      -- drops speech mid-sentence for 10-15s at a time on clean, loud input
      -- and sometimes never recovers, which no amount of pipeline work fixes:
      -- see home/dictation.nix. Offline is the accurate one on a finished
      -- utterance, so it takes the plain chord and streaming keeps SHIFT.
      hl.bind(mod .. " + ALT + L",   hl.dsp.exec_cmd("~/.local/bin/toggle-dictation-batch.sh"), { description = "Dictation (offline)" })
      hl.bind(mod .. " + ALT + SHIFT + L", hl.dsp.exec_cmd("~/.local/bin/toggle-dictation.sh"), { description = "Dictation (streaming)" })

      -- Session
      hl.bind(mod .. " + SHIFT + E", hl.dsp.exit(), { description = "Exit Hyprland" })

      -- Repeating volume/brightness ${
        if caelestia then "(wpctl; caelestia draws the OSD)" else "(via swayosd for the OSD)"
      }
      ${shell
        [
          ''hl.bind("XF86AudioRaiseVolume", hl.dsp.exec_cmd("swayosd-client --output-volume raise"), { description = "Volume up", repeating = true })''
          ''hl.bind("XF86AudioLowerVolume", hl.dsp.exec_cmd("swayosd-client --output-volume lower"), { description = "Volume down", repeating = true })''
          ''hl.bind("XF86MonBrightnessUp",  hl.dsp.exec_cmd("swayosd-client --brightness raise"),    { description = "Brightness up", repeating = true })''
          ''hl.bind("XF86MonBrightnessDown",hl.dsp.exec_cmd("swayosd-client --brightness lower"),    { description = "Brightness down", repeating = true })''
        ]
        [
          # Caelestia declares brightness globals but no volume ones — its OSD
          # watches PipeWire directly, so driving wpctl is what surfaces it.
          # @DEFAULT_AUDIO_SINK@ is deliberate: modules/audio.nix ranks the
          # outputs so the default is whatever is actually being listened to.
          ''hl.bind("XF86AudioRaiseVolume", hl.dsp.exec_cmd("wpctl set-volume -l 1.5 @DEFAULT_AUDIO_SINK@ 3%+"), { description = "Volume up", repeating = true })''
          ''hl.bind("XF86AudioLowerVolume", hl.dsp.exec_cmd("wpctl set-volume @DEFAULT_AUDIO_SINK@ 3%-"),        { description = "Volume down", repeating = true })''
          ''hl.bind("XF86MonBrightnessUp",  hl.dsp.global("caelestia:brightnessUp"),   { description = "Brightness up", repeating = true })''
          ''hl.bind("XF86MonBrightnessDown",hl.dsp.global("caelestia:brightnessDown"), { description = "Brightness down", repeating = true })''
        ]
      }

      -- Locked binds (work while the lock screen is active)
      ${shell
        [
          ''hl.bind("XF86AudioMute",    hl.dsp.exec_cmd("swayosd-client --output-volume mute-toggle"), { description = "Mute", locked = true })''
          ''hl.bind("XF86AudioMicMute", hl.dsp.exec_cmd("swayosd-client --input-volume mute-toggle"),  { description = "Mute mic", locked = true })''
        ]
        [
          ''hl.bind("XF86AudioMute",    hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"),   { description = "Mute", locked = true })''
          ''hl.bind("XF86AudioMicMute", hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle"), { description = "Mute mic", locked = true })''
        ]
      }
      hl.bind("XF86AudioPlay",    hl.dsp.exec_cmd("playerctl play-pause"), { description = "Play/pause", locked = true })
      hl.bind("XF86AudioNext",    hl.dsp.exec_cmd("playerctl next"),       { description = "Next track", locked = true })
      hl.bind("XF86AudioPrev",    hl.dsp.exec_cmd("playerctl previous"),   { description = "Previous track", locked = true })

      -- Mouse drag/resize
      hl.bind(mod .. " + mouse:272", hl.dsp.window.drag(),   { description = "Drag window", mouse = true })
      hl.bind(mod .. " + mouse:273", hl.dsp.window.resize(), { description = "Resize window", mouse = true })

      -- Autostart
      hl.on("hyprland.start", function()
        -- Import env into systemd/dbus, then bounce the portals (mirrors the niri startup line)
        --
        -- Every step is `;`-separated, NOT `&&`. This line used to chain on
        -- `&&` through `systemctl --user start hyprland-session.target` — a
        -- unit that does not exist on this host and never has. Nothing defines
        -- it: the HM Hyprland module would, but this session builds its config
        -- as lua and is launched from the greeter, so the target was never
        -- generated. `systemctl start` on an unknown unit exits 5, the `&&`
        -- short-circuited, and the portal restart after it was dead code for
        -- the entire life of this session type. The niri line works only
        -- because it has no target start between the import and the restart.
        --
        -- Why the restart has to happen at all: the systemd *user* manager
        -- outlives individual sessions, so portal state carries across a
        -- logout. On logout xdg-desktop-portal-gtk dies with the compositor,
        -- gets re-activated by the still-running xdg-desktop-portal seconds
        -- before the next compositor exists, and comes up blind — "cannot open
        -- display". The stale portal then holds a broken proxy to it, and
        -- every GTK client that asks the Settings portal for color-scheme
        -- (Ghostty, at startup) blocks on ReadAll until that resolves.
        -- Measured on a logout/login round trip: session up at 17:31:45, first
        -- Ghostty surface at 17:33:39, one second after -gtk finally came up
        -- on its own. 114 seconds of a terminal that will not open, while
        -- non-GTK apps launched instantly.
        --
        -- reset-failed precedes the restart because xdg-desktop-portal-hyprland
        -- rate-limits itself out of existence during this window: it retries
        -- while there is no compositor, hits "Start request repeated too
        -- quickly", and a plain restart on a start-limited unit fails.
        hl.exec_cmd([[bash -c 'systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP HYPRLAND_INSTANCE_SIGNATURE NIXOS_OZONE_WL GBM_BACKEND NVD_BACKEND LIBVA_DRIVER_NAME __GLX_VENDOR_LIBRARY_NAME; dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP HYPRLAND_INSTANCE_SIGNATURE; systemctl --user reset-failed xdg-desktop-portal-gtk xdg-desktop-portal-hyprland xdg-desktop-portal 2>/dev/null; systemctl --user restart xdg-desktop-portal-gtk xdg-desktop-portal-hyprland xdg-desktop-portal 2>/dev/null']])
        hl.exec_cmd("awww-daemon")
        -- Give the daemon a moment to bind its socket, then restore the shared
        -- still wallpaper used by Niri, Hyprland, and hyprlock.
        hl.exec_cmd([[bash -c 'sleep 1 && ${pkgs.awww}/bin/awww img ${config.home.homeDirectory}/wallpaper.png --transition-type fade --transition-duration 1']])
        hl.exec_cmd("wl-paste --watch cliphist store")
        ${shellSep "\n  "
          [
            ''hl.exec_cmd("swayosd-server --style ${cfgHome}/swayosd/style.css")''
            ''hl.exec_cmd("${waybarCmd}")''
            # Through its systemd unit, not a bare process: swaync is also
            # D-Bus-activated (the first notification at login starts
            # swaync.service), and a second, exec'd instance made that unit lose
            # the race and sit in `systemctl --failed`. One unit, one instance.
            # The environment import is repeated here so this start can't beat
            # the import above it (autostart commands run concurrently).
            ''hl.exec_cmd("bash -c 'systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP HYPRLAND_INSTANCE_SIGNATURE; systemctl --user start swaync'")''
            ''hl.exec_cmd("hypridle")''
          ]
          [
            # One process replaces all four. Started here rather than as a systemd
            # user service (programs.caelestia.systemd.enable = false in
            # caelestia.nix) so it is scoped to this session — the upstream unit
            # binds graphical-session.target, which every Wayland session reaches.
            ''hl.exec_cmd("caelestia shell -d")''
            # No hypridle: Caelestia runs its own idle monitor and lock screen,
            # and two idle daemons racing to lock is exactly the kind of fight
            # this split exists to avoid.
          ]
        }
        -- hyprsunset as a plain autostart = hyprland-session-only by construction
        -- (a systemd user service would leak into the niri session and fight wlsunset).
        hl.exec_cmd("hyprsunset")
      end)
    '';
in
{
  # Two session tiles, one compositor config, differing only in shell layer.
  # modules/desktop-hyprland.nix points each .desktop entry at its own file.
  xdg.configFile."hypr/hyprland.lua".text = mkHyprlandLua "waybar";
  xdg.configFile."hypr/hyprland-caelestia.lua".text = mkHyprlandLua "caelestia";

  # hyprsunset: scheduled night light (config read by the autostart daemon)
  xdg.configFile."hypr/hyprsunset.conf".text = ''
    profile {
        time = 6:30
        identity = true
    }

    profile {
        time = 20:30
        temperature = 3500
    }
  '';

  # Waybar: this module owns ~/.config/waybar/{config,style.css}; disable the Stylix
  # waybar target so the two never fight over style.css.
  stylix.targets.waybar.enable = false;
}
