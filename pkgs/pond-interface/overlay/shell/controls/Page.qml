import QtQuick
import QtQuick.Controls
import ".." as Shell

Item {
  id: root
  property var stackController
  property bool interactive: true
  property string title: ""
  property string kind: ""
  property real maximumHeight: 500
  default property alias contents: body.data
  implicitWidth: Shell.PanelStyle.width
  implicitHeight: Math.min(maximumHeight, body.implicitHeight + 92)
  data: [Text {
    x: 20; y: 19; width: 245; text: root.title
    color: "white"; font.family: Shell.Theme.titleFontFamily; font.pixelSize: 20
  },
  Glyph { x: 276; y: 22; kind: root.kind; opacity: 0.65 },
  Flickable {
    x: 20; y: 76; width: parent.width - 40; height: Math.max(0, parent.height - y - 16)
    contentHeight: body.implicitHeight
    boundsBehavior: Flickable.StopAtBounds
    clip: true
    enabled: root.interactive
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 3 }
    Column { id: body; width: parent.width; spacing: 14 }
  }]
}
