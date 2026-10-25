-- Language servers with nvim's built-in client. nvim-lspconfig only supplies
-- the lsp/<name>.lua defaults; the binaries come from home/neovim.nix.
-- A server is enabled only when its command is on PATH, so a missing one is
-- silent instead of an error on every buffer (e.g. before a rebuild).
local M = {}

local servers = {
  vtsls = {},
  tailwindcss = {},
  jsonls = {},
  cssls = {},
  html = {},
  eslint = {},
  pyright = {},
  ruff = {},
  gopls = { settings = { gopls = { gofumpt = true, usePlaceholders = true, staticcheck = true } } },
  nixd = {
    settings = {
      nixd = {
        formatting = { command = { "nixfmt" } },
        options = {
          nixos = { expr = '(builtins.getFlake "' .. vim.env.HOME .. '/nix-config").nixosConfigurations.perkons.options' },
          home_manager = {
            expr = '(builtins.getFlake "' .. vim.env.HOME .. '/nix-config").nixosConfigurations.perkons.options.home-manager.users.type.getSubOptions []',
          },
        },
      },
    },
  },
  lua_ls = { settings = { Lua = { workspace = { checkThirdParty = false }, hint = { enable = true } } } },
  elixirls = {},
  zls = {},
  clangd = {},
  jdtls = {},
  bashls = {},
  yamlls = {},
  taplo = {},
  marksman = {},
  -- rust_analyzer: rustaceanvim
}

-- lspconfig gives some servers a function as `cmd` (it picks flags at spawn
-- time), so there is no argv to read; name their binaries here.
local binaries = {
  tailwindcss = "tailwindcss-language-server",
  jsonls = "vscode-json-language-server",
  cssls = "vscode-css-language-server",
  html = "vscode-html-language-server",
  eslint = "vscode-eslint-language-server",
  jdtls = "jdtls",
  yamlls = "yaml-language-server",
}

local function available(name)
  local cmd = (vim.lsp.config[name] or {}).cmd
  local bin = type(cmd) == "table" and cmd[1] or binaries[name]
  return bin ~= nil and vim.fn.executable(bin) == 1
end

function M.setup()
  vim.diagnostic.config({
    severity_sort = true,
    underline = true,
    update_in_insert = false,
    virtual_text = { spacing = 2, source = "if_many", prefix = "●" },
    float = { border = "single", source = "if_many" },
    signs = {
      text = {
        [vim.diagnostic.severity.ERROR] = "✕",
        [vim.diagnostic.severity.WARN] = "▲",
        [vim.diagnostic.severity.INFO] = "●",
        [vim.diagnostic.severity.HINT] = "◆",
      },
    },
  })

  local ok, blink = pcall(require, "blink.cmp")
  vim.lsp.config("*", { capabilities = ok and blink.get_lsp_capabilities() or nil })

  local enable = {}
  for name, cfg in pairs(servers) do
    if next(cfg) then vim.lsp.config(name, cfg) end
    if available(name) then table.insert(enable, name) end
  end
  vim.lsp.enable(enable)

  vim.api.nvim_create_autocmd("LspAttach", {
    group = vim.api.nvim_create_augroup("auseklis_lsp", { clear = true }),
    callback = function(ev)
      local function map(lhs, rhs, desc, mode)
        vim.keymap.set(mode or "n", lhs, rhs, { buffer = ev.buf, desc = desc })
      end
      map("gd", function() Snacks.picker.lsp_definitions() end, "Goto definition")
      map("gr", function() Snacks.picker.lsp_references() end, "References")
      map("gI", function() Snacks.picker.lsp_implementations() end, "Goto implementation")
      map("gy", function() Snacks.picker.lsp_type_definitions() end, "Goto type definition")
      map("gD", vim.lsp.buf.declaration, "Goto declaration")
      map("<leader>ca", vim.lsp.buf.code_action, "Code action", { "n", "v" })
      map("<leader>cr", vim.lsp.buf.rename, "Rename")
      map("<leader>ss", function() Snacks.picker.lsp_symbols() end, "Symbols")
      map("<leader>sS", function() Snacks.picker.lsp_workspace_symbols() end, "Workspace symbols")
      map("<leader>cl", "<cmd>checkhealth vim.lsp<cr>", "LSP info")

      local client = vim.lsp.get_client_by_id(ev.data.client_id)
      if client and client:supports_method("textDocument/inlayHint") then
        vim.lsp.inlay_hint.enable(true, { bufnr = ev.buf })
      end
    end,
  })
end

return M
