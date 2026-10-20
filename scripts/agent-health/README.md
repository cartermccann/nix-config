# Agent tool health

A Python standard-library collector for aggregate local tool outcomes. It reads known local stores in bounded, read-only operations and writes private JSON snapshots. It never calls provider APIs, changes agent settings, retries user operations, or exports prompts, command arguments, results, call IDs, session IDs, credentials, or raw error messages.

## Run

```bash
python3 ~/nix-config/scripts/agent-health/agent-health collect
python3 ~/nix-config/scripts/agent-health/agent-health report
# After the Nix package is activated:
agent-health collect
agent-health report
```

`--home PATH` selects a fixture or different account root, not a recursive discovery root. `--state-dir PATH` defaults to `$HOME/.local/state/agent-health`. `--source` accepts `all`, `codex`, `codex_cliproxy`, `claude`, `opencode`, `opencode_stable`, or `t3`. `--as-of 2026-10-20T17:00:00Z` fixes the window anchor; `observed_at` is the actual collection time. Both commands emit JSON. Exit 0 means all requested source/day reads completed within coverage bounds, 2 means missing/partial/error coverage, and 3 means another collector holds the lock. Code 0 does not establish complete provider telemetry or zero agent defects.

## Scheduling and storage contract

Each run revisits the two latest **closed UTC days** as `[start, end)` windows. Source/day files are replaced rather than added. Late database status updates and Claude results observed after midnight can reconcile recent calls. Updates older than this two-day horizon are not automatically revisited. Calls not retained in a native store cannot be recovered. T3 legacy records use their observation timestamp; T3 V2 uses call start or fallback update timestamp.

Every snapshot stores source, schema version, window, `latest_attempt` and `last_success`. A failed, missing, or partial attempt remains visible and **never overwrites the last successful snapshot**. Consumers must show the latest attempt status and success observation time together; an old success is not a current healthy reading. `latest.json` describes the last completed collection and its source/day outcomes. `report` includes retained snapshots without calculating a cross-source total.

At most 90 daily UTC windows are retained. State directories are mode 0700; JSON and lock files mode 0600. Writes use atomic replacement, and a nonblocking `flock` serializes writers. Existing symlink state files/directories are rejected. State is local and private; no publication happens.

A systemd user timer should invoke `agent-health collect` daily with `UMask=0077`, a runtime limit of at least six minutes and a memory cap of at least 1 GiB. Do not mark exit 2 as success: incomplete coverage should remain visible in the service state. Parent integration owns the package, timer and activation.

## Known inputs and bounds

| Source | Local input |
|---|---|
| Codex | `.codex/thread_history_1.sqlite` and `state_5.sqlite` |
| Codex cliproxy | Same files in `.codex_cliproxy` |
| Claude | `.claude/projects`, at most depth 5, 500 files, 50,000 entries and 256 MB per source/day |
| OpenCode | `.local/share/opencode/opencode.db` |
| OpenCode stable | `.local/share/opencode/opencode-stable.db` |
| T3 | `.t3/userdata/statev2.sqlite`, falling back to `state.sqlite` only when absent |

Each source/day runs in an isolated child with a 20-second timeout, 768 MiB address-space limit and at most 50,000 database tool rows. Adapter SQL/read limits are 15 seconds (T3 SQLite limit 20 seconds). The parent admits work for at most 270 seconds, then records explicit budget failures for remaining windows. All children are sequential. Unsupported schemas fail closed. Missing databases yield `missing`, not a zero-success report. Missing or unreadable Claude files, depth/file/byte/time caps, malformed candidates and conflicting IDs produce `partial`.

The worker exports only fixed numeric metric fields, known category labels and explicitly allowlisted tool names. At most 128 tool groups per projection are exported. Unknown custom tool names are omitted from the tool breakdown but remain in source summaries. Bounded, sanitized worker output is capped at 2 MB; no raw adapter output or exception text reaches the parent report.

## Interpretation

- Keep every native source and T3 projection separate; the same call may appear in several. Never add their totals into a global rate.
- Shell nonzero exits and failing tests are recorded failures, including intentional negative tests. Search exit 1 with no output is a candidate no-match, not proof of a tool fault.
- Unknown/pending/conflicting outcomes stay unknown. Failure percentages use each adapter's stated known/terminal denominator.
- Codex native durations and OpenCode start/end timings differ from Claude message-interval upper bounds and T3 lifecycle/replay intervals. Do not compare these as one latency metric or sum them as blocked time.
- Tool naming is selective. `agent-tools` adoption is visible only when that executable is recorded directly as the command basename. Wrapped/nested invocations and native Claude Bash command contents are not reliable adoption evidence.
- The collector measures observed tool outcomes, not response quality, successful task completion or model rankings.

## Verify

```bash
cd ~/nix-config/scripts/agent-health
PYTHONPATH=adapters python3 -m unittest discover -s tests -v
python3 adapters/claude_health.py --self-test
```

The adapters were copied from the reviewed local audit in the T3 scratch workspace. Changes here add closed daily windows, safe persisted aggregation, late Claude result reconciliation, T3 V2 start-time selection and an explicit `agent-tools` executable name. Original adapter tests are retained alongside collector regression tests.
