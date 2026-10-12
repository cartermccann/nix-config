{
  config,
  lib,
  pkgs,
  user,
  ...
}:

{
  imports = [
    ./hardware-configuration.nix
    ../../modules/common.nix
    ../../modules/desktop-wayland.nix
    ../../modules/desktop-niri-noctalia.nix
    ../../modules/desktop-hyprland.nix
    ../../modules/desktop-plasma.nix
    ../../modules/nvidia.nix
    ../../modules/ollama.nix
    ../../modules/opencode-companion.nix
    ../../modules/llama-heavy.nix
    ../../modules/audio.nix
    ../../modules/bluetooth.nix
    ../../modules/printing.nix
    ../../modules/networking.nix
    ../../modules/dictation.nix
    ../../modules/dev.nix
    ../../modules/media.nix
    ../../modules/apps.nix
    ../../modules/oom-protection.nix
  ];

  # Zen through its home-manager module so Stylix themes it; the module pins
  # this host's existing profile path, so it stays a kronos choice.
  home-manager.users.${user}.imports = [ ../../home/zen.nix ];

  networking.hostName = "kronos";

  # Larger console font for the high-resolution display and fallback TTYs.
  console = {
    font = "ter-v24n";
    packages = [ pkgs.terminus_font ];
    earlySetup = true;
  };

  # The monitors are external, so brightnessctl has nothing to drive: drop
  # Ly's brightness keys rather than show dead hints. atlas keeps them.
  services.displayManager.ly.settings = {
    brightness_down_key = "null";
    brightness_up_key = "null";
  };

  # Ollama — high tier: RTX 5070 (12GB VRAM) + 64GB RAM (see modules/ollama.nix)
  local.ollamaTier = "high";

  system.stateVersion = "25.11";
}
