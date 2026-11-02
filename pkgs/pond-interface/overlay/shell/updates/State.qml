pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io
import "../installs" as Installs
import "../settings" as Settings

// System updates for a NixOS flake (updates/nix.py). Checking resolves the
// newest input revisions into a temporary lock file; flake.lock is only
// written, built and switched from the terminal the update opens, where the
// user confirms each step and authenticates the switch.
//
// The terminal outlives this shell and can die without telling it, so
// "updating" is never trusted on its own: `nix.py reconcile` asks whether a
// live updater still holds the flake's transaction lock, at startup, on every
// refresh and every 15 s while an update is shown as running. An update that
// was killed, crashed or interrupted by a restart becomes a failed card and
// the badge returns to "available" (after a fresh check) instead of spinning.
Singleton {
  id: root
  property string status: ""
  property string pending: ""
  property var inputs: []
  property string error: ""
  property string message: ""
  property string lastChecked: ""
  readonly property bool checking: check.running || reconcile.running
  property bool launching: false
  property bool unresolved: false
  property string outcomeKey: ""
  readonly property string helper: Quickshell.shellDir + "/updates/nix.py"

  function start() {
    if (status !== "available" || launching) return;
    pending = "";
    launching = true;
    Settings.State.close();
    afterFile.setText("");
    Installs.State.update("system-update", "System update", "installing", -1, "Opening Ghostty");
    launchWatch.restart();
    Quickshell.execDetached(["ghostty", "--title=Pond system update", "-e",
        "python3", root.helper, "run", statusFile.path]);
  }

  // Reboot or shut down once the update finishes. Only meaningful for the
  // update that is running now; nix.py discards the choice when it ends.
  function after(action) {
    if (status !== "updating") return;
    pending = pending === action ? "" : action;
    afterFile.setText(pending);
  }

  function refresh() {
    if (!reconcile.running) reconcile.running = true;
  }

  function apply(result, message, force, transaction) {
    root.message = message || "";
    const wasUpdating = force || root.status === "updating";
    const key = (transaction || "") + "\n" + result + "\n" + message;
    if (result !== "updating" && key === root.outcomeKey) return;
    if (result !== "updating") root.outcomeKey = key;
    if (result === "updating") {
      launchWatch.stop();
      root.launching = false;
      const resumed = root.status !== "updating";
      root.status = "updating";
      Installs.State.update("system-update", "System update", "installing", -1, message);
      if (resumed) root.refresh();
    } else if (result === "restart") {
      root.launching = false;
      root.unresolved = false;
      root.status = "restart";
      if (wasUpdating) Installs.State.update("system-update", "Update complete", "installed", -1, message);
    } else if (result === "complete") {
      root.launching = false;
      root.unresolved = false;
      root.status = "complete";
      if (wasUpdating) Installs.State.update("system-update", "Update complete", "installed", -1, message);
    } else if (result === "failed" || result === "available") {
      // Failed, cancelled, interrupted, abandoned, or built without switching:
      // nothing is installed, and a reboot chosen for it must not survive.
      root.launching = false;
      root.status = "available";
      root.unresolved = root.unresolved || result === "failed" || message.indexOf("switch pending") >= 0;
      root.pending = "";
      afterFile.setText("");
      Installs.State.update("system-update",
          result === "failed" ? "Update failed" : "Update not installed", "failed", -1, message);
      if (!check.running) check.running = true;
    }
  }

  // If the terminal never reports progress, say so instead of spinning.
  Timer {
    id: launchWatch
    interval: 20000
    onTriggered: {
      root.launching = false;
      if (root.status === "available")
        Installs.State.update("system-update", "System update", "failed", -1, "Could not open Ghostty");
    }
  }

  Timer {
    interval: 12 * 60 * 60 * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  // Liveness: an "updating" status needs a live owner behind it.
  Timer {
    interval: 15000
    running: root.status === "updating"
    repeat: true
    onTriggered: root.refresh()
  }

  Process {
    id: reconcile
    command: ["python3", root.helper, "reconcile", statusFile.path]
    stdout: StdioCollector { id: reconciled }
    onExited: {
      let result = null;
      try { result = JSON.parse(reconciled.text); } catch (e) {}
      if (result && result.alive) {
        launchWatch.stop();
        root.launching = false;
        root.status = "updating";
        root.message = result.message || "Updating";
        Installs.State.update("system-update", "System update", "installing", -1, result.message || "Updating");
        afterFile.reload();
        return;
      }
      if (result && result.abandoned) {
        root.apply(result.state, result.message, true, result.transaction);
      } else if (result && root.status === "updating") {
        // The file no longer says "updating" (or is gone) but we never saw the end.
        root.apply(result.state || "failed", result.message || "Update status was lost", true, result.transaction);
      } else if (result && (result.state === "failed" || result.state === "available")) {
        root.apply(result.state, result.message, false, result.transaction);
      } else if (result && root.status === "restart" && !result.restartNeeded) {
        root.status = "";
      }
      if (root.status !== "updating" && !check.running) check.running = true;
    }
  }

  Process {
    id: check
    command: ["python3", root.helper, "check"]
    stdout: StdioCollector { id: output }
    onExited: exitCode => {
      let result = null;
      try { result = JSON.parse(output.text); } catch (e) {}
      if (exitCode !== 0 || !result || result.error) {
        root.error = result && result.error ? result.error : "Could not check for updates";
        console.warn("pond updates:", root.error);
        return;
      }
      root.error = "";
      root.lastChecked = new Date().toLocaleTimeString(Qt.locale(), "hh:mm");
      root.inputs = result.inputs;
      if (result.activationPending) root.unresolved = true;
      if (root.status === "" || root.status === "available")
        root.status = result.available || root.unresolved ? "available" : "";
    }
  }

  Timer {
    interval: 60 * 60 * 1000
    running: root.status === "complete"
    onTriggered: root.status = ""
  }

  FileView {
    id: statusFile
    path: Quickshell.env("XDG_RUNTIME_DIR") + "/pond-update"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      const lines = text().split("\n");
      let owner = {};
      try { owner = JSON.parse(lines[2] || "{}"); } catch (e) {}
      root.apply(lines[0].trim(), (lines[1] || "").trim(), false, owner.txn);
    }
  }

  FileView {
    id: afterFile
    path: statusFile.path + ".after"
    printErrors: false
    onLoaded: if (root.status === "updating") root.pending = text().trim()
  }
}
