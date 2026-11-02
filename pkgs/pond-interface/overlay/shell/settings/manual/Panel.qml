pragma ComponentBehavior: Bound
import QtQuick
import ".." as Settings
import "../.." as Shell
import "shortcuts.js" as Shortcuts

// The user manual. Upstream draws it as one SVG that describes Pond on Niri
// (scrolling columns, installing apps from the launcher); here the shortcuts
// are real QML rows built from shortcuts.js, which a test checks against the
// generated Hyprland config. The artwork at the top and bottom is cut from
// upstream's manual.svg, as upstream's own panel cuts it.
Settings.Modal {
  id: root
  objectName: "manualPanel"
  title: ""
  implicitWidth: 500
  implicitHeight: Math.min(672, availableHeight)
  bodyTop: 0
  contentHeight: manual.height
  roundedContent: true
  closeButtonY: -17
  closeLabel: "Close manual"
  Accessible.role: Accessible.Dialog
  Accessible.name: "User Manual"
  onBackRequested: Settings.State.back()
  Keys.onEscapePressed: Settings.State.back()
  Keys.onDownPressed: scrollBy(56)
  Keys.onUpPressed: scrollBy(-56)
  Keys.onPressed: event => {
    if (event.key === Qt.Key_PageDown || event.key === Qt.Key_PageUp) {
      scrollBy((scrollItem.height - 40) * (event.key === Qt.Key_PageDown ? 1 : -1));
      event.accepted = true;
    } else if (event.key === Qt.Key_Home) {
      scrollItem.contentY = 0;
      event.accepted = true;
    } else if (event.key === Qt.Key_End) {
      scrollItem.contentY = Math.max(0, contentHeight - scrollItem.height);
      event.accepted = true;
    }
  }

  function scrollBy(distance) {
    scrollItem.contentY = Math.max(0, Math.min(contentHeight - scrollItem.height, scrollItem.contentY + distance));
  }

  readonly property color accent: Shell.PanelStyle.accent
  readonly property color outline: "#4A4A4A"

  // Inline components do not see this file's ids, hence the literals below.
  // A horizontal band of upstream's manual artwork.
  component Artwork: Item {
    property real from: 0
    width: 500
    clip: true
    Image {
      y: -parent.from
      width: 500; height: 2984
      source: Qt.resolvedUrl("../../assets/manual/manual.svg")
      sourceSize.width: width * Screen.devicePixelRatio
    }
  }

  component KeyCap: Rectangle {
    id: cap
    required property string label
    readonly property bool mouse: label.startsWith("mouse-")
    readonly property bool modifier: ["spr", "shift", "ctrl", "alt"].indexOf(label) >= 0
    height: 40
    width: Math.max(40, text.implicitWidth + (modifier ? 20 : 22))
    radius: 6
    color: "transparent"
    border.width: 1
    border.color: "#4A4A4A"
    Accessible.ignored: true
    // Modifiers sit in a ring, as on upstream's key caps.
    Rectangle {
      anchors.centerIn: parent
      visible: cap.modifier
      width: parent.width - 8; height: 32
      radius: 16
      color: "transparent"
      border.width: 1
      border.color: "#4A4A4A"
    }
    // A mouse with the pressed button filled.
    Rectangle {
      id: mouseBody
      anchors.centerIn: parent
      visible: cap.mouse
      width: 16; height: 22; radius: 8
      color: "transparent"
      border.width: 1.5
      border.color: "white"
      Rectangle {
        x: cap.label === "mouse-left" ? 2 : parent.width / 2
        y: 2
        width: parent.width / 2 - 2; height: 8
        radius: 3
        color: "white"
      }
    }
    Text {
      id: text
      anchors.centerIn: parent
      visible: !cap.mouse
      text: cap.label
      color: "white"
      font.family: Shell.Theme.fontFamily
      font.pixelSize: cap.modifier ? 11 : 13
      font.weight: Font.DemiBold
    }
  }

  component Heading: Item {
    id: heading
    required property string text
    width: 500; height: 52
    Rectangle {
      x: 0; y: 34
      width: 500; height: 1
      color: Shell.PanelStyle.accent
    }
    Rectangle {
      x: 28; y: 2
      width: label.implicitWidth + 4; height: 33
      color: Shell.PanelStyle.accent
    }
    Text {
      id: label
      x: 30; y: 0
      text: heading.text
      color: "#1D1D1D"
      font.family: Shell.Theme.titleFontFamily
      font.pixelSize: 32
      font.letterSpacing: -0.64
      Accessible.role: Accessible.Heading
      Accessible.name: heading.text
    }
  }

  Item {
    id: manual
    objectName: "manualContent"
    width: 500
    height: column.y + column.height

    Column {
      id: column
      width: 500

      // Logo, butterfly, "User Manual" and the window illustration.
      Artwork { from: 0; height: 800 }

      Repeater {
        model: Shortcuts.sections
        delegate: Column {
          id: section
          required property var modelData
          width: 500
          topPadding: 36
          spacing: 0

          Heading { text: section.modelData.title }
          Text {
            x: 30
            width: 440
            visible: text.length > 0
            topPadding: 12
            bottomPadding: 8
            text: section.modelData.intro
            wrapMode: Text.WordWrap
            color: root.accent
            font.family: Shell.Theme.fontFamily
            font.pixelSize: 15
            lineHeight: 1.3
          }
          Repeater {
            model: section.modelData.rows
            delegate: Item {
              id: row
              required property var modelData
              width: 500; height: 56
              Accessible.role: Accessible.StaticText
              Accessible.name: modelData.label + ": " + modelData.caps.join(" ")
              Text {
                x: 30
                anchors.verticalCenter: parent.verticalCenter
                width: 440 - caps.width - 12
                elide: Text.ElideRight
                text: row.modelData.label
                color: "white"
                font.family: Shell.Theme.fontFamily
                font.pixelSize: 15
              }
              Row {
                id: caps
                anchors { right: parent.right; rightMargin: 30; verticalCenter: parent.verticalCenter }
                spacing: 6
                Repeater {
                  model: row.modelData.caps
                  delegate: KeyCap { required property string modelData; label: modelData }
                }
              }
            }
          }
          Rectangle {
            x: 30
            visible: !!section.modelData.note
            width: 440
            height: visible ? note.implicitHeight + 32 : 0
            radius: 12
            color: "transparent"
            border.width: 1
            border.color: root.outline
            Text {
              id: note
              x: 16; y: 16
              width: parent.width - 32
              text: section.modelData.note || ""
              wrapMode: Text.WordWrap
              color: "#B5B5B5"
              font.family: Shell.Theme.fontFamily
              font.pixelSize: 14
              lineHeight: 1.3
            }
          }
        }
      }

      Item { width: 500; height: 40 }
      // The lily pad and upstream's thanks.
      Artwork { from: 2600; height: 300 }
      Item {
        width: 500; height: 90
        Row {
          anchors.horizontalCenter: parent.horizontalCenter
          y: 10
          spacing: 10
          FooterButton { service: "discord" }
          FooterButton { service: "x" }
          FooterButton { service: "mail" }
        }
      }
    }
  }
}
