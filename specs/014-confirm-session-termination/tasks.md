---

description: "Task list for 014 — A Stop That Is Confirmed, Not Assumed"
---

# Tasks: A Stop That Is Confirmed, Not Assumed

**Input**: Design documents from `specs/014-confirm-session-termination/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/termination-outcome.md](./contracts/termination-outcome.md),
[quickstart.md](./quickstart.md)

**Tests**: Included, and not optional. The constitution's Development Workflow requires unit
tests for every new or changed unit of behaviour, and requires failure- and
interruption-path tests for state-machine and recovery logic — which is all this feature is.
FR-016 additionally requires a check that fails if the confirmation step is removed.

**Organization**: Grouped by user story. US1 and US2 are both P1 and touch the same two files;
US2 is still separable — it changes what is *said and recorded*, not what is *done*.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: [US1], [US2], [US3] from [spec.md](./spec.md)
- Exact file paths are given in every task

## Path Conventions

Single project: `src/robot_army/`, `tests/unit/`, `tests/integration/` at the repository root,
per [plan.md](./plan.md) → Project Structure.

---

## Phase 1: Setup

**Purpose**: Know the starting point, so a later red is this feature's and not something else's.

- [X] T001 Establish the baseline: run `uv run pytest` and `uv run ruff check .` at the repository root and confirm both are green before touching anything

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The return type every story depends on. `operations.cancel` cannot report or
withhold anything while `terminate` returns `None`.

**⚠️ CRITICAL**: US1 and US2 both depend on T002–T003. Nothing else does.

- [X] T002 Add the `TerminationOutcome` frozen dataclass (`confirmed: bool`, `method: str`, `escalated: bool`, `detail: dict[str, Any]`) beside `HostHandle` in `src/robot_army/boundaries/__init__.py` (around line 196), with a docstring stating the invariants from [data-model.md](./data-model.md): `confirmed` is the only field that may drive a state change, and `method == "none"` accompanies only `confirmed=False`
- [X] T003 Change the `SessionHost.terminate` signature in the protocol at `src/robot_army/boundaries/__init__.py:492` to `terminate(handle, scope=None, *, expected_start=None, proc_root=None) -> TerminationOutcome`, with a docstring carrying rules T1 (an exit status is never evidence) and T7 (`BoundaryError` only when there is nothing to try) from [contracts/termination-outcome.md](./contracts/termination-outcome.md)
- [X] T004 [P] Update `SimulatedSessionHost.terminate` in `src/robot_army/boundaries/dtach.py:296` to return `TerminationOutcome(confirmed=True, method="simulated", escalated=False, detail={"scope": scope})` and perform no `/proc` observation — case C10, rule T8; keep the existing simulated audit record and explain in a comment that a simulated pid is `0` by construction (`dtach.py:286`) so observing it would send every simulated cancel down the failure branch (FR-014)
- [X] T005 [P] Update `StubSessionHost.terminate` in `tests/conftest.py:566` to return a `TerminationOutcome`, and add a `terminate_confirmed: bool = True` constructor switch so tests can drive the unconfirmed case
- [X] T006 [P] Amend the `SessionHost` block of `specs/001-minimum-daemon/contracts/boundaries.md` (around lines 115-146) to the new signature and add a contract note that `terminate` confirms the effect rather than trusting the stop command's exit status — this belongs to the signature change and is **not** part of the optional US3 work

**Checkpoint**: the boundary speaks in outcomes. Story work can begin.

---

## Phase 3: User Story 1 — Cancelling a session actually ends it (Priority: P1) 🎯 MVP

**Goal**: A cancel that reports success has verified the tracked process is gone; a cancel that
cannot verify it fails and changes nothing.

**Independent Test**: Drive a scope stop that returns 0 while the pid is still present
(quickstart Scenario 1's C2, then Scenario 4 against a real session) and confirm the worker is
dead afterwards — or, if it survives everything, that the cancel exited non-zero and the item
never moved.

### Tests for User Story 1

> Write these first and watch them fail. C2 is the issue's reproduction; if it passes before
> the implementation lands, it is not testing what it claims to.

- [X] T007 [P] [US1] Create `tests/unit/test_terminate_confirmation.py` covering every case in [contracts/termination-outcome.md](./contracts/termination-outcome.md): C1 (scope stop, pid gone), C2 (**scope stop returns 0, pid still alive, group signal ends it — the regression test for issue #34**), C3, C4, C5 (already gone before anything is tried), C6 (survives the bound), C8 (scope but no pid), C9 (pid present with a *different* start time — a recycled pid is our process gone, and must not be signalled), C10. Drive liveness from a fixture `/proc` tree via `proc_root=`, in the style of `tests/unit/test_procinfo.py`; assert `confirmed`, `method` and `escalated` on the returned outcome, never the subprocess exit code
- [X] T008 [P] [US1] Create `tests/unit/test_cancel.py` covering caller obligations K1, K2 and K4: an unconfirmed outcome leaves the work item and session rows **byte-identical** and returns `EXIT_FAILED`; a confirmed outcome moves the item to `interrupted` and the session to `lost`; a session row already terminal when the settle is reached (K2) still reports success and attempts no transition; no `IllegalTransition` appears anywhere in the audit log in any case

### Implementation for User Story 1

- [X] T009 [US1] Add `TERMINATE_CONFIRM_TIMEOUT = 5.0` beside `PROBE_TIMEOUT` in `src/robot_army/boundaries/dtach.py` (around line 37) and a module-level `_confirm_gone(pid, expected_start, *, proc_root, deadline, sleep, clock) -> bool` helper that polls `procinfo.is_alive(pid, expected_start, root=proc_root)` until it reports gone or the bound elapses — rule T4: a bound that elapses yields "not confirmed", never success
- [X] T010 [US1] Rewrite `DtachHost.terminate` (`src/robot_army/boundaries/dtach.py:159-193`) as the confirm-after-every-rung ladder: check already-gone first (C5), then the scope stop, then `_confirm_gone`, then — **deleting the `if result.ok: return` at line 183** — fall through to `_signal_group` on a surviving process with `escalated=True`, confirm again, and return the `TerminationOutcome` built from what was observed. Keep `_signal_group` as written; it was correct and merely unreachable. Keep the `BoundaryError` for no-scope-and-no-pid (T7), and return `confirmed=False, method="none"` for scope-but-no-pid (T5/C8). Leave a comment naming the defect this replaces, so nobody restores the early return as an optimisation
- [X] T011 [US1] In `src/robot_army/operations.py`, import `transition_session` and `TERMINAL_SESSION_STATES` from `robot_army.states` (extending the line 59 import) and pass `expected_start=session.proc_start` and the session's `pid` through to `terminate` from `cancel` (around line 1533) — `proc_start` is not optional: `procinfo.is_alive(pid, None)` degrades to a bare existence check (`procinfo.py:120`), which is the pid-reuse bug wearing a different hat
- [X] T012 [US1] In `operations.cancel`, gate every state change on `outcome.confirmed` (K1): on an unconfirmed outcome return `EXIT_FAILED` **before** any transition is attempted, and on a confirmed one re-read the session row and the work item (K2) and skip the transitions if the session is already in `TERMINAL_SESSION_STATES` or the item has left `ACTIVE` — following `dispatch.py:852-861`, whose comment explains the same cross-process spool race
- [X] T013 [US1] In `operations.cancel`, add the `SessionState.LOST` transition for the confirmed case, in the same `db.transaction` as the existing `INTERRUPTED` transition, with reason `stopped by cancel (<method>); process confirmed gone` (FR-008, [data-model.md](./data-model.md) → State transitions). Leave the work-item reason wording unchanged
- [X] T014 [US1] Extend `tests/unit/test_web_actions.py` with an unconfirmed cancel from the web surface: assert it renders as a failure rather than the "cancelled" flash, and that the item is unchanged (FR-012). No change to `src/robot_army/web/server.py` should be needed — `_report` (line 421) already refuses any non-`EXIT_OK` result; if the test says otherwise, fix the test's setup before touching the server
- [X] T015 [US1] Add an integration test to `tests/integration/test_spool_recovery.py` for the race in [research.md](./research.md) R5: an exit record for the cancelled session is applied by the spool drain between confirmation and the settle. Assert the cancel still reports success, attempts no transition, and logs no `IllegalTransition`

**Checkpoint**: US1 is complete and independently demonstrable — quickstart Scenarios 1, 2, 4
and 5 all pass, and the issue's reproduction no longer leaves a live worker behind.

---

## Phase 4: User Story 2 — The report describes what was confirmed, not what was tried (Priority: P1)

**Goal**: The printed line and the durable record both distinguish a confirmed stop, an
escalated stop, and one that could not be confirmed.

**Independent Test**: Cancel under all three shapes and identify which occurred from the
printed line alone, and separately from the audit record alone.

### Tests for User Story 2

- [X] T016 [US2] Extend `tests/unit/test_cancel.py` with the three report shapes from [contracts/termination-outcome.md](./contracts/termination-outcome.md) K3: the confirmed line, the escalated line (which must say the scope reported success *and* that the session was still running), and the failure line (which must name the surviving pid and the `dtach -a <socket>` attach command, and must not contain the word "stopped" as a claim about the session)
- [X] T017 [US2] Extend `tests/unit/test_audit.py` with the FR-011 reconstruction test: from the `session.terminate` record alone, assert the rungs attempted, what each returned, `alive_after` for each, `escalated`, `confirmed`, and the outcome reported are all present — and specifically that an escalated cancel records **both** the first rung's reported success and the observation that contradicted it (FR-002)

### Implementation for User Story 2

- [X] T018 [US2] In `DtachHost.terminate` (`src/robot_army/boundaries/dtach.py`), build the audit `outcome` detail as the per-rung list described in [data-model.md](./data-model.md) → "What is written to the durable record": `rungs: [{method, exit|signal, ok, alive_after, waited_s}]` plus `escalated`, `confirmed`, `pid`, `proc_start` and `scope`. The existing `systemctl.stop` subprocess record stays as it is — its `exit: 0` is no longer the end of the story, and the `alive_after` beside it is what makes that legible
- [X] T019 [US2] In `operations.cancel`, replace the unconditional `stopped session … via systemd scope …` line (`src/robot_army/operations.py:1554-1558`) with the three K3 shapes selected on the outcome, and put `confirmed`, `method` and `escalated` into `result.data` so the web surface and any machine-readable run carry the same distinction. This sentence is the one the issue's "Also" section is about: it must never again state an effect nothing observed

**Note on sequencing**: T018 and T019 landed inside T010 and T013 rather than as separate
edits. The record's shape and the report's three forms are structural properties of the
functions US1 rewrote — writing them twice would have meant writing a dishonest version
first. T016 and T017 pin them either way, which is what the phase is for.

**Checkpoint**: US1 and US2 are both complete. A maintainer can trust the line they read and
reconstruct the rest from the log.

---

## Phase 5: User Story 3 — Termination is confirmed the way launching already is (Priority: P3)

**Goal**: The rule is written where the next boundary operation gets written, and a check fails
if confirmation is removed.

**Independent Test**: Read `contracts/boundaries.md` and find the rule; delete the confirmation
call from `terminate` and watch the suite go red.

- [X] T020 [P] [US3] Add the general rule to the preamble of `specs/001-minimum-daemon/contracts/boundaries.md`, naming both instances the project has now hit — `kitty @ launch` returning 0 with a valid window id for a session that never started (M0 F16, FR-025), and `systemctl --user stop` returning 0 for an already-inactive unit while a process still runs in its cgroup (issue #34) — and stating that any boundary operation with an observable effect MUST confirm that effect independently before reporting it ([research.md](./research.md) R8)
- [X] T021 [US3] Add a codebase-property test in the style of `tests/unit/test_no_cmdline_matching.py` — `tests/unit/test_effects_are_confirmed.py` — asserting that `DtachHost.terminate` never returns `confirmed=True` while the liveness observation reports the process alive, driven by patching `procinfo.is_alive` to answer "alive" unconditionally across every rung combination (FR-016). This must fail if the confirmation step is removed, which is the whole point of it

**Checkpoint**: all three stories complete.

---

## Phase 6: Polish & Validation

- [X] T022 Run `uv run pytest` and `uv run ruff check .` at the repository root; the full suite must pass (Development Workflow: implementation is not complete until it does)
- [X] T023 Walk quickstart Scenarios 1–3 in `specs/014-confirm-session-termination/quickstart.md`, including Scenario 2 — temporarily restore `if result.ok: return` and confirm the escalation test goes red, then restore the fix. A check that cannot fail is not a check
- [ ] T024 Walk quickstart Scenarios 4 and 5 against a real session at `no-remote` or `live`: the issue's own reproduction (scope already inactive, worker alive) must end the worker and report the escalation, and an unkillable session must exit non-zero and leave the item `active`. This is the only step that settles the issue, because the defect was invisible to every existing test. **Not done — left for the maintainer.** It requires dispatching a real worker against a real repository, which spends subscription quota and launches an autonomous session; that is the author's call to make, not something to do on the way past. Everything it validates is covered in the suite against a fixture `/proc` tree (C1-C10), but a fixture is not the kernel and this defect was invisible to every automated test that existed before it
- [X] T025 Commit in atomic pieces with messages explaining *why*: `src/robot_army/boundaries/__init__.py` (the outcome type), `src/robot_army/boundaries/dtach.py` (the ladder), `src/robot_army/operations.py` (the caller's state gate, then the reporting), and `specs/001-minimum-daemon/contracts/boundaries.md` (the rule) are five separate changes

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)**: no dependencies
- **Phase 2 (Foundational)**: blocks US1 and US2. Does **not** block US3's T020
- **Phase 3 (US1)**: after Phase 2
- **Phase 4 (US2)**: after Phase 3 — US2 edits the same two functions US1 rewrites, so running them concurrently would collide even though the stories are conceptually separable
- **Phase 5 (US3)**: T020 can start immediately; T021 needs Phase 3
- **Phase 6**: after everything intended to ship

### Task dependencies worth naming

- T003 depends on T002 (the protocol references the dataclass)
- T004, T005, T006 depend on T002/T003 but not on each other — all three are `[P]`
- T010 depends on T009 (the helper it calls)
- T012 and T013 depend on T011 (which supplies `expected_start` and the imports)
- T018 and T019 depend on T010 and T012 respectively — same functions, later concerns
- T021 depends on T010

### Parallel opportunities

- Phase 2: T004, T005, T006 together
- Phase 3 tests: T007 and T008 together (different new files), both before T009
- T020 is independent of everything and can be written at any point

---

## Parallel Example: Phase 2

```bash
# After T002 and T003 land, these three touch different files:
Task: "Update SimulatedSessionHost.terminate in src/robot_army/boundaries/dtach.py"
Task: "Update StubSessionHost.terminate in tests/conftest.py"
Task: "Amend the SessionHost block of specs/001-minimum-daemon/contracts/boundaries.md"
```

---

## Implementation Strategy

### MVP (US1 only)

T001 → T002–T006 → T007–T015. At that point the defect is closed: no cancel reports a stop it
did not verify, and no item is marked `interrupted` while its worker runs. Stop and walk
quickstart Scenario 4 before going further.

### Incremental delivery

1. Setup + Foundational → the boundary returns an outcome
2. **US1** → the stop is real, or it fails loudly (MVP — this is the issue)
3. **US2** → the report and the record say which of the three things happened
4. **US3** → the rule is written down and guarded

US3 is genuinely droppable. US2 is not, in practice: shipping US1 alone leaves the maintainer
reading the same confident sentence and having to guess whether this build means it.

---

## Notes

- `[P]` means different files and no incomplete dependency
- Three things not to get wrong, from [plan.md](./plan.md): always pass `proc_start`; never
  confirm with the dtach socket probe (`SessionHost.is_alive` answers a different question);
  never force the settle without re-reading first
- Commit after each task or logical group; stop at any checkpoint to validate independently
