{
  config,
  lib,
  pkgs,
  user,
  ...
}:

let
  llm = import ../lib/llm-models.nix;

  shellAliases = {
    # Navigation
    ".." = "cd ..";
    "..." = "cd ../..";

    # Git
    gs = "git status";
    ga = "git add";
    gc = "git commit";
    gp = "git push";
    gl = "git log --oneline --graph";
    gd = "git diff";
    gb = "git branch";

    # Docker
    dc = "docker-compose";
    dps = "docker ps";
    dimg = "docker images";
    dlog = "docker logs";

    # Nix
    nrs = "nh os switch ~/nix-config";
    update = "nh os switch ~/nix-config --update";

    # Modern replacements.
    # Dropped as unused over a full history window: ll (fish ships its own `la`,
    # which is what actually gets typed), df=duf, http=xh, md=glow.
    ls = "eza --icons";
    cat = "bat";
    grep = "rg";
    y = "yazi";

    # Ollama chat (server runs in the rootful podman container; the host CLI
    # from tools.nix talks to its API on 127.0.0.1:11434 — no sudo needed).
    # Model tags come from lib/llm-models.nix, same list the preloader pulls.
    # Only `ai` survives; qwen/coder/qwy were never invoked. `fim` stays in the
    # preload list because neovim's minuet completion uses it directly.
    ai = "ollama run ${llm.daily}";
  };

  comcreateBanner = pkgs.writeShellScriptBin "comcreate-banner" (
    builtins.readFile ../scripts/comcreate-banner.sh
  );

  # "#3b6bff" -> "59;107;255" for the ANSI 24-bit escapes in the banner.
  pal = import ../lib/palette.nix;
  ansiRgb =
    hex:
    let
      h = lib.removePrefix "#" hex;
    in
    lib.concatStringsSep ";" (
      map (i: toString (lib.fromHexString (builtins.substring i 2 h))) [
        0
        2
        4
      ]
    );

  # Host wordmark shown on each new interactive shell. This replaced `ff`, a
  # wrapper that replayed fastfetch line-by-line with a sleep between lines —
  # so every terminal paid an animation plus a full system probe before it was
  # usable. Plain `fastfetch` is still installed (modules/apps.nix) for when
  # the system readout is actually wanted.
  perkonsBanner = pkgs.writeShellScriptBin "perkons-banner" (
    builtins.replaceStrings
      [ "@fill0@" "@fill1@" "@shadow@" ]
      [ (ansiRgb pal.base0D) (ansiRgb pal.accentBright) (ansiRgb pal.base0F) ]
      (builtins.readFile ../scripts/perkons-banner.sh)
  );
in
{
  home.packages = [
    comcreateBanner
    perkonsBanner
  ];

  programs.fish = {
    enable = true;
    inherit shellAliases;
    interactiveShellInit = ''
      function fish_greeting
        if not set -q TMUX
          perkons-banner
        end
      end
      set -gx NH_FLAKE $HOME/nix-config
      set -gx GOPATH $HOME/.local/share/go
      set -gx GOBIN $HOME/.local/bin
      fish_add_path $HOME/.local/bin

      # Autosuggestion color — visible but subtle on dark backgrounds
      set -U fish_color_autosuggestion 90909a

      # tmux dev layout
      function dev
        tmux new-session -d -s dev -c (pwd)
        tmux split-window -h -p 40
        tmux split-window -v -p 50
        tmux select-pane -t 1
        tmux send-keys -t dev:1.1 "nvim" Enter
        tmux select-pane -t dev:1.1
        tmux attach -t dev
      end

      # One canonical tmux session
      function t
        tmux attach; or tmux new -s work
      end

      # Heavy mode: the GPU fits one of ollama or llama-server (Qwen3.6-35B MoE),
      # so these swap between them. First run downloads ~24 GB;
      # watch with: sudo podman logs -f llama-heavy
      function heavy --description "swap ollama out, big MoE model in"
        sudo systemctl stop podman-ollama
        if not sudo systemctl start podman-llama-heavy
          echo "llama-heavy failed to start — restoring ollama"
          sudo systemctl start podman-ollama
          return 1
        end
        echo "heavy mode up: http://127.0.0.1:8089 (web UI + OpenAI-compatible API)"
        echo "back to normal: heavy-stop"
      end

      function heavy-stop --description "swap llama-heavy out, ollama back in"
        sudo systemctl stop podman-llama-heavy
        sudo systemctl start podman-ollama
        echo "heavy mode down, ollama restored"
      end

      # Git worktrees as `repo--branch` sibling dirs.
      # (named gwa/gwr — ga/gd are taken by git add / git diff aliases)
      function gwa --description "git worktree add -b <branch> ../<repo>--<branch>"
        if test (count $argv) -ne 1
          echo "usage: gwa <branch>"; return 1
        end
        set -l repo (basename (git rev-parse --show-toplevel)); or return 1
        set -l dir ../$repo--$argv[1]
        git worktree add -b $argv[1] $dir; and cd $dir
      end

      function gwr --description "remove current worktree dir + its branch"
        set -l dir (basename $PWD)
        set -l branch (string split --max 1 -- '--' $dir)[2]
        if test -z "$branch"
          echo "gwr: '$dir' is not a repo--branch worktree dir"; return 1
        end
        gum confirm "Remove worktree $dir and delete branch $branch?"; or return 1
        cd ..
        git -C (string replace -- "--$branch" "" $dir) worktree remove $dir --force
        and git -C (string replace -- "--$branch" "" $dir) branch -D $branch
      end
    '';
  };

  programs.bash = {
    enable = true;
    inherit shellAliases;
    initExtra = ''
      export PATH="$HOME/.local/bin:$PATH"
      if [[ $- == *i* && -z "$TMUX" ]]; then perkons-banner; fi
    '';
  };

  stylix.targets.starship.enable = false;
  stylix.targets.neovim.enable = false;
  stylix.targets.niri.enable = false;

  # Starship: the Auseklis prompt.
  #
  # Ported from the atlas prompt in the gentoo dotfiles (config/starship/
  # body.toml) and retinted from lib/palette.nix. The shape is that file's:
  # rounded caps rather than pointed separators, and each group its own
  # detached pill instead of one continuous powerline bar — which is the same
  # read as the Caelestia bar's separated surfaces and the compositor's
  # squircle rounding, rather than the Tokyo Night preset this replaces.
  #
  # Every glyph goes through fromJSON as an ASCII escape. Literal Nerd Font
  # PUA characters do not survive editing round-trips (they arrive stripped),
  # which is silent — you get a config that parses and renders nothing. All of
  # the codepoints below were checked against JetBrainsMono Nerd Font, the
  # family home/ghostty.nix actually renders with, via
  # `fc-list ':charset=<cp>:family=JetBrainsMono Nerd Font'`.
  programs.starship = {
    enable = true;
    settings =
      let
        g = builtins.fromJSON ''
          {
            "capL":  "\uE0B6",
            "capR":  "\uE0B4",
            "nix":   "\uF313",
            "shell": "\uF1B2",
            "git":   "\uE0A0",
            "node":  "\uE718",
            "rust":  "\uE7A8",
            "go":    "\uE627",
            "py":    "\uE606",
            "timer": "\uF017"
          }
        '';

        # A language version reads as ambient context, so the pills sit on the
        # quietest surface with dim text. One helper because all four are
        # identical apart from the glyph.
        langPill = symbol: {
          symbol = "${symbol} ";
          style = "bg:surface fg:subtext";
          format = " [${g.capL}](fg:surface)[$symbol$version ]($style)[${g.capR}](fg:surface)";
        };
      in
      {
        palette = "auseklis";
        add_newline = true;
        command_timeout = 800;

        # One line, prompt character trailing. No $time: both bars already show
        # the clock, and a third copy on every prompt line is noise.
        format = builtins.concatStringsSep "" [
          "$os"
          "$directory"
          "$git_branch"
          "$git_status"
          "$nix_shell"
          "$nodejs"
          "$rust"
          "$golang"
          "$python"
          "$cmd_duration"
          "$character"
        ];

        # Head and directory are one cobalt pill: the left cap opens it, the
        # OS glyph and path share the fill, the right cap closes it.
        os = {
          disabled = false;
          format = "[${g.capL}](fg:accent)[$symbol](bg:accent fg:ground)";
          symbols.NixOS = "${g.nix} ";
        };

        directory = {
          style = "bg:accent fg:ground bold";
          format = "[$path ]($style)[${g.capR}](fg:accent)";
          truncation_length = 3;
          truncate_to_repo = true;
          truncation_symbol = "…/";
          read_only = " ";
        };

        # Git on the raised surface, one step up from the language pills —
        # branch in full text, status in the brighter cobalt so dirt is the
        # thing that catches the eye.
        git_branch = {
          symbol = "${g.git} ";
          style = "bg:raised fg:text";
          format = " [${g.capL}](fg:raised)[$symbol$branch ]($style)";
        };

        git_status = {
          style = "bg:raised fg:accent_soft";
          format = "[$all_status$ahead_behind]($style)[${g.capR}](fg:raised)";
        };

        # Dev shell. Cyan is the scheme's tertiary — the same role that colours
        # the Caelestia clock — so "you are inside a nix shell" reads as a
        # different kind of fact from a language version, not just another pill.
        #
        # `heuristic` stays OFF. It is the documented way to catch shells that
        # do not export IN_NIX_SHELL, but what it actually tests is whether the
        # environment looks nix-shaped, which on NixOS is always true: with it
        # on the pill rendered in $HOME, outside any project, with the
        # environment scrubbed. An indicator that is always lit indicates
        # nothing. It is also unnecessary here — nix-direnv's `use flake`
        # sources `nix print-dev-env`, which does export IN_NIX_SHELL=impure
        # (verified against a real project), so the plain signal covers the
        # direnv workflow this machine actually uses.
        nix_shell = {
          symbol = "${g.shell} ";
          pure_msg = "pure";
          impure_msg = "shell";
          unknown_msg = "shell";
          style = "bg:surface fg:cyan";
          format = " [${g.capL}](fg:surface)[$symbol$state ]($style)[${g.capR}](fg:surface)";
        };

        nodejs = langPill g.node;
        rust = langPill g.rust;
        golang = langPill g.go;
        python = langPill g.py;

        # No fill, so it sits next to the pills rather than being flung to the
        # right margin — a one-line prompt has nowhere sensible to fling it.
        #
        # It IS a pill, though, and that is load-bearing rather than cosmetic:
        # ghostty runs at background-opacity 0.68, so anything drawn with the
        # default cell background sits on the wallpaper and its contrast is
        # whatever happens to be behind the window. An explicit background is
        # composited opaque, which is the only reason the other pills stay
        # readable. This module used to be bare text in base03 and was
        # effectively invisible (~1.3:1 over a mid-tone wallpaper). Amber, not
        # another grey: a duration only prints past min_time, so it always
        # means "that took a while" and should read as its own kind of fact.
        cmd_duration = {
          min_time = 2000;
          style = "bg:surface fg:warn";
          format = " [${g.capL}](fg:surface)[${g.timer} $duration ]($style)[${g.capR}](fg:surface)";
        };

        character = {
          success_symbol = "  [❯](bold fg:accent)";
          error_symbol = "  [❯](bold fg:err)";
          vimcmd_symbol = "  [❮](bold fg:cyan)";
        };

        aws.disabled = true;

        # Role names, not colour names, so the whole prompt retints from
        # lib/palette.nix in one place — same discipline as the atlas palette.
        palettes.auseklis = {
          accent = pal.base0D; # cobalt — directory pill
          accent_soft = pal.accentBright; # git status
          ground = pal.base00; # text on the cobalt pill
          surface = pal.base01; # language + nix-shell pills
          raised = pal.base02; # git pill, one step up
          text = pal.base05;
          subtext = pal.textDim;
          warn = pal.base0A; # amber — command duration
          cyan = pal.base0C; # dev shell
          err = pal.base08; # failed command
        };
      };
  };

  programs.fzf = {
    enable = true;
    enableBashIntegration = true;
    enableFishIntegration = true;
  };

  # atuin (rich shell history)
  programs.atuin = {
    enable = true;
    enableBashIntegration = true;
    enableFishIntegration = true;
  };

  # zoxide (smart cd)
  programs.zoxide = {
    enable = true;
    enableBashIntegration = true;
    enableFishIntegration = true;
  };

  # direnv for per-project nix shells
  programs.direnv = {
    enable = true;
    nix-direnv.enable = true;
  };
}
