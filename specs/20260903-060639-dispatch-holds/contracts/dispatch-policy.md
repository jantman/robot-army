# Contract: dispatch policy

Extends milestone 047's `contracts/dispatch-policy.md` (wait-for-merge), which extends
milestone 004's. Everything not restated here is unchanged.

## The gate

For a work item `I` in repository `R`:

> `I` is held with reason `held` if and only if a row exists in `item_holds` for `I`, **or** a
> row exists in `repo_holds` for `R`, **or** both.

- **Presence is the whole fact.** A hold has no level, no expiry, and no note. There is no
  state in which a hold exists but does not apply.
- **The repository half needs no per-item bookkeeping.** The hold is a fact about `R`, so an
  item discovered after the hold was placed is held on arrival (FR-012). Nothing backfills and
  no event is hooked.
- **Simulated rows are held like any other.** `item_holds` keys on `work_items(id)`, which does
  not distinguish `dry_run`. A dry-run item occupies a queue slot today, so it can be held
  today; a hold that ignored simulated work would rehearse the wrong behaviour. No outward
  request is made either way.
- **Scope is exactly what was named.** An item hold holds one item. A repository hold holds one
  repository's items and has no effect on any item outside `R`, in the same pass or any other
  (FR-011).
- **Nothing releases a hold but the author** (FR-026), except the cascade that removes it with
  the row it holds (FR-025).

When neither row exists, this clause does not apply and dispatch behaves exactly as it does
today.

## What the gate does not do

- It does **not** stop, cancel, or signal a session that is already running (FR-010). `cancel`
  is what stops a session, and the surfaces say so rather than letting a hold be mistaken for
  one.
- It does **not** retract an item whose dispatch is already under way. An item leaves `ready`
  the moment `dispatch_item` starts, so a hold placed after that point applies to the item's
  *next* eligibility, not to the attempt in flight.
- It does **not** change any item's state. Held items accumulate in `ready`, exactly as they do
  under a pause.

## Precedence

`HoldReason`'s declaration order is the precedence and gains one member:

```
paused > held > capacity_unobservable > global_cap > repo_cap > awaiting_merge
       > not_onboarded > off_column > preparation_failed
```

Exactly one reason is reported per item (FR-015). `held` sits directly below `paused` because
both are statements the author made deliberately and neither is changed by anything the queue
could do: freeing a slot, merging a pull request, re-onboarding a clone, moving a card, or
clearing stale failure residue all leave a held item exactly where it is. Reporting any of them
would name a fix that cannot work.

It sits **above** `capacity_unobservable`, which otherwise outranks everything below it. That
reason's justification is that the cap *numbers* are untrustworthy when capacity cannot be
observed, and showing an untrustworthy number is worse than showing none. `held` is not a
number and is not derived from the observation — a held item is held whether or not `/proc`
could be read — so the justification does not reach it.

It sits **below** `paused` because a paused system dispatches nothing at all, and naming one
item's hold would understate what is stopping the queue.

## Reporting when both holds apply

When `I` has an item hold **and** `R` has a repository hold, exactly one reason is still
reported — `held` — and its detail names **both**, stating that releasing one leaves the other
in force (FR-017).

This is not decoration. Collapsing to one reason without naming both produces the failure the
requirement exists to prevent: the author releases the item hold, expects the item to run, and
it does not, with the surface still saying `held` and appearing to have ignored the release.

The three detail shapes:

| Holds in force | Detail |
|---|---|
| item only | `held since <local time> by <cli\|web>` |
| repository only | `repository <key> is held since <local time> by <cli\|web>` |
| both | both clauses, plus an explicit statement that releasing one leaves the other in force |

## Selection

`held` is **not** a global hold. `dispatch._GLOBAL_HOLDS` remains
`{paused, capacity_unobservable, global_cap}` and `dispatch.py` is not modified by this feature.

A held entry is skipped (`continue`), so the pass goes on to consider every other repository's
work — which is FR-011, and which is the difference between solving the issue's scenario and
reproducing it. Four held items at the head of the queue must not stop the fifth from
dispatching.

The existing per-item hold logging applies unchanged: `select_and_dispatch` remembers the first
per-item hold it saw, and a pass that dispatches nothing records it through `_note_hold`, under
the same signature-change summarisation that already governs every other hold reason.

## Purity

`ordering.plan` remains pure: no writes, no network, no filesystem. The gate adds exactly two
reads — `db.list_item_holds` and `db.list_repo_holds` — taken **once for the whole plan** and
passed into `_hold_for`, in the same position and for the same reason as `resolved`,
`unfinished`, and `boards`.

This matters more than it looks. `plan` runs on every dispatch tick *and* on every web page
render, and it is the single producer of dispatch order for both the dispatcher and the
surfaces. A per-item query would multiply by the queue length on every page load; a write, a
network call, or a cached copy would break the identity that makes SC-003 — the item the queue
names as next is the item the next pass selects — structural rather than a claim maintained by
hand.

## Ordering

Unchanged, completely. `order_key` is not modified, `_apply_board_order` is not modified, and
nothing about position is persisted (FR-027). A released item returns to exactly the position
the existing ordering gives it, because the ordering never knew it was held — `plan` sorts
first and assigns hold reasons second, so holding an item cannot move it and releasing it
cannot either.

Positions stay contiguous and total across every item in the plan, held or not (FR-014).
Holding an item does not renumber the items around it.
