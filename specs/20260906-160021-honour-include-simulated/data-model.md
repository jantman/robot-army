# Phase 1 Data Model: rehearsed-ness as a stored property of an anomaly

One column, one rebuilt index, one migration. Everything else in this feature reads data that
already exists.

## `anomalies` — the new column

Schema version 13 → 14.

| Column | Type | Added | Meaning |
|---|---|---|---|
| `id` | INTEGER PK | 001 | |
| `kind` | TEXT NOT NULL | 001 | One of `models.ANOMALY_KINDS` |
| `entity_type` | TEXT | 001 | `work_item`, `session`, `card`, `repo`, `board`, or NULL |
| `entity_id` | TEXT | 001 | The entity's id *in its own namespace* — a work item's integer as text, a session id, a Trello card id |
| `detail` | TEXT NOT NULL | 001 | JSON written by whatever raised it |
| `detected_at` | TEXT NOT NULL | 001 | UTC ISO 8601 |
| `acknowledged_at` | TEXT | 001 | A maintainer looked at this and dismissed it |
| `resolved_at` | TEXT | 012 | The system re-checked and the condition no longer holds |
| **`dry_run`** | **INTEGER NOT NULL DEFAULT 0** | **014** | **The run that raised this was rehearsed** |

```sql
ALTER TABLE anomalies ADD COLUMN dry_run INTEGER NOT NULL DEFAULT 0;
```

### Why `NOT NULL DEFAULT 0` and not nullable

Migrations 011 and 013 both chose nullable columns so that "never asked" stayed distinguishable
from "asked and the answer was empty". This one deliberately does the opposite, and the
difference is that here the two readings do not cost the same.

There is nothing to back-fill from — an existing row carries no evidence of which run raised it.
`0` reads as *real*, so every pre-014 row stays visible. Showing a real anomaly that was in fact
rehearsed is a row the reader dismisses; hiding a rehearsed one that was in fact real is a
condition nobody ever sees. Only the first is recoverable, so the default errs toward visible.

The same asymmetry sets the Python default: `db.raise_anomaly(..., dry_run: bool = False)`. A
future call site that forgets the argument raises a *visible* anomaly.

### The partial unique index is rebuilt

```sql
DROP INDEX idx_anomalies_open;
CREATE UNIQUE INDEX idx_anomalies_open
    ON anomalies (kind, COALESCE(entity_type, ''), COALESCE(entity_id, ''), dry_run)
    WHERE acknowledged_at IS NULL AND resolved_at IS NULL;
```

The index is what stops a 60-second reconciliation loop writing 1,440 identical rows a day for
one condition; `raise_anomaly` uses `INSERT OR IGNORE` and relies on it.

Leaving it alone would mean a rehearsed run and a real run reporting the same condition for the
same entity collide, and the insert silently keeps whichever arrived first. A real anomaly could
then be swallowed by a rehearsal — and would be *invisible in the default view*, which is
strictly worse than the bug this feature fixes. They are different facts about different work.

`COALESCE` is carried over unchanged and is still load-bearing: SQLite never compares two NULLs
equal, so indexing the bare columns leaves every entity-less anomaly colliding with nothing and
duplicating on every pass. SQLite cannot alter an index in place, hence drop and recreate —
exactly as migration 012 did when it added `resolved_at` to the same predicate.

`idx_anomalies_ack` is untouched.

## `models.Anomaly`

```python
dry_run: bool
```

Coerced from SQLite's integer by the existing `_coerce`, which already handles `bool` because
SQLite has no boolean type. No other dataclass changes.

## What decides the value at each call site

The question is not "is a `dry_run` flag in scope here" but "is the condition this reports a fact
about rehearsed work, or about the machine". [research.md R2](research.md) carries the full table
of all seventeen sites. In summary:

- **Rehearsed when their subject is** — anomalies whose entity is a work item, a session or a
  card take that row's own `dry_run`: `session_id_mismatch`, `orphan_session` (both branches that
  have a session row), `dispatching_timeout`, `no_transcript`, `config_missing_repo`,
  `prunable_worktree`, `card_create_failing`, `card_issue_missing`.
- **Always real** — the machine, the filesystem and the network are real at every effect level:
  `capacity_unobservable`, `malformed_exit_record`, `orphan_exit_record`,
  `registry_version_unknown`, `clone_path_missing`, `clone_origin_changed`,
  `board_precondition`, `board_unreachable`. Board *reads* happen at every level; only writes are
  simulated, so both board kinds report a true fact about a real board.
- **The one undecidable site** — `reconcile._orphan_sweep`'s registry-scan branch may have no
  session row to read a flag from. It stays real, correctly: an unaccounted-for process consumes
  a real slot on a real machine whatever produced it.

## Accessors

| Function | Change |
|---|---|
| `db.raise_anomaly` | new `dry_run: bool = False` keyword, written into the row |
| `db.list_anomalies` | new `include_simulated: bool = False` keyword-only, applied through the existing `_scope` helper |
| `db.list_simulated_anomalies` | **new** — the withheld rows, so the operation can apply the same `--since` window to them that it applies to the visible ones. Returns rows, not a count, and [R4](research.md) argues why |
| `db.open_card_create_failing_anomalies` | **new** — the population the retraction pass re-checks. Narrow by construction, mirroring `open_orphan_session_anomalies` |
| a card lookup by `(card_id, dry_run)` | **new** — the anomaly's `entity_id` is a Trello card id, and `idx_cards_identity` makes the pair unique in practice |

`db.list_anomalies` joins `LISTING_ACCESSORS` in `tests/unit/test_db_scope.py`, which asserts the
parameter exists, defaults to `False`, and is keyword-only. `db.list_simulated_anomalies` must
**not** be added to that list, for the reason `count_simulated_work_items` is not: counting
withheld rows *is* the simulated-only question, and `include_simulated=False` there would be
nonsense. Both new accessors say so in their docstrings.

## The audit record — read differently, written unchanged

No field is added. A record already carries `dry_run: true` or `simulated: true` when it concerns
rehearsed work, `audit.record` already writes them, and `_format_record` has always rendered
either as the trailing `[simulated]` marker.

| Field | Written by | Meaning |
|---|---|---|
| `dry_run` | the acting component | this action was taken on rehearsed work |
| `simulated` | `effects.py` boundaries | this effect was performed by a simulated boundary |

Both mean "this did not really happen", both are absent rather than `false` when untrue, and the
new filter treats either as rehearsed — the same disjunction `_format_record` already uses, moved
into `_judge_record` so one predicate decides both what is shown and how it is marked.

## State transitions

None added. `card_create_failing` retraction writes `resolved_at` through the existing
`db.resolve_anomaly`, whose `resolved_at IS NULL` guard is what makes a repeated pass a genuine
no-op rather than a second write with the same effect. An anomaly still leaves the open list by
exactly the two routes migration 012 established, and `--all` still tells them apart.

```
raised ──► acknowledged   (a maintainer typed --acknowledge)
       └─► resolved       (the system re-checked: the process is gone, or the card is linked)
```
