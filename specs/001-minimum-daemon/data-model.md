# Phase 1 Data Model: Minimum Daemon

**Feature**: [spec.md](./spec.md) | **Research**: [research.md](./research.md) | **Date**: 2026-08-23

SQLite at `~/.local/state/robot-army/state.db`. All timestamps are UTC ISO 8601 with a `Z` suffix,
stored as `TEXT` — human-readable in a `sqlite3` shell, which the Operating Constraints prefer over
epoch integers.

## The configuration / state split

The single most important structural decision here: **`config.toml` holds what the maintainer
declares; the database holds what the system observed.** A repository's clone path, base branch, and
preparation steps are configuration and never appear in the database. Its onboarding approval, the
fingerprint of its committed permissions, and when its trust was last verified are observations and
never appear in the config.

This is what keeps the config hand-editable without a migration story, and keeps the database free of
values the maintainer would expect to change by editing a file.

## Entity collapse, and why

The spec names **Isolated Checkout** as an entity. It is stored as columns on `work_items` rather
than its own table, because the relationship is strictly 1:1 and permanent — a work item has exactly
one worktree for its whole life, including across resume and restart. A separate table would add a
join and a foreign key to express a fact that a column expresses directly, which Principle I decides
against. Its "condition" (present, dirty, missing) is not stored at all: it is derived from git at
the moment it is asked for, because a stored copy would be wrong the instant the maintainer touched
the directory.

The spec's **Audit Record** entity is likewise not a table. It is the JSONL file described in R14.
Putting it in SQLite would make it neither append-only nor readable with `tail`.

---

## `repos`

Onboarding state for a configured repository. A row exists only once the maintainer has explicitly
onboarded it (FR-001); its absence is what blocks dispatch.

| Column | Type | Notes |
|---|---|---|
| `repo_key` | TEXT PK | Matches the section key in `config.toml` |
| `onboarded_at` | TEXT NOT NULL | When the maintainer approved it |
| `settings_fingerprint` | TEXT | JSON object mapping committed settings path → SHA-256. `NULL` means "no committed settings files existed at onboarding" |
| `fingerprint_approved_at` | TEXT NOT NULL | When the current fingerprint was last approved |
| `trust_verified_at` | TEXT | Last time `hasTrustDialogAccepted` was confirmed true |

**Validation**
- `repo_key` MUST exist in the current config at dispatch; a row whose config section has been
  removed is surfaced as a configuration anomaly rather than silently ignored.
- `settings_fingerprint` is recomputed from git at every dispatch and compared. Any difference —
  including a file that did not exist at onboarding and now does — blocks dispatch (FR-004).

## `work_items`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `source` | TEXT NOT NULL | `github` in this milestone |
| `source_id` | TEXT NOT NULL | Stable external identity, `owner/repo#142` |
| `source_url` | TEXT NOT NULL | Canonical issue URL |
| `repo_key` | TEXT NOT NULL | FK → `repos.repo_key` |
| `issue_number` | INTEGER NOT NULL | |
| `title` | TEXT NOT NULL | |
| `body` | TEXT NOT NULL | Prompt body, stored in full — reconstruction needs it |
| `labels` | TEXT NOT NULL | JSON array as seen at dispatch |
| `state` | TEXT NOT NULL | See state machine below |
| `dry_run` | INTEGER NOT NULL | 0 or 1 |
| `worktree_path` | TEXT | NULL until preparation begins |
| `branch` | TEXT | NULL until preparation begins |
| `prepare_output` | TEXT | Captured stdout/stderr of preparation steps, on failure |
| `failure_reason` | TEXT | Human-readable, set whenever entering `failed` |
| `blocked_reason` | TEXT | Why an item is not eligible, set on rejection |
| `discovered_at` | TEXT NOT NULL | |
| `ready_at` | TEXT | |
| `dispatching_at` | TEXT | Load-bearing: FR-041's max-age check reads this |
| `active_at` | TEXT | |
| `ended_at` | TEXT | Last session ended |
| `done_at` | TEXT | |
| `updated_at` | TEXT NOT NULL | |

**Indexes**: `UNIQUE (source, source_id, dry_run)`; `INDEX (state)`; `INDEX (state, dispatching_at)`.

**The uniqueness key is the idempotency guarantee.** `(source, source_id, dry_run)` is what makes
FR-072 hold: re-polling an already-dispatched issue collides on insert and becomes a no-op rather
than a second worktree and a second session. Including `dry_run` is deliberate — it lets a simulated
run and a later live run of the same issue coexist, which is the normal workflow.

**Rows are created only for labelled issues in onboarded repositories.** An issue without the label,
or in a repository that is not onboarded, produces an audit-log line and no row. Rows for rejected
items are kept (state `failed`, with `blocked_reason`) because the maintainer deliberately labelled
those and will want to know why nothing happened — that is FR-009's purpose.

**Default query scope (FR-056)**: the persistence layer's accessors filter `dry_run = 0` unless
passed an explicit `include_simulated=True`. This is enforced by the accessor signatures rather than
by convention, so including simulated rows is the explicit act.

## `sessions`

A work item may have several sessions over its life — the first dispatch, then any resume or
restart. `attempt` orders them.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `work_item_id` | INTEGER NOT NULL | FK → `work_items.id` |
| `session_id` | TEXT NOT NULL UNIQUE | The UUID the daemon generated, persisted *before* launch |
| `attempt` | INTEGER NOT NULL | 1-based |
| `state` | TEXT NOT NULL | See state machine below |
| `dry_run` | INTEGER NOT NULL | Denormalised from the work item, so session queries need no join |
| `pid` | INTEGER | From the registry at confirmation |
| `proc_start` | TEXT | Kernel start-time ticks; the PID-reuse guard |
| `scope` | TEXT | systemd scope name, read from `/proc/<pid>/cgroup`; the terminate handle |
| `host_socket` | TEXT | dtach socket path |
| `window_id` | INTEGER | kitty window id |
| `launch_argv` | TEXT | JSON array of the full launch command, for diagnosis |
| `exit_code` | INTEGER | |
| `signal` | INTEGER | Set when `exit_code` is 128+N; NULL otherwise |
| `started_at` | TEXT NOT NULL | Row written before the process starts |
| `confirmed_at` | TEXT | When the registry entry with our `session_id` was observed |
| `ended_at` | TEXT | |

**Indexes**: `INDEX (work_item_id, attempt)`; `INDEX (state)`.

**`session_id` is written before the process exists.** This is FR-020, and it is what makes every
failure mode recoverable: a process that dies before writing anything still has a database row
naming it, so reconciliation has something to reason about rather than a gap.

**`pid` alone is never sufficient identity.** Every liveness check requires `pid` **and** a
`proc_start` that still matches `/proc/<pid>/stat` field 22 (FR-038).

## `anomalies`

Conditions the system detected but cannot resolve (FR-065). Kept as rows rather than log lines only,
because `robot-army status` must show outstanding ones and the maintainer must be able to
acknowledge them.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `kind` | TEXT NOT NULL | `orphan_session`, `dispatching_timeout`, `no_transcript`, `session_id_mismatch`, `registry_version_unknown`, `config_missing_repo`, `prunable_worktree` |
| `entity_type` | TEXT | `work_item`, `session`, `repo`, or NULL |
| `entity_id` | TEXT | |
| `detail` | TEXT NOT NULL | JSON with everything needed to act on it |
| `detected_at` | TEXT NOT NULL | |
| `acknowledged_at` | TEXT | |

**Indexes**: `UNIQUE (kind, entity_type, entity_id) WHERE acknowledged_at IS NULL`.

That partial unique index is what stops a reconciliation loop running every 60 seconds from
producing 1,440 identical rows a day for one orphan. Re-detecting an unacknowledged anomaly updates
nothing; acknowledging it allows a genuinely new occurrence to be recorded later.

## `poll_state`

Per-repository polling bookkeeping, kept out of `repos` because it is high-churn operational state
rather than onboarding fact.

| Column | Type | Notes |
|---|---|---|
| `repo_key` | TEXT PK | |
| `etag` | TEXT | Last `ETag` for the issue listing; sent as `If-None-Match` |
| `last_polled_at` | TEXT | |
| `last_status` | INTEGER | HTTP status of the last poll; `304` is the healthy steady state |
| `consecutive_failures` | INTEGER NOT NULL DEFAULT 0 | Drives backoff |
| `backoff_until` | TEXT | Set on rate limiting or repeated failure |

The `etag` column is what makes a 60-second poll sustainable: an unchanged listing returns `304` and
costs nothing against the rate limit (R4).

---

## Work item state machine

```
                    ┌──────────────┐
                    │  discovered  │  row written before evaluation, so an
                    └──────┬───────┘  interrupted evaluation is observable
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌────────┐   ┌────────┐   ┌───────────┐
         │ ready  │   │ failed │   │ abandoned │
         └───┬────┘   └───┬────┘   └───────────┘
             │            │ (human retry after config change)
             ▼            │
      ┌─────────────┐◄────┘
      │ dispatching │──────────────────► failed  (prep error, launch
      └──────┬──────┘                             unconfirmed, max age)
             ▼
        ┌────────┐
        │ active │
        └───┬────┘
            │
   ┌────────┼─────────────┬──────────────┐
   ▼        ▼             ▼              ▼
┌──────────────────┐ ┌─────────────┐ ┌──────┐
│ awaiting_review  │ │ interrupted │ │ done │
│  (exit 0)        │ │ (signal /   │ │      │
└────────┬─────────┘ │  no report) │ └──────┘
         │           └──────┬──────┘
         │                  │  resume / restart → dispatching
         └──────────────────┴──────────► done   (issue closed)
                            └──────────► abandoned (human)
```

**Legal transitions**, enforced by a single function that rejects everything else:

| From | To | Trigger |
|---|---|---|
| `discovered` | `ready` | All eligibility conditions pass |
| `discovered` | `failed` | An eligibility condition failed; `blocked_reason` set |
| `ready` | `dispatching` | Dispatcher selected it and capacity exists |
| `ready` | `abandoned` | Human |
| `dispatching` | `active` | Session confirmed present with our `session_id` |
| `dispatching` | `failed` | Preparation failed or timed out, launch unconfirmed, or max age exceeded |
| `active` | `awaiting_review` | Exit code 0 |
| `active` | `failed` | Exit code 1, 126, or 127 |
| `active` | `interrupted` | Exit code 128+N, or reconciliation found no live session and no exit |
| `active` | `done` | Issue observed closed |
| `awaiting_review` | `done` | Issue observed closed |
| `awaiting_review` | `dispatching` | Human resume |
| `awaiting_review` | `abandoned` | Human |
| `interrupted` | `dispatching` | Human resume or restart |
| `interrupted` | `done` | Issue observed closed |
| `interrupted` | `abandoned` | Human |
| `failed` | `ready` | Human retry, after the blocking condition changed |
| `failed` | `abandoned` | Human |

`done` and `abandoned` are terminal. Every transition writes `updated_at`, stamps its own timestamp
column where one exists, and emits an audit record — inside the same transaction as the state
change, so a crash cannot produce a state change with no record or a record with no state change.

**Two rules that are easy to get wrong, stated explicitly:**

1. **`interrupted` does not mean "nothing is running."** M0's F17: if the wrapper is killed
   uncleanly, the worker keeps running, reparented, while dtach tears down its socket — so the
   daemon sees no socket and no exit report and concludes `interrupted` while a real session is
   still editing files. The orphan sweep is what catches this, and it is why FR-043 exists.
2. **Exit 0 with the issue still open is a resting state, not an anomaly.** The maintainer may have
   typed `/exit` because they went to lunch. Nothing should nag about it.

## Session state machine

```
starting ──► running ──► exited_clean   (0)
    │           │
    │           ├──────► exited_error   (non-zero, no signal)
    │           │
    └───────────┴──────► lost           (no exit ever reported)
```

| From | To | Trigger |
|---|---|---|
| `starting` | `running` | Registry entry with our `session_id` observed |
| `starting` | `lost` | Confirmation window elapsed with no entry |
| `running` | `exited_clean` | Spool record, exit 0 |
| `running` | `exited_error` | Spool record, non-zero exit |
| `running` | `lost` | Reconciliation: not alive, no spool record ever arrived |

**Exit code → work item state**, per FR-033 and M0's measured E3.3 table:

| Exit code | Session state | Work item state | Reasoning |
|---|---|---|---|
| `0` | `exited_clean` | `awaiting_review` | A human deliberately ended it |
| `1`, `126`, `127` | `exited_error` | `failed` | Configuration errors — the worker never ran; retrying without a config change is pointless |
| `128+N` (137, 143, …) | `exited_error` with `signal` set | `interrupted` | Killed externally; likely resumable, not a failure of the item |
| any other non-zero | `exited_error` | `failed` | Conservative default; `failure_reason` records the raw code |
| none ever reported | `lost` | `interrupted` | Via reconciliation |

## Derived values (computed, never stored)

Required by FR-048 so the maintainer can judge whether resuming is worthwhile, and by FR-017.
Each is computed on demand because a stored copy would go stale the moment the maintainer touched
the directory.

| Value | Source |
|---|---|
| Worktree has uncommitted or untracked changes | `git status --porcelain` in the worktree |
| Branch has commits beyond its base | `git rev-list --count <base>..<branch>` |
| Worktree directory is missing | `git worktree list --porcelain` reports `prunable` |
| Issue is closed | GitHub API; cached within a reconciliation pass |
| An open pull request exists for the branch | GitHub API |
| Live session count against the cap | The registry scan of R8 |

## Interruption behaviour

Principle IV requires this to be answered explicitly for every persisted operation.

| Interrupted at | Result on next start |
|---|---|
| After `discovered` insert, before evaluation | Row in `discovered`; re-evaluated on next poll |
| During worktree creation | Item in `dispatching`; reconciliation fails it at max age; the partial worktree is detected and reported, never reused blindly |
| During a preparation step | Same; the step's timeout bounds how long this can persist |
| After session row insert, before launch | Row in `starting`; confirmation window elapses; session `lost`, item `failed` |
| After launch, before confirmation | The registry scan finds the session and confirms it late, or the window elapses and the orphan sweep catches the live process |
| After the worker exits, before the daemon drains the spool | The spool file persists; applied on next tick or next startup. **This is the case an HTTP POST would have lost permanently** (R5) |
| After applying a spool record, before unlinking it | Reapplied; application is idempotent on `(session_id, exit_code)` |
| Mid-migration | Each migration runs in a transaction; `user_version` advances only on commit |
| Mid-audit-write | Records are flushed per line; a partial final line is tolerated by readers, which skip unparseable lines and count them |
