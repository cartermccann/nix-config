# GNOME Keyring client initialization race

The login keyring unlocked correctly through Ly, but GNOME Keyring 48.0 crashed
12 times in the seven days ending September 14, 2026. The latest crash had this
sequence:

```
gkd_secret_service_get_pkcs11_session: assertion 'client' failed
aes_negotiate: assertion 'session' failed
g_variant_new: expected GVariant of type 'v' but received value has type '(null)'
```

D-Bus restarts the daemon with its collections locked, causing repeated unlock
prompts when an app next requests a secret. The logs do not establish which app
triggers the race.

## Patch provenance and scope

`lib/overlays.nix` backports both commits from the still-unmerged upstream
[MR !112](https://gitlab.gnome.org/GNOME/gnome-keyring/-/merge_requests/112), addressing
[issue #190](https://gitlab.gnome.org/GNOME/gnome-keyring/-/issues/190):

- `c06dd839a40517c5c0470fd492d2b07ce1356d2e`: propagate negotiation failures and
  reject missing output before completing OpenSession.
- `8fcdeffe67ae8c1600eae9637816acbf73f66b81`: initialize missing ServiceClient
  records synchronously instead of relying on the message filter's queued idle.

The diff is pinned locally and applies to 48.0 with zero fuzz or offsets. The only
local adjustment is a comment correction: initialization can read the default
alias file, so upstream's claim that it performs no I/O was removed. No password,
PAM policy, encryption, keyring format, or app configuration changes are involved.
Remove the override once the pinned nixpkgs package includes an equivalent fix.
Upstream CI failures at review time were runner configuration errors; this
backport is validated locally rather than treated as an accepted upstream release.

## Regression check

Run from the repository root:

```sh
nix build --impure --no-link -L --expr '
  let f = builtins.getFlake (toString ./.);
      p = f.nixosConfigurations.perkons.pkgs;
  in p.callPackage ./pkgs/gnome-keyring/check.nix {}'
```

For the unpatched control, use the same expression with the final line replaced:

```nix
in p.callPackage ./pkgs/gnome-keyring/check.nix {
  gnome-keyring = f.inputs.nixpkgs.legacyPackages.x86_64-linux.gnome-keyring;
  expectCrash = true;
}
```

The control must reproduce the recorded abort. The patched daemon must process
19,200 fresh connections (16 threads, 200 rounds, both plain and DH algorithms,
three passes), close every session, reject unsupported algorithms and malformed
input types, and retain its original process and D-Bus ownership with no assertion
logs. Tests use empty temporary HOME/XDG/control directories and a private bus
with no service activation directories; they never access the desktop bus or real
keyrings. Core dumps are disabled for the deliberately crashing control.

Verified September 14, 2026: the unpatched control reproduced the exact recorded
OpenSession crash during the plain-connection phase (3,149 failed calls). The
patched package passed all 19,200 connections, session closes, and both invalid
request checks. Tested package:
`/nix/store/jz8lvrzk733jcd8agmq5d0ircw469ka0-gnome-keyring-48.0`.

Validate integration with `nh os build ~/nix-config`. Carter then applies with
`nrs` and logs out and back in (or reboots) to replace the running daemon and let
PAM unlock it. A successful build does not replace the currently running daemon.
After activation, check the daemon's `/proc/<pid>/exe` resolves into the patched
store output and check new journal entries after opening Codex and unlocking the
screen. Longer-term absence of prompts requires normal desktop use.
