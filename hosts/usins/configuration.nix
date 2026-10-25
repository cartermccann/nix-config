{
  config,
  lib,
  pkgs,
  ...
}:

{
  imports = [
    ./hardware-configuration.nix
    ../../modules/common.nix
    ../../modules/desktop-wayland.nix
    ../../modules/desktop-niri-noctalia.nix
    ../../modules/ollama.nix
    ../../modules/audio.nix
    ../../modules/bluetooth.nix
    ../../modules/printing.nix
    ../../modules/networking.nix
    ../../modules/dictation.nix
    ../../modules/dev.nix
    ../../modules/media.nix
    ../../modules/apps.nix
  ];

  networking.hostName = "usins";

  # Use default kernel instead of nixos-hardware's patched Surface kernel
  # (Surface kernel patches are currently broken for 6.12.19)
  boot.kernelPackages = lib.mkForce pkgs.linuxPackages;

  # Broadcom BCM4331 WiFi
  hardware.firmware = [ pkgs.linux-firmware ];
  boot.kernelModules = [ "wl" ];
  boot.blacklistedKernelModules = [
    "b43"
    "bcma"
  ];

  # Swap — gives headroom for LLMs
  swapDevices = [
    {
      device = "/swapfile";
      size = 4096; # 4GB
    }
  ];

  # Ollama — CPU-only, i7 + 32GB RAM
  # Runs via Docker (see modules/ollama.nix)
  local.ollamaTier = "medium";

  system.stateVersion = "25.11";
}
