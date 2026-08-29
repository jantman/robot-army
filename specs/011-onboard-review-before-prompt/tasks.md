---

description: "Task list for 011 — the onboarding approval screen reaches the terminal before the prompt"
---

# Tasks: Read Before You Approve

**Input**: Design documents from `/specs/011-onboard-review-before-prompt/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/onboard-output.md](contracts/onboard-output.md),
[quickstart.md](quickstart.md)

**Tests**: **required, not optional.** The constitution's Development Workflow section states
that every new or changed unit of behaviour MUST ship with unit tests and that the full suite
MUST pass before the feature is complete. Test-first is explicitly *not* mandatory, so each test
task sits beside the change it covers rather than ahead of it.

**One test shape carries this feature.** The defect was invisible to every existing test because
they all inspect `result.lines` *after* the command returns, where "before the prompt" and
"after the prompt" look identical. Research [R9](research.md) settles the only form that can
tell them apart: pass a real stream as `out`, and snapshot that stream **from inside the
injected `confirm`**, at the moment input is demanded. Every ordering assertion in US1 is made
on that snapshot. A test that asserts the screen "appears somewhere" in the final output is the
test that let this ship in the first place.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Every task names the exact file it touches

## Path Conventions

Single Python package at the repository root: `src/robot_army/`, `tests/unit/`,
`tests/integration/`. Two source files change, one test file is added, two are extended. No new
directory and no new module.

**A note on `[P]` in this feature.** Nearly every test task lands in
`tests/integration/test_onboard.py`, so US1's and US2's tests are strictly sequential despite
being independent in meaning. Marking them `[P]` would be convenient and wrong. The genuine
parallelism is between `src/robot_army/cli.py` and everything else, between the two test files
in US3, and across the three documentation files in Polish.

---

## Phase 1: Setup

**Purpose**: a branch to work on and a known-good starting point to compare five exit paths
against.

- [ ] T001 Create the working branch `011-onboard-review-before-prompt` from `main` — `setup-plan.sh` reported that name from `.specify/feature.json` but created no branch, and the repository is currently on `main`
- [ ] T002 Record the baseline in `specs/011-onboard-review-before-prompt/` working notes: run `uv run pytest -q` and confirm it is green before anything changes, and capture today's output of `robot-army onboard <some-repo>` answered `n`, so "unchanged from today" claims in US2 and US3 can be checked against something real rather than remembered

**Checkpoint**: green suite, and a recorded before-picture of the defect.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the one primitive all three stories are built on. Nothing else in the feature can
proceed without it.

**⚠️ CRITICAL**: no user story work can begin until this phase is complete.

- [ ] T003 Add `flush_to(stream)` to the `Result` dataclass in `src/robot_army/operations.py`, beside `say()` and `render()` (operations.py:69-81): write the accumulated lines to `stream` with `flush=True`, then clear `self.lines`; a `None` stream writes nothing and leaves the lines untouched. The docstring must say why the clearing is the point and not an optimisation — it is what makes "printed exactly once" structural across five exit paths instead of a rule to remember five times (FR-006, research [R2](research.md))
- [ ] T004 Add `tests/unit/test_result_output.py` covering `Result.flush_to` in isolation: it writes what was said; it flushes rather than buffering; it empties `lines` so a following `render()` repeats nothing; `None` writes nothing and keeps the lines; and a second call after further `say()` writes only the new lines

**Checkpoint**: the primitive exists and is proven. User stories can begin.

---

## Phase 3: User Story 1 — Seeing what is about to be trusted, before answering for it (Priority: P1) 🎯 MVP

**Goal**: at the moment `robot-army onboard` blocks for input, the entire approval screen has
already been written and flushed to its destination.

**Independent Test**: run onboarding with a real stream as `out` and a `confirm` that snapshots
that stream before answering; the repository identity, clone path with its source, verified
origin, base ref, trust verdict and committed settings are all present in the snapshot.

- [ ] T005 [US1] Add `out: TextIO | None = None` to the `operations.onboard` signature in `src/robot_army/operations.py` (operations.py:915-923), documenting that `None` means "do not flush" — the pre-011 behaviour, which every direct caller and every existing test keeps (research [R3](research.md))
- [ ] T006 [US1] Call `result.flush_to(out)` at the single boundary between screen and outcome in `operations.onboard` — after the `--reapprove` fingerprint-diff block and immediately before the "already onboarded and the fingerprint is unchanged" check (currently operations.py:1005-1012). Comment it as the one flush point, since a second one anywhere would silently reintroduce the doubling US2 exists to prevent
- [ ] T007 [P] [US1] Wire the stream in the `onboard` entry of `src/robot_army/cli.py`'s dispatch table (cli.py:393-395): pass `sys.stdout` for a human-readable run and `None` when `--json` is set, so the screen is never written into a machine-readable document (FR-012)
- [ ] T008 [US1] Add the ordering harness and the headline ordering test to `tests/integration/test_onboard.py`: an `io.StringIO` as `out`, a `confirm` that appends `out.getvalue()` to a capture list before returning its answer, and assertions that the snapshot holds the repository line, the clone path with `(derived from [paths] repo_root)` or `(configured in [repos."…"])`, the verified origin with the remote it came from, the base ref, the trust verdict, and the full committed-settings text (spec acceptance 1 and 2; FR-001, FR-002, FR-003)
- [ ] T009 [US1] Add the no-committed-settings case to `tests/integration/test_onboard.py`: the line `no committed .claude/settings*.json at the base ref` is in the snapshot, so the prompt is never asked about a file the maintainer has not been shown either way (acceptance 3, FR-003)
- [ ] T010 [US1] Add the `--reapprove` ordering test to `tests/integration/test_onboard.py`: the `recorded path:` line, its `** CHANGED **` marker when the location moved, and the fingerprint-diff block are all in the snapshot (acceptance 4, FR-004)
- [ ] T011 [US1] Add the redirected-output test to `tests/integration/test_onboard.py`: `out` is a real file on disk, and the `confirm` callback reads that path back **from a fresh handle** rather than inspecting the writer's buffer, proving the screen was flushed and not merely written (acceptance 6, FR-005 — the one assertion a terminal cannot make)
- [ ] T012 [US1] Add the informed-decline test to `tests/integration/test_onboard.py`: answer `n` through the ordering harness, assert the screen was in the snapshot and that `db.get_repo` still returns `None` afterwards — reading costs nothing and records nothing (acceptance 5, FR-010)
- [ ] T013 [US1] Confirm the four existing composition tests still pass untouched with no `out` supplied (`test_onboard.py:135`, `:154`, `:445`, and the audit-detail test at `:170`). They assert on `result.lines` and answer whether the screen is *right*, which is a different question from when it arrives. If any of them needs editing, stop — the change is wrong (research [R9](research.md))

**Checkpoint**: the issue is fixed. `robot-army onboard` shows what it is asking about before it
asks. Shippable alone.

---

## Phase 4: User Story 2 — One screen, printed once, on every way out (Priority: P2)

**Goal**: each approval screen is emitted exactly once whichever way the run ends, and each run
still emits exactly one outcome line saying which way that was.

**Independent Test**: drive all five exits — approved, declined, already-current, `--yes`
refused, and refused during resolution — and confirm no line of the screen appears twice in the
combined output while each outcome line appears once.

- [ ] T014 [US2] Review the abort return in `operations.onboard` (operations.py:1038-1041): after T006 the flush has emptied `result.lines`, so `[*result.lines, "aborted"]` now yields exactly `["aborted"]`. Either simplify the splice or comment why it is safe — leaving it unexamined is how a later reader reintroduces the double print
- [ ] T015 [US2] Do the same for the `--yes` refusal return (operations.py:1018-1027), so the refusal follows the screen alone rather than re-carrying it
- [ ] T016 [US2] Add the approved-path once-only test to `tests/integration/test_onboard.py`: count a screen-unique marker (`clone path   :`) across the stream *and* the returned `result.lines` together, and assert it is exactly 1, followed by `onboarded <key>` exactly once (acceptance 1, FR-006, FR-007)
- [ ] T017 [US2] Add the declined-path once-only test to `tests/integration/test_onboard.py`: screen marker once, `aborted` once, exit 4 — the code that says "I decided not to" rather than "the system refused" (acceptance 2, FR-007, FR-008)
- [ ] T018 [US2] Add the `--yes`-over-unapproved-settings test to `tests/integration/test_onboard.py`: screen marker once, the `refusing --yes:` message once, exit 3 (acceptance 3)
- [ ] T019 [US2] Add the already-onboarded test to `tests/integration/test_onboard.py`: screen marker once, `already onboarded and the fingerprint is unchanged; nothing to do` once, exit 0, and `confirm` never called at all (acceptance 4)
- [ ] T020 [US2] Add the refused-during-resolution test to `tests/integration/test_onboard.py`: with `out` supplied, the stream receives nothing, because no approval screen was ever composed — only the refusal reaches the caller, still naming the cause and the edit that fixes it (acceptance 5, FR-009)

**Checkpoint**: the output of the fixed command is as readable as the fix makes it useful.

---

## Phase 5: User Story 3 — The exits from the screen stay accountable and stay machine-readable (Priority: P3)

**Goal**: every way out of the prompt leaves a record, and a machine-readable run can be both
answered and parsed.

**Independent Test**: interrupt a run at the prompt and find a `repo.onboard` record naming the
repository and an interrupted outcome; separately, run with `--json` on every exit path and
parse stdout as a single document each time.

- [ ] T021 [US3] Add a module-level prompt helper to `src/robot_army/operations.py` that writes the prompt to `sys.stderr` with `flush=True` then reads a line from stdin, and make it `onboard`'s default `confirm` in place of the bare `input` (operations.py:921). `cancel`, `purge_simulated` and `worktree_remove` keep `confirm: Any = input`, their stdout prompts and their exit codes — the asymmetry is deliberate and is the cheaper side of not changing three commands to fix one (FR-014, research [R10](research.md))
- [ ] T022 [US3] Wrap the `confirm(...)` call in `operations.onboard` (operations.py:1031) in `try/except (KeyboardInterrupt, EOFError)`. On `KeyboardInterrupt`: record cause `interrupted_at_prompt` through the existing `_record_onboard_outcome` and return `Result(code=EXIT_FAILED, lines=["interrupted"], data=result.data)` — today's exit code and today's message, so the only observable change is that the log now holds something. On `EOFError`: record `no_answer_available` and return `EXIT_CHECK_FAILED` with a line naming the missing answer, replacing the unhandled traceback that exists today (FR-011, research [R5](research.md), [R6](research.md))
- [ ] T023 [P] [US3] Render machine-readable output to stdout regardless of exit code in `src/robot_army/cli.py` (cli.py:295-298), leaving the human-readable stdout/stderr split exactly as it is. This makes `--json` do what its own help text at cli.py:242 already promises, and keeps the prompt (now on stderr) off the same stream as a failing run's document (FR-012, research [R4](research.md))
- [ ] T024 [US3] Add the interruption test to `tests/integration/test_onboard.py`: a `confirm` that raises `KeyboardInterrupt` yields exit 1 and the line `interrupted`, and the audit log holds one `repo.onboard` outcome with cause `interrupted_at_prompt` naming the repository and the resolved clone path (acceptance 1 and 2, FR-011)
- [ ] T025 [US3] Add the end-of-input test to `tests/integration/test_onboard.py`: a `confirm` that raises `EOFError` yields exit 4 and a record with cause `no_answer_available`, and no traceback escapes the call (research [R6](research.md))
- [ ] T026 [US3] Extend `test_every_refusal_writes_an_audit_outcome_naming_its_cause` in `tests/integration/test_onboard.py` (:342) to assert the invariant the feature establishes: **every** terminating path through `onboard` leaves exactly one `repo.onboard` outcome — approval or a refusal with a cause — across all five exits, up from four of five (SC-004)
- [ ] T027 [P] [US3] Add the machine-readable test to `tests/unit/test_cli_exit_codes.py`: with `--json`, stdout parses as one complete JSON document on a zero-exit run *and* on a non-zero-exit run, with no approval screen, prompt text or outcome prose in it (acceptance 3, FR-012)
- [ ] T028 [US3] Add the prompt-stream test to `tests/unit/test_cli_exit_codes.py`: onboarding's prompt text is written to stderr and not stdout, so the prompt stays visible and answerable while the machine-readable stream stays clean (acceptance 4)
- [ ] T029 [US3] Add the untouched-neighbours test to `tests/unit/test_cli_exit_codes.py`: `cancel`, `purge-simulated` and `worktree remove --force` keep their exact prompt text, their stdout prompt, and their exit codes (FR-014)

**Checkpoint**: all three stories functional and independently verifiable.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T030 [P] Add `interrupted_at_prompt` and `no_answer_available` to the `repo.onboard` cause table in `docs/logging.md` (:286), with the same framing milestone 005 used for the refusals it added — these close a gap where two non-zero exits wrote nothing, which is a bug fix rather than new behaviour
- [ ] T031 [P] Add a pointer from the "The approval screen" section of `specs/005-onboard-is-enough/contracts/onboarding.md` to [contracts/onboard-output.md](contracts/onboard-output.md), recording that 005 specified this ordering and 011 is what delivered it. Do **not** rewrite 005's contract — it is the record of what 005 decided, and the gap between it and the shipped code is the finding, not an error to erase
- [ ] T032 [P] Add the 011 entry to `docs/roadmap.md` and move "Whatever survives contact with reality" to the 012 slot, following the precedent the roadmap already records for 005 through 010 — a milestone with a shape displaces a parking lot without one, now seven times
- [ ] T033 Run `uv run pytest -q` and confirm the full suite is green — the constitution's completion gate, and the point at which the four untouched composition tests from T013 prove they were left alone
- [ ] T034 Walk checks 1–6 of [quickstart.md](quickstart.md) against a real configured install, with the redirected-output check (check 2) run for real — it is the one assertion no test and no terminal session can substitute for, and the one a plausible-but-wrong fix would fail

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: after Setup — **blocks all three stories**, since all three are built on `Result.flush_to`
- **US1 (Phase 3)**: after Foundational. No dependency on US2 or US3
- **US2 (Phase 4)**: after Foundational. Its implementation tasks read code T006 touches, so in practice it follows US1; its assertions stand alone
- **US3 (Phase 5)**: after Foundational. Independent of US1 and US2 in meaning, though T023's justification only bites once a prompt exists on stderr (T021)
- **Polish (Phase 6)**: after the stories that are being shipped

### Within each story

- T005 → T006 → T008 (the parameter, then the flush, then the proof)
- T007 can proceed as soon as T005 lands; it is the only task in `cli.py` during US1
- T021 → T022 (the helper before the handler that surrounds its call site)
- Every test task follows the change it covers; test-first is not required here (constitution)

### Parallel opportunities

- **T007** (`src/robot_army/cli.py`) runs alongside T008–T013 (`tests/integration/test_onboard.py`) — the only real parallelism in US1
- **T023** (`src/robot_army/cli.py`) runs alongside T024–T026 (`tests/integration/test_onboard.py`)
- **T024–T026** (`tests/integration/test_onboard.py`) and **T027–T029** (`tests/unit/test_cli_exit_codes.py`) are two independent files and can proceed together
- **T030, T031, T032** are three different documentation files with no ordering between them

Everything else is sequential, and honestly so: US1's six test tasks and US2's five all land in
`tests/integration/test_onboard.py`.

---

## Implementation Strategy

### MVP first (User Story 1 only)

1. Phase 1: Setup
2. Phase 2: Foundational — `Result.flush_to` and its unit test
3. Phase 3: US1
4. **Stop and validate**: run quickstart check 1 and check 2. Check 1 is the issue; check 2 is
   the one that separates a real fix from a fix that only looks right on a terminal
5. Ship it — issue #17 is closed by US1 alone

### Incremental delivery

1. Setup + Foundational → the flush point exists
2. US1 → the screen arrives before the question → **MVP, and the issue**
3. US2 → and it arrives exactly once, whichever way the run ends
4. US3 → and every way out leaves a record, and `--json` still parses
5. Polish → the log's cause table, the roadmap, and the pointer from 005's contract to what
   finally delivered it

### If anything has to be cut

US3's first half — the interruption and end-of-input records — is the piece the plan flagged as
reaching past the literal issue. Dropping T022, T024, T025 and part of T026 leaves stories 1 and
2 intact and restores today's behaviour on those two paths, including today's `EOFError`
traceback. T021, T023 and T027–T029 must stay together or go together: the prompt's move to
stderr and `--json`'s move to stdout are two halves of one decision (research [R4](research.md)),
and shipping either alone puts the prompt and the document on the same stream.

---

## Notes

- `[P]` = different files, no dependencies. US1 and US2 have almost none of it, and that is a
  fact about where the tests live, not an oversight.
- **There is exactly one `flush_to` call in the feature.** If a task finds itself adding a
  second, it is the wrong edit — the once-only guarantee in US2 is structural precisely because
  there is one call site (research [R2](research.md)).
- No task opens `src/robot_army/migrations.py`, `db.py`, or `models.py`. This feature changes no
  schema and adds no field; if an edit reaches for one of those files, re-read
  [data-model.md](data-model.md).
- Nothing that reaches the screen may carry a credential. `verified_line()` emits a normalised
  identity and never a raw URL, guarded by `test_onboard.py:445`; this feature changes when that
  line is printed, not what is in it.
- Commit after each task or logical group; messages explain why, per the constitution.
