---

description: "Task list for issue #20 — the onboarding security review reads real committed settings at every effect level"
---

# Tasks: The onboarding security review reads real committed settings at every effect level

**Input**: Design documents from `specs/20260906-070620-real-settings-read-in-simulation/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/simulated-reads.md](./contracts/simulated-reads.md),
[quickstart.md](./quickstart.md)

**Tests**: **required, not optional.** The constitution's Development Workflow section requires unit
tests for every new or changed unit of behaviour, and additionally failure-path tests for code
parsing external input. This boundary parses git output and feeds a security decision, so both
apply. Test tasks here are not the template's optional ones.

**Organization**: by user story. US1 alone is a complete, shippable fix for the reported bug.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different file, no dependency on an incomplete task
- **[Story]**: US1, US2, US3 from [spec.md](./spec.md)

## Path Conventions

Single project: `src/robot_army/`, `tests/unit/`, `tests/integration/`, `docs/guide/`.

---

## Phase 1: Setup — pin the bug before fixing it

**Purpose**: make the defect fail the suite, so the fix is demonstrated rather than asserted. Every
test in this phase is expected to **fail** on the current code and to keep passing afterwards.

- [ ] T001 Run `uv run pytest -q` and record that the suite is green before any change, so a later failure is attributable to this work
- [ ] T002 [P] Add a failing test in `tests/unit/test_git_boundary.py` asserting `SimulatedVersionControl.show_file_at_ref` returns the same bytes as `GitVersionControl.show_file_at_ref` for a file committed at a ref in the same fixture clone
- [ ] T003 [P] Add a failing test in `tests/unit/test_git_boundary.py` asserting `SimulatedVersionControl.default_remote` returns the same value as the real implementation for a clone with no remote, a clone whose only remote is `gh`, and a clone with `origin`

**Checkpoint**: T002 and T003 fail, naming the two methods. The bug is now in the suite.

---

## Phase 2: Foundational

**None.** No shared infrastructure, module, migration or dependency is needed before the user
stories: the class already holds the `self._real` delegate that the fix uses, and no schema changes
(see [data-model.md](./data-model.md)). Recorded explicitly so its absence reads as a decision.

---

## Phase 3: User Story 1 — the settings review shows what is really committed (P1) 🎯 MVP

**Goal**: `onboard` prints the real committed settings and records the real hashes at every effect
level, including `plan`.

**Independent test**: onboard a repository with committed settings under a simulated version-control
boundary and compare screen and fingerprint against the real one — they must be identical
([quickstart.md](./quickstart.md) scenarios 1 and 2).

- [ ] T004 [US1] Delegate `SimulatedVersionControl.show_file_at_ref` to `self._real` in `src/robot_army/boundaries/git.py`, replacing the unconditional `return None`
- [ ] T005 [US1] Write the reason at the method in `src/robot_army/boundaries/git.py`: this is a read of the operator's real clone, it is the read the FR-003 onboarding review depends on, and it was the subject of issue #20 — following the phrasing `list_remotes` and `remote_url` already use
- [ ] T006 [US1] Correct the `SimulatedVersionControl` class docstring in `src/robot_army/boundaries/git.py`, which currently names `show_file_at_ref` as an example of a read that answers empty "because at `plan` level no worktree was created for them to describe" — untrue of this method, and the sentence that made the bug look intended
- [ ] T007 [P] [US1] Add a unit test in `tests/unit/test_trust.py` asserting `compute_fingerprint` under a `SimulatedVersionControl` equals the fingerprint under the real one for a clone with both settings files committed
- [ ] T008 [P] [US1] Add a unit test in `tests/unit/test_trust.py` asserting `read_committed_settings` under a `SimulatedVersionControl` returns the full text of both files, not `{}`
- [ ] T009 [P] [US1] Add an integration test in `tests/integration/test_onboard.py` asserting the approval screen at a simulated boundary prints `committed tool-permission settings at the base ref:` and the file contents — the FR-003 review that was blank
- [ ] T010 [P] [US1] Add an integration test in `tests/integration/test_onboard.py` asserting a repository that genuinely commits no settings still reports `no committed .claude/settings*.json at the base ref` under a simulated boundary, so the fix did not turn the message into a lie in the other direction
- [ ] T011 [P] [US1] Add failure-path unit tests in `tests/unit/test_git_boundary.py` covering: a ref that does not exist in the clone, a path that is not a git repository at all, a committed settings file that is not valid UTF-8, and a committed settings file of zero bytes — each answering rather than raising, in both implementations

**Checkpoint**: T002 passes. The reported bug is fixed and the review is real at every level.

---

## Phase 4: User Story 2 — a stale blank approval surfaces instead of standing (P1)

**Goal**: the approvals already recorded against a blank screen block dispatch rather than passing
it. No production change is expected here — this phase proves the existing gate now does its job,
and fails if it does not.

**Independent test**: an approval row holding `{}` against a repository that does commit settings
blocks dispatch, naming the files ([quickstart.md](./quickstart.md) scenario 3).

- [ ] T012 [US2] Add a unit test in `tests/unit/test_trust.py` asserting `check_launch_gate` raises `DispatchBlocked` for a repository whose recorded fingerprint is empty but which has committed settings at the base ref, under a **simulated** boundary — the case that silently passed
- [ ] T013 [US2] Assert in that test that the message names the files as `added:` and points at `onboard <repo> --reapprove`, so the operator is told the remedy rather than only the refusal
- [ ] T014 [P] [US2] Add an integration test in `tests/integration/test_onboard.py` asserting `onboard --reapprove` under a simulated boundary shows the diff between the recorded empty fingerprint and the real one
- [ ] T015 [US2] If T012 passes without any production change, record that in the test's docstring — the gate was always right and only its input was wrong. Do **not** add code to make the test look load-bearing

**Checkpoint**: existing bad approvals are demonstrably caught.

---

## Phase 5: User Story 3 — the simulation stops inventing answers about the real clone (P2)

**Goal**: `default_remote` answers truthfully, and the rule deciding which reads may be invented is
written down and enforced so the next method is not another coin toss.

**Independent test**: a `plan`-level dispatch preparation against a clone with no remote records
`fetch_skipped`, as the real path does ([quickstart.md](./quickstart.md) scenario 5); and the
protocol-coverage test fails when a member is added without a decision.

- [ ] T016 [US3] Delegate `SimulatedVersionControl.default_remote` to `self._real` in `src/robot_army/boundaries/git.py`, replacing the unconditional `return "origin"`
- [ ] T017 [US3] Write the reason at the method in `src/robot_army/boundaries/git.py`: it is derived from `list_remotes`, which is already real, and inventing `"origin"` made a local-only clone look remote-backed and suppressed the `fetch_skipped` record the real path writes
- [ ] T018 [US3] State the subject rule in the `SimulatedVersionControl` class docstring in `src/robot_army/boundaries/git.py` — the subject of the question decides, not the verb — pointing at `contracts/simulated-reads.md`
- [ ] T019 [P] [US3] Write the mixed-subject reason into the `rev_parse` and `list_worktrees` docstrings in `src/robot_army/boundaries/git.py`, so a later reader does not "finish the job" by making them real (research R3)
- [ ] T020 [US3] Add the protocol-coverage test to `tests/unit/test_git_boundary.py`: a table mapping every member of `VersionControl.__protocol_attrs__` to `real` or a one-line as-if reason, asserted to cover the protocol exactly, so a member added without a decision fails the suite by name (FR-007)
- [ ] T021 [US3] Extend `test_the_simulated_implementation_answers_the_same_as_the_real_one` in `tests/unit/test_git_boundary.py` to cover every member the table marks `real`, driven off that table rather than a second hand-written list
- [ ] T022 [P] [US3] Add a unit test asserting `worktree.prepare` under a simulated boundary against a clone with **no** remote records `fetch_skipped: the repository has no configured remote`, matching the real path — the record that could not appear below `local`
- [ ] T023 [US3] Run `uv run pytest -q` and fix any existing test that asserted the invented `"origin"` or the invented `None`; a test that pinned the bug is updated with a note saying why, not deleted silently

**Checkpoint**: the class no longer invents any answer about the operator's real clone, and the rule is enforced.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T024 Add a section to `docs/guide/1-setup.md` describing what the onboarding review shows — the committed settings a session honours without asking — which the page currently never mentions, and stating that it is **real at every effect level** (FR-008)
- [ ] T025 Extend the "Trying it without consequences" section of `docs/guide/1-setup.md`, which says only that *polling and eligibility* are always real, so that it does not imply the onboarding review is reduced below `live`
- [ ] T026 [P] Add a note to `docs/guide/state.md` beside `settings_fingerprint` that an empty fingerprint recorded before this fix now blocks dispatch pending `onboard --reapprove`, and that nothing backfills those rows — the same honest-price argument the `NULL clone_path` paragraph already makes
- [ ] T027 Confirm no configuration key changed, so CLAUDE.md §2 does not apply: `uv run pytest tests/unit/test_example_config_drift.py` must pass untouched and `share/config.example.toml` must be unmodified in the diff
- [ ] T028 Confirm `README.md` is unmodified and still under its 150-line limit
- [ ] T029 Run `uv run ruff check src/ tests/` and fix anything it reports
- [ ] T030 Run the full `uv run pytest` suite; it must pass (SC-005)
- [ ] T031 Walk [quickstart.md](./quickstart.md) scenarios 1, 2 and 4 against a real clone with committed settings, at `effect_level = "plan"`, and confirm the screen and fingerprint match `live`

---

## Dependencies

```text
Phase 1 (T001–T003)  ── pins the bug
   │
   ├─► Phase 3 US1 (T004–T011)   the reported fix        ── MVP, shippable alone
   │        │
   │        └─► Phase 4 US2 (T012–T015)  depends on US1: the gate only blocks once the
   │                                     real fingerprint is computed
   │
   └─► Phase 5 US3 (T016–T023)   independent of US1 and US2 — a different method and a
                                 different caller; may be done in either order
                    │
                    ▼
        Phase 6 (T024–T031)      docs and verification, after the behaviour is settled
```

- **US1 → US2**: real. US2's test asserts a block that only happens once `compute_fingerprint` is real.
- **US1 ⟂ US3**: independent. Different methods, different callers, no shared test.
- T020 depends on T004 and T016 (the table records their verdicts); T021 depends on T020.

## Parallel Execution

Within US1, once T004–T006 are done: **T007, T008, T009, T010, T011** are five different test cases
across three files with no shared fixture mutation — all parallelisable.

Within US3, once T016–T017 are done: **T019 and T022** are parallel; T020 and T021 are sequential
with each other.

In Phase 6: **T026** is a different file from T024/T025 and is parallel with them.

## Implementation Strategy

**MVP is Phase 1 + Phase 3.** That is the reported bug, fixed and tested, in four lines of
production change. It could ship on its own and the issue would be closed correctly.

Phase 4 costs nothing in production code and is what turns the fix from "new approvals are correct"
into "the wrong approvals already on this machine are caught" — the difference between fixing the
bug and remediating it. Do not skip it because it adds no source lines.

Phase 5 is the issue's own second question — *consider whether any read belongs in the simulated
class at all* — and is the reason this defect is unlikely to recur. It is P2 because the system is
already correct without it, and it can be dropped without weakening the security fix.

**Do not** widen further: `rev_parse` and `list_worktrees` were evaluated and deliberately left
(research R3), and the wiring table in `effects.py` is right as it stands.
