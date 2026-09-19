# Implementation Plan: A first-class "start over", and an operating page you can scan

**Branch**: `robot-army/issue-179-a-first-class-start-over-for-an-item` | **Date**: 2026-09-18 |
**Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260918-201447-reset-item-quick-reference/spec.md`

## Summary

Add one verb — `robot-army reset <id>` — that discards a work item's checkout and branch,
re-reads its issue from GitHub, re-checks eligibility through the poller's own evaluation, and
returns the item to the queue. Expose it on the item's web page behind the confirmation
machinery every other mutating action already uses. Then rewrite `docs/guide/operating.md` from
593 lines of prose into a quick reference — recipes, a state table and a command table — with a
test that fails when either table drifts from the program.

**The approach is composition, not mechanism.** Both halves of the operation already exist and
are already right: `retry` is the only code that re-reads an issue and re-evaluates it, and
`worktree_remove` is the only code that discards a checkout under the guards that matter. The
work is to extract the middle of `retry` so both verbs share it — so the author check has one
implementation, not two — to call `worktree_remove` whole rather than reaching past its guards,
and to widen the transition table by two entries so the route exists at all.

The order of the steps is the one real design decision: **read and evaluate first, destroy
second**. The occasion for a reset is an issue that changed, and a changed issue is exactly the
one that may now be ineligible; destroying the checkout before finding that out would leave the
maintainer with neither the work nor a queued item. See [research.md](research.md) R1.

## Technical Context

**Language/Version**: Python 3.11+, standard library first

**Primary Dependencies**: none added. The operation uses `sqlite3`, the existing GitHub issue
reader boundary, and the existing version-control boundary.

**Storage**: SQLite at `~/.local/state/robot-army/state.db`. No migration — reset writes only
columns that already exist. Audit records append to `~/.local/state/robot-army/logs/audit-*.jsonl`.

**Testing**: `pytest`, run as `uv run pytest`. Unit tests under `tests/unit/`, integration under
`tests/integration/`.

**Target Platform**: one Linux machine, one user, no deployment infrastructure.

**Project Type**: single-user daemon with a CLI and a local web interface.

**Performance Goals**: not applicable. Reset is a hand-run command: one HTTP read, one `git
worktree remove`, two short transactions.

**Constraints**: the GitHub read has the boundary's existing timeout and bounded retries.
Database writes are transactional. The checkout removal is the one non-transactional effect, and
research R7 tabulates what an interruption at each point leaves behind.

**Scale/Scope**: roughly 200 lines of source across five modules, a rewritten guide page, edits
to four other guide pages, and five test files.

## Constitution Check

*GATE: passed before Phase 0. Re-checked after Phase 1 design — see the second verdict below.*

### I. Simplicity First (YAGNI & KISS)

**Pass.** One new verb, no new dependency, no new table, no new configuration key, no new
abstraction with one implementation. The single extraction — `_reread_and_refresh`, the middle
of `retry` — has exactly two callers by construction and exists to stop the author check being
written twice, which is the defect `retry` was itself written to close.

Three simpler-looking alternatives were rejected in [research.md](research.md), each because it
bought fewer moving parts somewhere visible at the cost of more somewhere load-bearing: reaching
past `worktree_remove` to its disk half (loses four guards), a `before_requeue` callback on the
shared helper (inverts control flow to save four lines), and an early live-session check in
`reset` to save one API call (a second copy of a guard's refusal wording).

The one place this plan *adds* rather than composes is the documentation drift test, which
parses markdown tables. That is new machinery for a documentation page — justified because the
failure it prevents is precisely the one the issue was filed about, and because a rule nothing
checks is a rule that has already rotted once here.

### II. Single-User, Local-First

**Pass.** No accounts, no roles, no authorization. The web control is reached the same way every
other control is, behind the same same-origin and host checks, on a loopback-bound server whose
lack of authentication is stated on the page it is documented on. No secret is read, written or
logged: reset's parameters are an item id and two booleans.

### III. Total Accountability

**Pass.** What this logs, in full:

| Action | Kind | Written |
|---|---|---|
| `reset` | intent + outcome | intent **before** anything, carrying `item_id` and `force`; outcome carrying what was removed, whether it was requeued, and what refused it |
| `reset.blocked` | event, `error` | a local condition refused it before any network read, with the blocker's sentence |
| `reset.evaluate` | event | the verdict after the live read: eligibility, reason, author, and which columns were refreshed — or, when the read failed, which of `issue_unreachable` / `issue_absent` it was |
| `worktree.remove` | intent + outcome | unchanged, written by the removal command reset calls; a removal is what happened and it is recorded under its own name |
| `state.work_item` | event | unchanged, written inside the transaction that changes the state |

A reset reconstructs from the log alone as: intent → evaluate → removal pair → transition →
outcome. A refusal reconstructs as intent → the record that names which guard refused →
outcome with `refused_by`. **No action is left unlogged**, so this plan claims no exception
under Principle III's exception path.

Two silent-failure risks were looked for specifically. The first is `worktree_remove`'s
non-zero exit when the worktree is gone but an unmerged branch was kept: reset branches on
`worktree_removed` rather than the exit code, and carries the removal's warning lines into its
own output, so the retained branch is reported rather than swallowed (research R3). The second
is a failed read falling back to the stored copy, which would look exactly like success — the
shared helper never does this, and that is the property `retry`'s existing tests already pin.

### IV. Interruption Tolerance

**Pass.** What happens if it is killed halfway through, step by step, is tabulated in
[research.md](research.md) R7. In summary: the content refresh and the state transition are each
in their own `db.transaction`, and `transition_work_item` writes its audit record inside the
transaction that changes the state, so neither can be observed half-written. The checkout
removal is the one effect that is not transactional; killed during it, the item keeps its old
state and a second `reset` completes — finding either no worktree to remove, or a retained
branch that `--force` finishes.

**No ordering in this plan can leave an item `ready` carrying content nobody re-read**, which is
the specific hazard `retry` was ordered to avoid and which reset inherits. The network read has
the boundary's existing timeout and bounded retries; no new network call is introduced.

### V. Public Code, Unsupported Project

**Pass.** No credential, hostname or personal datum enters the repository. No public API is
promised and no deprecation is owed: `retry` keeps its behaviour because changing it is not the
point, not because compatibility is owed to anyone. The documentation is written for the
author's future self at 2am, which is the whole of the second half of this feature.

### Operating Constraints

**Pass.** The verb is reachable from the terminal, exits non-zero on every refusal, and is
observable through `show`, `status`, `log` and the web. It is destructive and outward-facing in
the sense that matters — it deletes work — so it is logged **before** it acts and it requires
confirmation: its own `[y/N]`, or `worktree remove`'s typed-item-id prompt when `--force` is
given (research R4). It is not reachable by default in the one sense that counts: nothing runs
it automatically, and the web cannot pass `--force`.

### Development Workflow

**Pass.** This is the Spec Kit flow, and this plan carries this check. Unit tests are required
for every new or changed unit of behaviour; because this touches a state machine and persistence,
failure- and interruption-path tests are required too and are enumerated in the test plan below.
The full suite must pass before the work is complete.

### Post-design re-check

Re-evaluated after Phase 1. **No violation, and the Complexity Tracking table stays empty.** The
design as contracted adds one verb, one action specification entry, two transition-table entries,
three audit action names and one test module. Nothing in `data-model.md` adds a column, a table
or a configuration key; nothing in `contracts/` introduces an abstraction with a single
implementation. The one thing the design *removes* — 350-odd lines of prose from a guide page —
is scope the issue asked for and is redistributed rather than deleted.

## Project Structure

### Documentation (this feature)

```text
specs/20260918-201447-reset-item-quick-reference/
├── plan.md                      # this file
├── spec.md                      # what and why
├── research.md                  # the ordering decision and the eight others
├── data-model.md                # which columns and transitions the path touches
├── quickstart.md                # how to see it work
├── contracts/
│   ├── reset.md                 # the command: sequence, refusals, exit codes
│   ├── web-reset.md             # the control: routes, spec entry, behaviour
│   └── operating-page.md        # the page's required shape and its drift test
└── checklists/requirements.md
```

### Source (repository root)

```text
src/robot_army/
├── states.py            # + (interrupted → ready), (awaiting_review → ready)
├── operations.py        # + reset(); retry() refactored onto the extracted helper
├── cli.py               # + the reset subparser and its dispatch entry
└── web/
    ├── pages.py         # + the reset ActionSpec in ITEM_ACTIONS
    └── server.py        # + the POST /item/<id>/reset route

docs/guide/
├── operating.md         # rewritten as the quick reference
├── state.md             # + the two transitions; + displaced reboot/interruption prose
├── 3-selection.md       # + displaced blocker prose
├── 5-outcome.md         # + displaced cleanup prose
└── audit-log.md         # + reset, reset.blocked, reset.evaluate

tests/unit/
├── test_states.py              # the two new transitions; the still-illegal ones
├── test_operations_reset.py    # new: the command, its order, and every refusal
├── test_operations_retry.py    # unchanged behaviour after the refactor
├── test_web_actions.py         # the control's legality and the web's no-force rule
└── test_operating_reference.py # new: the two tables against the program
```

**Structure Decision**: the existing single-package layout. No module is added on the source
side; the feature lands as a function in `operations.py` beside the two it composes, an entry in
each of the three surfaces' tables, and two frozen-set members in `states.py`.

## Work, in order

1. **`states.py`** — the two transitions, and the test that enumerates what is still illegal.
   Everything else depends on this and nothing depends on the rest.
2. **`operations.py`** — extract `_reread_and_refresh` from `retry`, verify `retry` is
   observably unchanged, then write `reset` on top of it and `worktree_remove`.
3. **`cli.py`** — the subparser, `--force`, `--yes`, and the dispatch entry.
4. **`web/`** — the `ActionSpec` entry and the route.
5. **Guide pages** — the `operating.md` rewrite last, so it documents what was actually built,
   and the displaced prose lands on its narrative pages in the same change.
6. **`test_operating_reference.py`** — written against the rewritten page.

## Test plan

Beyond the success paths, the constitution requires failure and interruption paths for the state
machine and the persistence work. Specifically:

- **State machine**: both new transitions legal; `active → ready`, `done → ready`,
  `abandoned → ready`, `ready → ready` still illegal.
- **Refusals**, one test each: no such item; each unaccepted state; unresolved repository; a
  dispatch gate still blocking; issue unreachable; issue absent; issue ineligible (author
  changed, specifically — it is the reason the helper is shared); confirmation declined;
  confirmation abandoned at EOF; an open session row; git's dirty-tree refusal; a removal already
  on record.
- **Order**, asserted directly: on an ineligible verdict, the checkout is **still on disk** and
  `worktree_path` is still set. This is R1's decision and the test that pins it.
- **The retained-branch case**: worktree removed, branch kept, and the item still reaches
  `ready` with the warning reported. This is R3's decision and the test that pins it.
- **Interruption**: resuming after a kill between the removal and the transition — simulated by
  driving the steps in sequence — leaves an item a second `reset` completes.
- **Refresh**: the four columns are written from the read and never from the stored copy,
  including when the verdict then refuses.
- **Web**: the control is offered in exactly the three states and no others; a POST from any
  other state is refused `409`; the web never passes `force`; a git refusal is rendered with its
  reason rather than redirecting as success.
- **Simulation**: below the live effect level the removal reports "would remove" and touches
  nothing.
- **Documentation**: the two tables against `WorkItemState` and the CLI parser's subcommands.

## Complexity Tracking

No Constitution Check violation, so this table is empty.
