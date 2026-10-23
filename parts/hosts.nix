{ inputs, ... }:
let
  mkHost =
    hostname:
    {
      system ? "x86_64-linux",
      user ? "carter",
      extraModules ? [ ],
    }:
    let
      pkgs-unstable = import inputs.nixpkgs-unstable {
        inherit system;
        config.allowUnfree = true;
      };
      overlays = import ../lib/overlays.nix { inherit inputs; };
    in
    inputs.nixpkgs.lib.nixosSystem {
      inherit system;
      specialArgs = {
        inherit
          user
          pkgs-unstable
          ;
        inherit (inputs)
          google-workspace-cli
          noctalia
          zen-browser
          helium
          hyprland
          claude-desktop
          ;
      };
      modules = [
        ../hosts/${hostname}/configuration.nix
        inputs.home-manager.nixosModules.home-manager
        inputs.niri.nixosModules.niri
        inputs.stylix.nixosModules.stylix
        ../modules/stylix.nix
        (
          { pkgs, ... }:
          {
            nixpkgs.overlays = overlays;
            # niri-stable (a tagged release) instead of niri-unstable off a dev
            # branch — the latter needed `doCheck = false` purely to skip tests
            # that fail on that branch, and rebuilt from source on every update.
            programs.niri.package = pkgs.niri-stable;
          }
        )
        (
          { pkgs, ... }:
          {
            users.users.${user} = {
              isNormalUser = true;
              shell = pkgs.fish;
              extraGroups = [
                "wheel"
                "docker"
                "networkmanager"
                "input"
                # crash-watch follows systemd-coredump on the system journal.
                "systemd-journal"
                # 1Password desktop-app integration gates its CLI/MCP sockets
                # on these groups; membership lets `op` and 1password-mcp connect.
                "onepassword-cli"
                "onepassword-mcp"
              ];
              openssh.authorizedKeys.keys = [
                # atlas (carter@) — Tailscale SSH access
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKCM/HUY44HAmSX35c7AXzhE/dbIUwz8F7ZeaaNieHY4 carter@atlas"
              ];
            };

            home-manager = {
              useGlobalPkgs = true;
              useUserPackages = true;
              users.${user} = {
                imports = [
                  ../home/common.nix
                ];
              };
              extraSpecialArgs = {
                inherit user pkgs-unstable;
                inherit (inputs)
                  caelestia
                  zen-browser
                  comcreate-desktop-app
                  codex-desktop-linux
                  google-workspace-cli
                  fenix
                  herdr
                  hyprland
                  qmd
                  zed-delta
                  ;
              };
              backupFileExtension = "hm-bak";
            };
          }
        )
      ]
      ++ extraModules;
    };
in
{
  flake.nixosConfigurations.atlas = mkHost "atlas" {
    extraModules = [ inputs.nixos-hardware.nixosModules.microsoft-surface-common ];
  };
  flake.nixosConfigurations.kronos = mkHost "kronos" { user = "cjm"; };
}
