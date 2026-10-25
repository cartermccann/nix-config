{
  lib,
  opencode,
}:
# OpenCode 1.x pinned ahead of nixpkgs. auseklis-update bumps sources.json to
# the newest v1.x release: srcHash from `nix store prefetch-file --unpack` of
# the tag tarball, nodeModulesHash only when it differs from nixpkgs' (null
# keeps nixpkgs'). Once nixpkgs carries a 1.x at least as new, it wins and the
# pin goes inert.
let
  sources = lib.importJSON ./sources.json;
  nixpkgsCurrent =
    lib.versions.major opencode.version == "1" && lib.versionAtLeast opencode.version sources.version;
in
if nixpkgsCurrent then
  opencode
else
  opencode.overrideAttrs (
    old:
    {
      inherit (sources) version;
      src = old.src.override {
        tag = "v${sources.version}";
        hash = sources.srcHash;
      };
    }
    // lib.optionalAttrs (sources.nodeModulesHash != null) {
      passthru = old.passthru // {
        node_modules = old.passthru.node_modules.overrideAttrs { outputHash = sources.nodeModulesHash; };
      };
    }
  )
