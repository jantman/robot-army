# State: where it lives and what survives a reboot

## The layout

XDG variables are honoured when set; these are the defaults.

| Path | Contents | Survives reboot |
|---|---|---|
| `~/.config/robot-army/config.toml` | Configuration. **Read-only to the daemon** | yes |
| `~/.local/state/robot-army/state.db` | SQLite: work items, sessions, repos, anomalies, poll state | yes |
| `~/.local/state/robot-army/logs/audit-*.jsonl` | The audit log | yes |
| `~/.local/state/robot-army/logs/sessions/<item>.log` | Per-session wrapper log | yes |
| `~/.local/state/robot-army/spool/exits/` | Exit records awaiting the daemon | **yes — deliberately** |
| `~/.local/state/robot-army/spool/exits/rejected/` | Quarantined malformed records | yes |
| `~/.local/state/robot-army/heartbeat.json` | Liveness evidence | yes (and immediately stale) |
| `~/.local/state/robot-army/daemon.lock` | Single-instance lock | file yes, **lock no** |
| `/run/user/<uid>/robot-army/<item>.sock` | Session host sockets | **no — deliberately** |
| `~/worktrees/<repo>/issue-<n>/` | Isolated checkouts | yes |

Two entries in that table are load-bearing rather than arbitrary:

**Sockets live under `XDG_RUNTIME_DIR` because it is tmpfs and cleared on reboot.** A dead
socket from a previous boot is noise that reconciliation would otherwise have to reason
about; letting the kernel delete them is free. Sockets that survive *within* a boot because
a session died uncleanly are detected by probing — never by trusting that the file exists.

**Exit records survive on purpose.** The wrapper writes them as files rather than POSTing
them to the daemon, because a POST to a daemon that is down loses the record permanently —
and the daemon is legitimately down during restarts, upgrades, and reboots. A lost record
would silently downgrade a clean completion into a phantom that reconciliation could only
ever classify as `interrupted`. This is the single deliberate departure from the planning
document; the reasoning is [research.md R5](../specs/001-minimum-daemon/research.md).

**The lock file survives but the lock does not.** `flock` is released by the kernel when
the holding process dies by any means, including `SIGKILL`. A stale `daemon.lock` file
containing an old PID is normal and harmless.

## The configuration / state split

`config.toml` holds what I **declare**. The database holds what the system **observed**.

A repository's clone path, base branch, and preparation steps are configuration and never
appear in the database. Its onboarding approval, the fingerprint of its committed
permissions, and when its trust was last verified are observations and never appear in the
config.

That split is what keeps the config hand-editable with no migration story, and keeps the
database free of values I would expect to change by editing a file.

## The database

SQLite, WAL mode, `foreign_keys=ON`, `synchronous=FULL`. Five tables. Directly inspectable:

```bash
sqlite3 ~/.local/state/robot-army/state.db '.tables'
sqlite3 -header -column ~/.local/state/robot-army/state.db \
  'SELECT id, state, repo_key, issue_number, failure_reason FROM work_items'
```

WAL mode is what lets `robot-army status` read while the daemon holds a write connection.
`synchronous=FULL` is because the machine loses power; the throughput cost is irrelevant at
this write volume.

Schema changes are a forward-only `PRAGMA user_version` ladder. Each migration runs in a
transaction and advances the version as its last statement, so a process killed mid-migration
leaves the version unadvanced and the whole migration re-runs on the next start.

```bash
sqlite3 ~/.local/state/robot-army/state.db 'PRAGMA user_version'
```

There are no downgrades. The rollback plan is restoring the file from backup.

## What a reboot does

Every session is gone; every `active` row still says `active`. On the next start:

1. The spool drains first, so any session that exited cleanly before the reboot is recorded
   as `awaiting_review` rather than mistaken for one that vanished.
2. Reconciliation runs **before any dispatch**. For each remaining `active` item it finds no
   live process and no exit record, and moves it to `interrupted`.
3. Stale sockets are already gone, because `/run/user` is tmpfs.
4. Worktrees are still on disk, on their branches, with whatever the session had written.

None of this raises an error-level record. A reboot legitimately produces one `interrupted`
item per session that was running, and treating that as an error would train me to ignore
errors.

Nothing resumes automatically. `robot-army status` lists what is waiting; `robot-army show
<id>` reports whether the worktree has uncommitted changes, whether the branch has commits,
whether the issue is closed, and whether a PR is open — computed on demand, never stored,
because a stored copy would be wrong the moment I touched the directory.

## Interrupted at X → result on next start

The full table is in
[data-model.md](../specs/001-minimum-daemon/data-model.md#interruption-behaviour). The
summary:

| Interrupted at | Result |
|---|---|
| After the `discovered` insert, before evaluation | Row in `discovered`; re-evaluated on the next poll |
| During worktree creation or a preparation step | Item in `dispatching`; failed at max age with whatever output exists. The partial worktree is reported, never reused blindly |
| After the session row insert, before launch | Confirmation window elapses; session `lost`, item `failed` |
| After launch, before confirmation | Either the registry scan confirms it late, or the window elapses and the orphan sweep catches the live process |
| After the worker exits, before the spool drains | The file persists and is applied on the next tick or next startup |
| After applying a spool record, before unlinking it | Reapplied; application is idempotent |
| Mid-migration | The migration re-runs; `user_version` never advanced |
| Mid-audit-write | A partial final line is skipped and counted by readers |

## Disk

A prepared worktree was measured at up to **499 MB** once a virtualenv exists. There is no
automatic removal in this milestone — deleting work automatically is worse than running out
of disk, and `doctor` warns when free space is low.

```bash
robot-army worktree list        # sizes and conditions
df -h ~/worktrees
```

Worktrees live at `~/worktrees/` rather than under `~/.local/state` so they stay out of any
backup set aimed at `~/.local` — which matters at half a gigabyte each — and sit at a short
top-level path I will find without looking it up.

## Backing up

Worth keeping: `~/.config/robot-army/config.toml` and
`~/.local/state/robot-army/state.db`. The logs are worth keeping if the point is an audit
trail. The worktrees are reproducible from the branches, which live in the repositories.

Copy the database with SQLite rather than `cp`, so a concurrent write cannot produce a torn
file:

```bash
sqlite3 ~/.local/state/robot-army/state.db ".backup /tmp/robot-army-backup.db"
```
