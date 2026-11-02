import QtQuick
Image {
  property string kind: ""
  source: kind === "bluetooth" ? Qt.resolvedUrl("../assets/bluetooth/bluetooth.svg")
      : Qt.resolvedUrl("icons/" + kind + ".svg")
  sourceSize: Qt.size(48, 48)
  width: 20; height: 20
  fillMode: Image.PreserveAspectFit
  smooth: true
}
