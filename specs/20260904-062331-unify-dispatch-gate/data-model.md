# Data Model: One dispatch gate on every launch path

**No schema change. No migration. No new table, column, index or file.**

That is the headline, and it is not luck. Every fact the gate needs is already stored, and the
three shapes this feature introduces are all runtime values that exist for the length of one
call.

## What is read, and from where

| Fact | Source | Already read by |
|---|---|---|
| Is dispatch paused? | `dispatch_control.paused`, via `db.get_dispatch_control` | `ordering.plan` |
| Is this item held? | `item_holds`, via `db.list_item_holds` | `ordering.plan` |
| Is this repository held? | `repo_holds`, via `db.list_repo_holds` | `ordering.plan` |
| How full is the machine? | `capacity.snapshot` — the session registry, `/proc`, and `sessions` rows | `ordering.plan`'s caller |
| The machine-wide limit | `config.daemon.max_concurrent_sessions` | `capacity.snapshot` |
| This repository's limit | `config.effective_repo_cap(repo_key)` | `ordering.repo_capacity` |
| Which states may claim? | `states.WORK_ITEM_TRANSITIONS` | `states.transition_work_item` |

Every row in that table has an existing reader. The feature adds one more caller to each, not
one more source.

## What is written

| Write | When | Shape |
|---|---|---|
| `work_items.state` → `dispatching`, plus `dispatching_at` and `updated_at` | on a won claim | one `UPDATE`, unchanged in effect from what `transition_work_item` writes today |
| `state.work_item` audit record | with the claim, same transaction | unchanged in shape |
| `dispatch.refused` audit record | on every refusal | new action name; no database row |
| `dispatch.forced` audit record | on every overridden launch | new action name; no database row |

**A refusal writes nothing to the database.** Not a state, not a `failure_reason`, not a
`blocked_reason`, not a timestamp. That is FR-010 and FR-011, and it is what makes FR-012 —
press the button again once the condition is lifted — true with no repair step in between.

## Runtime shapes

### 1. `LaunchHold` — one reason a launch may not proceed

Not a new class. It is the pair `ordering._hold_for` already returns, `tuple[HoldReason, str]`:
the reason and its human-readable specifics. `launch_holds` returns a list of them, ordered by
`HoldReason`'s declaration order, which *is* the precedence.

Only five of the nine members can appear from `launch_holds`:

| `HoldReason` | Rank | Applies when | Detail carries |
|---|---|---|---|
| `PAUSED` | 1 | `dispatch_control.paused` | the command that lifts it |
| `HELD` | 2 | an item hold or a repo hold, or both | when each was placed and by which surface; when both, that releasing one leaves the other |
| `CAPACITY_UNOBSERVABLE` | 3 | the snapshot could not count | why the count failed |
| `GLOBAL_CAP` | 4 | `total >= global_cap` | the counts, the limit, and whether the count is a `/proc` ceiling |
| `REPO_CAP` | 5 | this repository's running count is at its limit | the repository, its two numbers, and whether the limit was configured or inherited |

The remaining four — `AWAITING_MERGE`, `NOT_ONBOARDED`, `OFF_COLUMN`, `PREPARATION_FAILED` —
stay in `_hold_for` alone. They decide whether a **new** item enters the queue, not whether
work already begun may resume, and the spec's Assumptions record that scope line.

The ranks are inherited, not chosen. `HoldReason`'s declaration order already encodes them and
already carries, in its docstring, the reasoning for each rank. Nothing here re-decides it, and
that is the whole of FR-007: the button and the queue cannot disagree because they read one
enum.

**Empty list means permitted.** There is no `None`-versus-empty ambiguity to get wrong.

### 2. The gate decision

Computed by `dispatch.check_launch_gate` from a *fresh* snapshot (FR-009) plus the three
database reads above. It has exactly three outcomes:

| Outcome | Condition | Effect |
|---|---|---|
| Permitted | `launch_holds` returned empty | nothing recorded, launch proceeds |
| Refused | non-empty, `force` not set | `dispatch.refused` recorded with the **first** hold; `DispatchRefused` raised; nothing written |
| Overridden | non-empty, `force` set | `dispatch.forced` recorded with **every** hold (FR-023); launch proceeds |

Never stored. A stored copy would be a second source of truth that can disagree with the
machine, which is the reason `CapacitySnapshot` is not stored either.

### 3. The dispatch claim

The single indivisible act of taking an item for launch.

- **Statement**: one `UPDATE work_items SET state='dispatching', dispatching_at=?,
  updated_at=? WHERE id = ? AND state IN (<derived sources>)`.
- **Derived sources**: `{source for (source, target) in WORK_ITEM_TRANSITIONS if target is
  DISPATCHING}` — today `ready`, `interrupted`, `awaiting_review`. Written nowhere else, so the
  state machine keeps one definition, and `dispatching` is excluded automatically because
  `DISPATCHING → DISPATCHING` is not a legal transition (FR-018).
- **Won**: `rowcount == 1`. The `state.work_item` record is written in the same transaction
  (FR-019).
- **Lost**: `rowcount == 0` → `ClaimLost`, which the launch turns into `DispatchRefused`.
  The row is re-read once, on this path only, to say whether the item is gone or merely in
  another state. Nothing is written (FR-017).
- **Atomicity**: SQLite evaluates the `WHERE` and applies the `SET` as one statement in one
  transaction. Of any number of concurrent attempts, exactly one sees `rowcount == 1`
  (FR-016).

### What `transition_work_item` keeps

Nothing about it changes. It still treats `source == target` as a legitimate no-op, because
reconciliation and spool replay both re-derive a state an item already holds, and both are
right to. FR-020 is satisfied by not editing the function rather than by a test defending a
behaviour someone might later optimise away.

## State transitions

Unchanged. `WORK_ITEM_TRANSITIONS` is not edited: no pair is added and none removed. The claim
enforces a subset of what the table already permits — the same subset, expressed atomically.

```
ready ─────────┐
interrupted ───┼──► dispatching ──► active
awaiting_review┘        │
                        └────────► failed
```

The only behavioural difference is that exactly one claimant now traverses an arrow that two
could previously traverse at once.
