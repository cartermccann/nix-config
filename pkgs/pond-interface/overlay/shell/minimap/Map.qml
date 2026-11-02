pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Shapes
import Quickshell
import "." as Minimap
import ".." as Shell
import "DragModel.js" as DragModel

Shell.Card {
  id: root

  property var screen: null
  property real collapseProgress: 0
  readonly property real widthProgress: Shell.Theme.collapseWidth(collapseProgress)
  readonly property real heightProgress: Shell.Theme.collapseHeight(collapseProgress)
  property real maximumHeight: 10000
  readonly property alias dragSession: dragSession
  signal focusRequested(int workspaceIndex)
  signal windowRequested(var windowId)
  signal addRequested(var workspaceId)

  readonly property real expandedOpacity:
      collapseProgress < 0.56 ? 1 : 0
  readonly property real collapsedOpacity:
      collapseProgress >= 0.56 ? 1 : 0
  readonly property int expandedVisibleRowCount: expandedColumn.height > 0
      ? Math.round((expandedColumn.height + Shell.Theme.workspaceRowGap)
                   / Shell.Theme.workspaceRowPitch)
      : 0
  readonly property int expandedRenderedRowCount: Math.max(
      Shell.Theme.workspaceGridMinimumRows, expandedVisibleRowCount)
  readonly property real expandedNaturalHeight:
      expandedVisibleRowCount <= Shell.Theme.workspaceGridMinimumRows
      ? Shell.Theme.workspaceGridMinimumHeight
      : Shell.Theme.workspaceContentPadding * 2
        + expandedVisibleRowCount * Shell.Theme.workspaceControlSize
        + (expandedVisibleRowCount - 1) * Shell.Theme.workspaceRowGap
  readonly property real collapsedNaturalHeight: collapsedColumn.children.reduce(
      (height, row) => height + (row.targetHeight ?? 0), 0)
  readonly property real naturalHeight: Shell.Theme.lerp(
      expandedNaturalHeight, collapsedNaturalHeight,
      Shell.Theme.collapseHeight(collapseProgress))
  // Window drags are shared by every display's minimap (DragCoordinator):
  // `windowDrop` is the destination when it lies in this minimap.
  readonly property bool windowDragActive: Minimap.DragCoordinator.active
  readonly property string draggedWindowId: Minimap.DragCoordinator.source?.windowData?.winId ?? ""
  readonly property var windowDrop: Minimap.DragCoordinator.destination?.map === root
      ? Minimap.DragCoordinator.destination : null
  readonly property bool workspaceNumbersRevealed: expandedLayer.enabled
      && (expandedComponentHover.hovered
          || (dragSession.active && !dragSession.windowDrag)
          || (root.windowDragActive && Shell.Theme.daylight))

  focus: dragSession.active
  Keys.onEscapePressed: dragSession.cancel()
  onCollapseProgressChanged: if (collapseProgress >= 0.56) dragSession.cancel()

  Minimap.DragSession {
    id: dragSession
    map: root
    parent: root.Window.window?.contentItem ?? root
  }

  Component.onCompleted: Minimap.DragCoordinator.register(root)
  Component.onDestruction: {
    // A sidebar removed mid-drag (display unplugged) releases the drag; the
    // window stays where it is.
    if (Minimap.DragCoordinator.sourceMap === root) {
      Shell.NiriMsg.finishWindowDrag(true, () => {});
      Minimap.DragCoordinator.end();
    }
    Minimap.DragCoordinator.unregister(root);
  }

  // Why a workspace or window action failed (Hyprland refused it, or it only
  // partly happened), shown on the display the action concerned.
  Connections {
    target: Shell.NiriMsg
    function onActionFailed(message, output, partial) {
      const here = root.screen?.name ?? "";
      // An output without a sidebar (unplugged) falls back to the focused one.
      const shownOn = output && Minimap.DragCoordinator.maps.some(m => m.screen?.name === output)
          ? output : Shell.NiriMsg.focusedOutputName;
      if (shownOn && here !== shownOn)
        return;
      actionNotice.text = message;
      actionNotice.shown = true;
      actionNoticeTimer.restart();
    }
  }
  Timer { id: actionNoticeTimer; interval: 5000; onTriggered: actionNotice.shown = false }
  Rectangle {
    id: actionNotice
    objectName: "minimapActionNotice"
    property bool shown: false
    property alias text: actionNoticeText.text
    z: 50
    x: 8
    width: root.width - 16
    y: root.height - height - 8
    height: actionNoticeText.implicitHeight + 16
    radius: 10
    color: Qt.rgba(0.08, 0.08, 0.08, 0.92)
    visible: opacity > 0.001 && root.collapseProgress < 0.56
    opacity: shown ? 1 : 0
    Behavior on opacity { Shell.HoverAnimation {} }
    Text {
      id: actionNoticeText
      x: 10; y: 8; width: parent.width - 20
      color: "#ffb4ab"
      wrapMode: Text.Wrap
      maximumLineCount: 3
      elide: Text.ElideRight
      font.family: Shell.Theme.fontFamily
      font.pixelSize: 12
    }
    TapHandler { onTapped: actionNotice.shown = false }
  }

  // A window dragged in from another display's minimap, drawn at the pointer.
  Minimap.WindowIcon {
    parent: root.Window.window?.contentItem ?? root
    z: 100
    width: Shell.Theme.workspaceControlSize
    height: width
    visible: root.windowDragActive && Minimap.DragCoordinator.pointerMap === root
        && Minimap.DragCoordinator.sourceMap !== root
    x: Minimap.DragCoordinator.pointer.x - (root.screen?.x ?? 0) - width / 2
    y: Minimap.DragCoordinator.pointer.y - (root.screen?.y ?? 0) - height / 2
    windowData: Minimap.DragCoordinator.source?.windowData ?? ({})
    lifted: true
    interactive: false
  }

  // The window drop target under a global point, resolved against this
  // minimap's own rows: Model.target(output, workspaceId, slot, creation).
  function windowTargetAt(globalX, globalY) {
    if (!expandedLayer.enabled || !expandedLayer.visible)
      return null;
    const p = expandedLayer.mapFromItem(null, globalX - (root.screen?.x ?? 0), globalY - (root.screen?.y ?? 0));
    if (p.x < 0 || p.x >= expandedLayer.width || p.y < 0 || p.y >= expandedLayer.height)
      return null;
    for (let i = 0; i < expandedRows.count; i++) {
      const row = expandedRows.itemAt(i);
      if (!row || !row.matchesOutput) continue;
      const top = expandedColumn.y + row.y - Shell.Theme.workspaceRowGap / 2;
      if (p.y >= top && p.y < top + Shell.Theme.workspaceRowPitch)
        return row.dropTarget(p.x);
    }
    return null;
  }

  // Where the dragged icon settles for a destination in this minimap.
  function snapPosition(target) {
    for (let i = 0; i < expandedRows.count; i++) {
      const row = expandedRows.itemAt(i);
      if (row && row.matchesOutput && Number(row.model.workspaceId) === target.workspaceId)
        return row.snapPosition(target.slot);
    }
    return null;
  }

  function windowIndex(workspaceId, items, index) {
    if (!root.windowDragActive)
      return index;
    const sourceIndex = items.findIndex(w => w.winId === root.draggedWindowId);
    const compact = index - (sourceIndex >= 0 && index > sourceIndex ? 1 : 0);
    return compact + (root.windowDrop?.workspaceId === workspaceId
        && compact >= root.windowDrop.slot ? 1 : 0);
  }

  function rowOffset(workspaceId, number) {
    if (!dragSession.active || dragSession.windowDrag || number < 1)
      return 0;
    const source = dragSession.source;
    if (source.workspaceId === workspaceId)
      return dragSession.rowOffset;
    const target = dragSession.destination?.number ?? source.number;
    if (number > source.number && number <= target) return -Shell.Theme.workspaceRowPitch;
    if (number < source.number && number >= target) return Shell.Theme.workspaceRowPitch;
    return 0;
  }

  function numberedWorkspaceCount() {
    let count = 0;
    for (let i = 0; i < Shell.NiriMsg.workspaceRows.count; i++) {
      const row = Shell.NiriMsg.workspaceRows.get(i);
      if (!root.screen || row.outputName === "" || row.outputName === root.screen.name)
        count = Math.max(count, row.sidebarWorkspaceNumber);
    }
    return count;
  }
  width: Shell.Theme.lerp(Shell.Theme.sidebarCardExpandedWidth,
      Shell.Theme.sidebarCardCollapsedWidth, widthProgress)
  height: Math.min(maximumHeight, naturalHeight)
  radius: Shell.Theme.daylight
      ? Shell.Theme.lerp(12, Shell.Theme.sidebarCardRadius, widthProgress)
      : Shell.Theme.sidebarCardRadius
  color: Shell.Theme.daylight
      ? (cardHovered || root.windowDragActive
          ? Shell.Theme.sidebarCardHoverFill : Shell.Theme.sidebarCardFill)
      : classicColor
  // The number hit target reaches ten pixels outside the expanded card. The
  // panel gutter contains it; clipping returns once the compact layer takes
  // over so its top and bottom rows retain the card radius.
  contentClipped: collapseProgress >= 0.56

  Item {
    id: expandedLayer
    x: -10
    width: root.width - x
    height: root.height
    clip: true
    opacity: root.expandedOpacity
    visible: opacity > 0.001
    enabled: root.collapseProgress < 0.56

    HoverHandler {
      id: expandedComponentHover
      enabled: expandedLayer.enabled
    }

    Column {
      id: expandedColumn
      // Own the complete screen-edge rail as part of every row. This keeps
      // pointer delivery inside the delegate instead of depending on an
      // out-of-bounds child to receive the grab.
      x: 0
      y: Shell.Theme.workspaceContentPadding
      width: expandedLayer.width
      spacing: Shell.Theme.workspaceRowGap

      Repeater {
        id: expandedRows
        model: Shell.NiriMsg.workspaceRows

        delegate: Item {
          id: expandedWorkspace

          required property int index
          required property var model

          readonly property bool matchesOutput: expandedWorkspace.model.outputName === ""
              || !root.screen
              || expandedWorkspace.model.outputName === root.screen.name
          readonly property var windowItems: {
            try {
              return JSON.parse(expandedWorkspace.model.windowsJson || "[]");
            } catch (e) {
              return [];
            }
          }
          readonly property var activeWindowId: model.activeWindowId
          property int windowStart: 0
          property var firstWindowId: null

          onWindowItemsChanged: Qt.callLater(updateWindowStart)
          onActiveWindowIdChanged: Qt.callLater(updateWindowStart)

          Connections {
            target: Minimap.DragCoordinator
            function onActiveChanged() {
              if (!Minimap.DragCoordinator.active) Qt.callLater(expandedWorkspace.updateWindowStart);
            }
          }

          function dropTarget(localX) {
            const slot = DragModel.slotAt(localX - windowViewport.x, windowCarousel.x,
                Shell.Theme.workspaceControlPitch, Shell.Theme.workspaceControlGap, remainingWindows);
            return DragModel.target(model.outputName || root.screen?.name || "", Number(model.workspaceId),
                slot, !!model.isCreationRow);
          }

          function snapPosition(slot) {
            return windowViewport.mapToItem(dragSession.parent,
                (slot - windowStart) * Shell.Theme.workspaceControlPitch, 0);
          }

          function updateWindowStart() {
            if (root.windowDragActive) return;
            const columns = Shell.Theme.workspaceGridColumns;
            // Preserve the first icon when windows before it close or move.
            const first = windowItems.findIndex(w => w.winId === firstWindowId);
            let start = first < 0 ? windowStart : first;
            const active = windowItems.findIndex(w => w.winId === activeWindowId);
            if (active >= 0)
              start = Math.max(active - columns + 1, Math.min(start, active));
            windowStart = Math.max(0, Math.min(start, windowItems.length - columns));
            firstWindowId = windowItems[windowStart]?.winId ?? null;
          }

          readonly property int carouselSlotCount: Math.max(
              Shell.Theme.workspaceGridColumns, windowItems.length + 1)
          readonly property int workspaceNumber:
              Number(expandedWorkspace.model.sidebarWorkspaceNumber) || 0
          readonly property bool dragTarget: root.windowDrop?.workspaceId === Number(model.workspaceId)
          readonly property int remainingWindows: windowItems.filter(w =>
              w.winId !== root.draggedWindowId).length
          property real dragOffset: root.rowOffset(Number(model.workspaceId), workspaceNumber)
          visible: matchesOutput
          width: expandedColumn.width
          height: matchesOutput ? Shell.Theme.workspaceControlSize : 0
          z: dragSession.source?.kind === "workspace"
              && dragSession.source.workspaceId === Number(model.workspaceId) ? 20 : 0
          transform: Translate { y: expandedWorkspace.dragOffset }
          Behavior on dragOffset {
            enabled: !dragSession.resetting && !dragSession.committing
                && !(dragSession.held && expandedWorkspace.z === 20)
            Shell.HoverAnimation { duration: Shell.Theme.workspaceDragSnapDuration }
          }
          Rectangle {
            x: Shell.Theme.workspaceActiveIndicatorInset - expandedLayer.x
            y: -Shell.Theme.workspaceRowGap / 2
            width: root.width - Shell.Theme.workspaceActiveIndicatorInset * 2
            height: Shell.Theme.workspaceRowPitch
            radius: 10
            color: expandedWorkspace.model.isActive || expandedWorkspace.dragTarget
                ? Shell.Theme.workspaceActiveIndicatorColor
                : Qt.rgba(Shell.Theme.workspaceActiveIndicatorColor.r,
                          Shell.Theme.workspaceActiveIndicatorColor.g,
                          Shell.Theme.workspaceActiveIndicatorColor.b, 0)

            Behavior on color {
              ColorAnimation {
                duration: Shell.Theme.sidebarWorkspaceSwitchDuration
                easing.type: Easing.OutCubic
              }
            }
          }

          MouseArea {
            x: -expandedLayer.x
            y: -Shell.Theme.workspaceRowGap / 2
            width: Shell.Theme.sidebarCardExpandedWidth
            height: Shell.Theme.workspaceRowPitch
            acceptedButtons: Qt.LeftButton
            cursorShape: Qt.PointingHandCursor
            onClicked: root.focusRequested(
                expandedWorkspace.model.workspaceIndex)
          }

          HoverHandler { id: rowHover; enabled: expandedLayer.enabled }

          Item {
            id: windowViewport
            x: Shell.Theme.lerp(Shell.Theme.workspaceContentPadding, 8, root.widthProgress) - expandedLayer.x
            width: Shell.Theme.workspaceGridColumns * Shell.Theme.workspaceControlSize
                + (Shell.Theme.workspaceGridColumns - 1)
                  * Shell.Theme.workspaceControlGap
            height: parent.height
            clip: true

            Item {
              id: windowCarousel
              x: -expandedWorkspace.windowStart
                  * Shell.Theme.workspaceControlPitch * (1 - root.widthProgress)
              width: expandedWorkspace.carouselSlotCount
                  * Shell.Theme.workspaceControlSize
                  + (expandedWorkspace.carouselSlotCount - 1)
                    * Shell.Theme.workspaceControlGap
              height: parent.height

              Behavior on x { enabled: root.collapseProgress === 0; Shell.Motion { duration: 220 } }

              Repeater {
                // Keep every window instantiated as the displayed range slides.
                model: expandedWorkspace.carouselSlotCount

                delegate: Item {
                  id: workspaceSlot

                  required property int index

                  readonly property bool hasWindow:
                      index < expandedWorkspace.windowItems.length
                  readonly property bool addSlot: index === expandedWorkspace.windowItems.length
                  readonly property bool plusRevealed: addSlot && root.collapseProgress === 0
                      && expandedWorkspace.matchesOutput && !dragSession.active && !root.windowDragActive
                      && (rowHover.hovered
                          || (!expandedWorkspace.model.outputHasWindows && expandedWorkspace.model.isCreationRow))
                  readonly property var windowData: hasWindow
                      ? expandedWorkspace.windowItems[index] : ({})
                  readonly property bool emptyWorkspaceActive: index === 0
                      && expandedWorkspace.windowItems.length === 0
                      && expandedWorkspace.model.isActive
                  readonly property int visualIndex: root.windowIndex(Number(expandedWorkspace.model.workspaceId),
                      expandedWorkspace.windowItems, index)
                  readonly property bool daylightDropTarget: Shell.Theme.daylight && expandedWorkspace.dragTarget
                      && visualIndex === root.windowDrop?.slot

                  visible: root.collapseProgress === 0 || (index >= expandedWorkspace.windowStart
                      && index < expandedWorkspace.windowStart + Shell.Theme.workspaceGridColumns)
                  x: visualIndex * Shell.Theme.workspaceControlPitch * (1 - root.widthProgress)
                  width: Shell.Theme.lerp(Shell.Theme.workspaceControlSize,
                      Shell.Theme.workspaceCollapsedControlSize, root.widthProgress)
                  height: width
                  Behavior on x {
                    enabled: root.collapseProgress === 0 && workspaceSlot.hasWindow && !dragSession.resetting && !dragSession.committing
                    Shell.HoverAnimation { duration: Shell.Theme.workspaceDragSnapDuration }
                  }

                  Rectangle {
                    anchors.fill: parent
                    visible: workspaceSlot.emptyWorkspaceActive && !workspaceSlot.daylightDropTarget
                    radius: width / 2
                    color: Shell.Theme.workspaceLauncherActive
                    border.color: Shell.Theme.daylight ? Shell.Theme.workspaceIconOutline : Shell.Theme.sidebarV3Border
                    border.width: Shell.Theme.sidebarStrokeWidth
                    antialiasing: true
                  }

                  Rectangle {
                    anchors.centerIn: parent
                    visible: root.collapseProgress === 0 && !workspaceSlot.hasWindow && !workspaceSlot.emptyWorkspaceActive
                        && (!(dragSession.active || root.windowDragActive) || Shell.Theme.daylight)
                        && !workspaceSlot.daylightDropTarget
                    width: Shell.Theme.workspacePlaceholderDotSize
                    height: Shell.Theme.workspacePlaceholderDotSize
                    radius: width / 2
                    color: Shell.Theme.workspacePlaceholderColor
                    opacity: workspaceSlot.plusRevealed ? 0 : 1
                    Behavior on opacity { Shell.HoverAnimation {} }
                  }

                  Minimap.WindowIcon {
                    id: windowIcon
                    anchors.fill: parent
                    visible: workspaceSlot.hasWindow
                    opacity: root.draggedWindowId !== ""
                        && root.draggedWindowId === workspaceSlot.windowData.winId ? 0 : 1
                    controlSize: workspaceSlot.width
                    windowData: workspaceSlot.windowData
                    onActivated: windowId => root.windowRequested(windowId)

                    DragHandler {
                      id: windowDrag
                      target: null
                      enabled: expandedLayer.enabled && workspaceSlot.hasWindow
                          && ((!dragSession.active && !root.windowDragActive) || active)
                      acceptedButtons: Qt.LeftButton
                      dragThreshold: 6
                      cursorShape: active ? Qt.ClosedHandCursor : Qt.PointingHandCursor
                      onActiveChanged: {
                        if (active) dragSession.begin({ kind: "window",
                            windowData: Object.assign({}, workspaceSlot.windowData),
                            slot: workspaceSlot.index,
                            workspaceId: Number(expandedWorkspace.model.workspaceId) },
                            windowIcon, centroid.scenePressPosition, centroid.scenePosition);
                        else dragSession.release();
                      }
                      onCentroidChanged: if (active) dragSession.move(centroid.scenePosition)
                      onCanceled: dragSession.cancel()
                    }
                  }

                  Minimap.PlusButton {
                    anchors.fill: parent
                    revealed: workspaceSlot.plusRevealed
                    enabled: expandedLayer.enabled && !dragSession.active && !root.windowDragActive
                    onActivated: root.addRequested(Number(expandedWorkspace.model.workspaceId))
                  }
                }
              }

              // Scrolls the row while a dragged window hovers near its edges.
              Item {
                id: windowDropScroll
                readonly property int lastStart: Math.max(0,
                    expandedWorkspace.remainingWindows + 1 - Shell.Theme.workspaceGridColumns)
                readonly property real viewportX: expandedLayer.mapFromItem(null,
                    Minimap.DragCoordinator.pointer.x - (root.screen?.x ?? 0),
                    Minimap.DragCoordinator.pointer.y - (root.screen?.y ?? 0)).x - windowViewport.x
                readonly property int scrollDirection:
                    viewportX < 12 && expandedWorkspace.windowStart > 0 ? -1
                    : viewportX > windowViewport.width - 12 && expandedWorkspace.windowStart < lastStart ? 1 : 0

                // Scrolling moves the slots beneath a stationary pointer.
                Connections {
                  target: windowCarousel
                  function onXChanged() {
                    if (expandedWorkspace.dragTarget)
                      Minimap.DragCoordinator.move(Minimap.DragCoordinator.pointer.x,
                                                   Minimap.DragCoordinator.pointer.y);
                  }
                }
                Timer {
                  interval: 300
                  repeat: true
                  running: expandedWorkspace.dragTarget && windowDropScroll.scrollDirection !== 0
                  onTriggered: {
                    expandedWorkspace.windowStart += windowDropScroll.scrollDirection;
                    expandedWorkspace.firstWindowId =
                        expandedWorkspace.windowItems[expandedWorkspace.windowStart]?.winId ?? null;
                  }
                }
              }

              Shape {
                visible: expandedWorkspace.dragTarget
                x: (root.windowDrop?.slot ?? 0) * Shell.Theme.workspaceControlPitch
                width: Shell.Theme.workspaceControlSize
                height: width
                antialiasing: true
                Behavior on x { Shell.HoverAnimation { duration: Shell.Theme.workspaceDragSnapDuration } }
                ShapePath {
                  strokeColor: Shell.Theme.daylight ? Shell.Theme.workspaceDropFill : "#4F3D64"
                  strokeWidth: 1
                  strokeStyle: Shell.Theme.daylight ? ShapePath.SolidLine : ShapePath.DashLine
                  dashPattern: [3, 3]
                  capStyle: ShapePath.RoundCap
                  fillColor: Shell.Theme.daylight ? Shell.Theme.workspaceDropFill : "#191919"
                  PathRectangle { x: 0.5; y: 0.5; width: 39; height: 39; radius: 20 }
                }
              }
            }
          }

          Item {
            anchors.verticalCenter: parent.verticalCenter
            z: 30
            width: 20
            height: Shell.Theme.workspaceRowPitch
            visible: root.workspaceNumbersRevealed && expandedWorkspace.workspaceNumber > 0
            Rectangle {
              x: 3
              anchors.verticalCenter: parent.verticalCenter
              width: 14
              height: 20
              radius: 10
              color: numberPointer.containsMouse || numberPointer.pressed
                  || (Shell.Theme.daylight && root.windowDragActive)
                  ? Shell.Theme.workspaceNumberDragBackground : Shell.Theme.workspaceNumberBackground
              antialiasing: true
              Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
              Text {
                anchors.fill: parent
                visible: !numberPointer.containsMouse && !workspaceDrag.active
                text: expandedWorkspace.workspaceNumber
                color: Shell.Theme.daylight ? "white" : Shell.Theme.sidebarV3Foreground
                font.family: Shell.Theme.plexFontFamily
                font.weight: Font.Medium
                font.pixelSize: 11
                font.letterSpacing: 0.11
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
              }
              Image {
                anchors.centerIn: parent
                width: 2
                height: 10
                visible: numberPointer.containsMouse || workspaceDrag.active
                source: "../assets/navigation/drag-handle.svg"
              }
            }
            MouseArea {
              id: numberPointer
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: workspaceDrag.active ? Qt.ClosedHandCursor : Qt.OpenHandCursor
              onClicked: root.focusRequested(expandedWorkspace.model.workspaceIndex)
            }
            DragHandler {
              id: workspaceDrag
              target: null
              enabled: expandedLayer.enabled && (!dragSession.active || active)
              acceptedButtons: Qt.LeftButton
              dragThreshold: 6
              xAxis.enabled: false
              onActiveChanged: {
                if (active) dragSession.begin({ kind: "workspace",
                    workspaceId: Number(expandedWorkspace.model.workspaceId),
                    number: expandedWorkspace.workspaceNumber,
                    count: root.numberedWorkspaceCount() }, expandedWorkspace,
                    centroid.scenePressPosition, centroid.scenePosition);
                else dragSession.release();
              }
              onCentroidChanged: if (active) dragSession.move(centroid.scenePosition)
              onCanceled: dragSession.cancel()
            }
          }

          DropArea {
            id: workspaceDrop
            parent: expandedLayer
            readonly property int workspaceId: Number(expandedWorkspace.model.workspaceId)
            readonly property int number: expandedWorkspace.workspaceNumber
            x: 0
            y: expandedColumn.y + expandedWorkspace.y - Shell.Theme.workspaceRowGap / 2
            width: 20
            height: Shell.Theme.workspaceRowPitch
            keys: ["pond-workspace"]
            enabled: expandedLayer.enabled && number > 0 && expandedWorkspace.matchesOutput
            onEntered: dragSession.destination = workspaceDrop
            onExited: if (dragSession.held && dragSession.destination === workspaceDrop) dragSession.destination = null
            onDropped: drop => drop.acceptProposedAction()
            function snapPosition() {
              return expandedColumn.mapToItem(dragSession.parent, 0, expandedWorkspace.y);
            }
          }
        }
      }
    }

    Column {
      visible: root.collapseProgress === 0
      x: Shell.Theme.workspaceContentPadding - expandedLayer.x
      y: Shell.Theme.workspaceContentPadding
          + root.expandedVisibleRowCount * Shell.Theme.workspaceRowPitch
      spacing: Shell.Theme.workspaceRowGap

      Repeater {
        model: Math.max(0, root.expandedRenderedRowCount
                        - root.expandedVisibleRowCount)

        delegate: Row {
          required property int index
          spacing: Shell.Theme.workspaceControlGap

          Repeater {
            model: Shell.Theme.workspaceGridColumns

            delegate: Item {
              required property int index
              width: Shell.Theme.workspaceControlSize
              height: Shell.Theme.workspaceControlSize

              Rectangle {
                anchors.centerIn: parent
                width: Shell.Theme.workspacePlaceholderDotSize
                height: Shell.Theme.workspacePlaceholderDotSize
                radius: width / 2
                color: Shell.Theme.workspacePlaceholderColor
              }
            }
          }
        }
      }
    }
  }

  Item {
    id: collapsedLayer
    width: Shell.Theme.sidebarCardCollapsedWidth
    height: collapsedColumn.height
    opacity: root.collapsedOpacity
    visible: opacity > 0.001
    enabled: root.collapseProgress >= 0.56

    Column {
      id: collapsedColumn
      y: Shell.Theme.lerp(Shell.Theme.workspaceContentPadding, 4, root.heightProgress)
      width: Shell.Theme.sidebarCardCollapsedWidth

      Repeater {
        model: Shell.NiriMsg.workspaceRows

        delegate: Item {
          id: collapsedWorkspace

          required property var model

          readonly property bool matchesOutput: collapsedWorkspace.model.outputName === ""
              || !root.screen
              || collapsedWorkspace.model.outputName === root.screen.name
          readonly property bool active: !!collapsedWorkspace.model.isActive
          readonly property int workspaceNumber:
              Number(collapsedWorkspace.model.sidebarWorkspaceNumber) || 0
          readonly property int displayNumber: workspaceNumber > 0
              ? workspaceNumber
              : (Number(collapsedWorkspace.model.sidebarRowOrdinal) || 0) + 1
          readonly property var windowItems: {
            try {
              return JSON.parse(collapsedWorkspace.model.windowsJson || "[]");
            } catch (e) {
              return [];
            }
          }
          readonly property int activeHeight:
              Shell.Theme.workspaceCollapsedHeaderHeight
              + windowItems.length * Shell.Theme.workspaceCollapsedControlPitch
              - (windowItems.length > 0 ? Shell.Theme.workspaceCollapsedControlGap : 0)
              + 4

          visible: matchesOutput
          width: Shell.Theme.sidebarCardCollapsedWidth
          property real targetHeight: !matchesOutput ? 0
              : (active && windowItems.length > 0 ? activeHeight : 41) + Shell.Theme.workspaceCollapsedControlGap
          height: matchesOutput ? Shell.Theme.lerp(Shell.Theme.workspaceRowPitch,
              targetHeight, root.heightProgress) : 0

          Behavior on targetHeight {
            enabled: root.collapseProgress === 1
            NumberAnimation {
              duration: Shell.Theme.sidebarWorkspaceSwitchDuration
              easing.type: Easing.OutCubic
            }
          }

          Rectangle {
            x: 4
            width: parent.width - 8
            height: Math.max(0, parent.height - Shell.Theme.workspaceCollapsedControlGap)
            radius: 12
            color: Shell.Theme.daylight ? Qt.rgba(1, 1, 1, 0.08) : Shell.Theme.workspaceActiveIndicatorColor
            opacity: collapsedWorkspace.active || collapsedRowPointer.containsMouse ? 1 : 0

            Behavior on opacity {
              NumberAnimation {
                duration: Shell.Theme.sidebarWorkspaceSwitchDuration
                easing.type: Easing.OutCubic
              }
            }
          }

          MouseArea {
            id: collapsedRowPointer
            y: -4
            width: parent.width
            height: parent.height
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.focusRequested(
                collapsedWorkspace.model.workspaceIndex)
          }

          FontMetrics {
            id: collapsedNumberMetrics
            font: collapsedNumber.font
          }

          Text {
            id: collapsedNumber
            readonly property real contentHeight: collapsedNumberMetrics.capitalHeight
                + (windowDots.visible ? 8 : 0)
            x: 0
            y: (41 - contentHeight) / 2
                + collapsedNumberMetrics.capitalHeight - baselineOffset
            width: parent.width
            text: collapsedWorkspace.displayNumber < 10
                ? "0" + collapsedWorkspace.displayNumber
                : String(collapsedWorkspace.displayNumber)
            color: Shell.Theme.sidebarV3Foreground
            opacity: collapsedWorkspace.active
                ? 1.0 : 0.5
            font.family: Shell.Theme.plexFontFamily
            font.weight: Font.Normal
            font.pixelSize: 13
            horizontalAlignment: Text.AlignHCenter

            Behavior on opacity {
              NumberAnimation {
                duration: Shell.Theme.sidebarWorkspaceSwitchDuration
                easing.type: Easing.OutCubic
              }
            }
          }

          Item {
            visible: collapsedWorkspace.active
            anchors.horizontalCenter: parent.horizontalCenter
            y: Shell.Theme.workspaceCollapsedHeaderHeight * root.heightProgress
            width: Shell.Theme.workspaceCollapsedControlSize
            height: Math.max(0, collapsedWorkspace.windowItems.length
                * Shell.Theme.workspaceCollapsedControlPitch - Shell.Theme.workspaceCollapsedControlGap)

            Repeater {
              // Keep delegates alive when focus, title or activity changes.
              // An array model destroys them on every new JSON snapshot.
              model: collapsedWorkspace.windowItems.length

              delegate: Minimap.WindowIcon {
                required property int index
                y: index * Shell.Theme.workspaceCollapsedControlPitch * root.heightProgress
                controlSize: Shell.Theme.workspaceCollapsedControlSize
                windowData: collapsedWorkspace.windowItems[index] || ({})
                onActivated: windowId => root.windowRequested(windowId)
              }
            }
          }

          Row {
            id: windowDots
            visible: !collapsedWorkspace.active
                && collapsedWorkspace.windowItems.length > 0
            y: collapsedNumber.y + collapsedNumber.baselineOffset + 4
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 2

            Repeater {
              model: Math.min(4, collapsedWorkspace.windowItems.length)

              delegate: Rectangle {
                id: appDot
                required property int index
                readonly property string imageFile: Quickshell.env("XDG_RUNTIME_DIR")
                    + "/pond-icon-" + Quickshell.processId + "-" + Qt.md5(String(appDot)) + ".png"
                width: 4
                height: 4
                radius: 2
                antialiasing: true
                readonly property string dotState: collapsedWorkspace.windowItems[index].dotState || ""
                color: dotState === "working" ? "#CBA6F7" : dotState === "unread" ? "#FF9F67"
                    : dotState === "read" ? "#CBE25B" : iconColor.colors[0] ?? "white"

                // ColorQuantizer needs a file; theme icons use an image-provider URL.
                Image {
                  id: colorSource
                  width: 32
                  height: 32
                  visible: false
                  asynchronous: true
                  source: Quickshell.iconPath(collapsedWorkspace.windowItems[appDot.index].iconName,
                      "application-x-executable")
                  readonly property bool ready: status === Image.Ready && Boolean(Window.window?.visible)
                  onReadyChanged: if (ready) Qt.callLater(capture)
                  onSourceChanged: {
                    iconColor.source = "";
                    if (ready) Qt.callLater(capture);
                  }
                  function capture() {
                    if (!ready)
                      return;
                    const requested = source.toString();
                    grabToImage(result => {
                      if (source.toString() === requested && result.saveToFile(appDot.imageFile))
                        iconColor.source = "file://" + appDot.imageFile;
                    });
                  }
                }

                ColorQuantizer {
                  id: iconColor
                  depth: 0
                  rescaleSize: 32
                }
                Component.onDestruction: {
                  colorSource.source = "";
                  Quickshell.execDetached(["rm", "-f", imageFile]);
                }
              }
            }
          }

          Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            y: parent.height - Shell.Theme.workspaceCollapsedControlGap / 2 - height / 2
            width: 16
            height: 1
            color: Shell.Theme.daylight ? Shell.Theme.sidebarCardOutline : Qt.alpha(Shell.Theme.sidebarV3Divider, 0.8)
            visible: collapsedWorkspace.y + collapsedWorkspace.height < collapsedColumn.height - 0.5
          }
        }
      }
    }
  }
}
