-- The house motion curve, cubic-bezier(.2,.8,.2,1), as a snacks easing
-- function (t elapsed, b start, c change, d duration). x(u) is solved for u by
-- Newton's method, then y(u) is the eased progress.
local function auseklis_ease(t, b, c, d)
  local x1, y1, x2, y2 = 0.2, 0.8, 0.2, 1
  local function bez(u, p1, p2) return 3 * (1 - u) ^ 2 * u * p1 + 3 * (1 - u) * u ^ 2 * p2 + u ^ 3 end
  local function dbez(u, p1, p2) return 3 * (1 - u) ^ 2 * p1 + 6 * (1 - u) * u * (p2 - p1) + 3 * u ^ 2 * (1 - p2) end
  local x, u = t / d, t / d
  for _ = 1, 6 do
    local dx = dbez(u, x1, x2)
    if math.abs(dx) < 1e-6 then break end
    u = math.min(1, math.max(0, u - (bez(u, x1, x2) - x) / dx))
  end
  return b + c * bez(u, y1, y2)
end

-- UI: snacks does the heavy lifting (picker, explorer, dashboard, notifier,
-- input, indent guides, smooth scroll). One filename display (the
-- statusline), one motion layer (snacks.scroll), one scope guide.
return {
  {
    "folke/snacks.nvim",
    priority = 1000,
    lazy = false,
    opts = {
      bigfile = { enabled = true },
      quickfile = { enabled = true },
      input = { enabled = true },
      notifier = { enabled = true, style = "compact", top_down = false },
      picker = { enabled = true, layout = { preset = "default" } },
      explorer = { enabled = true, replace_netrw = true },
      words = { enabled = true },
      scope = { enabled = true },
      statuscolumn = { enabled = true },
      -- one dim guide; the current scope a shade brighter (colors/palette.lua)
      indent = { enabled = true, animate = { enabled = false } },
      scroll = {
        enabled = true,
        animate = { duration = { step = 10, total = 160 }, easing = auseklis_ease },
      },
      dashboard = {
        enabled = true,
        preset = {
          header = [[
░█▀█░█▀▀░█▀█░█░█░▀█▀░█▄█
░█░█░█▀▀░█░█░▀▄▀░░█░░█░█
░▀░▀░▀▀▀░▀▀▀░░▀░░▀▀▀░▀░▀]],
          keys = {
            { icon = "󰈞 ", key = "f", desc = "Find file", action = ":lua Snacks.picker.files()" },
            { icon = "󰱼 ", key = "g", desc = "Grep", action = ":lua Snacks.picker.grep()" },
            { icon = "󰋚 ", key = "r", desc = "Recent", action = ":lua Snacks.picker.recent()" },
            { icon = "󰦛 ", key = "s", desc = "Restore session", section = "session" },
            { icon = "󰊢 ", key = "G", desc = "Lazygit", action = ":lua Snacks.lazygit()" },
            { icon = "󰒓 ", key = "c", desc = "Config", action = ":lua Snacks.picker.files({ cwd = vim.fn.stdpath('config') })" },
            { icon = "󰒲 ", key = "l", desc = "Lazy", action = ":Lazy" },
            { icon = "󰗼 ", key = "q", desc = "Quit", action = ":qa" },
          },
        },
        sections = {
          { section = "header" },
          { section = "keys", gap = 1, padding = 1 },
          { section = "startup" },
        },
      },
    },
  },

  {
    "folke/which-key.nvim",
    event = "VeryLazy",
    opts = {
      preset = "helix",
      win = { border = "single" },
      spec = {
        { "<leader>a", group = "agents" },
        { "<leader>b", group = "buffer" },
        { "<leader>c", group = "code" },
        { "<leader>f", group = "file" },
        { "<leader>g", group = "git" },
        { "<leader>gd", group = "diffview" },
        { "<leader>q", group = "quit/session" },
        { "<leader>s", group = "search" },
        { "<leader>u", group = "toggle" },
        { "<leader>x", group = "diagnostics" },
        { "[", group = "prev" },
        { "]", group = "next" },
        { "g", group = "goto" },
      },
    },
  },

  -- icons for everything that asks nvim-web-devicons
  {
    "echasnovski/mini.icons",
    opts = {},
    init = function()
      package.preload["nvim-web-devicons"] = function()
        require("mini.icons").mock_nvim_web_devicons()
        return package.loaded["nvim-web-devicons"]
      end
    end,
  },

  -- hex colours rendered inline (replaces nvim-colorizer)
  {
    "echasnovski/mini.hipatterns",
    event = { "BufReadPost", "BufNewFile" },
    opts = function()
      return { highlighters = { hex_color = require("mini.hipatterns").gen_highlighter.hex_color() } }
    end,
  },
}
