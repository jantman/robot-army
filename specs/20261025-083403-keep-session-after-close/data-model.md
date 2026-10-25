# Data model

## `item_keeps` (migration 016)

| Column | Type | Meaning |
|---|---|---|
| `work_item_id` | `INTEGER PRIMARY KEY REFERENCES work_items(id) ON DELETE CASCADE` | The kept item. The primary key makes "one keep per item" a constraint. |
| `kept_at` | `TEXT NOT NULL` | UTC ISO timestamp of placement. A redundant keep leaves this value alone. |
| `kept_by` | `TEXT NOT NULL` | The surface that placed it: `cli` or `web`. |

Presence is the fact. The table has no expiry, release timestamp, `dry_run` column or note
(research R1 and R7). It needs no backfill, because no keep existed before the migration.

Accessors in `db.py`, beside the hold accessors:

- `list_item_keeps(conn) -> dict[int, Keep]`: one scan, empty when nothing is kept.
- `set_item_keep(conn, item_id, *, by) -> tuple[Keep, bool]`: insert if absent, return the
  row in force and whether it was newly placed.
- `clear_item_keep(conn, item_id) -> Keep | None`: delete, return what was removed.
- `count_kept_sessions_by_repo(conn) -> dict[str, int]`: counts session rows in
  `starting`/`running` whose item is `done` and kept, grouped by `repo_key` (research R6).
- `is_kept(conn, item_id) -> bool`: used inside `reclaim_stale_session`, which handles one row
  at a time.

`models.Keep(kept_at: str, kept_by: str)` is frozen, and its shape matches `models.Hold`.

## Derived conditions (never stored)

| Name | Definition | Read by |
|---|---|---|
| **kept session** | An open session row (`starting`/`running`) whose item is `done` and has a keep | retirement (skips it), `reclaim_stale_session` (leaves it without an anomaly), cleanup candidates (excludes it), capacity (annotates it) |
| **inert keep** | A keep whose item is terminal and has no open session row | `keeps` and the web listing only ("no live session") |
| **pending keep** | A keep whose item has not yet reached `done` | nothing. It becomes a kept session when the item reaches `done` with a live worker. |

## State transitions

A keep has two states, present and absent:

- **absent → present**: `keep <id>` (CLI) or `POST /item/<id>/keep`. Refused when the item is
  unknown, or when it is terminal with no open session (FR-010).
- **present → absent**: `unkeep <id>` or `POST /item/<id>/unkeep`, or the item row being
  deleted (cascade, simulated purge only).

No state transition of the work item or the session touches a keep. A keep's presence changes
no work-item or session transition either: `done` is still reached by
`_resolve_closed_issues` alone (`tests/unit/test_done_single_writer.py`).

## `CapacitySnapshot` additions (not stored)

- `kept_per_repo: dict[str, int]`, defaulting to empty. It is a subset of the sessions
  already counted in `ours`.
- `kept` (property): `sum(kept_per_repo.values())`.
- `components` is unchanged. `breakdown` annotates the `ours` term (contract `capacity.md`).
