# Tasks: The Label Is Re-checked Before Dispatch, Not Only at Discovery

**Input**: Design documents from `specs/20260912-073115-label-rechecked-at-dispatch/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/output.md

**Tests**: required. The constitution requires unit tests for every changed unit of behaviour,
and a failure-path test for code that parses stored data (malformed labels, R3).

## Phase 1: Setup

None. No dependency, module, or configuration is added.

## Phase 2: Foundational

- [ ] T001 Add `NOT_LABELLED = "not_labelled"` to `HoldReason` directly after `HELD`. Add a
  bullet to its docstring justifying the rank: below `paused`/`held`, above the capacity
  reasons, because a cap reason would invite raising the cap (R2, issue #62). File:
  `src/robot_army/ordering.py`.
- [ ] T002 Add a pure `label_hold(item, label) -> str | None`. It returns the detail string
  from `contracts/output.md` when `label` is not among the item's stored labels, including
  when they do not decode to a list of strings (R3), and `None` otherwise. Docstring: why
  stored labels rather than a live read (R1). File: `src/robot_army/ordering.py`.

**Checkpoint**: the reason and its predicate exist. Nothing consults them yet.

## Phase 3: User Story 1 — de-scoped queued work does not dispatch (P1) 🎯 MVP

**Goal**: a `ready` item whose issue lacks `[github] label` is held, reported, and skipped.

**Independent Test**: seed `ready` items with `["robot-army"]`, configure `label = "scratch"`.
`plan` holds each one with `not_labelled`, and a dispatch pass starts nothing.

- [ ] T003 [US1] In `_hold_for`, splice the check in:
  1. Return `launch[0]` first only when its reason is `PAUSED` or `HELD`.
  2. Then return `NOT_LABELLED` with `label_hold`'s detail when it applies.
  3. Then any remaining launch hold, then the queue reasons as today.

  Update `launch_holds`'s and `_hold_for`'s docstrings so they no longer say the first five
  declared reasons are the launch holds. File: `src/robot_army/ordering.py`.
- [ ] T004 [P] [US1] Create `tests/unit/test_label_hold.py` with these tests:
  - A de-scoped item is held with `not_labelled`, and the detail names the configured label
    and the stored labels.
  - An item carrying the label is not held.
  - `(has: none)` is shown for an empty list.
  - Malformed JSON and a non-list value are both held with the "could not be read" detail.
  - It outranks `global_cap`, `capacity_unobservable`, `repo_cap`, `awaiting_merge`,
    `off_column` and `preparation_failed`.
  - `paused` and `held` outrank it.
  - Reverting the configured label lifts it (FR-005: nothing is written to the row).
- [ ] T005 [P] [US1] Add a test in `tests/integration/test_dispatch_capacity.py` covering
  SC-001 and US1 AS5. It seeds three items:
  1. a de-scoped item at the head
  2. an item carrying the configured label
  3. another de-scoped item

  With free capacity, `select_and_dispatch` dispatches only the second. With every candidate
  de-scoped, it dispatches nothing and records `dispatch.at_capacity` with
  `reason: "not_labelled"`.
- [ ] T006 [US1] Confirm `launch_holds` is unchanged for `resume`/`restart`. Add one assertion
  to `tests/unit/test_launch_gate.py` that a de-scoped `interrupted` item is not refused by
  `check_launch_gate` (FR-010).

## Phase 4: User Story 2 — labelling the issue releases the queued item (P2)

**Goal**: a poll that re-sees a `ready` item's issue brings its stored labels up to date.

**Independent Test**: with an item held for a missing label, deliver a listing containing its
issue carrying the label; after the poll the item is not held.

- [ ] T007 [US2] Change the existing-row branch of `poll_repo`. When `existing.state` is
  `READY` and the listing's label set differs from the stored set (unreadable stored labels
  count as differing):
  - write the listing's labels with `db.update_work_item_columns`, in a transaction, and
  - record `poll.labels_refreshed` with `old`/`new`, `target=source_id` and the row's
    `dry_run` (R4).

  Add a comment on why only `ready` and why sets. File: `src/robot_army/poll.py`.
- [ ] T008 [P] [US2] Add tests in `tests/unit/test_poll.py`:
  - differing labels on a `ready` row are written and recorded, and `plan` then shows no hold
  - identical labels in a different order write and record nothing
  - a row in another state (e.g. `active`) is not refreshed

## Phase 5: User Story 3 — the mismatch is announced at startup (P3)

**Goal**: one warning at startup when queued work does not carry the configured label.

- [ ] T009 [US3] Add `warn_about_label(conn, audit, config) -> str | None` beside
  `warn_about_environment`. It:
  - lists `ready` items with `include_simulated=True`
  - collects those `ordering.label_hold` holds
  - writes one `daemon.label_warning` record (`outcome="error"`, detail `label`, `count`,
    `item_ids`, `warning`) and returns the message, or returns `None`

  Call it from `Daemon.startup` directly after `warn_about_environment`. File:
  `src/robot_army/daemon.py`.
- [ ] T010 [P] [US3] Add tests in `tests/integration/test_daemon_loop.py`: startup with
  de-scoped `ready` items writes exactly one `daemon.label_warning` naming the count and ids;
  startup with none writes no such record.

## Phase 6: Polish

- [ ] T011 [P] In `docs/guide/3-selection.md`, add the new row to the reasons table (and fix
  its "Five things decide" count). Add a short section explaining that queued items are held,
  not abandoned, when the label changes, why the reason ranks above the cap, and the two ways
  to release them.
- [ ] T012 [P] In `docs/guide/2-intake.md`, extend "Changing the label" to say that already
  queued items are held and how to release them, linking to the selection page.
- [ ] T013 [P] Add `poll.labels_refreshed` and `daemon.label_warning` to
  `docs/guide/audit-log.md`, and note that `dispatch.at_capacity`'s `reason` may be
  `not_labelled`.
- [ ] T014 Run `uv run pytest`; the whole suite must pass.

## Dependencies

- T001 → T002 → everything else.
- US1 (T003–T006), US2 (T007–T008) and US3 (T009–T010) are independent after Phase 2, apart
  from T008's final `plan` assertion, which needs T003.
- T011–T013 depend only on the wording in `contracts/output.md`.
- T014 is last.

## Parallel opportunities

T004/T005 (different test files); T008 and T010 alongside US1; T011/T012/T013.

## Implementation strategy

The MVP is Phase 2 plus US1: de-scoped work stops dispatching, which is the reported harm.
US2 makes the hold releasable without reverting the configuration. US3 announces it at the
moment the operator can act.
