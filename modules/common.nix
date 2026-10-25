{
  config,
  lib,
  pkgs,
  user,
  ...
}:

let
  pal = import ../lib/palette.nix;

in
{
  # Boot — Limine, replacing systemd-boot.
  #
  # systemd-boot is switched off but deliberately NOT cleaned up. Disabling the
  # module only stops it being rewritten; its binary stays at
  # /EFI/systemd/systemd-bootx64.efi, its "Linux Boot Manager" NVRAM entry
  # stays, /EFI/BOOT/BOOTX64.EFI stays pointed at it, and /boot/loader/entries
  # stays frozen at the last generation it wrote. Limine installs to
  # /EFI/limine/ (efiInstallAsRemovable stays false, so it does not claim the
  # removable path) and only ever ADDS its own NVRAM entry — it deletes
  # nothing. So a Limine that fails to boot is recoverable from the firmware
  # boot menu with no rescue media: pick Linux Boot Manager and you are on a
  # known-good generation. Leave all of it alone until Limine has a real run
  # of boots behind it.
  boot.loader.systemd-boot.enable = false;
  boot.loader.efi.canTouchEfiVariables = true;

  # Stylix themes Limine too and would win the whole style block. Stylix now
  # runs Auseklis (modules/stylix.nix), but the boot menu below is hand-tuned
  # from lib/palette.nix beyond what a base16 mapping gives, so Stylix still
  # hands this target over. Same call as Caelestia taking gtk.css off Stylix
  # in home/hyprland/caelestia.nix.
  stylix.targets.limine.enable = false;

  boot.loader.limine = {
    enable = true;
    efiSupport = true;
    biosSupport = false;

    # Replaces systemd-boot's configurationLimit. Limine keeps its own copies
    # of every kernel and initrd it can boot under /boot/limine, and this ESP
    # is 2 GB, so the bound matters more here than it did before.
    maxGenerations = 10;

    # The editor can boot any entry with an arbitrary cmdline, which hands
    # anyone at the keyboard root via init=/bin/sh. Upstream recommends off.
    enableEditor = false;

    style = {
      # Flat ground, no image. The module mkDefaults an image in
      # (nixos-artwork's simple-dark-gray-bootloader) plus a "2F302F" backdrop,
      # so both have to be answered explicitly or the menu comes up grey with a
      # stock NixOS wallpaper behind it. backdrop is what fills the screen once
      # there is no wallpaper to cover it.
      wallpapers = [ ];
      backdrop = pal.night.raw.base02;

      interface = {
        branding = "perkons"; # same title as the Ly greeter's box
        # Kept visible on purpose, unlike Ly's hide_key_hints. This menu is the
        # rollback path; the one time it matters is the one time you will not
        # remember the keys.
        helpHidden = false;
      };

      # Auseklis night, so firmware → Limine → Ly reads as one surface rather
      # than three. base02 is the same ground Ly paints (modules/desktop-
      # wayland.nix), not base00, which is why this is not quite the desktop
      # background. Six hex digits is RRGGBB; the TT transparency byte the
      # option mentions is optional and omitted, which Limine treats as opaque.
      graphicalTerminal = {
        background = pal.night.raw.base02;
        foreground = pal.night.raw.base05;
        brightBackground = pal.night.raw.base01;
        brightForeground = pal.night.raw.base07;

        # black; red; green; brown; blue; magenta; cyan; gray — Limine's order.
        # "brown" is the slot modern palettes call yellow.
        palette = lib.concatStringsSep ";" [
          pal.night.raw.base00
          pal.night.raw.base08
          pal.night.raw.base0B
          pal.night.raw.base0A
          pal.night.raw.base0D
          pal.night.raw.base0E
          pal.night.raw.base0C
          pal.night.raw.base05
        ];
        brightPalette = lib.concatStringsSep ";" [
          pal.night.raw.base03
          pal.night.raw.base08
          pal.night.raw.base0B
          pal.night.raw.base0A
          pal.night.raw.base0D
          pal.night.raw.base0E
          pal.night.raw.base0C
          pal.night.raw.base07
        ];

        margin = 24;
      };
    };

    # brandingColor is typed as a 0-7 palette index, which is Limine's OLD
    # format. 10.8 documents interface_branding_colour as RRGGBB (CONFIG.md)
    # and would read a bare `4` as #000004 — an invisible title on a dark
    # ground. So the typed option is left null and the real value goes through
    # extraConfig, which is prepended to limine.conf.
    extraConfig = ''
      interface_branding_colour: ${pal.night.raw.base0D}
    '';
  };

  # NOT systemd-boot's 0. Limine at `timeout: 0` boots the default entry
  # instantly and there is no way into the menu — the press-any-key reveal
  # applies only while a timeout is actually running (CONFIG.md, `quiet`).
  # The menu is the rollback path, so it has to be reachable.
  boot.loader.timeout = 2;

  # Plymouth — Catppuccin boot splash (override Stylix's default theme)
  boot.plymouth = {
    enable = true;
    theme = lib.mkForce "catppuccin-macchiato";
    themePackages = [
      (pkgs.catppuccin-plymouth.override { variant = "macchiato"; })
    ];
  };

  # Quiet boot — hide kernel/systemd noise behind Plymouth
  boot.consoleLogLevel = 0;
  boot.initrd.verbose = false;
  boot.kernelParams = [
    "quiet"
    "splash"
    "boot.shell_on_fail"
    "loglevel=3"
    "rd.systemd.show_status=false"
    "rd.udev.log_level=3"
    "udev.log_priority=3"
  ];

  # Networking
  networking.networkmanager.enable = true;

  # Locale
  time.timeZone = "America/Denver";
  i18n.defaultLocale = "en_US.UTF-8";

  # Nix settings
  nix.settings = {
    experimental-features = [
      "nix-command"
      "flakes"
    ];
    # Hyprland's binary cache — without this the pinned 0.55.x flake build
    # compiles from source (heavy; risks OOM on this host).
    substituters = [ "https://hyprland.cachix.org" ];
    trusted-public-keys = [
      "hyprland.cachix.org-1:a7pgxzMz7+chwVL3/pzj6jIBMioiJM7ypFP8PwtkuGc="
    ];
  };
  nixpkgs.config.allowUnfree = true;
  programs.nix-ld.enable = true;

  # Expose the AT-SPI accessibility bus for background desktop automation
  # (for example cua-driver). This is not enabled automatically outside GNOME.
  services.gnome.at-spi2-core.enable = true;

  # Runtime libs for prebuilt/dynamically-linked binaries run via nix-ld.
  # Covers cua-driver and uv-fetched / Playwright binaries.
  programs.nix-ld.libraries = with pkgs; [
    stdenv.cc.cc.lib
    zlib
    openssl
    glib
    nss
    nspr
    # Electron / Chromium runtime
    atk
    at-spi2-atk
    at-spi2-core
    cups
    dbus
    expat
    libdrm
    gtk3
    pango
    cairo
    gdk-pixbuf
    libx11
    libxi
    libxcb
    libxcomposite
    libxdamage
    libxext
    libxfixes
    libxrandr
    libxshmfence
    mesa
    libgbm # gbm split out of mesa in nixpkgs 25.x; Electron 29 (Work Louder Input) needs libgbm.so.1
    libGL
    libxkbcommon
    alsa-lib
    systemd
  ];

  # Work Louder / Nomad device access (macropads, keyboards, ESP32-S3 serial).
  # Declarative replacement for the port's install-udev-worklouder.sh.
  # Espressif VID 303a (current WL boards) + legacy Nomad VID 574c; the
  # uaccess tag grants the logged-in user access without group juggling.
  services.udev.extraRules = ''
    KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="303a", MODE="0660", GROUP="input", TAG+="uaccess"
    KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="574c", MODE="0660", GROUP="input", TAG+="uaccess"
    # Bluetooth HID (HID-over-GATT) has no USB parent, so ATTRS{idVendor} never
    # matches — match the HID parent kernel name (bus 0005 = BT) instead.
    # Codex Micro configures over BLE HID; its USB-C is charge-only.
    KERNEL=="hidraw*", SUBSYSTEM=="hidraw", KERNELS=="0005:303A:*", MODE="0660", GROUP="input", TAG+="uaccess"
    KERNEL=="hidraw*", SUBSYSTEM=="hidraw", KERNELS=="0005:574C:*", MODE="0660", GROUP="input", TAG+="uaccess"
    SUBSYSTEM=="usb", ATTR{idVendor}=="303a", MODE="0660", GROUP="input", TAG+="uaccess"
    SUBSYSTEM=="usb", ATTR{idVendor}=="574c", MODE="0660", GROUP="input", TAG+="uaccess"
    SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", MODE="0660", GROUP="input", TAG+="uaccess"
    SUBSYSTEM=="tty", ATTRS{idVendor}=="574c", MODE="0660", GROUP="input", TAG+="uaccess"
    # Finalmouse (UltralightX dongle, VID 361d): its configurator talks over HID.
    KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="361d", MODE="0660", GROUP="input", TAG+="uaccess"
    SUBSYSTEM=="usb", ATTR{idVendor}=="361d", MODE="0660", GROUP="input", TAG+="uaccess"
  '';

  # Symlink /bin/bash for FHS compatibility (e.g. Claude Code plugin hooks)
  system.activationScripts.binbash = ''
    ln -sfn /run/current-system/sw/bin/bash /bin/bash
  '';

  # Automatic garbage collection
  nix.gc = {
    automatic = true;
    dates = "weekly";
    options = "--delete-older-than 14d";
  };

  # Deduplicate identical store files via hard links. The GC log reports
  # ~1.7 GiB already saved by incidental linking; making it automatic keeps
  # that compounding instead of depending on whatever last ran --optimise.
  # Runs on a timer rather than after every build so `nrs` stays fast.
  nix.optimise = {
    automatic = true;
    dates = [ "weekly" ];
  };

  # SSH — key-only auth
  services.openssh = {
    enable = true;
    settings = {
      PasswordAuthentication = false;
      PermitRootLogin = "no";
      KbdInteractiveAuthentication = false;
    };
  };

  # Fonts
  fonts.packages = with pkgs; [
    nerd-fonts.jetbrains-mono
    nerd-fonts.caskaydia-mono
    noto-fonts
    noto-fonts-cjk-sans
    noto-fonts-color-emoji
  ];

  # System packages
  environment.systemPackages = (
    with pkgs;
    [
      git
      gh
      wget
      curl
      htop
      btop
      unzip
      file
      killall
      pid-fan-controller
      croc
    ]
  );

  # Fish (needed at system level for login shell)
  programs.fish.enable = true;

  # Firefox
  programs.firefox.enable = true;
}
