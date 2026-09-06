---

description: "Task list for: Every verb that offers --include-simulated honours it"
---

# Tasks: Every verb that offers `--include-simulated` honours it

**Input**: Design documents from `specs/20260906-160021-honour-include-simulated/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/simulated-scope.md](contracts/simulated-scope.md)

**Tests**: **Not optional here.** The constitution's Development Workflow requires unit tests for
every new or changed unit of behaviour, and additionally failure- and interruption-path tests for
persistence, state machines and parsers. This feature touches a migration, a parser and a
state-settling pass, so all three apply. Test tasks are first-class below, not an appendix.

**Organization**: by user story, in the priority order [spec.md](spec.md) sets. Each story is a
usable increment on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: which user story the task serves
- Every task names the file it changes

## Path Conventions

Single package at the repository root: `src/robot_army/`, `tests/unit/`, `tests/integration/`,
`docs/guide/`. No new source file is created.

---

## Phase 1: Setup

**Purpose**: confirm the ground is where the plan says it is before changing it.

- [ ] T001 Run `uv sync && uv run pytest` from the repository root and record that the suite is green, so any later failure is attributable to this work rather than inherited
- [ ] T002 Confirm `SCHEMA_VERSION` is 13 in `src/robot_army/migrations.py` and that `MIGRATIONS` ends at `_migration_013`, so migration 014 is genuinely the next rung of the ladder

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: an anomaly cannot be filtered until it records whether the run that raised it was
rehearsed. Everything in US1 depends on this phase; US2 and US3 do not.

**⚠️ CRITICAL**: US1 cannot begin until T003–T012 are complete.

- [ ] T003 Add `SCHEMA_014_SQL` to `src/robot_army/migrations.py`: `ALTER TABLE anomalies ADD COLUMN dry_run INTEGER NOT NULL DEFAULT 0`, then `DROP INDEX idx_anomalies_open` and recreate it over `(kind, COALESCE(entity_type, ''), COALESCE(entity_id, ''), dry_run) WHERE acknowledged_at IS NULL AND resolved_at IS NULL`. Comment it the way 012 comments its own index rebuild — say why `NOT NULL DEFAULT 0` is right here when 011 and 013 chose nullable, and why a rehearsed and a real anomaly must not collide
- [ ] T004 Add `_migration_014` to `src/robot_army/migrations.py`, append it to `MIGRATIONS`, and update `SCHEMA_001_SQL`'s `CREATE TABLE anomalies` and `idx_anomalies_open` comments only if the file's convention is to keep them as history — check how 012 left them and match it
- [ ] T005 Add `dry_run: bool` to `Anomaly` in `src/robot_army/models.py`, positioned with the other flags; `_coerce` already turns SQLite's integer into a bool and needs no change
- [ ] T006 Add `dry_run: bool = False` to `db.raise_anomaly` in `src/robot_army/db.py` and write it into the `INSERT OR IGNORE`. Docstring: why the default is `False` rather than required — a forgotten argument must produce a *visible* anomaly
- [ ] T007 Add keyword-only `include_simulated: bool = False` to `db.list_anomalies` in `src/robot_army/db.py`, applied through the existing `_scope` helper, keeping the existing `acknowledged_at`/`resolved_at` clause intact and the `noqa: S608` convention
- [ ] T008 Add `db.list_simulated_anomalies(conn, *, unacknowledged_only=True)` to `src/robot_army/db.py`, returning rows rather than a count. Docstring must say why it returns rows — `--since` is applied in Python by `_within_window`, so a `COUNT(*)` would report a number the flag would not reveal — and that it must **not** join `test_db_scope`'s `LISTING_ACCESSORS`, for the reason `count_simulated_work_items` does not
- [ ] T009 [P] Pass `dry_run=item.dry_run` at the `session_id_mismatch` raise in `src/robot_army/dispatch.py`, leaving the `clone_path_missing`/`clone_origin_changed` gate raise alone — the clone is real at every level
- [ ] T010 [P] Pass the subject's `dry_run` at the five entity-bearing raises in `src/robot_army/reconcile.py`: `orphan_session` in `_supersede` (`other.dry_run`), `orphan_session` for a known session (`session.dry_run`), `dispatching_timeout` (`item.dry_run`), `no_transcript` (`session.dry_run`), `config_missing_repo` (`item.dry_run`), `prunable_worktree` (`item.dry_run`). Leave `registry_version_unknown` and `_orphan_sweep`'s registry-scan branch as real, and add a short comment at the latter saying why: there may be no session row, and an unaccounted-for live process holds a real slot whatever produced it
- [ ] T011 [P] Pass `dry_run=card.dry_run` at the `card_create_failing` and `card_issue_missing` raises in `src/robot_army/intake.py`, leaving `board_precondition` and `board_unreachable` as real — board reads are real at every effect level, so both report a true fact about a real board
- [ ] T012 [P] Leave `capacity.py`'s `capacity_unobservable` and `spool.py`'s two raises unchanged, and confirm by reading them that no `dry_run` is in scope to pass — this task is a verification, not an edit; it exists so the "seventeen sites were considered" claim in [research R2](research.md) is checked rather than asserted

### Foundational tests

- [ ] T013 [P] Extend `tests/unit/test_migrations.py`: 014 applies from a 013 database; the new column defaults to `0`; an anomaly row inserted before the migration reads back as `dry_run is False`; the rebuilt index accepts a rehearsed **and** a real open anomaly of the same kind and entity, and still refuses a second of either; and `user_version` is 14 afterwards
- [ ] T014 [P] Add a `tests/unit/test_migrations.py` case that the migration is atomic under interruption — a failure inside `_migration_014` leaves `user_version` at 13 with the old index intact, per the runner's explicit `BEGIN`/`rollback`
- [ ] T015 [P] Add `db.list_anomalies` to `LISTING_ACCESSORS` in `tests/unit/test_db_scope.py`, and add a case seeding one real and one rehearsed anomaly asserting the default listing shows only the real one and `include_simulated=True` shows both

**Checkpoint**: an anomaly knows whether it was rehearsed, the database can filter on it, and nothing above the persistence layer has changed yet.

---

## Phase 3: User Story 1 — A rehearsal raises no anomaly the maintainer mistakes for real (P1) 🎯 MVP

**Goal**: `anomalies`, the anomaly block of `status`, and the web's `/anomalies` page and header
pill all show real anomalies by default, say how many rehearsed ones they withheld, and show them
marked under the flag.

**Independent Test**: seed anomalies against real and rehearsed entities; run `anomalies`,
`status` and the `/anomalies` page in both spellings; assert the row sets differ by exactly the
rehearsed rows and that the stated withheld count equals that difference.

### Tests for User Story 1

- [ ] T016 [P] [US1] In `tests/unit/test_listing_withheld.py`, cover `anomalies`: rows visible with some withheld; nothing visible with some withheld and no window; nothing visible with some withheld under `--since`; nothing visible and nothing withheld. Assert the exact sentences [contracts/simulated-scope.md](contracts/simulated-scope.md) fixes, and that visible + withheld equals what the flag reveals
- [ ] T017 [P] [US1] In `tests/unit/test_listing_withheld.py`, cover `status`'s anomaly block in both spellings, including the case where **every** anomaly was withheld — the block must print its header with `0` and the withheld sentence rather than falling silent
- [ ] T018 [P] [US1] In `tests/unit/test_anomalies_since.py`, assert `--since` and `--all` each compose with `--include-simulated`, and that the withheld count is scoped to the same window and the same acknowledged/resolved breadth rather than counting every rehearsed row
- [ ] T019 [P] [US1] Add a test that `anomalies --acknowledge <id>` reaches a rehearsed anomaly **without** the flag, since an explicit id is already an explicit act — the rule `db.get_work_item` follows
- [ ] T020 [P] [US1] Add a test that an anomaly with `entity_type=None` — a `registry_version_unknown`, say — is listed by default, covering FR-009
- [ ] T021 [P] [US1] In the web tests, assert `/anomalies` filters by the toggle, states its withheld count, and that the header anomaly pill agrees with it on `/anomalies`, `/queue` and `/repos` alike

### Implementation for User Story 1

- [ ] T022 [US1] Add `include_simulated: bool = False` to `operations.anomalies` in `src/robot_army/operations.py`; pass it to `db.list_anomalies`; build the withheld set from `db.list_simulated_anomalies` and apply the **same** `_within_window` predicate to it, so the two populations cannot disagree
- [ ] T023 [US1] Render the withheld sentence in `operations.anomalies` via the existing `_withheld_note`, in all four cases the contract fixes, keeping milestone 012's distinction between "no outstanding anomalies" and "no anomalies detected in the last D"
- [ ] T024 [US1] Mark rehearsed rows in `operations.anomalies` with a `*` after the leading `[id]`, and print the existing `* = simulated (dry-run) row` legend once beneath a listing that contains one
- [ ] T025 [US1] Add `"simulated": anomaly.dry_run` to `_anomaly_dict` in `src/robot_army/operations.py`, and `withheld_simulated` to the `anomalies` payload — always present, including as `0`
- [ ] T026 [US1] Pass `include_simulated=include_simulated` to `db.list_anomalies` in `operations.status`, compute its withheld count the same way, render the indented withheld line in the anomaly block, and add an `anomalies` key to the existing `withheld_simulated` payload dict beside `counts` and `items`
- [ ] T027 [US1] Pass `include_simulated` through to `operations.anomalies` from `cli._dispatch` in `src/robot_army/cli.py`
- [ ] T028 [P] [US1] In `src/robot_army/web/pages.py`, pass `include_simulated` to `db.list_anomalies` inside `chrome()` so the header pill counts within the scope the page was served with
- [ ] T029 [P] [US1] In `src/robot_army/web/pages.py`, make `anomalies_view` pass the `include_simulated` it is already handed to `operations.anomalies`, and render the withheld count on the page

**Checkpoint**: the reported harm is gone — a rehearsal's anomalies no longer appear as real in any default view, on either front end.

---

## Phase 4: User Story 2 — The audit log can be read without the rehearsal's traffic (P1)

**Goal**: `log` and the web's `/log` exclude rehearsed records by default, include them under the
flag with their `[simulated]` marker intact, and say how many they withheld.

**Independent Test**: write an audit file mixing real records with ones carrying `simulated` or
`dry_run`; read it in both spellings; assert the record sets differ by exactly the marked records.

This story shares no file region with US1's operations changes beyond living in
`src/robot_army/operations.py`, and depends on nothing in Phase 2.

### Tests for User Story 2

- [ ] T030 [P] [US2] Add a test that `_judge_record` rejects a record carrying `simulated: true`, one carrying `dry_run: true`, and one carrying both, when `include_simulated` is false — and accepts all three when it is true
- [ ] T031 [P] [US2] Add a test over `operations.read_log` with a seeded audit file of real and rehearsed records: the default shows only real ones, the flag shows all, and the withheld count equals the difference
- [ ] T032 [P] [US2] Add a test that the simulated filter composes with `--since`, `--item` and `--limit`, and specifically that `--limit 20` yields twenty *real* records rather than twenty records of which some are hidden
- [ ] T033 [P] [US2] Add a test over `operations.read_log_page` that a page whose scanned region is entirely rehearsed still fills from older matching records rather than coming back empty — the property that requires the filter to live inside the scan
- [ ] T034 [P] [US2] Add a test that a partial final line is still skipped and counted as `unparseable_lines` independently of the simulated filter, in both spellings
- [ ] T035 [P] [US2] Add a web test that `/log` filters by the toggle and states the page's scanned-region withheld count

### Implementation for User Story 2

- [ ] T036 [US2] Add `include_simulated: bool` to `_judge_record` in `src/robot_army/operations.py`, rejecting a record whose `simulated` or `dry_run` is truthy when it is false. Comment that this is the same disjunction `_format_record` uses for the `[simulated]` marker, so what is hidden and what is marked cannot drift apart
- [ ] T037 [US2] Add `include_simulated: bool = False` to `operations.read_log`, thread it into `_judge_record`, count the records rejected for being rehearsed separately from `_UNREADABLE`, and report the count as its own sentence and as `withheld_simulated` in the payload
- [ ] T038 [US2] Add `include_simulated: bool = False` to `operations.read_log_page`, thread it into `_judge_record` **inside the backwards scan**, and report the number withheld from the records scanned for this page — in words that say so, per [research R5](research.md)
- [ ] T039 [US2] Pass `include_simulated` through to `operations.read_log` from `cli._dispatch` in `src/robot_army/cli.py`, leaving `--follow` unfiltered and unchanged
- [ ] T040 [US2] In `src/robot_army/web/pages.py`, make `log_view` pass the `include_simulated` it is already handed to `operations.read_log_page`, and render its withheld count

**Checkpoint**: the reconstruction path can be read without the rehearsal's traffic, on both front ends.

---

## Phase 5: User Story 3 — No verb offers a filter it cannot apply (P2)

**Goal**: the set of verbs carrying the flag is a named, enumerable thing; `repos` is not in it;
and a test fails the moment a verb joins it without being wired up.

**Independent Test**: enumerate the constant and drive every member in both spellings against a
seeded state, asserting each pair disagrees; assert `repos --include-simulated` exits 2.

Depends on US1 and US2 for the guard test to pass, because `anomalies` and `log` are members of
the set. The `repos` removal and the constant itself depend on neither.

### Tests for User Story 3

- [ ] T041 [P] [US3] Add a new `tests/unit/` module holding the cross-verb guard: a fixture seeding rehearsed rows of every kind the set covers — a work item, a card, an anomaly, a work item carrying a worktree path, and audit records — then a parametrised test over `cli.SIMULATED_SCOPED_COMMANDS` asserting each verb's output differs between the two spellings. Name the file for what it guards, and say in its docstring that it exists because the parser's promise and the commands' behaviour drifted apart once already
- [ ] T042 [P] [US3] In the same module, assert `robot-army repos --include-simulated` exits 2 with argparse naming the unrecognised option, and that `repos --help` mentions it nowhere
- [ ] T043 [P] [US3] Add a `tests/unit/test_listing_withheld.py` case for `worktree list` against a state holding both real and rehearsed worktree-bearing items — the regression test the issue asked for, closing its "untested" row

### Implementation for User Story 3

- [ ] T044 [US3] Introduce `SIMULATED_SCOPED_COMMANDS` in `src/robot_army/cli.py` holding `status`, `worktree`, `log`, `anomalies`, `cards`; decorate the parser from it rather than from an inline tuple; drop `repos`. Comment that it has two callers — the parser and the guard test — which is what makes the promise checkable rather than remembered
- [ ] T045 [US3] Update the `--include-simulated` help string in `src/robot_army/cli.py` if the FR-056 wording needs adjusting now that it is true of every verb offering it

**Checkpoint**: every advertised flag is a flag that works, and a test says so.

---

## Phase 6: User Story 4 — An anomaly whose condition has resolved leaves the list (P3)

**Goal**: a `card_create_failing` anomaly retracts itself once its card reaches `linked`, logged,
idempotent, and distinguishable from an acknowledgement.

**Independent Test**: raise the anomaly, drive the card to `linked`, run one reconciliation pass,
assert the anomaly is resolved and gone from the default listing while showing under `--all` as
resolved.

Depends on nothing in US1–US3 except that Phase 2's `dry_run` column makes the card lookup exact.

### Tests for User Story 4

- [ ] T046 [P] [US4] In `tests/unit/test_anomaly_resolution.py`, assert a `card_create_failing` whose card is `linked` is resolved by one reconciliation pass, disappears from the default listing, and shows under `--all` marked resolved rather than acknowledged
- [ ] T047 [P] [US4] Assert the anomaly is left outstanding when the card is still in `creating`, and when no card row matches at all — "I could not check" must never be written as "it is fine"
- [ ] T048 [P] [US4] Assert a second reconciliation pass over already-resolved state writes nothing and logs no second `anomaly.resolved`, exercising `db.resolve_anomaly`'s `resolved_at IS NULL` guard
- [ ] T049 [P] [US4] Assert the pass commits per anomaly, so an interruption partway through leaves the ones it reached resolved and logged and the rest outstanding — the interruption-path test the constitution requires of a state-settling pass
- [ ] T050 [P] [US4] Assert a rehearsed `card_create_failing` resolves against its own rehearsed card and not against a real card sharing the card id, which is what the `(card_id, dry_run)` lookup is for

### Implementation for User Story 4

- [ ] T051 [US4] Add `db.open_card_create_failing_anomalies(conn)` to `src/robot_army/db.py`, mirroring `open_orphan_session_anomalies` — narrow by construction, with a docstring saying why this kind and no other
- [ ] T052 [US4] Add a card lookup by `(card_id, dry_run)` to `src/robot_army/db.py`, noting that `idx_cards_identity` makes the pair unique in practice and that a missing row is a "cannot check", not a "fine"
- [ ] T053 [US4] Add `_resolve_card_create_anomalies` to `src/robot_army/reconcile.py` beside `_resolve_orphan_anomalies`: resolve when the card is `linked`, leave alone otherwise, commit one anomaly at a time under `db.transaction`, and write `anomaly.resolved` with the kind, the card and the state that establishes the condition false. Docstring must say why it lives in reconciliation rather than intake — retraction must not depend on Trello being reachable
- [ ] T054 [US4] Call it from `reconcile.run` and add its return to `result.anomalies_resolved`, positioned beside the orphan resolver
- [ ] T055 [US4] Change the `anomalies` sub-parser help in `src/robot_army/cli.py` from "conditions detected but not resolvable" — two kinds now retract themselves and the claim is wrong in the first place a reader looks

**Checkpoint**: the list stops going stale for the one kind whose condition demonstrably resolves.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T056 [P] Update `docs/guide/operating.md`: the `anomalies` line no longer says "not resolvable"; the "only kind that clears itself" paragraph now names two kinds and says what re-establishes each as false; and both `anomalies` and `log` are described as excluding rehearsed rows by default
- [ ] T057 [P] Update `docs/guide/1-setup.md`: the paragraph listing which listings say how many they withheld now names `anomalies` and `log` alongside `status`, `cards` and `worktree list`, and says why `repos` has no such flag
- [ ] T058 [P] Update `docs/guide/audit-log.md`: the `dry_run` / `simulated` row notes that the reader now excludes those records by default and that either field means rehearsed
- [ ] T059 [P] Update `docs/guide/state.md`: `anomalies.dry_run` in the anomalies table section, schema version 14, and the note that pre-014 rows read as real
- [ ] T060 [P] Amend the universal-rule line in `specs/001-minimum-daemon/contracts/cli.md` with a blockquote pointing at [contracts/simulated-scope.md](contracts/simulated-scope.md), in the shape 008's amendment already uses there
- [ ] T061 Run `uv run pytest` — the whole suite, including `tests/unit/test_example_config_drift.py`, which must pass untouched since no configuration key changed
- [ ] T062 Walk [quickstart.md](quickstart.md) sections 2, 3 and 4 against a seeded database, confirming the issue's own measurements now differ between the two spellings and that `repos --include-simulated` exits 2
- [ ] T063 Confirm `README.md` is still under 150 lines and unchanged — this feature adds nothing to it

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)**: no dependencies
- **Phase 2 (Foundational)**: after Setup. **Blocks US1 only** — US2, US3's `repos` removal, and US4's structure do not need the column
- **Phase 3 (US1)**: after Phase 2
- **Phase 4 (US2)**: after Setup. Independent of Phase 2 and of US1
- **Phase 5 (US3)**: its guard test needs US1 and US2 landed, because `anomalies` and `log` are members of the set it drives. T044's constant and the `repos` removal need neither
- **Phase 6 (US4)**: after Phase 2 (for the exact `(card_id, dry_run)` lookup). Independent of US1–US3 otherwise
- **Phase 7 (Polish)**: after every story it documents

### Within each story

- Tests are written alongside the change they cover, not after the story
- Persistence before operations, operations before the CLI and the web — both front ends read the same operation, which is what keeps them from disagreeing
- The withheld count and the listing are written in the same task where possible, because the equality between them is the property most easily broken

### Parallel opportunities

- **T009, T010, T011, T012** — four different modules, no shared region
- **T013, T014, T015** — three independent test additions
- **T016–T021** — six test additions across three files
- **T028, T029** — two independent functions in `web/pages.py`
- **T030–T035** — six independent test additions
- **T041, T042, T043** — the guard module and the worktree regression
- **T046–T050** — five cases in one file; parallel in authorship, one file to write
- **T056–T060** — five documents, no overlap
- **US2 and US4 can proceed in parallel with US1** once Phase 2 is in

---

## Parallel Example: Foundational call sites

```bash
# T009–T012 touch four different modules and share no region:
Task: "session_id_mismatch passes item.dry_run in src/robot_army/dispatch.py"
Task: "six entity-bearing raises pass their subject's dry_run in src/robot_army/reconcile.py"
Task: "card_create_failing and card_issue_missing pass card.dry_run in src/robot_army/intake.py"
Task: "verify capacity.py and spool.py have no dry_run in scope to pass"
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 → Phase 2 → Phase 3
2. **Stop and validate**: [quickstart.md](quickstart.md) §2's first pair now differs, and §3's
   `B - A == N` identity holds with and without `--since`
3. This alone removes the reported harm: no rehearsed anomaly is presented as real anywhere

### Incremental delivery

1. Setup + Foundational → the column exists and is filterable
2. US1 → the anomaly surfaces are honest → **MVP**
3. US2 → the log is readable → the second half of the reported defect
4. US3 → the promise becomes enforceable rather than remembered
5. US4 → the list stops going stale
6. Polish → the guide and the contract say what the program does

Each step is independently useful and none breaks the one before it.

---

## Notes

- Commit per task or per coherent group, with a message explaining *why* — the repository
  convention, and the constitution's
- `db.list_simulated_anomalies` must **not** join `test_db_scope`'s `LISTING_ACCESSORS`. Counting
  withheld rows *is* the simulated-only question and `include_simulated=False` there would be
  nonsense — the same argument `count_simulated_work_items` carries in its own docstring
- The withheld count and the listing must come from one predicate. Every time this system has got
  that wrong it was because two hand-written copies of a filter drifted
- No configuration key changes, so `exampleconfig.py` and `share/config.example.toml` stay
  untouched and T061 proves it
