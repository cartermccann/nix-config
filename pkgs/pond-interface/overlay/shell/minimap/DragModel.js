.pragma library
// Pure geometry and intent for minimap window drags across sidebar windows
// (minimap/DragCoordinator.qml). Each display has its own sidebar window, and
// Qt only delivers drag/drop events inside one window, so a drag is tracked
// in global (compositor logical) coordinates instead: the pointer keeps
// reporting to the window it was pressed in, which converts it to global by
// adding its display's origin (the sidebar is anchored to the display's
// top-left corner). Kept free of QML types so tests run it under node.

// The display rectangle containing a global point, as {index, rect}, or null.
// `rects` are {x, y, width, height}; edges belong to the display they start.
function screenAt(rects, x, y) {
  for (let i = 0; i < rects.length; i++) {
    const r = rects[i];
    if (r && x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height)
      return { index: i, rect: r };
  }
  return null;
}

// Global point -> the sidebar window's scene coordinates on display `rect`.
function toScene(rect, x, y) { return { x: x - rect.x, y: y - rect.y }; }
function toGlobal(rect, x, y) { return { x: x + rect.x, y: y + rect.y }; }

// Drop slot under `localX` in a row's window carousel: the gap before each
// icon belongs to that icon; clamped to "after the last remaining window".
function slotAt(localX, carouselX, pitch, gap, remaining) {
  return Math.max(0, Math.min(remaining, Math.floor((localX - carouselX + gap / 2) / pitch)));
}

// Row index under a local y, given each row's top and the row pitch.
function rowAt(tops, pitch, y) {
  for (let i = 0; i < tops.length; i++)
    if (tops[i] !== null && y >= tops[i] && y < tops[i] + pitch) return i;
  return -1;
}

// A destination: where a window would land. `creation` marks a row offered
// for a new workspace, whose ID is only a hint; the output is the intent.
function target(output, workspaceId, slot, creation) {
  return { output: String(output || ""), workspaceId: Number(workspaceId), slot: Number(slot),
           creation: !!creation };
}

function sameTarget(a, b) {
  if (!a || !b) return !a && !b;
  return a.output === b.output && a.workspaceId === b.workspaceId && a.slot === b.slot
      && a.creation === b.creation;
}

// The adapter request a drop commits to, or null when nothing would move.
function commit(source, destination) {
  if (!source || !destination) return null;
  if (!destination.creation && destination.workspaceId === source.workspaceId && destination.slot === source.slot)
    return null;
  return { window: source.windowData.winId, workspace: destination.workspaceId, slot: destination.slot,
           output: destination.output, create: destination.creation };
}
