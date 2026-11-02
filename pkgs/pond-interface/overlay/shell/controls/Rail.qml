pragma ComponentBehavior: Bound
import QtQuick
import "." as Controls
import QtQuick.Controls
import Quickshell
import ".." as Shell
import "../bluetooth" as Bluetooth
import "../updates" as Updates

Item {
  id: root
  property real collapseProgress: 0
  property real maximumHeight: 320
  property string selected: ""
  property var service: Controls.State
  property var bluetooth: Bluetooth.State
  property var updates: Updates.State
  signal requested(string kind)
  readonly property bool compact: collapseProgress >= 0.56
  readonly property real contentFade: 1 - Shell.Theme.ramp(collapseProgress, 0, 0.30)
  readonly property var hardware: service.hardware
  readonly property int connectedCount: bluetooth.devices.filter(d => d.connected).length
  readonly property int updateCount: updates.inputs.length + service.appUpdates.length
  readonly property var cards: [
    {key: "dictation", title: "Dictation", detail: service.dictationBusy ? "Working…"
        : service.dictation.live ? "Live · listening" : service.dictation.batch ? "Batch · recording" : !service.dictation.liveAvailable && !service.dictation.batchAvailable ? "Unavailable" : "Ready to listen",
      active: service.dictation.live || service.dictation.batch, alert: !!service.dictationError},
    {key: "awake", title: "Keep awake", detail: service.awakeSummary, active: service.keepAwake, alert: !!service.awakeError},
    {key: "hardware", title: "Hardware", detail: service.telemetryReady ? "Live usage" : "Connecting…", active: false, alert: false},
    {key: "bluetooth", title: "Bluetooth", detail: !bluetooth.available ? "No adapter" : bluetooth.blocked ? "Blocked"
        : !bluetooth.enabled ? "Off" : connectedCount ? connectedCount + " connected" : "No connections",
      active: connectedCount > 0, alert: !!bluetooth.errorText},
    {key: "updates", title: "Updates", detail: updates.launching ? "Opening update…" : updates.status === "updating" ? "Updating…"
        : updates.checking || service.appChecking ? "Checking…" : updates.unresolved ? "Action needed"
        : updates.status === "restart" ? "Restart needed" : updateCount ? updateCount + " available"
        : updates.error ? "Check failed" : updates.lastChecked ? (service.appUpdatesKnown ? "Up to date" : "System up to date") : "Not checked yet",
      active: updateCount > 0 || updates.status === "updating" || updates.unresolved,
      alert: !!updates.error || !!service.appUpdateError}
  ]
  width: Shell.Theme.lerp(Shell.Theme.sidebarCardExpandedWidth, Shell.Theme.sidebarCardCollapsedWidth,
                         Shell.Theme.collapseWidth(collapseProgress))
  height: Math.min(maximumHeight, stack.height)
  readonly property Region blurRegion: Region { item: viewport; radius: Shell.Theme.sidebarCardRadius }

  Flickable {
    id: viewport
    anchors.fill: parent
    contentHeight: stack.height
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    flickableDirection: Flickable.VerticalFlick
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 3 }
    Column {
      id: stack
      width: root.width
      spacing: Shell.Theme.sidebarCardGap
      Repeater {
        model: root.cards
        delegate: Shell.Card {
          id: card
          required property var modelData
          objectName: "control-" + modelData.key
          width: stack.width
          height: Shell.Theme.lerp(modelData.key === "hardware" ? 76 : 52, 44,
                                   Shell.Theme.collapseHeight(root.collapseProgress))
          classicColor: root.selected === modelData.key ? "#303038" : Shell.Theme.sidebarV3Background
          Accessible.role: Accessible.Button
          Accessible.name: modelData.title + ": " + modelData.detail
          Accessible.onPressAction: root.requested(modelData.key)
          activeFocusOnTab: true
          Keys.onReturnPressed: root.requested(modelData.key)
          Keys.onSpacePressed: root.requested(modelData.key)
          Rectangle {
            anchors.fill: parent; radius: card.radius; color: "transparent"
            border.width: card.activeFocus ? 1 : 0; border.color: Shell.PanelStyle.accent
          }
          Glyph {
            kind: card.modelData.key
            x: Shell.Theme.lerp(12, (card.width - width) / 2, Shell.Theme.collapseWidth(root.collapseProgress))
            y: root.compact ? (card.height - height) / 2 : 12
            opacity: card.modelData.active || card.cardHovered ? 1 : 0.65
          }
          Column {
            x: 40; y: 10; width: Math.max(0, parent.width - 48)
            opacity: root.contentFade; visible: opacity > 0
            spacing: 3
            Text {
              width: parent.width; text: card.modelData.title
              font.family: Shell.Theme.fontFamily; font.pixelSize: 12; font.weight: Font.Medium
              color: Shell.Theme.sidebarV3Foreground; elide: Text.ElideRight
            }
            Text {
              width: parent.width; text: card.modelData.detail
              font.family: Shell.Theme.fontFamily; font.pixelSize: 10
              color: card.modelData.active ? Shell.PanelStyle.enabledMark : Shell.Theme.sidebarV3Foreground
              opacity: card.modelData.active ? 0.9 : 0.55; elide: Text.ElideRight
            }
          }
          Row {
            visible: card.modelData.key === "hardware" && opacity > 0
            opacity: root.contentFade
            x: 12; y: 46; spacing: 6
            Repeater {
              model: [{name: "CPU", value: root.hardware.cpu}, {name: "RAM", value: root.hardware.memory?.percent},
                      {name: "GPU", value: root.hardware.gpu?.percent}]
              delegate: Column {
                required property var modelData
                width: 40; spacing: 4
                Text {
                  text: modelData.name + " " + root.service.percent(modelData.value)
                  font.family: Shell.Theme.fontFamily; font.pixelSize: 9; color: Shell.Theme.sidebarV3Foreground; opacity: 0.75
                }
                Rectangle {
                  width: 38; height: 2; radius: 1; color: "#35ffffff"
                  Rectangle {
                    width: parent.width * Math.min(100, Math.max(0, modelData.value || 0)) / 100
                    height: 2; radius: 1; color: Shell.PanelStyle.accent
                    Behavior on width { NumberAnimation { duration: 300 } }
                  }
                }
              }
            }
          }
          Rectangle {
            visible: card.modelData.active || card.modelData.alert
            x: parent.width - 10; y: 6; width: 5; height: 5; radius: 3
            color: card.modelData.alert ? "#FFB78A" : Shell.PanelStyle.enabledMark
          }
          MouseArea {
            id: pointer; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
            onClicked: root.requested(card.modelData.key)
          }
          ToolTip.visible: root.compact && pointer.containsMouse
          ToolTip.delay: 600
          ToolTip.text: card.modelData.title + " · " + card.modelData.detail
        }
      }
    }
  }
}
