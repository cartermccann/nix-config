# Pond on Hyprland

The Pond session is running on perkons after the startup-race correction.
The original implementation, both system builds, regression tests and focused
security review passed. See [the implementation plan](pond-hyprland-plan.md)
for scope and the verification limits below.

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

## Sidebar controls

Pond adds five cards above the bottom controls. Expanded cards show status;
collapsed cards retain icons, activity/error dots and hover labels. Clicking
opens the matching Pond card stack on that display. Escape, its close button,
or clicking outside dismisses it. On short displays the control stack scrolls
rather than covering the workspace minimap.

- **Dictation:** live streaming (`Super+Alt+Shift+L`) and batch
  (`Super+Alt+L`), using the existing Home Manager scripts and default microphone.
  The panel closes before starting/stopping so typed text returns to the app.
  The other mode is disabled while recording; status follows the keyboard
  shortcuts too. Model preparation/transcription runs without blocking the UI.
- **Keep awake:** 30 minutes, one hour, two hours, or until Pond exits. Activation
  requires a confirmed logind idle inhibitor; while active Pond removes its
  automatic lock/display/suspend monitors. Expiry, manual stop, or shell exit
  restores normal idle behavior. It never changes saved idle settings or the
  separate sleep-lock guard. Manual lock and suspend remain available.
- **Hardware:** CPU, available-memory-based RAM usage, NVIDIA GPU load,
  temperature/VRAM, and home-filesystem usage. One sampler serves all displays,
  sampling the system every five seconds and GPU every ten. Missing readings
  show as unavailable. The popout can open btop in Ghostty.
- **Bluetooth:** adapter power, paired-device connections, discovery and pairing
  through Pond's existing Bluetooth panel and agent.
- **Updates:** Update all refreshes pinned apps and system inputs through one
  guarded transaction. Each app row also has an Update button that leaves other
  app pins and inputs alone. Both open Ghostty for confirmation, an unprivileged
  system build, and confirmation before the authenticated switch. Check refreshes
  system revisions and the app-version cache through the existing user service.
  The terminal command `update` uses the same transaction; `update cursor` targets
  one app (also `codex`, `t3`, `granola`, `grok`, `opencode1`, and `delta`).
  Interrupted app stages are retried before switching. Failed builds retain app
  hashes and their matching input revisions together, with the original lock
  backed up in the transaction state directory. Review the diff before committing.

OpenCode 1's Bun dependency output is validated even when its version is current:
updating Bun can change the fixed-output hash without an OpenCode release. The
updater accepts a replacement hash only from the exact dependency derivation,
then builds the binary and tests the real v1 server API. A private Git-directory
ledger recognizes the updater's own exact pin bytes between runs; unknown local
pin edits are preserved and must be reviewed before updating that app.

`pond-shell ipc call controls open dictation` also opens a control (other names:
`awake`, `hardware`, `bluetooth`, `updates`); `controls close` dismisses them.
After rebuilding and applying with `nrs`, `pond-session restart-shell` loads the
new UI without logging out. The helper replaces the shell supervisor too, so
it cannot retain the previous package's store path. It leaves the launcher,
lockers and independent sleep-lock guard running. Keep-awake deliberately
resets on that restart.

Older helpers kept the old supervisor: after a rebuild this left the new IPC
client unable to find the old shell, including workspace-number shortcuts.
Logging out and back into Pond recovers that mismatch. Once the corrected
helper is applied, `pond-session restart-shell` switches to the current package.

Browser notification icons resolve Zen's `zen` identity to its installed
`zen-beta` desktop entry. Missing icons use Pond's bundled bell, so a host icon
theme without `application-x-executable` cannot produce checkerboards.

The restart/icon correction passed `nh os build`, sandbox smoke, the focused
restart/upgrade tests and real QML icon tests. The full lifecycle run recorded
176 passes and two timing failures (hung-lock IPC deadline, delayed lock
confirmation); those cases passed three and two isolated reruns respectively
without code changes. Logs: `/tmp/pond-restart-final-lifecycle.log`,
`/tmp/pond-lock-budget-check.log`, `/tmp/pond-default-race-check.log`.

Validation for these controls: `nh os build` and the sandbox smoke passed;
174 Python and 12 JavaScript tests passed with no skips. The smoke renders all
five popouts in both layouts. Live dictation focus/typing and Bluetooth device
actions remain acceptance checks; the tests use fake actions, never a live
recorder, inhibitor or pairing request.

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
