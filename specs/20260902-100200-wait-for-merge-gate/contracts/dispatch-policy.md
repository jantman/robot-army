# Contract: dispatch policy

Extends milestone 004's `contracts/dispatch-policy.md`. Everything not restated here is
unchanged.

## The gate

For a work item `I` in repository `R`, with `wait_for_merge` in force for `R`:

> `I` is held with reason `awaiting_merge` if and only if some **other** work item in `R` is
> in one of `{dispatching, active, awaiting_review, interrupted, failed}`.

- **Other**: an item never holds itself. In practice a candidate is always `ready`, which is
  not an unfinished state, so this is belt to braces rather than load-bearing.
- **Pre-dispatch states are excluded.** `discovered` and `ready` do not count as unfinished.
  Counting `ready` would make two queued issues hold each other forever.
- **Terminal states are excluded**, and that exclusion is the release: the gate opens when the
  unfinished item reaches `done` (its source issue closed — which is what merging a pull
  request that says *closes #N* does) or `abandoned` (the author said so).
- **Simulated rows count**, in both directions, for the reason `capacity.snapshot` counts
  them: a dry run must rehearse the real behaviour. No outward request is made either way.
- **Scope is one repository.** An unfinished item in `R` has no effect on any item outside
  `R`, in the same pass or any other (FR-007).

When `wait_for_merge` is not in force for `R`, this clause does not apply and dispatch behaves
exactly as it does today.

## Precedence

`HoldReason`'s declaration order is the precedence and gains one member:

```
paused > capacity_unobservable > global_cap > repo_cap > awaiting_merge
        > not_onboarded > preparation_failed
```

Exactly one reason is reported per item (FR-011). `awaiting_merge` sits below `repo_cap`
because the coarser limit binds first and a free session slot is the more immediate fact; it
sits above the two item conditions because it is a condition of the queue rather than of the
item.

## Selection

`awaiting_merge` is **not** a global hold. `dispatch._GLOBAL_HOLDS` remains
`{paused, capacity_unobservable, global_cap}`. A held item is skipped (`continue`); the pass
goes on to consider every other repository's work.

## Purity

`ordering.plan` remains pure: no writes, no network, no filesystem. The gate adds exactly one
database read per call — a single `list_work_items` scan over the unfinished states, resolved
once for the whole plan the way `repos.resolved_all` already is — and never one per item.

## What gets recorded

`select_and_dispatch`'s hold record widens from the three global holds to **any pass that
dispatched nothing while at least one candidate was held**:

| Record | When | Detail |
|---|---|---|
| `dispatch.at_capacity` | a pass ends with nothing dispatched and at least one held candidate, and the hold signature differs from the last recorded one | reason, detail, the head-held item, the live-session numbers |
| `dispatch.hold_ended` | an item becomes dispatchable, or the queue empties | reason, duration, passes spanned, what freed it |

The signature gains the held reason and the held item's repository, so *which* condition is
holding changing is news. De-duplication means a repository held for hours produces one record
and one `hold_ended` rather than one pair per five-second tick; that is the Principle III gap
plan.md enumerates and justifies.

`repo_cap`, which has never appeared in the log, is covered by the same widening.

## Unchanged

- The order (`oldest-first` / `repo-priority`) and its tie-breaks.
- `capacity.snapshot` and everything it counts.
- The per-repository session cap, its resolution, and its `repo_cap` hold.
- Every existing hold reason's meaning, detail text, and rank relative to the others.
