-- One global statusline (laststatus=3), no plugin. Left: mode dot, path,
-- git. Right: diagnostics, LSP, position. Colours are the AuseklisSL* groups
-- in colors/palette.lua; cobalt is only the normal-mode dot.
local M = {}

local modes = {
  n = { "NORMAL", "AuseklisSLNormal" },
  i = { "INSERT", "AuseklisSLInsert" },
  v = { "VISUAL", "AuseklisSLVisual" },
  V = { "V-LINE", "AuseklisSLVisual" },
  ["\22"] = { "V-BLOCK", "AuseklisSLVisual" },
  c = { "COMMAND", "AuseklisSLCommand" },
  R = { "REPLACE", "AuseklisSLReplace" },
  t = { "TERMINAL", "AuseklisSLInsert" },
}

local function esc(s) return (s:gsub("%%", "%%%%")) end

local function path()
  local name = vim.api.nvim_buf_get_name(0)
  if name == "" then return "[no name]" end
  local rel = vim.fn.fnamemodify(name, ":~:.")
  local dir, file = rel:match("^(.*/)([^/]+)$")
  local mod = vim.bo.modified and " %#AuseklisSLWarn#●" or ""
  if dir then
    return "%#AuseklisSLDim#" .. esc(dir) .. "%#AuseklisSLText#" .. esc(file) .. mod
  end
  return "%#AuseklisSLText#" .. esc(rel) .. mod
end

local function git()
  local d = vim.b.gitsigns_status_dict
  if not d or not d.head or d.head == "" then return "" end
  local out = "%#AuseklisSLDim#  󰘬 " .. esc(d.head)
  if (d.added or 0) > 0 then out = out .. " %#AuseklisSLAdd#+" .. d.added end
  if (d.changed or 0) > 0 then out = out .. " %#AuseklisSLChange#~" .. d.changed end
  if (d.removed or 0) > 0 then out = out .. " %#AuseklisSLErr#-" .. d.removed end
  return out
end

local function diagnostics()
  local n = vim.diagnostic.count(0)
  local s = vim.diagnostic.severity
  local out = {}
  if (n[s.ERROR] or 0) > 0 then table.insert(out, "%#AuseklisSLErr#✕ " .. n[s.ERROR]) end
  if (n[s.WARN] or 0) > 0 then table.insert(out, "%#AuseklisSLWarn#▲ " .. n[s.WARN]) end
  if (n[s.INFO] or 0) + (n[s.HINT] or 0) > 0 then
    table.insert(out, "%#AuseklisSLDim#● " .. (n[s.INFO] or 0) + (n[s.HINT] or 0))
  end
  return table.concat(out, " ")
end

local function lsp()
  local names = {}
  for _, c in ipairs(vim.lsp.get_clients({ bufnr = 0 })) do table.insert(names, c.name) end
  if #names == 0 then return "" end
  return "%#AuseklisSLDim#" .. esc(table.concat(names, " · "))
end

function M.render()
  local mode = modes[vim.fn.mode():sub(1, 1)] or { vim.fn.mode():upper(), "AuseklisSLNormal" }
  local left = table.concat({
    "%#" .. mode[2] .. "# ● %#AuseklisSLDim#" .. mode[1] .. "  ",
    path(),
    git(),
  })
  local right = table.concat(vim.tbl_filter(function(s) return s ~= "" end, {
    diagnostics(),
    lsp(),
    "%#AuseklisSLDim#%l:%c  %P ",
  }), "   ")
  return left .. "%#AuseklisSLText#%=" .. right
end

_G.auseklis_statusline = M.render
vim.o.statusline = "%!v:lua.auseklis_statusline()"

-- redraw when the inputs change outside a keystroke
vim.api.nvim_create_autocmd({ "DiagnosticChanged", "LspAttach", "LspDetach" }, {
  group = vim.api.nvim_create_augroup("auseklis_statusline", { clear = true }),
  callback = function() vim.cmd.redrawstatus() end,
})
vim.api.nvim_create_autocmd("User", {
  group = "auseklis_statusline",
  pattern = "GitSignsUpdate",
  callback = function() vim.cmd.redrawstatus() end,
})

return M
