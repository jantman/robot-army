# Contract: The Manual Removal's Record, and the Missing-Worktree Sweep

**Feature**: `specs/20260915-174645-terminal-prunable-worktree`
**Governs**: `operations.worktree_remove`, `operations._remove_checkout`,
`reconcile._sweep_worktrees`, `WorkItem.worktree_reclaimed`. Supplements
[`20260901-164616-guard-worktree-remove/contracts/worktree-removal.md`](../../20260901-164616-guard-worktree-remove/contracts/worktree-removal.md);
this contract **amends its W16** ("same `worktree_path` clear") for finished items, and changes
nothing else in it.

## The sweep

**M1.** Every work item with a recorded `worktree_path` is considered, whatever its state.

**M2.** An item for which `worktree_reclaimed` is true — `cleanup_state` is `done` or
`branch_retained` — MUST NOT be reported, and MUST NOT cause its clone to be listed.

**M3.** Any other item whose path git lists as prunable, or which is not a directory, is reported
as `prunable_worktree`. `retained`, `skipped` and `NULL` do not account for a missing directory.

**M4.** The note for a `done` item names `worktree remove <id>` and `cleanup <id>`; for an
`abandoned` item, `worktree remove <id>` only; for an unfinished item it is unchanged.

## The record

**M5.** When `worktree_remove` succeeds — `_remove_checkout` returned true — on an item in
`TERMINAL_WORK_ITEM_STATES`, it writes `cleanup_state`, `cleanup_reason` and `cleaned_at` in one
transaction and leaves `worktree_path` and `branch` unchanged.

**M6.** The state is `branch_retained` when the item has a branch that neither was deleted nor was
already gone; `done` otherwise.

**M7.** The reason is composed as in [data-model.md](../data-model.md) and ends
`— by \`robot-army worktree remove\``.

**M8.** On an item not in `TERMINAL_WORK_ITEM_STATES`, success clears `worktree_path` and writes no
cleanup column, as before.

**M9.** Every refusal — `already_removed`, `unresolved_repo`, `live_session`, `git`,
`directory_survived` — and every aborted or abandoned confirmation writes no column.

## Settling

**M10.** Whether the directory is absent is read **before** git is asked, and never when the
version-control boundary is simulated (which has no directory to be absent). When it was absent,
the worktree is treated as removed whether git succeeded (its record still present) or refused
("is not a working tree", record already pruned): `worktree_already_gone: true`, the line
`worktree … was already gone`, and the branch half proceeds. While the directory exists, git's
refusal stands (W7).

**M11.** Before deleting, the branch's existence is asked with `cleanup.branch_exists`. When it is
absent: no `git.delete_branch`, `branch_already_gone: true`, the line `branch … was already gone`,
and exit 0. An unanswerable question counts as present.

**M12.** When `cleanup_state` is `done` and the directory does not exist, `worktree_remove` refuses
with `refused_by: already_removed` and `EXIT_PRECONDITION`, naming the recorded `cleanup_state`,
`cleaned_at` and reason, before resolving the repository or consulting sessions. No git call is
made. `branch_retained` is **not** refused: its branch is still there, and a re-run — with
`--force` where `-d` refuses an unmerged branch — finishes that half and records `done`. For an
abandoned item nothing else can (amended in review of PR #178).

## The `worktree.remove` outcome

**M13.** Gains `cleanup_state` — the value written, present only when M5 wrote one.

**M14.** Gains `worktree_already_gone` and `branch_already_gone`, present (and `true`) only when
M10 or M11 applied.

**M15.** `refused_by` gains `already_removed`, with `reason` the sentence shown.

**M16.** The same keys appear in `result.data`, so `--json` carries them.
