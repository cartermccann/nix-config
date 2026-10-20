# Agent health and recovery

## Scope and delivery

`agent-health` collects aggregate tool outcomes from the existing local native
stores. `agent-activation` checks guard registration and, when requested, Codex
hook trust. `agent-recovery` exercises controlled failure scenarios. None of
these commands sends mail, modifies provider state, approves hooks, or installs
dependencies.

The Home Manager module is `home/agent-health.nix`. The daily timer is declared
for **08:15 America/Denver**, with up to three minutes of jitter and catch-up
after downtime. It becomes active after the NixOS configuration is switched.
Check the active unit before calling the schedule installed:

```sh
systemctl --user list-timers agent-health-collect.timer
systemctl --user status agent-health-collect.service
journalctl --user -u agent-health-collect.service -n 20 --no-pager
```

Validate configuration with `nh os build ~/nix-config`. Carter applies the
privileged switch with `nrs`. No credential or hook trust records are changed by
that switch.

## Commands

```sh
agent-activation --live-codex
agent-health collect
agent-health report
agent-recovery verify
agent-recovery manifest
```

Before switching, the same tools run from source:

```sh
python3 ~/nix-config/scripts/agent-activation/activation.py --live-codex
python3 ~/nix-config/scripts/agent-health/agent-health collect
python3 ~/nix-config/scripts/agent-health/agent-health report
python3 ~/nix-config/scripts/agent-recovery/agent-recovery verify
```

The activation command reports registration, synthetic handler checks, and
Codex native `hooks/list` trust separately. `modified` or `untrusted` requires
review through `/hooks` in the corresponding Codex home. Do not edit trust
records to bypass that review. Claude and OpenCode registration does not prove
an already-running session reloaded its hooks; restart those sessions and check
actual enforcement separately. Synthetic checks use temporary guard state.

## Daily records

The collector reconciles the **two most recent completed UTC days**, using
half-open time windows. It replaces a source/day snapshot instead of adding
counts from repeated runs. It retains 90 days under
`~/.local/state/agent-health/` (directory 0700, files 0600).

Sources remain separate: Codex main, Codex CLIProxy, Claude, OpenCode main,
OpenCode stable, and T3. T3 projections remain separate within their own report.
Never add those totals together: the same call can appear in multiple stores.

Each snapshot includes its window, observation time, latest collection attempt,
coverage information and last successful snapshot. A failed or partial attempt
does not replace successful evidence. A last-success snapshot can be stale;
read its timestamp alongside the latest attempt. Exit 2 means partial or failed
collection, and systemd will show a failure instead of silently calling it
healthy. Exit 3 means another collector holds the lock.

Only fixed aggregate fields, approved tool names, counts, timings and diagnostic
categories are persisted. No raw prompts, arguments, results, credentials or
call IDs are exported. The Claude adapter reads bounded local JSONL history in
memory to count tool metadata; it does not export that content. Unknown tool
names are omitted from named groups but their calls remain in overall counts.

Collection has per-source limits and an overall deadline; caps and missing
sources remain visible. Sources with no retained calls are not proof that no
calls happened. Data lost before collection cannot be reconstructed, and late
updates older than the two-day reconciliation window are not automatically
backfilled.

## Interpreting improvements

Compare the same source, tool and coverage across windows. Expected nonzero
search exits and intentionally failing tests contribute to some recorded
failure counts. Those counts are not infrastructure outage rates or model
rankings. Unknown outcomes are not successes.

Native execution durations and message/lifecycle intervals are different
measurements. Claude message intervals and T3 lifecycle timings are explicitly
proxies; do not compare them with native execution latency. Helper adoption is
visible only where the source records its invocation. The collector cannot
reliably attribute repeated attempts to one intention or identify every nested
helper call.

The recovery suite's ten mechanical cases verify helper behavior. Its twelve
portable scenario prompts and result contract support later agent evaluations.
Stale edits and browser state require actual captured agent traces and remain
unrun until those are supplied. A scored submission is reported evidence, not
independently authenticated behavior. See [agent-recovery.md](agent-recovery.md).

## Verification

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts/agent-health/adapters python3 -m unittest discover -s scripts/agent-health/tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/agent-activation -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/agent-recovery -p 'test_*.py' -v
python3 scripts/agent-recovery/agent-recovery verify
```

The Nix derivations run the collector/activation unit checks and recovery
verification during package builds. Fixture tests cover replacement rather
than double counting, failed collection retention, privacy filtering, schema
limits, process cleanup, hostile arguments and incorrect-result rejection.
