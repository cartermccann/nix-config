{ pkgs, ... }:

{
  stylix = {
    enable = true;
    polarity = "dark";
    image = ../wallpaper/fam.jpg;
    # Auseklis, the house palette (lib/palette.nix via lib/auseklis.nix), so GTK,
    # btop, bat, fzf, tmux, yazi, lazygit and the rest match the Hyprland
    # session instead of running catppuccin-macchiato beside it. Zen is covered
    # on perkons through home/zen.nix (Stylix needs an HM-managed profile). Not
    # covered: Qt (desktop-plasma.nix hands it to Plasma/Breeze).
    base16Scheme = (import ../lib/auseklis.nix).base16;

    fonts = {
      monospace = {
        package = pkgs.nerd-fonts.jetbrains-mono;
        name = "JetBrainsMono Nerd Font";
      };
      sansSerif = {
        package = pkgs.noto-fonts;
        name = "Noto Sans";
      };
      serif = {
        package = pkgs.noto-fonts;
        name = "Noto Serif";
      };
      emoji = {
        package = pkgs.noto-fonts-color-emoji;
        name = "Noto Color Emoji";
      };
    };

    cursor = {
      package = pkgs.bibata-cursors;
      name = "Bibata-Modern-Ice";
      size = 24;
    };
  };
}
