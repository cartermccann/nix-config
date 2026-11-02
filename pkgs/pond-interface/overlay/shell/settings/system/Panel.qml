import QtQuick
import "." as System
import ".." as Settings
import "../.." as Shell

Settings.Modal {
  id: root
  objectName: "systemPanel"
  title: "System"; bodyTop: 92
  contentHeight: fields.height + 30
  onBackRequested: Settings.State.back()
  Keys.onEscapePressed: Settings.State.back()
  Column {
    id: fields
    x: 30; width: root.width - 60; spacing: 8
    Settings.Field {
      objectName: "systemMouse"
      width: parent.width; navigation: true
      title: "Mouse sensitivity"; value: Math.round(System.State.mouse.speed * 100) + "%"
      onClicked: Settings.State.openPage("mouse")
    }
    Settings.Field {
      objectName: "systemLock"
      width: parent.width; navigation: true
      title: "Lock & sleep"; value: System.Idle.label(System.Idle.current.lock)
      onClicked: Settings.State.openPage("lock")
    }
    Settings.Field {
      objectName: "systemKeyboard"
      width: parent.width; navigation: true
      title: "Keyboard language"; value: System.State.languageNames.join(", ")
      onClicked: Settings.State.openPage("keyboard")
    }
    Settings.Field {
      objectName: "systemTimezone"
      width: parent.width; navigation: true
      title: "Timezone"; value: System.State.timezoneLabel + (System.State.timezoneManaged ? " · NixOS" : "")
      // A declared time.timeZone cannot be changed from the session.
      onClicked: {
        if (System.State.timezoneManaged)
          System.State.error = "The timezone is set by NixOS (time.timeZone). Change it in your system configuration and rebuild.";
        else Settings.State.openPage("timezone");
      }
    }
    Settings.Field {
      objectName: "systemReset"
      width: parent.width; navigation: true
      visible: System.State.customized
      enabled: !System.State.busy
      title: "Saved in Pond"; value: "Reset to system defaults"
      onClicked: System.State.resetSaved()
    }
    Text {
      width: parent.width; visible: !!System.State.error
      text: System.State.error; color: "#ffb4ab"; wrapMode: Text.Wrap
      font.family: Shell.Theme.fontFamily; font.pixelSize: 13
    }
  }
}
