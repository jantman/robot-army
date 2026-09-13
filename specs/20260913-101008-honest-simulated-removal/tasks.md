# Tasks: A Simulated Removal Says It Was Simulated

**Input**: Design documents from `specs/20260913-101008-honest-simulated-removal/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/simulated-wording.md

**Tests**: Required. The constitution requires unit tests for every changed unit of behaviour, and the
spec asks for a cross-verb test (FR-011).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US4)

## Phase 1: Setup

No setup: no dependency, config key, migration or module is added.

## Phase 2: Foundational (the boundary says what it is)

**Purpose**: the one fact every story's wording hangs on (research R1, R3).

- [ ] T001 Declare `simulated: bool` on the `VersionControl` protocol in `src/robot_army/boundaries/__init__.py`, with a short docstring note saying callers word their reports from it (issue #70) and it is constant for the object's life.
- [ ] T002 Set `simulated = False` on `GitVersionControl` and `simulated = True` on `SimulatedVersionControl` in `src/robot_army/boundaries/git.py`; extend `SimulatedVersionControl`'s docstring with one paragraph on why callers need it (the removal verbs, issue #70).
- [ ] T003 [P] Add `real_from(boundary: str) -> EffectLevel` beside `is_real` in `src/robot_army/effects.py`: the first `EffectLevel` in declaration order in `REAL_AT[boundary]`, `KeyError` naming an unknown boundary as `is_real` does. Docstring: derived for the reason `consequences()` is.
- [ ] T004 Declare `simulated = False` on the real-acting fakes: `ListingVcs` in `tests/unit/test_orphan_worktrees.py`, `DeletingVcs` in `tests/unit/test_purge_worktrees.py`, and `FakeVcs` in `tests/unit/test_cleanup.py` (with a constructor argument `simulated: bool = False`), each with a one-line comment why.
- [ ] T005 [P] Tests in `tests/unit/test_effects.py`: `real_from("version_control") is EffectLevel.LOCAL`; for every boundary in `REAL_AT`, `real_from` is in its set and no earlier level is; the wired version-control boundary's `simulated` is `not is_real("version_control", level)` at every level.

**Checkpoint**: the boundary reports itself; nothing prints differently yet.

## Phase 3: User Story 1 - `worktree remove` says "would" (P1) 🎯 MVP

**Goal**: contract W1–W4.

**Independent Test**: at `plan`, `worktree remove <id>` over a simulated item with no directory.

- [ ] T006 [US1] Add `_simulated_note(ctx) -> str` in `src/robot_army/operations.py` near `_remove_checkout`, returning the contract's `NOTE` line from `ctx.effect_level` and `effects.real_from("version_control")`.
- [ ] T007 [US1] In `_remove_checkout` in `src/robot_army/operations.py`: read `vcs.simulated`; set `outcome["simulated"]` and `result.data["simulated"]`; on success print `would remove worktree P` / `would delete branch B` then `_simulated_note(ctx)` when simulated, and the unchanged lines otherwise. Update the docstring's "report follows the disk" paragraph with the issue #70 half.
- [ ] T008 [US1] Create `tests/unit/test_simulated_wording.py` with a `plan`-level context helper (`EffectLevel.PLAN`, `make_boundaries(audit, level=EffectLevel.PLAN, vcs=SimulatedVersionControl(audit))`) and tests for W2 (lines exactly `would remove…`, `would delete…`, the note naming `plan` and `local`; exit 0; `data["simulated"]` true; the `worktree.remove` outcome record carries `simulated: true`; the row's path is cleared), W3 (no branch → no branch line), W4 (a real directory → still the `directory_survived` refusal), and W1 (a real-acting boundary → unchanged lines, `simulated: false`).

**Checkpoint**: the reported defect is fixed.

## Phase 4: User Story 2 - `cleanup` neither says nor records it (P1)

**Goal**: contract W5–W8.

**Independent Test**: at `plan`, `cleanup` over a finished simulated item with no directory, and one with a real directory.

- [ ] T009 [US2] In `src/robot_army/cleanup.py`: add module constant `SURVIVED_REASON` (the exact sentence `_remove_checkout` uses today) and make `_remove_checkout` in `src/robot_army/operations.py` use it via `cleanup_mod.SURVIVED_REASON`.
- [ ] T010 [US2] In `clean_item` in `src/robot_army/cleanup.py`: after a reported removal, if `Path(item.worktree_path).is_dir()`, `_retain(... RETAINED, SURVIVED_REASON)` without touching the branch; when `vcs.simulated`, use `worktree removal simulated` for the removal half and `branch deletion simulated — {evidence}` for the final `done`. Add `simulated: bool = False` to `Decision` and set it on every decision reached through the boundary's removal (split the post-removal half into a helper and `replace(..., simulated=vcs.simulated)` its result). Docstring the guard with the #59/#70 reasoning.
- [ ] T011 [US2] In `cleanup_now` in `src/robot_army/operations.py`: add `simulated` to each decision's data; when any decision is simulated, the summary reads `N of M considered item(s) would have their worktree removed` followed by `_simulated_note(ctx)`.
- [ ] T012 [US2] Tests in `tests/unit/test_cleanup.py`: a surviving directory (`tmp_path`) → `retained` with `SURVIVED_REASON`, no delete attempted, `worktree_removed` false, recorded `cleanup_state` is `retained`; `FakeVcs(simulated=True)` → W5 and W6 reasons, `Decision.simulated` true, no "worktree removed" or "branch removed" in the reason; ineligible decisions are `simulated` false; real boundary reasons unchanged (W8).
- [ ] T013 [US2] Tests in `tests/unit/test_simulated_wording.py`: `operations.cleanup_now` at `plan` → the "would have their worktree removed" summary and the note; data decisions carry `simulated: true`.

## Phase 5: User Story 3 - `worktree prune` does not claim to have checked (P2)

**Goal**: contract W9.

- [ ] T014 [US3] In `worktree_prune` in `src/robot_army/operations.py`: when `vcs.simulated`, print `<repo>: not checked — pruning is simulated` per repository then `_simulated_note(ctx)`; add `simulated` to the data. Real path unchanged.
- [ ] T015 [US3] Tests in `tests/unit/test_simulated_wording.py`: W9 at `plan` with one onboarded repository; no line says "nothing to prune"; data `simulated` true.

## Phase 6: User Story 4 - The destructive verbs agree (P2)

**Goal**: the cross-verb rule (FR-011, research R5).

- [ ] T016 [US4] In `tests/unit/test_simulated_wording.py`: one parametrised test over `cancel` (a running session cancelled through `SimulatedSessionHost`), `worktree remove`, `worktree prune` and `cleanup`, all at `plan`, asserting the output contains "simulated" and that every line containing `removed worktree`, `deleted branch`, `worktree removed`, `branch removed`, `nothing to prune`, `had their worktree removed` or `stopped session` also contains "simulated". Docstring names the issue and why the phrase list is the real path's vocabulary.

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T017 [P] `docs/guide/5-outcome.md`, "Cleaning up": a paragraph beside "A removal is reported only if the directory is gone" saying that below `local` the removal verbs say "would", with the note line, and `cleanup` records the reason as simulated and retains a real directory still on disk (FR-012).
- [ ] T018 [P] `docs/guide/audit-log.md`: an "issue #70" section: `worktree.remove`'s outcome gains `simulated` once git is reached; a cleanup survivor is an ordinary `cleanup.retained` with the survivor reason (FR-012).
- [ ] T019 Run `uv run pytest` and `uv run ruff check` (if configured) and fix anything that fails (SC-005), including existing tests whose simulated-boundary output changed wording.

## Dependencies & Execution Order

- T001, T002 → everything. T003 → T006. T004 before running any test.
- US1 (T006–T008) → US2 (T009 touches `_remove_checkout`; T011 uses `_simulated_note`) and US3.
- US4 (T016) after US1–US3.
- T017, T018 depend only on the design. T019 last.

### Parallel Opportunities

- T003 and T005 alongside T001/T002 (different files).
- T012 alongside T011; T014 alongside US2.
- T017 and T018 together.

## Implementation Strategy

US1 is the reported defect and the MVP. US2 carries the same fix into `cleanup`, where it is also a
persisted-state fix; US3 and US4 are small and land in the same PR.
