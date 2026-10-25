# Hypruse on perkons

Standalone, pinned Hypruse 0.10.0 proof of concept. This does not patch Codex,
replace its CUA integration, install global tools, or require a system switch.
Source revision `7dd11acae0a5631825907f0ba8340dd4088998c4` includes release 0.10.0
and its metadata follow-ups. The package uses the desktop session's `hyprctl`
instead of introducing a mismatched compositor package.

## Verified September 4, 2026

- Nix package builds and passes 585 upstream tests; 4 skipped and 13 live-desktop
  tests deselected. Python import and runtime dependency checks pass.
- A real stdio MCP handshake exposes exactly seven observation tools with
  `HYPRUSE_READONLY=1`, and no action tools.
- Two disposable, offline GTK processes were identified by exact PID, class,
  title and window address in a dedicated special workspace.
- Hypruse reads their actual AT-SPI buttons, notebook tabs, and text fields.
  GTK Entry has role `text` here, so `ui(actionable=false)` is required to include
  it; default actionable filtering omits it.
- A stale-window attempt was refused. A subsequent focus change stopped capture
  before a screenshot or input action. In the subsequently authorized trial 4,
  cropped screenshots and real named-button clicks worked in both apps, Page 2
  selection worked, and focus switched to app B. The keyboard Space command
  reported success but did not increment the button count; keyboard reliability
  remains unresolved. Do not claim full desktop readiness.
- Trial 4 cleanup confirms both fixtures stopped and original window focus
  restored. Exact cursor restoration did not verify (the compositor can warp
  the pointer on focus), so this is not claimed.
- Six local regression tests pass normally and under optimized Python. They
  check stale identity, geometry/monitor changes, hidden workspaces and focus
  theft. An independent read-only review passed the corrected harness.

Evidence lives locally in `~/.local/state/hypruse-poc/20260904-trial-*`.
The latest observation is `20260904-trial-4/observe.json`.
Trial 4 has a navigation-only success marker, not a full action-pass marker.
A client argument-name collision found during this trial was corrected before
any input was delivered. The navigation-only continuation omitted keyboard checks. These records contain
only disposable test content and window identity; they are not committed.

## Build and preserve the package

Run from this checkout (the expression uses Carter's existing locked nixpkgs):

```sh
nix build --impure --out-link /home/cjm/.local/state/hypruse-poc/package \
  --expr 'let f = builtins.getFlake "/home/cjm/nix-config"; pkgs = import f.inputs.nixpkgs { system = "x86_64-linux"; }; in pkgs.callPackage /home/cjm/nix-config/pkgs/hypruse {}'
```

That external symlink is a GC root, not a global installation or repository
`result` symlink. The fixture and SDK environment are separate outputs of
`poc.nix`, built in the same way with `import .../poc.nix { inherit pkgs; }`.

## Activate the scoped integration

`trial/.codex/config.toml` is an observation-only project configuration.
It was verified with:

```sh
cd /home/cjm/nix-config/pkgs/hypruse/trial
codex mcp get hypruse --json
```

Start a Codex task/CLI from that directory to load it. The trusted ancestor is
`/home/cjm/nix-config`; this does not change global trust or MCP configuration.
The existing root `.codex` is an unrelated zero-byte file and is left intact.
`codex -C ... mcp get` did not use the directory for configuration discovery in
the installed CLI; actually changing the working directory did.

The current task and prior voice call do not automatically gain tools because a
file exists. Verify a new task's tool list or `/mcp` before use. For a running
desktop client, OpenAI's documented MCP flow uses Settings → MCP servers →
Restart after configuration; do not restart the whole app during ongoing work.
See https://learn.chatgpt.com/docs/extend/mcp?surface=cli .

## Finish the controlled live test

`fixture-session.py` owns two disposable GTK apps and attempts to restore the
recorded focus/cursor on Ctrl-C, recording each result separately. It moves only its verified windows to
`special:hypruse-poc`; no existing app is moved or closed. `poc-client.py` accepts
the built Hypruse binary, the fixture evidence directory, and one phase:

1. `observe`: verify tool exposure, explicit fixture accessibility, then a
   cropped screenshot while the expected fixture is focused.
2. Inspect that screenshot; stop if any unexpected UI obscures the fixture.
3. `dryrun`: rehearse targeted typing without delivery; verify no text changed.
4. `act`: only after successful observation/rehearsal, test button clicks,
   keyboard Space, notebook selection and focus switching between the two
   fixture processes. Verify the app's own state after every action.
5. Ctrl-C the fixture keeper and inspect `cleanup.json`.

Do not run the input phase while the user is interacting. Stop on focus theft,
wrong identity, changed geometry, missing/ambiguous controls, tool errors or
unexpected state. No credential entry, network action or production UI belongs
in this test. Pointer/navigation testing passed in trial 4; the full keyboard-inclusive test has not.

## Boundaries found during source review

- `screenshot(window=...)` is a `grim` screen-rectangle crop, not an offscreen
  surface. Hidden or obscured targets can return other content. Require visible,
  stable, unobscured targets; never use an unscoped full-screen capture here.
- Never use fused `then="screenshot"`: it captures the focused monitor. Some
  fused `then="ui"` paths fall back to the active window. Use explicit reads.
- READONLY truly removes action tools. Confinement does not restrict reads and
  is not a security sandbox; workspace changes/pointer motion have wider scope.
- Keep marking and clipboard disabled. Marking installs a runtime border rule.
- The default `doctor` captures an unrelated 8×8 screen region; this trial uses
  targeted checks instead.
- `launch` can associate the first unrelated open-window event with its launch;
  use external, explicitly verified fixture processes for this trial.
- Native input shares the user's seat. Focus checks and delivery are not atomic.
- GTK text fields are absent from the default actionable list; named `click_ui`
  also uses that filter. Full-tree observation works; text-field clicking and
  broad app coverage need further work.

Source: https://github.com/IlyasKhallouki/hypruse/tree/7dd11acae0a5631825907f0ba8340dd4088998c4

## Rollback

Stop only the dedicated Hypruse/fixture processes. Remove the `hypruse` table
from this trial's `.codex/config.toml` (or rename that file), then restart that
MCP connection/task. Remove the external package symlink if no longer needed;
normal Nix GC can reclaim the package afterward. No system generation rollback,
Codex bundle restoration, browser profile changes or privileged switch is needed.

## Voice popout investigation

Installed Codex 26.901.31953 automatically changes from main-thread voice to its
global overlay when navigating away. No disable-auto-popout preference was
found. General → Popout Window is a separate feature. The observed floating
window was 772×3809 at y=-1329; the precise cause is not proven. Closing/dismissing
the voice overlay resets voice, so it was not touched. Carter subsequently ended
voice normally; it was not reopened.

The approved geometry workaround is implemented in
`../../scripts/codex-voice-resize.py`. It requires a freshly inspected exact
window address and defaults to a dry-run. It rejects tiled/main-window geometry
and requires the observed native floating identity and abnormally tall shape.
Those properties are guards, not proof of the window's purpose: inspect the
actual overlay before supplying its address. No blanket class rule is installed.

```sh
python /home/cjm/nix-config/scripts/codex-voice-resize.py --address 0xCURRENT
python /home/cjm/nix-config/scripts/codex-voice-resize.py --address 0xCURRENT --apply
```

Apply records a private rollback JSON before changing geometry. It targets
460×680 within the monitor workarea, rechecks the exact window identity in Lua,
and verifies geometry twice. To restore the same surviving window in the same
Hyprland session, use the printed snapshot path:

```sh
python /home/cjm/nix-config/scripts/codex-voice-resize.py --address 0xCURRENT --restore /tmp/codex-voice-geometry-XXXX.json --apply
```

Eight helper tests passed; read-only live Lua checks verified lookup, stable ID
matching and dispatcher construction. The current main window was refused.
The overlay is absent, so no resize/move was applied and visual usability remains
unverified. The helper does not reopen voice or disable automatic popout.

### Live voice-overlay trial

During the subsequent voice call, the actual 772×3809 overlay covered Tidal.
The helper resize did not hold, and its combined resize/move restore also failed
verification. A guarded move-only dispatch restored the exact original position
[179, -944], verified with size [772, 3809]. Do not treat the resize helper as a
working workaround yet. Tidal text entry was not attempted because focus was
unstable and the search UI was obscured.
