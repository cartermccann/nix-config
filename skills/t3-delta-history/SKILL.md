---
name: t3-delta-history
description: Check the background T3 Delta recorder, locate captured conversations, and inspect prior work when asked about recording or Delta history from T3.
---

# T3 Delta history

The recorder is a deterministic user service. This skill queries it; loading the skill does not enable recording.

Use `t3-delta status` first. If the command is absent, report that the recorder has not been activated. Never treat a successful `zed-delta open` request as a recorded conversation.

For the current thread, get its actual ID through T3's app-owned thread tools, then use `t3-delta status --thread <T3-thread-id>`. An empty result means this thread is not enrolled. The recorder includes approved Git repositories under `~/projects` and the explicit `~/nix-config` path, matched by exact path and remote in `~/nix-config/config/t3-delta-projects.json`, including their real linked Git worktrees. New repositories use their individual enrollment cutoff; ComCreate retains its original cutoff. It does not backfill old conversations. Missing remotes, sensitive-file holds, and unresolved duplicate or nested repositories are excluded; consult `~/nix-config/docs/t3-delta-projects-scope.md` for the inventory. T3 must be opened on the approved repository root, rather than an umbrella folder such as `~/projects` or a different clone.

Distinguish service health, checkout attachment time, pending outbox records, acknowledged messages, and the separately observed Delta upload state. Stale or catching-up status is not a completed cloud backup. File coverage starts after attachment; hidden reasoning, attachments, and tool output are excluded.

Read a bounded transcript with `t3-delta history --thread <T3-thread-id> --messages 1-20`; request another range only when relevant. Use the saved T3-to-Delta binding rather than guessing a thread from its title. Do not mine provider caches or credential directories to reconstruct missing history.

The installed Zed app command is `zed-delta`; `delta` is Git's diff viewer. The adapter is pinned to Delta recording contracts 6–7 and T3 0.1.0. A compatibility error requires review, not an automatic upstream update.

Restoring Delta files does not rewind T3's conversation. For recovery, identify the exact checkout and attachment period, inspect the requested state, and prepare a separate recovery checkout or patch. Coordinate with active T3 turns before applying requested changes. This helper does not implement automatic restore; never invent restore commands or claim an untested historical recovery succeeded.

For a recorder outage, read `systemctl --user status t3-delta-recorder` and the redacted service journal. `t3-delta auth` issues a dedicated 30-day T3 credential limited to `orchestration:read`; it never needs provider credentials. Stop the service before running that command, then restart it. Do not print the token file.
