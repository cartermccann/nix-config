{
  description = "NixOS Configuration";

  inputs = {
    nixpkgs.url = "nixpkgs/nixos-26.05";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    home-manager = {
      url = "github:nix-community/home-manager/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    google-workspace-cli = {
      url = "github:googleworkspace/cli";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    qmd = {
      url = "github:tobi/qmd";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    zed-delta = {
      url = "github:zed-industries/delta-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    delta-agent-history = {
      url = "github:comcreate-io/delta-agent-history/978c66b408a0314bf32cbe4f700574ac56262a24";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nixos-hardware.url = "github:NixOS/nixos-hardware/master";
    stylix = {
      url = "github:nix-community/stylix/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Niri is the fallback session (Hyprland is the daily driver), so it tracks
    # niri-flake's release channel rather than a dev branch. The previous
    # `inputs.niri-unstable.url = ".../niri?ref=wip/branch"` override arrived
    # incidentally (de7e930) with no rationale, went five months without a lock
    # bump, and forced a from-source compile plus `doCheck = false` to get past
    # the branch's failing tests. niri-stable is cached and actually released.
    niri.url = "github:sodiboo/niri-flake";
    # Hyprland 0.55+ (Lua config). Pinned to a release tag; intentionally NOT
    # following nixpkgs so prebuilt artifacts hit hyprland.cachix.org rather than
    # forcing a local source compile (substituter added in modules/common.nix).
    # v0.56.1, not v0.56.2: 0.56.2 is not on hyprland.cachix and its
    # from-source build fails at CMake FetchContent for glaze (2026-10-05).
    # 0.56.2 only adds a duplicate-config-value crash fix; retry next tag.
    hyprland = {
      url = "github:hyprwm/Hyprland?ref=v0.56.1&submodules=1";
    };
    ghostty = {
      url = "github:ghostty-org/ghostty";
    };
    noctalia = {
      url = "github:noctalia-dev/noctalia-shell";
    };
    # Caelestia — the Quickshell-based shell behind the "Hyprland (Caelestia)"
    # session tile. Deliberately NOT following our nixpkgs: it targets
    # nixos-unstable and builds quickshell from a git checkout under
    # clangStdenv, so pinning it back to 25.11 risks a Qt mismatch in a package
    # that has no binary cache to fall back on. The duplicated nixpkgs costs
    # eval time, not a second copy of the system closure.
    caelestia = {
      url = "github:caelestia-dots/shell";
    };
    zen-browser = {
      url = "github:0xc000022070/zen-browser-flake";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.home-manager.follows = "home-manager";
    };

    comcreate-desktop-app = {
      url = "git+ssh://git@github.com/comcreate-io/comcreate-dashboard?dir=desktop";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Claude Desktop repackaged for Linux (no official Nix support; official
    # Linux beta is apt-only). FHS variant needed so MCP servers can run
    # npx/uvx/docker.
    claude-desktop = {
      url = "github:aaddrick/claude-desktop-debian";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Keep the upstream runtime closure intact; its ELF audit targets its own
    # nixpkgs, so this input deliberately does not follow the host nixpkgs.
    codex-desktop-linux.url = "github:ilysenko/codex-desktop-linux/a0b23fed42be7f03d5a23f017efb111b1346a559";
    helium = {
      url = "github:schembriaiden/helium-browser-nix-flake";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    fenix = {
      url = "github:nix-community/fenix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    herdr = {
      url = "github:ogulcancelik/herdr/v0.9.3";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
      # herdr's lock pins a July rust-overlay that still reads stdenv.isLinux /
      # isDarwin, which warns on every eval. The Rust version itself comes from
      # herdr's rust-toolchain.toml, so a newer overlay doesn't change it.
      inputs.rust-overlay.follows = "rust-overlay";
    };
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
  };

  outputs =
    inputs@{ flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [ "x86_64-linux" ];
      imports = [ ./parts ];
    };
}
