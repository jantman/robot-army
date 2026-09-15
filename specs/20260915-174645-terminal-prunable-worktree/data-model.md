# Data Model: A hand-deleted worktree on a finished item is reported

**No migration.** Every column already exists.

## `work_items` cleanup columns (migration 004)

| Column | Change |
|---|---|
| `cleanup_state` | Gains a second writer: `worktree remove <id>`, on a finished item, writes `done` or `branch_retained`. No new value |
| `cleanup_reason` | For a manual removal: `<worktree part>; <branch part> — by \`robot-army worktree remove\`` |
| `cleaned_at` | Stamped by the same write |
| `worktree_path`, `branch` | Kept after a manual removal of a **finished** item, as after cleanup. Still cleared for an unfinished item (research R3) |

### The manual removal's reason

| Part | Value |
|---|---|
| worktree | `worktree removed`, `worktree removal simulated`, or `worktree directory was already gone`; `(forced)` appended when `--force` was given |
| branch | `branch deleted`, `branch deletion simulated`, `the branch was already gone`, `no branch on record`, or `branch kept — git would not delete it` |

`branch kept …` is the one that records `branch_retained`; every other combination records `done`.

## `WorkItem.worktree_reclaimed` (new property)

`cleanup_state in ("done", "branch_retained")` — the record says the worktree was removed. Read by:

- `reconcile._sweep_worktrees`: such an item is never reported missing, whatever its state.
- `operations.worktree_remove`: such an item with no directory is refused `already_removed`.

## `prunable_worktree` anomaly

Shape unchanged (`worktree_path`, `branch`, `state`, `note`). Raised for any item with a recorded
path, a missing directory and `not worktree_reclaimed`. `note`:

| Item state | Note |
|---|---|
| unfinished | unchanged: `directory is gone; \`robot-army worktree prune\` clears git's record` |
| `done` | directory is gone and nothing recorded removing it; `robot-army worktree remove <id>` records it and deletes the branch, or `robot-army cleanup <id>` does so under cleanup's guards |
| `abandoned` | as `done`, without the `cleanup` alternative |
