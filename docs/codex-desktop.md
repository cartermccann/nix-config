# Codex desktop on perkons

The desktop entry is configured in `home/codex-desktop.nix`. It uses the
`codex-desktop-linux` flake input, wrapped by `pkgs/codex-desktop/default.nix`.
The input retains its own nixpkgs because its native runtime needs that exact
library set. All application libraries are now dependencies of the Nix package.

The current pin uses official Linux app **26.924.51851** through community
source revision `1545938db09233638a32bbb7635d22eecafec386`. From this
release the Linux Computer Use backend ships in the `unified-computer-use`
plugin (`computer-use` is a manifest-only stub), so the cache-version hash is
applied there.

## Preserved behavior

- `codex-micro`: Linux USB/Bluetooth native bindings, feature visibility and
  hotplug support. Existing system udev rules remain in `modules/common.nix`.
- `directory-only-working-tree-watch`: bounded recursive watches with Git
  ignore handling, replacing the old custom `__cjmRecursiveWatch` preamble.
- Composer dictation: local gate override `4100906017`, retaining upstream
  microphone availability, loading and ChatGPT authentication checks.
- System dictation scripts and Micro bridge services remain independently
  configured by their existing Home Manager modules.
- Native `~/.config/Codex` profile and `~/.codex` agent data; explicit user
  overrides remain supported. No profile migration is needed.
- `codex-desktop` window class, URI forwarding, and scope limits of 28 GiB
  MemoryHigh, 32 GiB MemoryMax and 8,000 tasks. Existing environment overrides
  for those limits are honored. Each invocation cleans only its own exited
  application's scope.

The wrapper refuses to start while the legacy overlay's primary process is
running. Community launch usage reporting defaults off.

## Computer use and conversational voice

`computer-use-linux` enables the community Linux backend and app UI. Official
Linux Computer Use is not yet supported by OpenAI. The backend uses Hyprland
window targeting, the screenshot portal, AT-SPI, and the existing ydotoold
socket at `/run/ydotoold/socket`. The launcher exports that socket path while
honoring an explicit `YDOTOOL_SOCKET` override. No new uinput permissions are
needed. This controls the shared desktop; it is not a separate virtual desktop.
The packaged plugin version includes a backend content hash: upstream reused
its version across binary changes, and the app otherwise retains stale staging
and installed-plugin caches. Restart reconciles the changed version normally.

After switching and reopening the app, check Plugins > Computer Use and start
with "Check whether Linux Computer Use is ready". The plugin was already enabled
in Carter's Codex configuration. Keep initial trials confined to a disposable
test window.

Conversational ChatGPT Voice is separate from composer dictation. Settings >
Voice > Voice chat contains persistent controls; Start new voice chat resumes
the microphone/voice setup. The introductory banner records itself as seen when
setup opens, even before completion. Voice availability still depends on account
and workspace access. Screen context is a macOS-only extra in this release.

### Voice and pet overlay bounds

The shared avatar/voice overlay's native-draw layout allocates a canvas almost
twice the tallest display's height. On perkons, a 1,920-pixel portrait display
and an 87-pixel mascot produce `2 * 1920 + 56 - 87 = 3809` pixels, matching the
observed oversized voice window. The window is non-resizable; a compositor
resize did not hold. This is app geometry, not a Caelestia size rule.

The local Linux fix selects the existing compact viewport branch after the
native tray and placement calculations. That branch recomputes the mascot and
control offsets for its bounds instead of cropping the large canvas afterward.
Other platforms retain their existing native-canvas branch. The change is
applied during the Nix build, not to the running application's archive.

After activation and app restart, verify a voice call followed by navigation
to another task: the overlay should remain compact, its mute/end controls
should work, and other apps should remain usable. Check pet appearance and
expanded tray/captions as well. A successful build does not establish these
live interaction results.

### Hooks host capability

Stripe plugin 0.10.3 uses `command: node` with the script in a separate `args`
field. Codex 0.162.0 and 0.162.1 ignore that field and run bare Node on the hook's JSON
stdin, producing a syntax error. `codex-hooks-repair` folds only the recognized
Stripe lifecycle script argument into its command and saves the original
manifest beside it as `hooks.json.codex-compat-backup`. The desktop guard runs
this repair on launch. The `codex-hooks-repair.timer` user unit also checks
Stripe's manifests every minute, covering plugin refreshes while the app stays
open and newly installed version directories. An unchanged manifest is not
rewritten. Run `codex-hooks-repair` directly for an immediate repair.

After `nrs`, verify the timer with
`systemctl --user status codex-hooks-repair.timer` and its latest run with
`journalctl --user -u codex-hooks-repair.service -n 10`. There is a short interval
between a refresh and the next check; a running session may also retain cached
hook commands until it reloads them.

The repair leaves hook enablement and trusted hashes alone. Approve the corrected
Stripe commands in Settings > Hooks once; changed commands require native trust
review. Other plugins and hooks remain untouched. Verify the effective commands
with local app-server `hooks/list`, and run
`python3 scripts/test_codex_hook_compat.py` for the compatibility checks.

The durable/cloud app-server currently rejects `hooks/list`. A local compatibility
patch prevents that RPC in the shared settings/composer query and shows an
explicit unsupported message in Hooks settings, including while project roots
are missing. Refresh is disabled and cannot report a false success. Local and
connected hosts retain their original scoped RPCs, errors, and hook trust rules;
remote operations are never redirected to the local configuration.

The patch checks archive bounds globally and changed JavaScript digests strictly,
preserving unrelated payloads and metadata. Unknown source shapes fail the build.
This workaround is version-specific and should be rechecked when upstream adds
cloud hook support. After switching and restarting, verify the cloud message and
a successful local/connected Hooks settings load. Bundle tests do not establish
those live UI results.

## Updating and checking

Use the declaratively installed updater from any directory:

```bash
codex-update --check        # Read-only comparison with upstream
codex-update                # Update CLI and desktop pins, test, and build
codex-update --cli-only     # Update only the CLI, then test and build
codex-update --desktop-only # Update only the desktop, then test and build
```

The command defaults to `~/nix-config`; `--repo /path/to/nix-config` selects another
checkout. Before the first system switch, run the source directly with
`python3 ~/nix-config/scripts/codex-update.py --check` or omit `--check` to update.

The CLI version and architecture-specific hashes live in `pkgs/codex/sources.json`.
The updater resolves OpenAI's latest stable GitHub release, requires both Linux
musl package assets and their SHA-256 digests, and verifies changed downloads
using Nix before writing either hash. Alpha releases and downgrades are rejected.

The desktop updater resolves the community repository's current `main` revision,
which pins hashed official Linux packages. It retains an explicit revision in
`flake.nix` and prepares a targeted lock update in a temporary directory. Changes
to another root input's dependency graph are rejected. Only the CLI manifest,
desktop input line, and desktop lock closure are updated; local patches and
launcher settings are retained. The normal build applies those patches to the
actual payload and rejects unknown bundle shapes.

Every update runs all four local desktop patch test suites and `nh os build`.
Failed or interrupted builds restore the command's pin edits to their prior
contents, including pre-existing local edits. Concurrent edits are preserved and
reported for inspection. A per-checkout lock prevents simultaneous updater runs.
`--check` fetches metadata only and does not download packages, write files, or
build. This is an on-demand command; no scheduled updater or privileged switch
is installed.

Apply the successfully built result with `nrs` from a
normal terminal; a privileged switch requires Carter's sudo password. Quit the
existing app and reopen the desktop entry after activation. Actual dictation
and hardware key actions should be checked in the newly opened app.

The local ASAR patches reject unknown bundle shapes rather than silently lose
the modifications. The Watchbound repair updates native-module metadata after
Nix's ELF patching; the loader's size and SHA-256 checks remain enabled.

For a non-launching diagnostic, invoke the package's
`opt/codex-desktop/start.sh --diagnose` directly. The upstream `bin/codex-desktop`
wrapper can prepend Wayland flags ahead of `--diagnose`, accidentally launching
the GUI instead.

## Rollback

The previous overlay remains at
`~/projects/input-linux/codex-desktop-overlay/`. Its files were not replaced.
`~/projects/input-linux/codex-update-20260904/` contains the prior configuration
snapshots, signed-release evidence, and a GC root for the previous system.
The snapshots include pre-existing local work; do not blindly restore entire
files over newer edits. Use the previous NixOS generation or restore only the
Codex-specific configuration changes, then rebuild and switch.

The update does not commit, reset or otherwise discard unrelated work in the repo.
