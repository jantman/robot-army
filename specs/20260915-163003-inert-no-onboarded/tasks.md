---
description: "Task list: say so when nothing is onboarded (issue #83)"
---

# Tasks: Say so when nothing is onboarded

**Input**: Design documents from `specs/20260915-163003-inert-no-onboarded/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/inert-installation.md

**Tests**: Required — the constitution's Development Workflow requires unit tests for every new
or changed unit of behaviour. All new tests go in one file, `tests/unit/test_nothing_onboarded.py`.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

None — no dependency, config key, table, or module is added.

## Phase 2: Foundational

- [ ] T001 Add the module constant `NOTHING_ONBOARDED` (the reason text from
  `contracts/inert-installation.md`) to `src/robot_army/intake.py`, next to `Resolution`, with a
  docstring comment saying why it is fixed text and why the comment compares against it
  (research R3).

## Phase 3: User Story 1 — `doctor` fails on an installation with nothing onboarded (P1)

**Goal**: `doctor` reports an `onboarded repositories` check, failing with exit 4 when empty.

**Independent Test**: `operations.doctor` against an empty `repos` table has
`"onboarded repositories"` in `failures` and `code == EXIT_CHECK_FAILED`; onboarding one repo
makes it pass with `1 onboarded`.

- [ ] T002 [P] [US1] Tests in `tests/unit/test_nothing_onboarded.py`: doctor fails the check
  with nothing onboarded (detail names `robot-army onboard` and the db path, code is
  `EXIT_CHECK_FAILED`, name in `failures`); passes with one onboarded (`1 onboarded`); a
  known-but-unresolvable row (no section, no `clone_path`) still fails (research R1). Use the
  `wire` monkeypatch pattern from `tests/unit/test_doctor_projects.py`.
- [ ] T003 [US1] In `operations.doctor` in `src/robot_army/operations.py`, compute
  `repos_mod.resolved_all(...)` once, append the `onboarded repositories` check before the
  per-repo lines, and reuse the mapping for the per-repo loop.

## Phase 4: User Story 2 — the held card names `robot-army onboard` (P1)

**Goal**: with nothing onboarded, resolution returns `NOTHING_ONBOARDED` with
`source="onboarding"`, the comment tells nobody to edit the card, and onboarding un-holds it.

**Independent Test**: resolve a card naming `jantman/robot-army` with nothing onboarded; the
reason is `NOTHING_ONBOARDED` and the posted comment contains no `robot-army: <repo>` advice.

- [ ] T004 [P] [US2] Tests in `tests/unit/test_nothing_onboarded.py`: scan path and
  `robot-army:` line path both return `NOTHING_ONBOARDED` / `source == "onboarding"` /
  `candidates == ()`; `_needs_info_comment(NOTHING_ONBOARDED)` names `robot-army onboard`, says
  nothing on the card needs to change, and does not contain the "Add a line to this card"
  advice; `_needs_info_comment` for any other reason is byte-for-byte the old text; with one
  repo onboarded, the existing "no onboarded repository could be identified" reason is
  unchanged; a card held with `NOTHING_ONBOARDED` and unchanged board activity is re-evaluated
  and linked once its repository is onboarded, and is left `unchanged` while nothing is.
- [ ] T005 [US2] In `resolve_repository` in `src/robot_army/intake.py`, return
  `Resolution(repo_key=None, reason=NOTHING_ONBOARDED, source="onboarding")` when `onboarded` is
  empty, before `_resolve_declarations` (research R2); update the `Resolution.source` docstring.
- [ ] T006 [US2] In `_needs_info_comment` in `src/robot_army/intake.py`, return the
  nothing-onboarded comment from the contract when `reason == NOTHING_ONBOARDED`.
- [ ] T007 [US2] In the held-card activity gate in `src/robot_army/intake.py` (step 5 of card
  evaluation), let a card whose `reason == NOTHING_ONBOARDED` through when
  `repos.resolved_all` is non-empty, reading the set only for such cards (research R7).
- [ ] T008 [US2] Run `uv run pytest tests/unit/test_repo_resolution.py
  tests/unit/test_card_activity.py tests/integration/test_card_needs_info.py
  tests/unit/test_ignored_lists.py` to confirm the existing reasons did not move (SC-003).

## Phase 5: User Story 3 — the planning record says onboarding is lost (P3)

- [ ] T009 [P] [US3] Extend the "With the database lost entirely" row in
  `specs/003-trello-source/data-model.md`: onboarding does not come back, deliberately (it is
  consent), and must be redone with `robot-army onboard` before anything is dispatched.
- [ ] T010 [P] [US3] Replace the "work items are also gone — that is expected" aside in
  `specs/003-trello-source/quickstart.md` scenario 5 with the full expected loss: work items and
  onboarding; `repos` shows every repository NOT ONBOARDED, `doctor` fails on
  `onboarded repositories`, held cards name `robot-army onboard`.

## Phase 6: Polish — the guide

- [ ] T011 [P] `docs/guide/1-setup.md`: after "run this first, every time", say `doctor` fails on
  `onboarded repositories` until the first `onboard`, and that this is expected.
- [ ] T012 [P] `docs/guide/2-intake.md`: in "When a card doesn't say enough", describe the
  nothing-onboarded hold and that onboarding alone un-holds it; add `"source": "onboarding"` to
  the `source` sentence; in "One card, one issue", say database loss also loses onboarding and
  `doctor` says so.
- [ ] T013 [P] `docs/guide/audit-log.md`: add `onboarding` to the `trello.evaluated` `source`
  values.
- [ ] T014 Run `uv run pytest` (whole suite) and `uv run ruff check` if configured.

## Dependencies & Execution Order

- T001 blocks T004–T007.
- US1 (T002–T003) is independent of US2 apart from nothing; US3 and the guide tasks are
  independent of code.
- T014 last.

## Parallel Opportunities

- T002 and T004 are the same new file, so write them together; T003 and T005–T007 touch
  different modules.
- T009–T013 are all different files.

## Implementation Strategy

US1 alone is the MVP — `doctor` drawing the eye is what would have ended the verification round's
confusion after one card rather than five. US2 removes the misleading advice. US3 and the guide
keep the record honest. All ship in one PR.
