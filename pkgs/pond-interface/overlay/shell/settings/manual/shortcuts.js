.pragma library
// The shortcuts the manual lists, as bound in this machine's Pond session
// (home/hyprland/compositor.nix, shellKind "pond"). `binds` are the Hyprland
// key strings and `expect` a fragment of the bound command (or description);
// tests/test_manual_binds.py runs the generated hyprland-pond.lua and fails if
// any entry does not match a real bind. Keep the body plain JSON for it.
var sections = [
  {
    "title": "Getting started",
    "intro": "The launcher is the quickest way to open apps, files and clipboard history. Software on this machine is declared in ~/nix-config: add a package to home/tools.nix and rebuild with nrs. Nothing is installed from the launcher.",
    "rows": [
      { "label": "Open the launcher", "caps": ["spr", "space"], "binds": ["SUPER + SPACE"], "expect": "pond-session launcher toggle" },
      { "label": "Clipboard history", "caps": ["spr", "v"], "binds": ["SUPER + V"], "expect": "clipboard/history" },
      { "label": "Open terminal", "caps": ["spr", "enter"], "binds": ["SUPER + RETURN"], "expect": "ghostty" },
      { "label": "Terminal in tmux", "caps": ["spr", "alt", "enter"], "binds": ["SUPER + ALT + RETURN"], "expect": "tmux attach" },
      { "label": "Open a project", "caps": ["spr", "o"], "binds": ["SUPER + O"], "expect": "auseklis-project" },
      { "label": "Agent switcher", "caps": ["spr", "shift", "a"], "binds": ["SUPER + SHIFT + A"], "expect": "auseklis-agents" },
      { "label": "Keybinding cheatsheet", "caps": ["spr", "/"], "binds": ["SUPER + slash"], "expect": "auseklis-keys" },
      { "label": "Reopen this manual", "caps": ["spr", "shift", "/"], "binds": ["SUPER + SHIFT + slash"], "expect": "manual open" }
    ]
  },
  {
    "title": "Windows",
    "intro": "Windows tile automatically (Hyprland's dwindle layout). Each new window splits the focused one.",
    "rows": [
      { "label": "Focus left/down/up/right", "caps": ["spr", "h j k l"], "binds": ["SUPER + H", "SUPER + J", "SUPER + K", "SUPER + L", "SUPER + left", "SUPER + right"], "expect": "Focus" },
      { "label": "Move window", "caps": ["spr", "shift", "h j k l"], "binds": ["SUPER + SHIFT + H", "SUPER + SHIFT + L", "SUPER + SHIFT + up"], "expect": "Move window" },
      { "label": "Drag window", "caps": ["spr", "mouse-left"], "binds": ["SUPER + mouse:272"], "expect": "Drag window" },
      { "label": "Resize window", "caps": ["spr", "mouse-right"], "binds": ["SUPER + mouse:273"], "expect": "Resize window" },
      { "label": "Make window narrower/wider", "caps": ["spr", "-", "="], "binds": ["SUPER + minus", "SUPER + equal"], "expect": "window" },
      { "label": "Toggle split direction", "caps": ["spr", "alt", "s"], "binds": ["SUPER + ALT + S"], "expect": "Toggle split" },
      { "label": "Maximize window", "caps": ["spr", "f"], "binds": ["SUPER + F"], "expect": "Maximize" },
      { "label": "Fullscreen window", "caps": ["spr", "shift", "f"], "binds": ["SUPER + SHIFT + F"], "expect": "Fullscreen" },
      { "label": "Float window", "caps": ["spr", "t"], "binds": ["SUPER + T"], "expect": "floating" },
      { "label": "Close window", "caps": ["spr", "q"], "binds": ["SUPER + Q"], "expect": "Close window" },
      { "label": "Scratchpad", "caps": ["spr", "`"], "binds": ["SUPER + grave"], "expect": "Scratchpad" },
      { "label": "tmux-style prefix mode", "caps": ["spr", "a"], "binds": ["SUPER + A"], "expect": "tmux mode" }
    ]
  },
  {
    "title": "Workspaces",
    "intro": "The sidebar numbers the workspaces of each monitor. The number keys follow those numbers on the focused monitor; the one after the last opens a new workspace.",
    "rows": [
      { "label": "Jump to workspace", "caps": ["spr", "1-0"], "binds": ["SUPER + 1", "SUPER + 5", "SUPER + 0"], "expect": "workspaces focus" },
      { "label": "Move window to workspace", "caps": ["spr", "shift", "1-0"], "binds": ["SUPER + SHIFT + 1", "SUPER + SHIFT + 0"], "expect": "workspaces moveWindow" },
      { "label": "Next/previous workspace", "caps": ["spr", "(shift)", "tab"], "binds": ["SUPER + TAB", "SUPER + SHIFT + TAB"], "expect": "workspace" },
      { "label": "Show/hide the sidebar", "caps": ["spr", "b"], "binds": ["SUPER + B"], "expect": "sidebar toggle" },
      { "label": "Notifications (sidebar)", "caps": ["spr", "n"], "binds": ["SUPER + N"], "expect": "sidebar toggle" }
    ]
  },
  {
    "title": "Other",
    "intro": "",
    "rows": [
      { "label": "Take screenshot", "caps": ["spr", "s"], "binds": ["SUPER + S"], "expect": "screenshot capture area" },
      { "label": "Screenshot to annotate", "caps": ["spr", "shift", "s"], "binds": ["SUPER + SHIFT + S"], "expect": "satty" },
      { "label": "Screenshot monitor to clipboard", "caps": ["spr", "shift", "p"], "binds": ["SUPER + SHIFT + P"], "expect": "grimblast" },
      { "label": "Dictation", "caps": ["spr", "alt", "l"], "binds": ["SUPER + ALT + L"], "expect": "dictation" },
      { "label": "Settings", "caps": ["spr", ","], "binds": ["SUPER + comma"], "expect": "settings toggle" },
      { "label": "Wallpaper and appearance", "caps": ["spr", "shift", "w"], "binds": ["SUPER + SHIFT + W"], "expect": "settings open appearance" },
      { "label": "Lock screen", "caps": ["spr", "ctrl", "l"], "binds": ["SUPER + CTRL + L"], "expect": "pond-session lock" },
      { "label": "Restart the shell", "caps": ["spr", "shift", "space"], "binds": ["SUPER + SHIFT + SPACE"], "expect": "pond-session restart-shell" },
      { "label": "Power menu", "caps": ["spr", "shift", "x"], "binds": ["SUPER + SHIFT + X"], "expect": "settings toggle" }
    ],
    "note": "If locking fails, Super+Ctrl+L retries confirmation. A hung or crashed lock screen is never replaced automatically. If it cannot unlock, log in on another VT and end the affected Pond session; see docs/pond-hyprland.md."
  }
];
