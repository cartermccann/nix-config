vim.g.mapleader = " "
vim.g.maplocalleader = "\\"
vim.g.markdown_recommended_style = 0

local o = vim.opt

-- editing
o.expandtab = true
o.shiftwidth = 2
o.tabstop = 2
o.shiftround = true
o.smartindent = true
o.virtualedit = "block"
o.confirm = true
o.undofile = true
o.undolevels = 10000
o.clipboard = vim.env.SSH_TTY and "" or "unnamedplus"
o.mouse = "a"
o.formatoptions = "jcroqlnt"
o.completeopt = "menu,menuone,noselect"

-- search
o.ignorecase = true
o.smartcase = true
o.inccommand = "nosplit"
o.grepprg = "rg --vimgrep"
o.grepformat = "%f:%l:%c:%m"

-- layout
o.number = true
o.relativenumber = true
o.cursorline = true
o.signcolumn = "yes:1"
o.splitbelow = true
o.splitright = true
o.splitkeep = "screen"
o.scrolloff = 6
o.sidescrolloff = 8
o.wrap = false
o.linebreak = true
o.smoothscroll = true
o.jumpoptions = "view"
o.winminwidth = 5
o.pumheight = 10

-- folds: treesitter where there's a parser, open by default
o.foldlevel = 99
o.foldmethod = "expr"
o.foldexpr = "v:lua.vim.treesitter.foldexpr()"
o.foldtext = ""

-- Auseklis chrome: one global statusline, no tabline, hairline borders on
-- every float (0.12-native winborder), cobalt kept for the few things that
-- carry focus (see colors/palette.lua).
o.termguicolors = true
o.laststatus = 3
o.showtabline = 0
o.showmode = false
o.ruler = false
o.winborder = "single"
o.list = true
o.listchars = { tab = "  ", trail = "·", nbsp = "␣" }
o.fillchars = { eob = " ", fold = " ", foldopen = "▾", foldclose = "▸", foldsep = " ", diff = "╱", vert = "│" }
o.shortmess:append({ W = true, I = true, c = true, C = true })

-- timing
o.updatetime = 200
o.timeoutlen = 300
