# Contract: Manual Worktree Removal

**Feature**: `specs/20260901-164616-guard-worktree-remove`
**Governs**: `operations.worktree_remove`, `cleanup.live_sessions`, and the `worktree.remove`
action record. Supplements — does not replace —
[`specs/004-concurrency-polish/contracts/cleanup.md`](../../004-concurrency-polish/contracts/cleanup.md),
which governs the *automatic* path and is unchanged by this feature.

## The guard

**W1.** A work item has a **live session** when any row in `sessions` for that item is in
`cleanup.LIVE_SESSION_STATES` — currently `starting` or `running`. Every row is considered, not the
latest attempt only.

**W2.** The guard MUST NOT read the work item's state. A `done` or `abandoned` item with a live
session refuses exactly as an `active` one does. (This is the reported case, not an edge case.)

**W3.** The guard MUST NOT read the process table to decide. Process liveness is reported to the
operator and recorded; it never changes the decision. An open row with a process that cannot be
found still refuses.

**W4.** `cleanup.eligible` and `operations.worktree_remove` MUST obtain their answer from the same
function. Neither restates the state set.

**W5.** `cleanup.eligible`'s returned reason string is unchanged, byte for byte. `cleanup.clean_item`
routes on a substring of it, and the routing decides whether the item is reconsidered later.

## Refusal

**W6.** With a live session and no override, `worktree_remove` MUST return before any of: a call to
`remove_worktree`, a call to `delete_branch`, a confirmation prompt, or a write to `work_items`.

**W7.** The refusal exits `EXIT_PRECONDITION` (3). Git's dirty-tree refusal keeps `EXIT_FAILED` (1).
The two are distinguishable by exit status alone.

**W8.** The refusal message names, in this order: the worktree path; the session id, its attempt
and its state; the liveness answer; what removing it now would do; how to look at it; and the two
ways forward.

**W9.** The liveness answer is exactly one of four words, chosen from the record alone:

| Recorded | Answer | Rendered |
|---|---|---|
| `pid` and `proc_start`, process matches | `running` | `pid N is running` |
| `pid` and `proc_start`, no match | `gone` | `pid N is no longer there` |
| `pid`, no `proc_start` | `unidentified` | `pid N recorded, with no start time to identify it by` |
| no `pid` | `unrecorded` | `no process id recorded` |

**W10.** `procinfo.is_alive` MUST NOT be called with `proc_start` of `None`. Its documented
degradation to a bare existence check would report an unrelated process holding a recycled pid as
this session, and rows legitimately carry a pid with no start time.

**W11.** The reattach line is emitted only when `host_socket` is recorded, and is character-for-
character the line `show` prints: `dtach -a <socket>`.

**W12.** A refusal leaves `worktree_path`, `branch`, the work item state, `cleanup_state` and every
session row exactly as they were.

## Override

**W13.** `--force` may proceed over a live session. Without this, a session row nothing will ever
close makes a worktree permanently unremovable.

**W14.** The override requires the confirmation the override already requires: the typed item id.
No second prompt is added, and the flag alone never suffices.

**W15.** When a live session is present, the prompt names it before any input is read. When none is
present, the prompt is unchanged from today, word for word.

**W16.** After a satisfied confirmation, removal proceeds exactly as it does today — same git
calls, same branch deletion, same warning when the branch survives, same `worktree_path` clear.

## The `worktree.remove` record

**W17.** The whole operation is wrapped in an `intent`/`outcome` pair named `worktree.remove`, with
`entity_type="work_item"`, `entity_id=<item id>`, `target=<worktree path>`, and intent detail
`{"force": <bool>}`. The intent is flushed before anything is removed.

**W18.** The outcome detail carries:

| Key | When | Meaning |
|---|---|---|
| `refused` | always | `true` when this command refused |
| `refused_by` | on a refusal | `"live_session"` or `"git"` |
| `reason` | on a refusal | the sentence shown to the operator |
| `live_session` | whenever one was found | session id, attempt, state, pid, liveness |
| `forced_over_live_session` | always | `true` only when the override went ahead over W1 |
| `worktree_removed`, `branch_deleted` | always | what actually happened |

**W19.** A refusal is `outcome: "ok"`. The command was asked a question and answered it; `error` is
reserved for a boundary that broke.

**W20.** A forced removal over a live session MUST be distinguishable in the record from a forced
removal over a dirty tree. `force: true` alone does not distinguish them;
`forced_over_live_session` does.

**W21.** No record this feature adds is omitted under Principle III's exception path. There is no
claimed gap.

## Non-goals, stated so they are not read in

**W22.** The automatic cleanup path's behaviour is unchanged: same guard, same `skipped` outcome,
same reconsideration.

**W23.** Nothing here stops, signals, or closes a session. Reclaiming disk is not consent to end a
running job.

**W24.** `worktree list` is not changed. The refusal prevents the harm; advertising it in the
listing is a convenience with no second caller.

**W25.** `worktree prune` is not changed. It clears git's record of directories that are already
gone and touches no live worker.
