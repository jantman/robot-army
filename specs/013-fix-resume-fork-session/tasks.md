---

description: "Task list for 013-fix-resume-fork-session"
---

# Tasks: `resume` That Actually Resumes, and a Failure That Actually Fails

**Input**: Design documents from `/specs/013-fix-resume-fork-session/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Test tasks are **required**, not optional. The constitution's Development Workflow
states "Unit tests are required. Every new or changed unit of behavior MUST ship with unit
tests", and adds that state machines and recovery logic "MUST additionally carry tests
exercising their failure and interruption paths". This feature is entirely a state machine's
failure path.

**Organization**: Grouped by user story so each can be implemented and verified independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: US1, US2, US3 — maps to the user stories in [spec.md](./spec.md)

## Path Conventions

Single Python package at the repository root: `src/robot_army/`, `tests/unit/`,
`tests/integration/`. Paths below are repository-relative.

**Note on parallelism**: this feature is small and concentrated. US1 and US2 both edit
`src/robot_army/dispatch.py`, in different functions, so they are **not** parallelisable
despite being independent stories. Genuine `[P]` opportunities are marked where they exist and
are deliberately few — claiming more would be dishonest about a 30-line change.

---

## Phase 1: Setup

**Purpose**: Establish the baseline, so any later failure is attributable to this feature.

- [X] T001 Run `uv run pytest` and `uv run ruff check .` from the repository root and confirm both are green before making any change; record the pass/fail counts for comparison in T019

---

## Phase 2: Foundational (Blocking Prerequisites)

**None required.** No shared infrastructure, schema change, migration, or new dependency
precedes the stories — see [data-model.md](./data-model.md), which changes no table. Inventing a
foundational phase here would be filler. User story work begins immediately after T001.

---

## Phase 3: User Story 1 - Resuming an interrupted item and having it run (Priority: P1) 🎯 MVP

**Goal**: A resume launches a worker the binary accepts, restoring the prior conversation under
the session identity this system chose.

**Independent Test**: Resume an item in `interrupted` with a recorded previous session against
the real worker; the worker starts, stays up past the confirmation window, knows what the
previous one was doing, and the item reaches `active`.

**Why this is the MVP**: it stands alone. With only this story done, the happy path — the one
that has never once worked — works. US2 covers what happens when a launch fails; US1 covers it
not failing.

### Tests for User Story 1

- [X] T002 [US1] Create `tests/unit/test_launch_shapes.py` with argv-composition tests over `build_launch_plan`: a restoring launch places `--fork-session` alongside `--resume <prior>`, and a non-restoring launch composes a list byte-identical to today's (FR-004). Confirm the restoring case FAILS before T003
- [X] T003 [P] [US1] Add an integration test in `tests/integration/test_dispatch.py` asserting a resumed dispatch records a new attempt number distinct from the restored session, and that `dispatch.confirmed` names the session it restored from (FR-002)

### Implementation for User Story 1

- [X] T004 [US1] Append `--fork-session` in the `resume_session_id` branch of `build_launch_plan` in `src/robot_army/dispatch.py` (around line 479), and extend the existing comment to record that this flag is what makes "a new attempt restoring the prior session's context" true, and that the combination without it is rejected before anything runs — cite [contracts/worker-launch-shapes.md](./contracts/worker-launch-shapes.md) G1/G2
- [X] T005 [US1] Add `resumed_from` to the `dispatch.confirmed` audit detail in `src/robot_army/dispatch.py` (around line 873) when the launch restored a session, so FR-002 is answerable from the log without parsing a nested `launch_argv` that carries the whole prompt

### Verification for User Story 1

- [X] T006 [US1] Run `uv run pytest tests/integration/test_dispatch.py tests/integration/test_effect_levels.py tests/unit/test_effects.py` and confirm every pre-existing expectation passes **unedited** — FR-004 means the non-restoring launch did not move

**Checkpoint**: `resume` works on the happy path. Independently demonstrable via
[quickstart.md](./quickstart.md) step 4.

---

## Phase 4: User Story 2 - A launch that dies young is reported, not wedged (Priority: P1)

**Goal**: A worker that exits before confirmation completes settles its work item at the moment
of detection, with the recorded exit as the outcome, instead of stranding it in `dispatching`
for the 15-minute reaper.

**Independent Test**: Cause a launch whose worker exits non-zero and records that exit inside the
confirmation window; the item ends `failed` with the exit named, well inside the window, and the
reaper is never what resolves it.

**Independence from US1**: complete. This race is reachable by any fast-exiting session — the
earlier broken-binary case (T064) escaped it only because that worker died before writing an
exit record. It would be worth doing with US1 already shipped or never shipped at all.

### Tests for User Story 2

- [X] T007 [US2] Extend the simulated session host in `tests/conftest.py` so `confirm_session` can apply a session exit *before* returning `None` — the seam that reproduces the cross-process race, where the daemon drains the spool while dispatch is still waiting
- [X] T008 [US2] Add integration tests in `tests/integration/test_dispatch.py` for all four confirmation-elapsed outcomes in [contracts/confirmation-outcome.md](./contracts/confirmation-outcome.md): already `exited_error`, already `exited_clean`, already `lost`, and nothing recorded. Assert the item's resulting state, the failure reason, the **absence** of a `state.session` record in the three terminal cases, and that no `IllegalTransition` appears anywhere in the log
- [X] T009 [P] [US2] Add an integration test in `tests/integration/test_dispatch.py` asserting that an exception raised inside the launch leaves the item `failed` and is re-raised rather than swallowed (FR-008, contract C5)

### Implementation for User Story 2

- [X] T010 [US2] In the `entry is None` branch of `dispatch_item` in `src/robot_army/dispatch.py` (around lines 757-795), re-read the session row at that moment and branch: a terminal state is not transitioned and its recorded exit becomes the failure reason; a non-terminal state takes today's path verbatim, wording included. `exited_clean` leaves the item to the ordinary end-of-session rules rather than failing it
- [X] T011 [US2] In the same branch, call `_detect_session_id_mismatch` only when nothing was recorded — with a recorded exit the question is answered and the probe would hunt a rival session that cannot exist (contract C4)
- [X] T012 [US2] Extend the `dispatch.unconfirmed` audit detail in `src/robot_army/dispatch.py` with the session state observed at that moment and which outcome was taken, so the log distinguishes "never appeared" from "already exited" (FR-010)
- [X] T013 [US2] Wrap the launch section of `dispatch_item` in `src/robot_army/dispatch.py` so any escaping exception records a new `dispatch.error` with its detail, settles the item via `_fail`, and then **re-raises** — the re-raise is what keeps this from being the catch-all-that-continues Principle III forbids (FR-008)

### Verification for User Story 2

- [X] T014 [US2] Confirm `src/robot_army/states.py` is untouched — in particular that no `EXITED_* → LOST` edge was added to `SESSION_TRANSITIONS`, which would make the contradiction legal instead of resolving it and would overwrite a known exit status with "lost" ([research.md](./research.md) R3)

**Checkpoint**: no launch failure, of any cause, leaves an item in `dispatching`.

---

## Phase 5: User Story 3 - The same class of defect cannot ship again unnoticed (Priority: P3)

**Goal**: Every launch shape this system composes is exercised against the real worker binary, so
a combination the binary rejects fails the suite on this machine instead of surfacing in a manual
round months later.

**Independent Test**: With the binary present the check accepts the composed shapes and rejects a
deliberately contradictory one; with the binary absent it reports skipped, never passed.

**Depends on US1**: the restoring shapes only pass once `--fork-session` is in place. Running this
check before T004 is a valid and useful demonstration that it has teeth — it must fail.

### Implementation for User Story 3

- [X] T015 [US3] Register a `requires_worker` marker in `pyproject.toml` under `[tool.pytest.ini_options] markers`, alongside the existing `requires_git`, described as "test shells out to the real worker binary"
- [X] T016 [US3] Add the real-binary check to `tests/unit/test_launch_shapes.py`: parametrise the 24 shapes (6 permission modes × restoring/not × model set/unset), probe each with `printf '' | <binary> -p <shape flags>`, and assert the expected sentinel from [contracts/worker-launch-shapes.md](./contracts/worker-launch-shapes.md) rather than the exit status, which cannot discriminate. Mark `requires_worker` and `skipif(shutil.which(binary) is None, ...)`, following the `tests/integration/test_spool_recovery.py` precedent. The probe must not dispatch work, create a worktree, or leave a session behind (FR-016)

### Verification for User Story 3

- [X] T017 [US3] Prove the check fails when it should: temporarily remove `--fork-session` from `src/robot_army/dispatch.py`, run `uv run pytest -m requires_worker`, confirm it fails naming the shape and the binary's own complaint, then restore. A check that cannot be made to fail proves nothing — which is the entire lesson of this feature
- [X] T018 [US3] Prove the skip path: run `PATH=/usr/bin:/bin uv run pytest -m requires_worker -v` and confirm the result is **skipped with a reason**, never passed (FR-015)

**Checkpoint**: all three stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T019 Run `uv run pytest` and `uv run ruff check .` and confirm the whole suite passes — the constitution's bar for a feature being complete — with no pre-existing expectation edited to accommodate this change
- [X] T020 [P] Add a short section to `docs/logging.md` documenting the new `dispatch.error` record and the extended `dispatch.unconfirmed` and `dispatch.confirmed` details, following the per-milestone convention already used by "The milestone 004 actions" table
- [X] T021 Walk [quickstart.md](./quickstart.md) step 3 by hand against a scratch `XDG_STATE_HOME`: a fast-exiting launch ends `failed` with the exit named, the session keeps `exited_error`, and no `IllegalTransition` appears in `robot-army log`
- [ ] T022 **(left for the maintainer --- needs the real daemon and a genuinely interrupted item on this machine; see the completion report)** Walk [quickstart.md](./quickstart.md) step 4 end to end with a real daemon and a genuinely interrupted item — the phone round that found this. Include the case the spec calls out: a resume whose stored conversation is gone must land in `failed` inside the confirmation window, not sit in `dispatching`. **Ask the resumed worker what the previous one was doing**; a resume that starts an empty conversation is a failure even when every state transition looks right
- [ ] T023 Commit as atomic changes whose messages say why — the rejected flag combination, the confirmation race, the verification gap are three separate reasons — then push the branch and open a pull request referencing issue #35

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (T001)**: no dependencies
- **Foundational**: none exists; nothing blocks the stories
- **US1 (T002-T006)**: after T001
- **US2 (T007-T014)**: after T001. Independent of US1 in behaviour, but **sequential in practice** — both edit `src/robot_army/dispatch.py`
- **US3 (T015-T018)**: after US1, because the restoring shapes only pass once `--fork-session` lands
- **Polish (T019-T023)**: after the stories that are being delivered

### Within each story

- Tests before implementation. T002 must fail before T004; T008 must fail before T010
- T007 (the test seam) blocks T008 and T009
- T010 blocks T011 and T012 — all three are the same branch of the same function
- T017 requires T016 to exist and T004 to have landed

### Parallel opportunities

Few, and marked honestly:

- **T003** with T002 — different files (`tests/integration/` vs `tests/unit/`)
- **T009** with T008 — the same file, but independent test functions that do not conflict if written as one edit each; drop the `[P]` if working in a single pass
- **T020** with T019 — documentation touches no code

Everything else is sequential. Three of the four production edits are in one function of one
file; there is no honest way to parallelise them.

---

## Implementation Strategy

### MVP (User Story 1 only)

1. T001 — baseline
2. T002-T006 — the flag, its tests, and the FR-004 regression check
3. **STOP and VALIDATE**: quickstart step 4. Resume a real interrupted item and ask the worker
   what the last one was doing
4. This alone fixes the reported outage. It is worth shipping on its own

### Incremental delivery

1. Setup → baseline recorded
2. **US1** → resume works → validate → ship (MVP)
3. **US2** → failures report themselves → validate quickstart step 3 → ship
4. **US3** → the gap that hid this closes → validate by breaking it on purpose → ship
5. Polish → docs, full suite, the by-hand rounds, branch and PR

### Notes

- Commit after each story at minimum; the three stories are three distinct reasons for change
- Do not edit an existing test expectation to make this change pass. If one fails, the change
  moved something FR-004 says must not move
- The state gate stays as strict as it is. If a fix seems to need a new transition edge, it is
  the wrong fix (research R3)
