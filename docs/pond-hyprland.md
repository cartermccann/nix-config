# Pond on Hyprland

Ready for interactive testing: implementation, both system builds, regression
tests and the focused security review passed. Nothing has been activated. See
[the implementation plan](pond-hyprland-plan.md) for scope.

## Session boundaries

The perkons chooser is Hyprland (the existing Waybar default), Hyprland
(Caelestia), and Hyprland (Pond). Ly remains the login manager, with the restored
cobalt/navy colormix animation. usins keeps its existing desktop and Ly animation.
Ly is the login greeter; each shell retains separate in-session locking.

Pond owns its shell surfaces, notifications, launcher, idle/lock handling and
wallpaper only while its session is active. Ghostty and Zen remain the terminal
and browser. Waybar and Caelestia retain their existing appearance and awww
backgrounds. The existing monitor/NVIDIA setup, audio EQ, and application profiles
must not change as a side effect of installing Pond.

## Upstream, runtime and assets

- Upstream interface: `bflycomputer/pond-interface` at
  `f77883588cf0b27bc4c5fa5c0ee2dc1814a448a6`.
- Pond's private Quickshell 0.3.2, native QML plugins, and rebuilt shaders use Qt
  6.11.2. Caelestia's Quickshell is not overridden globally.
- `pond-shell` and `pond-lock` wrap the packaged shell and locker; arguments such
  as `ipc show` are forwarded. The dedicated PAM service is `pond-lock`.
- The animated daylight sky is procedural QML/shader content. The static
  `pond-dusk.png` is upstream lock-screen artwork. No separate upstream photo
  collection was found; GRUB button assets are not wallpapers.
- Onest, Advent Pro, IBM Plex Mono, JetBrains Mono and Fastfetch Flowers are
  packaged. **Pond Gramercy is proprietary and not distributed by upstream.**
  Onest substitutes for it. A supplied font package can be configured via
  `pond-interface.override { gramercyFont = …; }`.

Package layout, dependency details and update instructions are in
[the package README](../pkgs/pond-interface/README.md).

## Verification boundaries

The combined corrective build passed `nh os build ~/nix-config`, the full usins
system build, and the packaged sandbox smoke test. The regression run passed
167 Python tests and 12 JavaScript tests with zero skips. The expanded lifecycle
suite also passed 170 cases in the parent rerun, with zero failures or skips.
Changed Nix files pass nixfmt; `git diff --check` is clean. Pond's generated Lua
passes Hyprland's config verifier. Waybar and Caelestia Lua remain byte-identical
to their pre-port versions. Generated session choices, Ly animations, Zen MIME
handlers and wallpaper assets were inspected.

Latest build and test logs are in `/tmp/pond-lock-final-validation/`; the earlier
configuration-inspection index is in `/tmp/pond-final-validation/`. The revised
lock policy passed focused independent review with no remaining findings.
Review evidence is in `/tmp/pond-lock-gate-review.w_dk_3ig/`.
The blank manual screenshot is caused
by upstream's shader-based rounded mask under Qt's software backend. A separate
sandbox render with only that mask disabled displayed the manual text and
shortcut rows correctly (`/nix/store/qs4y9633kmxkys0n3748b5rw733vg0r2-pond-manual-render-check/settings-manual.png`).
The installed GPU rendering path is unchanged and needs live acceptance.

The smoke test runs a private D-Bus session and headless sway in the Nix build
sandbox. It checks QML loading, native imports, fonts and IPC. It does not prove
functional hardware controls, real PAM authentication or NVIDIA rendering.
The locker loads with session locking disabled. Lifecycle tests use fake
compositors/lockers and a private login1 service, never the active system bus.

The corrective pass adds a durable updater activation-pending state: declining or
failing a switch does not mean the system is up to date merely because `flake.lock`
already holds the latest inputs. Retrying rebuilds the current flake and offers
authenticated switching. Recovery preserves terminal failure warnings across shell
restarts and checks transaction ownership before rewriting abandoned status.
Repeated terminal signals are deferred during child cleanup. These paths have
dedicated fake-process and real offscreen-QML regressions.

## Locking and simultaneous sessions

Manual, idle, settings and logind locking go through `pond-session`. A supervised
guard holds the sleep-delay inhibitor independently of the shell. Lock requests
require compositor-confirmed readiness, with a 12-second overall deadline;
logind's delay budget is 15 seconds. Pond confirms through exact-PID `isSecure`;
hyprlock confirms through its own compositor `locked` callback, recorded in a
PID/start-time-specific log. Process survival or global `locked=true` is not
accepted as a successful lock.

The helper never enables `allow_session_lock_restore` or replaces an unknown
holder. Hyprland 0.56.1 cannot safely distinguish a dead locker from another live
client, and replacing a live client lets its later unlock clear the new lock.
An unconfirmed locker stays alive; a later request can confirm it if it recovers.
A crashed locker can leave Hyprland locked without a usable unlock screen.
In that case, log in on another VT, identify the affected session with
`loginctl list-sessions`, and end **that Pond session** with
`loginctl terminate-session SESSION_ID`. This closes its applications, so use it
only for a genuinely unusable session. The helper does not force-unlock it.

Real PAM, fallback readiness and suspend/resume still need acceptance.
If both lockers fail before externally requested sleep, the delay inhibitor
cannot prevent sleep indefinitely; resume triggers retries and a critical
notification. An already-locked but unverified session reports failure without
attempting takeover. Idle detection still depends on the shell running.

Vicinae supports one endpoint per user: the first Pond session claims it; another
simultaneous Pond session reports that its launcher is unavailable. Notifications
also have one user-bus owner. Pond prevents swaync activation while it owns that
role; an already-running notification daemon in another session is left alone.
These are explicit simultaneous-session limits.

## Port differences

The minimap orders tiles by position and approximates insertion with dwindle
swaps. It has no Niri column stacks, overview transitions or live rearrangement
during a drag. Floating windows retain their positions. Fullscreen/maximized
windows cannot be reordered; the shell explains refused or partial operations.
Cross-output pointer delivery and mixed-scale geometry still need live testing.
Unconfirmed display settings revert on shell restart or config reload; an
independent watchdog protects ordinary helper/shell crashes.

## Startup diagnostics

Pond waits up to 15 seconds for Hyprland's instance lock file before taking
notification/launcher claims or starting its supervisors. Hyprland's
`hyprland.start` hook runs on the first rendered frame and can precede that file;
previously this race made all three supervisors exit, leaving a gray desktop.

Each supervisor now retains its output under
`$XDG_RUNTIME_DIR/pond-session/$HYPRLAND_INSTANCE_SIGNATURE/`:
`supervise-shell.log`, `supervise-launcher.log` and `supervise-guard.log`.
Lifecycle messages also appear in `journalctl -t pond-session`.
A missing launcher is reported as not running; the other-session warning is
reserved for a verified live claim belonging to another session.

## Interactive acceptance checklist

Run `nrs` to apply the built configuration, then log out and choose
**Hyprland (Pond)** in Ly. Test Pond first, then log out and test the existing
Hyprland/Waybar and Caelestia sessions. Check:

- Ly shows the colormix animation and exactly the three intended desktop choices.
- Each desktop launches; logging out of Pond and entering Waybar or Caelestia
  leaves no competing notifications, wallpaper, launcher or idle daemons.
- Ghostty and Zen open from keybindings, launcher actions and file/link handlers.
- The sidebar/minimap follows focus and workspace changes on both monitors;
  moving tiled/floating windows works, and fullscreen reorder refusals are clear.
- The daylight sky animates on NVIDIA; custom images, saved selections and shared
  wallpaper library work. Other sessions keep their existing backgrounds.
- Display settings can be confirmed or automatically reverted; unplugging a
  monitor and restarting the shell does not corrupt the saved layout.
- Lock/unlock, wrong-password handling, suspend/resume and monitor power-off work.
- Audio controls preserve the Arya default routing until an explicit sink change.
  Networking, Bluetooth, brightness and media controls behave as supported by hardware.
- Screenshots, clipboard history, notification actions, screen sharing and file
  dialogs work through the correct portal.

Do not switch configurations or engage a new locker on the active desktop as a
substitute for these user-driven session tests.
