.pragma library
// Pure minimap model for the Hyprland adapter (shell/NiriMsg.qml). Kept free
// of QML types so tests can run it under a plain JavaScript engine.

// Agent terminals. Claude Code titles start "◐ "/"◑ " while working and
// "✳ " when idle; Codex prefixes a braille spinner while working and blinks
// "[!]"/"[.]" while it waits for input or approval. Pond's original rule
// treated every non-shell kitty title as an idle Codex; under Ghostty a shell
// title has no such shape, so an agent is recognised by its markers or name.
var TERMINALS = ["com.mitchellh.ghostty", "ghostty", "kitty"];

function isWorking(title) { return /^[\u2800-\u28ff\u25d0\u25d1] /.test(title || ""); }
function needsAttention(title) { return /^\[\s*[!.]\s*\](?:\s|$)/.test(title || ""); }
function isTerminal(appId) { return TERMINALS.indexOf(String(appId || "").toLowerCase()) >= 0; }
function isAgent(w) {
  const title = w.title || "";
  return isTerminal(w.appId) && title !== ""
      && (needsAttention(title) || /^[\u2800-\u28ff\u25d0\u25d1\u2733] /.test(title)
          || /(^|[\s:/-])(claude|codex)([\s:/-]|$)/i.test(title));
}

// Snapshot workspace -> the record NiriMsg consumers read (upstream fields).
function workspaceRecord(ws, windowsById) {
  const last = ws.lastWindow && windowsById[ws.lastWindow] ? ws.lastWindow : "";
  return {
    id: ws.id, idx: ws.id, name: ws.name || "", output: ws.output || "",
    activeWindowId: last, isActive: !!ws.isActive, isFocused: !!ws.isFocused,
    isUrgent: !!ws.isUrgent, hasWindows: false
  };
}

// Pick one creation workspace per output: a selected empty workspace, else an
// existing empty one after the occupied ones, else a not-yet-created ID.
// Hyprland destroys empty unselected workspaces, so the last case is common.
function creationRows(workspaces, outputs) {
  const used = {};
  for (const ws of workspaces) used[ws.id] = true;
  const byOutput = {};
  for (const name of outputs) byOutput[name] = [];
  for (const ws of workspaces) {
    if (!byOutput[ws.output]) byOutput[ws.output] = [];
    byOutput[ws.output].push(ws);
  }
  const result = {};
  for (const output of Object.keys(byOutput).sort()) {
    const list = byOutput[output];
    let anchor = 0, selectedEmpty = null;
    for (const ws of list) {
      if (ws.hasWindows || ws.isActive || ws.isFocused) anchor = Math.max(anchor, ws.id);
      if (!ws.hasWindows && (ws.isActive || ws.isFocused) && (!selectedEmpty || ws.isFocused))
        selectedEmpty = ws;
    }
    let creation = selectedEmpty ? { id: selectedEmpty.id, output: output, virtual: false } : null;
    if (!creation) {
      const empty = list.filter(ws => !ws.hasWindows && ws.id > anchor).sort((a, b) => a.id - b.id)[0];
      if (empty) creation = { id: empty.id, output: output, virtual: false };
    }
    if (!creation) {
      let candidate = Math.max(0, ...list.filter(ws => ws.id > 0).map(ws => ws.id)) + 1;
      while (used[candidate]) candidate++;
      used[candidate] = true;
      creation = { id: candidate, output: output, virtual: true };
    }
    result[output] = creation;
  }
  return result;
}

// Workspace rows for the sidebar minimap. `windows` is the adapter's window
// map (id -> record with workspaceId and order); `appInfo(appId)` resolves
// icons; `unread` marks agent terminals that finished out of sight.
function computeRows(workspaces, windows, outputs, appInfo, unread) {
  const byWorkspace = {};
  const list = Object.keys(windows).map(id => windows[id]);
  list.sort((a, b) => a.workspaceId - b.workspaceId || a.order - b.order
      || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  for (const w of list) {
    if (!byWorkspace[w.workspaceId]) byWorkspace[w.workspaceId] = [];
    byWorkspace[w.workspaceId].push(w);
  }
  const annotated = workspaces.map(ws => Object.assign({}, ws, { hasWindows: !!byWorkspace[ws.id] }));
  const creation = creationRows(annotated, outputs);
  const rowsSource = annotated.filter(ws => ws.hasWindows || ws.isActive || ws.isFocused
      || (creation[ws.output] && creation[ws.output].id === ws.id));
  for (const output in creation) {
    const c = creation[output];
    if (c.virtual)
      rowsSource.push({ id: c.id, idx: c.id, name: "", output: output, activeWindowId: "",
                        isActive: false, isFocused: false, isUrgent: false, hasWindows: false, virtual: true });
  }
  rowsSource.sort((a, b) => (a.output < b.output ? -1 : a.output > b.output ? 1 : 0) || a.id - b.id);

  const outputHasWindows = {};
  for (const ws of annotated) if (ws.hasWindows) outputHasWindows[ws.output] = true;
  const rows = [], ordinals = {}, numbers = {};
  for (const ws of rowsSource) {
    const records = (byWorkspace[ws.id] || []).map(w => {
      const info = appInfo(w.appId);
      const agent = isAgent(w);
      return {
        winId: w.id,
        iconName: info.iconName,
        iconSource: agent ? "" : info.iconSource,
        dotState: !agent ? "" : needsAttention(w.title) ? "attention"
            : isWorking(w.title) ? "working" : unread[w.id] ? "unread" : "read",
        loaderVariant: Math.abs(Number(w.serial) || 0) % 4 + 1
      };
    });
    const output = ws.output || "";
    const ordinal = ordinals[output] || 0;
    ordinals[output] = ordinal + 1;
    const isCreation = !!creation[output] && creation[output].id === ws.id;
    let number = 0;
    if (!isCreation || ws.isActive || ws.isFocused) {
      number = (numbers[output] || 0) + 1;
      numbers[output] = number;
    }
    rows.push({
      workspaceId: ws.id,
      workspaceIndex: ws.id,
      outputName: output,
      isActive: !!ws.isActive,
      isCreationRow: isCreation,
      outputHasWindows: !!outputHasWindows[output],
      activeWindowId: ws.activeWindowId || "",
      windowsJson: JSON.stringify(records),
      sidebarRowOrdinal: ordinal,
      sidebarWorkspaceNumber: number
    });
  }
  return { workspaces: annotated, rows: rows, creation: creation };
}

// The workspace a Super+N shortcut means on `output`: the Nth numbered row,
// matching the sidebar's numbers; one past the last is the creation row.
function workspaceForNumber(rows, output, number) {
  const own = rows.filter(r => r.outputName === output);
  const numbered = own.find(r => r.sidebarWorkspaceNumber === number);
  if (numbered) return numbered;
  const highest = own.reduce((n, r) => Math.max(n, r.sidebarWorkspaceNumber), 0);
  return number === highest + 1 ? own.find(r => r.isCreationRow && r.sidebarWorkspaceNumber === 0) || null : null;
}

// Position of a window among the windows of its workspace row.
function slotOf(windows, id) {
  const w = windows[id];
  if (!w) return -1;
  return Object.keys(windows).map(k => windows[k])
      .filter(o => o.workspaceId === w.workspaceId && o.order < w.order).length;
}
