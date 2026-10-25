{
  config,
  pkgs,
  user,
  ...
}:

{
  programs.neovim = {
    enable = true;
    viAlias = true;
    vimAlias = true;
    defaultEditor = true;
    # 26.05 flipped both defaults to false; no plugin here uses either provider.
    withRuby = false;
    withPython3 = false;
    # HM 26.05 writes the provider toggles to nvim/init.lua, which collides
    # with the out-of-store nvim/ symlink below; load them via the wrapper.
    sideloadInitLua = true;
    # Every language server and formatter comes from here; there is no Mason.
    # Mason's auto-enable is how a removed Copilot kept attaching with
    # telemetry on, and its prebuilt binaries break on NixOS (marksman/ICU).
    # config/nvim/lua/lsp.lua enables only the servers whose binary exists.
    extraPackages = with pkgs; [
      # language servers
      vtsls # TS/JS
      tailwindcss-language-server
      vscode-langservers-extracted # json, css, html, eslint
      pyright
      ruff
      gopls
      rust-analyzer
      nixd
      lua-language-server
      elixir-ls
      zls
      clang-tools
      jdt-language-server
      bash-language-server
      yaml-language-server
      taplo # toml
      marksman
      # formatters (conform.nvim)
      stylua
      prettierd
      shfmt
      gofumpt
      nixfmt
    ];
  };

  # The Auseklis base16 slots for colors/palette.lua, generated from
  # lib/palette.nix so the editor can't drift from the desktop. Lives outside
  # nvim/ because that directory is the out-of-store symlink below.
  xdg.configFile."auseklis/palette.lua".text = (import ../lib/auseklis.nix).luaPalette;

  # Live-editable nvim config via symlink into this repo
  xdg.configFile."nvim".source =
    config.lib.file.mkOutOfStoreSymlink "/home/${user}/nix-config/config/nvim";
}
