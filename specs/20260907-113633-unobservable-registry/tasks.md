---

description: "Task list for issue #44 — an observation that saw nothing is not evidence that nothing is alive"
---

# Tasks: An Observation That Saw Nothing Is Not Evidence That Nothing Is Alive

**Input**: Design documents from `specs/20260907-113633-unobservable-registry/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/observability-guard.md](./contracts/observability-guard.md),
[quickstart.md](./quickstart.md)

**Tests**: required. The constitution's Development Workflow makes unit tests mandatory for
every new or changed unit of behaviour, and requires failure- and interruption-path tests for
state machines and persistence. This feature is entirely about a failure path, so the tests are
the feature.

**Organization**: by user story, in priority order. There is no setup phase — the repository is
already set up and this feature adds no dependency, no migration, and no new module.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: which user story the task serves
- Every task names the file it changes

---

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: the predicate and the counter every guard depends on. Nothing in Phases 2–5 can
begin until these land.

**⚠️ CRITICAL**: T001–T004 block every user story.

- [ ] T001 Add `_registry_unobservable(scan: sessions.RegistryScan) -> str | None` to `src/robot_army/reconcile.py`, implementing the three clauses of [contracts/observability-guard.md](./contracts/observability-guard.md) C1 — `directory_missing`, `degraded`, and any non-empty `unknown_versions` — returning the reason string rather than a boolean because the reason is what the record carries (FR-011). Its docstring must state C2: why its third clause differs from `capacity._registry_unusable`'s, and that merging the two is forbidden.
- [ ] T002 [P] Add `liveness_withheld: int = 0` to `ReconcileResult` in `src/robot_army/reconcile.py` and to its `summary()`, beside `skipped_never_real`, with the comment saying why one counter and not three (data-model.md §2).
- [ ] T003 [P] Add `directory_missing` to the dict returned by `sessions.summarise()` in `src/robot_army/sessions.py`, so a pass across a vanished registry stops writing the same line as a pass across an idle machine (research.md R8).
- [ ] T004 Compute the reason once per pass in `reconcile.reconcile()` in `src/robot_army/reconcile.py`, immediately after `scan_registry` returns, and thread it to the three sweeps that need it. One observation per pass, decided once, so two halves of one pass cannot disagree about whether they could see.
- [ ] T005 [P] Add unit tests for the predicate in `tests/unit/test_registry_observability.py` covering all five registry conditions measured in research.md R2 — absent directory, unlistable directory, empty-but-present directory, all files on an unrecognised version, and a mixture of a known and an unknown version — asserting that only the empty-but-present case returns `None`, and that the returned reason names the condition.

**Checkpoint**: the predicate exists and is tested; no behaviour has changed yet.

---

## Phase 2: User Story 1 — A registry nobody can read does not interrupt everything (Priority: P1) 🎯 MVP

**Goal**: a pass that could not observe the registry stops marking every `active` item
`interrupted`.

**Independent Test**: three `active` items with live-looking sessions and a registry directory
that does not exist — `result.interrupted == 0`, all three still `active`; the same items against
an empty-but-present directory — still interrupted.

### Tests for User Story 1

- [ ] T006 [P] [US1] In `tests/unit/test_registry_observability.py`, assert the headline measurement in both directions: three `active` items with real pids and real `proc_start` values, parametrised over the four unusable conditions, produce `interrupted == 0`, `liveness_withheld == 3`, and leave every item `active` and every session `running`. Build the fixtures with `seed_item`, `seed_session` and `write_registry` exactly as research.md R1 did.
- [ ] T007 [P] [US1] In the same module, assert FR-004 by construction: with an unobservable registry, an `active` item with **no session record at all** is still marked `interrupted`, and an item whose session has `pid` `0` is still counted in `skipped_never_real` and left alone. These two are the registry-independent conclusions the guard's position must not reach (contracts C4.1).
- [ ] T008 [P] [US1] In the same module, assert that a session already recording `exited_clean` or `exited_error` is untouched by an unobservable pass, so the guard has not been placed above the exit-record check.

### Implementation for User Story 1

- [ ] T009 [US1] Add the guard to the active-item sweep in `reconcile.reconcile()` in `src/robot_army/reconcile.py`, at exactly the position fixed by contracts C4.1 — after the `not session.pid` check and immediately above the transition to `LOST`/`INTERRUPTED` — incrementing `liveness_withheld` and continuing. The comment must say that the position *is* FR-004's guarantee: every registry-independent branch has already returned, so those conclusions survive by construction rather than because a test remembered them.

**Checkpoint**: the reported defect is fixed. US2 and US3 are still open.

---

## Phase 3: User Story 2 — A blind pass does not hand back slots that are still taken (Priority: P1)

**Goal**: neither the stale-row sweep nor the superseded sweep closes a session record on a pass
that could not observe.

**Independent Test**: an open record under a finished work item, against an unobservable
registry — `reclaimed == 0` and the record still `running`; against an empty-but-present
directory — `reclaimed == 1` and the record `lost`.

### Tests for User Story 2

- [ ] T010 [P] [US2] In `tests/unit/test_registry_observability.py`, cover the stale-row sweep: a `done` work item with one open `running` session row, parametrised over the four unusable conditions, produces `reclaimed == 0` with the row untouched and `liveness_withheld` incremented — and, against an empty-but-present directory, `reclaimed == 1` with the row `lost`. The paired assertion is the point: the fix is wrong if it wins one half by losing the other.
- [ ] T011 [P] [US2] In the same module, cover the superseded sweep: an `active` item owning two open `running` rows against an unobservable registry produces `superseded == 0` with both rows still open, and against an empty-but-present directory closes the earlier attempt exactly as it does today.
- [ ] T012 [P] [US2] In the same module, assert SC-002 directly: take a `capacity.snapshot` before and after a blind pass over open rows and assert the total is unchanged. This is the harm the story exists to prevent — a row closed while its worker lives is a slot the cap hands out twice.
- [ ] T013 [P] [US2] In `tests/unit/test_slot_reclamation.py`, add a case asserting `reclaim_stale_session` returns the new `"withheld"` outcome for an unobservable scan and leaves the row exactly as found, alongside the existing cases for `"left"`, `"reported"` and `"reclaimed"`.
- [ ] T014 [P] [US2] In `tests/unit/test_cancel.py` or the module that covers `operations.abandon`, assert that abandoning an item while the registry is unobservable leaves the session row open and says so in the command's output, and that abandoning with a readable registry behaves exactly as it does today.

### Implementation for User Story 2

- [ ] T015 [US2] Add the guard to `reclaim_stale_session` in `src/robot_army/reconcile.py` at the position fixed by contracts C4.2 — after both `"left"` branches, before `scan.find` — returning the new `"withheld"` outcome. The docstring must explain why the guard lives in this function rather than at its callers: it already holds the rule "a worker that can be seen is reported, not closed", and blindness is a case of that same rule.
- [ ] T016 [US2] Handle `"withheld"` at its callers in `src/robot_army/reconcile.py`: `_sweep_stale_sessions` increments `liveness_withheld`; `_retire_one`'s `settled in ("reclaimed", "left")` test is left as it is, with a comment recording that it is unreachable while blind because it is only called after `scan.find()` returned a live entry (research.md R7).
- [ ] T017 [US2] Add the guard to `_sweep_superseded_sessions` in `src/robot_army/reconcile.py` per contracts C4.3 — after the two registry-independent skips, before `scan.find` — incrementing `liveness_withheld`.
- [ ] T018 [US2] In `src/robot_army/operations.py`, capture `reclaim_stale_session`'s outcome in `abandon` and, on `"withheld"`, add one line to the `Result` saying the session row was left open because the registry could not be observed and that reconciliation will settle it. Without it the maintainer is told the item was abandoned while a slot silently stays subscribed — the class of silence this feature is about.

**Checkpoint**: no sweep concludes death from a blind observation. The record is still silent.

---

## Phase 4: User Story 3 — A pass that could not see says so, once (Priority: P2)

**Goal**: the blindness is visible in the pass record and on the anomaly list, and retracts
itself when the registry returns.

**Independent Test**: run a blind pass sixty times — one open anomaly, not sixty; restore the
registry, run once more — the anomaly is gone with no maintainer action.

### Tests for User Story 3

- [ ] T019 [P] [US3] In `tests/unit/test_registry_observability.py`, assert the anomaly is raised once by a blind pass, that its `detail` carries the predicate's reason and the withheld count (FR-011), and that ten further identical passes leave exactly one open row — the partial unique index absorbing the repeat, as it does for `registry_version_unknown`.
- [ ] T020 [P] [US3] In the same module, assert the pass summary distinguishes the three cases data-model.md §3 tabulates: a usable observation, a blind pass with nothing running, and a blind pass with work in flight. Read them out of the `reconcile.pass` audit record, not out of `ReconcileResult`, because the record is what FR-007 is about.
- [ ] T021 [P] [US3] In `tests/unit/test_anomaly_resolution.py`, assert the third retractable kind: an open `registry_unobservable` is resolved by the first pass whose observation was usable, is then absent from `db.list_anomalies`, and a second such pass is a genuine no-op rather than a second write.
- [ ] T022 [P] [US3] In `tests/unit/test_anomalies_since.py` or the module asserting `ANOMALY_KINDS` is surfaced, confirm `registry_unobservable` appears in the "kinds this system can raise" line of `robot-army anomalies` (FR-065).

### Implementation for User Story 3

- [ ] T023 [P] [US3] Add `registry_unobservable` to `ANOMALY_KINDS` in `src/robot_army/models.py`, in the position and comment style of its neighbours, saying what the kind means and that it retracts itself.
- [ ] T024 [P] [US3] Add `open_registry_unobservable_anomalies(conn)` to `src/robot_army/db.py`, beside `open_orphan_session_anomalies` and `open_card_create_failing_anomalies` and in the same narrow shape, with a docstring saying why this kind qualifies for retraction: its condition can be positively re-established as false by an observation that succeeded.
- [ ] T025 [US3] Raise the anomaly in `reconcile.reconcile()` in `src/robot_army/reconcile.py` when the pass's observation was unusable — `entity_type=None`, `entity_id=None`, `dry_run=False` per data-model.md §4, `detail` carrying the reason, the withheld count, and the note. Raised at the *end* of the pass, so the count it records is the pass's final one rather than a partial.
- [ ] T026 [US3] Add `_resolve_registry_anomalies(conn, *, audit)` to `src/robot_army/reconcile.py`, in the shape of the two resolvers beside it, and call it on every pass whose observation was usable, adding its result to `anomalies_resolved`. Its docstring must record why the two cannot fight: one raises only when the observation failed, the other resolves only when it succeeded, and a single pass is one or the other.

**Checkpoint**: a blind pass is harmless *and* visible, and recovers without being acknowledged.

---

## Phase 5: User Story 4 — An idle machine is still an idle machine (Priority: P2)

**Goal**: prove the fix did not switch the safety sweep off, which is the same bug with the sign
flipped.

**Independent Test**: the existing suite. Every assertion about reconciliation was written
against an empty-but-present registry directory, so any of them breaking means the guard is
firing where the observation was usable.

- [ ] T027 [US4] Run `uv run pytest` and confirm the whole suite passes unchanged. This is the story's primary test and it is not a formality: the suite *is* the regression fence for "an empty directory is a usable observation".
- [ ] T028 [P] [US4] In `tests/unit/test_session_liveness.py`, make the invariant explicit rather than incidental: add a case asserting that an `active` item whose session process is gone is still `interrupted` against an empty-but-present directory, with `liveness_withheld == 0` and no anomaly of the new kind. The module's docstring gains a paragraph naming the guard as the thing that must not reach this case.
- [ ] T029 [P] [US4] In `tests/integration/test_reconcile_pass.py`, add a whole-pass scenario covering the recovery arc: a blind pass leaves three items alone and raises the anomaly, then a pass with the registry restored reaches the conclusions the visible registry supports and retracts the anomaly. This is the only test that exercises the two halves in sequence, which is what SC-006 asserts.

**Checkpoint**: all four stories are functional and the fix is fenced in both directions.

---

## Phase 6: Documentation & Cross-Cutting Concerns

- [ ] T030 [P] Document the new anomaly kind in `docs/guide/operating.md` beside `registry_version_unknown`: what makes an observation unusable, that a pass in that condition withholds its liveness conclusions rather than concluding death, that the anomaly retracts itself when the registry returns, and what the maintainer should actually check (a moved `XDG_RUNTIME_DIR`, a directory not yet created, a permission change).
- [ ] T031 [P] Record what `reconcile.pass` gains in `docs/guide/audit-log.md`, in the table that already tracks that record's shape: `liveness_withheld`, `directory_missing`, and how the three-way distinction in data-model.md §3 is read out of them.
- [ ] T032 Walk [quickstart.md](./quickstart.md) scenarios 1–6 against the built tree and correct either the guide or the code wherever they disagree. Scenario 6 (`abandon` while blind) is the only one that leaves pytest, and it is the one most likely to have drifted.
- [ ] T033 Confirm `test_only_effects_py_knows_the_effect_level_exists` still passes — nothing added to `reconcile.py`, comments included, may name the effect level (contracts C7.3). The test greps the file's text, so a comment mentioning `no-remote` fails the suite.
- [ ] T034 Run `uv run pytest` once more, then `git diff --stat` against the merge base, and confirm the changed-file set matches the tree in [plan.md](./plan.md) — no migration, no configuration key, no `capacity.py`, no `states.py`.

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Foundational)** — no dependencies; **blocks everything else**.
- **Phase 2 (US1)** — depends on Phase 1. Independently shippable: it fixes the reported defect.
- **Phase 3 (US2)** — depends on Phase 1. Independent of Phase 2; touches different sweeps.
- **Phase 4 (US3)** — depends on Phase 1, and on Phase 2 **or** Phase 3 for a non-zero
  `liveness_withheld` to record. The anomaly itself depends on neither.
- **Phase 5 (US4)** — depends on every guard being in place, because it asserts what they do
  *not* do.
- **Phase 6** — depends on all of the above.

### Within each story

- Tests before implementation. Each test in Phases 2–4 fails on the current tree for the right
  reason: the number it asserts is the number research.md measured.
- The two halves of every paired assertion (unusable vs. empty-but-present) belong in one test,
  not two. The property under test is the *separation*, and a fix that wins one half by losing
  the other is the same bug with the sign flipped.

### Parallel opportunities

- T002, T003 and T005 run alongside T001 once its signature is settled.
- Every test task marked [P] touches a different module or a different test in one module.
- Phases 2 and 3 are independent of one another and can proceed together.
- T023 (`models.py`) and T024 (`db.py`) are independent of each other and of the `reconcile.py`
  work in the same phase.
- T030 and T031 are different documentation pages.

---

## Implementation Strategy

### MVP

Phase 1 + Phase 2. That is the reported defect fixed: a registry nobody can read no longer
interrupts every running item. Stop there and the machine is safe, though still silent about it.

### Incremental delivery

1. **Phase 1** → the predicate exists, nothing behaves differently.
2. **Phase 2** → US1: the wholesale interruption stops. *Ship-able.*
3. **Phase 3** → US2: the capacity under-count stops. *Ship-able.*
4. **Phase 4** → US3: the blindness becomes visible and self-retracting.
5. **Phase 5** → US4: the fence in the other direction.
6. **Phase 6** → the two guide pages the constitution's rule requires.

### Notes

- Commit per logical group, with a message saying why. The four guards are four separate
  arguments and read better as separate commits than as one.
- `liveness_withheld` is incremented at three sites and asserted from one counter — check all
  three increments are reached by a test, or the counter will quietly under-report.
- The predicate must never grow a clause counting `scan.entries`. That clause is the defect.
