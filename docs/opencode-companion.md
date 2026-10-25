# Private OpenCode companion host

After Carter applies `nrs`, `modules/opencode-companion.nix` enables the Android
companion's pinned OpenCode **1.18.32** as a system service at `multi-user.target`,
before desktop login. It runs independently of the interactive OpenCode package
and restarts after an unexpected exit. Ollama already has its own boot service.

## State and access

- Service: `opencode-companion.service`, running as `cjm`.
- State: `/home/cjm/.local/share/opencode-companion`, owner-only parent directory.
- Password: `/home/cjm/.config/opencode-companion/password`, regular file, mode
  `0600`, owned by `cjm`. Never put the value in Nix, argv, git or a URL.
- Inside Bubblewrap: `/state/workspace` is the disposable development project.
  Real home directories, SSH keys and client repositories are not mounted.
- HTTP listens only on `127.0.0.1:4096`. Tailscale Serve exposes authenticated
  HTTPS on port `8443` within the existing tailnet.
- Model: existing local Ollama `gemma4:12b-it-qat`. Provider credentials are
  not inherited. Rukis (formerly Mini) uses Perkons for inference, so Perkons must be online.

The server archive and unmodified executable both have fixed SHA-256 checks.
Updates require the mobile compatibility checks before changing this pin.
The launcher preserves configuration and sessions; it never generates a new
password or initializes an empty replacement state on startup.

## First activation

Migration must happen with the old host stopped and no active execution:
copy the entire existing state directory, including SQLite sidecars, into the
state path above, and copy its password unchanged into the protected password
path. Preserve the old copy as a backup. Compare every copied file before
starting the replacement. Never copy a live database or revert to the backup
after the replacement has accepted new work without reconciling that work.

The 2026-09-28 migration followed this procedure. The temporary user service
`opencode-companion-preview` keeps the migrated host available pending the
privileged system switch. It is not enabled at boot. Once the Nix build and
review pass, Carter runs this in the usual interactive shell, while idle:

```sh
systemctl --user stop opencode-companion-preview
nrs
systemctl is-active opencode-companion
```

The user applies `nrs` per the repository workflow. If the switch fails before
the system service starts, check that port 4096 is unused, then recover the
temporary server with `systemctl --user start opencode-companion-preview`.
Never run both against the same state or port.

## HTTPS persistence

The existing `tailscaled.service` is enabled at boot. Its saved Serve route was
converted from foreground to background with:

```sh
tailscale serve --bg --https=8443 --yes http://127.0.0.1:4096
tailscale serve status --json
```

Background Serve configuration persists across reboots in tailscaled's state;
no duplicate boot command is required. Check for the route in the top-level
`Web` map, outside `Foreground`. Do not use `tailscale serve reset`, because
other routes may exist. Only remove this route with
`tailscale serve --https=8443 off` when intentionally disabling the host.

## Verification and troubleshooting

After activation, verify `systemctl is-enabled opencode-companion` and
`systemctl is-active opencode-companion tailscaled`. Check the HTTPS origin
returns 401 without authentication, healthy version 1.18.32 with the existing
password, an authenticated SSE connection, and the unchanged session history.
Use a new disposable session for a single synthetic local-model prompt; never
resubmit a prompt after an ambiguous timeout. Re-read its durable history.

`journalctl -u opencode-companion` shows lifecycle and preflight failures.
OpenCode stdout/stderr are deliberately discarded to keep private content out
of journald. Its internal core log is mapped to `/dev/null` inside a bounded,
volatile log mount. Persistent sessions are outside that mount. For deeper
diagnosis, reproduce against disposable state and inspect logs privately.

Boot enablement and a supervised restart are separate from an actual reboot
test. No reboot has been performed for this change. The temporary user service
does not prove the installed system unit starts until `nrs` is applied.

Mini already has an enabled Void/runit `opencode-companion` service and a
persistent background Serve route on 8443. The read-only audit verified both
default runlevel links, no `down` flags, health/auth/SSE, and retention of its
unrelated 443 route. No Mini configuration change was necessary.
