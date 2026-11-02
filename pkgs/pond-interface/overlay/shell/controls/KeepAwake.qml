import QtQuick
import "." as Controls
import ".." as Shell
Page {
  id: root
  property var service: Controls.State
  title: "Keep awake"; kind: "awake"
  Label {
    text: root.service.awakeSummary
    font.pixelSize: 18; opacity: 1
    color: root.service.keepAwake ? Shell.PanelStyle.enabledMark : "white"
  }
  Label { text: "Pause automatic locking, screen blanking and sleep. Manual lock and suspend still work." }
  Row {
    width: parent.width; spacing: 8
    Repeater {
      model: [30, 60, 120]
      delegate: Shell.ActionButton {
        required property int modelData
        width: (parent.width - 16) / 3
        text: modelData < 60 ? "30 min" : modelData / 60 + " hour" + (modelData > 60 ? "s" : "")
        interactive: root.interactive && !root.service.awakeBusy
        onClicked: root.service.setAwake(modelData)
      }
    }
  }
  Shell.ActionButton {
    width: parent.width; text: "Until Pond exits"
    interactive: root.interactive && !root.service.awakeBusy
    onClicked: root.service.setAwake(0)
  }
  Shell.ActionButton {
    width: parent.width; text: "Resume automatic idle"
    interactive: root.interactive && root.service.keepAwake && !root.service.awakeBusy
    onClicked: root.service.setAwake(-1)
  }
  Label { text: "Temporary for this shell. Logging out or restarting Pond restores your idle settings." }
  Label { visible: !!text; text: root.service.awakeError; color: "#FFB78A"; opacity: 1 }
}
