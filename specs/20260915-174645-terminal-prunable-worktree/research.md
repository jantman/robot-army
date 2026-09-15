# Research: A hand-deleted worktree on a finished item is reported

All findings are read out of the tree or measured; there are no external dependencies to research.

## R1 — `worktree remove` forgets the path; it has since milestone 001

**Finding**: `operations.worktree_remove` ends with
`db.update_work_item_columns(ctx.conn, item_id, worktree_path=None)` (`operations.py:2826`). It was
there in `cf6a4e0` (milestone 001) and was carried unchanged through #59's refactor (`c1dde63`). The
issue says the command "does not clear `worktree_path`"; that part is out of date. The rest holds:
it writes no cleanup record.

**Consequence**: the row after a manual removal *is* distinguishable from a hand-deletion today —
by a missing path — but at the cost of the path itself, which `docs/guide/state.md` and
`5-outcome.md` both promise is kept "even after a successful removal". That promise is true only of
cleanup.

**Decision**: for a finished item, keep the path and write the cleanup record, so manual and
automatic removal leave the same row. For an unfinished item, keep clearing it (R3).

## R2 — No new cleanup state

**Decision**: a manual removal records `done` (branch deleted, already gone, or none) or
`branch_retained` (branch survived). The reason names `worktree remove`.

**Rationale**: the column's table describes what is on disk — worktree removed / kept, branch
removed / kept — and a manual removal lands in exactly those cells. Who decided is what the reason
is for. A new value (`removed`) would have to be taught to `cleanup_pending`,
`list_cleanup_candidates`, the web cell (which renders anything other than `done` as an error) and
the sweep, for no decision that depends on it.

**Alternatives**: a `removed_by` column — rejected, one reader and no decision.

## R3 — Record only for finished items

**Finding**: `WORK_ITEM_TRANSITIONS` has no exits from `done` or `abandoned`
(`TERMINAL_WORK_ITEM_STATES`, `states.py:84`). `retry` applies only to `failed` items
(`operations.py:3772`) and prepares a fresh worktree, writing `worktree_path` again. Nothing on that
path touches the cleanup columns.

**Consequence**: a cleanup record on a `failed` item whose worktree was removed by hand would
survive a retry. The fresh worktree would then read as already reclaimed: the sweep would never
report it missing, and `list_cleanup_candidates` (`cleanup_state IS NULL OR 'skipped'`) would never
offer it to cleanup once the item finished. A silent, permanent opt-out.

**Decision**: write the record only when the item is in `TERMINAL_WORK_ITEM_STATES`, where it can
never go stale. An unfinished item keeps today's behaviour — path cleared, no record — which the
sweep already handles (no path, nothing to report).

**Alternatives**: record for every state and reset the cleanup columns wherever a worktree is
recorded — rejected; seven write sites in `dispatch.py` for a case that the split avoids.

## R4 — One predicate for "the record accounts for it"

**Decision**: `WorkItem.worktree_reclaimed` → `cleanup_state in ("done", "branch_retained")`,
beside `cleanup_pending`, spelled with the same literals that property uses. The sweep skips items
for which it is true, **whatever their state**; `worktree remove` refuses on it (R8).

`retained` and `skipped` mean the worktree was kept, so a missing directory is unexplained and
reported.

## R5 — Cleanup runs before the sweep

**Finding**: `reconcile.reconcile` calls `_cleanup_worktrees` at `reconcile.py:701` (when
`on_issue_close`) and `_sweep_worktrees` at `:801`. With cleanup on, a hand-deleted `done` item is
reclaimed — cleanup already treats an absent directory as recoverable (`cleanup.py:265-271`) — and
recorded before the sweep looks, so it is not reported. The spec's assumption holds.

## R6 — Git's answer for an absent directory, measured

Measured on git 2.55.0 in a scratch repository: a worktree added, its directory `rm -rf`'d.

| Git's record | `git worktree remove <path>` |
|---|---|
| still present (`prunable`) | exit 0, record cleared |
| already pruned | exit 128, `fatal: '<path>' is not a working tree` |

**Decision**: in `_remove_checkout`, read whether the directory is absent **before** asking git.
When it was, the worktree is treated as gone (`worktree_already_gone`) whichever way git answers,
and the branch half proceeds. This is the rule cleanup has had since milestone 004
(`cleanup.py:253-271`). It is read beforehand because afterwards an absent directory is also what
success looks like, and the record should say "was already gone" rather than "worktree removed"
when git only cleared its own record.

The rule is placed in the shared helper, so the other callers inherit it: the path form cannot
reach it (it refuses a non-directory first), and `purge-simulated` stops reporting a refusal for a
row whose directory was already deleted. Both are the same fact being told the same way.

`worktree_exists` rather than `Path.is_dir()`: at below-`local` levels the simulated boundary
answers every removal with success, so this branch is unreachable there either way.

## R7 — An absent branch is not a survivor

**Finding**: `_remove_checkout` calls `delete_branch` and, on `False`, prints `WARNING: removed the
worktree but branch … still exists`. When the branch was already deleted — the second interruption
point, or a hand clean-up — that warning is false, and the exit code is 1.

**Decision**: ask `cleanup.branch_exists` (today `_branch_exists`, made public for its second
caller) before deleting. Absent → "branch … was already gone", `branch_already_gone: true`,
recorded as `done`. It answers `True` when it cannot tell, so an unanswerable question still
attempts the delete, as today.

## R8 — A recorded removal is refused, not repeated

**Decision**: when `cleanup_state` is `done` and the directory is absent, `worktree remove <id>`
refuses with `refused_by: already_removed`, `EXIT_PRECONDITION`, naming the recorded decision and
its time, before the repository is resolved or any session consulted. Without it, re-running on a
cleaned item reaches git for nothing and — before R7 — printed a false warning.

**Not `branch_retained`** (amended in review of PR #178). The first draft refused on
`worktree_reclaimed`, which includes it. But `branch_retained` is what the first run leaves when
`-d` refuses an unmerged branch — the normal outcome for abandoned work — and a forced re-run is
how that branch is deleted. Refusing it orphaned an abandoned item's branch outright: `cleanup`
considers `done` items only, and `worktree remove <path>` refuses a claimed path.

A directory that is present despite the record proceeds normally: the record is then wrong, and
removing is the command's job.

## R9 — Rehearsal items

**Finding**: at `plan` a simulated item has a recorded path and no directory, and the sweep's test
is a real `Path.is_dir()`. Such items are already reported while unfinished, as rehearsed
(`dry_run`) anomalies, which `list_anomalies` withholds by default (`db.py:896`). The anomaly is
never resolved automatically, so an item observed by any pass while unfinished already carries one.

**Decision**: no special case. Finished rehearsal items are reported the same way; the unique
index absorbs the repeat for any item already reported, and `purge-simulated` removes both.

## R10 — The note names the settling command

**Decision**: the anomaly's `note` for a finished item names `robot-army worktree remove <id>`,
and for a `done` item also `robot-army cleanup <id>` (cleanup handles an absent directory already,
under its containment guard). An `abandoned` item gets only the first: `cleanup.eligible` refuses
anything not `done`. Unfinished items keep today's note.

**Why it matters**: the open-anomaly index is partial on `acknowledged_at IS NULL`, so an
acknowledged report for a finished item is raised again on the next pass for as long as the row is
unchanged. Acknowledging is not a way to settle it; the command in the note is.

## R11 — No resolver

The `prunable_worktree` kind does not resolve itself today
(`test_anomaly_resolution.py:233` asserts it). Settling an item stops new reports; an open one is
acknowledged as today. A resolver is a separate change with its own settling story.
