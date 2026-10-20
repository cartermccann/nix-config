# Agent tools

## Map and scope

The first release implements the approved `doctor`, `run`, and `search` map.
One Python standard-library package is exposed as a Nix executable. Each command
returns compact versioned JSON. The skill at `skills/agent-tools/SKILL.md` carries
the usage decisions; global agent instructions only link to it.

Data flow: CLI validation → command module → local Git, process, or ripgrep →
bounded result → one JSON object. There is no daemon, database, network service,
automatic retry, or automatic package installation. The existing Gmail guard
is separate. `check` and `json-edit` remain later candidates. Daily collection,
activation checks and recovery scenarios are separate commands documented in
[agent-health.md](agent-health.md).

## Run now and install

Before activating the Nix package, run the source entrypoint from any directory:

```sh
python3 ~/nix-config/scripts/agent-tools/agent-tools --help
```

The package lives at `pkgs/agent-tools/default.nix` and is included in
`home/tools.nix`. After `nh os build ~/nix-config` passes, the operator applies
the system with `nrs`. Then `agent-tools` is on PATH with its Git/ripgrep runtime
dependencies. No fish aliases are needed. The source entrypoint requires Python,
and the corresponding Git/ripgrep binaries already on PATH.

## doctor

```sh
agent-tools doctor --repo . --require python3 --require rg
```

Reports the requested path, canonical repository/worktree and Git metadata,
branch/detached/unborn state, dirty/conflict flags and bounded counts, required
executable availability, Python identity, package-script names, lockfiles, and
presence of a declared dev environment. It does not print script values,
environment values, remotes or credentials.

Git optional locks, filesystem monitors, untracked cache and hooks are disabled
for the read-only probes. Git directory/index environment overrides are removed.
Package JSON reads are capped at 1 MiB and 100 script names. Git status is capped
at 64 KiB and 1,000 entries, with a 5-second per-call / 20-second overall budget.
Partial counts are lower bounds; unknown dirty/conflict states remain unknown.
A dirty checkout can have diagnostic status `ok`; partial/unavailable probes
produce status `error`, exit 2. This does not certify that a repository is safe
to commit, clean, current with upstream, or ready to deploy.

## run

```sh
agent-tools run --cwd . --timeout 120 -- npm test
agent-tools run --cwd . --max-output-bytes 8192 -- git status --short
```

Arguments after `--` are argv, never a shell program. If a task genuinely needs
shell syntax, explicitly pass `bash -c` and the intended program; the helper does
not make that shell program safe. A parent shell can still expand unquoted text
before the helper receives it, so normal quoting rules still apply.

The JSON records `status`, raw `exit_code`, `signal`, `duration_ms`, `timed_out`,
`stdout`, `stderr`, and independent truncation flags. `completed` means execution
ended, not success. Nonzero test/command statuses remain nonzero.

Defaults: 120 seconds and a 65,536-byte prefix per stream. Output limit range is
1–1,048,576 bytes per stream; timeout must be positive/finite and at most 86,400
seconds. Both streams are drained while only bounded prefixes are retained.
Invalid UTF-8 uses replacement characters. Nothing is logged to disk by default.
Captured output can contain whatever the requested command prints; this is not
a secret redactor.

Each invocation owns a new process group. Deadline, normal completion, SIGINT,
and SIGTERM clean up group members, including descendants holding pipes open.
Cleanup has a short bounded grace period after the execution deadline. Programs
that deliberately create a separate session escape this ownership; this is not
a sandbox. Signal handlers are installed for main-thread calls only.
Use native harness sessions for servers and persistent/background processes.

Exit status is the child's status for ordinary execution. Signal exits use
128+signal, timeout 124, interruption 130, invalid/setup failure 2, and missing
executable 127. The JSON distinguishes child-status collisions with these values.

## search

```sh
agent-tools search --root . --literal 'get_updates' --limit 40
agent-tools search --root ./src --literal 'TODO' --glob '*.py' --max-files 500
```

Uses installed ripgrep for file discovery and literal matches. The root is
required; filesystem root and the home directory are rejected. Default ignore
rules remain active. `--hidden` includes hidden files, excluding `.git` contents.
`--follow` only accepts symlink targets inside the root. There is no regex mode
in the first release.

Defaults: 40 result lines, 2,000 files, 32 MiB of selected file sizes, 1 MiB per
file, 65,536 bytes of raw match output, and a 10-second overall search deadline.
The enumeration output cap is 256 KiB. File selection sizes are a snapshot;
concurrently modified files can change before ripgrep reads them. Ripgrep also
enforces the per-file size bound. The byte field is `selected_bytes`, not measured
I/O. Results include relative paths, line numbers and previews capped at 400
characters; preview truncation is explicit.

Statuses are `matched`, `no_matches`, `partial`, and `error`. Exit 0 means a
complete search, including zero matches; exit 3 means partial coverage; invalid
input/root exits 2, and missing ripgrep exits 127. Raw enumeration/batch exit codes
remain in the JSON. Errors, unreadable paths, symlink escapes and caps cannot
become a complete no-match result. Binary and ignored files retain normal
ripgrep semantics, so completeness always means completeness within that scope.
Narrow the root/glob before raising budgets.

## Maintain and verify

```sh
cd ~/nix-config/scripts/agent-tools
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

The tests exercise actual Bash/fish invocation, metacharacters and cwd handling,
nonzero exits, dual-stream output bounds, process-group cleanup, Git index
preservation, worktrees/conflicts, metadata limits, and search completeness.
The Nix build runs this suite too. No external accounts or real mail are touched.

Primary contracts: [Python subprocess](https://docs.python.org/3.13/library/subprocess.html)
and [ripgrep exit statuses](https://github.com/BurntSushi/ripgrep/discussions/2675).
