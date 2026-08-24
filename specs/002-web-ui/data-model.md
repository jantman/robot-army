# Data Model: Web UI & HTTP API

This milestone adds **one table and three columns** to the schema 001 established. Everything else it
displays is read from tables that already exist, through accessors that already exist.

The entities the spec names — Work Item, Session, Repository Configuration, Isolated Checkout, Audit
Record, Anomaly — are unchanged; see
[001's data-model.md](../001-minimum-daemon/data-model.md). What follows is what is new, plus the
shapes the interface assembles for rendering, which are derived and never stored.

---

## New: `dispatch_control`

Migration `002`, appended to the `PRAGMA user_version` ladder. Existing migrations are never edited.

```sql
CREATE TABLE dispatch_control (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    paused      INTEGER NOT NULL DEFAULT 0,
    paused_at   TEXT,
    paused_by   TEXT
);
INSERT INTO dispatch_control (id, paused) VALUES (1, 0);
```

| Column | Meaning |
|---|---|
| `id` | Constrained to `1`. This is a single-valued fact about the whole system, and the check makes a second row impossible rather than merely unlikely |
| `paused` | `0` or `1`. Read by the daemon before every dispatch decision |
| `paused_at` | UTC ISO 8601 with `Z`, matching every other timestamp in the schema. `NULL` when not paused |
| `paused_by` | `web` or `cli` — which interface set it, so "who stopped dispatch" is answerable from state as well as from the log. `NULL` when not paused |

**Why a table and not a file or a config key**: R6. Durability across restart and reboot is FR-035's
whole point, and the database provides it atomically alongside the data it governs. `docs/state.md`
already draws the line the config option would cross — configuration is what the author *declares*,
the database is what the system *observed*, and a pause is an operational act.

**Accessors** (in `db.py`, alongside the existing ones):

```
get_dispatch_control(conn) -> DispatchControl
set_dispatch_paused(conn, *, paused: bool, by: str) -> DispatchControl
```

`set_dispatch_paused` is called inside `db.transaction` by its callers, like every other mutation.
Setting a state that already holds is a no-op that returns the existing row rather than an error —
pausing twice is not a mistake, and the audit record still names the attempt.

**Model** (in `models.py`, matching the existing frozen-dataclass style):

```
DispatchControl:
    paused: bool
    paused_at: str | None
    paused_by: str | None
```

---

## Changed: the heartbeat record

`health.Heartbeat` gains one field:

| Field | Meaning |
|---|---|
| `dispatch_paused` | `true` while dispatch is suspended |

FR-036 requires the pause to appear in the liveness signal, so that a check that "the daemon is
healthy" cannot be true while it is silently doing nothing. Added as a named field rather than inside
`extra` because `docs/state.md` and `contracts/exit-record.md` document this file's shape for a
human reading it at 2am, and a first-class field is what that reader will look for.

The field defaults to `False`, so a heartbeat written by an older build parses unchanged.

---

## New: job request markers

Not a database object. `state_dir/requests/` holds at most one empty file per forcible job:

```
~/.local/state/robot-army/requests/poll
~/.local/state/robot-army/requests/reconcile
```

| Property | Value |
|---|---|
| Lifetime | From creation until the daemon's next tick — at most `tick_seconds` (5s) |
| Creation | `open(..., O_CREAT)`; re-requesting an already-pending job is a harmless no-op |
| Consumption | The daemon unlinks the marker and sets the job's existing `forced` flag |
| Survives reboot | Yes, harmlessly — a startup already reconciles and polls, so a leftover marker costs one redundant job at most |
| Written by | `cli` and `web`, through `operations.poll_now` / `operations.reconcile_now` |

Only these two names are valid. An unrecognised file in the directory is ignored and reported once as
a log record rather than deleted, because deleting something the system does not understand is worse
than leaving it.

**Why not a signal**: R5. The daemon may be mid-tick; a marker waits. And signalling a PID read from
a lock file is the weak-evidence process identification this project has already been bitten by.

---

## Derived view models

Assembled per request, never persisted, never cached except where R9 says so. Each is a plain dict
so that it is simultaneously the JSON representation (FR-001) and the input to the HTML renderer
(R2). Field names match the existing `_item_dict` / `_session_dict` / `_anomaly_dict` helpers in
`operations.py` and reuse them wherever the shape already fits.

### `Chrome` — on every page

| Field | Source | Note |
|---|---|---|
| `effect_level` | `ctx.effect_level` | FR-016 |
| `daemon` | `daemon.is_locked` + `heartbeat.json` | `running`, `pid`, `activity`, `heartbeat_age_seconds`, `healthy` |
| `effect_mismatch` | heartbeat vs. `ctx.effect_level` | Non-null blocks mutations (R4, FR-005) |
| `dispatch_paused` | `dispatch_control` | With `paused_at`, FR-036 |
| `anomaly_count` | `db.list_anomalies` | FR-017: visible from every view |
| `include_simulated` | query parameter | FR-019, default false |
| `rendered_at` | now | FR-018: the page states how old it is |

`daemon.running` false is the FR-005 case: read views render normally, the chrome says so
prominently, and controls that need the daemon refuse.

### `ActiveRow` — the active view (FR-011)

`item_id`, `repo_key`, `issue_number`, `title`, `issue_url`, `worktree_path`, `branch`,
`session_id`, `session_state`, `started_at`, `elapsed_seconds`, `simulated`.

Sourced from `db.list_work_items(states=[ACTIVE])` joined to `db.latest_session_for_item`.
`elapsed_seconds` is computed at render time.

### `QueueRow` — the queue view (FR-012, FR-013)

`item_id`, `repo_key`, `issue_number`, `title`, `issue_url`, `state`, `position`, `since`,
`blocked_reason`, `simulated`.

Three groups on one page: `ready` in dispatch order (`position` is the index in that order, which is
`ORDER BY id` — the same order `select_and_dispatch` uses), `dispatching` with how long it has been
there against the max age, and blocked items (`failed`, plus anything carrying `blocked_reason`) with
the specific reason.

### `InterruptedRow` — the interrupted view (FR-014)

`item_id`, `repo_key`, `issue_number`, `title`, `issue_url`, `branch`, `worktree_path`,
`ended_at`, `last_session`, `simulated`, and:

| Signal | Cost | Freshness |
|---|---|---|
| `uncommitted_changes` | local `git status --porcelain` | recomputed every render |
| `commits_on_branch` | local `git rev-list --count` | recomputed every render |
| `issue_closed` | GitHub | cached 60s in-process, with `signals_age_seconds` rendered |
| `open_pr` | GitHub | cached 60s in-process, with `signals_age_seconds` rendered |

The split and its justification are R9. `worktree_missing` is a fifth derived condition, surfaced
distinctly because 001 (FR-017) made it a recoverable state rather than an error.

### `ItemDetail` — the item page (FR-015)

The payload of `operations.show`, unchanged, plus the interrupted signals when the item is in a state
where they mean anything, plus the list of actions currently legal for it. The action list is derived
from the item's state, which is what makes FR-029 ("a control MUST NOT be offered where it is not
valid") a property of one function rather than a rule scattered through templates.

### `AuditPage` — the audit view (FR-042, FR-043, FR-044)

`records`, `filters`, `skipped_line_count`, `has_more`, `next_cursor`.

Records come from `operations.read_log`, which already filters and already skips-and-counts
unparseable lines. Paging is by (file, offset) working backwards through daily files (R14). Each
record is rendered with its `entity_type`/`entity_id` and `target` turned into links where they name
a GitHub repository, issue, or pull request — constructed from data already in the record, with no
additional source-system call.

---

## Validation rules

| Rule | Where enforced | Requirement |
|---|---|---|
| A work item action is legal only from the states 001 allows | `states.transition_work_item`, inside `db.transaction` | FR-027, FR-028 |
| Simulated rows are excluded unless explicitly asked for | `db.*` accessors' `include_simulated=False` default — unchanged | FR-019 |
| Only `paused`/`unpaused` may be written to `dispatch_control` | The accessor takes a `bool`; there is no free-form value | FR-033 |
| Only `poll` and `reconcile` are valid request markers | A literal tuple in `control.py` | — |
| The bind address must not be globally routable | `ipaddress.ip_address(...).is_global` at startup | FR-004 |
| The schema version must equal the code's | Startup precondition; the web never migrates | R11 |
| No rendered response contains the configured token | Payloads originate from `operations.*`; a test asserts it | FR-020 |

---

## State transitions

This milestone introduces **no new work item or session states**. Every action it offers moves an
item through a transition 001 already defined:

| Action | From | To | Performed by |
|---|---|---|---|
| resume | `interrupted`, `awaiting_review` | `dispatching` → `active` | `operations.resume` (worker thread) |
| restart | `interrupted`, `awaiting_review` | `dispatching` → `active` | `operations.restart` (worker thread) |
| abandon | any legal source | `abandoned` | `operations.abandon` |
| cancel | `active` | `interrupted` | `operations.cancel` |
| retry | `failed` | `ready` | `operations.retry` |
| attach | `active` | *(no transition)* | `operations.attach` |
| acknowledge | *(anomaly, not an item)* | acknowledged | `operations.anomalies` |

The pause is orthogonal to both state machines: it gates whether `select_and_dispatch` runs at all,
and changes no item's state. Items simply remain `ready`, which is FR-034's requirement that they
accumulate rather than being rejected or lost.

---

## Interruption behaviour

Following 001's table, for what this milestone adds.

| Interrupted at | Result on the next request or start |
|---|---|
| While rendering any view | Nothing was written; the next request re-reads and re-renders |
| After a POST arrived, before its transaction committed | Rolled back by `db.transaction`. The browser sees a dropped connection; the item page tells the truth on reload |
| After the transaction committed, before the response was sent | Applied. A reload shows the new state, and the `303` pattern means no re-post |
| Mid-`resume`, after the `dispatching` transition | Exactly 001's case: the confirmation window elapses, reconciliation finds no session, the item returns to `interrupted`. The audit log holds the intent with no outcome |
| After writing a request marker, before the daemon read it | The marker persists and is consumed on the next tick or the next start |
| After the daemon unlinked a marker, before running the job | The forced flag is lost; the job runs on its ordinary interval. Acceptable: the cost is waiting out one interval, and the alternative — unlink after running — risks running it twice |
| Mid-`set_dispatch_paused` | Rolled back; dispatch continues as before. The pause is not half-applied |
| Mid-migration 002 | `user_version` never advanced; the whole migration re-runs on the next start, as 001's ladder guarantees |
| Web process killed while a worker thread is dispatching | The thread dies with the process. The item is left mid-dispatch and handled by the case above — reconciliation, not the web, is what resolves it |

The last row is the reason the worker thread needs no supervision, no restart, and no record of its
own: the daemon's reconciliation already owns "an item in `dispatching` with nothing behind it", and
this is not a new way to produce that condition.
