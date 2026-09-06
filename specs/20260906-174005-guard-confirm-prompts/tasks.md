---

description: "Task list for: every confirmation prompt survives being given up on"
---

# Tasks: Every confirmation prompt survives being given up on

**Input**: Design documents from `specs/20260906-174005-guard-confirm-prompts/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/prompt-abandonment.md](contracts/prompt-abandonment.md)

**Tests**: required. The constitution's Development Workflow says every new or changed unit
of behaviour ships with unit tests, and that interruption paths carry tests of their own —
which is the whole of this feature.

**Organization**: grouped by the user stories in spec.md. US1 and US2 are both P1 and are
delivered by the same code; they are separate stories because they are separate claims
about it — "the attempt is recorded" and "nothing is treated as consent" — and each is
tested separately.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1, US2, US3 from spec.md

## Path Conventions

Single project: `src/robot_army/`, `tests/unit/`, `tests/integration/`, `docs/guide/`.

---

## Phase 1: Setup

**Purpose**: nothing to install and nothing to scaffold — no new dependency, no new module,
no configuration key. This phase exists to record that, and to establish the baseline the
"answered prompts are unchanged" claim is measured against.

- [ ] T001 Run `uv run pytest` from the repository root and record that the suite is green before any change, so a later failure is attributable to this feature
- [ ] T002 Read the four prompt call sites in `src/robot_army/operations.py` — `onboard`, `worktree_remove`, `cancel`, `purge_simulated` — and confirm against [research.md](research.md) R6 that no database transaction or network call is open at any of them

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the single guarded path every prompt goes through. Every user story below
depends on this phase; nothing in it is user-visible on its own.

⚠️ **Complete before starting Phase 3.**

- [ ] T003 Add the `PromptAbandoned` exception to `src/robot_army/operations.py`, carrying the `Result` the command would have returned, with a docstring saying why it carries a whole `Result` rather than a cause code (per [data-model.md](data-model.md))
- [ ] T004 Add the shared helper to `src/robot_army/operations.py`, beside `_ask`: it takes the prompt, the injected `confirm` callable, a `record(cause)` callback and the caller's `lines`/`data`; returns the answer; and on `KeyboardInterrupt`/`EOFError` calls `record` and raises `PromptAbandoned`. It holds the only copy of the two cause labels, the two exit codes and the two messages, per [contracts/prompt-abandonment.md](contracts/prompt-abandonment.md)
- [ ] T005 Write the helper's docstring to explain why the guard wraps the *injected* callable rather than living inside `_ask` — an injected `confirm` is how every caller and every test drives these prompts, so a guard in the default callable is bypassed by all of them (research.md R1)
- [ ] T006 Add `except operations.PromptAbandoned as gone:` to `main` in `src/robot_army/cli.py`, assigning `gone.result` to `result` so the existing `--json` rendering and stdout/stderr split apply unchanged, and placed so the existing `except KeyboardInterrupt` still catches interrupts raised outside a prompt
- [ ] T007 [P] Add `tests/unit/test_prompt_abandonment.py` with the helper's own tests: both causes produce the documented exit code, line and cause label; the `record` callback is called exactly once with that label; an answered prompt returns the answer untouched and calls nothing

**Checkpoint**: the helper exists and is guarded, but no command uses it yet.

---

## Phase 3: User Story 1 — Giving up at a destructive prompt is recorded, not crashed (P1)

**Goal**: an abandoned `worktree remove --force`, `cancel` or `purge-simulated` leaves a
record from which the command, its target and the cause can be read, and leaves the world
untouched.

**Independent test**: run each of the three with stdin closed and with an interrupt at the
prompt; check for a stated exit code, one line, no traceback, and a record in the audit log.

### Implementation

- [ ] T008 [US1] Route `worktree_remove`'s force prompt in `src/robot_army/operations.py` through the helper, with a `record` callback that sets `abandoned` and `cause` on the already-open `worktree.remove` outcome dict, and passing `result.data` so a `--json` run still renders one; let the exception propagate through `ctx.audit.action` so its own handler writes the `error` outcome
- [ ] T009 [US1] Route `cancel`'s prompt in `src/robot_army/operations.py` through the helper, with a `record` callback that writes a standalone `session.cancel` record — `outcome="error"`, `entity_type="session"`, `entity_id` the session id, detail carrying `abandoned`, `cause` and the item id — because nothing is open when `cancel` asks
- [ ] T010 [US1] Route `purge_simulated`'s prompt in `src/robot_army/operations.py` through the helper, with a `record` callback that writes a standalone `purge.simulated` record carrying `abandoned`, `cause` and the counts the prompt quoted
- [ ] T011 [US1] Route `onboard`'s prompt through the helper in `src/robot_army/operations.py` and delete its two now-duplicated `except` blocks, passing `_record_onboard_outcome` as the callback and `result.lines`/`result.data` so the approval screen and the document still ride along exactly as they do today

### Tests

- [ ] T012 [P] [US1] In `tests/unit/test_worktree_remove_guard.py`, add both-cause tests for the force prompt: nothing removed, no branch deleted, the exit code and line, and a `worktree.remove` intent/outcome pair whose intent names the path with `force: true` and whose outcome is `error` with `abandoned` and the cause
- [ ] T013 [P] [US1] In `tests/unit/test_cancel.py`, add both-cause tests: no signal sent to the session, the item and session rows unchanged, the exit code and line, and a `session.cancel` record naming the session and the cause
- [ ] T014 [P] [US1] In `tests/unit/test_prompt_abandonment.py`, add both-cause tests for `purge_simulated`: no rows deleted, the exit code and line, and a `purge.simulated` record carrying the counts and the cause
- [ ] T015 [P] [US1] In `tests/integration/test_onboard.py`, assert onboarding's two abandonment paths are unchanged after the refactor — same exit codes, same lines, same `repo.onboard` records with the same causes
- [ ] T016 [US1] In `tests/unit/test_cli_exit_codes.py`, add end-to-end tests running each of the four commands through `main` with a `confirm` that raises each cause, asserting the exit code, that the line is on stderr and that no traceback escapes

**Checkpoint**: US1 is independently deliverable — the three unguarded commands now record and exit cleanly.

---

## Phase 4: User Story 2 — Absent input is never read as consent (P1)

**Goal**: nothing an unanswered prompt guards ever happens, including at the typed-id prompt
where the answer is not `y`.

**Independent test**: run each of the four with `< /dev/null` and confirm the guarded action
did not happen and the exit is non-zero.

**Depends on**: Phase 2. Shares its implementation with US1 — the helper never returns a
value on the abandonment path, so there is nothing a caller could misread as an answer.
These tasks are the tests that say so, plus the stream work that makes the `--json` claim
in FR-008 true.

### Implementation

- [ ] T017 [US2] Change `cancel`, `purge_simulated` and `worktree_remove` in `src/robot_army/operations.py` to default `confirm` to `_ask` rather than builtin `input`, so every prompt is on stderr and a `--json` run's stdout carries one parseable document (research.md R5)
- [ ] T018 [US2] Rewrite `_ask`'s docstring in `src/robot_army/operations.py`: it no longer claims only `onboard` uses it, and it says why the earlier "nothing composed above the question" reasoning did not survive `--json` — naming milestone 011's FR-014 as superseded rather than leaving the file asserting something untrue

### Tests

- [ ] T019 [P] [US2] In `tests/unit/test_prompt_abandonment.py`, add a test per command that the guarded effect is nil after each cause: row counts unchanged, worktree still on disk, session state unchanged, repository still unapproved
- [ ] T020 [P] [US2] In `tests/unit/test_worktree_remove_guard.py`, add the specific claim that an absent answer is not a match for the item id — the removal does not happen and the outcome is an abandonment, not the existing `aborted` decline
- [ ] T021 [US2] Replace `test_onboard_prompts_through_that_helper_and_its_neighbours_do_not` in `tests/unit/test_cli_exit_codes.py` with a test that all four now default to `_ask`, and write its docstring to explain the reversal: FR-014 weighed a *screen* and did not weigh the machine-readable document that two of the three can be asked for
- [ ] T022 [US2] Update `test_the_other_prompts_keep_their_wording_and_their_stdout_stream` in `tests/unit/test_cli_exit_codes.py` — the wording assertions stay exactly as they are (FR-007), the name and the stream claim change
- [ ] T023 [US2] In `tests/unit/test_cli_exit_codes.py`, add a test that `purge-simulated --json` and `worktree remove --json`, given up on, put a parseable document on stdout and the prompt and explanation on stderr

**Checkpoint**: US2 is independently deliverable and the two P1 stories are complete.

---

## Phase 5: User Story 3 — The next prompt added inherits the handling (P2)

**Goal**: a fifth prompt is guarded without its author writing any handling.

**Independent test**: drive a prompt through the shared helper and see it guarded with no
per-call-site code; read the four call sites and see none of them handling an interrupt.

- [ ] T024 [US3] In `tests/unit/test_prompt_abandonment.py`, add a test that a command-shaped function that asks its question through the helper and writes nothing else is guarded for both causes — the claim that the fifth prompt inherits it, expressed as behaviour rather than as a grep
- [ ] T025 [US3] In `tests/unit/test_prompt_abandonment.py`, add a test asserting no `KeyboardInterrupt` or `EOFError` handler remains anywhere in `src/robot_army/operations.py` outside the helper, so re-introducing per-call-site handling fails the suite

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T026 [P] Update `docs/guide/audit-log.md`: the four abandonment records, their `cause` labels, and `session.cancel` as a new action name written only for an abandoned cancel — with the reason its successful path has no matching pair
- [ ] T027 [P] Update `docs/guide/operating.md` with what giving up at a prompt does: nothing happens, one line, the two exit codes, and where to find the record
- [ ] T028 [P] Update `docs/guide/5-outcome.md` where `cancel` and `worktree remove` are described, so the pages for those pipeline stages say what an unanswered prompt does
- [ ] T029 Run `uv run pytest` and confirm the whole suite passes, including every pre-existing test of an *answered* prompt, unmodified — that is the check that FR-007 held
- [ ] T030 Walk [quickstart.md](quickstart.md) end to end against a real configured install, including reading the records back with `jq`, and confirm the `purge-simulated < /dev/null` reproduction from issue #23 now behaves like `onboard` does

---

## Dependencies

```text
Phase 1 (setup)
   └─> Phase 2 (the helper + the one catch)     ← blocking
          ├─> Phase 3 / US1  (the three call sites + onboard's refactor)
          │      └─> Phase 4 / US2  (streams; its tests read the US1 call sites)
          │             └─> Phase 5 / US3
          └─> Phase 6 (docs can start any time after Phase 2; T029/T030 come last)
```

US2's implementation tasks (T017, T018) touch the same four call sites US1 edits, so US2
follows US1 rather than running beside it. Its *tests* are independent of that ordering.

## Parallel Execution

Within Phase 3: T012, T013, T014 and T015 are four different test files — run together.
T008 through T011 all edit `src/robot_army/operations.py` and are sequential.

Within Phase 6: T026, T027 and T028 are three different documentation pages — run together.

## Implementation Strategy

**MVP**: Phases 1–3. That is the issue's actual complaint fixed: the three unguarded
commands stop crashing and start recording. Everything after it is the part that keeps it
fixed.

Deliver in phase order. Each of Phases 3, 4 and 5 ends at a checkpoint where the suite is
green and the branch could be pushed.
