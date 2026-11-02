# Pond review and verification status

Ready for interactive testing. The final focused security review passed with no
remaining findings. Both hosts build, the package smoke test passes, and all
349 regression cases pass without skips. Nothing has been activated.
Live hardware and authentication acceptance remains with the user; see
[the test guide](pond-hyprland.md).

## Session and locking

L1–L7 and A1 are closed in the reviewed scope. The helper uses PID/start-time
registration, bounds lock calls including queueing, detects compositor death,
and unifies manual, idle, settings and logind lock requests. A supervised guard
handles sleep/resume independently of the shell. Persistent notification claims
gate swaync activation; all launcher paths respect the first session's claim.
Workspace/screenshot IPC bindings and the manual match the generated Pond Lua.

The final recovery design requires exact client confirmation: Pond's `isSecure`
or hyprlock's compositor `locked` callback in its own PID/start-time log.
Historical records, process survival and global lock state cannot prove success.
The helper never enables the restore option or replaces an unknown holder.
Unconfirmed clients stay alive and can confirm on a later request. A crashed
locker may require ending the affected session from an authenticated VT.

The focused review closed delayed-fallback false readiness, lost recovery,
stale-owner takeover, failed restore reset, deadline overrun, and QML launcher
guard bypass. It independently verified pinned protocol/logging semantics and
isolated boundary tests. Evidence: `/tmp/pond-lock-gate-review.w_dk_3ig/`.
The expanded lifecycle suite passes 170 cases; running it against the preceding
script produced 42 failures, demonstrating coverage of the corrected behavior.

## Compositor and settings

A2, A3, A5, A6, A9 and A10 closed in corrective review. A4's remaining
creation-row partial movement was fixed and covered by generated Lua and
real-adapter/fake-compositor regressions.

- Display rollback state and a ready watchdog exist before mutation. Restart
  restores and verifies pending geometry; failed rollback remains actionable.
- Fullscreen/maximized reorder is refused before mutation. Partial movement
  errors report the observed location and appear in the relevant sidebar.
- Cross-monitor dragging shares a coordinator and preserves creation/output
  intent through workspace-ID collisions.
- Failed snapshots retry with backoff; successful recovery restores connection
  state. System-setting mutations serialize and stale queries are discarded.

## Updater

A7/A8 and their follow-up regressions are addressed. Transactions serialize,
protect concurrent lock-file edits, detect source drift before switch, preserve
pending activation, and restore unresolved warnings after shell restart.
Reconciliation rereads status under the mutex. Repeated signals cannot interrupt
child cleanup. An inherited-lock guardian retains serialization if the updater
is killed while build/switch children survive. Regression coverage includes
catchable signals, SIGKILL, concurrent edits and real offscreen QML callbacks.

## Final evidence

`/tmp/pond-lock-final-validation/` contains the final executable validation:

- `nh os build ~/nix-config` and the full usins system build: pass.
- Final package sandbox smoke: pass.
- Python: 167 tests; JavaScript: 12 tests; lifecycle: 170 cases. Zero skips.
- Functional source hashes still match the tested files.
- Changed Nix formatting and `git diff --check`: clean.

Earlier configuration inspection in `/tmp/pond-final-validation/` verifies
Pond Lua, byte-identical Waybar/Caelestia Lua, chooser entries, Ly animations,
Zen MIME handlers and wallpaper assets. Subsequent changes were confined to
lock handling, guarded launcher calls, tests and documentation.

The standard smoke's blank manual is a Qt software-renderer shader-mask limit.
A separate sandbox render with only that mask disabled displayed its content.
Real NVIDIA rendering, PAM, suspend/resume, cross-output pointer interaction,
DDC and portals remain interactive acceptance checks, not automated claims.

## 2026-11-02 live-login startup correction

The first real Pond login exposed a startup ordering gap: all three supervisors
reported the compositor gone before its instance lock file was ready, leaving
no shell or launcher. The launcher notice incorrectly claimed another session
owned it even though the recorded owner was absent.

Startup now waits up to 15 seconds for the compositor before taking claims or
spawning supervisors. Supervisor output is retained in per-session runtime logs,
and missing-launcher notices distinguish absence from another live owner.
Six new startup regressions reproduce the late-file condition: five failed on
the previous implementation, and all six pass with the fix. The complete
lifecycle suite passes 176 cases, zero failures/skips; `nh os build` and
`git diff --check` pass. Logs: `/tmp/pond-startup-before.log`,
`/tmp/pond-startup-after.log`, `/tmp/pond-startup-lifecycle.log` and
`/tmp/pond-startup-build.log`. The corrected live login remains to be retried.
