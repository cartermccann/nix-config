{
  config,
  lib,
  pkgs,
  ...
}:

# micro-bridge — drive the focused GUI agent app from the Work Louder Codex Micro.
#
# The Micro's bottom row is not Codex-locked, which is the whole premise. Every
# key on the board emits the same vendor HID notify; the "prompt / voice / send"
# meanings live in a lookup table inside Codex's renderer, not in the hardware.
# The bridge reads that channel, works out which app has focus, and performs the
# equivalent action there. See github.com/cartermccann/micro-herdr.
#
# Injection does not vary by machine: wtype synthesises the keystrokes and
# wlrctl activates windows, and both work on Hyprland here and on mango on
# usins, because both compositors implement virtual-keyboard-v1 and
# foreign-toplevel-management. Only the focus signal differs, and the bridge
# abstracts that internally (Hyprland's .socket2.sock here, `mmsg watch` there).
#
# Two things this unit deliberately will NOT do:
#
#   - It refuses to start unless the Work Louder device kit is present. The kit
#     is proprietary, ships inside the Work Louder "Input" desktop app, and is
#     not redistributable, so it cannot be packaged here and has to be vendored
#     by hand. ConditionPathExists makes a missing kit a clean "condition
#     failed" rather than a restart loop.
#   - It should not run alongside micro-herdr. Note the reason, because the
#     obvious one is wrong: hidraw allows MULTIPLE concurrent readers, so two
#     daemons would not fight over the device, they would both act on every key
#     and fire each action twice. Codex desktop is the same story, and is
#     handled by the `passthrough` list in the daemon's profiles.json, which
#     makes the bridge ignore every key while Codex has focus so Codex's own
#     Micro integration owns the device. Closing Codex is not required.
let
  homeDir = config.home.homeDirectory;
  checkout = "${homeDir}/projects/micro-herdr";
  kit = "${homeDir}/apps/vendor/wl-device-kit/dist/index.js";

  # Batch dictation, not the live streaming mode, even though live is the daily
  # driver for the keyboard shortcut. The mic key latches its target when you
  # press it and re-focuses that window just before the transcript is typed,
  # because dictation picks its destination seconds after you start speaking and
  # the pointer may have drifted. Live streaming types as you talk, i.e. before
  # the latch could be applied, so it would defeat the mechanism. Point this at
  # toggle-dictation.sh if you would rather have words appear as you speak and
  # accept that they land wherever focus happens to be.
  dictateScript = "${homeDir}/.local/bin/toggle-dictation-batch.sh";
in
{
  home.packages = [
    pkgs.wtype
    pkgs.wlrctl
  ];

  systemd.user.services.micro-bridge = {
    Unit = {
      Description = "Codex Micro -> focused GUI agent app (Cursor, Grokbot)";
      # Needs a compositor: it follows Hyprland's event socket to know which app
      # a keypress belongs to, and injects keystrokes into that app.
      After = [ "graphical-session.target" ];
      PartOf = [ "graphical-session.target" ];
      # hidraw allows two readers, so starting both this and the herdr/shell
      # daemon would double-fire every key. Starting micro-bridge stops
      # micro-herdr, and vice versa.
      Conflicts = [ "micro-herdr.service" ];
      ConditionPathExists = [
        "${checkout}/micro-bridge.mjs"
        kit
      ];
    };

    Service = {
      Type = "simple";
      # profiles.json and lib/ are resolved relative to the checkout.
      WorkingDirectory = checkout;
      Environment = [
        "MICRO_HERDR_KIT=${kit}"
        "MICRO_BRIDGE_DICTATE=${dictateScript}"
        # The bridge shells out to wtype, wlrctl and the dictation script. It
        # discovers HYPRLAND_INSTANCE_SIGNATURE from XDG_RUNTIME_DIR when it is
        # not inherited, so a compositor restart does not leave it blind.
        "PATH=${
          lib.makeBinPath [
            pkgs.wtype
            pkgs.wlrctl
            pkgs.coreutils
          ]
        }:${homeDir}/.local/bin"
      ];
      ExecStart = "${pkgs.nodejs_24}/bin/node ${checkout}/micro-bridge.mjs";
      Restart = "always";
      RestartSec = 5;
    };

    Install = {
      WantedBy = [ "graphical-session.target" ];
    };
  };

  # `systemctl --user enable` writes a regular unit file plus a wants
  # symlink that home-manager will not clobber, even with
  # backupFileExtension. Drop those leftovers before checkLinkTargets so
  # a hand-enabled unit cannot fail the next switch.
  home.activation.unclobberMicroBridge = lib.hm.dag.entryBefore [ "checkLinkTargets" ] ''
    unit="$HOME/.config/systemd/user/micro-bridge.service"
    wants="$HOME/.config/systemd/user/graphical-session.target.wants/micro-bridge.service"
    if [ -L "$wants" ] && [ "$(readlink -n "$wants")" = "$unit" ]; then
      rm -f "$wants"
    fi
    if [ -f "$unit" ] && [ ! -L "$unit" ]; then
      rm -f "$unit"
    fi
  '';

  # The Micro's mic / knob send F13 / F14 / F15 into Cursor. Those commands
  # have no default bindings, so without this file the chords land and do
  # nothing. JSONC is what Cursor's workbench reads. The Agents window ignores
  # this file (falls back to Parakeet in profiles.json).
  xdg.configFile."Cursor/User/keybindings.json" = {
    force = true;
    text = ''
      [
        {
          "key": "f13",
          "command": "composer.toggleVoiceDictation"
        },
        {
          "key": "f14",
          "command": "composer.cycleModelParameter"
        },
        {
          "key": "f15",
          "command": "composer.cycleModel"
        }
      ]
    '';
  };
}
