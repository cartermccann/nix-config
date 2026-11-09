# iPhone development on perkons

The configuration provides `pymobiledevice3` 11.50.3, `libimobiledevice`,
`apple-dev`, and `apple-dev-setup`. USB pairing is handled by the NixOS
`usbmuxd` service. Apply a validated configuration with `nrs` in Fish
(`nh os switch ~/nix-config`). The system switch needs Carter's sudo password.

## Connect the iPhone

Use a Lightning USB data cable, unlock the phone, and approve Trust This
Computer. Enable Developer Mode under Settings > Privacy & Security when
available; iOS may require a restart and confirmation on the phone.

After the system switch:

```sh
pymobiledevice3 version
pymobiledevice3 usbmux list
```

Pairing data and device diagnostics are private local data. Keep them out of
Git. Do not restore, erase, update firmware, or change signing identities as
part of a connectivity check.

## CoachLoop

The current product is `~/projects/work/CoachLoop/product`; the Expo app is
`apps/mobile`. Its existing EAS `development` profile creates an internal
development client. Use a separately authorized EAS/macOS build for that
client, then serve JavaScript from perkons with the project's existing Metro
workflow. Expo Go does not include CoachLoop's custom native modules.

Installing these tools does not build, sign, upload, or install CoachLoop.
Preserve the selected build profile and app identity: current source uses
`co.sedberry.coachloop` for the default diagnostic build and
`online.coachloop.app` for production. Verify current app config before any
install; do not substitute one for the other.

Linux native builds through `apple-dev` remain experimental for this app.
The toolchain has a custom CocoaPods builder, but CoachLoop's Skia
XCFrameworks, VisionCamera/Nitro modules, and local ReplayKit module need
actual build/device qualification. No generated iOS project was created
during tooling setup. Keep EAS/macOS release builds in place.

## Screen control

Upstream pymobiledevice3 documents browser mirroring and touch/keyboard
control on iOS 27 or newer. It requires Developer Mode and a mounted
Developer Disk Image. Treat device interactions as a separate verified
session, not an automatic consequence of installing the package.

Do not mirror while testing CoachLoop camera or microphone capture:
upstream documents that mirroring prevents those sensors working normally.

T3's Device panel reported local iOS unavailable during setup. This package
does not add an iOS backend to T3 or establish agent control of the phone.

## Optional local iOS SDK

The pinned `nix-apple-dev` input provides the Swift/xtool build environment.
It uses its own pinned Python environment inside `apple-dev`; the standalone
`pymobiledevice3` command is the newer device-control package above.

The Apple SDK is not downloaded or enabled by this configuration. For a
separate native-build trial, obtain the matching Xcode archive from Apple
and follow the pinned toolchain's SDK setup instructions. No Apple account,
signing key, provisioning profile, or paid build is needed merely to install
the local tools.

## References

- https://github.com/doronz88/pymobiledevice3
- https://github.com/doronz88/pymobiledevice3/blob/master/docs/guides/screen-mirroring.md
- https://github.com/m9tdev/nix-apple-dev/tree/3cd81da2856d86cc5030e3da74760c7bd05114aa
- https://docs.expo.dev/develop/development-builds/introduction/

Update device tooling by changing `pkgs/iphone-tools/pyproject.toml`, running
`uv lock` there with Python 3.13, updating the wrapper's version in
`default.nix`, and rebuilding. The lock pins dependency artifacts and hashes.
