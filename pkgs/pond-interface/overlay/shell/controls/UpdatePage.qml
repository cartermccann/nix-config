import QtQuick
import "." as Controls
import ".." as Shell
import "../updates" as Updates
Page {
  id: root
  property var service: Controls.State
  property var updates: Updates.State
  title: "Available updates"; kind: "updates"
  Label {
    text: root.updates.status === "updating" ? (root.updates.message || "Updating system…")
        : root.updates.status === "restart" ? "Restart needed to finish the update."
        : root.updates.unresolved ? "An update needs attention. Review it to continue."
        : root.updates.checking ? "Checking for system updates…"
        : root.updates.lastChecked ? "Checked " + root.updates.lastChecked : "No completed system check yet."
  }
  Label { visible: !!text; text: root.updates.error || root.service.appUpdateError; color: "#FFB78A"; opacity: 1 }
  Text {
    text: "SYSTEM · " + root.updates.inputs.length; color: "white"
    font.family: Shell.Theme.fontFamily; font.pixelSize: 11; font.letterSpacing: 1; opacity: 0.6
  }
  Repeater {
    model: root.updates.inputs
    delegate: Shell.DetailLine {
      required property var modelData
      width: parent.width; label: modelData.input; value: modelData.from + " → " + modelData.to
    }
  }
  Label { visible: root.updates.inputs.length === 0; text: root.updates.unresolved ? "Changes already downloaded; activation is pending." : "No new revisions listed." }
  Shell.ActionButton {
    width: parent.width; text: root.updates.launching ? "Opening Ghostty…" : root.updates.unresolved ? "Resume system update" : "Update all"
    interactive: root.interactive && root.updates.status !== "updating" && !root.updates.launching
    onClicked: { root.stackController.closeAll(); root.updates.start("all"); }
  }
  Label { text: "Opens a terminal to review, build and confirm before applying." }
  Text {
    text: "APP VERSIONS · " + root.service.appUpdates.length; color: "white"
    font.family: Shell.Theme.fontFamily; font.pixelSize: 11; font.letterSpacing: 1; opacity: 0.6
  }
  Repeater {
    model: root.service.appUpdates
    delegate: Row {
      required property string modelData
      width: parent.width
      spacing: 8
      Label { width: parent.width - 74; text: parent.modelData; opacity: 0.9 }
      Shell.ActionButton {
        width: 66; text: "Update"
        interactive: root.interactive && root.updates.status !== "updating" && !root.updates.launching
            && !root.updates.unresolved && !!root.updates.appTarget(parent.modelData)
        onClicked: {
          const target = root.updates.appTarget(parent.modelData);
          root.stackController.closeAll();
          root.updates.start(target);
        }
      }
    }
  }
  Label {
    visible: root.service.appUpdates.length === 0
    text: root.service.appUpdatesKnown ? "No app updates in the last daily check." : "App versions have not been checked yet."
  }
  Label { visible: root.service.appUpdates.length > 0; text: "Update one app here, or update apps and system inputs together with Update all." }
  Shell.ActionButton {
    width: parent.width
    text: root.updates.checking || root.service.appChecking ? "Checking…" : "Check for updates"
    interactive: root.interactive && !root.updates.checking && !root.service.appChecking && root.updates.status !== "updating" && !root.updates.launching
    onClicked: { root.updates.refresh(); root.service.refreshApps(); }
  }
}
