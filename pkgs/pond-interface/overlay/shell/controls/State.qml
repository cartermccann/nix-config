pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io

Singleton {
  id: root
  readonly property string helper: Quickshell.shellDir + "/controls/control.py"
  property var hardware: ({})
  property var dictation: ({live: false, batch: false, liveAvailable: false, batchAvailable: false})
  property bool telemetryReady: false
  property string dictationError: ""
  property string dictationMode: ""
  readonly property bool dictationBusy: dictationDelay.running || dictationAction.running
  property bool keepAwake: false
  property bool awakeRequested: false
  property double awakeUntil: 0
  property double now: Date.now()
  property string awakeError: ""
  readonly property bool awakeBusy: awakeRequested !== keepAwake || (!awakeRequested && inhibitor.running)
  readonly property string awakeSummary: !keepAwake ? (awakeRequested ? "Starting…" : "Automatic idle")
      : awakeUntil ? Math.max(1, Math.ceil((awakeUntil - now) / 60000)) + " min remaining" : "Until shell exits"
  property string appUpdateError: ""
  property var appUpdates: []
  property bool appUpdatesKnown: false
  readonly property bool appChecking: appCheck.running
  signal panelCommand(string kind, string outputName)

  function percent(value) { return typeof value === "number" && isFinite(value) ? Math.round(value) + "%" : "—"; }
  function gib(value) { return typeof value === "number" && isFinite(value) ? (value / 1073741824).toFixed(1) + " GiB" : "—"; }
  function setAwake(minutes) {
    if (awakeBusy) return;
    awakeError = "";
    if (minutes < 0) {
      awakeRequested = false;
      awakeUntil = 0;
      if (inhibitor.running) inhibitor.write("stop\n");
      return;
    }
    now = Date.now();
    awakeUntil = minutes > 0 ? now + minutes * 60000 : 0;
    awakeRequested = true;
    if (!inhibitor.running) { inhibitor.running = true; inhibitorStartup.restart(); }
  }
  function toggleDictation(mode) {
    if (dictationBusy || ["live", "batch"].indexOf(mode) < 0) return;
    if (!dictation[mode + "Available"] || dictation[mode === "live" ? "batch" : "live"]) return;
    dictationError = "";
    dictationMode = mode;
    // The caller closes the popout before this delay: typed text belongs to
    // the user's application, not a layer-shell control that stole focus.
    dictationDelay.restart();
  }
  function refreshApps() {
    if (!appCheck.running) { appUpdateError = ""; appCheck.running = true; }
  }
  function receive(message) {
    if (message.type === "hardware") { hardware = message; telemetryReady = true; }
    else if (message.type === "dictation") dictation = message;
  }
  Process {
    id: telemetry
    command: ["python3", root.helper, "watch"]
    running: true
    stdinEnabled: true
    stdout: SplitParser {
      onRead: line => { try { root.receive(JSON.parse(line)); } catch (e) { console.warn("Pond telemetry:", e); } }
    }
    onExited: {
      root.telemetryReady = false;
      root.hardware = ({});
      root.dictation = ({live: false, batch: false, liveAvailable: false, batchAvailable: false});
      restartTelemetry.restart();
    }
  }
  Timer { id: restartTelemetry; interval: 5000; onTriggered: telemetry.running = true }
  Timer {
    id: dictationDelay
    interval: 250
    onTriggered: {
      dictationAction.command = [Quickshell.env("HOME") + "/.local/bin/toggle-dictation"
          + (root.dictationMode === "batch" ? "-batch" : "") + ".sh"];
      dictationAction.running = true;
    }
  }
  Process {
    id: dictationAction
    onExited: code => {
      if (code !== 0) root.dictationError = "Dictation could not finish. Check its notification or recording log.";
    }
  }
  Process {
    id: inhibitor
    command: ["systemd-inhibit", "--what=idle", "--mode=block", "--who=Pond",
        "--why=Keep awake", "python3", root.helper, "hold"]
    stdinEnabled: true
    stdout: SplitParser {
      onRead: line => {
        try {
          if (JSON.parse(line).ready) {
            inhibitorStartup.stop();
            if (root.awakeRequested) root.keepAwake = true;
            else inhibitor.write("stop\n");
          }
        } catch (e) {}
      }
    }
    onExited: code => {
      inhibitorStartup.stop();
      if (root.awakeRequested) root.awakeError = "Keep-awake ended unexpectedly. Automatic idle is enabled.";
      root.awakeRequested = false;
      root.keepAwake = false;
      root.awakeUntil = 0;
    }
  }
  Timer {
    id: inhibitorStartup; interval: 5000
    onTriggered: {
      root.awakeError = "Could not inhibit automatic idle. Try again.";
      root.awakeRequested = false;
      inhibitor.running = false;
    }
  }
  Timer {
    interval: 1000; running: root.keepAwake; repeat: true
    onTriggered: {
      root.now = Date.now();
      if (root.awakeUntil && root.now >= root.awakeUntil) root.setAwake(-1);
    }
  }
  FileView {
    id: appCache
    path: (Quickshell.env("XDG_CACHE_HOME") || Quickshell.env("HOME") + "/.cache") + "/auseklis/updates"
    watchChanges: true; printErrors: false
    onFileChanged: reload()
    onLoaded: {
      root.appUpdates = text().trim().split("\n").filter(line => line.trim() && !line.startsWith("Flake inputs  "));
      root.appUpdatesKnown = true;
    }
  }
  Process {
    id: appCheck
    // Share Waybar's timer service: concurrent checks are coalesced by systemd.
    command: ["systemctl", "--user", "start", "auseklis-updates.service"]
    onExited: code => {
      if (code !== 0) root.appUpdateError = "Could not refresh app versions.";
      appCache.reload();
    }
  }
}
