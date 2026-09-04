# Implementation Plan: One dispatch gate on every launch path

**Branch**: `speckit/20260904-062331-unify-dispatch-gate` | **Date**: 2026-09-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260904-062331-unify-dispatch-gate/spec.md`

## Summary

One function extracted, one function added, one exception added, one call replaced, one guard
added to the web, one flag added to two CLI verbs, and two documents corrected. No new module,
no new dependency, no new table, no migration, no configuration key, and no network call.

It is this small because both halves of the fix already exist, in the wrong places relative to
each other:

- **The policy exists** in `ordering._hold_for`, which computes nine hold reasons in a
  precedence its enum's declaration order defines. Five of those reasons are the three brakes
  the issue names. They are unreachable from the launch only because `_hold_for` is private and
  `dispatch_item` never asks. Extracting the first five branches as `ordering.launch_holds` and
  calling it from `dispatch` makes FR-007 and FR-008 structural — the queue and the button do
  not agree on a precedence, they run the same one ([R1](research.md#r1), [R2](research.md#r2)).
- **The state machine exists** in `states.WORK_ITEM_TRANSITIONS`, which already says that
  `dispatching` may be entered only from `ready`, `interrupted` and `awaiting_review` — and
  therefore not from `dispatching`. The double dispatch happens because
  `transition_work_item` reads the state, then writes it, and treats "already there" as a
  no-op. A new `states.claim_work_item` does the same move as one conditional `UPDATE`, with
  its legal sources *derived* from that table rather than written out a second time
  ([R6](research.md#r6)).

Three decisions carry the shape of the change:

- **A refusal is not a failure.** It runs before the first write and touches nothing —
  no state change, no `failure_reason`, no `blocked_reason`, no worktree, no comment. It
  travels as `DispatchRefused`, a **sibling** of `DispatchBlocked` rather than a subclass,
  precisely because every existing `except DispatchBlocked` fails the item
  ([R3](research.md#r3), [R5](research.md#r5)).
- **The override is terminal-only.** The web already offers unpause, unhold and repo-unhold,
  one press away; lifting the condition is truer than overriding it and leaves the queue
  agreeing with the button ([R7](research.md#r7)).
- **The web checks twice.** Once in the request thread so the author sees the refusal on the
  page they are looking at, and once at the launch because minutes can pass between the two
  and only the second is authoritative ([R8](research.md#r8)).

Two things this plan does **not** do, both recorded rather than quietly omitted: it does not
move the four queue-only hold reasons — waiting for a merge, off-column, unresolvable
repository, stale preparation failure — which are conditions on admitting a new item, not on
resuming work already begun; and it does not close the narrow window in which two concurrent
launches of *different* items each see the same free slot ([R10](research.md#r10)).

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`).

**Primary Dependencies**: none added and none reached. `httpx` remains the only runtime
dependency; nothing in this feature makes a network call.

**Storage**: the existing SQLite database, unchanged. **No migration.** Every column this
feature reads or writes — `work_items.state`, `dispatching_at`, `updated_at`, the
`dispatch_control` row, `item_holds`, `repo_holds` — exists today.

**Testing**: `pytest`. New unit tests in `tests/unit/`, new integration coverage in
`tests/integration/test_dispatch_capacity.py`. `uv run pytest`, `uv run ruff check`.

**Target Platform**: one Linux machine with a shell.

**Project Type**: single-process CLI daemon with a read-only-plus-controls web view.

**Performance Goals**: the gate is paid per *launch*, not per tick and not per page render.
`ordering.plan` gains nothing — `launch_holds` is the code it already ran, moved. The daemon
path costs one extra `capacity.snapshot` per dispatched item, which is a directory listing,
a few `/proc` reads and one indexed query, and is paid only when an item actually dispatches
([R9](research.md#r9)). The web pays one snapshot per resume or restart button press.

**Constraints**: `ordering` stays pure — no writes, no filesystem, no network, and no new
import; the reads the gate needs happen in `dispatch`, which is already impure.
`transition_work_item` is not modified, so reconciliation and spool replay keep their no-op
re-assertion (FR-020). `_GLOBAL_HOLDS` and `ordering.plan`'s sort are untouched, so SC-006 —
the dispatcher selects the same items in the same order — holds by construction.

**Scale/Scope**: one author, a handful of repositories, a queue of tens of items, a session
limit in single digits.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 — see the re-check at the end.*

### I. Simplicity First (YAGNI & KISS)

**Pass.** No new module, no new dependency, no new table, no new configuration key, no new
process, and no new concurrency primitive. The two new names — `ordering.launch_holds` and
`states.claim_work_item` — each sit in the module that already owns their subject.

Six abstractions were available and all six were declined, each with the condition that would
justify it recorded in research:

- **A `gate.py` module** for the shared decision. Declined ([R1](research.md#r1)): it would
  hold one function whose entire purpose is to be the same rule `ordering` already applies,
  and a separate home is where a second rule grows.
- **A policy engine or gate registry** — pluggable checks a launch path composes. Declined:
  there are five conditions and two callers. Machinery with one shape of use is the tax
  Principle I names.
- **A configuration key to disable the gate.** Declined ([R7](research.md#r7)) and forbidden
  by FR-022: a standing bypass is the bug being fixed.
- **A cross-process dispatch lock.** Declined ([R10](research.md#r10)): it would close a
  narrower residual window at the cost of a new concurrency mechanism, against an anticipated
  need rather than a demonstrated one.
- **A `claimed_by` column on `work_items`.** Declined ([R6](research.md#r6)): no reader wants
  it, the state column already says who won, and two writes are worse than one under
  Principle IV.
- **A hard-coded list of claimable states**, as the issue's snippet suggests. Declined
  ([R6](research.md#r6)): the legal sources are derived from `WORK_ITEM_TRANSITIONS`, keeping
  the state machine defined in exactly one place.

One duplication is *removed* rather than added: five hold branches that would otherwise have
been written twice are written once.

### II. Single-User, Local-First

**Pass.** No account, no role, no permission model — the override is a command-line flag typed
by the operating-system user, which is the trust boundary. All state stays in the existing
local SQLite database at its documented path. No hosted service, no network call, and no new
file. The feature works with the network unplugged.

### III. Total Accountability

**Pass.** What this logs:

| Event | Record | Contents |
|---|---|---|
| A launch refused by the gate, any surface | `dispatch.refused`, `outcome="error"` | item id, the `HoldReason`, its human-readable detail, and the calling surface |
| A launch that went past the gate on `--force` | `dispatch.forced`, `outcome="ok"` | item id and **every** condition overridden, not only the first (FR-023) |
| A launch that lost the claim | `dispatch.refused` with reason `claim_lost` | item id, the state found instead |
| The claim itself | the existing `state.work_item` pair | unchanged in shape; written inside the same transaction as the `UPDATE` (FR-019) |
| A refusal in the web request thread | the existing `web.<action>` pair, `outcome="error"` | already written before the guards run, so a refused POST cannot leave no record |

Silent failure is absent by construction: the gate either permits, or raises an exception that
carries the reason to a caller that prints it and exits non-zero. `dispatch_item`'s generic
handler grows an `except DispatchRefused: raise` clause specifically so a refusal is not
misfiled as `dispatch.error` — a misfiled record defeats reconstruction as surely as a missing
one ([R4](research.md#r4)).

**Gaps enumerated, as the constitution requires:**

1. **A repeated refusal is recorded every time.** The queue's own hold recorder de-duplicates
   (a hold is logged when it starts and when it ends, not once per five-second tick). Refusals
   here are **not** de-duplicated, and deliberately: each one is an action the author took, and
   a button press that leaves no record is the failure this feature exists to fix. The volume
   is bounded by how fast a person can press a button.
2. **The residual overshoot in [R10](research.md#r10) is not detected**, so it cannot be
   logged. Nothing observes "two launches each took the last slot"; the log will show two
   ordinary, permitted launches. Named here, in research, and in the security analysis rather
   than left to be rediscovered.

### IV. Interruption Tolerance

**Pass.** What happens if it is killed halfway:

- **Before the claim** — during the gate, or between the gate and the claim. Nothing has been
  written. The item is exactly as it was; the author repeats the action.
- **During the claim.** It is one `UPDATE` inside one transaction that also carries its audit
  record. It commits or it does not; there is no state in between, and no possibility of the
  change without the record or the record without the change.
- **After the claim, before the session is confirmed.** The item is left in `dispatching`,
  which is the condition the 15-minute reaper and reconciliation already own. This feature
  adds no new way to produce it and no new way to resolve it.
- **The web worker thread dies mid-action.** Unchanged from today, and `docs/state.md` already
  records the answer: reconciliation resolves it, the same path any interrupted dispatch takes.

No network call is added, so no timeout or retry bound is needed. Nothing is retried.

### V. Public Code, Unsupported Project

**Pass.** No credential, hostname or personal datum enters any new record: the refusal detail
is a repository key, two integers, and a timestamp already rendered on the queue page.
`dispatch_item` gains a keyword-only parameter with a default, and `operations.resume` /
`restart` likewise, so nothing outside the repository has to change — not because compatibility
is owed, but because the smaller diff is the better one. Documentation updates (FR-027, FR-028)
are written for the author's future self: what refuses a launch, what overrides it, and what
the override cannot reach.

## Project Structure

### Documentation (this feature)

```text
specs/20260904-062331-unify-dispatch-gate/
├── plan.md              # This file
├── research.md          # Phase 0 — ten decisions, two of them recorded costs
├── data-model.md        # Phase 1 — no schema change; the three runtime shapes
├── quickstart.md        # Phase 1 — how to see each requirement hold, by hand
├── contracts/
│   ├── dispatch-gate.md # The gate: inputs, precedence, what a refusal may not touch
│   ├── cli.md           # `resume` / `restart`, their flag, their exit codes
│   └── web.md           # The request-thread guard and its response
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
├── spec.md
└── tasks.md             # Phase 2 output — created by /speckit-tasks, not here
```

### Source Code (repository root)

```text
src/robot_army/
├── ordering.py     # + launch_holds() — the five shared branches, extracted from _hold_for
├── dispatch.py     # + DispatchRefused, + check_launch_gate(); dispatch_item gains force=
├── states.py       # + ClaimLost, + claim_work_item(); transition_work_item UNCHANGED
├── operations.py   # resume/restart gain force=; both render a refusal as EXIT_PRECONDITION
├── cli.py          # resume/restart gain --force
└── web/server.py   # + require_dispatchable(), called from _slow_item_action

tests/
├── unit/
│   ├── test_launch_gate.py        # new — the five conditions, precedence, force
│   ├── test_claim_work_item.py    # new — atomicity, derived sources, FR-020 untouched
│   ├── test_states.py             # extended — the no-op re-assertion still holds
│   ├── test_web_actions.py        # extended — the request-thread refusal
│   └── test_cli_exit_codes.py     # extended — --force plumbing, exit code 3
└── integration/
    └── test_dispatch_capacity.py  # extended — resume/restart under cap, pause, holds

docs/
└── security-analysis.md           # RA-05 marked resolved, with the residual named

README.md                          # resume/restart are subject to the gate; --force
```

**Structure Decision**: no new source file. Every change lands in the module that already owns
its subject — the precedence in `ordering`, the launch in `dispatch`, the state machine in
`states`, the verbs in `operations` and `cli`, the guard in `web/server`. The only new files
are tests and this feature's own documents.

## Implementation Sketch

Ordered by dependency; `/speckit-tasks` turns this into the task list.

1. **`ordering.launch_holds`** — extract the `paused`, `held`, `capacity_unobservable`,
   `global_cap` and `repo_cap` branches of `_hold_for` into a public function returning every
   applicable `(HoldReason, detail)` in declaration order. `_hold_for` calls it, takes the
   first if any, and otherwise continues into its four remaining branches. `ordering.plan`'s
   output must be byte-for-byte identical afterwards (SC-006).
2. **`states.claim_work_item` and `states.ClaimLost`** — one conditional `UPDATE` whose legal
   sources are derived from `WORK_ITEM_TRANSITIONS`, the same stamp columns, the same
   `state.work_item` record, inside the caller's transaction. `transition_work_item` is not
   edited.
3. **`dispatch.DispatchRefused` and `dispatch.check_launch_gate`** — the wrapper that reads
   what `ordering` will not: `capacity.snapshot`, `db.get_dispatch_control`,
   `db.list_item_holds`, `db.list_repo_holds`. Records `dispatch.refused` and raises, or
   records `dispatch.forced` and returns when `force` is set and conditions applied.
4. **`_dispatch_item`** — call the gate immediately after the repository resolves and before
   the author check; replace the `transition_work_item(..., DISPATCHING)` call with
   `claim_work_item`; translate `ClaimLost` into `DispatchRefused`. `dispatch_item` gains
   `force: bool = False` and an `except DispatchRefused: raise` clause.
5. **`select_and_dispatch`** — catch `DispatchRefused`, record it, and end the pass
   ([R9](research.md#r9)). It passes no `force`.
6. **`operations.resume` / `operations.restart`** — gain `force: bool = False`, pass it
   through, and catch `DispatchRefused` to return `EXIT_PRECONDITION` with the reason.
7. **`cli.py`** — `--force` on both verbs, with help text that says what it overrides and what
   it does not.
8. **`web/server.py`** — `require_dispatchable`, called from `_slow_item_action` alongside the
   existing guards, raising `Refusal(reason, status=409, code=EXIT_PRECONDITION)`.
9. **Tests**, then **documentation** (FR-027, FR-028).

## Complexity Tracking

> No Constitution Check violations. This table is empty by design, not by omission — six
> candidate abstractions were declined outright and are listed under Principle I above rather
> than justified here.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| *(none)*  | —          | —                                    |

## Constitution Re-check (post-Phase 1)

Re-run after `data-model.md`, `contracts/` and `quickstart.md` were written.

- **I. Simplicity** — still passing, and the design got *smaller* during Phase 1: writing
  `contracts/dispatch-gate.md` showed that returning the full ordered list from one function
  ([R2](research.md#r2)) removes the need for a second "collect all reasons" function that an
  earlier sketch carried for FR-023. No new dependency, module, table or key appears in any
  Phase 1 artifact.
- **II. Single-user, local-first** — unchanged. `data-model.md` confirms nothing new is
  persisted; all three new shapes are runtime values.
- **III. Total accountability** — the record table above was written against the contracts and
  matches them. The two gaps are enumerated, and `quickstart.md` step 7 makes the first one
  observable so the author can see the log volume for themselves rather than take it on trust.
- **IV. Interruption tolerance** — `data-model.md`'s claim section states the atomicity as a
  property of one statement in one transaction, and adds no state that could be left partial.
- **V. Public code** — `contracts/cli.md` and `contracts/web.md` fix the exact wording of every
  new message; none carries a secret, a path outside the repository, or a hostname.

Verdict: **pass**, no violations to justify, and Complexity Tracking stays empty.
