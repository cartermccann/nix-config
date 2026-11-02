import QtQuick
import "." as Controls
import ".." as Shell
Page {
  id: root
  property var service: Controls.State
  title: "Dictation"; kind: "dictation"
  Label {
    text: root.service.dictationBusy ? "Preparing or finishing dictation…"
        : root.service.dictation.live ? "Listening · live transcription"
        : root.service.dictation.batch ? "Recording · transcribe when you stop"
        : "Speak into the default microphone. Text goes into your focused application."
    color: root.service.dictation.live || root.service.dictation.batch ? Shell.PanelStyle.enabledMark : "white"
  }
  Shell.DetailLine { width: parent.width; label: "Live"; value: "Types as you speak · Super+Alt+Shift+L" }
  Shell.ActionButton {
    width: parent.width
    text: root.service.dictation.live ? "Stop live dictation" : "Start live dictation"
    interactive: root.interactive && !root.service.dictationBusy && !root.service.dictation.batch && root.service.dictation.liveAvailable
    onClicked: { root.stackController.closeAll(); root.service.toggleDictation("live"); }
  }
  Shell.DetailLine { width: parent.width; label: "Batch"; value: "Transcribes after recording · Super+Alt+L" }
  Shell.ActionButton {
    width: parent.width
    text: root.service.dictation.batch ? "Stop and transcribe" : "Start batch recording"
    interactive: root.interactive && !root.service.dictationBusy && !root.service.dictation.live && root.service.dictation.batchAvailable
    onClicked: { root.stackController.closeAll(); root.service.toggleDictation("batch"); }
  }
  Label { text: "The panel closes before recording or typing. Use the same button or keyboard shortcut to stop." }
  Label { visible: !!text; text: root.service.dictationError; color: "#FFB78A"; opacity: 1 }
  Label {
    visible: !root.service.dictation.liveAvailable && !root.service.dictation.batchAvailable
    text: "Dictation is unavailable in this session."
  }
}
