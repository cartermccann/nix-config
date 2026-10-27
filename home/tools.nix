{
  config,
  pkgs,
  pkgs-unstable,
  comcreate-desktop-app,
  google-workspace-cli,
  fenix,
  herdr,
  zed-delta,
  delta-agent-history,
  ...
}:
let
  rustToolchain = fenix.packages.${pkgs.stdenv.hostPlatform.system}.stable.withComponents [
    "cargo"
    "clippy"
    "rustc"
    "rustfmt"
    "rust-analyzer"
    "rust-src"
  ];

  screamingFrog = pkgs.callPackage ../pkgs/screaming-frog-seo-spider { };
  grokCli = pkgs.callPackage ../pkgs/grok-cli { };
  grokBot = pkgs.callPackage ../pkgs/grok-bot { };
  slackMcpServer = pkgs.callPackage ../pkgs/slack-mcp-server { };
  rakazo = pkgs.callPackage ../pkgs/rakazo { };
  codexCli = pkgs-unstable.callPackage ../pkgs/codex { };
  t3code = pkgs-unstable.callPackage ../pkgs/t3code { };
  agentToolGuard = pkgs.callPackage ../pkgs/agent-tool-guard { };
  agentTools = pkgs.callPackage ../pkgs/agent-tools { };
  deltaUpstream = zed-delta.packages.${pkgs.stdenv.hostPlatform.system}.delta;
  deltaHistory = delta-agent-history.packages.${pkgs.stdenv.hostPlatform.system}.default;
  deltaApp = pkgs.symlinkJoin {
    name = "zed-delta-${deltaUpstream.version}";
    paths = [ deltaUpstream ];
    # Git's diff viewer already owns `delta`. Desktop entries use upstream's
    # absolute CLI path, so renaming the public command preserves URL handling.
    postBuild = ''
      mv "$out/bin/delta" "$out/bin/zed-delta"
    '';
  };
  codexUpdater = pkgs.writeShellApplication {
    name = "codex-update";
    runtimeInputs = with pkgs; [
      python3
      curl
      nix
      git
      nh
    ];
    text = ''
      exec python3 ${../scripts/codex-update.py} --repo ${pkgs.lib.escapeShellArg "${config.home.homeDirectory}/nix-config"} "$@"
    '';
  };

  # OpenCode v2 owns its binary in ~/.opencode/bin so its built-in updater can
  # replace it in place: a /nix/store copy is read-only, and nixpkgs `opencode`
  # is still the 1.x line. This launcher is the declarative half. It bootstraps
  # that install on first run with upstream's installer (the same script the
  # updater runs), then execs it from that exact path, because the updater only
  # recognises the install when process.execPath is ~/.opencode/bin/opencode.
  # The binary is glibc-linked and runs through nix-ld (modules/common.nix).
  # Updates install automatically only with `"update": "auto"` in
  # ~/.config/opencode/opencode.json; v2's default is "notify".
  #
  # The installer probes `opencode --version`, which lands back here; the
  # bootstrap flag makes that probe fail instead of recursing into the installer.
  opencodeLauncher = pkgs.writeShellApplication {
    name = "opencode";
    runtimeInputs = with pkgs; [
      curl
      gnutar
      gzip
    ];
    text = ''
      bin="$HOME/.opencode/bin/opencode"
      if [ ! -x "$bin" ]; then
        [ -z "''${OPENCODE_LAUNCHER_BOOTSTRAP:-}" ] || exit 1
        installer=$(mktemp)
        trap 'rm -f "$installer"' EXIT
        curl -fsSL -o "$installer" https://opencode.ai/v2/install
        OPENCODE_LAUNCHER_BOOTSTRAP=1 bash "$installer" --no-modify-path >&2
        rm -f "$installer"
        trap - EXIT
      fi
      exec "$bin" "$@"
    '';
  };

  # OpenCode 1.x beside the v2 launcher, for T3 Code. T3 0.0.45 starts
  # `opencode serve` and calls /global/health, which v2 dropped (its server
  # answers every route with the web UI's HTML), so T3's OpenCode provider
  # points binaryPath at `opencode1`. Remove once a stable T3 ships the
  # OpenCode 2 client (merged upstream 2026-10-01, nightly-only so far).
  # Pinned in pkgs/opencode1/sources.json; auseklis-update bumps it.
  opencode1Pkg = pkgs-unstable.callPackage ../pkgs/opencode1 { };
  opencode1 = pkgs.writeShellScriptBin "opencode1" ''
    exec ${opencode1Pkg}/bin/opencode "$@"
  '';

  comcreateDesktopUpstream =
    comcreate-desktop-app.packages.${pkgs.stdenv.hostPlatform.system}.default;
  comcreateDesktop = pkgs.symlinkJoin {
    name = "comcreate-desktop";
    paths = [ comcreateDesktopUpstream ];
    nativeBuildInputs = [ pkgs.makeWrapper ];
    postBuild = ''
      # Keep GTK native to the current session. On NVIDIA/Wayland, disabling
      # explicit sync avoids WebKitGTK's protocol error without turning off
      # the accelerated DMA-BUF backing store.
      wrapProgram "$out/bin/comcreate-desktop" \
        --prefix LD_LIBRARY_PATH : "${pkgs.lib.makeLibraryPath [ pkgs.libayatana-appindicator ]}" \
        --prefix GIO_EXTRA_MODULES : "${pkgs.glib-networking}/lib/gio/modules" \
        --prefix GST_PLUGIN_SYSTEM_PATH_1_0 : "${pkgs.gst_all_1.gst-plugins-base}/lib/gstreamer-1.0" \
        --set-default WEBKIT_DISABLE_DMABUF_RENDERER 0 \
        --set-default __NV_DISABLE_EXPLICIT_SYNC 1
    '';
  };

  # electron_44 comes from unstable because Granola 7.626.1 ships Electron
  # 44.4.2 and unstable's is 44.5.1. Same major, so the ABI holds, but stay as
  # close as the channel allows.
  granola = pkgs.callPackage ../pkgs/granola {
    electron = pkgs-unstable.electron_44-bin;
  };
in

{
  home.packages = with pkgs; [
    # Languages & runtimes
    python3
    uv
    go
    nodejs_24
    bun
    pnpm
    deno
    typescript
    biome
    rustToolchain

    # CLI staples
    # Kept despite zero interactive history: rg/fd/jq/yq are what coding agents
    # reach for constantly, and atuin only records what *you* type.
    ripgrep
    fd
    jq
    yq
    tree
    eza
    dust # backs the "Disk Usage" desktop entry in home/common.nix
    tree-sitter
    yazi
    glow # CLI markdown reader (not wired to a keybind)
    gum # shell prompts/confirm (gwr worktree fn, menu scripts)
    procs # modern ps; used by home/codex-desktop.nix

    # Dev workflow
    lazygit
    lazydocker # backs the "Docker" desktop entry in home/common.nix
    git-lfs
    bruno # Git-friendly API client (GUI — usage not visible in shell history)

    # Networking
    netcat-openbsd

    # Cloud & deploy
    flyctl
    google-cloud-sdk # GA4/GSC/BigQuery client work
    awscli2 # SES/IAM for client infra (Silverado mail)

    # Nix tooling
    nh
    nixfmt
    nix-output-monitor

    # AI / LLM
    opencodeLauncher # OpenCode v2, self-updating in ~/.opencode/bin (see the let binding)
    opencode1 # OpenCode 1.x as `opencode1`, the T3 Code provider binary (see the let binding)
    pkgs-unstable.bubblewrap # PATH bwrap preferred by Codex over its bundled fallback
    codexCli
    codexUpdater
    agentToolGuard
    agentTools
    pkgs-unstable.cliproxyapi # OpenAI/Gemini/Claude/Codex-compatible local proxy
    pkgs-unstable.ollama # CLI client only (server is the podman container) — unstable to stay near the 0.30.x server API
    grokCli # official xAI Grok CLI (grok/agent) — prebuilt binary in ../pkgs/grok-cli
    grokBot # Grok Bot desktop agent — third-party Linux port, see ../pkgs/grok-bot
    slackMcpServer # korotovsky slack-mcp-server (Go), used by the slack-mcp entry in ~/.mcp.json
    rakazo # Rakazo desktop client, unpacked upstream AppImage, see ../pkgs/rakazo
    pkgs-unstable.cursor-cli # Cursor CLI (binary is `cursor-agent`) — unfree prebuilt, tracks lab releases
    t3code # T3 Code desktop GUI with its native terminal runtime dependency
    deltaApp # Zed Delta desktop app; CLI is zed-delta to preserve Git's delta
    deltaHistory

    # Meetings & notes
    granola # macOS build repacked onto nixpkgs Electron, see ../pkgs/granola

    # Media & recording
    # obs-studio lives in modules/media.nix (system-level) — it was declared in
    # both, so this copy was redundant.
    yt-dlp
    ffmpeg # audio/video handling (yt-dlp post-processing, media skills)
    pandoc # document format conversion
    poppler-utils # pdftotext/pdfinfo

    # SEO
    screamingFrog # proprietary crawler, bundled JDK

    # Games
    prismlauncher # Minecraft Java launcher (Microsoft login, bundles its own JDKs)

    # Rice — trimmed to the ones that are actually wired to something.
    # Dropped (zero invocations, nothing references them): pipes-rs, cbonsai,
    # asciiquarium, sl, lolcat, peaclock, tty-clock.
    cava # rice-dashboard pane (home/niri.nix); Super+Ctrl+D in Hyprland
    cmatrix
    figlet # required by scripts/comcreate-banner.sh (runs on every bash shell)

    # From flake inputs
    comcreateDesktop
    google-workspace-cli.packages.${pkgs.stdenv.hostPlatform.system}.default # gws
    herdr.packages.${pkgs.stdenv.hostPlatform.system}.default # workspace manager for AI agents
  ];
}
