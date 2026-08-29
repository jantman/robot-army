# Data Model: Status Never Contradicts Itself About Hidden Simulated Work

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-08-29

## Schema changes

**None.** No migration, no new table, no new column, no index. `SCHEMA_VERSION` is unchanged and
`src/robot_army/migrations.py` is not touched.

This is stated rather than omitted because it is the load-bearing fact of the design: every
number this feature prints is already in the database and already reachable by the accessors
that exist. The defect was never a missing fact — it was two correct facts printed beside each
other with nothing said about how they relate.

## Existing entities, unchanged

| Entity | Table | Relevant attribute | Role here |
|---|---|---|---|
| Work item | `work_items` | `dry_run` (integer, 0 or 1) | Marks the row simulated. Set at creation from the effect level in force; never mutated afterwards. |
| Card | `cards` | `dry_run` | Same, for the Trello source (P3 only). |

`dry_run` semantics are milestone 001's and are not revisited: it governs outward-facing effects
and reporting only, never bookkeeping (FR-055), which is exactly why simulated rows count
against the concurrency cap and therefore why the queue must include them.

## Derived quantity: the withheld count

Not persisted, not cached, not carried between invocations. Computed twice per `status` call and
discarded when the command exits.

**Definition**: for a given query scope, the number of rows matching that scope that were
excluded from the rendered output solely because they are simulated and simulated rows were not
requested.

**Scopes** — the two are distinct and must not be conflated (see [research.md R2](research.md)):

| Section | Scope of the visible query | Scope of its withheld count |
|---|---|---|
| Counts by state | All work items, no filters | All simulated work items, no filters |
| Item listing | Work items matching `--state` and `--repo` | Simulated work items matching the same `--state` and `--repo` |

**Invariants**:

1. When `--include-simulated` is passed, both counts are exactly `0`. Nothing was withheld, so
   nothing is disclosed.
2. The withheld count for a section equals the increase in that section's visible row count that
   passing `--include-simulated` would produce, for the same database and the same filters. This
   is the property that makes the number honest, and it is directly testable by running the
   command both ways.
3. The count is never negative and never exceeds the total row count for its scope.
4. The count is independent of the queue. The queue draws only `ready` items and honours no
   filters; it can neither supply nor validate this number.

**Failure behaviour**: none of its own. The count comes from a `COUNT(*)` on the connection
`status` has already opened and used several times by that point; a database error raises and
propagates exactly as the existing listing queries' errors do, and `status` exits non-zero. There
is no fallback value — reporting a guessed withheld count would reintroduce the class of defect
this feature removes.

## Accessors added

Both live in `src/robot_army/db.py`, beside the listing accessors they mirror, so that
`_scope`'s definition of "simulated" stays the only one in the codebase.

```
count_simulated_work_items(conn, *, states=None, repo_key=None) -> int
count_simulated_cards(conn, *, states=None) -> int          # P3 only
```

They take no `include_simulated` parameter by design: counting withheld rows *is* the
simulated-only question. They must not be added to `LISTING_ACCESSORS` in
`tests/unit/test_db_scope.py`, whose structural assertion is about accessors that can be asked
for either scope.

The `worktree list` disclosure (P3) needs no accessor. Its set is defined partly in Python — work
items that have a `worktree_path` — so its count is taken from the rows it already walks rather
than from SQL that would have to duplicate that predicate.
