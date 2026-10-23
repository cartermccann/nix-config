# T3 Delta project scope

Expanded at **2026-10-23 14:15:13 America/Denver** (20:15:13 UTC).

**151 exact path/remote pairs are approved**: 150 under `~/projects` (96 work, 29 personal/tooling, and 25 archived repositories), plus the explicitly approved `~/nix-config` repository. The separate nix-config enrollment begins at 2026-10-23 20:45:28 UTC and has been registered as the `nix-config` T3 project. There is no last-commit age filter. This is an explicit manifest, not a wildcard authorizing newly created folders.

The recorder is eligible for new conversations opened on these repository roots in T3, including their real linked Git worktrees. ComCreate keeps its original enrollment; other repositories use the expansion cutoff. Old T3 transcripts are not imported, and unregistered repositories are not bulk-added to the sidebar.

The full approved list and **397 excluded checkout entries**, with reasons, are in [the scope manifest](../config/t3-delta-projects.json). Most exclusions are duplicate, generated, or reference checkouts. Reasons overlap: 30 filename holds, 1 prior unresolved filename review, 8 nested parents, and 14 missing-origin roots. Ambiguous primary clones remain excluded.

## Existing T3 projects requiring attention

| Repository | Current hold |
| --- | --- |
| `work/CoachLoop` | contains separate nested repositories; no origin remote; no readable committed HEAD |
| `Personal/runa/M-M-Runa` | contains separate nested repositories; global subtree discovery incomplete |
| `work/skaidr` | contains separate nested repositories |
| `Personal/reviveAZ` | no origin remote |
| `work/silverado/silverado-web-ops` | contains separate nested repositories |
| `work/_comcreate/comcreate-org/comcreate-dashboard` | tracked or unignored sensitive-looking filenames; prior filename review unresolved |


CoachLoop’s `product` repository is approved; the parent wrapper is not. Open `~/projects/work/CoachLoop/product` as the T3 project to use recording there. T3 projects rooted at `~/projects`, `~/projects/work`, the home directory, or a separate Desktop clone do not identify an approved repository.

## Coverage and verification

Discovery verified 546 Git roots from 547 candidates. The global pass was capped at 1,000,000 entries and depth 10, with dependency/generated directories and symlink traversal excluded. Its coverage is explicitly partial. A targeted follow-up checked the five otherwise eligible repositories left by that cap in 8,428 entries and cleared all five; no repository remains excluded solely because the scan stopped. The remaining incomplete-subtree flags also have independent exclusions.

No repository contents, old transcripts, or provider credentials were bulk-imported by inventory. At runtime, initial attachment rechecks the actual remote, exact registered root, linked-worktree identity, and sensitive filename boundary.

Scope behavior passed **86 tests** and independent review. The historical file-undo limitation from the original pilot remains; see [operation and recovery](t3-delta-recorder.md).
