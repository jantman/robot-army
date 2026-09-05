---

description: "Task list for the onboard-time spec numbering warning"
---

# Tasks: Warn at onboarding when Spec Kit numbers features by scanning

**Input**: Design documents from `specs/20260905-192844-warn-speckit-numbering/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/numbering-warning.md](contracts/numbering-warning.md)

**Tests**: Required, not optional. The constitution's Development Workflow section requires unit
tests for every new or changed unit of behaviour, and additionally requires failure-path tests for
code parsing external input — which the reader in Phase 2 is.

**Organization**: grouped by user story. The four stories share one reader, built in Phase 2, so
the phases here are genuinely sequential rather than parallel: this is a feature of two functions,
and pretending otherwise would be theatre.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different file, no dependency on an incomplete task
- **[Story]**: which user story the task serves (US1–US4)
- Every task names its file

## Path Conventions

Single Python project: `src/robot_army/`, `tests/unit/`, `tests/integration/`, `docs/guide/`.

---

## Phase 1: Setup

**Purpose**: nothing to set up. Recorded so its absence is a decision rather than an oversight.

- [ ] T001 Confirm the working tree is clean and `uv run pytest` passes before any change, so a
      later failure is attributable to this feature — no file changes

**Checkpoint**: baseline green.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the reader every story depends on, and the test fixture that can build the shapes it
must answer for.

**⚠️ CRITICAL**: no user story can begin until T003 exists.

- [ ] T002 Add an `init_options: str | None = None` parameter to `make_speckit_tree` in
      `tests/conftest.py` that writes its value verbatim to `.specify/init-options.json` when given
      — raw text rather than a dict, so one parameter builds the valid, the malformed and the
      hostile cases
- [ ] T003 Add `INIT_OPTIONS`, `SAFE_NUMBERING`, the `Numbering` dataclass and `numbering()` to
      `src/robot_army/speckit.py`, implementing the nine ordered rules in
      [data-model.md](data-model.md) — including the 64 KiB bound and the
      `[A-Za-z0-9_.-]{1,32}` guard on the echoed value. Docstrings explain *why*, per the
      repository's conventions; the module must still import nothing from `config`
- [ ] T004 [P] Write `tests/unit/test_speckit_numbering.py` covering one case per rule: absent
      file, `timestamp`, `sequential`, an unrecognised legible value, absent key, invalid JSON,
      a JSON array, a non-string value, a value with a newline in it, an over-length value, a file
      over the size bound, and an unreadable file (chmod 000, skipped when running as root as the
      existing detection tests do)
- [ ] T005 [P] Assert in `tests/unit/test_speckit_numbering.py` that no failure path raises and
      that the hostile value never appears in `reason` — the two promises FR-007 and FR-008 make

**Checkpoint**: `uv run pytest tests/unit/test_speckit_numbering.py` green. The reader answers
correctly for every row of the outcomes table with nothing calling it yet.

---

## Phase 3: User Story 1 — Onboarding a scan-numbered repository says so (Priority: P1) 🎯 MVP

**Goal**: the warning reaches the maintainer before the approval prompt.

**Independent Test**: onboard a clone with Spec Kit and `"feature_numbering": "sequential"`, and
one with no `init-options.json`; the block appears ahead of the prompt in both, with the two
different sentences, and answering `y` onboards normally.

- [ ] T006 [US1] In `src/robot_army/operations.py`, import `speckit` and, in `onboard`, ask
      `speckit.detect(clone_path)` and — only when detected — `speckit.numbering(clone_path)`,
      after the committed-settings block and before `result.flush_to(out)`
- [ ] T007 [US1] Add `_numbering_lines()` to `src/robot_army/operations.py` rendering the scanned
      block exactly as [contracts/numbering-warning.md](contracts/numbering-warning.md) gives it,
      with the configured-value and the not-set wordings as separate sentences
- [ ] T008 [US1] Add integration tests to `tests/integration/test_onboard.py`: the block appears
      for `sequential`, appears with the not-set wording when the file is absent, sits before the
      prompt, and does not change the exit code or the record written on approval
- [ ] T009 [US1] Add an integration test to `tests/integration/test_onboard.py` asserting the
      onboarded clone is unmodified afterwards — FR-011, and the promise `speckit.py` already makes

**Checkpoint**: the feature does its job. Everything after this is making sure it does not do it in
the wrong places.

---

## Phase 4: User Story 2 — A safe repository is not nagged (Priority: P1)

**Goal**: silence for `timestamp`, silence for a non-Spec-Kit repository, silence for scaffolding
without the lifecycle commands.

**Independent Test**: onboard each of those three and compare the screen against what the same run
produced before this feature.

- [ ] T010 [US2] Add integration tests to `tests/integration/test_onboard.py` asserting no
      numbering line for `"feature_numbering": "timestamp"`, for a clone with no `.specify/` at
      all, and for a clone with `.specify/init-options.json` but no Spec Kit scaffolding — the
      last proving detection gates the read
- [ ] T011 [US2] Add an integration test to `tests/integration/test_onboard.py` for scaffolding
      present but a lifecycle command missing: detection says no, so the block is absent even
      though the file says `sequential`

**Checkpoint**: the warning appears on exactly the repositories it should.

---

## Phase 5: User Story 3 — An unreadable setting is reported as unreadable (Priority: P2)

**Goal**: the third outcome is visibly the third outcome.

**Independent Test**: onboard a clone with invalid JSON and one with a JSON array; each shows the
unknown block with its own reason, and onboarding proceeds.

- [ ] T012 [US3] Extend `_numbering_lines()` in `src/robot_army/operations.py` with the unknown
      block, printing `Numbering.reason` verbatim on its own line
- [ ] T013 [US3] Add integration tests to `tests/integration/test_onboard.py`: invalid JSON and a
      JSON array each produce the unknown wording rather than the scanned wording, and neither
      changes the exit code

**Checkpoint**: all three outcomes are distinguishable on the screen.

---

## Phase 6: User Story 4 — The machine-readable forms carry the finding (Priority: P3)

**Goal**: a script sees what the screen says, and the log answers what was approved.

**Independent Test**: run each of the four repository shapes in JSON mode and read the three keys;
approve one and read the audit line.

- [ ] T014 [US4] Add `speckit`, `speckit_numbering` and `speckit_numbering_value` to the
      `result.data` dictionary in `onboard`, in `src/robot_army/operations.py`
- [ ] T015 [US4] Add `speckit` and `speckit_numbering` to the `repo.onboard` audit detail in
      `src/robot_army/operations.py` (FR-013)
- [ ] T016 [US4] Add integration tests to `tests/integration/test_onboard.py`: the three JSON keys
      for each shape, `null` numbering when the repository is not a Spec Kit project, no warning
      prose anywhere in the JSON document, and the two fields present on the recorded
      `repo.onboard` line

**Checkpoint**: every requirement in the spec has a test behind it.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T017 [P] Update the "Adding a repository" section of `docs/guide/1-setup.md` with what the
      warning says, why it cannot be a dispatch-time check, and that ignoring it is a choice —
      the page the runtime guidance names for onboarding changes
- [ ] T018 [P] Update `docs/guide/audit-log.md`: the two new `repo.onboard` detail fields in the
      milestone-005 detail table, and this read added to the deliberately-unlogged table beside
      the Spec Kit detection reads it belongs with
- [ ] T019 Run `uv run pytest` — the whole suite must pass, including
      `tests/unit/test_docs_links.py`, which checks the links the two doc tasks add
- [ ] T020 Walk [quickstart.md](quickstart.md) against a scratch clone, confirming the screen reads
      the way the contract says and that the clone is untouched afterwards

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001)**: none
- **Foundational (T002–T005)**: blocks every story. `numbering()` is the only new unit of
  behaviour; all four stories are ways of consuming it
- **US1 (T006–T009)**: after Foundational. The MVP
- **US2 (T010–T011)**: after US1 — it asserts the *absence* of what US1 adds, so it cannot be
  meaningfully written first
- **US3 (T012–T013)**: after US1; extends the same renderer
- **US4 (T014–T016)**: after US1, and independent of US2 and US3
- **Polish (T017–T020)**: after everything it documents

### Within each story

Renderer before its tests, because the contract fixes the exact text and the tests assert it
verbatim; a test written first would be asserting a string invented twice.

### Parallel Opportunities

Genuinely limited, and stated honestly rather than padded:

- T004 and T005 touch the same new test file but different test functions, and can be written
  together
- T017 and T018 are different documentation files with no shared content

Everything else in `operations.py` touches one function, and `tests/integration/test_onboard.py` is
one file — marking those `[P]` would invite exactly the same-file conflict the format warns about.

---

## Implementation Strategy

### MVP

Phases 1–3. At that point a maintainer onboarding a scan-numbered Spec Kit repository is told
before they answer, which is the entirety of the Human Decision on issue #41.

### Incremental Delivery

1. Foundational → the reader exists and is proven against every shape of file
2. US1 → the warning appears (**MVP**)
3. US2 → and appears nowhere else
4. US3 → and tells "unreadable" apart from "unsafe"
5. US4 → and is legible to scripts and to the log
6. Polish → and is documented where the guide says it belongs

### Notes

- One maintainer, so the parallel-team section of the template does not apply and is omitted
  rather than filled in with fiction
- Commit per phase, with a message explaining why — the repository's convention
- No task writes to a database, adds a config key, or touches `share/config.example.toml`; if one
  seems to, the design has drifted from [plan.md](plan.md)
