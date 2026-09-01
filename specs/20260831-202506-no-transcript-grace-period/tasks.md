---

description: "Task list for: Give the Missing-Transcript Check Time to Be Right"
---

# Tasks: Give the Missing-Transcript Check Time to Be Right

**Input**: Design documents from `specs/20260831-202506-no-transcript-grace-period/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/transcript-check.md](./contracts/transcript-check.md),
[quickstart.md](./quickstart.md)

**Tests**: **Required, not optional.** The constitution's Development Workflow makes unit tests
mandatory for every new or changed unit of behaviour. This feature has a sharper reason than usual:
issue #58 closes by observing that the existing coverage asserts only the failing case, and that a
test asserting *"a dispatch whose transcript appears shortly afterwards raises no anomaly"* would
have caught this before the first live dispatch. That test is T010 and it is the deliverable, not a
formality. Test tasks are written before the implementation they cover in each phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US3)

## Path Conventions

Single project: `src/robot_army/`, `tests/` at repository root.

---

## Phase 1: Setup

**Purpose**: Establish the baseline so any regression is attributable to this feature.

- [ ] T001 Capture the baseline: run `uv run pytest` and `uv run ruff check src tests` from the repository root and record that both are green before any edit. If either is red, stop and report — this feature must not be built on top of an unexplained failure.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The column that holds the open question, the model that reads it, and the test seam
that lets a test plant a transcript without touching the maintainer's real home directory.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T002 [P] Add `claude_projects_dir()` to `src/robot_army/paths.py` returning `Path.home() / ".claude" / "projects"`, directly beside `claude_registry_dir()` and documented the same way — it is the second directory under `~/.claude` this project reads and it needs the same seam for the same reason (research R6).
- [ ] T003 Change `sessions.transcript_exists` in `src/robot_army/sessions.py` to fall back to `claude_projects_dir()` when no `home=` argument is given, importing it beside the existing `claude_registry_dir` import. Keep the `home=` parameter — it has callers in `tests/unit/test_sessions.py` and is the finer seam. Depends on T002.
- [ ] T004 [P] Add an autouse fixture to `tests/conftest.py` that points `robot_army.sessions.claude_projects_dir` at a per-test empty directory under `tmp_path`, mirroring `_no_real_session_registry` and citing the same rationale: without it a suite run reads the maintainer's actual `~/.claude/projects` and the result depends on which sessions they happen to have run. Add a `write_transcript(projects_dir, session_id)` helper beside it that creates `<projects_dir>/<some-project>/<session_id>.jsonl`, so a test can say "the transcript appeared" in one line.
- [ ] T005 [P] Add `transcript_checked_at: str | None = None` to `Session` in `src/robot_army/models.py`. `from_row` selects by declared field, so the column is invisible to the dataclass until this exists.
- [ ] T006 Add migration 008 to `src/robot_army/migrations.py` exactly as `data-model.md` specifies: `ALTER TABLE sessions ADD COLUMN transcript_checked_at TEXT`, the partial index `idx_sessions_transcript_open ON sessions (transcript_checked_at) WHERE transcript_checked_at IS NULL`, and the one-time backfill `UPDATE sessions SET transcript_checked_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE transcript_checked_at IS NULL`. Append `_migration_008` to `MIGRATIONS`; never edit an existing migration. Document in the SQL comments why the backfill exists — without it the first pass after the upgrade reports the entire session history at once.
- [ ] T007 [P] Extend `tests/unit/test_migrations.py`: assert `SCHEMA_VERSION == 8`, that `idx_sessions_transcript_open` appears among the indexes, and — the one that matters — that a database seeded with session rows *before* `migrate()` runs comes out with every one of them carrying a non-null `transcript_checked_at`, so no pre-existing session is retro-judged. Depends on T006.
- [ ] T008 [P] Add `sessions_awaiting_transcript_check(conn)` to `src/robot_army/db.py`: `SELECT * FROM sessions WHERE transcript_checked_at IS NULL ORDER BY id`, returning `list[Session]`. Docstring records that the `NULL` set is the whole population by design (research R2) and that the partial index is what keeps this off a full-history scan (FR-010). Depends on T005.

**Checkpoint**: the schema carries the open question, existing history is already answered, and a test can make a transcript appear.

---

## Phase 3: User Story 1 — A healthy dispatch raises no anomaly (Priority: P1) 🎯 MVP part 1

**Goal**: Nothing reports a missing transcript at dispatch time, and nothing reports one before the
grace period has elapsed. This is the reported bug.

**Independent Test**: run a dispatch whose transcript appears seconds later, then run reconciliation
passes over it. The story passes when the anomalies table is empty at every point in that sequence.

- [ ] T009 [P] [US1] Add a test to `tests/integration/test_dispatch.py` asserting that a completed dispatch leaves the anomalies table **empty** — contract invariant C1. This is the assertion whose absence let the defect ship; write it before removing anything.
- [ ] T010 [P] [US1] Create `tests/unit/test_transcript_check.py` and write the two non-reporting cases first: (a) a session confirmed seconds ago with no transcript on disk — one pass writes nothing, `transcript_checked_at` is still `NULL`, and the session is examined again on the next pass (C3); (b) a session whose transcript exists — no anomaly, `transcript_checked_at` set, one `session.transcript_found` audit record (C4). Case (a) is the regression test issue #58 asks for.
- [ ] T011 [US1] Delete the inline check from `src/robot_army/dispatch.py` (~lines 1035–1057): the comment block, the `not dry_run and not sessions.transcript_exists(...)` condition, and the `raise_anomaly` it guards. Nothing replaces it. Check whether `sessions` is still used elsewhere in the module and drop the import only if it is not.
- [ ] T012 [US1] Implement `TRANSCRIPT_GRACE_SECONDS = 300` and `_sweep_transcripts(conn, *, audit)` in `src/robot_army/reconcile.py`, following the decision table in `contracts/transcript-check.md`: iterate `db.sessions_awaiting_transcript_check`; skip-and-close when `not session.pid`; close when `transcript_exists(session.session_id)`; **leave untouched** when `_age_seconds(session.confirmed_at or session.started_at) < TRANSCRIPT_GRACE_SECONDS`. Write `transcript_checked_at` inside a `db.transaction`, and one audit record per session closed. Row 4 (reporting) is left for US2 — until then the fourth branch closes the row with `session.transcript_missing` and raises nothing. Constant carries research R1's reasoning in a comment: 300s is ~40× the single warm-cache measurement available, and errs long because a late report costs nothing while an early one recreates this bug.
- [ ] T013 [US1] Wire the sweep into `reconcile()` in `src/robot_army/reconcile.py`, positioned after `_sweep_stale_sessions` and before `_orphan_sweep` (research R7), and add `transcripts_checked` and `no_transcript` counters to `ReconcileResult` and to `summary()` so the existing `reconcile.pass` audit record carries them (FR-011).
- [ ] T014 [US1] Add a test to `tests/integration/test_reconcile_pass.py`: a full pass over a healthy session raises no anomaly and reports `transcripts_checked: 1, no_transcript: 0` in its summary.

**Checkpoint**: dispatch is silent, young sessions are left alone, and the healthy path is asserted end to end. The detector does not yet report — US2 restores that, and the spec marks both P1 for exactly this reason.

---

## Phase 4: User Story 2 — A genuinely unresumable session is still reported (Priority: P1) 🎯 MVP part 2

**Goal**: When the grace period has elapsed and no transcript exists, exactly one anomaly is raised,
ever, saying something the maintainer can act on.

**Independent Test**: run a session that never writes a transcript, advance past the grace period,
and run many passes — including one after acknowledging the anomaly. Exactly one anomaly exists
throughout, and its note names both possible causes without asserting either.

- [ ] T015 [P] [US2] Add to `tests/unit/test_transcript_check.py`: grace elapsed with no transcript raises exactly one `no_transcript` anomaly carrying `item_id`, `waited_s`, and `session_state`, and sets `transcript_checked_at` (decision-table row 4).
- [ ] T016 [P] [US2] Add the once-only tests to `tests/unit/test_transcript_check.py`: ten further passes over the same transcript-less session create no second anomaly; and — the case the anomalies table's partial unique index cannot cover on its own — acknowledging the anomaly and running further passes still creates no second one (C2). The second half is the reason the state is a column rather than a query.
- [ ] T017 [P] [US2] Add the state-independence and undateable-row tests to `tests/unit/test_transcript_check.py`: a session that has **ended** with no transcript is reported on the same terms as a running one; a session that ended **with** a transcript is not reported (C5); and a session with an unparseable `started_at` and no `confirmed_at` is reported with `waited_s: null`, matching the `dispatching_timeout` sweep's precedent for a row that cannot be dated (research R4).
- [ ] T018 [US2] Implement decision-table row 4 in `_sweep_transcripts`: raise the `no_transcript` anomaly and write `transcript_checked_at` **inside one `db.transaction`** (C7), with the detail fields `data-model.md` lists, plus one `session.transcript_missing` audit record. Depends on T012.
- [ ] T019 [US2] Write the replacement note text in `_sweep_transcripts`, verbatim from `data-model.md`: it names both causes, states that the check cannot distinguish them, points at `robot-army doctor` for one and the session's exit record for the other, and ends with the instruction true either way — restart rather than resume (FR-009). Do not carry over the old wording, which asserted a cause that was verifiably absent on the machine where it fired.
- [ ] T020 [US2] Add the interruption test to `tests/unit/test_transcript_check.py`: force a failure between the anomaly write and the column write (monkeypatch inside the transaction) and assert that **neither** lands, and that the next pass then reports exactly once. This is the Principle IV answer for this feature and the mechanism behind C2.

**Checkpoint**: the detector works again, reports once, and says something worth reading. This is the shippable fix for issue #58.

---

## Phase 5: User Story 3 — A rehearsal can exercise the detector (Priority: P2)

**Goal**: The exemption asks "did a real process ever run?", not "was the effect level below live",
so a session launched at `no-remote` is judged exactly like a live one.

**Independent Test**: a `dry_run` session row carrying a real pid is judged and reported; a session
row with `pid = 0` is skipped without the filesystem ever being consulted.

**Note**: T012 already writes the predicate as `not session.pid` rather than porting the wrong one
forward — building a known-wrong guard in order to replace it two phases later would be dishonest
work. This story's deliverable is therefore the proof and the reasoning, which is what was missing
before: the old guard was never asserted either way.

- [ ] T021 [P] [US3] Add to `tests/unit/test_transcript_check.py`: a session with `pid = 0` (the only shape the simulated host can produce) is closed with a `session.transcript_skipped` audit record, no anomaly, and **no filesystem read** — assert the last by spying on `sessions.transcript_exists` and checking it was never called, since row 1 must precede row 2 (contract, decision table).
- [ ] T022 [P] [US3] Add the case that is the whole point to `tests/unit/test_transcript_check.py`: a session row with `dry_run = True` **and a real pid** — the shape a `no-remote` dispatch produces — is judged and reported exactly like a live session. Name the test after what it protects; it is the assertion whose absence made every rehearsal blind to this detector.
- [ ] T023 [US3] Document the predicate in `_sweep_transcripts` in `src/robot_army/reconcile.py`: a comment recording that the question is "did this session have a process?", not "was the effect level live", that `dry_run` is true at `no-remote` where the process is real, and that this is the same correction issue #33 already made one sweep over in this module. Do not name `EffectLevel` — `tests/unit/test_effects.py::test_only_effects_py_knows_the_effect_level_exists` greps this file's text, comments included, and naming it fails the suite.
- [ ] T024 [US3] Run `uv run pytest tests/unit/test_effects.py -v` and confirm both grep tests still pass after T023.

**Checkpoint**: the detector is reachable from a rehearsal, and a future regression in it is catchable without a live dispatch.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T025 [P] Rewrite the `no_transcript` bullet in `README.md` (~line 644) to match the implemented behaviour (FR-013): raised by reconciliation after a grace period rather than at dispatch, meaning the session left nothing resumable, with the cause not determined by the check itself. Delete "Almost always a `CLAUDE_CODE_*` variable in kitty's environment" — that claim is what sent the maintainer looking in the wrong place.
- [ ] T026 [P] Update the docstring of `sessions.transcript_exists` in `src/robot_army/sessions.py`: keep the M0 F19 history, but stop implying the environment variable is the only cause, and note that the caller now owns the timing question.
- [ ] T027 Run the full suite and linter: `uv run pytest` and `uv run ruff check src tests`. Both green before this feature is considered complete (constitution, Development Workflow).
- [ ] T028 Walk `quickstart.md` §4 on a real machine: rehearse a dispatch at the level that launches real sessions, confirm `robot-army anomalies` reports nothing immediately after and nothing after a forced `robot-army reconcile` (SC-001); then point the projects seam at an empty directory for one pass to watch the other branch fire exactly once with `waited_s` recorded (SC-002, SC-003, SC-006). This is the only task that touches a live machine.
- [ ] T029 Read the raised anomaly's text as a stranger would and confirm SC-005: following it leads to `robot-army doctor` and, when `doctor` is clean, the note has already said that a clean environment does not close the question. If it has not, the text is wrong — fix it in `src/robot_army/reconcile.py`, not the criterion.

---

## Dependencies

```text
T001
 └─> Phase 2 ──┬─> T002 -> T003
               ├─> T004 (needs T003 for the seam it patches)
               ├─> T005 -> T008
               └─> T006 -> T007

Phase 2 ─> Phase 3 ──> T009 [P]
                       T010 [P]
                       T011
                       T012 (needs T008, T010)
                       T013 (needs T012)
                       T014 (needs T013)

Phase 3 ─> Phase 4 ──> T015, T016, T017 [P]
                       T018 (needs T012)
                       T019 (needs T018)
                       T020 (needs T018)

Phase 4 ─> Phase 5 ──> T021, T022 [P]
                       T023
                       T024 (needs T023)

Phase 5 ─> Phase 6 ──> T025, T026 [P]
                       T027 -> T028 -> T029
```

US1 must precede US2: US2 fills in the fourth branch of the function US1 creates. US3 depends on
both only because its tests exercise the finished sweep; its predicate ships in T012.

## Parallel Execution Examples

```text
Phase 2:  T002, T004, T005, T006, T007 can start together (T003 after T002; T007 after T006;
          T008 after T005). Five files, no shared edits.

Phase 3:  T009 and T010 are different test files and can be written side by side, before any
          source edit. T011 (dispatch.py) and T012 (reconcile.py) touch different modules and
          can proceed in parallel once their tests exist.

Phase 4:  T015, T016 and T017 are three independent test cases in one new file — write them
          together, then implement T018/T019 against all three at once.

Phase 5:  T021 and T022 in parallel.

Phase 6:  T025 and T026 in parallel; T027 onward is sequential by nature.
```

## Independent Test Criteria

| Story | Passes when |
|-------|-------------|
| US1 | A dispatch whose transcript appears shortly afterwards produces no anomaly at any point, and a session younger than the grace period is left untouched with its question still open |
| US2 | A session that never writes a transcript is reported exactly once — across many passes, across a restart, and after acknowledgement — with `waited_s` recorded and guidance that names both causes |
| US3 | A `dry_run` row with a real pid is judged like a live session; a `pid = 0` row is closed without the filesystem being read |

---

## Implementation Strategy

### MVP: US1 + US2 together

The spec marks both P1, and they are not separable in the way stories usually are. US1 alone stops
the false positive but leaves the detector silent — which is a *different* defect, and the more
dangerous of the two, since the failure it detects is unrecoverable and invisible by any other
means. **Do not ship after Phase 3.** The MVP is Phases 1–4:

1. Phase 1 — baseline.
2. Phase 2 — the column, the model field, the seam, the migration.
3. Phase 3 — US1: dispatch stops asking; young sessions are left alone.
4. Phase 4 — US2: the report comes back, once, with honest guidance.
5. **STOP and VALIDATE**: `uv run pytest tests/unit/test_transcript_check.py -v`, then confirm a
   real dispatch produces no anomaly and a transcript-less session produces exactly one.

### Incremental Delivery

1. Setup + Foundational → the question has somewhere durable to live and history is already answered.
2. US1 → every dispatch stops crying wolf.
3. US2 → the wolf is reported again, once, and the report is worth reading. **Ship here.**
4. US3 → the whole thing becomes exercisable in rehearsal, so the next regression in it is caught
   before a real issue is at stake.
5. Polish → the README stops sending the reader to the wrong place.

---

## Notes

- **The grace period is a constant, not config.** One caller, no second use in hand (Principle I).
  If 300s proves wrong, change the number.
- **The anomaly write and the column write share one transaction.** That single fact is what makes
  "exactly one anomaly per session, ever" true across a kill mid-pass. Do not split them for
  convenience.
- **The `no_transcript` kind, entity type, and entity id do not change**, so `robot-army anomalies`,
  `--since`, acknowledgement, and the non-zero exit on outstanding anomalies keep working untouched.
- Commit after each task or logical group; messages explain why, per the constitution.
- Nothing here changes what a transcript is or where it is looked for. Only when the question is
  asked, and what is said when the answer is no.
