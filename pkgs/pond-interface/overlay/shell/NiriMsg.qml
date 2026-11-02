pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io
import "hypr/Model.js" as Model

// Compositor adapter. The name and interface are upstream Pond's Niri adapter,
// kept so its consumers (sidebar minimap, drag session, notifications,
// keyboard feedback, settings) are unchanged; the implementation talks to
// Hyprland through hypr/adapter.py, one long-lived process that streams state
// snapshots and executes requests.
//
// Hyprland semantics differ from Niri: window IDs are address strings,
// workspaces have fixed numeric IDs and only exist while selected or
// occupied, there is no overview, and dwindle has no columns. Window drags
// therefore commit once on release (cancelling leaves the window untouched),
// the minimap orders windows by their tile position, and dropping at a slot
// walks the window there with tile swaps. A creation row's workspace ID is
// only a hint: requests carry the row's output and `create`, and the adapter
// makes a new workspace on that output even if the ID was taken meanwhile.
Singleton {
  id: root

  property var workspaces: []
  property var monitors: []
  property var keyboardLayouts: []
  property int keyboardLayoutIndex: 0
  signal keyboardLayoutSwitched
  signal configReloaded
  // Hyprland has no overview; kept for consumers of the upstream interface.
  readonly property bool overviewOpen: false
  property var dragOrigin: null
  property bool connected: false
  property string lastError: ""
  // Failed actions, for the minimap's error notice: the message, the output
  // the action concerned ("" if none) and whether it partly happened.
  signal actionFailed(string message, string output, bool partial)
  property bool _workspaceLauncherOpen: false
  property var _windows: ({})
  property var _appInfoCache: ({})
  Connections {
    target: DesktopEntries.applications
    function onValuesChanged() {
      root._appInfoCache = ({});
      root._recompute();
    }
  }
  property var _creation: ({})
  property var _unread: ({})
  property int _focusedWorkspaceId: 0
  property bool _haveSnapshot: false
  property string _focusedOutput: ""
  property ListModel workspaceRows: ListModel {}
  readonly property string focusedOutputName: _focusedOutput

  // --- Requests ---------------------------------------------------------

  property int _nextRequest: 1
  property var _pending: ({})

  function _request(action, args, done) {
    if (!backend.running) {
      _fail("The Hyprland adapter is not running", args?.output ?? "", false);
      return false;
    }
    const id = _nextRequest++;
    _pending[id] = { done: done || null, at: Date.now(), action: action, output: args?.output ?? "" };
    backend.write(JSON.stringify(Object.assign({ id: id, action: action }, args || {})) + "\n");
    requestTimeout.start();
    return true;
  }

  function _settle(id, ok, error, partial) {
    const entry = _pending[id];
    if (!entry) return;
    delete _pending[id];
    if (!ok) {
      console.warn("pond: " + entry.action + " failed:", error);
      _fail(error || "Hyprland rejected " + entry.action, entry.output, !!partial);
    }
    if (entry.done) entry.done(ok);
  }

  function _fail(message, output, partial) {
    lastError = message;
    actionFailed(message, output || "", !!partial);
  }

  // Where a request for workspace `id` should go: a creation row carries its
  // output and the intent to create; an existing workspace its own output.
  function _intent(id) {
    const creation = Object.values(_creation).find(c => c.id === id);
    if (creation) return { output: creation.output, create: true };
    const ws = workspaces.find(w => w.id === id);
    return { output: ws ? ws.output : "", create: false };
  }

  function _failAll(reason) {
    for (const id of Object.keys(_pending)) _settle(Number(id), false, reason);
  }

  Timer {
    id: requestTimeout
    interval: 500
    repeat: true
    onTriggered: {
      const now = Date.now();
      let waiting = false;
      for (const id of Object.keys(root._pending)) {
        if (now - root._pending[id].at > 4000) root._settle(Number(id), false, "Hyprland did not answer in time");
        else waiting = true;
      }
      if (!waiting) stop();
    }
  }

  // --- Upstream interface -----------------------------------------------

  function focusWindow(id) {
    if (id === undefined || id === null || id === "") return;
    _request("focus-window", { window: String(id) });
  }

  // `index` is the row's workspaceIndex, which is the Hyprland workspace ID.
  function focusWorkspace(index) {
    if (index === undefined || index === null || isNaN(Number(index))) return;
    _request("focus-workspace", Object.assign({ workspace: Number(index) }, _intent(Number(index))));
  }

  function openLauncher(workspaceId) {
    const ws = workspaces.find(w => w.id === workspaceId);
    if (!ws && !_isCreation(workspaceId)) return;
    if (ws && ws.isFocused) {
      Quickshell.execDetached(["pond-session", "launcher", "open"]);
      return;
    }
    _request("focus-workspace", Object.assign({ workspace: workspaceId }, _intent(workspaceId)), success => {
      if (success) Quickshell.execDetached(["pond-session", "launcher", "open"]);
    });
  }

  function windowExists(id) { return _windows[id] !== undefined; }

  // Begins a minimap window drag. Nothing moves until finishWindowDrag:
  // previewing every hovered slot on Hyprland would shuffle tiles across
  // monitors while the pointer travels.
  function showWindowOverview(id, done) {
    const window = _windows[id];
    if (!window || dragOrigin) return false;
    dragOrigin = Object.assign({}, window, { slot: Model.slotOf(_windows, id), pending: null });
    Qt.callLater(() => done(true));
    return true;
  }

  // `intent` ({output, create}) comes from the row the window was dropped
  // on, frozen when the drag began; without it the current rows decide.
  function moveWindow(id, workspaceId, slot, done, intent) {
    if (!windowExists(id) || !_workspaceKnown(workspaceId)) return false;
    const where = intent ? { output: String(intent.output || ""), create: !!intent.create } : _intent(workspaceId);
    if (dragOrigin && dragOrigin.id === id) {
      dragOrigin = Object.assign({}, dragOrigin, {
        pending: { workspaceId: workspaceId, slot: slot, output: where.output, create: where.create } });
      Qt.callLater(() => done(true));
      return true;
    }
    return _request("move-window", Object.assign({ window: id, workspace: workspaceId, slot: slot }, where), done);
  }

  function finishWindowDrag(restore, done) {
    const origin = dragOrigin;
    if (!origin) return false;
    const target = origin.pending;
    const unchanged = !target || (!target.create && target.workspaceId === origin.workspaceId
                                  && target.slot === origin.slot);
    const finished = success => {
      dragOrigin = null;
      _recompute();
      done(success);
    };
    if (restore || unchanged) {
      Qt.callLater(() => finished(true));
      return true;
    }
    if (!windowExists(origin.id)) {
      _fail("The window closed before it could be moved", target.output, false);
      Qt.callLater(() => finished(false));
      return true;
    }
    // The output and creation intent recorded at drop time, not whatever
    // workspace now has the row's ID.
    if (!_request("move-window", { window: origin.id, workspace: target.workspaceId, slot: target.slot,
                                   output: target.output, create: target.create }, finished))
      Qt.callLater(() => finished(false));
    return true;
  }

  function moveWorkspace(id, targetId, done) {
    const source = workspaces.find(w => w.id === id);
    const target = workspaces.find(w => w.id === targetId);
    if (!source || !target || source.output !== target.output) return false;
    return _request("move-workspace", { workspace: id, target: targetId }, done);
  }

  function appMatches(appId, appName, desktopEntry) {
    function normalize(value) {
      return String(value || "").toLowerCase().replace(/\.desktop$/, "").replace(/[^a-z0-9]+/g, "");
    }
    const wanted = desktopEntry || appName;
    if (!appId || !wanted) return false;
    const entry = DesktopEntries.heuristicLookup(appId);
    const target = DesktopEntries.heuristicLookup(wanted);
    if (entry && target) return entry.id === target.id;
    return [appId, entry?.id, entry?.name].some(value => normalize(value) === normalize(wanted));
  }

  function focusApp(appName, desktopEntry) {
    const window = Object.values(_windows).find(w => appMatches(w.appId, appName, desktopEntry));
    if (window) focusWindow(window.id);
    return !!window;
  }

  // --- Additions: numbered workspace shortcuts and display power --------

  // Super+N follows the sidebar's numbering on the focused display.
  function focusWorkspaceNumber(number) {
    const row = Model.workspaceForNumber(_rows, focusedOutputName, number);
    if (row) focusWorkspace(row.workspaceId);
    return !!row;
  }

  function moveFocusedWindowToNumber(number) {
    const row = Model.workspaceForNumber(_rows, focusedOutputName, number);
    const window = Object.values(_windows).find(w => w.isFocused);
    if (!row || !window) return false;
    return _request("move-window", { window: window.id, workspace: row.workspaceId,
                                     output: row.outputName, create: !!row.isCreationRow, slot: null });
  }

  function setDisplayPower(on) { return _request("dpms", { state: on ? "on" : "off" }); }

  function refresh() { return _request("refresh", {}); }

  // --- Backend ----------------------------------------------------------

  Process {
    id: backend
    command: ["python3", Quickshell.shellDir + "/hypr/adapter.py"]
    stdinEnabled: true
    running: true
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: line => {
        let message;
        try { message = JSON.parse(line); }
        catch (e) { console.warn("pond: bad adapter line:", e); return; }
        root._handle(message);
      }
    }
    stderr: SplitParser { onRead: line => console.warn("pond adapter:", line) }
    onExited: (code, status) => {
      root.connected = false;
      root._failAll("The Hyprland adapter stopped");
      restartTimer.start();
    }
  }

  Timer {
    id: restartTimer
    interval: 1500
    onTriggered: backend.running = true
  }

  function _handle(message) {
    if (message.type === "snapshot") _applySnapshot(message);
    else if (message.type === "title") _applyTitle(message.id, message.title);
    else if (message.type === "keyboard") {
      keyboardLayouts = message.names;
      keyboardLayoutIndex = message.index;
      if (message.switched) keyboardLayoutSwitched();
    } else if (message.type === "reply") _settle(message.id, message.ok, message.error, message.partial);
    else if (message.type === "status") {
      connected = message.connected;
      if (message.error) {
        lastError = message.error;
        console.warn("pond:", message.error);
      }
    } else if (message.type === "configreloaded") configReloaded();
  }

  function _windowRecord(w) {
    return { id: w.id, workspaceId: w.workspaceId, output: w.output, title: w.title, appId: w.appId,
             isFocused: w.isFocused, isFloating: w.isFloating, fullscreen: w.fullscreen,
             order: w.order, serial: w.serial };
  }

  function _trackUnread(old, w) {
    if (w.isFocused) delete _unread[w.id];
    else if (old && Model.isAgent(w) && Model.isWorking(old.title) && !Model.isWorking(w.title))
      _unread[w.id] = true;
  }

  function _applySnapshot(snapshot) {
    const windows = {};
    for (const w of snapshot.windows) {
      const record = _windowRecord(w);
      _trackUnread(_windows[w.id], record);
      windows[w.id] = record;
    }
    for (const id of Object.keys(_unread)) if (!windows[id]) delete _unread[id];
    _windows = windows;
    monitors = snapshot.monitors;
    _focusedOutput = snapshot.focusedOutput || "";
    keyboardLayouts = snapshot.keyboard.names;
    keyboardLayoutIndex = snapshot.keyboard.index;
    workspaces = snapshot.workspaces.filter(ws => !ws.special).map(ws => Model.workspaceRecord(ws, windows));

    // Selecting an empty workspace opens the launcher, as on upstream Pond.
    const focused = workspaces.find(ws => ws.isFocused);
    const focusedId = focused ? focused.id : 0;
    const focusedOccupied = focused && Object.values(windows).some(w => w.workspaceId === focused.id);
    if (_haveSnapshot && focused && focusedId !== _focusedWorkspaceId && !dragOrigin && !focusedOccupied) {
      _workspaceLauncherOpen = true;
      Quickshell.execDetached(["pond-session", "launcher", "open"]);
    } else if (_workspaceLauncherOpen && focusedOccupied) {
      _workspaceLauncherOpen = false;
      Quickshell.execDetached(["pond-session", "launcher", "close"]);
    }
    _focusedWorkspaceId = focusedId;
    _haveSnapshot = true;
    _recompute();
  }

  function _applyTitle(id, title) {
    const old = _windows[id];
    if (!old || old.title === title) return;
    const updated = Object.assign({}, old, { title: title });
    _trackUnread(old, updated);
    const windows = Object.assign({}, _windows);
    windows[id] = updated;
    _windows = windows;
    _recompute();
  }

  function _isCreation(id) {
    return Object.values(_creation).some(c => c.id === id);
  }

  function _workspaceKnown(id) {
    return workspaces.some(w => w.id === id) || _isCreation(id);
  }

  function _appInfo(appId) {
    if (_appInfoCache[appId])
      return _appInfoCache[appId];
    const lower = appId.toLowerCase();
    const matched = appId && (DesktopEntries.heuristicLookup(appId)
           || DesktopEntries.byId(appId)
           || DesktopEntries.byId(lower)
           || DesktopEntries.heuristicLookup(lower));
    // Electron can install an iconless, hidden protocol handler whose ID
    // exactly matches the window. It wins heuristicLookup over the packaged
    // application; use that application's StartupWMClass to recover its icon.
    const entry = matched?.icon ? matched
        : (appId && DesktopEntries.applications.values.find(candidate => candidate.icon
            && candidate.startupClass.toLowerCase() === lower)) || matched;
    const iconName = entry?.icon || lower;
    const info = {
      iconName: iconName,
      iconSource: entry && !Quickshell.hasThemeIcon(iconName) && _hicolorIcon(iconName)
          || Quickshell.iconPath(iconName, "application-x-executable")
    };
    // DesktopEntries scans asynchronously; don't cache an incomplete match.
    if (entry?.icon)
      _appInfoCache[appId] = info;
    return info;
  }

  function _hicolorIcon(name) {
    const dataHome = Quickshell.env("XDG_DATA_HOME") || Quickshell.env("HOME") + "/.local/share";
    const dataDirs = (Quickshell.env("XDG_DATA_DIRS") || "").split(":").filter(dir => dir);
    for (const dir of [dataHome, ...dataDirs]) {
      for (const size of ["scalable", "512x512", "256x256", "128x128", "64x64", "48x48"]) {
        const path = dir + "/icons/hicolor/" + size + "/apps/" + name
            + (size === "scalable" ? ".svg" : ".png");
        if (Quickshell.iconPath(path, true))
          return "file://" + path;
      }
    }
    return "";
  }

  property var _rows: []

  function _recompute() {
    // The drag's source delegate and drop slots must survive live changes.
    if (dragOrigin) return;
    const outputs = monitors.map(m => m.name);
    const result = Model.computeRows(workspaces, _windows, outputs, appId => _appInfo(appId), _unread);
    let changed = workspaces.length !== result.workspaces.length;
    for (let i = 0; !changed && i < workspaces.length; i++)
      changed = workspaces[i].hasWindows !== result.workspaces[i].hasWindows;
    if (changed) workspaces = result.workspaces;
    _creation = result.creation;
    _rows = result.rows;
    _syncModel(workspaceRows, result.rows, "workspaceId");
  }

  function _syncModel(model, desired, identityKey) {
    for (let i = 0; i < desired.length; i++) {
      const d = desired[i];
      let j = -1;
      for (let s = i; s < model.count; s++) {
        if (model.get(s)[identityKey] === d[identityKey]) { j = s; break; }
      }
      if (j === -1) {
        model.insert(i, d);
        continue;
      }
      if (j !== i)
        model.move(j, i, 1);
      const cur = model.get(i);
      for (const key of Object.keys(d))
        if (cur[key] !== d[key])
          model.setProperty(i, key, d[key]);
    }
    while (model.count > desired.length)
      model.remove(model.count - 1);
  }
}
