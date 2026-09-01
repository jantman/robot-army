---

description: "Task list for: Refuse to Signal an Unverified PID During Termination"
---

# Tasks: Refuse to Signal an Unverified PID During Termination

**Input**: Design documents from `specs/20260831-184927-guard-terminate-pid/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/signal-refusal.md](./contracts/signal-refusal.md),
[quickstart.md](./quickstart.md)

**Tests**: **Required, not optional.** The constitution's Development Workflow makes unit tests
mandatory for every new or changed unit of behaviour, and this feature's central success criterion
(SC-001) *is* a test assertion: zero signals delivered. Test tasks are written before the
implementation they cover in each phase.

## ⚠️ Safety rule for every task in this file

**No task may deliver a real signal to a real process.** Do not create a session row carrying
pid `1` and run `robot-army cancel` against it — that is the action that destroyed the maintainer's
desktop session on 2026-08-31 and is the reason this feature exists. Every failure case is driven
through the unit suite against a fixture `/proc` tree with spied primitives. The only task that
touches a live machine is T025, a positive control that ordinary cancels still work.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US4)

## Path Conventions

Single project: `src/robot_army/`, `tests/` at repository root.

---

## Phase 1: Setup

**Purpose**: Establish the baseline so any regression introduced by this feature is attributable to
it rather than to pre-existing state.

- [X] T001 Capture the baseline: run `uv run pytest` and `uv run ruff check src tests` from the repository root and record that both are green before any edit. If either is red, stop and report — this feature must not be built on top of an unexplained failure.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The outcome vocabulary and the test-session safety net that every user story below
depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 [P] Add `refused_reason: str | None = None` to `TerminationOutcome` in `src/robot_army/boundaries/__init__.py`, and document the four invariants from `contracts/signal-refusal.md` S4 in its docstring: `refused_reason is not None` ⟺ `method == "refused"`; a refusal is always `confirmed=False`; a refusal is always `escalated=False`; a refusal attempted zero rungs and delivered zero signals.
- [X] T003 [P] Add an autouse fixture to `tests/conftest.py` that replaces the `os` reference **in `robot_army.boundaries.dtach`'s own namespace** with a guard object whose `killpg` fails the test loudly, and give it an opt-in escape for the tests that install their own spy. Scope it to that module reference only — patching an attribute on the real `os` module would leak into every other test in the session (research R8). This makes an accidental real signal during development impossible rather than merely unlikely.
- [X] T004 Extend `StubSessionHost.terminate` in `tests/conftest.py` to be able to return a refusal outcome (a `refuse_reason` constructor argument producing `TerminationOutcome(confirmed=False, method="refused", refused_reason=...)`), so cancel-level tests can exercise S-K1–S-K3 without reaching the real host. Depends on T002; same file as T003, so run it after T003 rather than beside it.

**Checkpoint**: the outcome type can express a refusal, and no test can signal anything by accident.

---

## Phase 3: User Story 1 — A cancel can never signal outside this session (Priority: P1) 🎯 MVP

**Goal**: `None`, `0`, `1`, and any pid whose process group resolves to `1` are refused before
anything is signalled. This alone removes the catastrophe.

**Independent Test**: drive each rejected value through `_signal_group` and through
`DtachHost.terminate` with signal delivery spied; the story passes when the spy's call list is
**empty**, the command reports a refusal, and no rung ran.

### Tests for User Story 1

- [X] T005 [P] [US1] Create `tests/unit/test_signal_refusal.py` covering `_signal_group` **directly** — the function the rest of the suite has always stubbed out, which is why a `kill(-1)` survived C1–C10 coverage (research R8). Replace dtach's `os` with a spy via `monkeypatch.setattr("robot_army.boundaries.dtach.os", FakeOs())`, then assert for input pid `0`, input pid `1`, and a live pid whose `getpgid` returns `1`: `BoundaryError` is raised **and the spy's `killpg` call list is empty**. Assert on the empty call list, not merely on the exception — proving the refusal branch is reachable is not the same as proving the signal is unreachable (contract S7, S-C1/S-C3/S-C8, SC-001).
- [X] T006 [P] [US1] Extend `tests/unit/test_terminate_confirmation.py` with the ladder-level refusal cases S-C1, S-C2, S-C3 and S-C8 (pid `1` with no recorded start time; pid `1` with a start time matching `/proc/1`; pid `0` on a non-simulated row; a live pid whose group resolves to `1`). Each asserts `confirmed is False`, `method == "refused"`, a `refused_reason` naming the rejected value, **and** that `systemctl` was never invoked and `_signal_group` was never entered — a refusal attempts nothing (contract S5).

### Implementation for User Story 1

- [X] T007 [US1] Add a module-level guard function to `src/robot_army/boundaries/dtach.py` — e.g. `_refusal_reason(pid: int | None) -> str | None` — returning a short human-readable sentence for `None`, `0` and `1`, and `None` otherwise. Name the value and the consequence in the sentence (pid `1` resolves to process group 1, and signalling it signals every process the user owns; pid `0` resolves through `getpgid(0)` to the *caller's own* group, measured as an ordinary pid, which is why rejecting `pgid <= 1` alone does not cover it — research R1).
- [X] T008 [US1] Call the guard at the very top of `DtachHost.terminate` in `src/robot_army/boundaries/dtach.py`, **before the milestone-014 liveness pre-check and before the scope rung**, returning `TerminationOutcome(confirmed=False, method="refused", refused_reason=...)`. Placing it before the scope rung is deliberate: a row whose pid has just been judged untrustworthy has no more trustworthy a scope (contract S5, research R5). Depends on T007.
- [X] T009 [US1] Re-validate inside `_signal_group` in `src/robot_army/boundaries/dtach.py`: reject the input pid (`0`, `1`) before calling `os.getpgid`, and reject a resolved `pgid <= 1` before calling `os.killpg`, raising `BoundaryError` in both cases. This is unreachable when T008 is correct; it exists so that no future path can reach `os.killpg` with a catastrophic argument, and the redundancy is the requirement rather than an accident of style (contract S3, S7). Depends on T007.
- [X] T010 [P] [US1] Record rules S1–S3 in the `SessionHost.terminate` docstring in `src/robot_army/boundaries/__init__.py`, beside the existing T1/T7 rules, so the protocol states what an implementation may signal and not only what it must confirm.

**Checkpoint**: the incident's exact input can no longer reach `os.killpg` by any route. This is a
shippable increment on its own.

---

## Phase 4: User Story 2 — A signal only ever reaches a positively identified process (Priority: P2)

**Goal**: a recorded pid with no recorded `proc_start` is a bare number, not an identity, and is
refused rather than signalled on the strength of the number alone. This is what let pid `1` through:
`procinfo.is_alive(1, None)` is `True` (measured).

**Independent Test**: cancel a session row with a live pid and an empty recorded start time, with
signalling spied; the story passes when nothing is signalled and the refusal names the missing
start time.

### Tests for User Story 2

- [X] T011 [P] [US2] Extend `tests/unit/test_terminate_confirmation.py` with S-C6 (live pid, `proc_start` absent → refused, reason names the missing start time), and add explicit regression assertions that S-C7 (live pid, `proc_start` **mismatching** → `already_gone`, `confirmed=True`) and S-C9 (ordinary live pid with matching start time → the full unchanged ladder) still behave exactly as they do today. A refusal and a recycled pid are different facts and must not collapse into one (FR-004, SC-005).

### Implementation for User Story 2

- [X] T012 [US2] Extend the guard in `src/robot_army/boundaries/dtach.py` to refuse a recorded pid that carries no recorded `expected_start`, positioned **before** the existing liveness pre-check so the refusal is not swallowed by `already_gone` (research R5 ordering table). Do not weaken `procinfo.is_alive`'s documented degradation for other callers — the stricter rule belongs to termination (contract S1). Depends on T008.

**Checkpoint**: the whole class is closed, not only the three known-catastrophic values.

---

## Phase 5: User Story 3 — The refusal is fully accounted for (Priority: P3)

**Goal**: a refusal is visible, distinguishable from every other outcome in the record, exits
non-zero, and settles nothing.

**Independent Test**: trigger each refusal and read the action log alone; the story passes when the
log answers which session, which value, why, and that nothing was signalled.

### Tests for User Story 3

- [X] T013 [P] [US3] Extend `tests/unit/test_cancel.py`: a refusal exits non-zero; the work item is still `ACTIVE` and the session row still `RUNNING`; `Result.data` carries `refused` and `refused_reason`; the message names the session id, the rejected field and its value; the message is **not** the unconfirmed-stop wording, which would claim a signal was sent (contract S-K1–S-K3); and no `state.session` or `state.work_item` record was written beside the `session.terminate` outcome.
- [X] T014 [P] [US3] Extend `tests/unit/test_web_actions.py`: a refusal through the `POST /item/<id>/cancel` route surfaces the refusal message and its non-`EXIT_OK` code rather than the `cancelled` success banner. `web/server.py:427` already returns the result lines for a non-zero code, so this is a regression assertion on existing machinery, not a new rendering path — confirm that and add no banner.

### Implementation for User Story 3

- [X] T015 [US3] In `src/robot_army/boundaries/dtach.py`, attach `refused: true`, `refused_reason`, and `signals_sent: 0` to the existing `session.terminate` action outcome on the refusal path. Introduce no new action name: a reader asking "what happened when I cancelled item 29" searches for the termination action. `signals_sent: 0` is stated explicitly because "we refused" and "we refused and sent nothing" are the same claim only if the reader trusts the code, and this feature exists because that trust was misplaced once (plan, Principle III). Depends on T008.
- [X] T016 [US3] Add the refusal branch to `cancel` in `src/robot_army/operations.py`: return `EXIT_FAILED` with a line naming the session, the rejected value and the reason, and pointing the maintainer at the session row to inspect. Place it before the existing `if not outcome.confirmed:` branch so the refusal does not inherit the "still running after signalling the process group" wording. Carry `refused` and `refused_reason` in `result.data` (contract S-K2, S-K3). Depends on T002, T008.

**Checkpoint**: a refusal is reconstructible from the log alone, and settles nothing.

---

## Phase 6: User Story 4 — A simulated session is never handed to the real host (Priority: P3)

**Goal**: close the one route that reaches this code through ordinary operation with no hand-edited
database — dispatch at `local`, raise the effect level, cancel — so a simulated session stays a
clean simulated stop instead of degrading into a refusal.

**Independent Test**: cancel a session record marked simulated while the configured effect level
would select `DtachHost`; the story passes when the simulated host handles it, the outcome is a
confirmed simulated stop, and the real termination path is never entered.

### Tests for User Story 4

- [X] T017 [P] [US4] Extend `tests/unit/test_effects.py`: at every simulated effect level `boundaries.session_host is boundaries.simulated_session_host` — one object, two names, because `SimulatedSessionHost` holds an `_alive` set and two instances would answer `is_alive` differently (research R7); at real levels the two differ; and `Boundaries.describe()` names `simulated_session_host` so the `daemon.start` record still accounts for every wired implementation.
- [X] T018 [P] [US4] Extend `tests/unit/test_cancel.py`: a session record with `dry_run=1` cancelled while the wired `session_host` is the real one is terminated by the simulated host, yields `confirmed=True` / `method="simulated"`, delivers zero signals, and never enters `DtachHost.terminate` (contract S-C10, S8, SC-007).

### Implementation for User Story 4

- [X] T019 [US4] In `src/robot_army/effects.py`, add `simulated_session_host: SessionHost` to `Boundaries`, wire it in `wire()` by constructing `SimulatedSessionHost(audit)` **once** and reusing that same instance for `session_host` when the level is simulated, and add the field to `describe()`. Leave `REAL_AT` untouched — the table stays data, not branches (research R7, plan Complexity Tracking).
- [X] T020 [US4] In `src/robot_army/operations.py`, have `cancel` select the host from the session record before building the handle — `ctx.boundaries.simulated_session_host if session.dry_run else ctx.boundaries.session_host` — and use that same host for the whole operation including `attach_command`. Set `HostHandle(simulated=bool(session.dry_run))` so the handle stops lying about what it describes. Decide from the record, never from the configuration in force at cancel time (FR-012, contract S-K4). Depends on T019.

**Checkpoint**: the go-live sequence no longer hands a `pid=0` row to the real host, and the guards
remain as the second layer behind it.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T021 [P] Add a pointer beside rule T7 in `specs/014-confirm-session-termination/contracts/termination-outcome.md` noting that `specs/20260831-184927-guard-terminate-pid/contracts/signal-refusal.md` adds a third outcome — refused — distinct from both "could not try" and "tried and it survived", so a reader arriving at the older contract is not left with a two-way distinction that no longer holds.
- [X] T022 [P] Update `docs/incident-2026-08-31-desktop-session-killed.md` with a short "what fixed it" section pointing at this feature directory and issue #69, and state explicitly that **#67 remains open**: this feature narrows what may be signalled and does not touch the systemd scope rung's blast radius. The incident write-up must not read as though the whole class was closed.
- [X] T023 Run `uv run ruff check src tests` and `uv run pytest` from the repository root; both must be green. The constitution makes a passing suite the completion gate, and this feature is a safety guard — a red suite here is not a partial success.
- [X] T024 Walk `quickstart.md` Scenarios 1–4 (all offline, fixture `/proc`, spied primitives) and confirm each expectation, including the negative ones: `already_gone` unchanged for a mismatching start time, and the ordinary ladder unchanged for a well-formed row.
- [ ] T025 **BLOCKED — nothing to cancel.** Run `quickstart.md` Scenario 5 on the real machine — the **only** live task, and a positive control: cancel a genuine running session with the interactive prompt (not `--force`), confirm it stops, the item becomes `interrupted`, and `robot-army log --item <id>` shows the same rungs and `confirmed: true` as before this feature, with `refused` absent.

  > **Not run on 2026-08-31.** `robot-army status` reports `0 ours, 4 other` and the `sessions`
  > table holds no row in `running` or `starting` (6 `exited_clean`, 2 `lost`); the daemon's
  > heartbeat is 4150s stale. `cancel` requires a live session, so there is none to use as the
  > positive control. Dispatching one would start a real Claude Code worker against a real
  > repository, which is well outside this feature's scope and not a decision to make on the
  > maintainer's behalf.
  >
  > Run instead, on the real machine, at effect level `live`: `robot-army cancel 23 --force`
  > exercised the changed `operations.cancel` through the real CLI and reported
  > `work item 23 has no running session to cancel` with exit 1 — the production wiring imports
  > and runs. The positive control that an *ordinary* cancel still stops a session is covered
  > offline by `test_the_guards_do_not_cost_an_ordinary_stop` and
  > `test_a_matching_start_time_still_takes_the_whole_ladder`; this task remains open for the
  > next time a real session is running.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup. **Blocks every user story** — the outcome type must
  be able to express a refusal before anything can return one.
- **US1 (Phase 3)**: depends on Foundational. No dependency on any other story.
- **US2 (Phase 4)**: depends on Foundational **and on T008** — it extends the guard US1 installs.
  This is a genuine sequential dependency, not an organisational one: US2 has no separate call site.
- **US3 (Phase 5)**: depends on Foundational and T008 (it reports the outcome US1 produces).
  Independent of US2 and US4.
- **US4 (Phase 6)**: depends on Foundational only. **Fully independent of US1–US3** and could be
  built first; it is last because it is a correctness refinement over a hazard the guards have
  already made harmless.
- **Polish (Phase 7)**: depends on all desired stories.

### Within each user story

Tests are written before the implementation they cover. Within `boundaries/dtach.py` the
implementation tasks are strictly sequential (T007 → T008 → T009/T012/T015) because they edit
overlapping regions of one file.

### Parallel Opportunities

- **T002 ∥ T003** — different files (`boundaries/__init__.py`, `tests/conftest.py`). T004 follows
  T003 (same file) and T002 (needs the field).
- **T005 ∥ T006** — different test modules, and T010 ∥ both (different file again).
- **T013 ∥ T014** — `test_cancel.py` and `test_web_actions.py`.
- **T017 ∥ T018** — `test_effects.py` and `test_cancel.py`.
- **T021 ∥ T022** — a spec contract and a docs file.
- **US4 ∥ US1/US2/US3** — the only story-level parallelism: it touches `effects.py` and a different
  region of `operations.py`, and shares no file region with the guard work.

### Parallel Example: User Story 1

```bash
# Both test modules first, together:
Task: "Create tests/unit/test_signal_refusal.py — _signal_group directly, killpg spied, zero calls"
Task: "Extend tests/unit/test_terminate_confirmation.py with S-C1/S-C2/S-C3/S-C8"
Task: "Record S1-S3 in the SessionHost.terminate docstring in boundaries/__init__.py"

# Then the guard, strictly in order (one file):
#   T007 -> T008 -> T009
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 — baseline.
2. Phase 2 — foundational (blocks everything).
3. Phase 3 — US1.
4. **STOP and VALIDATE**: `uv run pytest tests/unit/test_signal_refusal.py -v`, and confirm the
   `killpg` spy's call list is empty for every rejected value.

That is a complete, shippable fix for issue #69's severity: `kill(-1)` is unreachable. Everything
after it is principle and polish over a hazard that is already gone.

### Incremental Delivery

1. Setup + Foundational → the outcome type can say "refused" and no test can signal by accident.
2. **US1** → the catastrophe is removed. **Ship here if nothing else lands.**
3. US2 → the whole class is closed, not just the three known values.
4. US3 → the refusal becomes reconstructible from the log and honest at the terminal.
5. US4 → the ordinary go-live route stops producing refusals it should never have produced.

Each increment leaves the suite green and the system strictly safer than the one before it.

---

## Notes

- **This feature is a guard, so its tests are the deliverable.** An implementation that refuses
  correctly but is only tested through `terminate` proves the refusal branch is reachable, not that
  the signal is unreachable. T005 exists precisely because the existing suite made that mistake:
  every prior test stubbed `_signal_group` out, and the `kill(-1)` sat inside contract-documented,
  covered code for months.
- Commit after each task or logical group; messages explain why, per the constitution.
- Nothing here claims to fix **#67**. Confirming that the recorded target died still says nothing
  about what else died with it.
