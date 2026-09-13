# Data Model: A Simulated Removal Says It Was Simulated

No table, column or migration. Three shapes gain a field.

## `VersionControl.simulated` (boundary attribute)

| Implementation | Value |
|---|---|
| `GitVersionControl` | `False` |
| `SimulatedVersionControl` | `True` |

Constant for the life of the object. Read by `_remove_checkout`, `worktree_prune` and
`cleanup.clean_item`.

## `cleanup.Decision`

| Field | Change |
|---|---|
| `simulated: bool = False` | **New.** `True` when the decision was reached through a removal the boundary simulated. Ineligible and unresolved-repository decisions never reached the boundary and stay `False`. |
| `reason` | Wording changes when `simulated` (see contract W5–W7). |
| `reclaimed` | Unchanged — `worktree_removed`. `cleanup_now` distinguishes "would" by `simulated`. |

A surviving directory yields `state="retained"`, `worktree_removed=False`, `branch_deleted=False`,
`reason=SURVIVED_REASON`.

## `worktree.remove` outcome record

Gains `simulated` (bool) whenever git was reached, beside `worktree_removed` and `branch_deleted`.
`worktree_removed: true, simulated: true` reads: the boundary reported the removal, and it was
simulated.

## `--json` data

| Command | Addition |
|---|---|
| `worktree remove` (both forms) | `simulated` once git was reached |
| `worktree prune` | `simulated` |
| `cleanup` | `simulated` on each decision |
