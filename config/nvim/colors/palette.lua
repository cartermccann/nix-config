-- Palette — neovim colorscheme derived from lib/palette.nix
-- Auseklis night: cobalt on near-black. Mirrors the Hyprland session so the
-- editor matches the desktop.
--
-- Palette source of truth: ~/nix-config/lib/palette.nix (Base16). The 16 slots
-- are read from ~/.config/auseklis/palette.lua, which home/neovim.nix generates
-- from it, so a palette change reaches the editor on the next rebuild. The
-- derived tints below (string-green, bg washes, selection, indent guides) are
-- design calls tuned for reading a full file, so they stay hand-written here.
--
-- Transparency: defaults ON so the ground comes from the terminal/compositor
-- blur (ghostty is the glass pane). Set
-- `vim.g.palette_transparent = false` before `:colorscheme palette` for a solid bg.

vim.cmd("hi clear")
if vim.fn.exists("syntax_on") then
  vim.cmd("syntax reset")
end
vim.o.background = "dark"
vim.g.colors_name = "palette"

local transparent = vim.g.palette_transparent ~= false

local b = dofile(vim.fn.expand("~/.config/auseklis/palette.lua"))

local c = {
  -- ── Base16 slots — Auseklis night (generated from lib/palette.nix) ──
  bg = b.base00, -- near-black ground
  surface = b.base01, -- panels / status
  raised = b.base02, -- selection / raised
  muted = b.base03, -- comments / disabled
  taupe = b.base04, -- dark fg (subtext)
  fg = b.base05, -- default fg
  fg_light = b.base06, -- light fg
  white = b.base07, -- lightest
  red = b.base08,
  orange = b.base09,
  yellow = b.base0A,
  green = b.base0B,
  cyan = b.base0C,
  azure = b.base0D, -- PRIMARY accent — cobalt (focus/active)
  periwinkle = b.base0E, -- magenta
  green_deep = b.base0F, -- deep cobalt
  dim = b.base04, -- textDim

  -- ── Derived tints (kept restrained for full-file readability) ──
  -- Comments do NOT use base03. Base16 nominates base03 for "comments /
  -- disabled", but the Auseklis ramp puts it three steps off base00 and it
  -- measures 1.92:1 against the ground — under even the 3:1 UI floor, and a
  -- comment-heavy file (the nix modules in this repo) reads as empty space.
  -- This sits at 4.54:1, i.e. WCAG AA for small text, while staying far below
  -- the 16.36:1 of base05 body text so comments still recede. base03 keeps its
  -- other jobs (fold/sign column, ghost text, ANSI 8) where faint is correct.
  comment = "#6f7a92", -- 4.54:1 on base00 — readable, still recessive
  overlay = "#212734", -- cursorline / subtle raise above raised (SURFACE2)
  green_soft = "#6ee7b7", -- gentler green for strings
  sel = "#1c2740", -- visual: faint cobalt-tinted selection
  hairline = "#242b38", -- inactive separators / borders
  border_active = "#2a4bbd", -- active win separator (cobalt-leaning)
  red_bg = "#2a1618",
  green_bg = "#0e2620",
  blue_bg = "#111a33",
  yellow_bg = "#2a2210",
  none = "NONE",
}

-- background helpers honoring transparency
local NB = transparent and c.none or c.bg -- normal bg
local FB = c.surface -- float bg (always solid for separation)

local hl = function(group, opts)
  vim.api.nvim_set_hl(0, group, opts)
end

-- ── Editor ──
hl("Normal", { fg = c.fg, bg = NB })
hl("NormalNC", { fg = c.fg, bg = NB })
hl("NormalFloat", { fg = c.fg, bg = FB })
hl("FloatBorder", { fg = c.hairline, bg = FB })
hl("FloatTitle", { fg = c.azure, bg = FB, bold = true })
hl("Visual", { bg = c.sel })
hl("VisualNOS", { bg = c.sel })
hl("Search", { fg = c.white, bg = "#463813" })
hl("IncSearch", { fg = c.bg, bg = c.azure })
hl("CurSearch", { fg = c.bg, bg = c.azure })
hl("Substitute", { fg = c.bg, bg = c.orange })
hl("CursorLine", { bg = c.overlay })
hl("CursorColumn", { bg = c.overlay })
hl("ColorColumn", { bg = c.surface })
hl("LineNr", { fg = "#39414f" })
hl("CursorLineNr", { fg = c.azure, bold = true })
hl("SignColumn", { fg = c.muted, bg = NB })
hl("FoldColumn", { fg = c.muted, bg = NB })
hl("Folded", { fg = c.taupe, bg = c.surface })
hl("VertSplit", { fg = c.hairline })
hl("WinSeparator", { fg = c.border_active })
hl("StatusLine", { fg = c.fg, bg = c.surface })
hl("StatusLineNC", { fg = c.taupe, bg = c.surface })

-- ── Statusline (lua/config/statusline.lua): one bar, cobalt only on the
-- normal-mode dot; other modes and git/diagnostic counts use the accents. ──
hl("AuseklisSLText", { fg = c.fg, bg = c.surface })
hl("AuseklisSLDim", { fg = c.taupe, bg = c.surface })
hl("AuseklisSLNormal", { fg = c.azure, bg = c.surface, bold = true })
hl("AuseklisSLInsert", { fg = c.green, bg = c.surface, bold = true })
hl("AuseklisSLVisual", { fg = c.periwinkle, bg = c.surface, bold = true })
hl("AuseklisSLCommand", { fg = c.yellow, bg = c.surface, bold = true })
hl("AuseklisSLReplace", { fg = c.red, bg = c.surface, bold = true })
hl("AuseklisSLAdd", { fg = c.green, bg = c.surface })
hl("AuseklisSLChange", { fg = c.yellow, bg = c.surface })
hl("AuseklisSLWarn", { fg = c.yellow, bg = c.surface })
hl("AuseklisSLErr", { fg = c.red, bg = c.surface })
hl("TabLine", { fg = c.taupe, bg = c.surface })
hl("TabLineSel", { fg = c.azure, bg = NB, bold = true })
hl("TabLineFill", { bg = NB })
hl("WinBar", { fg = c.fg, bg = NB })
hl("WinBarNC", { fg = c.taupe, bg = NB })
hl("Pmenu", { fg = c.fg, bg = FB })
hl("PmenuSel", { fg = c.white, bg = c.sel, bold = true })
hl("PmenuSbar", { bg = c.raised })
hl("PmenuThumb", { bg = c.taupe })
hl("WildMenu", { fg = c.bg, bg = c.azure })
hl("Directory", { fg = c.azure })
hl("Title", { fg = c.azure, bold = true })
hl("MatchParen", { fg = c.azure, bg = c.raised, bold = true })
hl("NonText", { fg = "#2f3648" })
hl("SpecialKey", { fg = "#2f3648" })
hl("Whitespace", { fg = "#232a37" })
hl("EndOfBuffer", { fg = NB == c.none and c.bg or c.bg })
hl("Conceal", { fg = c.muted })
hl("QuickFixLine", { bg = c.sel })
hl("MsgArea", { fg = c.fg })
hl("ModeMsg", { fg = c.azure, bold = true })
hl("MoreMsg", { fg = c.green })
hl("Question", { fg = c.green })
hl("WarningMsg", { fg = c.yellow })
hl("ErrorMsg", { fg = c.red, bold = true })

-- ── Cursor ──
hl("Cursor", { fg = c.bg, bg = c.azure })
hl("lCursor", { fg = c.bg, bg = c.azure })
hl("CursorIM", { fg = c.bg, bg = c.azure })
hl("TermCursor", { fg = c.bg, bg = c.azure })
hl("TermCursorNC", { fg = c.bg, bg = c.taupe })

-- ── Diff ──
hl("DiffAdd", { bg = c.green_bg })
hl("DiffChange", { bg = c.blue_bg })
hl("DiffDelete", { fg = c.red, bg = c.red_bg })
hl("DiffText", { bg = "#22305a" })

-- ── Spell ──
hl("SpellBad", { undercurl = true, sp = c.red })
hl("SpellCap", { undercurl = true, sp = c.yellow })
hl("SpellRare", { undercurl = true, sp = c.periwinkle })
hl("SpellLocal", { undercurl = true, sp = c.cyan })

-- ── Legacy syntax ──
-- Palette logic: azure = keywords/flow, periwinkle = functions, yellow = types,
-- green = strings, orange = numbers/constants, cyan = props/builtins,
-- cream = plain identifiers, muted = punctuation/comments.
hl("Comment", { fg = c.comment, italic = true })
hl("Constant", { fg = c.orange })
hl("String", { fg = c.green_soft })
hl("Character", { fg = c.green_soft })
hl("Number", { fg = c.orange })
hl("Boolean", { fg = c.orange })
hl("Float", { fg = c.orange })
hl("Identifier", { fg = c.fg })
hl("Function", { fg = c.periwinkle })
hl("Statement", { fg = c.azure })
hl("Conditional", { fg = c.azure })
hl("Repeat", { fg = c.azure })
hl("Label", { fg = c.cyan })
hl("Operator", { fg = c.taupe })
hl("Keyword", { fg = c.azure })
hl("Exception", { fg = c.azure })
hl("PreProc", { fg = c.periwinkle })
hl("Include", { fg = c.azure })
hl("Define", { fg = c.periwinkle })
hl("Macro", { fg = c.periwinkle })
hl("PreCondit", { fg = c.periwinkle })
hl("Type", { fg = c.yellow })
hl("StorageClass", { fg = c.yellow })
hl("Structure", { fg = c.yellow })
hl("Typedef", { fg = c.yellow })
hl("Special", { fg = c.cyan })
hl("SpecialChar", { fg = c.cyan })
hl("Tag", { fg = c.azure })
hl("Delimiter", { fg = c.taupe })
hl("SpecialComment", { fg = c.taupe, italic = true })
hl("Debug", { fg = c.orange })
hl("Underlined", { fg = c.cyan, underline = true })
hl("Ignore", { fg = c.muted })
hl("Error", { fg = c.red, bold = true })
hl("Todo", { fg = c.bg, bg = c.yellow, bold = true })

-- ── Treesitter ──
hl("@comment", { link = "Comment" })
hl("@comment.error", { fg = c.bg, bg = c.red, bold = true })
hl("@comment.warning", { fg = c.bg, bg = c.yellow, bold = true })
hl("@comment.todo", { link = "Todo" })
hl("@comment.note", { fg = c.bg, bg = c.cyan, bold = true })
hl("@constant", { fg = c.orange })
hl("@constant.builtin", { fg = c.cyan })
hl("@constant.macro", { fg = c.periwinkle })
hl("@string", { fg = c.green_soft })
hl("@string.escape", { fg = c.cyan })
hl("@string.regexp", { fg = c.orange })
hl("@string.special", { fg = c.cyan })
hl("@string.special.url", { fg = c.cyan, underline = true })
hl("@character", { fg = c.green_soft })
hl("@character.special", { fg = c.cyan })
hl("@number", { fg = c.orange })
hl("@number.float", { fg = c.orange })
hl("@boolean", { fg = c.orange })
hl("@function", { fg = c.periwinkle })
hl("@function.builtin", { fg = c.cyan })
hl("@function.call", { fg = c.periwinkle })
hl("@function.macro", { fg = c.periwinkle })
hl("@function.method", { fg = c.periwinkle })
hl("@function.method.call", { fg = c.periwinkle })
hl("@method", { fg = c.periwinkle })
hl("@method.call", { fg = c.periwinkle })
hl("@constructor", { fg = c.yellow })
hl("@parameter", { fg = c.fg_light, italic = true })
hl("@keyword", { fg = c.azure })
hl("@keyword.function", { fg = c.azure })
hl("@keyword.operator", { fg = c.azure })
hl("@keyword.return", { fg = c.azure, italic = true })
hl("@keyword.import", { fg = c.azure, italic = true })
hl("@keyword.export", { fg = c.azure, italic = true })
hl("@keyword.conditional", { fg = c.azure })
hl("@keyword.repeat", { fg = c.azure })
hl("@keyword.exception", { fg = c.azure })
hl("@keyword.coroutine", { fg = c.azure })
hl("@conditional", { fg = c.azure })
hl("@repeat", { fg = c.azure })
hl("@exception", { fg = c.azure })
hl("@label", { fg = c.cyan })
hl("@operator", { fg = c.taupe })
hl("@type", { fg = c.yellow })
hl("@type.builtin", { fg = c.yellow, italic = true })
hl("@type.definition", { fg = c.yellow })
hl("@type.qualifier", { fg = c.azure })
hl("@namespace", { fg = c.fg_light })
hl("@module", { fg = c.fg_light })
hl("@include", { fg = c.azure })
hl("@variable", { fg = c.fg })
hl("@variable.builtin", { fg = c.red, italic = true })
hl("@variable.parameter", { fg = c.fg_light, italic = true })
hl("@variable.member", { fg = c.cyan })
hl("@property", { fg = c.cyan })
hl("@field", { fg = c.cyan })
hl("@attribute", { fg = c.periwinkle })
hl("@tag", { fg = c.azure })
hl("@tag.builtin", { fg = c.azure })
hl("@tag.attribute", { fg = c.periwinkle, italic = true })
hl("@tag.delimiter", { fg = c.muted })
hl("@punctuation.bracket", { fg = c.taupe })
hl("@punctuation.delimiter", { fg = c.taupe })
hl("@punctuation.special", { fg = c.cyan })
hl("@text", { fg = c.fg })
hl("@markup", { fg = c.fg })
hl("@markup.strong", { fg = c.orange, bold = true })
hl("@markup.italic", { italic = true })
hl("@markup.underline", { underline = true })
hl("@markup.strikethrough", { strikethrough = true })
hl("@markup.heading", { fg = c.azure, bold = true })
hl("@markup.raw", { fg = c.green_soft })
hl("@markup.link", { fg = c.cyan, underline = true })
hl("@markup.link.url", { fg = c.cyan, underline = true })
hl("@markup.link.label", { fg = c.periwinkle })
hl("@markup.list", { fg = c.azure })
hl("@markup.quote", { fg = c.taupe, italic = true })
hl("@diff.plus", { fg = c.green })
hl("@diff.minus", { fg = c.red })
hl("@diff.delta", { fg = c.cyan })

-- ── LSP semantic tokens ──
hl("@lsp.type.namespace", { link = "@namespace" })
hl("@lsp.type.type", { link = "@type" })
hl("@lsp.type.class", { link = "@type" })
hl("@lsp.type.enum", { link = "@type" })
hl("@lsp.type.interface", { link = "@type" })
hl("@lsp.type.struct", { link = "@type" })
hl("@lsp.type.typeParameter", { fg = c.yellow, italic = true })
hl("@lsp.type.parameter", { link = "@variable.parameter" })
hl("@lsp.type.variable", { link = "@variable" })
hl("@lsp.type.property", { link = "@property" })
hl("@lsp.type.enumMember", { fg = c.cyan })
hl("@lsp.type.function", { link = "@function" })
hl("@lsp.type.method", { link = "@function.method" })
hl("@lsp.type.macro", { fg = c.periwinkle })
hl("@lsp.type.decorator", { fg = c.periwinkle })
hl("@lsp.type.keyword", { link = "@keyword" })
hl("@lsp.typemod.variable.readonly", { fg = c.orange })
hl("@lsp.typemod.variable.defaultLibrary", { fg = c.cyan })
hl("@lsp.typemod.function.defaultLibrary", { fg = c.cyan })
hl("@lsp.mod.deprecated", { strikethrough = true })

-- ── Diagnostics ──
hl("DiagnosticError", { fg = c.red })
hl("DiagnosticWarn", { fg = c.yellow })
hl("DiagnosticInfo", { fg = c.cyan })
hl("DiagnosticHint", { fg = c.periwinkle })
hl("DiagnosticOk", { fg = c.green })
-- Unused imports / dead bindings. Stated rather than inherited: neovim's
-- default links DiagnosticUnnecessary to Comment, so unused code took whatever
-- comments happened to be — which was base03, and invisible. base04 at 6.34:1
-- reads as code that has been demoted, and the missing italic keeps it from
-- looking like prose. Declaring it also decouples it from future Comment
-- tuning, which is how it went wrong in the first place.
hl("DiagnosticUnnecessary", { fg = c.taupe })
hl("DiagnosticUnderlineError", { undercurl = true, sp = c.red })
hl("DiagnosticUnderlineWarn", { undercurl = true, sp = c.yellow })
hl("DiagnosticUnderlineInfo", { undercurl = true, sp = c.cyan })
hl("DiagnosticUnderlineHint", { undercurl = true, sp = c.periwinkle })
hl("DiagnosticVirtualTextError", { fg = c.red, bg = c.red_bg })
hl("DiagnosticVirtualTextWarn", { fg = c.yellow, bg = c.yellow_bg })
hl("DiagnosticVirtualTextInfo", { fg = c.cyan, bg = c.blue_bg })
hl("DiagnosticVirtualTextHint", { fg = c.periwinkle, bg = c.surface })

-- ── LSP ──
hl("LspReferenceText", { bg = c.raised })
hl("LspReferenceRead", { bg = c.raised })
hl("LspReferenceWrite", { bg = c.raised, underline = true })
hl("LspSignatureActiveParameter", { fg = c.azure, bold = true })
hl("LspInlayHint", { fg = c.muted, bg = c.surface, italic = true })
hl("LspCodeLens", { fg = c.muted, italic = true })

-- ── AI ghost text (minuet virtualtext / inline completion) ──
hl("MinuetVirtualText", { fg = c.muted, italic = true })
hl("ComplHint", { fg = c.muted, italic = true })
hl("ComplHintMore", { fg = c.muted, italic = true })

-- ── Git signs ──
hl("GitSignsAdd", { fg = c.green })
hl("GitSignsChange", { fg = c.azure })
hl("GitSignsDelete", { fg = c.red })
hl("GitSignsAddNr", { fg = c.green })
hl("GitSignsChangeNr", { fg = c.azure })
hl("GitSignsDeleteNr", { fg = c.red })
hl("GitSignsAddInline", { bg = c.green_bg })
hl("GitSignsDeleteInline", { bg = c.red_bg })
hl("Added", { fg = c.green })
hl("Changed", { fg = c.azure })
hl("Removed", { fg = c.red })

-- ── Which-key ──
hl("WhichKey", { fg = c.azure })
hl("WhichKeyGroup", { fg = c.periwinkle })
hl("WhichKeyDesc", { fg = c.fg })
hl("WhichKeySeparator", { fg = c.muted })
hl("WhichKeyFloat", { bg = FB })
hl("WhichKeyBorder", { fg = c.hairline, bg = FB })
hl("WhichKeyValue", { fg = c.taupe })

-- ── Treesitter context ──
hl("TreesitterContext", { bg = c.surface })
hl("TreesitterContextLineNumber", { fg = c.taupe, bg = c.surface })
hl("TreesitterContextBottom", { sp = c.hairline, underline = true })

-- ── Snacks ──
hl("SnacksDashboardHeader", { fg = c.azure })
hl("SnacksDashboardIcon", { fg = c.periwinkle })
hl("SnacksDashboardKey", { fg = c.azure, bold = true })
hl("SnacksDashboardDesc", { fg = c.fg })
hl("SnacksDashboardFooter", { fg = c.muted })
hl("SnacksDashboardSpecial", { fg = c.green })
hl("SnacksNotifierInfo", { fg = c.cyan })
hl("SnacksNotifierWarn", { fg = c.yellow })
hl("SnacksNotifierError", { fg = c.red })
hl("SnacksNotifierDebug", { fg = c.muted })
hl("SnacksIndent", { fg = "#1b212c" })
hl("SnacksIndentScope", { fg = c.hairline })

-- ── Blink cmp ──
hl("BlinkCmpMenu", { fg = c.fg, bg = FB })
hl("BlinkCmpMenuBorder", { fg = c.hairline, bg = FB })
hl("BlinkCmpMenuSelection", { bg = c.sel })
hl("BlinkCmpLabel", { fg = c.fg })
hl("BlinkCmpLabelMatch", { fg = c.azure, bold = true })
hl("BlinkCmpLabelDeprecated", { fg = c.muted, strikethrough = true })
hl("BlinkCmpKind", { fg = c.cyan })
hl("BlinkCmpKindFunction", { fg = c.periwinkle })
hl("BlinkCmpKindMethod", { fg = c.periwinkle })
hl("BlinkCmpKindKeyword", { fg = c.azure })
hl("BlinkCmpKindClass", { fg = c.yellow })
hl("BlinkCmpKindVariable", { fg = c.fg })
hl("BlinkCmpKindSnippet", { fg = c.green })
hl("BlinkCmpDoc", { fg = c.fg, bg = FB })
hl("BlinkCmpDocBorder", { fg = c.hairline, bg = FB })
hl("BlinkCmpGhostText", { fg = c.muted, italic = true })

-- ── Flash ──
hl("FlashLabel", { fg = c.bg, bg = c.azure, bold = true })
hl("FlashMatch", { fg = c.fg, bg = c.raised })
hl("FlashCurrent", { fg = c.bg, bg = c.cyan })

-- ── Mini ──
hl("MiniIconsAzure", { fg = c.azure })
hl("MiniIconsBlue", { fg = c.azure })
hl("MiniIconsCyan", { fg = c.cyan })
hl("MiniIconsGreen", { fg = c.green })
hl("MiniIconsGrey", { fg = c.taupe })
hl("MiniIconsOrange", { fg = c.orange })
hl("MiniIconsPurple", { fg = c.periwinkle })
hl("MiniIconsRed", { fg = c.red })
hl("MiniIconsYellow", { fg = c.yellow })

-- ── Lazy ──
hl("LazyButton", { fg = c.fg, bg = c.surface })
hl("LazyButtonActive", { fg = c.bg, bg = c.azure, bold = true })
hl("LazyH1", { fg = c.bg, bg = c.azure, bold = true })
hl("LazySpecial", { fg = c.azure })
hl("LazyProgressDone", { fg = c.green })
hl("LazyProgressTodo", { fg = c.raised })

-- ── Diffview ──
hl("DiffviewFilePanelTitle", { fg = c.azure, bold = true })
hl("DiffviewFilePanelCounter", { fg = c.periwinkle })
hl("DiffviewFilePanelFileName", { fg = c.fg })
hl("DiffviewNormal", { link = "Normal" })

-- ── Terminal ANSI palette ──
vim.g.terminal_color_0 = c.bg
vim.g.terminal_color_1 = c.red
vim.g.terminal_color_2 = c.green
vim.g.terminal_color_3 = c.yellow
vim.g.terminal_color_4 = c.azure
vim.g.terminal_color_5 = c.periwinkle
vim.g.terminal_color_6 = c.cyan
vim.g.terminal_color_7 = c.fg
vim.g.terminal_color_8 = c.muted
vim.g.terminal_color_9 = c.red
vim.g.terminal_color_10 = c.green
vim.g.terminal_color_11 = c.yellow
vim.g.terminal_color_12 = c.azure
vim.g.terminal_color_13 = c.periwinkle
vim.g.terminal_color_14 = c.cyan
vim.g.terminal_color_15 = c.white
