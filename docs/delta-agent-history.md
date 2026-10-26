# Delta Agent History and new-project enrollment

The pinned `delta-agent-history` flake supplies the generic CLI, service renderer
and original `t3-delta` compatibility command. `home/tools.nix` installs it once.
The existing `t3-delta-recorder` service keeps its name, environment identity,
private state, credential, producer IDs and immutable outbox. The old local source
under `scripts/t3-delta-recorder` remains rollback material, not a second worker.
The separate `delta-agent-history` user service drains accepted generic events;
it does not discover or upload arbitrary conversations without a producer.

`home/t3-delta-recorder.nix` declares both configs from the same existing inventory
and per-project cutoffs. Generic recall links the immutable Nix-store legacy
config and the original T3 state read-only. The generic source `generic:local` is
the reference producer; this does not claim Claude/Codex/OpenCode hooks have been
installed or that arbitrary desktop conversations are captured.

Both configs reference the mutable, private registry
`~/.config/delta-agent-history/projects.json`. It contains only explicitly
approved additions, schema version 1, with exact `path`, `remote` and
`enrolled_after` values. The declarative base inventory remains authoritative.
Do not replace its original dates or migrate old recordings.

After activation, for a reviewed new Git repository:

```sh
delta-history enroll --project /absolute/new-project --check
delta-history enroll --project /absolute/new-project --accept-upload
```

The second command requires consent to private visible-conversation and checkout
uploads. It adds the exact repository to the shared registry and managed Delta
guidance to the project's `AGENTS.md` and `CLAUDE.md`, preserving other content.
Both workers reload additions between cycles. T3 still needs the project
registered/opened at its canonical approved Git root. Enrollment does not create
a T3 project, start a service, issue credentials or backfill older conversations.
Start a new T3 thread after enrollment; a thread created before its prospective
cutoff is not backfilled, including the thread used to set up that project.
Generic source hooks/plugins/wrappers remain separate capture integrations.
Registry additions report `scope_storage=shared_registry`,
`workers_reload_on_next_cycle=true` and `drainer_reload_required=false`; later
new-project enrollment does not require another Nix rebuild or worker restart.

The shared, Codex and Claude skill directories receive `delta-history` and
`delta-enroll`. A Home Manager activation helper appends/updates one managed
enrollment section in the existing global `~/.codex/AGENTS.md` and
`~/.claude/CLAUDE.md`. It preserves other text and file modes, refuses symlinks,
checks concurrent edits and does not copy global guidance into the Nix store.

Validate the exact package pin and system before switching:

```sh
python3 -m unittest discover -s scripts/tests -p test_delta_agent_guidance.py -v
nh os build ~/nix-config
```

The user applies `nrs` with their sudo password. Until that switch, the new CLI,
skills, global guidance, configs and recorder improvements are not activated.
Afterward verify `delta-history --help`, `delta-history status`,
`systemctl --user status t3-delta-recorder`, and bounded recorded-project recall.
Also verify `systemctl --user status delta-agent-history` for the generic drainer.
Service/process availability, local queue acceptance and observed Delta upload
remain separate evidence. Never print config, tokens or transcripts in reports.
