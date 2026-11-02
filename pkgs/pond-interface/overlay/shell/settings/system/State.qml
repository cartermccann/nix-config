pragma Singleton
import QtQuick
import ".." as Settings
import Quickshell
import Quickshell.Io

Singleton {
  id: root
  readonly property string helper: decodeURIComponent(Qt.resolvedUrl("control.py").toString().replace(/^file:\/\//, ""))
  property var languages: []
  property var timezones: []
  property var selectedLanguages: []
  property var languageNames: []
  property string timezone: ""
  property string timezoneLabel: ""
  property bool timezoneManaged: false
  property bool customized: false
  property string error: ""
  property var mouse: ({speed: 0.5, scroll: 1, acceleration: true, naturalTouchpad: true, naturalMouse: false})
  readonly property bool busy: action.running || queue.length > 0
  readonly property bool active: Settings.State.opened && ["system", "mouse", "keyboard", "timezone"].indexOf(Settings.State.page) >= 0
  // Every mutation, and every local mouse edit, bumps the generation. A load
  // started before the latest one describes the old settings and is dropped
  // (then reloaded), so it can never reset the controls or feed stale values
  // into the next mouse change. Actions run one at a time, in order.
  property int generation: 0
  property var queue: []
  property bool reloadWanted: false
  // A load clears only an error it caused itself; an action's error stays
  // until the next change or until the settings are reopened.
  property bool loadError: false
  onActiveChanged: if (active) { error = ""; loadError = false; reload(); }
  Process {
    id: query
    property int generation: -1
    command: ["python3", root.helper, "load"]
    stdout: StdioCollector {
      onStreamFinished: {
        if (query.generation === root.generation && !action.running) root.accept(text, true);
        else root.reloadWanted = true;
      }
    }
    onExited: if (root.reloadWanted) Qt.callLater(root.reload)
  }
  Process {
    id: action
    stdout: StdioCollector { onStreamFinished: root.accept(text, false) }
    // The result reflects the change; reload anyway so a failed or partial
    // change cannot leave the controls showing values that were not applied.
    onExited: { root.reloadWanted = true; Qt.callLater(root.next); }
  }
  Timer {
    id: mouseCommit
    interval: 300
    onTriggered: root.enqueue(["mouse", root.mouse.speed, root.mouse.scroll, root.mouse.acceleration,
                               root.mouse.naturalTouchpad, root.mouse.naturalMouse].map(String))
  }
  function accept(text, loaded) {
    try {
      const result = JSON.parse(text);
      if (result.error) { error = result.error; loadError = loaded; return; }
      if (result.languages) languages = result.languages;
      if (result.timezones) timezones = result.timezones;
      if (timezone && timezone !== result.timezone) Date.timeZoneUpdated();
      selectedLanguages = result.selectedLanguages;
      languageNames = result.languageNames;
      timezone = result.timezone;
      timezoneLabel = result.timezoneLabel;
      timezoneManaged = !!result.timezoneManaged;
      customized = !!result.customized;
      // A pending local edit is newer than any result.
      if (!mouseCommit.running && !queue.some(args => args[0] === "mouse")) mouse = result.mouse;
      if (loaded && loadError) { error = ""; loadError = false; }
    } catch (e) { error = "Could not read system settings"; loadError = loaded; }
  }
  function reload() {
    if (action.running || queue.length) { reloadWanted = true; return; }
    if (query.running) { reloadWanted = true; return; }
    reloadWanted = false;
    query.generation = generation;
    query.running = true;
  }
  function enqueue(args) {
    generation += 1;
    error = "";
    loadError = false;
    // A newer mouse change supersedes a queued one.
    queue = (args[0] === "mouse" ? queue.filter(q => q[0] !== "mouse") : queue).concat([args]);
    next();
  }
  function next() {
    if (action.running) return;
    if (!queue.length) { if (reloadWanted) reload(); return; }
    const args = queue[0];
    queue = queue.slice(1);
    generation += 1;
    action.command = ["python3", root.helper].concat(args);
    action.running = true;
  }
  function toggleLanguage(id) { enqueue(["keyboard", id]); }
  function resetSaved() { enqueue(["reset"]); }
  function setTimezone(id) { if (id !== timezone) enqueue(["timezone", id]); }
  function setMouse(key, value) {
    generation += 1;
    mouse = Object.assign({}, mouse, {[key]: value});
    mouseCommit.restart();
  }
}
