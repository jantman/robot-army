---

description: "Task list for unique simulated issue numbers (issue #22)"
---

# Tasks: Unique simulated issue numbers

**Input**: Design documents from `specs/20260906-145911-unique-simulated-issue-numbers/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/simulated-issue-number.md](contracts/simulated-issue-number.md)

**Tests**: Required, not optional. The constitution's Development Workflow section requires unit
tests for every new or changed unit of behaviour, and failure- and interruption-path tests for
persistence and state-machine code. Both apply here.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths are given in every task

## Path Conventions

Single project: `src/robot_army/`, `tests/unit/`, `tests/integration/` at the repository root.

---

## Phase 1: Setup

**Purpose**: Project initialization.

No task. The project is initialised, the dependency set does not change (`research.md` R1: standard
library only), no schema migration is needed (`data-model.md`: no table is added or altered), and no
configuration key is touched — so `exampleconfig.py` and `share/config.example.toml` stay as they
are and `tests/unit/test_example_config_drift.py` keeps passing untouched. Recording the absence
here rather than inventing a placeholder task.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The allocation query and the connection that reaches it. Every user story depends on
one or both.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T001 Add `highest_simulated_issue_number(conn, *, repo_key)` to `src/robot_army/db.py`, beside `find_card_by_issue` (~line 634), returning `SELECT MAX(issue_number) FROM cards WHERE repo_key = ? AND dry_run = 1` as an `int | None`. Docstring says *why*: the unique index is `(repo_key, issue_number, dry_run)`, so an allocator that reads those same three columns cannot be refused by it; `MAX` ignores `NULL`, which is what makes the `needs_info` and mid-`creating` rows need no exclusion clause.
- [X] T002 [P] Add unit tests for the new helper to `tests/unit/test_db.py`: `None` for a repository with no simulated rows, the maximum for one holding several, the highest rather than the most recent when a gapped sequence exists (900001 and 900004 → 900004), `dry_run = 0` rows ignored, rows for another `repo_key` ignored, and cards with a `NULL` `issue_number` ignored rather than raising.
- [X] T003 Give `wire()` in `src/robot_army/effects.py` (line 204) a required `conn: sqlite3.Connection` parameter and pass it to `SimulatedIssueWriter` at line 250. The docstring gains one sentence on why a boundary takes a connection: the simulated writer stands in for GitHub's allocator, and the local database is where the numbers it has already issued are recorded.
- [X] T004 Thread the connection through `src/robot_army/daemon.py`: `wire_boundaries` (line 759) takes and forwards it, and its caller in `run_daemon` (line 722) passes the `conn` opened at line 719. The ordering comment in `run_daemon` stays true — the lock is still first, and the connection was already open before wiring.
- [X] T005 [P] Pass the connection from `build_context` in `src/robot_army/operations.py` (line 191); it is already opened above the `wire()` call in both the migrating and the `migrate=False` branch.
- [X] T006 [P] Update `wire()` call sites in `tests/unit/test_effects.py` and `tests/integration/test_effect_levels.py` (~line 113) to pass a connection, and assert in `tests/unit/test_effects.py` that omitting it is an error rather than a silent default — the point of research R3.

**Checkpoint**: `uv run pytest tests/unit/test_db.py tests/unit/test_effects.py` passes; the writer still counts, and nothing yet allocates.

---

## Phase 3: User Story 1 — A rehearsal files each card on its first pass (Priority: P1) 🎯 MVP

**Goal**: A simulated issue number is unused when it is minted, whatever the repository already
holds and whatever process mints it.

**Independent test**: Seed N simulated cards for one repository, run one intake pass over a fresh
card, and assert it is linked with `create_failures == 0` and a number none of the N holds — with N
above `CREATE_ANOMALY_THRESHOLD`, so the old behaviour would have raised an anomaly on the way.

- [X] T007 [US1] Replace `SimulatedIssueWriter.__init__`'s shared `_counter` in `src/robot_army/boundaries/github.py` (line 1105) with a required `conn: sqlite3.Connection`, and make `create_issue` (line 1125) allocate `max(SIMULATED_ISSUE_BASE, db.highest_simulated_issue_number(...) or 0) + 1`. Rewrite the docstring: it currently explains the fixed high offset, and must now also explain that the number is drawn from the record rather than from a count, and that this is what makes it unused rather than merely unusual. Import `db` locally if a module cycle demands it, and say so in a comment if it does.
- [X] T008 [P] [US1] Update the constructor call sites in `tests/unit/test_github.py` (lines 255-262) and `tests/unit/test_card_invariant.py` (lines 137, 230) to pass the connection each test already has.
- [X] T009 [P] [US1] Update the constructor call sites in `tests/integration/test_card_to_issue.py` (lines 212, 253, 280) to pass the test's connection.
- [X] T010 [US1] Extend `tests/unit/test_simulated_writers.py` with the contract's guarantees 1, 2, 3 and 5: a repository with no simulated rows gets `SIMULATED_ISSUE_BASE + 1`; a repository already holding 900001-900008 gets 900009 on the **first** call of a **freshly constructed** writer (the restart case, which is the defect); a gapped sequence allocates above the highest rather than filling the gap; and two repositories number independently. The existing `test_simulated_create_issue_numbers_do_not_repeat` stays and gains a recorded row between the two calls so that it exercises allocation rather than a counter.
- [X] T011 [US1] Add an integration test to `tests/integration/test_card_to_issue.py` for SC-001 and SC-002: seed more simulated card rows for one repository than `CREATE_ANOMALY_THRESHOLD`, run one intake pass over a new card, and assert the card reaches `LINKED`, `create_failures` is `0`, no `card_create_failing` anomaly was raised, and its number collides with none of the seeded rows. This is the test that fails against `main`.
- [X] T012 [US1] Add an integration test to `tests/integration/test_card_to_issue.py` for two cards resolved to the same repository in one pass: both linked, distinct numbers, no failure recorded against either (spec US1 acceptance scenario 2).

**Checkpoint**: US1 is independently deliverable. `uv run pytest tests/unit/test_simulated_writers.py tests/integration/test_card_to_issue.py` passes, and the eight-passes-over-nineteen-minutes behaviour is gone.

---

## Phase 4: User Story 2 — A card's number does not depend on unrelated simulated traffic (Priority: P2)

**Goal**: The number depends only on the record, never on how many simulated comments the process
happened to write first.

**Independent test**: File a card in a process that recorded several simulated comments and in one
that recorded none; the number is the same.

- [X] T013 [US2] Give `SimulatedIssueWriter.comment` in `src/robot_army/boundaries/github.py` (line 1109) its own counter, used only to make `#issuecomment-simulated-N` fragments distinguishable within a run. A short comment says why the two are separate: a comment fragment needs uniqueness within a process and nothing reads it back; an issue number needs uniqueness against the record. (Depends on T007, same class.)
- [X] T014 [P] [US2] Add unit tests to `tests/unit/test_simulated_writers.py`: a card filed after several `comment()` calls receives the same number as one filed with none, and two comments in one process still produce different fragments. The existing comment-body test keeps passing unchanged.

**Checkpoint**: `uv run pytest tests/unit/test_simulated_writers.py` passes; FR-005 holds.

---

## Phase 5: User Story 3 — The recorded reason describes what will actually happen (Priority: P3)

**Goal**: What a reader is told about recovery is true.

**Independent test**: Force a mapping refusal and assert the recorded reason names the holding card
and describes allocation rather than a "fresh number" the system never produced.

- [X] T015 [US3] Rewrite the message in `_mapping_conflict` in `src/robot_army/intake.py` (line 1267) so it states what the next pass does — allocate above the highest number recorded for that repository — while keeping the part that already earned its place: naming the card that holds the number. Keep it to one line a reader of `robot-army cards` can act on, per the contract.
- [X] T016 [US3] Rewrite the explanatory comment in `_perform_creation` in `src/robot_army/intake.py` (lines 1205-1212). It currently asserts that "the counter having advanced guarantees" a fresh number, which stops being true. It must say instead that allocation now makes the refusal unreachable in ordinary operation, and why the guard stays anyway: an escaped `IntegrityError` aborts the pass and strands the card, which is the silent gap the guard exists to prevent.
- [X] T017 [US3] Rework `test_a_restart_that_reissues_a_simulated_number_does_not_abort_the_cycle` and its `simulated_boundaries` helper in `tests/integration/test_card_interruption.py` (lines 444-471). The collision can no longer be produced by restarting the writer — that mechanism *is* the defect — so force it directly: a stub issue writer returning a number an existing row already holds, or a conflicting row inserted between the mint and the write. What the test proves must not change: the `IntegrityError` degrades to a recorded retry with a reason and a failure count, the remaining cards in the pass are still processed, and the card still reaches the anomaly threshold if it keeps failing. Its docstring must stop describing the restarting counter as the reachable case.
- [X] T018 [P] [US3] Add or extend a test in `tests/unit/test_card_invariant.py` asserting the refusal reason names the holding card and describes allocation, and containing no claim the system does not implement — the assertion that would have caught the wrong sentence.

**Checkpoint**: `uv run pytest tests/integration/test_card_interruption.py tests/unit/test_card_invariant.py` passes; SC-005 holds.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T019 [P] Add a short paragraph to the "One card, one issue" section of `docs/guide/2-intake.md` explaining that below `live` the number in the index is invented rather than GitHub's, that it is allocated above the highest already recorded for that repository so a rehearsal files each card on its first pass, and that `purge-simulated` restarts the numbering. Required by CLAUDE.md — intake is the pipeline stage this change affects. `README.md` is not touched and must stay under 150 lines.
- [X] T020 [P] Check `docs/guide/state.md` and `docs/guide/audit-log.md` need no edit and say so in the commit message if they do not: no table, index or record shape changes (`data-model.md`), and `github.issue.create` keeps its existing fields.
- [X] T021 Run `uv run pytest` — the whole suite, which is the gate (SC-006) — and whatever linting the project runs, and fix what they find.
- [X] T022 Walk [quickstart.md](quickstart.md) section 1, confirming each row of its table maps to a test that now exists.

---

## Dependencies & Execution Order

```
Phase 2 (T001-T006)  ──►  Phase 3 US1 (T007-T012)  ──►  Phase 4 US2 (T013-T014)
       foundational              MVP                          same class as T007
                                   │
                                   └────────────────────►  Phase 5 US3 (T015-T018)
                                                                   │
                                                            Phase 6 (T019-T022)
```

- **T001 blocks T007** — the writer calls the helper.
- **T003 blocks T004, T005, T006** — the signature must change before its callers do.
- **T007 blocks T013** — both edit `SimulatedIssueWriter`; sequential, not parallel.
- **T007 blocks T017** — the reworked collision test must be written against the new writer.
- **US3 does not depend on US2.** It could be delivered second if the message mattered more than
  the counter split, though the order above matches the spec's priorities.
- **T021 is last** and depends on everything.

## Parallel Opportunities

- Phase 2: T005 and T006 alongside T004 once T003 lands; T002 alongside all of them.
- Phase 3: T008 and T009 together — different test files, both mechanical.
- Phase 5: T018 alongside T015/T016 — different file.
- Phase 6: T019 and T020 together.

## Implementation Strategy

**MVP is Phase 2 + Phase 3.** That alone closes the issue's headline defect: cards file on the
first pass and no anomaly is raised for a collision. US2 and US3 are the two smaller truths the
issue also reports — a number that should not depend on unrelated traffic, and a message that
should not describe a strategy the system lacks — and each is independently deliverable on top.

Commits are atomic per the constitution: the foundational plumbing, the allocation, the counter
split, the message, and the documentation are five changes with five different reasons.

## Deviations from the plan, and why

- **T002 landed in `tests/unit/test_card_invariant.py`, not `tests/unit/test_db.py`.** There is no
  `test_db.py`; the card-mapping database tests live in `test_card_invariant.py`, which already
  owns `idx_cards_issue` and has the `linked_row` helper the new cases needed. Creating a file to
  match a path in a plan would have split one subject across two.
- **T018 landed in `tests/integration/test_card_interruption.py`.** The assertion is about the
  reason recorded after a refusal, and that file is the only place a refusal is now produced —
  asserting the message where the collision is forced keeps the test and its setup together.
- **Extra test, not planned: `test_a_restart_alone_no_longer_collides`.** The reworked collision
  test forces its collision with a stub, so on its own it no longer proves the headline defect is
  gone. This one asserts the plain restart case that used to fail.

