---
description: "Task list for: the base ref comes from the repository, not from a guess"
---

# Tasks: The base ref comes from the repository, not from a guess

**Input**: Design documents in `specs/20260906-145347-detect-default-branch/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/base-ref.md](./contracts/base-ref.md),
[quickstart.md](./quickstart.md)

**Tests**: Required, not optional. The constitution's Development Workflow section makes unit
tests mandatory for every new or changed unit of behaviour, and adds failure-path tests for
code parsing external input. Both apply: the resolver is a four-step decision table, and
detection parses the output of a subprocess that is allowed to fail.

**Organization**: by user story. US1 is the reported bug — the screen. US2 is the half of the
bug that nobody has seen yet, because no `master` repository has been dispatched into. US3 is
the escape hatch, whose behaviour is *preserved*, so its phase is mostly tests and
documentation — that is the correct shape, not an omission.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: `[US1]`, `[US2]` or `[US3]`, mapping to spec.md's user stories

## Path conventions

Single project. `src/robot_army/`, `tests/unit/`, `tests/integration/`, `docs/guide/`,
`share/` at the repository root.

---

## Phase 1: Setup

**Purpose**: establish the baseline, and pin the bug before touching anything.

- [X] T001 Run `uv sync && uv run pytest` from the repository root and record that the suite is green **before** any edit, so a pre-existing failure is known now rather than attributed to this change later.
- [X] T002 Add a failing test to `tests/integration/test_onboard.py` that builds a clone whose default branch is `master` — `git init -b master` upstream, `git clone` it, so `refs/remotes/origin/HEAD` is real — with `.claude/settings.json` committed on `master`, and asserts the screen says `base ref     : master` and prints that file's contents. It must fail today with `main` and "no committed .claude/settings*.json at the base ref". This is the issue, in the suite.

**Checkpoint**: the bug is reproducible by `uv run pytest` and by nothing else.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the read and the resolver every story phase consumes. Nothing user-visible
changes in this phase.

**⚠️ All three story phases depend on this.**

- [X] T003 Add `default_branch(self, clone_path: str, remote: str) -> str | None` to the `VersionControl` protocol in `src/robot_army/boundaries/__init__.py`, placed beside `default_remote`. Docstring records **why** it has two answers rather than the three `remote_branch_head` insists on: nothing irreversible hangs off it, so "no such ref" and "could not ask" both mean *fall back and say so*; and that implementations MUST NOT contact the network or write to the clone.
- [X] T004 Implement `GitVersionControl.default_branch` in `src/robot_army/boundaries/git.py` as `git symbolic-ref --quiet refs/remotes/<remote>/HEAD` with `check=False` and `QUICK_TIMEOUT`, returning the name with `refs/remotes/<remote>/` stripped, else `None`. Docstring says why the full ref is read rather than `--short`: `--short` yields `origin/master`, and un-prefixing that assumes no `/` in the remote's name, whereas stripping a known prefix is exact.
- [X] T005 Implement `SimulatedVersionControl.default_branch` in `src/robot_army/boundaries/git.py` as a delegation to `self._real`, with a docstring naming the class's own rule — the subject is the operator's real clone, like `default_remote` and `remote_url` — and the specific cost of getting it wrong: an invented `"main"` would make a `plan`-level onboarding of a `master` repository print exactly the screen issue #150 is about.
- [X] T006 [P] Add unit tests to `tests/unit/test_git_boundary.py` for detection: a real clone of a `master` repository answers `master`; a repository whose remote was added but never fetched answers `None`; a clone whose remote is named something other than `origin` answers when asked about that remote; a path that is not a git repository answers `None` rather than raising; and the simulated boundary returns the real answer at every effect level.
- [X] T007 In `src/robot_army/config.py`, stop copying the worker value into sections: `base_branch=str(section.get("base_branch", worker.base_branch))` becomes `str(section.get("base_branch", ""))`, and `WorkerConfig.base_branch` plus its `_str("worker", "base_branch", …)` default become `""`. Comment states what `""` now means — *not stated* — and that the four existing consumers already read it that way because all four are spelled `repo.base_branch or config.worker.base_branch`.
- [X] T008 [P] Add unit tests to `tests/unit/test_config.py` pinning that a `[repos.*]` section omitting `base_branch` parses to `""` rather than inheriting `[worker] base_branch`, and that a section stating it keeps its own value.
- [X] T009 Add the frozen `BaseRef` dataclass (`ref`, `source`, `detail`) and `base_ref(config, key, vcs, clone_path, *, remote=None) -> BaseRef` to `src/robot_army/repos.py`, beside `select_remote`, implementing the four-step order in [contracts/base-ref.md](./contracts/base-ref.md). Detection is attempted only at step 2; `remote` defaults to `vcs.default_remote(clone_path)`; a `BoundaryError` from either call is step 2 declining, not a raise. Docstring explains why `source` and `detail` are both kept — the same split `Verification` makes between `cause` and `refusal` — and why detection outranks `[worker] base_branch` (research R3).
- [X] T010 [P] Add unit tests to `tests/unit/test_repos.py` covering all four rungs and their provenance: a stated per-repository value wins **and no git command runs**; detection wins over a stated `[worker] base_branch`; a clone that cannot answer falls back to `[worker] base_branch`; nothing stated anywhere yields `main` with `source="default"`; a clone with no remote at all skips detection; and a `BoundaryError` from `default_remote` or `default_branch` falls back rather than propagating.

**Checkpoint**: the resolver answers correctly in isolation. No surface uses it yet, and the
suite is green except T002.

---

## Phase 3: User Story 1 — onboarding a `master` repository shows `master` (Priority: P1) 🎯 MVP

**Goal**: the approval screen names the repository's real default branch, shows the settings
committed there, and says where the answer came from — so what is approved is what will be
honoured.

**Independent test**: T002 passes, and `--json` reports `base_ref: "master"`.

- [X] T011 [US1] Replace the base-ref expression in `operations.onboard` (`src/robot_army/operations.py`, currently `(section.base_branch if section else "") or ctx.config.worker.base_branch`) with `repos_mod.base_ref(...)`, passing `resolved.remote` — the remote identity was already verified against, so detection asks the same one that decided which repository this is.
- [X] T012 [US1] Print the provenance on the base-ref line in `operations.onboard`, in the shape the `clone path` line above it already uses: `base ref     : master   (detected from origin/HEAD)`. All four `detail` spellings come from the resolver, not from a second set of strings built here.
- [X] T013 [US1] Add `base_ref_source` and `base_ref_detail` to `onboard`'s `result.data` beside the existing `base_ref`, so the `--json` document answers the same question as the screen (FR-012's rule that the machine-readable form carries no human-readable text is respected: these are fields, not the screen).
- [X] T014 [US1] Add `base_ref_source` to the `repo.onboard` audit detail in `operations.onboard`, so the record answers *what branch was approved and what decided it*.
- [X] T015 [P] [US1] Extend `tests/integration/test_onboard.py`: the provenance appears for each of the four rungs; the recorded fingerprint for the `master` fixture covers the file committed on `master`; and the `repo.onboard` record carries `base_ref` and `base_ref_source`.
- [X] T016 [P] [US1] Update `docs/guide/1-setup.md`: the onboarding screen's base-ref line now carries its provenance, and the base ref is detected from the clone rather than assumed.
- [X] T017 [P] [US1] Update `docs/guide/audit-log.md`: `repo.onboard`'s detail gains `base_ref_source`.

**Checkpoint**: the reported bug is fixed on the screen the reporter was looking at. Dispatch
still resolves the old way.

---

## Phase 4: User Story 2 — the session is created from the branch that was approved (Priority: P1)

**Goal**: every other consumer of the base ref resolves it the same way, so no two surfaces
disagree and a `master` repository dispatches correctly.

- [X] T018 [US2] Add a failing integration test to `tests/integration/test_worktree.py`: a session prepared in a `master` repository with no `base_branch` configured anywhere creates its branch from `master` and fetches `master`. It must fail today against `main`.
- [X] T019 [US2] Resolve through `repos.base_ref` in `dispatch.check_gates` (`src/robot_army/dispatch.py`), replacing `repo.base_branch or config.worker.base_branch`, so the gate's fingerprint is computed at the ref onboarding approved.
- [X] T020 [US2] Resolve through `repos.base_ref` in `worktree.prepare` (`src/robot_army/worktree.py`). The existing preference for `<remote>/<base_ref>` as the start point is unchanged; record the resolved ref in the `worktree.prepare` audit detail exactly as it already records `base_ref`.
- [X] T021 [US2] Resolve through `repos.base_ref` in `cleanup`'s containment check (`src/robot_army/cleanup.py`), replacing `config.base_branch_for(item.repo_key)` — a branch is judged contained against the branch it was actually based on.
- [X] T022 [US2] Resolve through `repos.base_ref` in the two listings and the resume signals in `src/robot_army/operations.py`: the `repos` listing, the `worktrees` listing, and `_local_resume_signals` (whose cache key already includes the base ref, so a changed answer is observed afresh).
- [X] T023 [US2] Remove `Config.base_branch_for` from `src/robot_army/config.py` now that it has no callers. A second, clone-blind way to answer the same question is how two surfaces come to disagree.
- [X] T024 [US2] Take the branch name out of the wait-for-merge hold message in `src/robot_army/ordering.py` — "#12 is active and has not landed yet". Comment records why this one surface does not resolve: `ordering.plan` is pure by contract and recomputed on every web page render, and a hold *message* is not worth a subprocess there (research R4).
- [X] T025 [P] [US2] Extend `tests/integration/test_dispatch.py` (or the closest existing gate test) so the gate's fingerprint for a `master` fixture is computed at `master`, and an unchanged repository does not report a changed fingerprint.
- [X] T026 [P] [US2] Update `tests/unit/test_ordering.py` (and any hold-surface test asserting the old sentence) for the reworded message.
- [X] T027 [P] [US2] Update `docs/guide/3-selection.md` for the reworded wait-for-merge hold message. **No edit was needed**: the page already quoted the sentence without a branch name (`"#41 is awaiting_review and has not landed"`), so the reworded message is what it always claimed. Checked rather than assumed.

**Checkpoint**: every surface names the same branch; T018 and T002 both pass.

---

## Phase 5: User Story 3 — a configured base branch still wins (Priority: P2)

**Goal**: the override behaves exactly as it did, and the configuration surface tells the truth
about what decides.

- [X] T028 [US3] Render `[worker] base_branch` commented out in `src/robot_army/exampleconfig.py` (`active=False`, `why_commented=` a line saying the clone's default branch decides and this is the fallback when it cannot be read).
- [X] T029 [US3] Regenerate the committed example: `uv run robot-army example-config --output share/config.example.toml --force`, and confirm `tests/unit/test_example_config_drift.py` and `tests/unit/test_example_config.py` pass — the file must still load clean and configure nothing outward-facing.
- [X] T030 [P] [US3] Add the fifth row — *derived from the repository* — to the active-versus-commented table in `specs/20260905-124257-docs-overhaul-example-config/contracts/example-config.md`, and update the one-sentence summary of those rules in `CLAUDE.md`.
- [X] T031 [P] [US3] Update `docs/guide/configuration.md`: the resolution order in FR-003, spelled out, with `[worker] base_branch` described as the fallback for a clone that cannot answer and `[repos.*] base_branch` as the override that beats detection.
- [X] T032 [P] [US3] Add unit tests pinning the override end to end: a repository with `[repos."owner/name"] base_branch` set is unaffected by what its clone says, on the onboarding screen and at the dispatch gate.

**Checkpoint**: the override is provably untouched, and the shipped example no longer overrides
detection.

---

## Phase 6: Polish & cross-cutting

- [X] T033 Run `uv run pytest` and `uv run ruff check` (and `ruff format --check` if the CI workflow runs it) from the repository root; the whole suite must pass before this feature is complete.
- [X] T034 Walk [quickstart.md](./quickstart.md) by hand against a scratch `master` clone, including the four-rung table, and correct the document if any expected output differs from what the code prints.
- [X] T035 Re-read the docstrings written in T003, T004, T005 and T009 against the code as it finally stands. This codebase's convention is that a docstring explains why, and a *stale* why is worse than none — the specific risk here is a comment describing the precedence before T007 changed what an empty string means.

---

## Dependencies

- **Phase 1** pins the bug. **Phase 2** blocks everything.
- **US1 (Phase 3)** depends on Phase 2 only, and is shippable alone: it fixes the screen the
  issue is about.
- **US2 (Phase 4)** depends on Phase 2 only. Independent of US1 in code — different call sites
  — but sequenced after it because US1 is what was reported.
- **US3 (Phase 5)** depends on Phase 2 (T007's parse change is what makes "stated" mean
  something) and touches no code US1 or US2 touches.
- **Phase 6** last.

## Parallel opportunities

- T006, T008 and T010 are three test files against three separate units — parallel once their
  implementation tasks land.
- T015 through T017 (tests, two guide pages) are parallel within US1.
- T025 through T027 are parallel within US2.
- T030 through T032 are parallel within US3.

## Implementation strategy

**MVP is Phase 1 + Phase 2 + US1**: the reported screen tells the truth, and every other
surface behaves exactly as it does today. US2 is not optional in the end — it is the more
damaging half — but it is separable, so a red build in the middle of it never leaves onboarding
worse than it is now.
