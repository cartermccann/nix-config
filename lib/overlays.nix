{ inputs }:
[
  inputs.niri.overlays.niri
  (final: prev: {
    ghostty = inputs.ghostty.packages.${prev.stdenv.hostPlatform.system}.default;
  })
  # Stable neovim 0.12.x from the unstable channel (25.11 ships 0.11.7).
  (final: prev: {
    neovim-unwrapped =
      inputs.nixpkgs-unstable.legacyPackages.${prev.stdenv.hostPlatform.system}.neovim-unwrapped;
  })
  # waybar 0.15.0's mpris module hides its widget from inside playerctl
  # callbacks, which segfaults in Gtk::Widget::set_visible (seen here
  # 2026-10-06 in onPlayerStop; upstream #5124 for onPlayerNameVanished).
  #   - c19abf3 (#5165): upstream's fix for onPlayerNameVanished
  #   - mpris-hide-on-main-thread.patch: the same fix for onPlayerStop, the
  #     path that actually crashed here; the hide moves into update(), which
  #     runs on the main thread. Upstream master dropped the call too (0a50e82).
  # Neither is in a release yet; drop both once nixpkgs ships one that has them
  # (the patches will then fail to apply, which is the signal).
  (final: prev: {
    waybar = prev.waybar.overrideAttrs (old: {
      patches = (old.patches or [ ]) ++ [
        (final.fetchpatch {
          url = "https://github.com/Alexays/Waybar/commit/c19abf373bceb3eb264690f94d367407d7133568.patch";
          hash = "sha256-yRsqQZRqP+qClLsJ/WOp0NkvYqLECNl7ylndS9Kva+4=";
        })
        ../pkgs/waybar/mpris-hide-on-main-thread.patch
      ];
    });
  })
  # Pond desktop shell and locker for the "Hyprland (Pond)" session; see
  # pkgs/pond-interface/README.md.
  (final: prev: {
    pond-interface = final.callPackage ../pkgs/pond-interface { };
  })
  # Backport GNOME Keyring !112: fresh Secret Service clients can otherwise
  # crash the daemon and trigger repeated unlock prompts. See the local README.
  (final: prev: {
    gnome-keyring = prev.gnome-keyring.overrideAttrs (old: {
      patches = (old.patches or [ ]) ++ [ ../pkgs/gnome-keyring/fix-client-race.patch ];
    });
  })
]
