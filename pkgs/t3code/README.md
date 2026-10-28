# T3 Code

The stable release is pinned in `sources.json` while nixpkgs catches up. This
reuses nixpkgs' source build and resource monitor, with the Electron/node-pty
libstdc++ wrapper retained.

`auseklis-updates check` includes T3 in the update badge. `auseklis-update` now
refreshes the stable source pin, calculates the release's pnpm dependency hash,
and builds T3 before continuing to the system test build. It skips a locally
edited pin and restores the original pin if verification fails. A successful
build still needs `nrs` and a T3 restart before the new version is running.

The focused updater can also run on its own:

```bash
python3 scripts/t3-update.py --repo "$HOME/nix-config" --user "$USER"
```

Exit status 3 means the pin changed and its package built successfully; 0 means
no change, and 1 means failure. The one-click updater uses that status to include
T3 in its app rollback and commit scope.

To update, set `version`, prefetch the release source with
`nix store prefetch-file --unpack --json
https://github.com/pingdotgg/t3code/archive/refs/tags/v<VERSION>.tar.gz`, and set
`srcHash`. Build once with `pnpmHash` set to `lib.fakeHash`'s SRI value, then use
the actual hash reported by Nix and rebuild. If nixpkgs changes its packaging,
review this override before updating the flake inputs.

Validate with `nh os build ~/nix-config`, then apply with `nrs` and restart T3.
Version 0.1.0 copies V1 history into a separate V2 database on first launch;
connected desktop and mobile clients must also support V2. Keep the old data for
rollback. OpenCode 2 requires 2.0.18 or newer; don't run OpenCode 1 and 2 together.

Upstream release: https://github.com/pingdotgg/t3code/releases/tag/v0.1.3
