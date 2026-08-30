---

description: "Task list for issue #33 — liveness is checked wherever the session is real"
---

# Tasks: Liveness Is Checked Wherever the Session Is Real

**Input**: Design documents from `specs/20260830-133818-reconcile-session-liveness/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/liveness-decision.md](./contracts/liveness-decision.md)

**Baseline**: `main` at 15bf843. Suite at baseline: **1780 passed, 1 skipped**.

**Tests**: Included. The constitution requires unit tests for every new or changed unit of
behaviour, and requires failure-path tests for persistence and recovery logic — which this is.
It also states plainly that **test-first is not mandatory**, so no task below asks for a
deliberately failing test first; the requirement is that the tests exist and are meaningful.

**Organization**: Grouped by user story. Two ordering facts drive the whole list and are worth
reading before starting:

1. **The fixture correction (T003) must land before the production change (T009).** Today
   `tests/unit/test_reconcile.py` builds `dry_run=True` session rows carrying a real-looking pid
   — a shape the dispatch path never creates. Two tests depend on it (research.md R5). Corrected
   *first*, the change is a no-op against current code and the suite stays green; corrected
   *after*, the suite is red in between.
2. **The superseded sweep (US3) must run before #28's stale-session sweep in the pass**, which
   it does by construction if it is placed inside the existing active-item loop
   ([C5](./contracts/liveness-decision.md)). Reversing that order double-reports an orphaned
   worker or hides it.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story the task belongs to (US1–US4)
- Paths are repository-relative from `/home/jantman/worktrees/robot-army/issue-33`

---

## Phase 1: Setup

**Purpose**: Establish the baseline this feature's claims are measured against. There is no
project to initialise — the package, its dependencies and its tooling all already exist.

- [ ] T001 Confirm the worktree is at `main` 15bf843 or later and that `git status` shows only the untracked `specs/20260830-133818-reconcile-session-liveness/` directory
- [ ] T002 Record the baseline by running `uv run pytest -q` and confirming **1780 passed, 1 skipped**; any other number means the baseline moved and research.md R5's "2 failures, no more" claim must be re-measured

**Checkpoint**: Baseline known. Every later claim about breakage is relative to this number.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Two changes that every user story depends on, and which are each independently
correct against *current* code — so this phase can land as its own commit with the suite green.

**⚠️ CRITICAL**: No user story work begins until T003 is done, or the suite goes red mid-feature
for reasons unrelated to the change being made.

- [ ] T003 Correct the misleading fixture in `tests/unit/test_reconcile.py`: give the two `active_item(..., dry_run=True)` call sites `pid=0, proc_start=None`, the shape a simulated session actually has. Do not change either test's assertions. Verify with `uv run pytest tests/unit/test_reconcile.py -q` → 24 passed
- [ ] T004 Add a comment at `active_item`'s definition in `tests/unit/test_reconcile.py` recording that its default `pid=4242` is a *real* session's shape, so a future `dry_run=True` caller does not silently recreate the conflation this feature removes
- [ ] T005 Add `skipped_never_real: int = 0` and `superseded: int = 0` to `ReconcileResult` in `src/robot_army/reconcile.py`, placed after `reclaimed` to match the pass's execution order
- [ ] T006 Add `"skipped_never_real"` and `"superseded"` to `ReconcileResult.summary()` in `src/robot_army/reconcile.py`, without renaming or reordering any existing key — `docs/logging.md` and the `--json` consumers read them by name

**Checkpoint**: Suite still green at 1780. Counters exist and read zero. Nothing has changed
behaviour yet.

---

## Phase 3: User Story 1 — A dead worker is noticed wherever the worker was real (P1) 🎯 MVP

**Goal**: The liveness sweep asks whether the session had a process, not whether the record is
flagged simulated — so a dead session at `no-remote` is reconciled exactly as it is at `live`.

**Independent Test**: At `no-remote`, dispatch an item, kill its worker, run reconciliation, and
confirm the item is `interrupted`, the session `lost`, and the capacity slot free.

### Implementation for User Story 1

- [ ] T007 [US1] Replace the `if session.dry_run:` skip in the active-item sweep of `src/robot_army/reconcile.py` with a skip on the absent process identifier (`if not session.pid:`), incrementing `result.skipped_never_real` at the skip ([C1](./contracts/liveness-decision.md))
- [ ] T008 [US1] Rewrite that skip's comment in `src/robot_army/reconcile.py` to state the rule and the reason: a session with no process identifier never had a host to be alive, `NULL` and `0` mean the same thing here, and the effect level is deliberately not consulted because T147 forbids it
- [ ] T009 [US1] Verify the change is confined correctly by running `uv run pytest tests/unit/test_effects.py -q` — T147 asserts no module outside the allow-list names `EffectLevel`, and `reconcile.py` is not exempt (FR-003)

### Tests for User Story 1

- [ ] T010 [P] [US1] Create `tests/unit/test_session_liveness.py` with a parametrised case covering all four record shapes from [data-model.md](./data-model.md): `(dry_run=0, pid=real)`, `(dry_run=1, pid=real)`, `(dry_run=1, pid=0)`, `(dry_run=1, pid=NULL)` — asserting the item state and session state each produces against an empty registry and empty `/proc`
- [ ] T011 [P] [US1] In `tests/unit/test_session_liveness.py`, assert the `no-remote` shape specifically: an `active` item with `dry_run=1` and a real pid whose process is gone becomes `interrupted` with its session `lost`. This is the case nothing covered before, which is why the defect shipped (FR-005)
- [ ] T012 [P] [US1] In `tests/unit/test_session_liveness.py`, assert `live` behaviour is byte-for-byte what it was: same inputs, same outcome, same counters (FR-013)
- [ ] T013 [P] [US1] In `tests/unit/test_session_liveness.py`, assert the exit-record precedence — a session already recording `exited_clean` or `exited_error` is left alone even though no process is found ([C2](./contracts/liveness-decision.md) ordering)
- [ ] T014 [P] [US1] In `tests/unit/test_session_liveness.py`, assert a pre-existing row is classified with no migration step: a row written before this change carries the same `pid` it always did and is handled correctly on the first pass (FR-008)
- [ ] T015 [US1] Add an integration case to `tests/integration/test_reconcile_pass.py` (create the module if absent) walking a full pass: `no-remote` item dispatched, worker killed, one `reconcile()` call, asserting item, session, and the released capacity slot together (FR-007)

**Checkpoint**: US1 complete and independently testable. This is the MVP — issue #33's reported
defect is fixed. Expect **1780 + new cases passed, 0 failed**; if the two `test_reconcile.py`
tests fail here, T003 was skipped.

---

## Phase 4: User Story 2 — Rehearsing without a real session stays quiet (P2)

**Goal**: Items at `plan` and `local` are never touched by the liveness sweep, and a pass says
so distinguishably.

**Independent Test**: At `plan` and `local`, dispatch items, reconcile repeatedly, confirm they
stay `active` with sessions untouched.

> **This phase adds no production code.** The behaviour is delivered by US1's implementation;
> what US2 contributes is the evidence that the fix did not break the invariant the original
> skip existed to protect. That invariant is the highest-consequence regression this feature
> could cause — every simulated item marked `interrupted` on the next pass — so it is tested as
> its own story rather than folded into US1's cases.

### Tests for User Story 2

- [ ] T016 [P] [US2] In `tests/unit/test_session_liveness.py`, assert that an `active` item whose session never had a process is unchanged across **three consecutive** `reconcile()` calls — not one, because the failure this guards against is a sweep that marks everything interrupted on the *next* pass (FR-006)
- [ ] T017 [P] [US2] In `tests/unit/test_session_liveness.py`, assert `skipped_never_real` is incremented for such a session and `interrupted` is not, so a skipped session is distinguishable from one checked and found alive (FR-009, SC-005)
- [ ] T018 [P] [US2] In `tests/unit/test_session_liveness.py`, assert `checked` retains its existing meaning — one per `active` item visited — so the pair `checked`/`skipped_never_real` reads correctly and no existing consumer's figure changed meaning
- [ ] T019 [US2] Verify FR-055 is still honoured end to end by confirming `tests/unit/test_reconcile.py::test_a_simulated_session_is_not_reconciled_against_proc` passes unmodified in its assertions — only its fixture shape changed, in T003

**Checkpoint**: The invariant is defended by tests that would fail loudly if the discriminator
were ever inverted.

---

## Phase 5: User Story 3 — A superseded attempt's worker is not left unwatched (P2)

**Goal**: Every open session an item still owns is examined, not only its newest — so a resumed
item's abandoned worker is reported rather than hidden by two blind spots holding each other up.

**Independent Test**: Give an `active` item two open sessions, leave the older one's process
alive, and confirm reconciliation reports it as an orphan while the newer attempt is handled
normally.

### Implementation for User Story 3

- [ ] T020 [US3] Add `_sweep_superseded_sessions()` to `src/robot_army/reconcile.py` implementing [C3](./contracts/liveness-decision.md)'s three branches over `db.list_sessions_for_item`, skipping the item's current attempt and any record not in `starting`/`running`
- [ ] T021 [US3] In `_sweep_superseded_sessions()` in `src/robot_army/reconcile.py`, implement the **alive** branch: add the pid to `claimed_pids`, raise an `orphan_session` anomaly carrying `pid`, `cwd`, `work_item_id` and `attempt`, and **leave the record open**. Document in the docstring that closing it would report fewer running sessions than exist, which is the only direction of capacity error that causes harm
- [ ] T022 [US3] In `_sweep_superseded_sessions()`, implement the **dead** branch: transition the record to `lost` with a reason naming it as superseded rather than current, so the log distinguishes the two closures ([C6](./contracts/liveness-decision.md))
- [ ] T023 [US3] In `_sweep_superseded_sessions()`, implement the **never-real** branch: a superseded record with no process identifier is left entirely alone, by the same rule as [C1](./contracts/liveness-decision.md)
- [ ] T024 [US3] Call `_sweep_superseded_sessions()` from inside the active-item loop in `src/robot_army/reconcile.py`, before the current attempt's liveness check, accumulating into `result.superseded`. Placing it here is what satisfies [C5](./contracts/liveness-decision.md)'s ordering without any new coordination
- [ ] T025 [US3] Confirm `_orphan_sweep` and `_sweep_stale_sessions` are untouched with `git diff -U0 src/robot_army/reconcile.py` — both must remain byte-identical, as #28 established and [C4](./contracts/liveness-decision.md) requires

### Tests for User Story 3

- [ ] T026 [P] [US3] In `tests/unit/test_session_liveness.py`, assert the live-ghost case: an `active` item with an older open session whose process is alive produces exactly one `orphan_session` anomaly naming that session, with its record still open (FR-017)
- [ ] T027 [P] [US3] In `tests/unit/test_session_liveness.py`, assert no double report — `_orphan_sweep` contributes `orphans=0` for that worker because C3 left the record `running` (FR-019, SC-008)
- [ ] T028 [P] [US3] In `tests/unit/test_session_liveness.py`, assert the dead-ghost case: the older record becomes `lost`, `superseded` is incremented, and the **item stays `active`** on its current attempt — a resumed item must never be interrupted by the ghost of the attempt the resume replaced (FR-018)
- [ ] T029 [P] [US3] In `tests/unit/test_session_liveness.py`, assert simulated multi-attempt rows are untouched: two open `pid=0` records under one `active` item, `superseded=0`, both still `running` (FR-006 under US3's new code path)
- [ ] T030 [P] [US3] In `tests/unit/test_session_liveness.py`, assert idempotency: a second `reconcile()` over the same live ghost produces no second anomaly, the open-anomaly index absorbing re-detection
- [ ] T031 [P] [US3] In `tests/unit/test_session_liveness.py`, assert the single-session case is unchanged — an `active` item with exactly one open record behaves identically to before this phase (FR-011's non-regression half)
- [ ] T032 [US3] Add an integration case to `tests/integration/test_reconcile_pass.py` covering a resumed item whose first worker survived, asserting the anomaly, the untouched record, and the item's state from its current attempt alone
- [ ] T033 [US3] Assert the capacity consequence in `tests/unit/test_capacity.py`: a superseded record closed as `lost` stops counting toward both the global and per-repository caps (FR-007)

**Checkpoint**: All behavioural stories complete. Both blind spots closed.

---

## Phase 6: User Story 4 — The terminal-death rehearsal describes what actually happens (P3)

**Goal**: 001's quickstart scenario 4 states outcomes this machine actually produces, so it can
tell a broken system from a stale document.

**Independent Test**: Follow the scenario as written and confirm each stated expectation is the
one observed.

> **This phase needs a real worker** and cannot be settled by the suite. It is the one part of
> this feature CI cannot close.

- [ ] T034 [US4] Run the terminal-death case from `specs/001-minimum-daemon/quickstart.md` scenario 4 against a real session at `no-remote`: `kill -9` the wrapper, then check with `ps` whether the worker actually survived, and record the observed result
- [ ] T035 [US4] Update `specs/001-minimum-daemon/quickstart.md` scenario 4 to state what T034 observed. If the worker did **not** survive, say so plainly and point the orphan case at this feature's scenario 3, which produces one by a route that works; if it **did** survive, confirm the `orphan_session` anomaly appears and note that reconciliation now reaches this at `no-remote` too (FR-014)
- [ ] T036 [US4] Append T034's result to issue #1's verification round, since that is where the original observation was recorded and where the contradiction was found

**Checkpoint**: The acceptance test for stories 1 and 3 can distinguish success from failure.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T037 [P] Update `docs/logging.md` around line 449 to record the two new `reconcile.pass` figures and the newly-reachable `orphan_session` case, in the same table and style #28 used for `reclaimed`
- [ ] T038 [P] Update `docs/state.md` around line 428 with the interruption behaviour of the superseded sweep, alongside the existing stale-session-sweep rows
- [ ] T039 [P] Record in `docs/state.md` the accepted gap this feature enumerates: reconciliation reads an unobservable registry as death, tracked as issue #44, with a pointer to this feature's plan for the reasoning
- [ ] T040 Run `uv run ruff check src/ tests/` and confirm clean
- [ ] T041 Run the full suite and confirm **no failures** and a total of 1780 plus the cases added here; a failure in `tests/unit/test_reconcile.py` means T003 was skipped or reverted
- [ ] T042 Walk quickstart scenarios 1–4 from [quickstart.md](./quickstart.md) by hand against a real database, since a green suite is not a substitute for walking them once
- [ ] T043 Re-check the plan's Constitution Check against what was actually built, and record any elaboration that crept in beyond the four already rejected in [plan.md](./plan.md)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no dependencies
- **Phase 2 (Foundational)**: depends on Phase 1 — **blocks every user story**, and T003 in particular blocks T007
- **Phase 3 (US1)**: depends on Phase 2. The MVP
- **Phase 4 (US2)**: depends on Phase 3 — tests US1's implementation, adds no code of its own
- **Phase 5 (US3)**: depends on Phase 2 for its counter; **independent of US1's code change**, though both edit `reconcile.py` so they serialise in practice
- **Phase 6 (US4)**: depends on Phase 3 and Phase 5 for what it documents; needs a real machine
- **Phase 7 (Polish)**: depends on all desired stories

### Within each story

- The constitution does not mandate test-first. Tasks are listed implementation-then-tests where
  that reads more naturally, and either order satisfies the requirement that the tests exist.
- T020–T024 are strictly sequential — one function, built branch by branch, then wired in.

### Parallel Opportunities

- **T010–T014** (US1 tests) are all `[P]`: distinct cases in one new file, no shared state
- **T016–T018** (US2 tests) are all `[P]`, and can run alongside US1's tests once T007 lands
- **T026–T031** (US3 tests) are all `[P]` once T024 has wired the sweep in
- **T037–T039** (docs) are `[P]`: three separate sections in two files
- **Not parallel**: T005/T006 (same dataclass), T007 and T020–T024 (same module, adjacent lines),
  T003 before everything

### Parallel Example: User Story 1

```bash
# Once T007–T009 have landed, the five US1 cases are independent:
Task: "T010 four record shapes in tests/unit/test_session_liveness.py"
Task: "T011 the no-remote shape in tests/unit/test_session_liveness.py"
Task: "T012 live is unchanged in tests/unit/test_session_liveness.py"
Task: "T013 exit-record precedence in tests/unit/test_session_liveness.py"
Task: "T014 pre-existing rows need no migration in tests/unit/test_session_liveness.py"
```

---

## Implementation Strategy

### MVP (Phases 1–3, then stop and validate)

Phases 1, 2 and 3 fix issue #33 as reported: eleven tasks, one changed condition, one new test
module. Validate with quickstart scenario 1 at both `no-remote` and `live` before going further.
This is a complete, shippable increment — US3 is a second bug found while measuring, not a
prerequisite.

### Incremental delivery

1. **Phase 2 alone** is committable with the suite green and no behaviour changed — a clean first
   commit that removes a misleading fixture and adds two unused counters
2. **+ Phase 3** → issue #33 fixed → validate → commit
3. **+ Phase 4** → the invariant defended by tests → commit
4. **+ Phase 5** → the superseded gap closed → validate scenario 3 → commit
5. **+ Phases 6–7** → documentation caught up with reality → commit

### Commit discipline

The constitution asks for atomic commits whose messages say **why**. Three of these phases have
a *why* that is not obvious from the diff and should be written down: Phase 2's fixture change
looks cosmetic and is a prerequisite; Phase 4 adds only tests and is defending the highest-
consequence regression in the feature; Phase 5's alive-branch deliberately leaves a record open,
which reads like a bug unless the capacity reasoning is in the message.

---

## Notes

- `[P]` = different files or independent cases, no dependency on incomplete work
- Every task above names a real path in this checkout; `tests/integration/test_reconcile_pass.py`
  is the only file that may not exist yet
- Phase 0 measured all of this against the tree — see [research.md](./research.md) R1–R10 — so
  task-level surprises should be rare. If one appears, it belongs in research.md before it
  belongs in the code
- Issue #44 is deliberately **not** in this list. It was split out by decision D2 and fixing it
  here would change `live` behaviour, which FR-013 forbids
