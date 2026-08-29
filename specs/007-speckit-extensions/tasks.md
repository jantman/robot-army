---

description: "Task list for Spec Kit Awareness"
---

# Tasks: Spec Kit Awareness

**Input**: Design documents from `/specs/007-speckit-extensions/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: included, and not optional here. The constitution's Development Workflow section requires
unit tests for every new or changed unit of behaviour, and additionally requires failure- and
interruption-path tests for code that parses external input — which the phase ladder does, since
`tasks.md` is someone else's markdown. Test-first is **not** required ("the requirement is that the
tests exist and are meaningful, not the order they were written in"), so test tasks sit beside their
implementation rather than being gated ahead of it.

**Organization**: grouped by user story. Each story is independently deliverable and independently
testable, and stopping after any checkpoint leaves a working system.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: US1, US2, US3 — maps to the user stories in [spec.md](spec.md)
- Every task names the exact file it touches

## Path Conventions

Single project: `src/robot_army/` and `tests/` at the repository root, per
[plan.md](plan.md#project-structure).

---

## Phase 1: Setup

**Purpose**: the empty shapes everything else fills in. No project initialisation is needed — this is
an established package with its dependencies already resolved.

- [X] T001 Create `src/robot_army/speckit.py` with the module docstring, the frozen `Detection` and `Phase` dataclasses from [data-model.md](data-model.md), and the constants `LIFECYCLE = ("specify", "plan", "tasks", "implement")`, `SKILL_PATH`, `COMMAND_PATH`, `SCAFFOLD_PATHS`, and `RUNGS` — declarations only, no logic
- [X] T002 [P] Add a `speckit_worktree` fixture factory to `tests/conftest.py` that builds a throwaway directory with switchable parts: scaffolding present/absent, lifecycle commands in `skills` / `commands` / mixed / partial form, and an arbitrary set of `specs/<name>/` directories with chosen artifacts inside

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the detection predicate, which all three stories consult, and the configuration
resolution two of them display.

**⚠️ CRITICAL**: no user story work begins until T003 and T004 are done. Detection gates the prompt
block (US1), gates observation (US2, per [contracts/detection.md](contracts/detection.md) §4), and is
the entire content of the listing column (US3).

- [X] T003 Implement `detect(root)` in `src/robot_army/speckit.py` per [contracts/detection.md](contracts/detection.md) §1 — both halves required, `form` reported as `skills` / `commands` / `mixed`, every failure path returning a `Detection` with a log-ready `reason` and never raising
- [X] T004 [P] Write `tests/unit/test_speckit_detect.py` covering one case per row of the outcomes table: both halves present in each form and mixed, scaffolding without commands, commands without scaffolding, a partial command set naming which are missing, a `.specify` that is a file rather than a directory, an unreadable directory, and a nonexistent path
- [X] T005 Add the `[speckit]` section to `src/robot_army/config.py` per [contracts/config.md](contracts/config.md): `SpecKitConfig` with `enabled: bool = True`, parsing with unknown-key rejection matching the other sections, and `Config.speckit` wired into `parse()`
- [X] T006 Add `speckit: bool | None = None` to `RepoConfig` in `src/robot_army/config.py`, parse it in the `[repos.*]` handling, and implement `Config.speckit_enabled_for(key) -> tuple[bool, str | None]` returning the answer and the setting that produced it, following the shape of `permission_mode_for`
- [X] T007 [P] Write `tests/unit/test_speckit_config.py` covering the absent section defaulting to enabled, `enabled = false`, a per-repo `speckit = false` over a global true, a per-repo `speckit = true` over a global false, an unknown key in `[speckit]` raising `ConfigError`, and the provenance string returned in each case

**Checkpoint**: `detect()` answers correctly for every shape of repository on this machine, and the
configuration says whether a given repository wants the behaviour.

---

## Phase 3: User Story 1 — A Spec Kit repository gets a Spec Kit session (Priority: P1) 🎯 MVP

**Goal**: a dispatched session in a Spec Kit repository starts with the lifecycle, with no per-repository
file edit anywhere.

**Independent Test**: dispatch one issue into a Spec Kit repository with no `.claude/robot-army.md`
and confirm the prompt carries the block; dispatch one into a plain repository and confirm the prompt
is byte-identical to the pre-milestone golden string.

- [X] T008 [US1] Capture the current output of `prompt.compose()` for a fixture issue as a golden string in `tests/unit/test_speckit_prompt.py`, taken **before** T009 changes anything, so FR-010's byte-identity has a reference that predates the edit
- [X] T009 [US1] Add the `GUIDANCE` constant to `src/robot_army/speckit.py` with the exact text from [contracts/prompt.md](contracts/prompt.md) — fixed text, no interpolation of any kind
- [X] T010 [US1] Add an optional `speckit_block: str | None = None` parameter to `compose()` in `src/robot_army/prompt.py`, inserting it as its own `---`-separated section between the repository instructions and the issue block, and leaving the output byte-identical when it is `None`
- [X] T011 [US1] In `build_launch_plan()` in `src/robot_army/dispatch.py`, call `speckit.detect(worktree_path)` and `config.speckit_enabled_for(repo_key)`, pass the block to `compose()` only when detected and enabled, and write one `speckit.detect` audit record carrying `detected`, `reason`, `form`, `enabled`, `suppressed_by`, and `path` per [contracts/config.md](contracts/config.md)
- [X] T012 [US1] Wrap the detection call in `dispatch.py` so that no exception can escape into a dispatch (FR-005): any failure is recorded as a detection miss with its error text, and the launch proceeds with the unmodified prompt
- [X] T013 [P] [US1] Extend `tests/unit/test_speckit_prompt.py`: the block sits between repository instructions and the issue; the block is absent when undetected and the output equals the T008 golden string; the same issue composed twice is identical; a repository with `.claude/robot-army.md` keeps its instructions first
- [X] T014 [P] [US1] Write `tests/unit/test_speckit_dispatch_prompt.py` covering the four suppression paths — detected and enabled, detected and globally off, detected and off for this repository, undetected — asserting the prompt content and the audit record's `enabled` / `suppressed_by` fields for each
- [X] T015 [P] [US1] Add a test to `tests/unit/test_speckit_dispatch_prompt.py` that a detection raising `OSError` produces a recorded miss and a normal launch, proving FR-005 by injection rather than by inspection

**Checkpoint**: US1 is complete and shippable on its own. Sessions in Spec Kit repositories start the
way the author would have started them, and nothing else in the system has changed.

---

## Phase 4: User Story 2 — I can see which phase an active session reached (Priority: P2)

**Goal**: the stage of a running Spec Kit session is legible from the terminal and from a phone,
derived from files rather than reported by anyone.

**Independent Test**: dispatch into a Spec Kit repository, let a session produce a spec and then a
plan, and confirm the item view moves `specify` → `plan` within one reconciliation interval with no
cooperation from the session.

- [X] T016 [US2] Add `_migration_007` and `SCHEMA_007_SQL` to `src/robot_army/migrations.py` adding `speckit_baseline`, `speckit_phase`, `speckit_feature_dir`, and `speckit_phase_at` to `work_items`, and append it to the `MIGRATIONS` ladder — appended, never editing an existing migration
- [X] T017 [P] [US2] Add the four matching optional fields to `WorkItem` in `src/robot_army/models.py` and to the row mapper in `src/robot_army/db.py`
- [X] T018 [P] [US2] Extend `tests/unit/test_migrations.py` with the 006 → 007 upgrade: the columns exist, existing rows read back `NULL` for all four, and re-running `migrate()` is a no-op
- [X] T019 [US2] Implement `baseline(root)` in `src/robot_army/speckit.py` per [contracts/detection.md](contracts/detection.md) §3 — sorted immediate subdirectory names of `<root>/specs/`, `()` when absent, never raising
- [X] T020 [US2] Implement `observe(root, *, baseline)` in `src/robot_army/speckit.py` per §2 — candidate selection excluding the baseline, the four rungs highest-wins, the documented deterministic tie-break, `tasks.md` unreadable still counting as rung `tasks`, and `None` for every "nothing to say" case
- [X] T021 [P] [US2] Write `tests/unit/test_speckit_phase.py`: one case per rung, an empty feature directory, a `tasks.md` with only unticked boxes, one with `- [X]` and one with `- [x]`, a `tasks.md` that is invalid UTF-8, an absent `specs/`, and a `specs/` that is a file
- [X] T022 [P] [US2] Write `tests/unit/test_speckit_attribution.py`: six baseline directories full of ticked tasks yielding `None` (the stale-artifact trap), a new directory yielding its rung, work inside a baseline directory yielding `None`, an empty baseline treating every directory as the item's, and two new directories resolving deterministically
- [X] T023 [US2] Have `worktree.prepare()` in `src/robot_army/worktree.py` compute the baseline after the worktree exists and return it on `PreparationResult`, then store it as JSON in `dispatch.dispatch_item()` inside the **same transaction** that writes `worktree_path` and `branch`
- [X] T024 [US2] Add `record_phase()` to `src/robot_army/speckit.py` implementing the six write rules in [data-model.md](data-model.md): NULL baseline never observes, advance-only within a directory, a directory change recorded with both names, absence never clears, one record per transition, and the audit line flushed before the commit
- [X] T025 [US2] Add the observation pass to `reconcile()` in `src/robot_army/reconcile.py` over items in `active` and `awaiting_review` whose worktree exists — gated on `detect()` per [contracts/detection.md](contracts/detection.md) §4 — and add a `speckit_phase_changes` counter to `ReconcileResult.summary()`
- [X] T026 [P] [US2] Write `tests/unit/test_speckit_record_phase.py` with one test per write rule, including the two that are easy to get wrong: a removed worktree leaving the recorded phase standing, and a re-derivation of the same rung writing no second record
- [X] T027 [US2] Show the phase in `operations.show()` and the status listing in `src/robot_army/operations.py` — the rung, the feature directory, and how long ago — omitting the line entirely when there is no phase rather than printing an empty or unknown one
- [X] T028 [US2] Show the phase on the item view and as a badge on the active view in `src/robot_army/web/pages.py`, and include the four fields in the corresponding `.json` payloads
- [X] T029 [US2] Write `tests/integration/test_speckit_dispatch.py` driving the whole path: prepare a Spec Kit fixture worktree carrying finished features, dispatch, assert no phase; write `specs/999-x/spec.md`, reconcile, assert `specify` and exactly one record; write `plan.md`, reconcile, assert `plan`; reconcile again unchanged and assert no new record

**Checkpoint**: US1 and US2 both work independently. The daemon now says what stage a session is at,
and it is right about it in a checkout full of finished features.

---

## Phase 5: User Story 3 — I know in advance which repositories this changes (Priority: P3)

**Goal**: the author can list which onboarded repositories this behaves differently in, before
labelling anything.

**Independent Test**: run the listing against a mix of Spec Kit and plain clones, with the machine
offline, and confirm every row is right.

- [X] T030 [US3] Add a `spec-kit` column to the table in `operations.repos()` in `src/robot_army/operations.py` reporting `yes`, `no`, `off` (detected but suppressed), or `?` (clone unreadable), running `detect()` against the **primary clone** rather than a worktree
- [X] T031 [US3] Add `speckit` to the `repos` JSON payload in `src/robot_army/operations.py` with `detected`, `reason`, and `enabled`, so `/repos.json` and the CLI agree
- [X] T032 [P] [US3] Extend `tests/unit/test_repos.py` with one case per column value, including the never-recorded-location row keeping its existing shape with `?` in the new column
- [X] T033 [P] [US3] Add a test to `tests/unit/test_repos.py` asserting the listing performs no network call, by running it with the GitHub boundary replaced by one that fails on any use

**Checkpoint**: all three stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T034 Write `tests/integration/test_speckit_writes_nothing.py` proving FR-018 and SC-004: snapshot every path under a worktree with its size and content hash, run a full dispatch and a reconciliation pass, and assert the snapshot is identical — including ignored files, which a `git status` check would miss
- [X] T035 [P] Document the milestone in `README.md` — what a Spec Kit repository gets, the `[speckit]` section and per-repository override, where the phase appears, and the plain statement that nothing is written into a worktree and no extension file is read or produced
- [X] T036 [P] Add the four new columns to `docs/state.md` with their meaning, their nullability, and the rule that absence never clears a recorded phase
- [X] T037 [P] Add `speckit.detect` and `speckit.phase` to `docs/logging.md`, together with the Principle III omission this milestone claims: no record for a cycle in which nothing changed, and why
- [X] T038 Add the 007 entry to `docs/roadmap.md` — status, what it is, the decision not to use extensions with its reasoning, and an empty "What running it taught" section — and move the "whatever survives contact with reality" parking lot to 008
- [X] T039 Run `uv run ruff check` and `uv run ruff format --check` over the changed files and fix what they report
- [X] T040 Run the full suite with `uv run pytest` and confirm it passes — the constitution's completion gate
- [X] T041 Walk scenarios 1 through 5 of [quickstart.md](quickstart.md) by hand, including the stale-artifact trap in scenario 4, and note anything the fixtures did not predict

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (T001–T002)**: no dependencies
- **Foundational (T003–T007)**: needs T001. **Blocks every story** — T003 in particular, which all
  three consult
- **US1 (T008–T015)**: needs Foundational. No dependency on US2 or US3
- **US2 (T016–T029)**: needs T003 (the gate) and T001. Independent of US1 — observation does not care
  whether the guidance was sent, which is the property that lets a session that ignored the prompt
  still be observed correctly
- **US3 (T030–T033)**: needs T003 and T006. Independent of US1 and US2
- **Polish (T034–T041)**: T034 needs US1 and US2; the documentation tasks need whichever stories
  shipped; T040 is last

### Within US2

T016 → T017 → T023 (columns before anything writes to them) · T019, T020 → T024 → T025 (the
predicates before the rules that use them, the rules before the caller) · T024 → T027, T028 (nothing
displays a phase before one can be recorded)

### Parallel opportunities

- T004 and T007 while T005/T006 are in flight (different files)
- T013, T014, T015 together once T009–T012 are done
- T017, T018, T021, T022 together once T016 and T019/T020 land
- T032 and T033 together
- T035, T036, T037 together — three different documents
- US1, US2 and US3 can proceed in parallel after the Foundational checkpoint

### Parallel example: after the Foundational checkpoint

```bash
# Three independent stories, three independent files:
Task: "T009–T012 — the guidance block and its dispatch wiring in src/robot_army/{speckit,prompt,dispatch}.py"
Task: "T016–T020 — migration, model fields, baseline and observe in src/robot_army/{migrations,models,speckit}.py"
Task: "T030–T031 — the listing column in src/robot_army/operations.py"
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 Setup
2. Phase 2 Foundational — the blocking one
3. Phase 3 US1
4. **Stop and validate**: quickstart scenarios 1–3. Sessions in Spec Kit repositories now start with
   the lifecycle and nothing else in the system has moved.
5. This is a complete, defensible increment. The `.claude/robot-army.md` files that exist only to say
   "this repository uses Spec Kit" can be deleted at this point.

### Incremental delivery

1. Setup + Foundational → detection answers correctly
2. + US1 → the guidance ships (MVP)
3. + US2 → the phase becomes visible, including in a checkout full of finished features
4. + US3 → the behaviour is listable before it fires
5. + Polish → documented, linted, and walked through by hand

### Notes

- `[P]` means a different file with no incomplete dependency
- Commit per task or per logical group, with messages saying **why**, per the constitution
- The two tasks most likely to be got wrong quietly are **T022** (the stale-artifact trap — the whole
  reason the baseline exists) and **T034** (proving nothing is written, rather than believing it)
- No task adds a dependency, a boundary, a state, or a command. If one appears to need any of those,
  that is a signal to re-read [plan.md](plan.md)'s Constitution Check before writing it
