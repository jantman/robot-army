# Phase 1 Data Model: Holding Items and Repositories Out of Dispatch

**Feature**: [spec.md](spec.md) | **Research**: [research.md](research.md) | **Plan**: [plan.md](plan.md)

## Migration 010

Appended to `MIGRATIONS`; never editing an existing one. One transaction, `user_version`
advanced last, so an interrupted upgrade re-runs the whole migration (Principle IV).

**No backfill, and none is possible.** No hold existed before this migration, so an upgraded
database is correct the instant the tables exist. Nothing is added to `work_items` or `repos`.

```sql
-- One work item the author has taken out of dispatch until they say otherwise (issue #117).
--
-- The work item id is the PRIMARY KEY, which does two jobs at once. It makes "at most one
-- hold per item" a constraint rather than a convention, so FR-004's idempotence is the
-- database refusing a duplicate rather than code deduplicating on read. And it is the whole
-- row apart from the two provenance columns -- a hold has no levels, no expiry and no note
-- (FR-026, research R10), so presence *is* the fact.
--
-- ON DELETE CASCADE is FR-025 and is the reason this is two tables rather than one table with
-- a scope column (research R1). `PRAGMA foreign_keys` is ON -- db.py's docstring says the
-- schema relies on it and test_migrations asserts it -- so a hold cannot outlive the item it
-- holds, and cannot reattach itself to a recycled id. `db.purge_simulated` is the only path
-- that deletes a work item today and needs no change: the cascade is the cleanup.
--
-- Simulated rows are covered with no special case, because they are work_items rows like any
-- other. A dry-run item occupies a queue slot, so it can be held; a hold that ignored
-- simulated work would rehearse the wrong behaviour.
CREATE TABLE item_holds (
    work_item_id INTEGER PRIMARY KEY REFERENCES work_items(id) ON DELETE CASCADE,
    held_at      TEXT NOT NULL,
    held_by      TEXT NOT NULL
);

-- Every work item in one repository, taken out of dispatch until released (issue #117).
--
-- Keyed on repos(repo_key), not on a bare string, and that is the point: FR-006 refuses a
-- hold on a repository that was never onboarded, and this makes a typo impossible to store
-- rather than merely unlikely. A hold on a repository the system does not watch would hold
-- nothing and report nothing wrong, which is the worst available outcome.
--
-- A separate table rather than a column on `repos` because `repos` is an *approval* record:
-- migration 005 is emphatic that it stores what a human approved at a verified location and
-- that nothing re-derives after approval. A hold is the opposite -- temporary, toggled often,
-- and meaningless a week later. `repo_projects` set the same precedent in migration 009.
--
-- This is what makes FR-012 free: the hold is a fact about the repository, so an item
-- discovered tomorrow in a held repository is held on arrival with nothing to backfill and no
-- event to hook.
CREATE TABLE repo_holds (
    repo_key TEXT PRIMARY KEY REFERENCES repos(repo_key) ON DELETE CASCADE,
    held_at  TEXT NOT NULL,
    held_by  TEXT NOT NULL
);
```

**No index is created on either table.** The primary key is the only access path — both are
read whole into a dict once per plan (R5) and written one row at a time by key. A secondary
index would have no query to serve.

**`held_by` is `NOT NULL`**, unlike `dispatch_control.paused_by`, which is nullable because it
is cleared when dispatch resumes. A hold has no cleared state: the row exists or it does not,
so every row that exists was placed by something and can say which.

## The model

```python
@dataclass(frozen=True, slots=True)
class Hold:
    """One deliberate statement that some work must not be dispatched (issue #117).

    Carries no target. The two accessors return ``{target: Hold}``, so the key is the target
    and the value is everything else -- which is why one dataclass serves both tables despite
    their key columns differing.
    """

    held_at: str
    held_by: str
```

Deliberately **not** registered in `models.ROW_TYPES`. That mapping exists so `db.py` can pick
a row factory per *table* for queries returning whole rows; both hold queries select two columns
into a dict keyed by the target, so a factory registration would be an entry nothing reads.

## Accessors (`db.py`)

Two readers and four writers. Every writer is called inside `db.transaction` by its caller,
matching `set_dispatch_paused`.

| Function | Returns | Guarantees |
|---|---|---|
| `list_item_holds(conn)` | `dict[int, Hold]` | One scan. Every item hold in force, keyed by work item id. |
| `list_repo_holds(conn)` | `dict[str, Hold]` | One scan. Every repository hold in force, keyed by repo key. |
| `set_item_hold(conn, item_id, *, by)` | `tuple[Hold, bool]` | The hold now in force, and whether it was newly placed. |
| `clear_item_hold(conn, item_id)` | `Hold \| None` | The hold that was removed, or `None` if there was none. |
| `set_repo_hold(conn, repo_key, *, by)` | `tuple[Hold, bool]` | As above, for a repository. |
| `clear_repo_hold(conn, repo_key)` | `Hold \| None` | As above, for a repository. |

**The `bool` in each setter's return is FR-004 made explicit.** `set_*_hold` uses
`INSERT ... ON CONFLICT DO NOTHING` and then reads the row back, so holding something already
held returns the **existing** hold with its **original** `held_at` — never a refreshed
timestamp. This is the same judgement `set_dispatch_paused` already makes and states: *pausing
twice is not a mistake, and reporting the pause that is already in force, with its original
timestamp, is more useful than reporting a fresh one.*

**Each clearer returning what it removed is FR-005.** The caller distinguishes "released a hold
placed at *t*" from "there was nothing to release" without a second query, and reports the
second as a no-op rather than a failure.

**Neither reader raises on an empty table.** No holds is the overwhelmingly common state and it
is not an error condition; both return an empty dict.

## Validation rules

Enforced where each can be enforced closest to the fact:

| Rule | Where | Requirement |
|---|---|---|
| At most one hold per item / per repository | `PRIMARY KEY` | FR-004 |
| A hold never outlives what it holds | `ON DELETE CASCADE` | FR-025 |
| No hold on a repository that was never onboarded | `REFERENCES repos(repo_key)`, and `repos.known` checked first for the message | FR-006 |
| No hold on a work item that does not exist | `REFERENCES work_items(id)`, and `db.get_work_item` checked first for the message | FR-006 |
| A hold change is all or nothing | `db.transaction` (`BEGIN IMMEDIATE`) | FR-024 |
| Holds survive restart and reboot | the database itself | FR-021, FR-022 |

The foreign keys are the enforcement; the pre-checks in `operations` exist so the author gets
*"no repository jantman/typo is onboarded"* instead of an integrity error. Both layers are
deliberate: the message is for the author, the constraint is for correctness, and neither is
load-bearing for the other.

## State transitions

**None.** No work item state changes, no session state changes, and no existing table gains a
column. That is the property the spec's Key Entities section promises: a hold is recorded
beside an item, never inside it, so it can be placed and released while the item transitions
freely — or never transitions at all.

The only lifecycle a hold has is its own:

```
absent --(hold)--> present --(unhold)--> absent
              |                     |
              +-- hold again: no-op, original held_at kept (FR-004)
                                    +-- unhold again: no-op, reported (FR-005)

present --(the held work item or repository row is deleted)--> absent   [cascade, FR-025]
```

Nothing else removes a hold. In particular, a work item reaching `done` or `abandoned` leaves
its hold in place (R11): automatic clearing on a transition is expiry under another name, which
FR-026 rules out, and the `holds` listing shows each held item's current state so a hold on
finished work is visible rather than mysterious.

## What this feature does not store

Stated because each was considered and declined, and because the absences are load-bearing:

- **No order.** Manual reordering is out of scope (FR-027). `ordering.plan`'s sort keys are
  untouched and nothing about position is persisted — the module's standing invariant, that the
  order is a sort key and the position is a list index, survives this feature intact.
- **No expiry timestamp**, because nothing expires (FR-026).
- **No note or reason text**, because the audit record carries more than a note would (R10).
- **No configuration.** `config.py` and `share/config.example.toml` are unchanged.
- **No counter of how often something was held.** The audit log is the history; a table that
  duplicated it would be a second source of truth for the same question.
