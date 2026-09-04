---

description: "Task list for one dispatch gate on every launch path (issue #120, RA-05)"
---

# Tasks: One dispatch gate on every launch path

**Input**: Design documents from `specs/20260904-062331-unify-dispatch-gate/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: **Required, not optional.** The constitution's Development Workflow states that
every new or changed unit of behaviour ships with unit tests, and that persistence and
recovery logic, state machines, and code parsing external input additionally carry tests for
their failure and interruption paths. This feature is a state machine change and a policy
gate, so both clauses apply.

**Organization**: grouped by user story. One honest note before the list — US2's
implementation is delivered by US1's code, because the pause and the holds travel through the
same `launch_holds` call as the caps. US2 is therefore a verification-heavy phase, and that is
a property of the design ([R1](research.md#r1)), not a gap in the task list. It stays a
separate phase because it is separately testable and separately deliverable: if US1 shipped
and US2's checks failed, the pause would be the thing still broken.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: which user story the task serves
- Every task names the exact file it touches

## Path Conventions

Single project. `src/robot_army/` and `tests/` at the repository root.

---

## Phase 1: Setup

**Purpose**: establish the baseline this change must not regress.

- [ ] T001 Record the pre-change baseline: run `uv run pytest` and `uv run ruff check` from the repository root, and note the passing test count — SC-006 and SC-009 are both measured against it
- [ ] T002 [P] Re-read `src/robot_army/ordering.py` lines 296–380 (`_hold_for`) and `src/robot_army/dispatch.py` lines 701–760 (`_dispatch_item`'s head) against [contracts/dispatch-gate.md](contracts/dispatch-gate.md), confirming the fixed order of operations before editing either

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the shared mechanism every story below calls. Nothing here changes behaviour —
Phase 2 is complete when the full suite passes unchanged.

**⚠️ CRITICAL**: no user story work can begin until this phase is complete.

- [ ] T003 Extract the `paused`, `held`, `capacity_unobservable`, `global_cap` and `repo_cap` branches of `_hold_for` into a new public `launch_holds()` in `src/robot_army/ordering.py`, returning `list[tuple[HoldReason, str]]` of **every** applicable condition in `HoldReason` declaration order, per [contracts/dispatch-gate.md](contracts/dispatch-gate.md); keep the function pure — no new import, no I/O, every input a keyword argument
- [ ] T004 Rewrite `_hold_for` in `src/robot_army/ordering.py` to call `launch_holds()` first and return its first element when non-empty, then fall through to the four unchanged queue-only branches (`awaiting_merge`, `not_onboarded`, `off_column`, `preparation_failed`); its returned value must be identical to today's for every input
- [ ] T005 Write the docstring for `launch_holds()` in `src/robot_army/ordering.py` explaining why it returns all reasons rather than the first (FR-007 wants one, FR-023 wants all, and one ordered evaluation gives both — [R2](research.md#r2)), matching the module's existing commentary density
- [ ] T006 [P] Add `class DispatchRefused(Exception)` to `src/robot_army/dispatch.py` carrying `hold: HoldReason | None` and `detail: str`, with a docstring stating that it is deliberately **not** a subclass of `DispatchBlocked` because every `except DispatchBlocked` in the codebase fails the item ([R3](research.md#r3))
- [ ] T007 [P] Add `tests/unit/test_launch_gate.py` with the pure-function tests for `launch_holds()`: each of the five conditions in isolation, an empty list when none applies, declaration order preserved when several apply at once, and `HELD` appearing once with both holds named when an item hold and a repo hold are both in force (FR-006)
- [ ] T008 Add a regression test to `tests/unit/test_holds_ordering.py` asserting `ordering.plan()` output is unchanged by the extraction — same entries, same positions, same hold reasons, same details, for a fixture exercising all nine reasons (SC-006)
- [ ] T009 Run `uv run pytest` and `uv run ruff check`; the suite must pass with the same count as T001

**Checkpoint**: the precedence now lives in one callable place, and nothing observable has
changed.

---

## Phase 3: User Story 1 — The session limit holds however a session is started (Priority: P1) 🎯 MVP

**Goal**: `resume` and `restart`, from the terminal and from the web, refuse to start a
session when the machine or the repository is at its limit, or when the count cannot be
established — leaving the item untouched and saying why.

**Independent Test**: with the limit set to a number already reached, invoke resume and
restart from the terminal and from the web interface. Four refusals, zero new sessions, and
`robot-army show <id> --json` identical before and after ([quickstart.md](quickstart.md)
steps 4, 5, 6).

### Implementation for User Story 1

- [ ] T010 [US1] Add `check_launch_gate()` to `src/robot_army/dispatch.py` per [contracts/dispatch-gate.md](contracts/dispatch-gate.md): take a **fresh** `capacity.snapshot` (FR-009 — never accept one as an argument), read `db.get_dispatch_control`, `db.list_item_holds` and `db.list_repo_holds`, call `ordering.launch_holds()`, and raise `DispatchRefused` carrying the first hold when non-empty
- [ ] T011 [US1] Record `dispatch.refused` with `outcome="error"` inside `check_launch_gate()` in `src/robot_army/dispatch.py`, carrying item id, `HoldReason`, detail and the calling surface (FR-013); record nothing at all when the gate permits
- [ ] T012 [US1] Call `check_launch_gate()` in `_dispatch_item` in `src/robot_army/dispatch.py`, positioned after the repository resolves and **before** the author check and every write, per the fixed order in [contracts/dispatch-gate.md](contracts/dispatch-gate.md); add the comment explaining why refusing precedes failing ([R5](research.md#r5))
- [ ] T013 [US1] Add `except DispatchRefused: raise` to `dispatch_item`'s handler in `src/robot_army/dispatch.py`, ahead of the generic `except Exception`, with a comment saying the refusal is already recorded and is not a `dispatch.error` ([R4](research.md#r4))
- [ ] T014 [US1] Add `force: bool = False` to `dispatch_item` and `_dispatch_item` signatures in `src/robot_army/dispatch.py` and thread it to `check_launch_gate()`; the override's own behaviour is US4's, this task only opens the path
- [ ] T015 [US1] Catch `DispatchRefused` in `select_and_dispatch` in `src/robot_army/dispatch.py`, record it, and return `dispatched` to end the pass rather than propagating out of the daemon tick; comment why the second snapshot can legitimately disagree with the planner's ([R9](research.md#r9))
- [ ] T016 [US1] Catch `dispatch.DispatchRefused` in `operations.resume` and `operations.restart` in `src/robot_army/operations.py` and return `Result(code=EXIT_PRECONDITION, ...)` with the summary line and the reason line from [contracts/cli.md](contracts/cli.md), plus `refused`, `hold` and `detail` in `data`
- [ ] T017 [US1] Add `require_dispatchable(ctx, item_id, action)` to `src/robot_army/web/server.py` beside the existing guards, raising `Refusal(reason, status=409, code=EXIT_PRECONDITION)`, and call it last inside `_slow_item_action`'s body per [contracts/web.md](contracts/web.md); document why the check runs twice ([R8](research.md#r8))

### Tests for User Story 1

- [ ] T018 [P] [US1] Extend `tests/unit/test_launch_gate.py` with `check_launch_gate()` tests: refuses at the machine-wide limit, refuses at a repository limit while a different repository passes, refuses when capacity is unobservable, permits an idle machine, and takes a fresh snapshot on every call
- [ ] T019 [P] [US1] Add tests to `tests/unit/test_launch_gate.py` proving a refusal writes nothing: state, `failure_reason`, `blocked_reason`, `dispatching_at` and `worktree_path` identical before and after, and no `state.work_item` record emitted (FR-010, FR-011, SC-004)
- [ ] T020 [P] [US1] Extend `tests/integration/test_dispatch_capacity.py` with the end-to-end case: two sessions live under a limit of two, then `operations.resume` and `operations.restart` each refused, then a slot freed and the same call succeeding on the first attempt (FR-012, SC-001)
- [ ] T021 [P] [US1] Extend `tests/unit/test_web_actions.py` with the request-thread refusal: `POST /item/<id>/resume` at the limit returns `409` with the reason in the body, the worker is **not** handed the action, and the `web.resume` audit pair closes with `outcome="error"` (FR-015)
- [ ] T022 [P] [US1] Extend `tests/unit/test_cli_exit_codes.py` asserting a gate refusal from `resume` and `restart` exits `3` (`EXIT_PRECONDITION`) and is distinguishable from an attempted-and-failed launch exiting `1` (FR-014)
- [ ] T023 [US1] Add a test to `tests/integration/test_dispatch.py` proving `select_and_dispatch` is unchanged for the same database, configuration and machine state — same items, same order, no extra hold record (SC-006)

**Checkpoint**: the limit now protects the subscription on all four launch paths. This is the
MVP; the finding's headline case is closed.

---

## Phase 4: User Story 2 — A pause and a hold stop every launch path (Priority: P2)

**Goal**: the author's own two statements of intent — the pause and the holds — bind resume
and restart exactly as they bind the queue, and the refusal names which one it was.

**Independent Test**: pause, then hold the item, then hold its repository, each in turn, and
attempt resume and restart on both surfaces; each refusal names that specific condition
([quickstart.md](quickstart.md) steps 1, 2, 3).

**Note**: the code paths are already in place from T010–T017 — the pause and the holds arrive
through the same `launch_holds()` call as the caps. This phase verifies them and fixes what
the verification finds; it adds no new mechanism, which is the design working as intended.

### Tests for User Story 2

- [ ] T024 [P] [US2] Extend `tests/unit/test_launch_gate.py`: `check_launch_gate()` refuses while `dispatch_control.paused`, and the message names `robot-army unpause` rather than `robot-army resume` (FR-005)
- [ ] T025 [P] [US2] Extend `tests/unit/test_launch_gate.py`: refuses on an item hold, on a repository hold, and on both — the last naming each with its timestamp and placing surface, and saying that releasing one leaves the other in force (FR-006)
- [ ] T026 [P] [US2] Add a precedence test to `tests/unit/test_launch_gate.py`: with the system paused **and** the machine at its limit, the refusal names `paused`; with an item held **and** the machine at its limit, it names `held` (FR-007, US2 AS5)
- [ ] T027 [P] [US2] Add a test to `tests/unit/test_holds_surfaces.py` asserting the launch's refusal detail is character-identical to the string `ordering.plan()` renders for the same item in the same condition (FR-008, SC-003)
- [ ] T028 [P] [US2] Extend `tests/integration/test_dispatch_capacity.py`: a paused system and a held item each refuse `resume` and `restart`, and each starts on the first attempt once the condition is lifted (SC-003, SC-004)

### Implementation for User Story 2

- [ ] T029 [US2] Fix whatever T024–T028 surface in `src/robot_army/dispatch.py` or `src/robot_army/ordering.py`; if nothing needs fixing, record that in the commit message rather than inventing a change

**Checkpoint**: all three brakes named in the issue now bind every launch path.

---

## Phase 5: User Story 3 — Only one dispatcher can win an item (Priority: P3)

**Goal**: of any number of processes racing to launch one item, exactly one starts a session.
No two agents in one worktree on one branch.

**Independent Test**: drive two launches of the same item so both pass their checks before
either claims it; exactly one proceeds and the other is refused with `another dispatcher
claimed it` ([quickstart.md](quickstart.md) step 8).

### Implementation for User Story 3

- [ ] T030 [US3] Add `class ClaimLost(Exception)` to `src/robot_army/states.py` carrying the item id and the state found instead
- [ ] T031 [US3] Add `claim_work_item()` to `src/robot_army/states.py` per [contracts/dispatch-gate.md](contracts/dispatch-gate.md): one `UPDATE work_items SET ... WHERE id = ? AND state IN (...)`, legal sources **derived** from `WORK_ITEM_TRANSITIONS` rather than written out, the same stamp columns and the same `state.work_item` record as `transition_work_item`, inside the caller's transaction (FR-019)
- [ ] T032 [US3] Raise `ClaimLost` from `claim_work_item()` in `src/robot_army/states.py` when `rowcount == 0`, re-reading the row once on that path only to distinguish a missing item (`LookupError`) from a wrong state, and writing nothing (FR-017)
- [ ] T033 [US3] Document in `claim_work_item()`'s docstring in `src/robot_army/states.py` why `transition_work_item` is deliberately left unmodified — reconciliation and spool replay depend on its no-op re-assertion, and FR-020 is satisfied by not editing it ([R6](research.md#r6))
- [ ] T034 [US3] Replace the `transition_work_item(..., target=DISPATCHING)` call in `_dispatch_item` in `src/robot_army/dispatch.py` with `claim_work_item(...)`, and translate `ClaimLost` into `DispatchRefused` with `hold=None` and the `another dispatcher claimed it` detail from [contracts/cli.md](contracts/cli.md)
- [ ] T035 [US3] Verify and adjust `dispatch_item`'s failure handler in `src/robot_army/dispatch.py` so a lost claim does **not** settle the item — the winner owns its state now, and the loser must not fail work it did not claim (FR-017, US3 AS3)

### Tests for User Story 3

- [ ] T036 [P] [US3] Add `tests/unit/test_claim_work_item.py`: the claim succeeds from `ready`, `interrupted` and `awaiting_review`; raises `ClaimLost` from `dispatching`, `active`, `failed`, `done` and `abandoned`; raises `LookupError` for a missing item; and its legal sources match `WORK_ITEM_TRANSITIONS` by derivation rather than by a second list (FR-018)
- [ ] T037 [P] [US3] Add to `tests/unit/test_claim_work_item.py` an assertion that a won claim writes the same columns and the same `state.work_item` record as `transition_work_item` would, so nothing downstream can tell which function moved the item
- [ ] T038 [P] [US3] Extend `tests/unit/test_states.py` proving `transition_work_item` still treats `source == target` as a no-op, using the reconciliation and spool-replay shapes that depend on it (FR-020)
- [ ] T039 [US3] Add a concurrency test to `tests/integration/test_dispatch_capacity.py` racing two claims on one item from two connections, repeated at least 50 times, asserting exactly one win and one `ClaimLost` every time with no exception (SC-005)
- [ ] T040 [P] [US3] Add a test asserting a lost claim leaves the item exactly as the winner left it — no state change, no `failure_reason`, no worktree removal (US3 AS3)

**Checkpoint**: the cross-process double dispatch is closed.

---

## Phase 6: User Story 4 — The author can override deliberately, from the terminal (Priority: P4)

**Goal**: `--force` starts a session past the author's own policy, records every condition it
went past, and reaches none of the safety checks.

**Independent Test**: with the machine at its limit, the system paused and the item held, run
`robot-army resume <id> --force`; the session starts and the log names all three
([quickstart.md](quickstart.md) step 7).

### Implementation for User Story 4

- [ ] T041 [US4] Implement the override branch in `check_launch_gate()` in `src/robot_army/dispatch.py`: when `force` is set and `launch_holds()` returned a non-empty list, record `dispatch.forced` with `outcome="ok"` carrying **every** hold (FR-023) and return instead of raising
- [ ] T042 [P] [US4] Add `force: bool = False` to `operations.resume` and `operations.restart` in `src/robot_army/operations.py`, pass it through to `dispatch_item`, and print the `overriding N conditions on item X: ...` line from [contracts/cli.md](contracts/cli.md) with `"forced": [...]` in `data`
- [ ] T043 [P] [US4] Add `--force` to the `resume` and `restart` parsers in `src/robot_army/cli.py` and to their dispatch lambdas, with help text naming what it overrides **and** what it does not — including that it differs from `cancel --force`, which skips a confirmation prompt ([contracts/cli.md](contracts/cli.md), [R7](research.md#r7))

### Tests for User Story 4

- [ ] T044 [P] [US4] Extend `tests/unit/test_launch_gate.py`: `force` proceeds past each of the five conditions, and past all of them at once, with a `dispatch.forced` record naming every one rather than only the first (FR-023, SC-008)
- [ ] T045 [P] [US4] Extend `tests/unit/test_launch_gate.py` asserting `force` does **not** bypass the author check, `check_gates`, the legal-transition table, or the atomic claim (FR-024, FR-025, US4 AS4)
- [ ] T046 [P] [US4] Extend `tests/unit/test_cli_exit_codes.py` proving `--force` reaches `dispatch_item` from both verbs, that its absence means `force=False`, and that no configuration key can set it (FR-022)
- [ ] T047 [P] [US4] Add a test asserting the web offers no override — neither route accepts a force parameter and `require_dispatchable` never passes one (FR-026)

**Checkpoint**: every requirement in the spec is implemented.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T048 [P] Update the RA-05 row in `docs/security-analysis.md`'s High table and its detail section at lines 514–559: mark it resolved, name `dispatch.check_launch_gate` and `states.claim_work_item` as what now enforces it, and **name the residual** from [R10](research.md#r10) — two concurrent launches of *different* items can still each see the same free slot (FR-027)
- [ ] T049 [P] Update `README.md` where `resume` and `restart` are described (around lines 954–959 and the web controls at 215–217): state that both are subject to the session limit, the pause and holds, describe `--force`, and say the web's escape hatch is lifting the condition rather than overriding it (FR-028)
- [ ] T050 [P] Add the launch gate to `docs/state.md`'s interruption table: killed before the claim writes nothing, the claim is one atomic statement, killed after it leaves `dispatching` for the existing reaper
- [ ] T051 Walk [quickstart.md](quickstart.md) steps 1–8 by hand against a running daemon and correct any step whose expected output does not match reality
- [ ] T052 Run `uv run pytest` and `uv run ruff check`; the suite must be green and the count must exceed T001's baseline by the tests added here (SC-009)
- [ ] T053 Re-read the diff against the Constitution Check in [plan.md](plan.md): no new dependency, module, table or configuration key; every new record present as the table describes; both enumerated Principle III gaps still accurate

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no dependencies
- **Phase 2 (Foundational)**: depends on Phase 1 — **blocks every user story**
- **Phase 3 (US1)**: depends on Phase 2
- **Phase 4 (US2)**: depends on Phase 3, because its verification exercises US1's wiring
- **Phase 5 (US3)**: depends on Phase 2 only — the claim is independent of the gate and can be
  built in parallel with US1 and US2 by anyone with the capacity to do so
- **Phase 6 (US4)**: depends on Phase 3 (T014 opens the parameter path)
- **Phase 7 (Polish)**: depends on every story that is going to ship

### Within Each Story

- T003 → T004 → T005 (one file, sequential)
- T010 → T011 → T012 → T013 → T014 (all in `dispatch.py`, sequential)
- T030 → T031 → T032 → T033 (all in `states.py`, sequential); T034 → T035 after
- T041 → T042 → T043 (the parameter has to exist before the flag can pass it)
- Tests within a story marked [P] touch different files and can run together

### Parallel Opportunities

- T006 and T007 alongside T003–T005 — different files
- **US3 (Phase 5) alongside US1 and US2** — the only genuinely independent story here, and the
  largest parallel win available
- T018–T023, T024–T028, T036–T040, T044–T047: each group is different files
- T048, T049, T050 in Phase 7 are three different documents

---

## Parallel Example: User Story 1

```bash
# After T010–T017 land, the five test tasks touch five different files:
Task: "T018 check_launch_gate condition tests in tests/unit/test_launch_gate.py"
Task: "T020 end-to-end cap refusal in tests/integration/test_dispatch_capacity.py"
Task: "T021 request-thread 409 in tests/unit/test_web_actions.py"
Task: "T022 exit code 3 vs 1 in tests/unit/test_cli_exit_codes.py"
Task: "T023 dispatcher-unchanged regression in tests/integration/test_dispatch.py"
```

---

## Implementation Strategy

### MVP first (User Story 1 only)

1. Phase 1 → Phase 2 → Phase 3.
2. **Stop and validate**: [quickstart.md](quickstart.md) steps 4, 5 and 6.
3. At this point the finding's headline is closed — the limit that protects one subscription
   is enforced on all four launch paths.

### Incremental delivery

1. Setup + Foundational → the precedence lives in one place, nothing observable changed.
2. US1 → the cap holds everywhere → validate → ship.
3. US2 → the pause and the holds hold everywhere → validate → ship.
4. US3 → exactly one dispatcher wins → validate → ship.
5. US4 → the deliberate override → validate → ship.
6. Polish → the documents say what the code does.

Each step leaves the system correct and the suite green. None of them requires the next.

---

## Notes

- Commit at each checkpoint, with a message saying **why** — the constitution asks for the
  reason, not the summary of the diff.
- `transition_work_item` in `src/robot_army/states.py` must not be edited by any task. If a
  task appears to need it edited, the design is wrong and FR-020 is at risk — stop and
  re-read [R6](research.md#r6).
- `ordering.plan`'s output must stay identical throughout. T008 is the guard, and it should be
  written before T003 lands rather than after.
- A refusal must never write to the database. T019 is the guard, and it belongs in the same
  commit as T010–T012 rather than later.
