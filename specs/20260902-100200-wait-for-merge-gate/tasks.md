---

description: "Task list for the wait-for-merge gate and its per-repository configuration"
---

# Tasks: Per-Repo Concurrency and Wait-for-Merge

**Input**: Design documents from `specs/20260902-100200-wait-for-merge-gate/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included and **not optional**. The constitution's Development Workflow requires
unit tests for every new or changed unit of behaviour, and additionally requires failure and
interruption paths to be tested for code that parses external input or makes irreversible
decisions — which is exactly `fast_forward`'s six refusals.

**Organization**: Grouped by the spec's three user stories. Each is independently
implementable and independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: [US1] the gate, [US2] the fast-forward, [US3] visibility and configuration

## Path Conventions

Single project: `src/robot_army/`, `tests/unit/` at the repository root.

---

## Phase 1: Setup

**Purpose**: Nothing to initialise. The project, its dependencies, and its lint and test
configuration already exist and this feature adds no dependency (plan.md, Technical Context).

- [X] T001 Confirm the baseline is green before changing anything: `uv run pytest` and `uv run ruff check` both pass on the current branch

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The configuration surface every story reads. Nothing else can be written until
`Config.effective_wait_for_merge` exists.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 Add `wait_for_merge: bool = False` to `DispatchConfig` in `src/robot_army/config.py`, with a docstring saying why it defaults off (an existing installation must not change behaviour) — per `contracts/config.md`
- [X] T003 Add `wait_for_merge: bool | None = None` to `RepoConfig` in `src/robot_army/config.py`, documenting `None`-means-inherit with the same reasoning `max_sessions` gives for keeping "unset" distinct
- [X] T004 Add `"wait_for_merge"` to the `dispatch` entry of `_KNOWN_KEYS` and to `_REPO_KEYS` in `src/robot_army/config.py`, so a misspelling in either section is refused rather than silently ignored
- [X] T005 Parse the key in both places in `src/robot_army/config.py` — the `[dispatch]` section and each `[repos.*]` section — rejecting a non-boolean with a problem naming the key and the value seen
- [X] T006 Add `Config.effective_wait_for_merge(key) -> tuple[bool, bool]` to `src/robot_army/config.py`, beside `effective_repo_cap` and shaped identically, with a docstring stating why there is no `min()` counterpart (the global value is the setting itself, not a ceiling)
- [X] T007 Carry `wait_for_merge` through both `RepoConfig` construction branches in `repos.resolve` in `src/robot_army/repos.py`, so a resolved config never silently drops a field the author set
- [X] T008 [P] Tests for the configuration surface in `tests/unit/test_config.py`: the default is off; `[dispatch] wait_for_merge` parses; a per-repository override parses; a non-boolean is refused; `wait_for_merge` misspelt in `[dispatch]` is an error; misspelt in `[repos.*]` is an error; and every row of `contracts/config.md`'s resolution table returns the stated `(value, explicit)` pair
- [X] T009 [P] Test in `tests/unit/test_repos.py` (where `repos.resolve` is tested; `test_repo_resolution.py` is about intake) that `repos.resolve` preserves an explicitly set `wait_for_merge` and leaves it `None` when the section does not set it

**Checkpoint**: the setting exists, resolves, and is refused when misspelt. Stories can begin.

---

## Phase 3: User Story 1 — One issue at a time, and not the next until the last one landed (Priority: P1) 🎯 MVP

**Goal**: A repository with wait-for-merge in force does not dispatch its next issue while it
has an unfinished item, and says so on every surface.

**Independent Test**: Enable the setting for one repository, put one item in `awaiting_review`
and another in `ready`, and confirm the ready item is held with a reason naming the first;
then move the first to `done` and confirm the second dispatches. A second repository with a
ready item must be unaffected throughout.

### Tests for User Story 1

- [X] T010 [P] [US1] In `tests/unit/test_ordering.py`, test that an item is held with `awaiting_merge` when its repository has the setting in force and another item in it is `awaiting_review`, and that the detail names the repository, the unfinished item's issue number, and its state (SC-003)
- [X] T011 [P] [US1] In `tests/unit/test_ordering.py`, test the release: with the unfinished item moved to `done`, and again to `abandoned`, the hold is gone and the item is dispatchable
- [X] T012 [P] [US1] In `tests/unit/test_ordering.py`, test the state predicate exhaustively — `dispatching`, `active`, `awaiting_review`, `interrupted` and `failed` each hold; `discovered` and `ready` do **not** (R1: two ready items must not deadlock a repository against itself)
- [X] T013 [P] [US1] In `tests/unit/test_ordering.py`, test per-repository isolation: an unfinished item in repository A leaves a ready item in repository B dispatchable in the same plan (FR-007)
- [X] T014 [P] [US1] In `tests/unit/test_ordering.py`, test that the setting being off — globally and per repository — produces no hold at all, so today's behaviour is exactly preserved
- [X] T015 [P] [US1] In `tests/unit/test_ordering.py`, test precedence: an item to which both `repo_cap` and `awaiting_merge` apply reports `repo_cap`; one to which both `awaiting_merge` and `preparation_failed` apply reports `awaiting_merge`; and `paused` still outranks everything (FR-011, R4)
- [X] T016 [P] [US1] In `tests/unit/test_ordering.py`, test that a simulated item is gated exactly as a real one is, in both directions (data-model.md)
- [X] T017 [P] [US1] In `tests/integration/test_dispatch_capacity.py` (which already has the registry, `/proc`, two-repository and trust-file fixtures this needs), test that a pass which dispatches nothing because every candidate was held writes one `dispatch.at_capacity` naming `awaiting_merge`, that a second identical pass writes nothing further, and that `dispatch.hold_ended` is written once when the hold lifts (FR-015, R5)
- [X] T018 [P] [US1] In `tests/integration/test_dispatch_capacity.py`, test that `awaiting_merge` does **not** end the pass: with repository A held and repository B dispatchable, the pass dispatches B (FR-007)

### Implementation for User Story 1

- [X] T019 [US1] Insert `AWAITING_MERGE = "awaiting_merge"` into `HoldReason` in `src/robot_army/ordering.py` between `REPO_CAP` and `NOT_ONBOARDED`, and extend the enum docstring to justify its rank the way every other member's rank is justified (R4)
- [X] T020 [US1] Add `unfinished_by_repo(conn)` to `src/robot_army/ordering.py`: one `db.list_work_items(include_simulated=True, states=UNFINISHED_STATES)` scan returning `repo_key -> [WorkItem, ...]`, with a module-level `UNFINISHED_STATES` frozenset and a docstring stating why `ready` and `discovered` are excluded
- [X] T021 [US1] Compute it once per call in `ordering.plan` in `src/robot_army/ordering.py` and pass it into `_hold_for`, following the pattern the existing `resolved = repos.resolved_all(...)` line establishes and states in its comment (R3)
- [X] T022 [US1] Add the gate clause to `_hold_for` in `src/robot_army/ordering.py` after the `repo_cap` clause: when `config.effective_wait_for_merge(item.repo_key)` is true and the repository has an unfinished item other than this one, return `AWAITING_MERGE` with detail naming the repository, that item's issue number and its state
- [X] T023 [US1] Widen `_hold_signature` and `_note_hold` in `src/robot_army/dispatch.py` to cover per-item holds, adding the held reason and repository to the signature so a change of *which* condition holds is news (R5)
- [X] T024 [US1] In `select_and_dispatch` in `src/robot_army/dispatch.py`, record a hold when the pass ends having dispatched nothing and at least one candidate was held, and split the existing `_clear_hold(freed_by="the queue drained")` call so "nothing was eligible" and "everything eligible was held" stay distinguishable
- [X] T025 [US1] Update the docstrings in `src/robot_army/ordering.py` and `src/robot_army/dispatch.py` that enumerate the hold reasons and the global/per-item split, so the code's own account of the precedence stays the single readable statement of it

**Checkpoint**: US1 is complete and independently demonstrable. The gate holds, releases,
isolates repositories, and leaves a record.

---

## Phase 4: User Story 2 — The next issue starts from the merged code (Priority: P2)

**Goal**: For a wait-for-merge repository, the clone's own default branch is fast-forwarded to
the fetched remote head — and is left strictly alone whenever that would be anything more than
a fast-forward of a clean checkout.

**Independent Test**: With the setting on and the clone's default branch clean and behind the
remote, dispatch and confirm the clone advanced. Then reproduce each refusal from
`quickstart.md`'s table and confirm the clone is byte-identical before and after and the
dispatch still succeeds.

### Tests for User Story 2

- [X] T026 [P] [US2] In `tests/unit/test_git_boundary.py`, test the success path against real git (`requires_git`): a clean clone on the default branch and behind its remote returns `updated` with distinct `before` and `after` shas, and the branch actually moved
- [X] T027 [P] [US2] In `tests/unit/test_git_boundary.py`, test `already_current`: running it a second time returns that outcome, not `updated`, and invokes no merge
- [X] T028 [P] [US2] In `tests/unit/test_git_boundary.py`, test each refusal returns `skipped` with a reason naming the cause, and leaves `git rev-parse HEAD` and `git status --porcelain` unchanged — dirty tree (tracked and untracked separately), on another branch, detached `HEAD`, mid-rebase, no remote, and a local commit the remote lacks
- [X] T029 [P] [US2] In `tests/unit/test_git_boundary.py`, test that a git failure during the merge returns `failed` with git's message rather than raising
- [X] T030 [P] [US2] In `tests/unit/test_simulated_writers.py`, test that `SimulatedVersionControl.fast_forward` writes nothing and returns `skipped`
- [X] T031 [P] [US2] In `tests/integration/test_worktree.py` (which already prepares against real git), test that `worktree.prepare` calls `fast_forward` when the setting is in force, does **not** call it when it is not (FR-020), records the outcome in the `worktree.prepare` audit record, and — for `skipped` and for `failed` alike — still produces a successful `PreparationResult` (FR-019)

### Implementation for User Story 2

- [X] T032 [P] [US2] Add the `FastForwardResult` dataclass to `src/robot_army/boundaries/__init__.py` with the four fields from data-model.md, and export it
- [X] T033 [US2] Add `fast_forward` to the `VersionControl` protocol in `src/robot_army/boundaries/__init__.py`, with a docstring in the style of `remote_branch_head`'s stating why the four outcomes must stay four
- [X] T034 [US2] Implement `fast_forward` in `src/robot_army/boundaries/git.py` per `contracts/version-control.md`: the six preconditions in order, `already_current` when the shas match, then `git merge --ff-only` as the last line of defence, with every branch returning rather than raising
- [X] T035 [US2] Implement `SimulatedVersionControl.fast_forward` in `src/robot_army/boundaries/git.py`, logging the call and returning `skipped` as every other simulated verb does
- [X] T036 [US2] Call it from `worktree.prepare` in `src/robot_army/worktree.py`, immediately after the fetch and before `add_worktree`, only when `config.effective_wait_for_merge(repo.key)` is true, writing the outcome and reason into the existing `audit.action` outcome dict beside `fetch_skipped` — and never converting a non-`updated` result into a `PreparationResult` failure (R8, FR-019)

**Checkpoint**: US1 and US2 both work, independently. The clone advances when it safely can
and is untouched, with a stated reason, when it cannot.

---

## Phase 5: User Story 3 — Seeing and setting the limits for a repository (Priority: P3)

**Goal**: `robot-army capacity` answers, for every onboarded repository, how many sessions it
may run, whether that was chosen, whether wait-for-merge applies, and whether *that* was
chosen.

**Independent Test**: Configure the setting globally on and override it off for one
repository; confirm the per-repository block reports the effective value and its source for
every onboarded repository, including those with no live session.

### Tests for User Story 3

- [X] T037 [P] [US3] In `tests/unit/test_capacity_reporting.py` (new; `test_capacity.py` tests `capacity.snapshot`, not the command), test that the per-repository block lists every onboarded repository — including one with zero live sessions — and reports sessions, the effective cap, and whether the cap was chosen
- [X] T038 [P] [US3] In `tests/unit/test_capacity_reporting.py`, test that each line reports the effective wait-for-merge value and distinguishes a value chosen for that repository from one inherited from `[dispatch]` (FR-014, US3 AS1)
- [X] T039 [P] [US3] In `tests/unit/test_capacity_reporting.py`, test that the JSON `data` payload carries the same four facts per repository and that `per_repo` keeps its existing meaning as the live-session count
- [X] T040 [P] [US3] In `tests/unit/test_web_views.py`, test that the queue view renders the new hold reason and its detail, proving FR-012's single-source claim end to end on the surface that does not share the terminal's rendering code

### Implementation for User Story 3

- [X] T041 [US3] Widen the per-repository block of `operations.capacity` in `src/robot_army/operations.py` from "repositories with a live session" to "every onboarded repository", reporting sessions, effective cap and its source, and effective wait-for-merge and its source, per `contracts/config.md`'s rendering
- [X] T042 [US3] Extend the `data` payload of `operations.capacity` and `_capacity_dict` in `src/robot_army/operations.py` with the per-repository facts, leaving `per_repo`'s existing key and meaning untouched so nothing reading it today changes meaning

**Checkpoint**: all three stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T043 [P] Update `README.md`'s "How many sessions run at once" section: add both new keys to the TOML example, and add a short subsection explaining that the per-repository cap counts live sessions while wait-for-merge waits for the work to land, and that they compose
- [X] T044 [P] Document the fast-forward in `README.md` — that it happens only for wait-for-merge repositories, that it never forces, and where its outcome appears in the log
- [X] T045 Walk `quickstart.md` end to end, including every row of its refusal table, and correct the document wherever the built behaviour differs from what it claims
- [X] T046 Run `uv run ruff check` and `uv run pytest`; both must pass (constitution: implementation is not complete until the suite passes)
- [X] T047 Re-read the Constitution Check in `plan.md` against the code as built, and correct the plan if the implementation diverged from what it promised — particularly the enumerated Principle III gap

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no dependencies
- **Phase 2 (Foundational)**: depends on Phase 1 — **blocks all three stories**, because each
  reads `Config.effective_wait_for_merge`
- **Phase 3 (US1)**: depends on Phase 2 only
- **Phase 4 (US2)**: depends on Phase 2 only — independent of US1
- **Phase 5 (US3)**: depends on Phase 2 only — independent of US1 and US2
- **Phase 6 (Polish)**: depends on whichever stories are being delivered

### Story Independence

All three stories depend only on the foundational configuration and on nothing from each
other. US1 is the feature the issue asks for and is a complete deliverable alone; US2 is a
convenience for the author's own clone; US3 is visibility. Any subset ships.

### Within Each Story

Tests and implementation touch different files and may be written in either order — the
constitution explicitly does not mandate test-first. Within the implementation tasks:
`HoldReason` before the clause that returns it (T019 → T022); `unfinished_by_repo` before the
plan wiring (T020 → T021 → T022); the dataclass before the protocol before the implementations
before the caller (T032 → T033 → T034/T035 → T036).

### Parallel Opportunities

- T008 and T009 (foundational tests, different files)
- T010–T018 (US1 tests; T010–T016 share `test_ordering.py` so they are one file's worth of
  work, T017–T018 are a separate new file)
- T026–T031 (US2 tests, three different files)
- T037–T040 (US3 tests, two different files)
- T043 and T044 (both README, so sequential in practice — parallel only across writers)
- Whole stories: once Phase 2 is done, US1, US2 and US3 can proceed concurrently

---

## Parallel Example: User Story 2

```bash
# The boundary tests split cleanly across three files:
Task: "fast_forward success and already_current in tests/unit/test_git_boundary.py"
Task: "SimulatedVersionControl.fast_forward writes nothing in tests/unit/test_simulated_writers.py"
Task: "prepare calls it only when in force in tests/unit/test_worktree_prepare.py"
```

---

## Implementation Strategy

### MVP first (US1 only)

1. Phase 1 → Phase 2 → Phase 3.
2. **Stop and validate**: the gate holds, releases, isolates repositories, and records.
3. That alone closes the issue's second item. Ship it.

### Incremental delivery

1. Foundation → US1 (the gate — the MVP)
2. → US2 (the clone fast-forward)
3. → US3 (visibility)
4. → Polish (README, quickstart walk, suite green)

Each step is independently valuable and none breaks a previous one.

---

## Notes

- The issue's **first** item — per-repository concurrency limits, global and per repository —
  already ships. No task builds it. T037 and T043 cover it by testing and documenting what is
  there, which is the honest way to close that half.
- No migration. If a task appears to need one, the design has drifted from R6 — stop and
  reconcile rather than adding a column.
- `ordering.plan` must stay pure. A task that wants a network call or a write inside it is a
  task that belongs somewhere else.
- Commit at each checkpoint; each is a coherent, working increment.


---

## Completion

All 47 tasks complete. `uv run ruff check` passes and `uv run pytest` reports **2143 passed,
1 skipped** — up from 2073 on the baseline, so 70 tests arrived with the feature. The one
skip is the pre-existing manual Trello round that needs real credentials.

Four test tasks name a different file above than the plan first guessed at, and each edit
says why in place. The pattern is the same every time: the fixtures those behaviours need
already existed somewhere, and a new file would have had to rebuild them to say less.
