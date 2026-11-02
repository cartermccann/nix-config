//@ pragma Env QSG_RENDER_LOOP = threaded
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import "battery" as Battery
import "calendar" as Calendar
import "settings" as Settings
import "settings/appearance" as Appearance
import "settings/system" as System
import "notifications" as Notifications
import Pond.Wallpaper as Wallpaper
import Pond.Screenshot as Screenshot
import Pond.System

ShellRoot {
  id: root
  property bool expanded: true
  readonly property string sessionHelper: Quickshell.shellDir + "/settings/session.py"

  Shortcuts {}

  // Lock before sleep and logind's Lock signal are not handled here: the
  // session's lock guard (pond-session, a supervised process of its own) holds
  // the sleep delay inhibitor, so a crashed or restarting shell does not leave
  // suspend unlocked. Every lock below goes through the same owner.

  Variants {
    id: idleActions
    readonly property var commands: ({
      lock: ["python3", root.sessionHelper, "lock"],
      sleep: ["python3", root.sessionHelper, "suspend"]
    })
    model: ["lock", "display", "sleep"].filter(key => System.Idle.current[key] > 0)
        .map(key => ({timeout: System.Idle.current[key], key: key}))
    delegate: IdleMonitor {
      required property var modelData
      timeout: modelData.timeout
      onIsIdleChanged: {
        // Hyprland only wakes displays on input when configured to; the
        // shell powers them back on itself as soon as the seat is active.
        if (modelData.key === "display") NiriMsg.setDisplayPower(!isIdle);
        else if (isIdle) Quickshell.execDetached(idleActions.commands[modelData.key]);
      }
    }
  }

  // Pond's display, keyboard and pointer settings are runtime overrides;
  // reapply them on start and whenever Hyprland reloads its configuration.
  Process {
    id: restoreSettings
    command: ["python3", Quickshell.shellDir + "/hypr/userstate.py", "restore"]
    running: true
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          const report = JSON.parse(text);
          for (const error of report.errors || []) console.warn("pond settings:", error);
          for (const skipped of report.skipped || []) console.info("pond settings skipped:", skipped);
        } catch (e) { console.warn("pond settings: could not restore", text.trim()); }
      }
    }
  }
  Connections {
    target: NiriMsg
    function onConfigReloaded() { if (!restoreSettings.running) restoreSettings.running = true; }
  }

  // Screenshots: Hyprland has no capture event, so captures reach the
  // preview through this IPC target.
  property int screenshotSerial: 0
  Process {
    id: captureProcess
    property string file
    property string output
    stderr: StdioCollector { onStreamFinished: if (text.trim()) console.warn("pond screenshot:", text.trim()) }
    onExited: code => { if (code === 0) Screenshot.State.receive(captureProcess.file, captureProcess.output); }
  }
  IpcHandler {
    target: "screenshot"
    function receive(path: string, output: string): void {
      Screenshot.State.receive(path, output || NiriMsg.focusedOutputName);
    }
    // target: area | output | active | screen
    function capture(target: string): void {
      if (captureProcess.running || ["area", "output", "active", "screen"].indexOf(target) < 0) return;
      const pictures = Quickshell.env("XDG_PICTURES_DIR") || Quickshell.env("HOME") + "/Pictures";
      const stamp = new Date().toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
      captureProcess.file = pictures + "/Screenshots/Screenshot-" + stamp + "-" + (++root.screenshotSerial) + ".png";
      captureProcess.output = NiriMsg.focusedOutputName;
      captureProcess.command = ["sh", "-c", 'mkdir -p "${2%/*}" && exec grimblast --freeze copysave "$1" "$2"',
                                "sh", target, captureProcess.file];
      captureProcess.running = true;
    }
  }

  IpcHandler {
    target: "workspaces"
    // Super+N and Super+Shift+N: the numbers shown in the sidebar.
    function focus(number: int): bool { return NiriMsg.focusWorkspaceNumber(number); }
    function moveWindow(number: int): bool { return NiriMsg.moveFocusedWindowToNumber(number); }
  }

  FileView {
    path: Quickshell.screens.length
        ? (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/pond-interface/manual-shown"
        : ""
    printErrors: false
    onLoadFailed: error => {
      if (error !== FileViewError.FileNotFound) return;
      Settings.State.open("", "manual");
      Qt.callLater(() => setText("shown\n"));
    }
  }

  IpcHandler {
    target: "sidebar"
    function toggle() { root.expanded = !root.expanded; }
  }

  IpcHandler {
    target: "manual"
    function open() { Settings.State.open("", "manual"); }
  }

  IpcHandler {
    target: "settings"
    function toggle() { Settings.State.toggle(); }
    function open(page: string) {
      if (page && Settings.State.pages.indexOf(page) < 0) return;
      Settings.State.open("", page);
    }
    function close() { Settings.State.close(); }
  }

  Variants {
    model: Quickshell.screens
    delegate: Scope {
      required property var modelData
      Wallpaper.Window {
        screen: modelData
        custom: Appearance.State.wallpaperMode === "custom"
        dimmed: Theme.daylight
        imageSource: Appearance.State.wallpaperUrl
      }
      Sidebar {
        id: sidebar
        screen: modelData
        expanded: root.expanded
        onToggleRequested: root.expanded = !root.expanded
      }
      Notifications.ToastWindow { id: toast; bar: sidebar }
      Notifications.CenterWindow { bar: sidebar }
      Calendar.Window { bar: sidebar }
      Battery.Window { bar: sidebar }
      Battery.Notification { bar: sidebar; above: toast }
      PanelDismissWindow { bar: sidebar }
      Settings.Window { menuScreen: modelData }
    }
  }
}
