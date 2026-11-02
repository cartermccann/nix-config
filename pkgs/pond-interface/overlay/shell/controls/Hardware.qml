import QtQuick
import "." as Controls
import Quickshell
import ".." as Shell
Page {
  id: root
  property var service: Controls.State
  readonly property var data: service.hardware
  title: "Hardware"; kind: "hardware"
  Metric { label: "CPU"; value: root.service.percent(root.data.cpu); percent: root.data.cpu }
  Metric {
    label: "Memory"; percent: root.data.memory?.percent
    value: root.data.memory ? root.service.gib(root.data.memory.used) + " / " + root.service.gib(root.data.memory.total) : "Unavailable"
  }
  Metric { label: "GPU"; value: root.service.percent(root.data.gpu?.percent); percent: root.data.gpu?.percent }
  Label {
    text: root.data.gpu ? root.data.gpu.name
        + (typeof root.data.gpu.temperature === "number" ? " · " + root.data.gpu.temperature + "°C" : "")
        + (typeof root.data.gpu.usedMiB === "number" && typeof root.data.gpu.totalMiB === "number"
          ? "\n" + (root.data.gpu.usedMiB / 1024).toFixed(1) + " / " + (root.data.gpu.totalMiB / 1024).toFixed(1) + " GiB VRAM" : "")
        : "GPU telemetry unavailable"
  }
  Metric {
    label: "Home disk"; percent: root.data.disk?.percent
    value: root.data.disk ? root.service.gib(root.data.disk.used) + " / " + root.service.gib(root.data.disk.total) : "Unavailable"
  }
  Label { text: "Usage refreshes every 5 seconds; GPU every 10 seconds." }
  Shell.ActionButton {
    width: parent.width; text: "Open system monitor"; interactive: root.interactive
    onClicked: { root.stackController.closeAll(); Quickshell.execDetached(["ghostty", "--class=TUI.float", "-e", "btop"]); }
  }
}
