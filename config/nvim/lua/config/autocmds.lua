local au = vim.api.nvim_create_autocmd
local group = vim.api.nvim_create_augroup("auseklis", { clear = true })

-- flash the yanked text
au("TextYankPost", {
  group = group,
  callback = function() vim.hl.on_yank({ timeout = 150 }) end,
})

-- pick up files changed outside nvim (agents edit them constantly)
au({ "FocusGained", "TermClose", "TermLeave" }, {
  group = group,
  callback = function()
    if vim.o.buftype ~= "nofile" then vim.cmd("checktime") end
  end,
})

-- keep splits even when the terminal resizes
au("VimResized", { group = group, command = "tabdo wincmd =" })

-- reopen a file where it was left
au("BufReadPost", {
  group = group,
  callback = function(ev)
    local mark = vim.api.nvim_buf_get_mark(ev.buf, '"')
    if mark[1] > 0 and mark[1] <= vim.api.nvim_buf_line_count(ev.buf) then
      pcall(vim.api.nvim_win_set_cursor, 0, mark)
    end
  end,
})

-- q closes the throwaway windows
au("FileType", {
  group = group,
  pattern = { "help", "qf", "man", "checkhealth", "grug-far", "gitsigns-blame" },
  callback = function(ev)
    vim.bo[ev.buf].buflisted = false
    vim.keymap.set("n", "q", "<cmd>close<cr>", { buffer = ev.buf, silent = true })
  end,
})

-- prose wraps
au("FileType", {
  group = group,
  pattern = { "markdown", "gitcommit", "text" },
  callback = function()
    vim.opt_local.wrap = true
    vim.opt_local.spell = true
  end,
})

-- create missing parent directories on save
au("BufWritePre", {
  group = group,
  callback = function(ev)
    if ev.match:match("^%w%w+:[\\/][\\/]") then return end
    vim.fn.mkdir(vim.fn.fnamemodify(vim.uv.fs_realpath(ev.match) or ev.match, ":p:h"), "p")
  end,
})
