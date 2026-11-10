{ pkgs, ... }:
let
  acira = pkgs.callPackage ../pkgs/acira-browser { };
  id = "zen-beta.desktop";
  browserDefaults = {
    "x-scheme-handler/http" = [ id ];
    "x-scheme-handler/https" = [ id ];
    "text/html" = [ id ];
    "application/xhtml+xml" = [ id ];
    "application/x-extension-htm" = [ id ];
    "application/x-extension-html" = [ id ];
    "application/x-extension-shtml" = [ id ];
    "application/x-extension-xhtml" = [ id ];
    "application/x-extension-xht" = [ id ];
  };
in
{
  home.packages = [ acira ];
  home.sessionVariables.BROWSER = "zen-beta";

  xdg.mimeApps = {
    enable = true;
    # Preserve the existing non-browser deep links when HM takes over this file.
    defaultApplications = browserDefaults // {
      "x-scheme-handler/granola" = [ "granola.desktop" ];
      "x-scheme-handler/beeper" = [ "beepertexts.desktop" ];
      "x-scheme-handler/t3code" = [ "com.t3tools.T3Code.desktop" ];
      "x-scheme-handler/delta" = [ "dev.zed.Delta.desktop" ];
      "x-scheme-handler/codex" = [ "chatgpt.desktop" ];
      "x-scheme-handler/slack" = [ "slack.desktop" ];
      "x-scheme-handler/chrome" = [ "zen-beta.desktop" ];
    };
  };

  # The hand-managed file is backed up as mimeapps.list.before-acira-20261109.
  # A pre-existing .hm-bak would otherwise prevent Home Manager taking ownership.
  xdg.configFile."mimeapps.list".force = true;
  # Home Manager also redirects the obsolete applications-directory location.
  # Its previous empty file is preserved beside it with the same dated suffix.
  xdg.dataFile."applications/mimeapps.list".force = true;

  # GLib, xdg-open and Vicinae consult desktop-specific defaults first.
  xdg.configFile."hyprland-mimeapps.list".source = pkgs.runCommand "hyprland-mimeapps.list" { } ''
    cat > "$out" <<EOF
    [Default Applications]
    x-scheme-handler/http=${id}
    x-scheme-handler/https=${id}
    text/html=${id}
    application/xhtml+xml=${id}
    EOF
  '';
}
