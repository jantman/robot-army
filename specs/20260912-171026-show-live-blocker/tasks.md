# Tasks: `show` Reports What Blocks an Item Now, Not What Blocked It Then

**Input**: Design documents from `specs/20260912-171026-show-live-blocker/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/show-output.md

**Tests**: required by the constitution (Development Workflow) — every changed unit ships with
unit tests, including the failure path (the check that cannot complete).

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

None — no dependency, module, table or config key is added.

## Phase 2: Foundational (blocks both stories)

- [X] T001 Add keyword-only `raise_anomalies: bool = True` to `check_gates`, `_check_recorded_location` and `_raise_location_anomaly` in `src/robot_army/dispatch.py`; with `False`, `_raise_location_anomaly` raises the same `DispatchBlocked(message)` without calling `db.raise_anomaly`. Docstring says why (research R3: a read-only render must not write; dispatch and retry keep reporting).
- [X] T002 Add frozen dataclass `LocalBlocker(reason: str | None, unresolved: bool)` and `_local_blocker(ctx, item, *, trust_file=None, raise_anomalies=True) -> LocalBlocker` in `src/robot_army/operations.py` beside `retry`: `repos_mod.resolve` → `None` gives `retry`'s existing "does not resolve to a clone any more" sentence with `unresolved=True`; otherwise `dispatch.check_gates(..., raise_anomalies=raise_anomalies)`, `DispatchBlocked` → `LocalBlocker(str(exc), False)`; pass → `LocalBlocker(None, False)`. Docstring cites FR-002 and research R2.
- [X] T003 Rewrite `retry`'s two pre-read refusals in `src/robot_army/operations.py` to call `_local_blocker` and branch on it, keeping every line, exit code, `retry.blocked` record and `data` identical (FR-008).
- [X] T004 Add a `raise_anomalies=False` test to `tests/unit/test_show_blocker.py` (new): a moved clone makes `check_gates` raise the same message with no anomaly row; the default still writes one.

**Checkpoint**: `uv run pytest tests/unit/test_operations_retry.py tests/unit/test_show_blocker.py` green; retry tests unmodified.

## Phase 3: User Story 1 — `show` names the current blocker (P1) 🎯 MVP

**Goal**: for a `failed` item, `show` reports `retry`'s local verdict as the current blocker, marked checked now, per contracts/show-output.md.

**Independent Test**: fail an item for a missing clone, restore it untrusted, run `show`: the trust failure is reported, `(checked now; not the reason recorded when it failed)`, and matches `retry`'s refusal.

- [X] T005 [US1] Add `_current_blocker(ctx, item) -> dict` in `src/robot_army/operations.py` returning the `current_blocker` shape from data-model.md: `not_checked` for any state but `failed`; otherwise `_local_blocker(ctx, item, raise_anomalies=False)`, catching only `BoundaryError` and `OSError` into `unknown` (research R4); `differs_from_recorded` when blocked and a non-empty stored `blocked_reason` differs.
- [X] T006 [US1] (As built: `current_blocker` is public and carries the contract's sentence as `summary`, which `_blocker_lines` and the web page both print.) Add `_blocker_lines(item, current) -> list[str]` in `src/robot_army/operations.py` producing exactly the contract's lines (blocked/same, blocked/differs, clear naming `robot-army retry <id>`, could-not-check, the conditional `recorded` line, and `(recorded, not re-checked)` for non-failed rows); replace `show`'s `blocked` line with it and add `result.data["current_blocker"]`.
- [X] T007 [US1] Tests in `tests/unit/test_show_blocker.py`: every contract row (blocked-same, blocked-differs, clear, could-not-check via a raising stub, unresolved repository, non-failed with stored reason, non-failed without); `show`'s blocker text equals `retry`'s refusal text for the same item (SC-001); `show` for a moved clone writes no anomaly and leaves the work-item row unchanged (SC-003); `failure` line unchanged; `--json` `current_blocker` present and `item.blocked_reason` still the stored value.

**Checkpoint**: US1 fully functional from the terminal.

## Phase 4: User Story 2 — the web item page agrees (P2)

**Goal**: the item page's `blocked` entry renders `current_blocker` with the terminal's wording.

**Independent Test**: with the US1 item, `/item/<id>` shows the current blocker checked now and the recorded failure; the JSON carries `current_blocker`.

- [X] T008 [US2] In `src/robot_army/web/pages.py`, render the item page's `blocked` `dd` from `payload["current_blocker"]`, reusing the text from T006's wording (a shared helper in `operations` returning the sentence, so the two surfaces cannot drift); the `failure` entry is unchanged.
- [X] T009 [US2] Tests in `tests/unit/test_show_blocker.py` (kept beside the feature's other tests): a failed item's page shows the current blocker with "checked now" and its recorded failure; the JSON payload carries `current_blocker`; rendering adds no anomaly.

## Phase 5: Polish

- [X] T010 [P] Update `docs/guide/operating.md`: `show`'s and the item page's `blocked` is checked now against `retry`'s local checks; `failure` is history; what "nothing on this machine blocks it now" means.
- [X] T011 Run `uv run pytest` (whole suite) and `uv run ruff check` if configured; fix anything red.

## Dependencies

- T001 → T002 → T003 → T004 (foundational, sequential: same files)
- T005 → T006 → T007 (US1)
- T008 depends on T006 (the shared wording); T009 on T008
- T010 independent once T006's wording is fixed; T011 last

## Parallel Opportunities

- T010 (docs) can run alongside T008/T009.
- Otherwise the work is in two files and sequential by nature.

## Implementation Strategy

MVP is Phase 2 + US1: the terminal stops lying. US2 then brings the web page in line from the same payload. Commit after each phase.
