# Agent tool guard

Shared local Gmail admission control for Codex, Claude Code, and OpenCode.
The same Python/SQLite implementation works from Bash and fish; shell aliases
are not involved. It never executes, retries, or replays a connector call.

## Policy

- At most one admitted Gmail invocation at a time across these local runtimes,
  with at least 750 ms between starts. Calls waiting longer than five seconds
  are denied with an actionable retry delay.
- Explicit HTTP 429 or `RATE_LIMIT_EXCEEDED` failures apply a shared cooldown:
  60 seconds initially, exponential backoff up to 15 minutes, with 10% jitter.
  A supplied longer Retry-After takes precedence. A successful call resets
  the strike count. Account switching does not bypass the shared cooldown.
- Validate current Gmail read inputs and the exact current Monday connector
  `get_updates` contract (`link_id`, singular `objectId`, `objectType`).
  Mutation tools are paced but are outside the read-schema validator.
- Existing runtime permissions still apply. A successful pre-hook emits no
  permission override; this guard cannot authorize a tool action.

State lives at `~/.local/state/agent-tool-guard/state.sqlite`, or under
`$XDG_STATE_HOME`. The directory is mode 0700 and the database mode 0600.
Only hashed owner/account identities, lease IDs, timestamps, and counters are
stored. Arguments and message bodies are not persisted. Symlinked or insecure
state files fail closed. `AGENT_TOOL_GUARD_STATE` overrides the database for tests.

## Installation

The Nix package is declared in `home/tools.nix`. Validate using
`nh os build ~/nix-config`; apply the system through the usual operator `nrs`.
The initial hook registration uses the stable source path and existing Python
so it does not require a privileged system switch:

```sh
python3 ~/nix-config/scripts/agent-tool-guard/guard/install.py \
  --python /etc/profiles/per-user/cjm/bin/python3 \
  --hook /home/cjm/nix-config/scripts/agent-tool-guard/guard/hook.py \
  --codex-home /home/cjm/.codex \
  --codex-home /home/cjm/.codex_cliproxy \
  --claude-settings /home/cjm/.claude/settings.json \
  --opencode-plugin /home/cjm/.config/opencode/plugins/tool-guard.js
```

The installer preserves unrelated configuration and is idempotent. Codex
requires a `/hooks` review in each configured home after registration. It
does not edit hook trust records. Restart Claude/OpenCode sessions to load new
hooks/plugins. Package commands after the Nix switch are `agent-tool-guard`
and `agent-tool-guard-install`; both accept `--help`.

## Runtime coverage and limits

Verified interfaces: Codex 0.163.0, Claude Code 2.1.304, OpenCode 2.0.32 and
the legacy OpenCode 1.19.3 plugin interface. Hooks apply only to calls routed
through those runtimes and loaded/trusted hooks, not arbitrary direct API use.

Codex 0.163.0 skips PostToolUse for failed MCP calls. Before each new Gmail
admission, the adapter checks a read-only projection of the newest 512 native
thread items in `.codex` and `.codex_cliproxy`, limited to the last five minutes
and a 750 ms observer budget. It matches the active owner and lease start time;
unrelated completion records cannot release a lease. It extracts only MCP
metadata and bounded explicit failure details. It does not scan raw transcripts.
Known standard `mcp__server__tool` and `server.tool` names are reconstructed;
renamed/deduplicated custom aliases may not match. Missing, lagging, or busy
native databases can delay completion detection.

OpenCode V2 supplies failure events. V1 supplies MCP error results but thrown
transport errors skip its after-hook. Abandoned leases expire after 120 seconds
to avoid permanently blocking mail. This is an availability fallback, not proof
that the underlying call stopped: a call lasting over 120 seconds can overlap a
new admission. Failures missed by hooks/native completion records cannot set
a cooldown. Oversized hook response values are discarded to bound memory.

This reduces locally generated bursts; it does not guarantee that a provider's
quota is available, repair authentication, or replace schema discovery when
connectors change. Update validators and tests when contracts change.

## Verification

```sh
cd ~/nix-config/scripts/agent-tool-guard
python3 -m unittest discover -s guard
node guard/test_plugin.mjs
```

Tests cover independent-process contention, durable cooldown, expiry, ownership
fences, private state, current schemas, Codex native completion recovery, hook
registration preservation/idempotence, and both OpenCode plugin interfaces.
All fixtures use synthetic metadata. No mail is sent or modified by the tests.

## Installed snapshot, 2026-10-20

- 48 Python tests and five Node integration assertions passed; independent
  review found no remaining blocking defects.
- Both Codex homes returned both guard events from native `hooks/list`, with
  zero loader warnings. Main home trust status: `modified`; T3 Hub home:
  `untrusted`. Trust activation remains an operator action.
- All seven installed Codex/Claude command definitions were invoked with
  synthetic inputs and returned the expected denial/completion behavior.
- A second installer run reported no changes. Existing unrelated hooks were
  preserved. No credentials, hook trust records, or system activation changed.
- Live provider behavior and post-activation failure-rate improvements are
  not measured by these local checks.
