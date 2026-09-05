# Phase 1 — Data model

The feature's storage footprint is one nullable column, one index rebuilt, and one field parsed out
of a file that is already being read. Everything else reuses what is there.

## Schema change — migration 12 (schema 11 → 12)

```sql
-- An anomaly can now stop being true without anyone reading it. `orphan_session` is the
-- only kind that can be positively re-established as false — the process it names is gone —
-- and this column records that we asked and got that answer. It is deliberately NOT
-- `acknowledged_at`: acknowledgement is a maintainer saying "I have seen this", and
-- collapsing the two would make `robot-army anomalies --all` unable to tell the difference
-- between a condition that resolved and one someone dismissed.
ALTER TABLE anomalies ADD COLUMN resolved_at TEXT;

-- The partial index has to be rebuilt, and this is the part that is easy to get wrong.
-- Its purpose is to stop a 60-second loop writing 1,440 identical rows a day; a resolved
-- row left inside it would block a genuinely new occurrence of the same condition from
-- ever being recorded again. Resolution has to lift a row out of the index for exactly the
-- reason acknowledgement does.
DROP INDEX idx_anomalies_open;
CREATE UNIQUE INDEX idx_anomalies_open
    ON anomalies (kind, COALESCE(entity_type, ''), COALESCE(entity_id, ''))
    WHERE acknowledged_at IS NULL AND resolved_at IS NULL;
```

`COALESCE` is carried over unchanged and is load-bearing for the same reason as before: in SQLite
two NULLs never compare equal, so indexing the bare columns would let an anomaly with no entity
duplicate on every pass.

### `anomalies` after the migration

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `kind` | TEXT NOT NULL | one of `ANOMALY_KINDS`; unchanged, and **no kind is added** |
| `entity_type` | TEXT | unchanged |
| `entity_id` | TEXT | unchanged |
| `detail` | TEXT NOT NULL | JSON. For `orphan_session` it already carries `pid` and `proc_start`, which is the evidence re-checking needs (R9) |
| `detected_at` | TEXT NOT NULL | unchanged |
| `acknowledged_at` | TEXT | unchanged — the maintainer said "seen" |
| `resolved_at` | TEXT | **new** — the system re-checked and the condition no longer holds |

### Interruption behaviour

The migration is one `ALTER` and an index swap inside the transaction `migrate()` already opens, so
a kill during it leaves schema 11 intact and the migration re-runs. Consistent with the reason
`_statements()` splits scripts rather than using `executescript()`.

### Backfill

None. Every existing row has `resolved_at = NULL`, which reads as "not resolved" — correct for all
three rows currently on the machine. Anomaly 24, whose pid 498936 is already gone, is resolved by
the first pass after this ships, which is the feature working rather than a migration step.

## Model change

```python
@dataclass(frozen=True, slots=True)
class Anomaly:
    ...
    acknowledged_at: str | None = None
    resolved_at: str | None = None       # new
```

## Query change

`db.list_anomalies(conn, unacknowledged_only=True)` filters on
`acknowledged_at IS NULL AND resolved_at IS NULL`.

This one change is what makes every consumer correct without touching any of them —
`operations.anomalies` (the CLI), `operations.status` and `web/pages.py` are all callers of this
single function. `--all` continues to mean "everything, including rows that are no longer open",
which now includes resolved ones.

## New write

```python
db.resolve_anomaly(conn, anomaly_id) -> bool
```

Mirrors `acknowledge_anomaly` exactly, including returning whether it changed anything:
`UPDATE anomalies SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL`. The guard on the
`WHERE` is what makes a repeated pass idempotent, which is FR-024's "MUST NOT be duplicated" from
the other side.

## Registry entry — one parsed field, no storage

`sessions.RegistryEntry` is an in-memory parse of `~/.claude/sessions/<pid>.json` and is never
persisted.

```python
@dataclass(frozen=True, slots=True)
class RegistryEntry:
    ...
    status: str | None            # already parsed; its docstring changes, not its value
    status_updated_at: int | None # new — `statusUpdatedAt`, epoch milliseconds

    def idle_for(self, *, now_ms: int | None = None) -> float | None:
        """Seconds this session has been idle, or None if that cannot be established."""
```

`idle_for` returns `None` — never a number — when `status` is not exactly `"idle"`, when
`statusUpdatedAt` is absent or not an integer, or when it is in the future. Every one of those
resolves to "do not retire" at the call site. The parse is defensive in the same way
`parse_entry` already is about `sessionId` and `pid`: a field of the wrong type is a missing field,
not an exception, because a worker upgrade must not take the daemon down.

**Unchanged and worth stating**: `parse_entry` still refuses anything ending in `.key`, and still
gates on `KNOWN_VERSIONS`. The new field is read from the same already-decoded payload, so no new
file is opened and the existing test asserting nothing reads a credential-shaped file still covers
this code.

## What is deliberately not changed

| | Why |
|---|---|
| `sessions` table | Retirement closes a row through `transition_session` to `LOST`, which already stamps `ended_at`. Nothing needs to record "this one was retired" beyond the transition's reason and the audit log |
| `SessionState` / `SESSION_TRANSITIONS` | No new state and no new edge. `RUNNING → LOST` already exists and already means what is needed |
| `WorkItemState` / `WORK_ITEM_TRANSITIONS` | Retirement never touches the work item. `done` stays `done` |
| `work_items` table | R1: `done` already means "the issue was observed closed", so there is nothing to record |
| `ANOMALY_KINDS` | No kind is added. `orphan_session` already names the condition |
| `capacity.py` | FR-013: the cap goes on counting a live process. Slots are freed by ending processes, never by discounting them |
| `cleanup.py` and its two guards | FR-015: retirement makes the session guard pass honestly; the guard itself is untouched |
| `config.py`, `exampleconfig.py`, `share/config.example.toml` | No configuration key changes, so neither CLAUDE.md step is triggered |
