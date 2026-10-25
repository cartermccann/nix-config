-- nvim-treesitter's main branch: it only installs parsers, nvim does the rest.
-- Parsers compile with the gcc + tree-sitter CLI that are on PATH globally.
local parsers = {
  "bash", "c", "css", "diff", "dockerfile", "elixir", "heex", "git_rebase", "gitcommit",
  "go", "gomod", "gosum", "html", "java", "javascript", "jsdoc", "json", "lua", "luadoc",
  "markdown", "markdown_inline", "nix", "python", "query", "regex", "rust", "sql", "toml",
  "tsx", "typescript", "vim", "vimdoc", "yaml", "zig",
}

return {
  {
    "nvim-treesitter/nvim-treesitter",
    branch = "main",
    lazy = false, -- the main branch doesn't support lazy-loading
    build = ":TSUpdate",
    config = function()
      local ts = require("nvim-treesitter")
      local have = ts.get_installed()
      local missing = vim.tbl_filter(function(p) return not vim.tbl_contains(have, p) end, parsers)
      if #missing > 0 then ts.install(missing, { summary = true }) end

      vim.api.nvim_create_autocmd("FileType", {
        group = vim.api.nvim_create_augroup("auseklis_treesitter", { clear = true }),
        callback = function(ev)
          if pcall(vim.treesitter.start, ev.buf) then
            vim.bo[ev.buf].indentexpr = "v:lua.require'nvim-treesitter'.indentexpr()"
          end
        end,
      })
    end,
  },

  {
    "nvim-treesitter/nvim-treesitter-textobjects",
    branch = "main",
    event = "VeryLazy",
    config = function()
      require("nvim-treesitter-textobjects").setup({ move = { set_jumps = true } })
      local move = require("nvim-treesitter-textobjects.move")
      local objects = { f = "@function.outer", c = "@class.outer", a = "@parameter.inner" }
      for key, query in pairs(objects) do
        local up = key:upper()
        local function map(lhs, fn, desc)
          vim.keymap.set({ "n", "x", "o" }, lhs, function()
            -- in diff mode ]c/[c stay vim's next/prev change (diffview, :diffsplit)
            if vim.wo.diff and key == "c" then return vim.cmd.normal({ vim.v.count1 .. lhs, bang = true }) end
            move[fn](query, "textobjects")
          end, { desc = desc })
        end
        map("]" .. key, "goto_next_start", "Next " .. query)
        map("[" .. key, "goto_previous_start", "Prev " .. query)
        map("]" .. up, "goto_next_end", "Next " .. query .. " end")
        map("[" .. up, "goto_previous_end", "Prev " .. query .. " end")
      end
    end,
  },

  { "windwp/nvim-ts-autotag", event = { "BufReadPre", "BufNewFile" }, opts = {} },

  -- the enclosing function/class pinned at the top while scrolling
  {
    "nvim-treesitter/nvim-treesitter-context",
    event = { "BufReadPost", "BufNewFile" },
    opts = { mode = "cursor", max_lines = 3 },
    keys = {
      { "<leader>ut", function() require("treesitter-context").toggle() end, desc = "Toggle treesitter context" },
    },
  },
}
