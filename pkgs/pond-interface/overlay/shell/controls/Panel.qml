import QtQuick
import "." as Controls
import ".." as Shell
import "../updates" as Updates

Shell.CardStack {
  id: root
  property string kind: ""
  property real maximumHeight: 540
  property var service: Controls.State
  property var updates: Updates.State
  pageComponents: ({drawer: page})
  Component {
    id: page
    Loader {
      property var stackController
      property bool interactive: true
      sourceComponent: root.kind === "dictation" ? dictation : root.kind === "awake" ? awake
          : root.kind === "hardware" ? hardware : updatesPage
      onLoaded: {
        item.stackController = root;
        item.interactive = Qt.binding(() => interactive);
        item.maximumHeight = Qt.binding(() => Math.max(160, root.maximumHeight - 22));
      }
    }
  }
  Component { id: dictation; Dictation { service: root.service } }
  Component { id: awake; KeepAwake { service: root.service } }
  Component { id: hardware; Hardware { service: root.service } }
  Component { id: updatesPage; UpdatePage { service: root.service; updates: root.updates } }
}
