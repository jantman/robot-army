# Implementation Plan: Per-Repo Concurrency and Wait-for-Merge

**Branch**: `robot-army/issue-47-per-repo-concurrency-and-wait-for-merge` | **Date**: 2026-09-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260902-100200-wait-for-merge-gate/spec.md`

## Summary

Two settings and one rule. `[dispatch] wait_for_merge` and `[repos."x"] wait_for_merge`
mirror the concurrency cap's existing pair exactly, resolved by a
`Config.effective_wait_for_merge` that is `effective_repo_cap` with a boolean in place of a
`min()`. The rule is a fourth clause in `ordering._hold_for`: when the setting is in force
for a repository that already has an **unfinished** item — dispatched at least once, not yet
terminal — every other item in that repository is held with a new `AWAITING_MERGE` reason.

Nothing new is stored, nothing new is asked of GitHub, and no new state machine appears. The
gate is a query over `work_items` that `ordering.plan` already has the connection to run, and
the thing it waits for — an item reaching `done` because its source issue closed, which is
what merging a pull request that says *closes #N* does — is a path `reconcile` already walks
every minute.

Two smaller pieces travel with it. `worktree.prepare` gains a fast-forward of the clone's own
default branch, for wait-for-merge repositories only, through a new
`VersionControl.fast_forward` that refuses anything other than a fast-forward of a clean
checkout and reports why. And `select_and_dispatch`'s hold record, which today fires only for
the three global holds, is widened to cover a pass that dispatched nothing because every
candidate was held — which is what gives the new reason, and `repo_cap` alongside it, a
durable record without adding a second recording path.

The issue's first item — per-repository concurrency limits, configurable globally and per
repository — already ships and is unchanged by this plan. It is covered here by tests and by
a README section that says so, not by a second mechanism.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added. `httpx` remains the sole runtime dependency and this
feature makes no HTTP request of its own.

**Storage**: the existing SQLite database. **No migration.** The gate is derived from
`work_items.state`, which already exists and is already indexed by the queries in use.

**Testing**: `pytest`, `tests/unit/`. `uv run pytest` for the suite, `uv run ruff check` for
lint.

**Target Platform**: one Linux machine with a shell (Operating Constraints).

**Project Type**: single-process CLI daemon with a read-only web view.

**Performance Goals**: `ordering.plan` runs on every 5-second dispatch tick *and* on every
web page render. The gate must add at most one bounded query per plan, not one per queued
item, and must make no network call — see R3.

**Constraints**: `ordering.plan` is pure (no writes, no I/O beyond reading the database) and
must stay that way; `capacity.snapshot` is the only observer of the machine and stays so.

**Scale/Scope**: a handful of repositories, tens of work items, one author.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 — see the re-check at the end.*

### I. Simplicity First (YAGNI & KISS)

**Pass.** No new dependency, no new table, no new module, no new process. The feature is two
configuration keys, one enum member, one clause in an existing function, one boundary method,
and one widened conditional. Three abstractions were available and all three were declined:

- A *pull-request tracking table* — rejected in the spec's Assumptions. The wait the author
  wants is already expressed by the work item's own lifecycle.
- A *generic per-repository policy object* — rejected. There are exactly two settings and
  they resolve by the same two-line rule; a policy type with two fields and one caller is the
  speculative generality Principle I names.
- A *pluggable gate registry* in `ordering` — rejected for the same reason. `_hold_for` is a
  readable sequence of returns whose order *is* the precedence, and a table of predicates
  would replace that with indirection for one new entry.

### II. Single-User, Local-First

**Pass.** No accounts, no network service, no hosted state. Both settings are read from the
existing local TOML file; the gate reads the existing local SQLite database. The one new
outward-adjacent action — fast-forwarding a clone — happens on the local filesystem and only
for a repository the author explicitly configured.

### III. Total Accountability

**Pass, with one gap enumerated below.**

**What this logs**:

| Action | Record |
|---|---|
| A pass dispatches nothing because every candidate is held | `dispatch.at_capacity`, now also emitted for per-item holds, carrying the reason, its detail, and which item is at the head |
| That hold ending | `dispatch.hold_ended`, with duration, passes spanned, and what freed it |
| The clone's default branch fast-forwarded | two records, and both were kept: `git.fast_forward`'s own intent/outcome pair in the boundary, and `worktree.prepare`'s existing outcome dict carrying `fast_forward` plus the reason and the before/after shas |
| The fast-forward declined or failed | the same two, with the specific reason — dirty tree, wrong branch, detached head, mid-operation, diverged, no remote, or the git error |
| The gate opening | already logged: `state.work_item` records every transition into `done` or `abandoned`, which is the only thing that opens it |

**The enumerated gap** (Principle III's documented-exception path): a hold is *not* recorded
once per held item per tick. `_note_hold` keeps a single-slot in-memory record keyed by a
signature and re-records only when the signature changes, so a repository held for four hours
produces one record and one `hold_ended`, not 2,880 of each. The justification is the one the
existing mechanism was built on: the condition is queryable at any instant through
`robot-army status` and the web queue, the transitions that open and close it are separately
and durably logged, and a record per tick would be a flood that a reader would have to filter
before it could be read. This is the same gap `dispatch.at_capacity` already carries for the
global holds; this feature widens the coverage rather than adding a new exception.

**What happens if it is killed halfway through**: nothing needs unwinding. The gate is
computed on read and stored nowhere, so a process killed while holding leaves no state to
correct — the next process recomputes the same answer from the same rows (R6). The
fast-forward is `git merge --ff-only`, whose own index and ref updates are atomic; killed
before it, the clone is untouched and the worktree still branches from the fetched remote ref;
killed after it, the clone is simply current. The in-memory `_HOLD` slot is deliberately
volatile and its loss costs exactly one duplicate record.

### IV. Interruption Tolerance

**Pass.** No new persistent state, so no new atomicity requirement. No new network call, so no
new timeout or retry bound. The one new subprocess sequence is bounded by `subproc`'s existing
timeout, and every step of it is a read except the final `merge --ff-only`, which is
idempotent: running it twice leaves the branch where the first run left it.

### V. Public Code, Unsupported Project

**Pass.** No credentials, no personal data, no hostnames. `wait_for_merge` defaults to off, so
an existing configuration keeps working untouched — not as a compatibility promise, but
because the author's other repositories should not change behaviour when one repository asks
for something.

### Operating Constraints

**Pass.** Both settings are visible from the terminal (`robot-army capacity`), the hold is
visible in `robot-army status` and the web queue, and every command still exits non-zero on
failure. The fast-forward is the only thing here that mutates anything the author owns
outside the daemon's own directories, and per the Operating Constraints' rule for such
actions it is **unreachable by default** — it happens only for a repository with
`wait_for_merge` explicitly in force — is logged with its before/after shas, and refuses
rather than forces at the first sign that it would be more than a fast-forward.

### Development Workflow

**Pass.** Unit tests ship with every changed behaviour, including the refusal paths of the
fast-forward and the precedence of the new hold reason, per the constitution's requirement
that failure paths are tested and not only success paths.

## Project Structure

### Documentation (this feature)

```text
specs/20260902-100200-wait-for-merge-gate/
├── plan.md              # This file
├── spec.md
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── config.md        # the two settings and their resolution
│   ├── dispatch-policy.md  # the gate, its precedence, and what records it
│   └── version-control.md  # the fast_forward boundary method
├── checklists/
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
src/robot_army/
├── config.py            # + RepoConfig.wait_for_merge, DispatchConfig.wait_for_merge,
│                        #   Config.effective_wait_for_merge, key sets
├── ordering.py          # + HoldReason.AWAITING_MERGE, UNFINISHED_STATES,
│                        #   unfinished_by_repo(), the gate clause
├── dispatch.py          # widen the hold record to per-item holds
├── worktree.py          # call fast_forward when the setting is in force
├── repos.py             # carry the field through resolve()
├── operations.py        # capacity: report the setting per repository
└── boundaries/
    ├── __init__.py      # + FastForwardResult, VersionControl.fast_forward, simulated impl
    └── git.py           # the real fast_forward

tests/unit/
├── test_config.py             # the two keys, defaults, resolution, unknown-key refusal
├── test_repos.py              # resolve carries the field rather than dropping it
├── test_ordering.py           # the gate: holds, releases, per-repo isolation, precedence
├── test_capacity_reporting.py # capacity reports both limits and both sources
├── test_web_views.py          # the queue view renders the new reason
└── test_git_boundary.py       # fast_forward's refusals and its one success

tests/integration/
├── test_worktree.py           # prepare calls it only when the setting is in force
└── test_dispatch_capacity.py  # the gate end to end; a per-item hold recorded once
```

Two of those moved out of `tests/unit/` once the existing fixtures were looked at. The
gate's end-to-end behaviour and the fast-forward's caller both need a registry, a `/proc`,
real repositories and a trust file, and `tests/integration/test_dispatch_capacity.py` and
`tests/integration/test_worktree.py` already build all of it. A new unit file would have had
to reproduce that machinery in order to say strictly less.

**Structure Decision**: unchanged from milestone 004's split, and the new code lands on the
right side of it. `capacity.py` observes the machine and gains nothing here, because the gate
is not a fact about the machine — it is a fact about the *work*, and it is a policy applied to
that fact. Both belong to `ordering.py`, which is where the configuration meets the
observation. The temptation to hang `unfinished_per_repo` off `CapacitySnapshot` beside
`per_repo` is specifically rejected in research R2: it would make `capacity` import work-item
states to answer a question no observer of the machine can answer.

## Complexity Tracking

> No Constitution Check violations. This section is empty by design.

## Constitution Check — re-evaluated after Phase 1

Re-run against the design as it now stands in `research.md`, `data-model.md`, and
`contracts/`. **All five principles still pass, and the design got simpler rather than more
complex during Phase 1.**

Three things changed the assessment and none of them adversely:

1. **No migration** (R6, data-model.md). The gate turned out to be a predicate over a column
   that has existed since milestone 001. Principle IV's atomicity requirement has nothing new
   to apply to, and SC-004 — the setting taking effect with no stored state to correct —
   became true by construction.
2. **The hold record widened rather than multiplied** (R5). The alternative was a second
   recording path for per-item holds; reusing the existing single-slot mechanism means
   Principle III gains one enumerated, justified gap instead of a new one, and `repo_cap`
   picks up log coverage it never had.
3. **The fast-forward became one boundary method with four outcomes** (R7) rather than six
   git invocations assembled in `worktree.prepare`. Principle I is better served, and the
   Operating Constraints' rule for actions that mutate the author's own files is met
   squarely: unreachable by default, logged with before and after shas, and refusing rather
   than forcing at the first sign of trouble.

The one thing worth restating because it is the sharpest edge in the design: **the
fast-forward touches a directory the author owns and works in.** Every guarantee in
`contracts/version-control.md` exists for that reason — six preconditions checked before
anything is attempted, `--ff-only` as a last line of defence behind them, no `--force`
anywhere, and a skip that is recorded rather than silent. A test per refusal is required, not
optional (Development Workflow: failure paths, not only success paths).

No entry in Complexity Tracking. Nothing needed justifying.


## Post-implementation reconciliation (T047)

Re-read against the code as built. **The Constitution Check above still holds**, and the
enumerated Principle III gap is unchanged in substance. Four things differ from what this
document promised, and each is recorded rather than quietly absorbed:

1. **The fast-forward writes two records rather than one.** The plan said the outcome would
   go into `worktree.prepare`'s existing audit action, and it does. But `fast_forward` is a
   boundary verb that mutates the author's own clone, and every other such verb in
   `boundaries/git.py` opens its own `audit.action` — so it does too, and its *intent*
   record lands before the merge runs. That is Principle IV's crash signature (an intent
   with no outcome) applied to the one step here that changes a directory the author works
   in, and dropping it to honour a table in this document would have been the wrong trade.

2. **`capacity`'s per-repository listing widened slightly further than promised.** It is the
   union of the onboarded repositories and any repository with a live session, rather than
   the onboarded set alone, so a session running in a repository whose onboarding record has
   since gone still appears somewhere visible instead of being silently dropped.

3. **The hold record carries a `dispatched == 0` condition the plan did not mention.** A pass
   that started work and then ran out of candidates has not stalled, and recording that as a
   hold would have written a record every time the machine filled up in the ordinary way. It
   is commented at the point it matters.

4. **`_repo_settings` is deliberately not on the web's path.** `operations.capacity` calls
   it; `_capacity_dict`, which the web chrome renders on every page, does not. Rendering a
   page therefore still costs what it cost before, which is what FR-013 protects.

Nothing was added to Complexity Tracking. Everything built here was already justified by the
Constitution Check above.

### One defect found in review, and what it changes

Widening the hold recorder (R5) carried a consequence the plan did not follow through on.
Before this milestone, `_HOLD` could only ever contain a `_GLOBAL_HOLDS` reason, and a pass
carrying one returned before dispatching anything — so *"a dispatch happened"* and *"the
recorded hold ended"* were the same fact, and clearing unconditionally on a successful
selection was sound. Making per-item holds recordable broke that equivalence in exactly the
way the feature intends: a repository waiting for its work to land stays held while an item
in a different repository dispatches. The unconditional clear then wrote a `hold_ended` for
a repository still waiting, attributed it to an unrelated item, and left the next quiet pass
to reopen the hold with its duration restarted from zero.

The fix separates two questions that had been one. A hold's **signature** answers *has
anything about this changed?* and suppresses a repeat; its new **identity** —
`(reason, repository)` — answers *is this the same hold at all?* and decides whether an
ending has occurred. `_resolve_hold` now asks the plan rather than the dispatch: if any
entry in this pass is still held by the recorded identity, nothing is cleared. A change of
identity is also bracketed with an ending, so the log closes holds rather than leaving each
closing to be inferred from the next opening.

Worth recording because the mistake is instructive: the bug was not in the new gate, it was
in an old invariant that the new gate quietly invalidated. Widening a mechanism means
re-checking what its callers were entitled to assume.

**And the fix had a defect of its own, found in the next review round.** Identity was
`(reason, repository)` for every hold, including the global ones — but a global hold is a
fact about the *machine*, and the entry reporting it is merely whichever item happened to be
at the head of the queue. That head shifts whenever an item is abandoned or the order
changes, so one uninterrupted "the machine is full" was reported as a succession of holds
handing over to each other, each ending blamed on a repository that had freed nothing. A
global hold's identity now carries a `MACHINE` sentinel in place of a repository, which is
what makes the pair a pair rather than an over-specified reason.

The fix's own docstring initially over-claimed, and was corrected: identity governs whether
an *ending* is asserted, and nothing else. A head shift still changes the signature, and
`_note_hold` has always reopened its slot on a signature change, so a long global hold still
reports as several `dispatch.at_capacity` records with their own durations. That is
pre-existing and pinned by a test. What the fix removes is the `hold_ended` records
asserting something that did not happen.

Two rounds, two bugs, both in the same seam: not in what was added, but in what the addition
made untrue. That seam is worth naming for whoever changes this next — the hold recorder's
correctness rests on which facts its identity is allowed to depend on.
