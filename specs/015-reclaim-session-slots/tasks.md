---

description: "Task list for 015-reclaim-session-slots"
---

# Tasks: Reclaiming capacity slots held by sessions that are no longer running

**Input**: Design documents from `/specs/015-reclaim-session-slots/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Test tasks are **required**, not optional. The constitution's Development Workflow
states "Unit tests are required. Every new or changed unit of behavior MUST ship with unit
tests", and adds that "persistence and recovery logic, state machines, and code parsing external
input MUST additionally carry tests exercising their failure and interruption paths". This
feature is a recovery sweep over a state machine, so both clauses bind.

**Organization**: Grouped by user story so each can be implemented and verified independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: US1, US2, US3 — maps to the user stories in [spec.md](./spec.md)

## Path Conventions

Single Python package at the repository root: `src/robot_army/`, `tests/unit/`,
`tests/integration/`. Paths below are repository-relative.

**Note on parallelism**: this feature is small and concentrated. US1 and US2 both edit
`src/robot_army/operations.py`, and Phase 2 and US3 both edit `src/robot_army/reconcile.py`, so
those are **not** parallelisable despite the stories being independent. Most `[P]` markers below
are on test files, which is where the genuine parallelism is. Claiming more would be dishonest
about a sixty-line change.

**Note on the shared helper**: unusually for this project, Phase 2 is real rather than filler.
All three stories call the same decision helper, so it genuinely blocks all of them —
see [contracts/slot-reclamation.md](./contracts/slot-reclamation.md) C1.

---

## Phase 1: Setup

**Purpose**: Establish the baseline, so any later failure is attributable to this feature.

- [X] T001 Run `uv run pytest -q` and `uv run ruff check .` from the repository root and confirm both are green before making any change; record the counts for comparison in T023 (the measured baseline at planning time was 1714 passed, 1 skipped)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The single decision rule every story calls. **No user story work may begin until
this phase is complete** — US1, US2 and US3 are three call sites of the same function, and
implementing any of them separately would re-derive the rule.

**⚠️ CRITICAL**: the liveness branch is the one that can cause real harm. Closing a row whose
worker is alive makes reported capacity *lower* than the number of running workers, which
oversubscribes the very subscription the cap protects.

- [X] T002 Create `tests/unit/test_slot_reclamation.py` with three cases for the decision helper, one per branch of [contracts/slot-reclamation.md](./contracts/slot-reclamation.md) C1: an open row under a `dispatching` or `active` item is left untouched; a simulated row (`dry_run=1`, `pid=0`) under an `interrupted` item is reclaimed to `lost` with `ended_at` stamped; a row whose registry entry is alive in a fake `/proc` is **not** transitioned and raises an `orphan_session` anomaly. Use the existing `seed_item` / `seed_session` / `write_registry` / `write_proc` helpers from `tests/conftest.py`. Confirm all three FAIL before T003
- [X] T003 Implement the decision helper in `src/robot_army/reconcile.py` per C1, taking the open session row, its work item, the registry scan already taken by the caller, an optional `proc_root`, and the reason string as a parameter (the three call sites must be distinguishable in the log — C3). Liveness must use `RegistryEntry.alive()`; introduce no second detection method. Do not wire it to any caller in this task
- [X] T004 Add the FR-005 safety case to `tests/unit/test_slot_reclamation.py`: after the helper declines to close a live row, `capacity.snapshot` still counts it, so the reported total is never lower than the number of live workers (sequential with T002 — same file)
- [X] T005 Add the idempotency cases to `tests/unit/test_slot_reclamation.py` (FR-008): applying the helper twice to an already-reclaimed row writes no second `state.session` record, and a persistently live worker produces one `orphan_session` anomaly rather than one per call (sequential with T002/T004 — same file)

**Checkpoint**: the rule exists, is correct on all three branches, and is proven not to under-count. Story work can begin.

---

## Phase 3: User Story 1 - Cancelling an item gives its slot back (Priority: P1) 🎯 MVP

**Goal**: `robot-army cancel` releases everything the session was holding, before the command
returns, with or without a running daemon.

**Independent Test**: with `default_repo_max_sessions = 1`, dispatch two simulated items for one
repository, cancel the first, and confirm the second dispatches instead of sitting at `repo_cap`.

**Why this is the MVP**: it is the reported defect and the one that blocked a verification round.
With only this story done, the `local` rehearsal workflow works again. US2 covers the second
route out of `active`; US3 covers rows already leaked.

### Tests for User Story 1

- [X] T006 [US1] Add cancel cases to `tests/unit/test_slot_reclamation.py`: after `operations.cancel` on a simulated `running` session, the session row is `lost` with `ended_at` set, the item is `interrupted`, and `capacity.snapshot` reports the repository below its cap — asserted with **no reconciliation pass run**, which is the whole of FR-012. Confirm they FAIL before T007
- [X] T007 [P] [US1] Add an integration case to `tests/integration/test_dispatch_capacity.py` for quickstart scenario 1: two simulated items in one repository at a cap of 1, cancel the first, the second becomes dispatchable and its `repo_cap` hold clears

### Implementation for User Story 1

- [X] T008 [US1] Wire `operations.cancel` in `src/robot_army/operations.py`: add an optional `registry_dir: Path | None = None` parameter following the existing `resume`/`restart` precedent, and call the T003 helper **inside the existing `db.transaction`** that moves the item to `interrupted`, with a reason naming cancellation. The item's own transition must not change (FR-011)
- [X] T009 [P] [US1] Add a case to `tests/unit/test_web_actions.py` asserting the web cancel action releases the slot too, since `web/server.py` routes it through the same `operations.cancel`; confirm the existing cancel cases in that module still pass unchanged

**Checkpoint**: User Story 1 is fully functional and testable independently. The reported bug is fixed.

---

## Phase 4: User Story 2 - Abandoning an item holds nothing (Priority: P2)

**Goal**: `robot-army abandon` closes any session row still open under the item.

**Independent Test**: abandon an item whose session row is open and confirm the slot is released,
without touching the cancel path.

### Tests for User Story 2

- [X] T010 [US2] Add abandon cases to `tests/unit/test_slot_reclamation.py`: after `operations.abandon` on an item with an open simulated session, the row is `lost` and the slot released, the item is `abandoned`, and its worktree path is untouched (FR-011). Include the cancel-then-abandon sequence from the spec's edge cases, asserting the second close is a no-op rather than an error. Confirm they FAIL before T011
- [X] T011 [P] [US2] Add a case to `tests/unit/test_web_actions.py` covering the web abandon action (`web/server.py:1023`), which routes through the same `operations.abandon`

### Implementation for User Story 2

- [X] T012 [US2] Wire `operations.abandon` in `src/robot_army/operations.py`: add the same optional `registry_dir` parameter, look up the item's latest session, and call the T003 helper inside the existing `db.transaction` that moves the item to `abandoned`, with a reason naming abandonment. It must still consult liveness even though `abandon` is not reachable from `active` — FR-005 is a property of the rule, not of the caller

**Checkpoint**: both reported routes out of `active` release their slot at the command.

---

## Phase 5: User Story 3 - Slots already leaked are reclaimed without discarding work (Priority: P3)

**Goal**: reconciliation asserts the invariant regardless of route, including for rows left open
before this feature existed, and says how many it closed.

**Independent Test**: start from a database holding an open row under a finished item, run
reconciliation, and confirm the row is closed while every work item and intake card survives.

### Tests for User Story 3

- [X] T013 [US3] Add sweep cases to `tests/unit/test_slot_reclamation.py`: a pass over a database in the state cancel leaves behind *today* reclaims the row and reports `reclaimed 1` rather than `checked 0` (FR-009); a second pass reports `reclaimed 0` and writes no repeated `state.session` record (FR-008). Confirm they FAIL before T015
- [X] T014 [P] [US3] Add an integration case to `tests/integration/test_dispatch_capacity.py` for quickstart scenario 4: after the sweep reclaims a slot, every work item, every session history row, and every tracked card is still present (FR-004), and `operations.resume` on the reclaimed item still works from that session's context (FR-010)

### Implementation for User Story 3

- [X] T015 [US3] Add `reclaimed: int = 0` to `ReconcileResult` and to its `summary()` in `src/robot_army/reconcile.py`, so the count reaches the `reconcile.pass` audit record and the `robot-army reconcile` CLI output (which prints every `summary()` key)
- [X] T016 [US3] Implement the sweep in `reconcile()` in `src/robot_army/reconcile.py`: iterate `db.list_sessions(include_simulated=True, states=[STARTING, RUNNING])`, resolve each row's work item, apply the T003 helper with a reason naming the sweep, and count reclamations into `result.reclaimed`. Position it **after `_resolve_closed_issues` and before `_orphan_sweep`** — both halves are load-bearing (C2)
- [X] T017 [US3] Add a case to `tests/unit/test_slot_reclamation.py` proving the ordering matters: a live worker under a `done` item (the issue-closed-while-running case from research R6) takes the REPORT branch and is counted in `orphans`, not in `reclaimed`

**Checkpoint**: all three stories are independently functional and the invariant holds by any route.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T018 Confirm the untouched-list in [contracts/slot-reclamation.md](./contracts/slot-reclamation.md) C6 by reading `git diff`: `states.py`, `capacity.py`, `models.py`, `_orphan_sweep`, `_resolve_closed_issues` and `purge_simulated` must all be unchanged, and neither transition table may have gained an edge
- [X] T019 [P] Update `docs/logging.md` where it describes the `reconcile.pass` summary (around line 360) to name the new `reclaimed` counter and what it means
- [X] T020 [P] Update `docs/state.md`'s interruption table (around lines 400–418) with the row this feature adds: killed between stopping the process and closing the row leaves the row open, and the sweep resolves it
- [X] T021 Walk quickstart scenario 3 by hand or as a test and confirm the dangerous case: a live worker under an `interrupted` item is reported as `orphan_session` and is **not** swept away, where today it produces `orphans: 0` and no anomaly at all
- [X] T022 Run the regression surface named in [quickstart.md](./quickstart.md) explicitly: `uv run pytest tests/unit/test_capacity.py tests/unit/test_web_actions.py tests/unit/test_states.py tests/unit/test_spool.py tests/integration/test_dispatch_capacity.py -q`
- [X] T023 Run `uv run pytest -q` and `uv run ruff check .` from the repository root; compare against the T001 baseline and confirm no pre-existing test regressed
- [X] T024 Write the commit message: it must explain *why* — a session row that outlives its work item holds a capacity slot no one can see, and the queue stalls with `repo_cap` as the reason, which reads as the cap working correctly. Reference issue #28

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no dependencies
- **Phase 2 (Foundational)**: depends on T001 — **BLOCKS all three user stories**, because all three are call sites of the T003 helper
- **Phase 3 (US1)**: depends on Phase 2
- **Phase 4 (US2)**: depends on Phase 2. Independent of US1 in behaviour, but edits the same file (`operations.py`), so run it after US1 rather than concurrently
- **Phase 5 (US3)**: depends on Phase 2. Independent of US1 and US2 in behaviour, but edits the same file as Phase 2 (`reconcile.py`)
- **Phase 6 (Polish)**: depends on every story you intend to ship

### Within Each User Story

- Tests are written first and confirmed to FAIL before the implementation task
- The helper (T003) before any call site
- `ReconcileResult.reclaimed` (T015) before the sweep that increments it (T016)

### Parallel Opportunities

Genuinely few, and all of them are test files:

- **T007** (integration test) is parallel with **T006** (unit tests) — different files
- **T009** (`test_web_actions.py`) is parallel with the rest of US1 — different file
- **T011** (`test_web_actions.py`) is parallel with the rest of US2 — different file
- **T014** (integration test) is parallel with **T013** (unit tests) — different files
- **T019** and **T020** (two different docs files) are parallel with each other

**Not parallel, despite looking it**: every task touching `tests/unit/test_slot_reclamation.py`
(T002, T004, T005, T006, T010, T013, T017), and the two source files, each of which is edited by
more than one phase.

---

## Parallel Example: User Story 1

```bash
# After T003 (the helper) lands, these two test files can be written together:
Task: "Add cancel cases to tests/unit/test_slot_reclamation.py"          # T006
Task: "Add the capacity integration case to tests/integration/test_dispatch_capacity.py"  # T007
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. T001 — baseline
2. T002–T005 — the shared rule (**cannot be skipped**; US1 is a call site of it)
3. T006–T009 — cancel
4. **STOP and VALIDATE**: run quickstart scenario 1 with no daemon running
5. At this point issue #28's headline case is fixed and the `local` rehearsal works

### Incremental Delivery

1. Setup + Foundational → the rule exists and is proven safe
2. Add US1 → cancel releases the slot → **MVP**
3. Add US2 → abandon releases the slot → both reported routes covered
4. Add US3 → the sweep → the invariant holds by any route, and existing leaks are recovered
5. Polish → docs, the untouched-list check, full suite

### If you stop early

US1 alone fixes the reported bug. US1 + US2 close both routes named in the issue. Only US3
recovers a database that is *already* leaking — which, per the issue's own comment, yours is:
the verification round was worked around by raising `default_repo_max_sessions` rather than by
clearing the row, so the leak is still there and US3 is what gets that slot back.

---

## Notes

- `[P]` tasks touch different files and have no dependency on incomplete work
- Every task above names a concrete file path
- Commit after each task or logical group; messages explain why, not what
- The one thing not to get wrong: the rule is an **allow-list** of `dispatching`/`active`, not a deny-list of `TERMINAL_WORK_ITEM_STATES` — that constant is only `{done, abandoned}`, and the reported bug leaves the item `interrupted`, so an implementation written against it would miss the very case it is fixing (research R5)
