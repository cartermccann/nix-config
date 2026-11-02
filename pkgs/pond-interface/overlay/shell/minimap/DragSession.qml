import QtQuick
import "." as Minimap
import ".." as Shell

// Upstream's per-minimap drag session. Workspace reordering is unchanged
// (local DropAreas, one display). Window drags are shared through
// Minimap.DragCoordinator so they can end on another display's sidebar:
// this session reports the pointer in global coordinates, the coordinator's
// destination comes from whichever minimap is under it, and this session
// commits the move when the pointer is released.
Item {
  id: root
  // The minimap owning this session; its screen anchors global coordinates.
  property var map: null
  property var source: null
  property Item destination: null
  property bool held: false
  property bool committing: false
  property bool resetting: false
  property bool preparingOverview: false
  property bool movingPreview: false
  property bool previewed: false
  property var applied: null
  onDestinationChanged: if (!windowDrag && held) preview.restart()
  readonly property var windowDestination: windowDrag ? Minimap.DragCoordinator.destination : null
  onWindowDestinationChanged: if (windowDrag && held) preview.restart()
  readonly property bool active: source !== null
  readonly property bool windowDrag: source?.kind === "window"
  property point origin
  property point press
  property point pointer
  property point grabOffset
  readonly property bool sourcePresent: !source || (windowDrag
      ? Shell.NiriMsg.windowExists(source.windowData.winId)
      : Shell.NiriMsg.workspaces.some(w => w.id === source.workspaceId))
  onSourcePresentChanged: if (!sourcePresent) cancel()
  readonly property real rowOffset: !source || windowDrag ? 0
      : held ? Math.max((1 - source.number) * Shell.Theme.workspaceRowPitch,
          Math.min((source.count - source.number) * Shell.Theme.workspaceRowPitch, pointer.y - press.y))
      : ((destination?.number ?? source.number) - source.number) * Shell.Theme.workspaceRowPitch

  z: 100
  width: Shell.Theme.workspaceControlSize
  height: width
  // A window dragged onto another display is drawn there by that minimap,
  // and is not snapped back into this one when dropped there.
  visible: active && !(windowDrag && (held ? Minimap.DragCoordinator.pointerMap !== map
      : !!windowDestination && windowDestination.map !== map))
  Drag.source: root
  Drag.keys: ["pond-workspace"]
  Drag.hotSpot.x: grabOffset.x
  Drag.hotSpot.y: grabOffset.y
  Drag.supportedActions: Qt.MoveAction
  Drag.proposedAction: Qt.MoveAction

  function screenOrigin() {
    const s = map?.screen;
    return Qt.point(s?.x || 0, s?.y || 0);
  }

  function begin(data, item, pressPoint, currentPoint) {
    if (active || !item.contains(item.mapFromItem(null, pressPoint.x, pressPoint.y)))
      return;
    if (data.kind === "window" && !Minimap.DragCoordinator.begin(
        Object.assign({}, data, { output: map?.screen?.name ?? "" }), map))
      return;
    source = data;
    origin = item.mapToItem(parent, 0, 0);
    press = parent.mapFromItem(null, pressPoint.x, pressPoint.y);
    grabOffset = Qt.point(press.x - origin.x, press.y - origin.y);
    held = true;
    move(currentPoint);
    if (!windowDrag) Drag.active = true;
    if (windowDrag) {
      applied = { workspaceId: data.workspaceId, slot: data.slot, output: map?.screen?.name ?? "", creation: false };
      preparingOverview = Shell.NiriMsg.showWindowOverview(data.windowData.winId, success => {
        preparingOverview = false;
        if (!success) cancel();
        else if (held) preview.restart();
        else settle.restart();
      });
      if (!preparingOverview) cancel();
    }
  }

  function move(scenePoint) {
    if (!held)
      return;
    pointer = parent.mapFromItem(null, scenePoint.x, scenePoint.y);
    x = windowDrag ? pointer.x - grabOffset.x : origin.x;
    y = windowDrag ? pointer.y - grabOffset.y : origin.y + rowOffset;
    if (windowDrag) {
      const o = screenOrigin();
      Minimap.DragCoordinator.move(scenePoint.x + o.x, scenePoint.y + o.y);
    }
  }

  function release() {
    if (!held)
      return;
    held = false;
    if (windowDrag) {
      const target = windowDestination;
      const local = target && target.map === map ? map.snapPosition(target) : null;
      const snap = local ? local : target ? null : origin;
      if (snap) { x = snap.x; y = snap.y; }
      settle.restart();
      return;
    }
    const accepted = Drag.drop() !== Qt.IgnoreAction;
    if (!accepted) destination = null;
    const target = destination ? destination.snapPosition() : origin;
    x = target.x;
    y = target.y;
    settle.restart();
  }

  function cancel() {
    if (!active || committing)
      return;
    if (windowDrag) Minimap.DragCoordinator.destination = null;
    else Drag.cancel();
    destination = null;
    held = false;
    x = origin.x;
    y = origin.y;
    settle.restart();
  }

  function clear() {
    if (windowDrag && Shell.NiriMsg.finishWindowDrag(!windowDestination && previewed, finish))
      return;
    finish();
  }

  function sameAsApplied(target) {
    return applied && target && applied.workspaceId === target.workspaceId && applied.slot === target.slot
        && applied.output === target.output && applied.creation === target.creation;
  }

  function updatePreview() {
    if (preparingOverview || movingPreview) return;
    const target = windowDestination;
    if (!target || sameAsApplied(target)) {
      if (!held) clear();
      return;
    }
    movingPreview = Shell.NiriMsg.moveWindow(source.windowData.winId,
        target.workspaceId, target.slot, success => {
          movingPreview = false;
          if (success) applied = target;
          else { Minimap.DragCoordinator.destination = null; held = false; }
          if (held) preview.restart();
          else settle.restart();
        }, { output: target.output, create: target.creation });
    if (movingPreview) previewed = true;
    else { Minimap.DragCoordinator.destination = null; held = false; clear(); }
  }

  function finish() {
    resetting = true;
    preview.stop();
    applied = null;
    previewed = false;
    if (windowDrag && Minimap.DragCoordinator.sourceMap === map) Minimap.DragCoordinator.end();
    source = null;
    destination = null;
    committing = false;
    Qt.callLater(() => resetting = false);
  }

  Timer {
    id: preview
    interval: 32
    onTriggered: if (root.held && root.windowDrag) root.updatePreview()
  }

  Timer {
    id: settle
    interval: Shell.Theme.workspaceDragSnapDuration
    onTriggered: {
      if (root.preparingOverview || root.movingPreview) return;
      if (root.windowDrag) {
        root.committing = true;
        root.updatePreview();
        return;
      }
      if (!root.destination) { root.clear(); return; }
      root.committing = true;
      const done = success => root.clear();
      const started = Shell.NiriMsg.moveWorkspace(root.source.workspaceId,
          root.destination.workspaceId, done);
      if (!started) root.clear();
    }
  }

  Behavior on x { enabled: !root.held; Shell.HoverAnimation { duration: Shell.Theme.workspaceDragSnapDuration } }
  Behavior on y { enabled: !root.held; Shell.HoverAnimation { duration: Shell.Theme.workspaceDragSnapDuration } }

  Minimap.WindowIcon {
    anchors.fill: parent
    visible: root.windowDrag
    windowData: root.source?.windowData ?? ({})
    lifted: true
    interactive: false
  }
}
