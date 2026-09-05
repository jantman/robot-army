---

description: "Task list for retiring a finished item's session"
---

# Tasks: Retire a finished item's session

**Input**: Design documents from `/specs/20260905-121903-retire-finished-sessions/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md),
[contracts/session-retirement.md](./contracts/session-retirement.md),
[contracts/anomaly-resolution.md](./contracts/anomaly-resolution.md), [quickstart.md](./quickstart.md)

**Tests**: Required, not optional. The constitution's Development Workflow makes unit tests
mandatory for every new or changed unit of behaviour, and requires failure- and interruption-path
tests additionally for persistence, state machines and code parsing external input. All three
apply here, so test tasks are first-class and are not marked as optional anywhere below.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: US1, US2, US3 from [spec.md](./spec.md)
- Every task names the exact file it touches

## Path Conventions

Single project: `src/robot_army/`, `tests/unit/`, `docs/guide/` at the repository root.

---

## Phase 1: Setup

**Purpose**: establish the baseline this feature is measured against.

- [X] T001 Run `uv run pytest` and record that the suite is green before any edit, and note the current schema version reported by `uv run robot-army status` (expected: 11) so the migration in Phase 5 can be seen to move it to 12

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the one shared structure two story phases both write to. Doing it once here keeps US1
and US3 from colliding in the same dataclass.

**⚠️ CRITICAL**: complete before starting Phase 3 or Phase 5.

- [X] T002 Add `retired: int = 0` and `anomalies_resolved: int = 0` to `ReconcileResult` in `src/robot_army/reconcile.py`, and add both to `summary()` so they reach the existing `reconcile.pass` audit record and the `robot-army reconcile` output (FR-027, contract C5). No new audit action for the pass itself

---

## Phase 3: User Story 1 — Merging the pull request finishes the whole thing (Priority: P1) 🎯 MVP

**Goal**: an item that reaches `done` because its issue closed has its idle worker ended, its row
closed, its slot released and its terminal window gone, with no anomaly and no action by the
maintainer.

**Independent test**: seed a `done` item with a live, long-idle worker; run one reconciliation
pass; assert the process is gone, the row is `lost` with `ended_at`, capacity is back and
`robot-army anomalies` is empty. On the real machine, [quickstart.md](./quickstart.md) Scenario 1
does this against items 45 and 54.

### The idle signal

- [X] T003 [US1] Add `status_updated_at: int | None` to `RegistryEntry` in `src/robot_army/sessions.py`, parsed from `statusUpdatedAt` (epoch milliseconds) in `parse_entry`, defensively: a value that is not an `int` becomes `None` rather than raising, in the same style as the existing `sessionId`/`pid` handling, because a worker upgrade must not take the daemon down
- [X] T004 [US1] Add `RegistryEntry.idle_for(*, now_ms: int | None = None) -> float | None` to `src/robot_army/sessions.py` returning seconds idle, and `None` — never a number — when `status` is not exactly `"idle"`, when `status_updated_at` is `None`, or when it is in the future (data-model.md; contract C2 rule 5)
- [X] T005 [US1] Rewrite the `# status is displayed and never used for control decisions.` comment in `parse_entry` in `src/robot_army/sessions.py` to say what now depends on it and why that is safe: the `KNOWN_VERSIONS` gate still refuses a registry shape we have not seen, and every unknown answer resolves to "do not retire", so being wrong about the registry can delay a retirement but can never cause one (research R2)
- [X] T006 [US1] Extend `write_registry` in `tests/conftest.py` with `status: str | None = "idle"` and `status_updated_at: int | None = None` parameters, where `None` **omits** the key entirely. The default must leave every existing caller producing a non-retirable entry, so no existing test changes behaviour
- [X] T007 [P] [US1] Add parsing cases to `tests/unit/test_sessions.py`: `statusUpdatedAt` present and integral; absent; a string; a float; in the future; `status` absent; `status` set to something other than `"idle"`. Each must yield `idle_for() is None` except the first, and none may raise
- [X] T008 [P] [US1] Add a case to `tests/unit/test_sessions.py` proving the `.key` prohibition still holds with the new field in place — the existing "nothing opens a credential-shaped file" assertion must still pass, because the new field comes from the same already-decoded payload

### The invariant retirement rests on

- [X] T009 [P] [US1] Create `tests/unit/test_done_single_writer.py` asserting that `reconcile._resolve_closed_issues` is the **only** writer of `WorkItemState.DONE` in `src/robot_army/`, by grepping the source for assignments/targets of that value and asserting exactly one write site. The failure message must name `contracts/session-retirement.md` C8 and explain that a second route to `done` silently widens retirement's precondition (research R1). Use the same technique as the existing single-site tests for the session-host discriminator and the effect level

### The sweep

- [X] T010 [US1] Add `RETIRE_IDLE_SECONDS = 1800` to `src/robot_army/reconcile.py` with a docstring in the shape of `TRANSCRIPT_GRACE_SECONDS`'s: the two measured idle times (84 and 198 minutes), why erring long is nearly free (the transcript survives and `claude --resume` works, so a session ended early is recoverable), and why it is a constant rather than a configuration key — one caller, no second use in hand
- [X] T011 [US1] Implement `_retire_finished_sessions(conn, *, boundaries, audit, scan, proc_root)` in `src/robot_army/reconcile.py` over `db.list_sessions(include_simulated=True, states=[STARTING, RUNNING])`, applying contract C2's seven rules in order and returning the count of confirmed retirements
- [X] T012 [US1] Implement the retirement act itself inside T011's function per contract C3: log `session.retire` with `item_id`, `session_id`, `pid`, `proc_start` and `idle_s` **before** anything is signalled; use `boundaries.session_host` with **no record-driven host selection at all** — the `pid` guard of C2 rule 2 has already skipped every simulated row, and `test_only_cancel_selects_a_host_from_a_record` asks that a second such site be reconsidered rather than added (revised during implementation; C3 updated to match); call `host.terminate(handle, session.scope, expected_start=session.proc_start)`; then settle inside one `db.transaction` via `reclaim_stale_session(..., reason="retired: …")`; then log the outcome with `method`, `confirmed`, `escalated` and any `refused_reason`
- [X] T013 [US1] Wire `_retire_finished_sessions` into `reconcile()` in `src/robot_army/reconcile.py` **after** `_resolve_closed_issues` and **before** the `config.cleanup.on_issue_close` block, counting into `result.retired`. Add a comment giving all three halves of the position (contract C1, research R4), in the style of the positioning comments already on the neighbouring sweeps

### Tests for the sweep

- [X] T014 [US1] Create `tests/unit/test_session_retirement.py` with one case per rule of contract C2: item absent; item not `done`; `pid` falsey; no registry entry; entry not alive; `idle_for()` is `None` (status not `"idle"`); idle but under `RETIRE_IDLE_SECONDS`; and the retire case. Use `seed_item`, `seed_session`, `write_registry` and `write_proc` from `tests/conftest.py`. Confirm the tests fail before T011 exists
- [X] T015 [US1] Add the four outcome cases from contract C4 to `tests/unit/test_session_retirement.py`: `retired` (confirmed, row `lost` with `ended_at`, counted); `refused` (implausible pid — **assert nothing was signalled**, row untouched, not counted); `survived` (`confirmed=False` — row stays open, slot still held, `orphan_session` raised); `already_settled` (the row reached a terminal state between the decision and the settle — recorded as an ordinary outcome, **not** a failure). Also assert `method="already_gone"` counts as `retired`
- [X] T016 [US1] Add the silence case to `tests/unit/test_session_retirement.py` (contract C6, FR-004): a `done` item whose worker is idle for less than the threshold produces **no audit record at all** for that session and no column write, and is reconsidered on the next pass. The absence is the assertion
- [X] T017 [US1] Add ordering cases to `tests/unit/test_slot_reclamation.py` proving FR-009 holds by position rather than by suppression: a retired session's row is `left` when `_sweep_stale_sessions` reaches it later in the same pass, and — independently — even an open row would take `reclaim_stale_session`'s `reclaimed` branch rather than `reported`, because `RegistryEntry.alive()` re-reads `/proc` at call time while `scan` is a snapshot (research R4). Assert no `orphan_session` exists after the pass, on that pass and the next
- [X] T018 [US1] Add a case to `tests/unit/test_cleanup.py` proving the same-pass benefit: with `cleanup.on_issue_close = true`, a `done` item whose session is retired this pass has its worktree considered by `_cleanup_worktrees` in that **same** pass and is no longer recorded `skipped` for a live session (FR-015, SC-004). The two cleanup guards themselves must be unchanged

### The late exit record (FR-008, interruption path)

- [X] T019 [US1] ~~Add a branch to `apply_record` in `src/robot_army/spool.py` for a late exit record.~~ **Measured during implementation: no bug exists.** `_already_applied` already returns `True` for an `exit` whose session row is in any terminal state, `lost` included, so the record is a duplicate, the file is unlinked, and the drain counts it. `spool.py` is unchanged; research R7 is corrected in place and the tests in T020 stay, because retirement makes this race routine
- [X] T020 [P] [US1] Add cases to `tests/unit/test_spool.py`: an exit record arriving against a `lost` row settles quietly, is logged, and **the spool file is gone afterwards**; the same record applied twice is a duplicate rather than an error; and a kill between the signal and the settle leaves a dead process under an open row that the next pass's `_sweep_stale_sessions` reclaims with no manual step

### Found in review

- [X] T047 [US1] Make `_orphan_sweep` in `src/robot_army/reconcile.py` re-check `entry.alive(proc_root=...)` before raising, instead of trusting the pass's opening `scan` snapshot, and pass `proc_root` to it from `reconcile()`. Without this every ordinary retirement raised an `orphan_session` against the worker it had just killed — invisible in `robot-army anomalies` because `_resolve_orphan_anomalies` cleared it in the same pass, but visible as an inflated `result.orphans` and a raise/resolve pair in the log for every successful item. Add both regression cases to `tests/unit/test_session_retirement.py`: a full pass asserting `orphans == 0` and no `anomaly.resolved` record, and a genuine live orphan still being reported. Contract C7 and research R4 corrected to match

**Checkpoint**: User Story 1 is complete and independently deliverable. This alone answers #138 and
unwedges the machine — run [quickstart.md](./quickstart.md) Scenario 1 against items 45 and 54.

---

## Phase 4: User Story 2 — Ending a session by hand, whatever state its item is in (Priority: P2)

**Goal**: `robot-army cancel <id>` settles correctly for an item in a terminal state, instead of
killing the process and then reporting that it "had already recorded its own ending" while leaving
the row open.

**Independent test**: stop the session of an item in a terminal state; assert the row closes, the
slot is released, the item stays in its terminal state, and the message describes what happened.
Needs nothing from Phase 3.

- [X] T021 [US2] Fix the settling logic in `cancel()` in `src/robot_army/operations.py`: the `already_moved = current.state is not WorkItemState.ACTIVE` test currently routes every terminal item into the "settled by exit record" branch. Split the two facts it conflates — "the session already recorded its own ending" (still the exit-record branch) versus "the item is not `active`" (a session that must still be closed) (FR-018, FR-019)
- [X] T022 [US2] In the same function in `src/robot_army/operations.py`, close the session row for a confirmed stop under a terminal item and **leave the work item in its terminal state** — do not move it to `interrupted`, which is only correct for an `active` item — and make the returned message name the method that actually stopped it rather than asserting an ending the system did not observe
- [X] T023 [P] [US2] Add cases to `tests/unit/test_cancel.py` for an item in `done` and in `abandoned` with a live session: the row becomes `lost` with `ended_at`, the slot is released, the item state is unchanged, and the message says the session was stopped and by which method
- [X] T024 [P] [US2] Add the three preserved guards to `tests/unit/test_cancel.py` (FR-020), each as its own case: an implausible pid is refused with **zero signals sent** and the row handed over for inspection; a pid whose recorded start time no longer matches is `already_gone` rather than signalled; and a process that survives leaves the row open, reports the failure, and claims no released slot
- [X] T025 [P] [US2] Add a case to `tests/unit/test_cancel.py` for the genuine exit-record race that T021 must not break: a worker that recorded its own ending between the signal and the re-read is still reported as settled by the exit record, not as a failure
- [X] T026 [US2] Confirm by reading `git diff` that `cancel()`'s host-selection block is unchanged, and that `tests/unit/test_simulated_writers.py`'s assertion that this is the only place in the system picking an implementation from stored state still passes

**Checkpoint**: User Stories 1 and 2 are both complete and independently testable.

---

## Phase 5: User Story 3 — An anomaly that has resolved itself stops being reported (Priority: P3)

**Goal**: an `orphan_session` whose process is gone resolves automatically, distinguishably from a
maintainer acknowledging it.

**Independent test**: raise an `orphan_session`, end the process, run a pass, and assert the anomaly
is no longer listed, is visible under `--all` as resolved rather than acknowledged, and that the
same condition can be raised again later.

- [X] T027 [US3] Add migration 12 to `src/robot_army/migrations.py`: `ALTER TABLE anomalies ADD COLUMN resolved_at TEXT`, then `DROP INDEX idx_anomalies_open` and recreate it as `WHERE acknowledged_at IS NULL AND resolved_at IS NULL`. Carry the existing `COALESCE` wrapping unchanged, and comment why a resolved row must leave the index — otherwise the same condition could never be recorded again if it recurred (data-model.md, contract A5)
- [X] T028 [P] [US3] Add `resolved_at: str | None = None` to the `Anomaly` dataclass in `src/robot_army/models.py`
- [X] T029 [US3] Add `resolve_anomaly(conn, anomaly_id) -> bool` to `src/robot_army/db.py`, mirroring `acknowledge_anomaly` including the `WHERE … AND resolved_at IS NULL` guard that makes a repeated pass a no-op returning `False`
- [X] T030 [US3] Change `list_anomalies` in `src/robot_army/db.py` so `unacknowledged_only=True` filters on `acknowledged_at IS NULL AND resolved_at IS NULL`. This is the single change that makes the CLI, `status` and the web page all correct with no edit to any of them (contract A4)
- [X] T031 [US3] Implement `_resolve_orphan_anomalies(conn, *, audit, proc_root)` in `src/robot_army/reconcile.py` per contract A2: over unacknowledged, unresolved `orphan_session` rows, leave any row whose `detail` has no `pid`; leave any row whose `procinfo.is_alive(pid, proc_start)` is `True`; otherwise resolve. Inside one `db.transaction`, stamp `resolved_at` and record `anomaly.resolved` with `anomaly_id`, `kind`, `entity_id`, `pid`, `proc_start` and the reason
- [X] T032 [US3] Wire `_resolve_orphan_anomalies` into `reconcile()` in `src/robot_army/reconcile.py` **after** `_orphan_sweep`, counting into `result.anomalies_resolved`, with a comment giving the position's reason in the style of the neighbouring sweeps
- [X] T033 [US3] Make `robot-army anomalies --all` render a resolved row **visibly differently** from an acknowledged one in `src/robot_army/operations.py::anomalies` — they are different facts, and that difference is the whole reason `resolved_at` is not `acknowledged_at`
- [X] T034 [P] [US3] Create `tests/unit/test_anomaly_resolution.py` with one case per rule of contract A2: `detail` with no `pid` is left alone permanently; a live process leaves the row listed and does not duplicate it; a dead pid resolves; and a **recycled** pid — alive at that number but with a different start time — resolves, proving identity rather than the number is what is asked about (FR-024)
- [X] T035 [P] [US3] Add the recurrence case to `tests/unit/test_anomaly_resolution.py`, which is the one that catches a wrong index: raise an `orphan_session`, resolve it, raise the same `(kind, entity_type, entity_id)` again, and assert **two** rows exist — the resolved one and a new open one (contract A5)
- [X] T036 [P] [US3] Add the scope case to `tests/unit/test_anomaly_resolution.py` (contract A6): an anomaly of a kind other than `orphan_session` whose condition has passed is still listed and is untouched
- [X] T037 [P] [US3] Add migration cases to `tests/unit/test_migrations.py`: the schema reaches 12; existing rows come through with `resolved_at IS NULL`; the rebuilt index still dedupes an unacknowledged, unresolved anomaly; and a kill mid-migration leaves schema 11 intact with the migration re-running cleanly
- [X] T038 [P] [US3] Add a case to `tests/unit/test_web_views.py` (or `test_web_render.py`, whichever renders the anomalies panel) asserting a resolved anomaly is absent from the web interface, which follows from T030 without any change to `web/pages.py`

**Checkpoint**: all three user stories complete.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T039 [P] Update `docs/guide/5-outcome.md`: a new section before "Cleaning up" covering the session's ending — why a worker never ends itself, what `done` plus a long-idle worker now triggers, the 1800-second quiet period and why erring long is nearly free, that the transcript survives and the session stays resumable, and that `abandoned` and `failed` items keep their workers on purpose. Note the consequence for cleanup: a retired session is what lets `skipped` become `done`
- [X] T040 [P] Update `docs/guide/operating.md`: revise the `orphan_session` entry to say it no longer fires on the ordinary successful path, and add that an `orphan_session` whose process is gone now resolves itself while every other kind still waits for `--acknowledge`
- [X] T041 [P] Update `docs/guide/state.md`: the `anomalies` table's new `resolved_at` column, what distinguishes it from `acknowledged_at`, and that schema 12 rebuilds the partial unique index
- [X] T042 [P] Update `docs/guide/audit-log.md`: add `session.retire` and `anomaly.resolved` with their fields, add `retired` and `anomalies_resolved` to the `reconcile.pass` record's documented shape, and add a row to the deliberately-unlogged table for the not-yet-idle decision (FR-004) with its justification — the file already carries that table and this is the constitution's required enumeration
- [X] T043 Confirm no configuration key changed: `config.py`'s `_KNOWN_KEYS` and `_REPO_KEYS` are untouched, so `share/config.example.toml` needs no regeneration and `docs/guide/configuration.md` needs no edit. `tests/unit/test_example_config_drift.py` passing is the proof
- [X] T044 Read `git diff` and confirm contract C7's untouched list: `capacity.py`, `cleanup.py`, `states.py` (no new state, no new edge in either transition table), `models.py::ANOMALY_KINDS` (no new kind), `_orphan_sweep`, `_resolve_closed_issues`, and `sessions.parse_entry`'s `.key` refusal and `KNOWN_VERSIONS` gate
- [X] T045 Run `uv run pytest`. The full suite must pass, including `tests/unit/test_docs_links.py` (README stays under 150 lines) and the existing effect-level grep test over `reconcile.py`, whose text now includes the new sweep
- [ ] T046 **Deferred to the maintainer — not run.** Walking [quickstart.md](./quickstart.md) Scenario 1 means ending two live worker processes and migrating the production database, on a machine deliberately left in the reported condition. That is the maintainer's call, not the implementation's, and it is better done once this is merged: restarting the daemon on the new code performs the retirement on its own, since both workers have been idle far past the threshold. Everything the scenario asserts is covered by tests; what this adds is confirmation against the real registry, the real kitty tabs and the real anomalies

---

## Dependencies

```
Phase 1 (T001)
    ↓
Phase 2 (T002)  ← the shared ReconcileResult fields
    ↓
    ├──────────────┬──────────────┐
    ↓              ↓              ↓
Phase 3 (US1)   Phase 4 (US2)  Phase 5 (US3)
 T003…T020       T021…T026      T027…T038
    └──────────────┴──────────────┘
                   ↓
             Phase 6 (T039…T046)
```

**Story independence**: US2 depends on nothing in US1 or US3 — it touches only
`operations.py::cancel` and `tests/unit/test_cancel.py`, and could ship first if the two live
orphans need clearing by hand before retirement exists. US3 shares only the `ReconcileResult`
fields from T002 with US1. US1 is the only story that must ship for #138 to be answered.

**Within Phase 3**: T003 → T004 → T005 are sequential (same file, building on each other). T006
precedes T014–T016 (they need the fixture). T010 → T011 → T012 → T013 are sequential (same file,
each building on the last). T019 precedes T020.

**Within Phase 5**: T027 → T028 → T029 → T030 are sequential in effect (the migration must land
before the model, the model before the query). T031 → T032 sequential. T034–T038 are parallel once
T032 lands.

## Parallel execution examples

**Phase 3, after T006 lands:**

```
T007  tests/unit/test_sessions.py        — registry parsing cases
T008  tests/unit/test_sessions.py        — the .key regression   (sequential with T007: same file)
T009  tests/unit/test_done_single_writer.py — the C8 invariant   [P]
```

**Phase 5, after T032 lands — five files, no overlap:**

```
T034  tests/unit/test_anomaly_resolution.py
T035  tests/unit/test_anomaly_resolution.py   (sequential with T034: same file)
T036  tests/unit/test_anomaly_resolution.py   (sequential with T034: same file)
T037  tests/unit/test_migrations.py           [P]
T038  tests/unit/test_web_views.py            [P]
```

**Phase 6 — four documentation files, fully parallel:**

```
T039  docs/guide/5-outcome.md
T040  docs/guide/operating.md
T041  docs/guide/state.md
T042  docs/guide/audit-log.md
```

## Implementation strategy

**MVP is Phase 1 + Phase 2 + Phase 3 (User Story 1).** That is 20 tasks and it delivers the whole
of #138's ask: the successful path stops producing anomalies, stops holding slots, and stops
blocking cleanup. The two anomalies on the machine disappear the first time a pass runs, and the
machine unwedges.

**Then Phase 4 (US2)**, which is the escape hatch for the states retirement deliberately never
touches, and which fixes a command that currently reports something untrue.

**Then Phase 5 (US3)**, which is hygiene on a list the first two phases have already stopped
filling.

**Phase 6 is not optional.** CLAUDE.md makes a guide page update part of any behaviour change, and
the constitution makes the suite passing part of the definition of complete. T043 and T044 are
verification tasks, not paperwork: each names a specific thing that must **not** have changed.
