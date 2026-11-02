pragma Singleton
import QtQuick
import Quickshell
import "DragModel.js" as Model

// One window drag shared by every sidebar's minimap. The minimap the drag
// started in reports the pointer in global coordinates; the minimap on the
// display under the pointer resolves the row and slot (on its own screen and
// with its own rows), so the destination, its highlight and the drop slot
// are drawn where the pointer is, while the source minimap commits the move.
// Workspace reordering stays within one minimap (one display) and does not
// use this.
Singleton {
  id: root

  property var maps: []
  // {kind: "window", windowData, workspaceId, slot, output}
  property var source: null
  property var sourceMap: null
  // Model.target(...) plus `map`, the minimap that resolved it.
  property var destination: null
  property point pointer
  // The minimap under the pointer, or null between displays.
  property var pointerMap: null
  readonly property bool active: source !== null

  function register(map) {
    if (maps.indexOf(map) < 0) maps = maps.concat([map]);
  }

  function unregister(map) {
    maps = maps.filter(m => m !== map);
    if (pointerMap === map) pointerMap = null;
    if (destination && destination.map === map) destination = null;
  }

  function begin(data, map) {
    if (active) return false;
    destination = null;
    pointerMap = map;
    sourceMap = map;
    source = data;
    return true;
  }

  function screenRect(map) {
    const s = map.screen;
    return s ? { x: s.x || 0, y: s.y || 0, width: s.width || 0, height: s.height || 0 } : null;
  }

  // Global pointer position from the source minimap's window.
  function move(x, y) {
    if (!active) return;
    pointer = Qt.point(x, y);
    const hit = Model.screenAt(maps.map(screenRect), x, y);
    const map = hit ? maps[hit.index] : null;
    if (pointerMap !== map) pointerMap = map;
    const found = map ? map.windowTargetAt(x, y) : null;
    const next = found ? Object.assign(found, { map: map }) : null;
    if (!Model.sameTarget(next, destination)) destination = next;
  }

  // The adapter request for the current destination, or null.
  function request() { return Model.commit(source, destination); }

  function end() {
    source = null;
    sourceMap = null;
    destination = null;
    pointerMap = null;
  }
}
