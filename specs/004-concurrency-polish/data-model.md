# Data Model: Concurrency & Polish

What this milestone adds to the schema, what it computes without storing, and what happens when it is
killed at each point. Referenced decisions are R1..R17 in [research.md](research.md).

The headline is how little is stored. Capacity, queue position, hold reasons, and dispatch order are
all **derived at the moment they are needed** (R1, R7, R8) — none of them is a column, because a
stored copy is a second source of truth that can disagree with the dispatcher. The only durable
addition is the record of what cleanup did, which is durable precisely because it is the answer to a
question asked long after the fact.

---

## Migration 004

Three nullable columns on `work_items`. No new table (R13).

```sql
ALTER TABLE work_items ADD COLUMN cleanup_state  TEXT;
ALTER TABLE work_items ADD COLUMN cleanup_reason TEXT;
ALTER TABLE work_items ADD COLUMN cleaned_at     TEXT;
```

`work_items.state` is **not** touched and `WORK_ITEM_TRANSITIONS` gains no entries. Whether an item's
disk has been reclaimed is a different axis from whether its work is finished — the same separation
§7 makes between work state and session state.

`worktree_path` and `branch` are **never nulled** after a removal. FR-024 requires the record to
retain what was removed, `_sweep_worktrees` already keys on the path being present, and "what was at
this path?" is exactly the question a retained-branch record has to answer.

### `cleanup_state` values

| Value | Meaning | Next pass does |
|---|---|---|
| `NULL` | Never considered. Every pre-migration row, and every row while cleanup is disabled | Considers it, if eligible |
| `done` | Worktree removed and branch removed | Nothing |
| `branch_retained` | Worktree removed; branch kept because containment could not be proved (R12) | Nothing — a retained branch is a decision, not a pending step |
| `retained` | Neither removed; a guard refused. `cleanup_reason` names which | Nothing automatically; `robot-army cleanup <id>` reconsiders |
| `skipped` | Not attempted — a session was still live (FR-027) | Reconsiders it |

`skipped` is the only non-`NULL` value the automatic pass revisits, and that is the point of
distinguishing it from `retained`: one is "not yet", the other is "we looked and decided no".

---

## Computed, never stored

### `CapacitySnapshot` (`capacity.py`, R1–R5)

```python
@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    observable: bool          # False -> withhold dispatch entirely (R4, FR-007)
    degraded: bool            # True  -> counted via /proc; session ids unavailable
    total: int                # every live worker session on the machine
    ours: tuple[str, ...]     # session ids we started (R5)
    others: int               # a count, never a handle (R5, FR-006)
    global_cap: int
    per_repo: dict[str, int]  # repo_key -> live sessions in that repository
    reason: str | None        # why unobservable, when it is
```

**`total` is a union by session id**, not a sum and not a maximum (R3):

```
total = |registry entries alive|
      + |our starting/running rows whose session_id matches no registry entry|
```

The second term is the launch window. Between the host returning and the worker writing its registry
file, a dispatch in flight is invisible to the registry; counting it is what makes FR-009's guarantee
hold for two dispatches in the same tick. A row counted this way may also be a dead session not yet
reconciled — counting it briefly errs toward withholding, which is the safe direction, and the next
reconciliation pass clears it.

**`others` is an integer on purpose.** No control path can obtain a PID for a session the system did
not start, so FR-006 is kept by the type rather than by review (R5).

**`per_repo` counts simulated sessions** exactly as the global count does (FR-004), and is keyed only
by repositories the system started something in — an out-of-band session in a repository directory is
not attributable to a repository, because the author's own clone is not under the worktree root.

### `QueueEntry` and `HoldReason` (`ordering.py`, R7–R9)

```python
class HoldReason(StrEnum):        # precedence order, first match wins (R9)
    PAUSED = "paused"
    CAPACITY_UNOBSERVABLE = "capacity_unobservable"
    GLOBAL_CAP = "global_cap"
    REPO_CAP = "repo_cap"
    NOT_ONBOARDED = "not_onboarded"
    PREPARATION_FAILED = "preparation_failed"

@dataclass(frozen=True, slots=True)
class QueueEntry:
    item: WorkItem
    position: int                 # 1-based index in the current order
    hold: HoldReason | None       # None -> dispatchable right now
    detail: str                   # e.g. "repo foo: 1 of 1"
```

`ordering.plan(conn, *, config, capacity) -> list[QueueEntry]` is a pure function of the database, the
configuration, and a snapshot. It is the **only** producer of dispatch order: `select_and_dispatch`
walks it, and `queue_view` and `robot-army status` render it (R8). Position is a list index computed
on read and is never persisted (FR-019).

Order keys (R7), applied in Python because priority lives in TOML rather than in the database:

| Mode | Key |
|---|---|
| `oldest-first` | `(discovered_at, id)` |
| `repo-priority` | `(-priority, discovered_at, id)` |

Both are total, which is what SC-006's hundred consecutive checks require.

### `NotificationEvent` (`notifications.py`, R14)

```python
@dataclass(frozen=True, slots=True)
class NotificationEvent:
    kind: str                  # dispatch | completion | failure | needs_info
    item_id: int | None
    repo_key: str | None
    title: str                 # one line, no credentials (FR-037)
    detail: str                # where to look
    url: str | None            # issue or card, whichever exists
```

Composed only from identifiers and state names. There is no field a secret could reach, and a test
asserts it across a run including an authentication failure (SC-010).

---

## Audit actions added

Every one is an `audit.action` intent/outcome pair written before the call, or an `audit.record` for a
decision that touches nothing outside the process.

| Action | When | Carries |
|---|---|---|
| `dispatch.at_capacity` | **Changed** (R16) — on hold-signature change, not every pass | counts, cap, head item, ours/others split |
| `dispatch.hold_ended` | When a capacity hold clears | duration, passes spanned, what freed it |
| `capacity.unobservable` | The registry and `/proc` both failed (R4) | which failed, and how |
| `cleanup.considered` | An item was evaluated for cleanup | decision and the guard that decided it |
| `git.remove_worktree` | Existing action, new caller | path, force (always false here), outcome |
| `git.delete_branch` | Existing action, new caller | branch, force (true only after R12's check passed), containment evidence |
| `cleanup.retained` | A guard refused removal | which guard, what it saw |
| `notify.send` | A notification is attempted | kind, item, whether it was suppressed by the cycle bound |
| `notify.suppressed` | The per-cycle bound was reached (R15) | how many, of which kinds |

`capacity.unobservable` also raises a de-duplicated anomaly of the same kind, so a persistent
observation failure is visible in `robot-army anomalies` rather than only in the log.

---

## Interruption table

Principle IV's question — "what happens if it is killed halfway through?" — answered per kill point.
Every row has a test.

| Killed at | State on disk | Recovery |
|---|---|---|
| After the capacity snapshot, before dispatch | Nothing written | The next pass takes a fresh snapshot. A snapshot is never stored, so it cannot be stale |
| Between two dispatches in one pass | Earlier items dispatched, later ones still `ready` | The next pass re-snapshots and re-plans. `select_and_dispatch` holds no cross-pass state |
| During a hold, before the signature was recorded | No `at_capacity` record for this hold | The next pass sees no remembered signature and writes one. Worst case is one extra record, never a missing hold |
| After `git worktree remove`, before `git branch -d` | Worktree gone, branch present, `cleanup_state` unwritten | The next pass finds a `done` item whose worktree path no longer exists, re-runs the branch half, and records the outcome. The removal is idempotent — removing an absent worktree is a refusal, not a corruption |
| After both removals, before the row is written | Both gone, `cleanup_state` still `NULL` | The next pass re-attempts, both steps refuse harmlessly (nothing to remove), and the row is written `done` |
| During `git fetch` for the containment check | Nothing removed | Containment is unproven, so the branch is retained and the item is reconsidered next pass. The failure direction is always "keep" |
| After a state transition, before its notification | State committed and logged; no message sent | The state change is fully reconstructible from the log. The lost message is the gap named in plan.md's Principle III section — accepted, not hidden |
| Mid-notification, after the POST left | Possibly delivered, recorded as attempted with its outcome | No retry. A duplicate notification is noise; a retry loop is a Principle IV violation |
| Mid-migration 004 | `user_version` still 3 | The ladder re-runs migration 004 from the start. `ALTER TABLE ADD COLUMN` is the only statement, and the version bump is last |

---

## What is deliberately not modelled

- **No queue table, no queue object, no queue state.** The queue is the set of `ready` rows the
  database already holds; the order is a sort key over it (R7); the position is a list index (R8).
- **No `priority` column.** Priority is configuration the author edits, not state the system
  discovers. Copying it into the database would require a sync, and the sync would be the bug (R6).
- **No `cleaned` work-item state.** `done` is terminal and stays terminal (R13).
- **No durable notification queue, watermark, or outbox** (R14, R15). The in-process per-cycle counter
  and hold signature are deliberately volatile: losing them costs one extra record and a handful of
  extra messages, which is far less than a table costs to keep correct.
- **No aging or starvation counter** (FR-021). Starvation under repository-priority ordering is an
  accepted, documented consequence of choosing that mode.
