---
name: agent-tools
description: Use local repository diagnostics, bounded foreground command execution, and scoped literal search when investigating shell failures or unfamiliar checkouts. Supports Bash, fish, Claude, Codex, and OpenCode.
---

# Agent tools

Use these helpers when they remove repeated shell plumbing. Ordinary short
commands and native file/search tools remain appropriate.

Executable: `agent-tools`. Before the Nix package is activated, substitute
`python3 ~/nix-config/scripts/agent-tools/agent-tools` for that executable.
Use `--help` or `<subcommand> --help` for options. Results, including argument
errors, emit one JSON object with `schema_version: 1`. Help/version use plain text.

- **Unfamiliar checkout or missing prerequisite:**
  `agent-tools doctor --repo . --require python3 --require rg`
  Inspect repository/worktree, dirty/conflict flags, required tools, and declared
  environment. This reads state; it never installs dependencies or executes
  project scripts. Dirty files do not mean the diagnostic itself failed.
- **Foreground command with bounded output and a deadline:**
  `agent-tools run --cwd . --timeout 120 -- npm test`
  Arguments after `--` are literal argv. No pipes, substitutions, wildcard or
  environment expansion happen inside the runner. A result with
  `status: completed` can still have a failing `exit_code`; inspect both.
  It owns and cleans up the process group, so use the native harness for dev
  servers, persistent jobs and their session-ID wait tools.
- **Broader repository discovery:**
  `agent-tools search --root . --literal 'get_updates' --limit 40`
  Search is literal and scoped, respecting ignore rules. Hidden files and
  in-root symlinks require explicit options. `no_matches` means no match within
  the selected scope. `partial` means coverage is incomplete: inspect reasons,
  narrow the search, or raise only the specific budget needed.

Exit codes: doctor `0` complete / `2` blocked or incomplete; run preserves the
child's status (`124` timeout, `130` interrupted, `127` missing executable);
search `0` complete including no matches / `3` partial / `2` invalid request
or `127` missing executable. Child statuses can collide with wrapper statuses,
so the JSON fields are authoritative.

No retries, package installation, credential changes, or action authorization
are provided by this toolkit. Output is returned to the caller, not persisted.
Existing tool permissions and task scope still apply.

Read `~/nix-config/docs/agent-tools.md` for output contracts, exact limits,
verification, and implementation boundaries. Gmail/Monday direct-MCP policy
remains in the separate `agent-tool-guard` hooks.
