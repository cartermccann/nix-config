# Agent recovery checks

`agent-recovery` checks shared local tooling against synthetic failures. It does
not launch Claude, Codex, or OpenCode and does not measure their performance.
Ten scenarios exercise the real `agent-tools` executable or the real guard
module; two behavioral scenarios remain explicitly `not_run` until evaluated
with an actual agent. No scripts pretend to be an agent and then grade themselves.

## Run

From source, with active `agent-tools` on PATH:

```sh
python3 ~/nix-config/scripts/agent-recovery/agent-recovery verify
python3 ~/nix-config/scripts/agent-recovery/agent-recovery manifest
```

To check source tools instead of the active generation:

```sh
python3 ~/nix-config/scripts/agent-recovery/agent-recovery verify \
  --tools 'python3 /home/cjm/nix-config/scripts/agent-tools/agent-tools' \
  --guard-root /home/cjm/nix-config/scripts/agent-tool-guard/guard
```

`--tools` is an argv prefix parsed with `shlex.split`, never a shell command.
No pipes, substitution, globbing, or environment expansion happens inside it.
Use an absolute executable path in automation. The equivalent environment
variables are `AGENT_RECOVERY_TOOLS` and `AGENT_RECOVERY_GUARD_ROOT`. Without an
override, the launcher selects `agent-tools` on PATH, then the sibling source
entrypoint. Guard modules default to the sibling source directory. A Nix wrapper
must pin these two variables to the packaged executable and
`<guard-package>/share/agent-tool-guard/guard`, respectively. Package the launcher,
`recovery.py`, and `manifest.json` together. No activation is performed here.

The implementation uses Python's standard library and Linux process groups,
`/proc` state, and file-size limits. Bash and fish can both invoke the executable;
no shell aliases are required. Python, Git, and ripgrep availability comes from
the existing toolkit environment. Prompts and the result contract are portable;
this deterministic process-cleanup runner targets Linux/NixOS.

## Scenarios and coverage

| Case | Deterministic observation |
| --- | --- |
| Missing executable | Exit 127, no child exit status, no argument echo |
| Invalid working directory | Setup failure, no child execution |
| Literal search without matches | Complete no-match, not a regex or tool error |
| Limited search | Partial status and one retained result |
| Failing test | Completed child with exit 2, distinct from setup error |
| Hostile argv | Empty, whitespace, quote, newline, wildcard and shell syntax preserved; injection marker absent |
| Bounded output | Both prefixes bounded and both truncations reported |
| Deadline | Timeout preserved and owned child/grandchild stopped |
| Interrupt | SIGINT preserved and owned child/grandchild stopped |
| Guard | Malformed read rejected; synthetic 429 blocks immediate reads, alternate accounts and writes; reads admitted after cooldown |
| Stale edit | **Not run:** requires an actual agent, external edit injection and final diff |
| Stale browser snapshot | **Not run:** requires an actual agent and controlled browser fixture |

Each check gets its own temporary directory. Guard time is injected, so cooldown
checks do not sleep for a minute. Only an isolated temporary SQLite database is
used. Guard calls inspect admission and completion state; they never call a
connector or replay a write. This proves guard-core behavior, not hook registration
or adapter enforcement. Actual activation needs separate runtime verification.

Tool subprocesses have an eight-second outer deadline and a 1 MiB output-file
limit per stream. Fixtures request smaller helper deadlines and output limits.
Cleanup verifies the owned leader and descendant are stopped; zombies count as
stopped because they cannot execute. Test processes do not escape their process
group. This is not a sandbox for malicious executables or `setsid` descendants.
Helper output stays internal; emitted JSON contains only case names, booleans,
durations, counts and fixed error types. No provider calls, account state,
credentials, real messages, installation, or user files are needed.

## Deterministic result contract

`verify` prints one JSON object with `schema_version: 1`,
`suite_id: "agent-recovery-v1"`, `result_type: "deterministic_tooling"`, aggregate
`status`, `behavior_status: "not_run"`, `counts`, and `results`.

Each executed record has `case_id`, `status` (`passed` or `failed`), and
`duration_ms`. Successful execution of the fixture includes an `assertions` map
whose exact keys come from the manifest. All assertions must be booleans and true
to pass. An exception produces a failed record with `error_type`; raw exception
messages and tool output are not emitted. Unevaluated behavioral records have
`status: "not_run"` and `reason: "requires_actual_agent_trace"`.

Exit 0 means all ten deterministic checks passed. It does **not** mean all twelve
behavioral scenarios passed. Any deterministic failure exits 1. An invalid CLI
request exits 2. `manifest` emits the case prompts and assertion names without
running anything.

## Later evaluation with real agents

Run the same manifest in an isolated, disposable workspace for each harness and
record its exact model/version and tool configuration. An external evaluator
must provide the fixtures, inject the stale edit/navigation at a known point,
and capture the tool trace independently. For the guard scenario, supply the
isolated core or a fake connector that returns 429; never use real mail or writes.
Do not inject failures into a working client repository or real browser session.

The ten deterministic cases prove helper mechanics only. To measure agent
behavior, check whether the agent interprets those results correctly and recovers
appropriately using the same prompts. Success cannot be inferred from passing
the helper checks. Until real runs exist, all model/harness comparisons remain
unmeasured. Record tool calls and elapsed time from the actual trace if comparing
efficiency; this initial scorer does not invent performance metrics or rankings.

### Submitted behavior result

`agent-recovery score submission.json` validates a bounded, regular JSON file
(at most 1 MiB). The exact required top-level values are:

```json
{
  "schema_version": 1,
  "suite_id": "agent-recovery-v1",
  "result_type": "agent_behavior",
  "harness": "codex",
  "model": "actual model identifier from run metadata",
  "results": []
}
```

`harness` must be `claude`, `codex`, or `opencode`; `model` must be a nonempty
string. `results` must contain every manifest case exactly once. Unknown,
duplicate, or missing IDs are invalid. A case is one of:

```json
{"case_id":"stale_edit","status":"not_run"}
```

```json
{
  "case_id": "stale_edit",
  "status": "completed",
  "observations": {
    "reread_after_conflict": true,
    "concurrent_edit_preserved": true,
    "no_blind_replay": false
  },
  "evidence": [{
    "kind": "captured_tool_trace",
    "path": "relative/path/to/sanitized-trace.json",
    "sha256": "64 lowercase hex characters of the actual artifact digest"
  }]
}
```

Completed cases require exactly the manifest's assertion names, strict JSON
booleans, and at least one evidence reference with nonempty path and a valid
64-character lowercase SHA-256 value. Every assertion must be true to pass.
The scorer rejects malformed records; it never accepts a caller-supplied `passed`
label. It does not load evidence paths, verify hashes, authenticate traces, or
determine whether observations describe those traces correctly. A reviewer must
do that separately. Its result is explicitly `reported_agent_behavior` with
`trace_validation: "not_performed"`, not a verified agent capability claim.

The scorer exits 0 for all reported cases passing, 1 for any failed or not-run
case, and 2 for invalid input. Aggregate status is `failed` if any case fails,
otherwise `incomplete` if any case is not run, otherwise `passed`. A deterministic
verification result cannot be submitted as behavioral evidence.

## Maintenance checks

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s ~/nix-config/scripts/agent-recovery -p 'test_*.py' -v
```

The negative controls flip every assertion false, omit evidence, use malformed
hashes, pass numbers instead of booleans, duplicate/omit cases, inject exceptions,
and substitute a broken helper that claims success for a failed test. They must
produce failure or invalid input, never a false pass. Passing rubric-shape tests
is not a real agent evaluation.
