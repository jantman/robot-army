# Tasks: Capacity breakdown that sums to its total

**Input**: Design documents from `specs/20260911-160717-capacity-breakdown-sum/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/output.md

**Tests**: required — the constitution requires unit tests for every changed unit of
behaviour, and the spec's SC-001 is the sum invariant asserted by test.

## Phase 1: Setup

None. No dependency, module, or configuration is added.

## Phase 2: Foundational

- [X] T001 Add `simulated: int = 0` and `in_flight: int = 0` to `CapacitySnapshot`, each with a
  `#:` comment saying what it counts and why it is a bare integer (FR-008), in
  `src/robot_army/capacity.py`
- [X] T002 In `snapshot()`, partition `unmatched` by `Session.hosted_by_simulation` into the two
  counts and pass them to the snapshot; extend the launch-window comment to say the partition is
  what lets the breakdown sum to `total` (issue #61), in `src/robot_army/capacity.py`
- [X] T003 Add a `components` property returning ordered `(label, count)` pairs — `ours`,
  `other` always; `simulated`, `in flight` when non-zero — in `src/robot_army/capacity.py`

**Checkpoint**: the snapshot carries all four terms.

## Phase 3: User Story 1 — every counted session appears (P1) 🎯 MVP

**Goal**: every surface's printed components sum to its printed total.

**Independent Test**: seed ours, the author's, simulated and unregistered real rows; each
surface's numbers sum to its total.

- [X] T004 [US1] Test the sum invariant for each population — registry ours/other, simulated
  rows, a real starting row, a mix, and the degraded `/proc` path — plus `components` and
  `describe()` omitting zero terms and naming non-zero ones, in `tests/unit/test_capacity.py`
- [X] T005 [US1] Render `components` in `describe()` so `status`'s line sums, in
  `src/robot_army/capacity.py`
- [X] T006 [US1] Print `simulated` and `in flight` lines in the `capacity` block and add
  `simulated`/`in_flight` to its JSON; add both keys to `_capacity_dict`, in
  `src/robot_army/operations.py`
- [X] T007 [US1] Render the non-zero new terms in the capacity pill, in
  `src/robot_army/web/html.py`
- [X] T008 [P] [US1] Test that the `capacity` terminal block's four lines sum to its first
  line's total and that JSON carries both keys, in `tests/unit/test_capacity_reporting.py`
- [X] T009 [P] [US1] Test that the pill names simulated sessions and sums, in
  `tests/unit/test_web_views.py`

## Phase 4: User Story 2 — simulated told apart from in flight (P2)

**Goal**: the two causes are named separately.

- [X] T010 [US2] Test that a simulated row counts as `simulated`, a real starting row as
  `in_flight`, and a `no-remote`-shaped row (`dry_run`, real pid) as `in_flight`, in
  `tests/unit/test_capacity.py`

(The implementation is T002; this phase is the test that pins the split.)

## Phase 5: User Story 3 — holds and the log carry the breakdown (P3)

- [X] T011 [US3] Render `components` in the global-cap hold detail, in
  `src/robot_army/ordering.py`
- [X] T012 [US3] Add `simulated` and `in_flight` to the `dispatch.at_capacity` detail, in
  `src/robot_army/dispatch.py`
- [X] T013 [P] [US3] Test the hold detail names simulated sessions, in
  `tests/unit/test_ordering.py` (its snapshot helper gains the two fields)
- [X] T014 [P] [US3] Test the `dispatch.at_capacity` record carries both counts and sums to
  `live_sessions`, in `tests/integration/test_dispatch_capacity.py`

## Phase 6: Polish

- [X] T015 [P] Explain the four lines — what `simulated` and `in flight` mean and why each
  persists or clears — in `docs/guide/3-selection.md`
- [X] T016 [P] Note the two new `dispatch.at_capacity` keys in `docs/guide/audit-log.md`
- [X] T017 Run `uv run pytest`; the whole suite must pass

## Dependencies

T001 → T002 → T003 → everything else. US1, US2 and US3 are independent after Phase 2.
T015–T016 depend only on the wording settled in T005–T006. T017 last.

## Parallel opportunities

T008/T009 (different test files); T013/T014; T015/T016.

## Implementation strategy

MVP is Phase 2 + US1: the `capacity` screen sums. US2 is a test on logic already written; US3
carries the same terms to the hold detail and the log.
