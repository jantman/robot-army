# Tasks: A Board Anomaly Always Names the Board's Current Failure

**Input**: Design documents from `specs/20260913-090239-board-anomaly-restate/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/board-anomaly-restatement.md

**Tests**: Required. The constitution requires unit tests for every changed unit of behaviour, and
failure and interruption paths for persistence.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)

## Phase 1: Setup

No setup: no dependency, config key, migration or module is added.

## Phase 2: Foundational (the two database functions)

**Purpose**: The lookup and the guarded rewrite every story's behaviour sits on.

- [ ] T001 Add `open_anomaly(conn, *, kind, entity_type, entity_id, dry_run=False) -> Anomaly | None` in `src/robot_army/db.py`, next to `raise_anomaly`: the one unacknowledged, unresolved row the partial unique index allows, matched with the same `COALESCE` on the entity columns the index uses. The docstring says why it exists (research R4).
- [ ] T002 Add `restate_anomaly(conn, anomaly_id, detail) -> bool` in `src/robot_army/db.py`, next to `resolve_anomaly`: `UPDATE anomalies SET detail = ?, detected_at = ? WHERE id = ? AND acknowledged_at IS NULL AND resolved_at IS NULL`, returning whether it changed a row. The docstring says why `detected_at` moves (R3) and why the open guard is in the statement rather than trusted to the caller's lookup (R6).
- [ ] T003 [P] Unit tests in `tests/unit/test_anomaly_resolution.py`: `open_anomaly` finds the open row and returns `None` for an acknowledged, a resolved, a rehearsed (`dry_run=True`) and another entity's row; `restate_anomaly` rewrites `detail` and `detected_at` of an open row, keeps its id, and returns `False` without writing for an acknowledged or resolved row (the lost race with `--acknowledge`); a `db.transaction` that raises after `restate_anomaly` leaves the row's previous detail and detection time (interruption path).

**Checkpoint**: the functions exist and are tested on their own.

## Phase 3: User Story 1 - The open board anomaly says what is wrong now (P1) 🎯 MVP

**Goal**: A board failing for a new reason rewrites its open anomaly (contract C3, C4, C5).

**Independent Test**: raise for "board is private", then call again with "tag exists" failing, and list anomalies.

- [ ] T004 [US1] Change `board_disabled_anomaly` in `src/robot_army/intake.py`: inside the existing `db.transaction`, look up the board's open `board_precondition` row with `open_anomaly`; with none, `raise_anomaly` as today; with one whose parsed detail differs from the new detail (or does not parse as an object with `failed_checks`), `restate_anomaly` it and, only if that returned `True`, write `anomaly.restated` (outcome `ok`, entity `anomaly/<id>`, detail `kind`, `anomaly_entity_id`, `previous_failed_checks`, `failed_checks`, `previous_detected_at`, `reason`; `previous_detail_unreadable: true` with `previous_failed_checks: null` for an unreadable previous detail). The `trello.board.check` record after the transaction is unchanged. Update the docstring with the issue #73 reasoning.
- [ ] T005 [US1] Tests in `tests/unit/test_board_preconditions.py`: C3 (different names → one row, same id, names only "tag exists", detected_at moved); both checks failing → one row naming both; C4 (same name, different detail text → restated); C5 (stored detail unreadable → restated, record flags it); C6 (acknowledged row untouched, new row raised); C7 (another board's open row untouched); the issue's sequence end to end through `Daemon._check_board` (public, then label renamed with no acknowledgement → `list_anomalies` shows one board anomaly naming only the label, SC-001); a restated row appears in `anomalies --since` via `operations`' window filter.

**Checkpoint**: US1 is fixed and demonstrable.

## Phase 4: User Story 2 - A board that stays broken stays quiet (P1)

**Goal**: An unchanged failure writes nothing (C2).

**Independent Test**: five identical failures.

- [ ] T006 [US2] Extend `test_a_repeated_failure_does_not_accumulate_anomaly_rows` in `tests/unit/test_board_preconditions.py` to assert the row's `detail` and `detected_at` are unchanged and no `anomaly.restated` record was written (SC-002); add a test that two successive restatements still leave exactly one open row.

## Phase 5: User Story 3 - A changed reason can be reconstructed (P2)

**Goal**: The overwritten reason survives in the log (FR-005, SC-003).

- [ ] T007 [US3] Test in `tests/unit/test_board_preconditions.py`: one restatement writes exactly one `anomaly.restated` record whose `previous_failed_checks` equals the overwritten row's `failed_checks`, whose `previous_detected_at` equals its old `detected_at`, and whose `entity_id` is the anomaly's id; a lost race (row acknowledged between lookup and update, simulated by acknowledging it and calling `restate_anomaly` directly) writes no record.

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T008 [P] `docs/guide/operating.md`: add a `board_precondition` entry to "Anomalies worth understanding" saying the open board anomaly is restated in place when the board's failure changes, what moves (`detected_at`), what stays (the id), and that an unchanged failure writes nothing (FR-008).
- [ ] T009 [P] `docs/guide/audit-log.md`: an "issue #73" section documenting `anomaly.restated` — when, its detail fields, and that an unchanged failure writes none (FR-008).
- [ ] T010 [P] `docs/guide/state.md`: in the `anomalies` section, say that a `board_precondition` row's `detail` and `detected_at` are rewritten while it is open, and why (the only kind whose text is restated rather than first-detection evidence).
- [ ] T011 Run `uv run pytest` and `uv run ruff check` (if configured) and fix anything that fails (SC-004).

## Dependencies & Execution Order

- T001, T002 → T003 and T004. T004 → T005, T006, T007.
- T008–T010 depend only on the design and can run in parallel with anything.
- T011 last.

### Parallel Opportunities

- T003 alongside T004 (different files).
- T008, T009, T010 together.

## Implementation Strategy

US1 with the foundational functions is the fix (MVP). US2 guards the existing dedup against
regression, US3 proves the reconstruction standard; both are tests over T004's behaviour, so the
whole feature lands in one PR.
