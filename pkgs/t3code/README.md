# T3 Code

The stable release is pinned in `sources.json` while nixpkgs catches up. This
reuses nixpkgs' source build and resource monitor, with the Electron/node-pty
libstdc++ wrapper retained.

`ouranos-updates check` includes T3 in the update badge. `ouranos-update` reports
the pinned and latest stable versions. It does not automatically change this
source pin: a new release also needs a verified pnpm dependency hash and a build.

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

Upstream release: https://github.com/pingdotgg/t3code/releases/tag/v0.1.0
