import QtQuick
import ".." as Shell
Column {
  id: root
  property string label: ""
  property string value: "—"
  property var percent: null
  width: parent.width
  spacing: 8
  Item {
    width: parent.width; height: 19
    Text { text: root.label; color: "white"; opacity: 0.65; font.family: Shell.Theme.fontFamily; font.pixelSize: 13 }
    Text {
      anchors.right: parent.right; text: root.value; color: "white"
      font.family: Shell.Theme.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
    }
  }
  Rectangle {
    width: parent.width; height: 4; radius: 2; color: Shell.PanelStyle.border
    Rectangle {
      width: parent.width * Math.max(0, Math.min(100, root.percent || 0)) / 100
      height: 4; radius: 2; color: Shell.PanelStyle.accent
      Behavior on width { NumberAnimation { duration: 300 } }
    }
  }
}
