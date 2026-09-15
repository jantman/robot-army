---
description: "Tasks for issue #113 — a hand-deleted worktree on a finished item is reported"
---

# Tasks: A hand-deleted worktree on a finished item is reported

**Input**: `specs/20260915-174645-terminal-prunable-worktree/` — plan.md, spec.md, research.md,
data-model.md, contracts/manual-removal-record.md (M1–M16), quickstart.md

**Tests**: required — the constitution requires unit tests for every changed unit of behaviour,
and failure/interruption paths for persistence.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

No setup: no dependency, migration or configuration change (plan.md).

---

## Phase 2: Foundational

- [X] T001 Add `WorkItem.worktree_reclaimed` (`cleanup_state in ("done", "branch_retained")`) beside `cleanup_pending`, with a docstring saying why `retained`/`skipped` do not count, in src/robot_army/models.py (research R4)
- [X] T002 [P] Rename `cleanup._branch_exists` to `branch_exists` and update its caller, docstring noting its second caller, in src/robot_army/cleanup.py (research R7)
- [X] T003 Unit-test `worktree_reclaimed` for every cleanup state including `NULL` in tests/unit/test_terminal_prunable_worktree.py

**Checkpoint**: the predicate exists; nothing reads it yet.

---

## Phase 3: User Story 1 — A finished item's missing worktree is reported (P1) 🎯 MVP

**Goal**: the sweep reports a finished item whose directory is missing and unaccounted for.

**Independent Test**: seed a `done` item with a missing path and no record; one pass raises one
`prunable_worktree`.

- [X] T004 [US1] Tests in tests/unit/test_terminal_prunable_worktree.py: `done` and `abandoned` items with a missing directory and `NULL`/`retained`/`skipped` records are reported (M1, M3); `done`/`branch_retained` items are not, over several passes, whatever the state (M2); repeated passes create no second open anomaly; the note for `done` names `worktree remove <id>` and `cleanup <id>`, for `abandoned` only the first, for an unfinished item it is unchanged (M4); an item with a reclaimed record does not cause its clone to be listed (M2)
- [X] T005 [US1] In `_sweep_worktrees`, drop the terminal-state filter and skip `item.worktree_reclaimed` items; compose the note by state; update the docstring to say why the filter was wrong and what replaced it, in src/robot_army/reconcile.py
- [X] T006 [US1] Confirm the existing sweep test (`test_a_missing_worktree_directory_is_flagged_not_removed`) and `test_anomaly_resolution.py`'s `prunable_worktree` case still pass unchanged, in tests/unit/test_reconcile.py

**Checkpoint**: scenario 10 item d is reported with cleanup off.

---

## Phase 4: User Story 2 — `worktree remove` records what it did (P1)

**Goal**: a manual removal of a finished item writes cleanup's record and keeps the path.

**Independent Test**: `worktree remove <id>` on a clean `done` item → `cleanup_state` `done`, path
kept, not a cleanup candidate, next pass raises nothing.

- [X] T007 [US2] Tests in tests/unit/test_terminal_prunable_worktree.py: success on `done` and `abandoned` records `done`, reason ending `— by \`robot-army worktree remove\``, `cleaned_at` set, path and branch kept (M5, M7); branch that git will not delete records `branch_retained` (M6); `--force` puts `(forced)` in the reason; a simulated removal says `worktree removal simulated; branch deletion simulated`; an unfinished item (`failed`, `interrupted`, `awaiting_review`) has its path cleared and no record (M8); each refusal — live session, git dirty-tree, directory survived, unresolved repo, wrong confirmation — writes no column (M9); outcome and `result.data` carry `cleanup_state` (M13, M16); after recording, `db.list_cleanup_candidates` excludes the item and a reconciliation pass raises nothing for it
- [X] T008 [US2] In `worktree_remove`, after `_remove_checkout` succeeds: for `TERMINAL_WORK_ITEM_STATES` compose the reason, choose `done`/`branch_retained`, and write it with `db.record_cleanup` in one transaction; otherwise clear `worktree_path` as before; set `outcome["cleanup_state"]` and `result.data["cleanup_state"]`; update the docstring, in src/robot_army/operations.py
- [X] T009 [US2] Make `_remove_checkout` report enough for T008 to compose the reason (whether the worktree was already gone, whether the branch was already gone) through `result.data`, in src/robot_army/operations.py
- [X] T010 [US2] Update assertions that a `done` item's path is cleared after removal — tests/unit/test_worktree_remove_guard.py (lines ~248, ~496), tests/unit/test_simulated_wording.py (~110), tests/integration/test_worktree_removal.py (~84) — to assert the path is kept and the record written

**Checkpoint**: manual and automatic removal leave the same row.

---

## Phase 5: User Story 3 — A reported item can be settled (P2)

**Goal**: the command the anomaly names works on an absent directory and stops the reports.

**Independent Test**: `done` item with a missing directory → `worktree remove <id>` exits 0 and
records; after acknowledging, a pass raises nothing.

- [X] T011 [US3] Unit tests in tests/unit/test_terminal_prunable_worktree.py with a VCS that refuses like git's "not a working tree": an absent directory is treated as removed, `worktree_already_gone` recorded, branch half runs (M10); a present directory with the same refusal is still refused as `git`; an absent branch is not deleted, says "already gone", exits 0, records `done`, `branch_already_gone` (M11); `branch_exists` unanswerable still attempts the delete; a reclaimed item with no directory is refused `already_removed`, exit 3, no git call, no session consulted (M12, M15); the acknowledged anomaly is not raised again after settling
- [X] T012 [US3] Integration tests with real git in tests/integration/test_worktree_removal.py: a `done` item whose directory was `rm -rf`'d is settled by `worktree remove <id>` both before and after `git worktree prune` — branch gone, git's record gone, row recorded
- [X] T013 [US3] In `_remove_checkout`, treat a git refusal with `vcs.worktree_exists(path)` false as removed (`worktree_already_gone`, the "was already gone" line), and ask `cleanup_mod.branch_exists` before `delete_branch` (`branch_already_gone`); document both in the docstring, in src/robot_army/operations.py
- [X] T014 [US3] In `worktree_remove`, refuse `already_removed` when `item.worktree_reclaimed` and the directory is absent, inside the audit action and before repo resolution, naming the recorded state, time and reason, in src/robot_army/operations.py

**Checkpoint**: every reported item has a one-command settlement.

---

## Phase 6: Polish & Documentation

- [X] T015 [P] docs/guide/5-outcome.md: `worktree remove` writes the cleanup record for finished items and keeps the path; an absent directory or branch is not a refusal; `already_removed`
- [X] T016 [P] docs/guide/operating.md: `prunable_worktree` now covers finished items, what accounts for a missing directory, and how to settle it (acknowledging alone does not)
- [X] T017 [P] docs/guide/state.md: the cleanup columns' second writer; correct "`worktree_path` and `branch` are never nulled"; interruption rows for a manual removal killed between the removals and before the record
- [X] T018 [P] docs/guide/audit-log.md: "The issue #113 records" — `cleanup_state`, `worktree_already_gone`, `branch_already_gone`, `already_removed`
- [X] T019 Run `uv run pytest` and `uv run ruff check` (if configured); all pass
- [X] T020 Mark tasks complete in this file

---

## Dependencies & Execution Order

- T001–T002 before any story. T003 after T001.
- US1 (T004–T006) needs only T001. It is safe alone: today's manual removal clears the path, so
  nothing it removed can be reported.
- US2 (T007–T010) needs T001; T009 before T008.
- US3 (T011–T014) needs T002 and US2's T009 (shared helper).
- Docs (T015–T018) after the stories they describe; parallel with each other.

## Parallel Opportunities

- T001 ∥ T002 (different files).
- T015 ∥ T016 ∥ T017 ∥ T018 (different files).
- Story tests share `tests/unit/test_terminal_prunable_worktree.py`, so they are sequential.

## Implementation Strategy

MVP is US1: the reported defect, safe under the current removal behaviour. US2 then makes manual
removal leave cleanup's record, and US3 makes every report settleable. One commit per story, then
docs.
