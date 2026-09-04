# Research: One dispatch gate on every launch path

Ten decisions. Each names what was chosen, why, and what it displaced. Two of them —
[R9](#r9) and [R10](#r10) — record costs and residual gaps rather than choices, because a
plan that only lists what it fixed is not honest about what it leaves.

## R1 — The gate is one function in `ordering`, called by both the queue and the launch

**Decision.** Extract the first five branches of `ordering._hold_for` into a public
`ordering.launch_holds(...)`. `_hold_for` calls it and then continues with the queue-only
reasons. `dispatch` calls it too, through a thin wrapper that does the I/O `ordering` will
not do.

**Rationale.** FR-007 and FR-008 require the launch and the queue to use one precedence and
one vocabulary. There are exactly two ways to get that: share the code, or maintain two
copies and a test that compares them. `ordering.py`'s own docstring already settles which
this project prefers — the queue view and the dispatcher do not *agree* on an order, they
*are* the same function, and the module says so in as many words. Extending that to the
launch is applying the existing rule to one more caller, not inventing a policy.

The split of labour follows the module boundary that already exists. `ordering` is pure: it
reads the database and the configuration and writes nothing, which is what lets the web call
it on every page render. So `launch_holds` takes the pause flag, the two hold dictionaries
and a capacity snapshot as arguments, exactly as `_hold_for` does today, and the *reading* of
those — `capacity.snapshot`, `db.get_dispatch_control`, `db.list_item_holds`,
`db.list_repo_holds` — happens in `dispatch`, which is already impure. `ordering` stays pure
and gains no new import.

**Alternatives considered.**

- *Re-implement the five checks inside `dispatch_item`.* Rejected. Two copies of a
  precedence rule is two copies, and the failure mode is silent: the queue says one thing,
  the button says another, and nothing detects it until the author is confused at midnight.
- *Move the whole gate into `dispatch` and have `ordering` call it.* Rejected. It inverts a
  dependency the codebase states in the other direction — `ordering` imports `capacity`,
  never the reverse; `dispatch` imports `ordering`, never the reverse — and it would make
  the pure module depend on the one that launches processes.
- *A new `gate.py`.* Rejected under Principle I. It would hold one function, and that
  function's whole purpose is to be the *same* rule the queue uses. A separate home is where
  a second rule eventually grows.

## R2 — `launch_holds` returns every applicable reason, in precedence order

**Decision.** It returns `list[tuple[HoldReason, str]]` — all conditions that apply, ordered
by `HoldReason`'s declaration order. The first element is what any surface reports. The whole
list is what an override records.

**Rationale.** FR-023 requires the override record to name every condition it went past, not
only the first, and FR-007 requires reporting to name only the first. Those are two views of
one ordered fact. Returning the list gives both from one evaluation, with the precedence
written down exactly once.

The cost is evaluating five conditions where one would have short-circuited. All five are
comparisons over data the caller has already loaded — two dictionary lookups, three integer
comparisons — and `_hold_for` already reads all of that whether or not the first branch
fires. Measured in work, this is free.

**Alternatives considered.**

- *Keep `_hold_for` returning the first, and add a second function for the override that
  collects all.* Rejected: two functions that must stay in the same order is the duplication
  R1 exists to avoid, in miniature.
- *Record only the first condition on an override.* Rejected by FR-023, and the requirement
  is right: the author who forces past a pause needs to know they also forced past a hold,
  or they will be surprised by what runs.

## R3 — Refusal is a new exception, a sibling of `DispatchBlocked` and not a subclass

**Decision.** `dispatch.DispatchRefused(Exception)`, carrying the `HoldReason` and the
detail. It does **not** inherit from `DispatchBlocked`.

**Rationale.** `DispatchBlocked` has an established meaning at every one of its catch sites:
the item cannot run and is *failed* for it. `_dispatch_item` catches it and calls `_fail(...,
blocked=True)`; `operations.retry` catches it and refuses the retry. A refusal for a pause,
a hold, or a full machine says nothing about the item, and FR-010 and FR-011 forbid touching
it. Subclassing would make the new exception silently eligible for handlers written to fail
items, and the bug would appear as "the machine was busy, so my work item is now failed" —
a worse outcome than the one being fixed.

**Alternatives considered.**

- *Subclass `DispatchBlocked` and audit every catch site.* Rejected: correctness would then
  depend on nobody adding a broad `except DispatchBlocked` later.
- *Return a value instead of raising.* Rejected. `dispatch_item` returns `bool`, and `False`
  already means "attempted and failed, look at the item's failure reason". A refusal has no
  failure reason to look at, by FR-011, so `False` would send the author to an empty field.
  FR-014 needs the reason to travel to the caller, and an exception is how this codebase
  already moves a reason out of a launch.

## R4 — The refusal is recorded at the gate, and the wrapper re-raises it untouched

**Decision.** The gate writes one `dispatch.refused` record and raises. `dispatch_item`'s
outer handler grows an `except DispatchRefused: raise` clause ahead of its generic one.

**Rationale.** `dispatch_item`'s generic handler exists to catch *unforeseen* failures: it
files a `dispatch.error` record, settles an item stranded in `dispatching`, and re-raises. A
refusal is neither unforeseen nor an error of the dispatch, and it happens before the claim,
so there is nothing to settle. Letting it fall through would put `outcome="error"` in the log
for the system working exactly as designed — and Principle III's standard is reconstruction,
which a misfiled record actively defeats.

**Alternatives considered.**

- *Let the generic handler record it.* Rejected as above.
- *Record nothing and let the caller log it.* Rejected by FR-013: there are three callers,
  and one of them would eventually not log.

## R5 — The gate runs before the claim, and before anything else in `_dispatch_item`

**Decision.** Order inside `_dispatch_item`: load the item, resolve the repository, **gate**,
author check, claim, `check_gates`, worktree, launch.

**Rationale.** FR-010 and FR-011 require a refused launch to change nothing, and the only way
to guarantee that is to refuse before the first write. The repository resolution stays ahead
of the gate because the gate needs `item.repo_key` to be meaningful and because the
unresolvable-repository case already fails the item today — changing that would be scope this
feature did not ask for.

The author check keeps its position relative to the gate deliberately: it is a security
boundary that fails the item, the gate is a policy that does not, and a held item on a paused
machine written by somebody else should be *refused*, not failed. Refusing first is also the
cheaper order and leaks less: an attempt the author never authorised does not get to write a
`blocked_reason` while the system is paused.

## R6 — A new `states.claim_work_item`; `transition_work_item` is not touched

**Decision.** Add one function to `states.py`:

```
claim_work_item(conn, audit, *, item_id, target, reason, extra_columns=None)
```

It performs a single `UPDATE ... WHERE id = ? AND state IN (<legal sources for target>)`,
raises `ClaimLost` when no row was updated, and writes the same `state.work_item` record
`transition_work_item` writes, inside the caller's transaction. `_dispatch_item`'s one call
to `transition_work_item(..., DISPATCHING)` becomes a call to this.

**Rationale.** FR-020 requires re-asserting a held state to stay a legitimate no-op for
reconciliation and spool replay. Not editing `transition_work_item` makes that true by
construction rather than by a test that guards a behaviour someone might optimise away.

The legal source states are **derived** from `WORK_ITEM_TRANSITIONS`, not written out:

```
{source for (source, t) in WORK_ITEM_TRANSITIONS if t is target}
```

The issue's suggested fix hard-codes `('ready','interrupted','awaiting_review')`, which is
today's correct answer written down a second time. Deriving it means the legal-transitions
table stays the single place the machine is defined — which is that module's stated purpose —
and an item already in `dispatching` is excluded automatically, because
`DISPATCHING → DISPATCHING` is not in the table. That is exactly FR-018.

`rowcount` distinguishes won from lost but not *why* it was lost, so the losing path re-reads
the row once to say whether the item is gone or merely in another state. One extra read, on
the rare path only.

**Alternatives considered.**

- *Make `transition_work_item` itself conditional.* Rejected: it would break the no-op
  re-assertion that reconcile and spool replay depend on, which is the thing FR-020 protects.
- *Take a lock around read-then-write.* Rejected under Principle I: SQLite gives a
  conditional `UPDATE` atomically already, and a lock is a second mechanism for the same
  guarantee.
- *Add a `claimed_by` column.* Rejected: no reader would want it. The state column already
  says who won, and Principle IV prefers one atomic write to two.

## R7 — `--force` on the terminal; no override in the web

**Decision.** `robot-army resume <id> --force` and `robot-army restart <id> --force` thread a
`force: bool = False` through `operations` into `dispatch_item`. The web has no equivalent.

**Rationale.** The web already offers the better escape hatch for every ordinary case, and
offers it one press away: `POST /dispatch/unpause`, `POST /item/<id>/unhold`, and
`POST /repos/unhold` all exist. Lifting the condition is a *truer* action than overriding it
— it leaves the system in a state that matches what the author actually decided, and the
queue then agrees with the button. An override button would add a confirmation page, a route,
and a form to say something worse.

The one case the web cannot express is "the machine is full and I want this anyway", which is
also the case where the author most benefits from being at a keyboard, and where the
constitution's Operating Constraints already put the answer: every capability is reachable
from the terminal.

**Alternatives considered.**

- *A force checkbox on the web confirmation page.* Rejected as above, and under Principle I.
- *A configuration setting that disables the gate.* Rejected outright. A standing bypass is
  the bug this feature exists to remove; FR-022 forbids it.
- *A different flag name, e.g. `--override`.* Considered seriously, because `robot-army
  cancel --force` already means "skip the confirmation prompt". Rejected: the issue names
  `--force`, and one word doing two jobs in two commands, each with help text that says
  which, was judged less confusing than two words for one concept across the CLI.

## R8 — The web checks twice: once for the author, once for the truth

**Decision.** Add `require_dispatchable(ctx, item_id, action)` to `server.py`'s existing guard
family, called inside `_slow_item_action`'s body alongside `require_daemon`,
`require_effect_agreement` and `require_legal`. The gate inside `dispatch_item` still runs on
the worker.

**Rationale.** The web answers resume and restart immediately and does the slow work on a
single background worker — a shape the server's docstring defends and this feature does not
disturb. That shape means a refusal discovered on the worker reaches the author only through
the log, while the page shows an item that simply did not change. FR-015 exists because that
is indistinguishable from nothing having happened.

Checking in the request thread costs one capacity snapshot per button press and gives the
author the reason on the page they are looking at, through the `Refusal` machinery that
`require_daemon` and `require_legal` already use — including its audit pairing, so a refused
POST still leaves the record that says one arrived. The check on the worker stays, because
it is the only one that is authoritative: minutes can pass between the two.

## R9 — Cost accepted: two capacity snapshots per dispatched item

**Cost.** `select_and_dispatch` snapshots once per candidate to build the plan (an existing
requirement — a stale snapshot is how a batch collectively exceeds a cap), and
`dispatch_item` now snapshots again to gate the launch. A dispatched item therefore costs two
observations instead of one.

**Why it is accepted.** A snapshot is a directory listing, a handful of `/proc` reads, and one
indexed query. It is paid only when an item is actually dispatched — a full or paused machine
never reaches `dispatch_item` at all, because the plan stops the pass first — so the added
cost lands on the rare path, not on the five-second tick.

**Why the obvious saving was refused.** Passing the planner's snapshot into `dispatch_item`
would remove the second observation and violate FR-009. It would also be wrong in the one case
that matters: between the plan and the launch, the author can start a session by hand, and a
remembered count cannot see it. The whole point of `capacity` is that the count is of the
machine rather than of our own bookkeeping.

**Consequence to handle.** Because the second observation is real, it can legitimately
disagree with the first. `select_and_dispatch` therefore catches `DispatchRefused` rather
than letting it escape the daemon tick — and splits on it exactly as the loop above already
splits `ordering.plan`'s own holds, by asking whether the reason is in `_GLOBAL_HOLDS`:

- **Global** (`paused`, `capacity_unobservable`, `global_cap`) ends the pass. No later item
  could fit into a slot this one could not.
- **Per-item** (`held`, `repo_cap`, and a lost claim, which carries no `HoldReason` at all)
  skips the item and leaves the queue moving. `attempted` already holds the id, so
  re-planning cannot offer it again and the loop cannot spin.

An earlier draft returned on *any* refusal, which was wrong in the way the module's own
docstring already warns about: a lost claim says another process took **this** item, and
nothing whatever about the next candidate. Reusing `_GLOBAL_HOLDS` rather than making a
second judgement about which reasons are global is what keeps the two places in agreement.

## R10 — Residual: two concurrent launches of *different* items can still overshoot

**The gap.** The gate observes, then the launch proceeds; a session becomes countable only
once its row exists. Two processes launching two *different* items at the same instant can
each observe the same free slot and each take it, ending at one session over the cap. The
atomic claim does not help — it makes exactly one dispatcher win *one item*, which is a
different question.

**Why it is not fixed here.** Closing it needs a cross-process lock held across the
observation and the launch, or counting `dispatching` items as occupying slots. The first is a
new concurrency mechanism, which Principle I asks to be justified against a demonstrated need
rather than an anticipated one. The second changes what the *queue* reports as well as what
the launch permits, which SC-006 forbids this feature from doing.

**What it is bounded by.** It requires two launches in flight within the same few seconds,
from two different surfaces, for two different items, on a one-person machine. The overshoot
is one session, not unbounded: the third attempt sees both.

**When to revisit.** If the author ever observes a real overshoot, or if concurrent dispatch
from more than one process becomes ordinary rather than incidental, the cheap first move is
counting items in `dispatching` toward the total — `capacity`'s stated invariant is that the
count never errs downward, so an over-count in that direction is already the safe one.

**Recorded rather than silent.** This is named here, in the plan's Constitution Check, and in
`docs/security-analysis.md`'s RA-05 entry, because a fix that quietly leaves a narrower
version of the same hole is how a finding gets closed twice.
