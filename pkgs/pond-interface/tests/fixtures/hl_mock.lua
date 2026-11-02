-- A minimal stand-in for Hyprland 0.56's `hl` Lua API, enough to execute the
-- adapter's generated action code: dispatcher results ({ok = ...}), selector
-- resolution at construction time, workspace objects that survive ID
-- changes (with `.monitor.name` and a `.windows` count), "ID is taken"
-- errors, and Actions::swapWith's refusal when either window is fullscreen.
-- Tiled windows keep a global tile order so swaps and their rollback are
-- observable. FAIL_AT injects a failure into the Nth dispatch.
local state = { workspaces = {}, windows = {}, tiles = {}, log = {}, dispatches = 0, focused_monitor = nil }
FAIL_AT = FAIL_AT or 0

local function find_ws(sel)
  if type(sel) == "table" then return sel end
  sel = tostring(sel)
  for _, o in ipairs(state.workspaces) do
    if tostring(o.id) == sel or (sel:sub(1, 5) == "name:" and o.name == sel:sub(6)) then return o end
  end
  return nil
end

function add_workspace(id, name, monitor)
  local o = { id = id, name = name or tostring(id), label = name or tostring(id), renamed = name ~= nil,
              monitor = { name = monitor or "DP-1" }, windows = 0 }
  state.workspaces[#state.workspaces + 1] = o
  return o
end

function add_window(address, ws, fullscreen)
  local o = find_ws(ws)
  o.windows = o.windows + 1
  state.windows["address:" .. address] = { address = address, workspace = o, fullscreen = fullscreen or 0 }
  state.tiles[#state.tiles + 1] = address
end

function set_fullscreen(address, mode) state.windows["address:" .. address].fullscreen = mode end
function focus_monitor(name) state.focused_monitor = name end

local function result(ok, err)
  if ok then return { ok = true } end
  return { ok = false, error = err }
end

local function dispatcher(name, fn)
  return function()
    state.dispatches = state.dispatches + 1
    state.log[#state.log + 1] = name
    if state.dispatches == FAIL_AT then return result(false, "injected failure in " .. name) end
    return fn()
  end
end

hl = { dsp = { workspace = {}, window = {} } }

function hl.get_workspace(sel) return find_ws(sel) end
function hl.get_window(sel) return state.windows[sel] end
function hl.dispatch(d)
  if type(d) ~= "function" then return nil end
  return d()
end

function hl.dsp.workspace.change_id(t)
  local sel = type(t.workspace) == "table" and tostring(t.workspace.id) or t.workspace
  return dispatcher("change_id " .. sel .. " " .. t.id, function()
    local o = find_ws(sel)
    if not o then return result(false, "no such workspace") end
    if find_ws(tostring(t.id)) then return result(false, "ID is taken") end
    o.id = t.id
    if not o.renamed then o.name = tostring(t.id) end
    return result(true)
  end)
end

function hl.dsp.workspace.move(t)
  return dispatcher("workspace.move " .. t.workspace .. " " .. t.monitor, function()
    local o = find_ws(t.workspace)
    if not o then return result(false, "Workspace not found") end
    o.monitor = { name = t.monitor }
    return result(true)
  end)
end

function hl.dsp.window.move(t)
  return dispatcher("window.move " .. t.window .. " " .. t.workspace, function()
    local w = state.windows[t.window]
    if not w then return result(true) end -- Hyprland ignores a missing window
    -- A new workspace appears on the focused monitor.
    local o = find_ws(t.workspace) or add_workspace(tonumber(t.workspace), nil, state.focused_monitor)
    w.workspace.windows = w.workspace.windows - 1
    o.windows = o.windows + 1
    w.workspace = o
    return result(true)
  end)
end

function hl.dsp.window.swap(t)
  return dispatcher("window.swap " .. t.window .. " " .. t.with, function()
    local a, b = state.windows[t.window], state.windows[t.with]
    if not a or not b then return result(false, "window not found") end
    if a.fullscreen ~= 0 or b.fullscreen ~= 0 then return result(false, "Can't swap fullscreen window") end
    local i, j
    for k, address in ipairs(state.tiles) do
      if address == a.address then i = k elseif address == b.address then j = k end
    end
    state.tiles[i], state.tiles[j] = state.tiles[j], state.tiles[i]
    return result(true)
  end)
end

function hl.dsp.focus(t)
  local what = t.workspace or t.window or t.monitor
  if what == nil then return nil end
  return dispatcher("focus " .. what, function()
    if t.monitor then state.focused_monitor = t.monitor end
    return result(true)
  end)
end

function hl.dsp.dpms(t)
  return dispatcher("dpms " .. t.action, function() return result(true) end)
end

function hl.monitor(spec) state.log[#state.log + 1] = "monitor " .. spec.output end
function hl.config(spec) state.log[#state.log + 1] = "config" end

function report(ok, value)
  local ids, places = {}, {}
  for _, o in ipairs(state.workspaces) do
    ids[#ids + 1] = o.label .. "=" .. tostring(o.id)
    places[#places + 1] = tostring(o.id) .. "@" .. o.monitor.name .. "#" .. tostring(o.windows)
  end
  io.write("ok=", tostring(ok), "\n")
  io.write("value=", tostring(value), "\n")
  io.write("ids=", table.concat(ids, ","), "\n")
  io.write("places=", table.concat(places, ","), "\n")
  io.write("tiles=", table.concat(state.tiles, ","), "\n")
  io.write("log=", table.concat(state.log, ";"), "\n")
end
