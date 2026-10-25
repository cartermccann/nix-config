-- AI, one tool per layer:
--   inline ghost text  minuet-ai.nvim → local Ollama FIM (qwen2.5-coder:3b-base,
--                      ~1.9 GB on the 5070), <Tab> accepts it
--   agents             Claude Code and Codex in herdr panes. claudecode.nvim is
--                      Claude's IDE bridge (/ide); lua/ai/herdr.lua sends code
--                      to either agent's pane in the same project.
-- No Copilot: local FIM is free, private and instant, and the subscriptions
-- (Claude, Codex) are the agent layer, where 1-5s latency is fine.
return {
  {
    "milanglacier/minuet-ai.nvim",
    event = "InsertEnter",
    opts = {
      provider = "openai_fim_compatible",
      n_completions = 1,
      context_window = 1024,
      throttle = 400,
      debounce = 200,
      request_timeout = 3,
      notify = "warn",
      provider_options = {
        openai_fim_compatible = {
          -- `api_key` names an env var that must merely exist; TERM always does.
          api_key = "TERM",
          name = "Ollama",
          end_point = "http://localhost:11434/v1/completions",
          model = "qwen2.5-coder:3b-base",
          optional = { max_tokens = 256, top_p = 0.9 },
        },
      },
      virtualtext = {
        auto_trigger_ft = { "*" },
        auto_trigger_ignore_ft = { "snacks_dashboard", "snacks_picker_input", "lazy", "help", "checkhealth" },
        keymap = {
          accept = "<A-A>",
          accept_line = "<A-a>",
          accept_n_lines = "<A-z>",
          prev = "<A-[>",
          next = "<A-]>",
          dismiss = "<A-e>",
        },
      },
    },
  },

  -- <Tab>: accept ghost text → jump snippet → literal tab
  {
    "saghen/blink.cmp",
    opts = {
      keymap = {
        ["<Tab>"] = {
          function()
            local ok, vt = pcall(require, "minuet.virtualtext")
            if ok and vt.action.is_visible() then
              vt.action.accept()
              return true
            end
          end,
          "snippet_forward",
          "fallback",
        },
        ["<S-Tab>"] = { "snippet_backward", "fallback" },
      },
    },
  },

  -- Loaded at startup, not on first keypress, so Claude Code's /ide finds this
  -- nvim as soon as it opens.
  {
    "coder/claudecode.nvim",
    event = "VeryLazy",
    dependencies = { "folke/snacks.nvim" },
    opts = { terminal = { provider = "none" } },
    keys = {
      { "<leader>ac", "<cmd>ClaudeCodeSend<cr>", mode = "v", desc = "Claude: send selection" },
      { "<leader>ab", "<cmd>ClaudeCodeAdd %<cr>", desc = "Claude: add buffer" },
      { "<leader>aa", "<cmd>ClaudeCodeDiffAccept<cr>", desc = "Claude: accept diff" },
      { "<leader>ad", "<cmd>ClaudeCodeDiffDeny<cr>", desc = "Claude: deny diff" },
      { "<leader>ax", function() require("ai.herdr").send("codex") end, mode = { "n", "v" }, desc = "Codex: send code ref" },
      { "<leader>aX", function() require("ai.herdr").send("claude") end, mode = { "n", "v" }, desc = "Claude pane: send code ref" },
      { "<leader>af", function() require("ai.herdr").send_file("codex") end, desc = "Codex: send file" },
      { "<leader>aF", function() require("ai.herdr").send_file("claude") end, desc = "Claude pane: send file" },
      { "<leader>ae", function() require("ai.herdr").diagnostics("codex") end, desc = "Codex: fix these diagnostics" },
      { "<leader>aE", function() require("ai.herdr").diagnostics("claude") end, desc = "Claude pane: fix these diagnostics" },
      { "<leader>at", function()
        if vim.fn.executable("auseklis-herdr") == 0 then return vim.notify("auseklis-herdr not on PATH", vim.log.levels.WARN) end
        vim.system({ "auseklis-herdr" })
      end, desc = "Open herdr" },
    },
  },
}
