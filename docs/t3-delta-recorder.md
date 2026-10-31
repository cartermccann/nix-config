# T3 → Delta background recorder

T3 stays the working interface. The local `t3-delta-recorder` user service polls T3's supported read-only MCP interface every ten seconds and records new conversations from approved Git repositories under `~/projects` and the explicit `~/nix-config` path into private Delta threads. The `t3-delta-history` skill checks status and retrieves bounded history; capture does not depend on an agent loading that skill.

## Scope and activation

The exact approved path/remote pairs and per-repository enrollment timestamps are in `config/t3-delta-projects.json`. The inventory and exclusions are documented in [project scope](t3-delta-projects-scope.md). ComCreate retains the original timestamp persisted in `~/.local/state/t3-delta-recorder/source.sqlite3`; additional repositories begin at their expansion cutoff, so existing T3 histories are not imported.

Open each repository as its own T3 project. Existing approved projects are discovered automatically; repositories not registered in T3 become eligible when opened there. The recorder does not fill the T3 sidebar with hundreds of unused projects. An umbrella folder, a held repository, or an unapproved duplicate checkout does not enroll. Linked Git worktrees must share the approved repository's actual Git common directory, so a separate clone with the same remote cannot impersonate one.

Persistent installation is declarative in `home/t3-delta-recorder.nix`. Validate with `nh os build ~/nix-config`, then apply with `nrs` in Carter's terminal. After activation:

```sh
systemctl --user status t3-delta-recorder
t3-delta status
```

The current session has started a transient `t3-delta-recorder-pilot` service using the verified Nix package and the exact built configuration for 151 approved repositories. It conflicts with and stops before the persistent service starts, so activation does not run two writers. Before `nrs`, its status is available through `~/.local/state/t3-delta-recorder/pilot-package/bin/t3-delta --config ~/.local/state/t3-delta-recorder/pilot-config.json status`.

Start a new conversation in an approved T3 project and work normally. No special Git commands or recurring recording prompt are required. Ask in T3 whether the current thread is recording or to inspect its captured history. The new skill becomes discoverable after activation and a fresh provider session.

## What is captured

Completed local user/assistant text and proposed plans are copied through Delta's public recording CLI. Observed T3 run identifiers, and provider/model when present for that exact run, label captured assistant messages. These are passive observations, not ownership of Delta execution turns. Hidden reasoning, attachments, inherited conversation, and raw tool payloads are excluded. Consecutive assistant records may appear as one Delta message; user messages remain distinct.

Delta attaches the existing checkout and observes eligible files. Ignored local files remain outside the import; already tracked files are not protected by ignore rules. Initial attachment screens known sensitive filenames. This is a filename boundary check, not a content secret detector.

File coverage begins **after attachment**, which can occur after the first edit because discovery uses polling. Separate linked Git worktrees retain separate bindings; threads sharing one checkout also share its file changes. The recorder never claims per-thread file isolation for a shared directory.

## Health, privacy, and retries

`t3-delta status` reports matching projects, enrollment/attachment times, source problems, pending records, validated acknowledgements, and separately observed private cloud upload state. `watching` with zero captured threads means ready for new work, not proof that a real ComCreate session has been captured. `awaiting_project`, `catching_up`, `source_missing`, `needs_review`, `unavailable`, and `stale` describe incomplete coverage. Failed uploads and unknown privacy never appear healthy.

A durable local outbox retries identical sequences after partial acknowledgements or restarts, even if T3 is unavailable or no longer lists the original thread. Changed completed messages, replaced checkout bindings, fencing, unsupported contracts, and ambiguous visibility stop further capture for review. A source hold does not discard immutable records already queued. T3 turns continue independently of recorder failures.

State and its dedicated read-only T3 credential live under `~/.local/state/t3-delta-recorder/` with private directory/file permissions. Never print the token file. The supported credential expires after 30 days. To renew it:

```sh
systemctl --user stop t3-delta-recorder
t3-delta auth
systemctl --user start t3-delta-recorder
```

Inspect redacted failures with `journalctl --user -u t3-delta-recorder -n 30`. The worker logs error classes, not raw transcripts, CLI stderr, or credential values. If stopped, Delta may continue watching already attached files while its desktop app runs; stopping the recorder stops conversation polling and new enrollment, not an existing Delta attachment.

## History and recovery

```sh
t3-delta status --thread '<actual T3 thread id>'
t3-delta history --thread '<actual T3 thread id>' --messages 1-20
```

History requests are bounded to twenty Delta messages and use the saved mapping. Transcript ACKs, reported cloud upload, and successful file recovery are distinct evidence.

Recovery must use a separate checkout or patch and coordinate with active edits. The pilot does not automatically rewind files or native T3 conversation state. On Delta 0.21.0, standalone `worktree mount` rejects the attached private history as not visible; `thread fork` followed by owner-scoped `thread mount` successfully materialized its latest recorded file state during the synthetic proof. Receipt `version` strings are retained as opaque evidence and are **not** directly accepted by `thread fork --at-version`. Historical point-in-time recovery remains unverified; do not advertise this pilot as a proven backup/undo system.

## Compatibility and evidence

Reviewed interfaces: T3 **0.1.0 and 0.1.5**, Delta **0.21–0.23**, recording output contracts **6–8**. The pinned upstream package separates worktree creation, thread membership and connection-owned mounts for contract 8. Existing producer IDs, thread IDs, references and queued record bytes are preserved. Non-project configuration changes exit the old worker so its service supervisor can restart it with the current CLI. Future contract maxima above eight require review. References: [T3 read-only access](https://github.com/pingdotgg/t3code/blob/v0.1.5/docs/user/outside-agents.md), [contract 8 plugin reference](https://github.com/zed-industries/delta-claude/tree/70aabf083dc985d231494d94ad45498bd9e6bc77), and [Delta plugin protocol](https://delta.dev/docs/cli/build-a-plugin).

The local package override in `pkgs/delta-agent-history/` explicitly permits
reviewed T3 0.1.5 alongside 0.1.0. The upstream pin otherwise rejects every
version except 0.1.0. Both installed commands and systemd services use this same
patched package. Unknown stable and nightly releases still stop for review.
The patch changes no database schema, enrollment, credentials or queued payloads.
Its tests cover 0.1.5 capture and replay without duplicates, plus rejection before
discovery or export for unsupported versions. Live read-only environment,
project, thread-list and message reads succeeded against 0.1.5; Delta 0.23.0
reported contract 8. All five recorder SQLite databases, including the generic
drainer's nested Delta sink, passed `quick_check`.
These checks do not establish fresh capture or upload while the services are
stopped. Apply the build with `nrs`, then verify both services and advancing
capture/acknowledgement timestamps before treating recording as healthy.

Verification on 2026-10-23: **86 tests passed**, final independent crash-recovery review passed, the skill validator passed, and `nh os build ~/nix-config --no-nom --diff never` completed successfully. The persistent system has been built but still needs Carter's privileged `nrs` switch.

Live synthetic proof on 2026-10-23: private/uploaded/granted status; server ACKs for synthetic messages; identical replay reported `duplicate` without an extra message; edited `pilot.txt` recovered from a separate fork as the expected `after` content. Fixture state is in `~/.local/state/t3-delta-recorder/live-verification/`. No ComCreate source edit was needed.

The scope expansion was explicitly requested after the synthetic pilot. Each real project still needs its first new T3 conversation to produce recording evidence; configured enrollment alone does not prove capture. The old bulk repository-opening queue is not used. Historical recovery remains a separate unverified capability.
