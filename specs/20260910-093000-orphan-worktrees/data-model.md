# Data Model: No Worktree Is Left Where robot-army Cannot Reach It

**No schema change.** No table, column, index, migration or state file is added. The
`anomalies.kind` column is unconstrained text, and the partial unique index already covers the
new kind.

## Orphaned worktree (derived, never stored)

Computed on demand from the disk and the `work_items` table; the only persistent trace is the
anomaly that reports it.

| Field | Source |
|---|---|
| `path` | `<worktree_root>/<short>/issue-<n>`, resolved, for each `short` among onboarded repositories (research R4) |
| `claimed` | always false by definition — a directory whose resolved path equals any work item's resolved `worktree_path` is not an orphan (R5) |
| `repo_key`, `clone` | the onboarded repository whose clone lists `path` in `git worktree list`; absent when none does |
| `branch` | from that listing; `None` for detached HEAD or when no clone lists it |

## `orphan_worktree` anomaly

| Column | Value |
|---|---|
| `kind` | `orphan_worktree` |
| `entity_type` | `worktree` |
| `entity_id` | the resolved path |
| `dry_run` | `0` — no row to take a run's flag from (R7) |
| `detail.path` | the path |
| `detail.listed_by` | the repository key whose clone lists it, or `null` |
| `detail.branch` | the branch, or `null` |
| `detail.listing_failed` | `true` when a clone's worktree list could not be read this pass (only present then) |
| `detail.note` | how to remove it: `robot-army worktree remove <path>` when a clone lists it; otherwise that no clone lists it as a worktree, so robot-army will not remove it |

Lifecycle: raised by the orphan sweep → deduplicated by the existing index while it holds →
`resolved_at` set by `_resolve_orphan_worktree_anomalies` when the directory is gone or claimed
(R8), or `acknowledged_at` set by the maintainer.

## `Session.hosted_by_simulation` (property)

`dry_run and pid == 0 and proc_start is None` — the signature only `SimulatedSessionHost`
writes. Moved from a local in `operations.cancel`; read by `cancel` and by the purge's session
guard (R2).

## `ReconcileResult.orphan_worktrees`

New counter: `orphan_worktree` anomalies newly raised this pass, reported in the pass summary as
`orphan_worktrees` beside `prunable_worktrees`. Resolutions count toward the existing
`anomalies_resolved`.

## Audit records

No new action name.

- **`worktree.remove`** gains two shapes:
  - by path: `entity_type: "worktree"`, `entity_id` and `target` = the path, `detail.by:
    "path"`. The outcome carries the same keys as the item-id form, plus `refused_by` values
    `outside_root`, `claimed` (with `claimed_by_item`), `not_a_directory`, `not_a_worktree`,
    `live_worker` (with the worker's `pid` and `session_id`).
  - from a purge: `entity_type: "work_item"`, as the item-id form, with `detail.by:
    "purge-simulated"`. Its refusals add `refused_by: "live_session"` as today.
  - both forms: `refused_by: "directory_survived"` when git reported success and the directory
    is still there (R3).
- **`purge.simulated`** intent detail gains `worktrees` (paths offered) and `remove_worktrees`
  (bool). Its outcome gains `worktrees_removed` and `worktrees_left` (paths).
- **`anomaly.resolved`** for `orphan_worktree`: `kind`, `anomaly_entity_id` (the path),
  `reason` (`directory_gone` or `claimed_by_item`) and `claimed_by_item` when that applies.
