---

description: "Task list for Trello Column Ignore List (milestone 006)"
---

# Tasks: Trello Column Ignore List

**Input**: Design documents from `/specs/006-trello-ignore-lists/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: Included, and not optional. The constitution's Development Workflow requires unit tests for
every new or changed unit of behaviour, and *additional* failure-and-interruption tests for
persistence logic and for code parsing external input — a migration and a config parser are both. It
also says test-first is **not** mandatory and coverage targets **must not** be adopted, so test tasks
sit beside the code they cover rather than ahead of it.

Two groups of tests in this milestone are worth more than the rest.

The first is **T037**, the regression test asserting that a tracked card moved into an ignored column
is *not* dropped. `dropped` is terminal and the obvious implementation — filtering ignored cards out
of the poll listing — produces exactly that bug: parking a card would destroy it permanently, and
un-parking would do nothing, forever, silently. The test exists to fail loudly if a later edit
"tidies" `outcome.cards` to exclude ignored cards.

The second is **T028**, one test per row of [contracts/surfaces.md](contracts/surfaces.md)'s ordering
table. The gate's position inside `evaluate_card` is a contract, and every neighbour is fixed by a
different requirement — a reordering breaks exactly one of them and nothing else notices.

**Organization**: By user story, in the priority order spec.md assigns. One maintainer, so `[P]`
marks work that does not collide — not work that needs a second person.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Touches files no other pending task touches; safe to interleave
- **[Story]**: US1–US4, mapping to spec.md's user stories
- Every task names its exact file path

## Path Conventions

Single project, as 001–005 established: `src/robot_army/`, `tests/unit/`. **No new source module** —
every change lands where the thing it changes already lives. One new test module,
`tests/unit/test_ignored_lists.py`.

---

## Phase 1: Setup

**Purpose**: The one configuration value every story reads. Existence-on-the-board checking is
deliberately **not** here — that is US4, so that story stays independently droppable.

- [X] T001 Add `ignore_lists: tuple[str, ...] = ()` to `TrelloConfig` in `src/robot_army/config.py`, with a docstring stating that it holds board column *names*, that it is empty by default so an unconfigured installation is behaviourally identical to milestone 003, and that it gates intake only — a card with a recorded issue is never affected (FR-001, FR-002, [contracts/config.md](contracts/config.md))
- [X] T002 Parse and validate `ignore_lists` in the `[trello]` loader in `src/robot_army/config.py`: reject a non-list or any non-string entry with `[trello] ignore_lists must be a list of strings`, reject an empty entry with `[trello] ignore_lists contains an empty column name`, and collapse duplicates with `dict.fromkeys` exactly as `[notifications] events` already does — preserving the author's written order, because the `doctor` report reads back in it (FR-019a, FR-020) (depends on T001)
- [X] T003 Add `"ignore_lists"` to the `"trello"` entry of `_SECTION_KEYS` in `src/robot_army/config.py`, so writing the key is not itself an unknown-key error in a section where unknown keys are errors rather than warnings (depends on T001)
- [X] T004 [P] Extend `tests/unit/test_config.py` with cases for: the empty default; a valid list; a bare string rejected; a list containing a non-string rejected; a list containing `""` rejected; duplicates collapsing to one while preserving order; and the existing `_looks_like_token` sweep still firing on a `[trello]` value (Principle II — no literal credential in a public repository)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The board-side resolution, the schema column, and the single predicate. Nothing changes
behaviour yet — after this phase the system knows which column ids are ignored and where every
tracked card is, and still acts on all of them.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### The board information

- [X] T005 [P] Add `lists_by_id: dict[str, str]` (id → name) to `BoardInfo` in `src/robot_army/boundaries/__init__.py`, documenting that it is the inverse of `lists` and exists because `lists` is name-keyed and therefore collapses two board columns that share a name — list ids are unique, so the inversion preserves duplicates by construction rather than by a rule (FR-019b, [contracts/board-checks.md](contracts/board-checks.md))
- [X] T006 Populate `lists_by_id` in `TrelloCardReader.board_info()` in `src/robot_army/boundaries/trello.py` from the **same** `GET /boards/{id}/lists` response `lists` is built from — no additional request, and the once-per-process memo untouched (depends on T005)
- [X] T007 [P] Update every fixture and helper that constructs a `BoardInfo` in `tests/` so `lists_by_id` is consistent with `lists`; an inconsistent fixture would let a test pass against a board shape the API cannot produce (depends on T005)
- [X] T008 [P] Add tests in `tests/unit/test_board_preconditions.py` that `board_info()` builds `lists_by_id` from one response, that a board with **two columns of the same name** yields two `lists_by_id` entries and one `lists` entry, and that no extra HTTP call is made (depends on T006)

### The schema

- [X] T009 Add `SCHEMA_006_SQL` with `ALTER TABLE cards ADD COLUMN current_list_id TEXT;` plus a comment saying what it answers that `origin_list_id`, `placed_list_id` and `pending_move_to` do not — *where the card is now* — and that NULL means "tracked before this migration, not yet re-polled", with nothing backfilling it; add `_migration_006` and append it to `MIGRATIONS` in `src/robot_army/migrations.py`, never editing an existing entry
- [X] T010 [P] Add `current_list_id: str | None = None` to `Card` in `src/robot_army/models.py`, extending the existing three-list-id docstring to four and stating which question each answers (depends on T009)
- [X] T011 Thread `current_list_id` through `src/robot_army/db.py`: the `cards` row mapping, and the `update_card_columns` writable-column set so `_refresh_tracked_card` can write it (depends on T010)
- [X] T012 [P] Add tests in `tests/unit/test_migrations.py` that `user_version` goes 5 → 6, that pre-existing `cards` rows survive with `current_list_id` NULL, that `migrate()` is idempotent on an already-migrated database, and — the interruption path the constitution requires — that a migration killed before `PRAGMA user_version` is advanced leaves version 5 and re-runs whole (depends on T009)

### The resolution and the predicate

- [X] T013 Add `ignored_list_ids: frozenset[str] = frozenset()` to `BoardStatus` in `src/robot_army/intake.py`, beside the `label_id` / `in_progress_list_id` / `done_list_id` it already carries
- [X] T014 Resolve `ignored_list_ids` in `check_board` in `src/robot_army/intake.py` as `frozenset(list_id for list_id, name in info.lists_by_id.items() if name in trello.ignore_lists)` — ids not names, so the per-card comparison is an equality check that survives a column being renamed mid-run (R11's reasoning, reused); it must stay empty when `[trello]` is absent, when `ignore_lists` is empty, and on every early return from an unreachable board, which is what makes FR-002 hold structurally (depends on T005, T006, T013)
- [X] T015 Add `_is_ignored(list_id: str | None, status: BoardStatus) -> bool` to `src/robot_army/intake.py` — the **single** definition of ignored, returning `False` for a missing or empty `list_id` because the safe direction for a value we do not have is milestone 003's behaviour (depends on T013)
- [X] T016 [P] Create `tests/unit/test_ignored_lists.py` and cover `_is_ignored` there: a member id is ignored, a non-member is not, `None` and `""` are not, and an empty `ignored_list_ids` makes every call false (depends on T015)

**Checkpoint**: The ignored column ids resolve and the predicate exists. Nothing consults it yet, and
every existing test still passes unchanged.

---

## Phase 3: User Story 1 - Park a card without untagging it (Priority: P1) 🎯 MVP

**Goal**: A tagged card in an ignored column produces no issue, no comment and no move, and is never
tracked — so it cannot appear in any listing.

**Independent Test**: Configure one ignored column, put a tagged card naming a known repository in
it, wait one poll interval, and confirm no issue exists in any repository, no comment was added to
the card, and the card did not move.

### Implementation for User Story 1

- [X] T017 [US1] Add `ignored: int = 0` to `PollOutcome` in `src/robot_army/intake.py`, documented as *tagged cards this cycle sitting in an ignored column* — counted once, in `poll_board`, over the polled listing, whether or not the card is tracked (FR-021)
- [X] T018 [US1] In `poll_board` in `src/robot_army/intake.py`, skip `db.insert_card` for a card where `_is_ignored(card.list_id, status)` and increment the ignored count; add a comment recording that not tracking is what makes FR-006 true *structurally* — a card with no row cannot be surfaced by `robot-army cards` or the web listing, because both read rows (depends on T015, T017)
- [X] T019 [US1] In the same function, add `"ignored"` to the `trello.poll` audit record's detail beside `tagged` and `newly_tracked`, and add a comment stating that this aggregate is the whole record for ignored cards — no per-card record is written, which is the enumerated Principle III exception argued in [research.md](research.md) R6 and stated in [plan.md](plan.md) (depends on T018)
- [X] T020 [US1] In `poll_board`, add an explicit comment at the `return PollOutcome(...)` that `outcome.cards` **must** keep carrying every polled card including ignored ones, naming the consequence of removing them: `_reconcile_board_contents` drops every tracked card absent from that collection and `dropped` is terminal, so filtering here would make parking a tracked card destroy it permanently and silently (depends on T018)
- [X] T021 [US1] Add the ignored gate to `evaluate_card` in `src/robot_army/intake.py`, positioned **after** the `linked`, `dropped` and `creating` branches and **before** `_restore_from_marker`, returning `Verdict(card.card_id, "ignored", reason=<column name>)` without reading or writing the board; include the ordering table from [contracts/surfaces.md](contracts/surfaces.md) as a comment so a later edit cannot reorder it by accident (depends on T015)
- [X] T022 [US1] Deliberately do **not** add `"ignored"` to `_VERDICT_COUNTER` in `src/robot_army/intake.py`, and add a comment saying why: the poll record already counts the card, and counting the verdict as well would report the same card twice in one cycle and make the record unreconstructible (depends on T021)
- [X] T023 [US1] Resolve the column *name* for the verdict's reason from `status.info.lists_by_id` in `src/robot_army/intake.py`, so the listing reads `parked in 'Icebox'` rather than a 24-hex id (depends on T021)

### Tests for User Story 1

- [X] T024 [P] [US1] In `tests/unit/test_ignored_lists.py`, assert an untracked tagged card in an ignored column is not inserted, no issue is created, no card write occurs, and `PollOutcome.ignored` counts it (FR-003, FR-004) (depends on T018)
- [X] T025 [P] [US1] In `tests/unit/test_ignored_lists.py`, assert an ignored card naming **no** repository is not recorded as `needs_info` and receives no clarification comment — exclusion is decided before resolvability (FR-005) (depends on T021)
- [X] T026 [P] [US1] In `tests/unit/test_ignored_lists.py`, assert that with `ignore_lists` unset the tracked rows, issues created, board writes and audit records are identical to the milestone 003 path — the property SC-003 and FR-002 state, and the one most worth a dedicated test because everything else is built on it (depends on T018, T021)
- [X] T027 [P] [US1] In `tests/unit/test_intake_poll.py`, assert the `trello.poll` record carries `tagged`, `ignored` and `newly_tracked` as three distinct numbers, and that a cycle where every tagged card is ignored reports `ignored == tagged` with no `error` and no `skipped_reason` — "everything was excluded" must never be reachable-looking as "I could not ask" (depends on T019)
- [X] T028 [P] [US1] In `tests/unit/test_ignored_lists.py`, add one test per row of [contracts/surfaces.md](contracts/surfaces.md)'s gate-ordering table: a `linked` card in an ignored column is still finished off, a `dropped` one stays dropped, a `creating` one still resumes creation, an ignored card makes no `card_comments` call, and an ignored card never reaches `resolve_repository` (depends on T021)

**Checkpoint**: User Story 1 is complete and independently testable. Cards can be parked; nothing yet
guarantees they can be un-parked, which is the next story.

---

## Phase 4: User Story 2 - Un-parking works, and works on its own (Priority: P2)

**Goal**: A card moved out of an ignored column becomes intake on the next poll with no other action,
and a tracked card moved *into* one is parked rather than destroyed.

**Independent Test**: Put a tagged card in an ignored column, confirm nothing is created, move it to
an ordinary column, wait one poll interval, and confirm an issue appears with no further action.

### Implementation for User Story 2

- [X] T029 [US2] In `_refresh_tracked_card` in `src/robot_army/intake.py`, write the board's current `idList` to `current_list_id` whenever it differs from the stored value, inside the transaction the function already opens — and leave the activity baseline untouched, because that omission is FR-023 of milestone 003 and overwriting it would erase the edit signal (depends on T011)
- [X] T030 [US2] In the same function, detect the two transitions across `status.ignored_list_ids` and write `trello.parked` / `trello.released` records naming the card, the column id, the column name and the state — **in the same transaction as the column update**, matching `transition_card`'s discipline so a crash cannot produce one without the other; one record per transition, never one per poll (FR-023, FR-024) (depends on T029)
- [X] T031 [US2] Pass the `BoardStatus` into `_refresh_tracked_card` / `_reconcile_board_contents` in `src/robot_army/intake.py` so the transition detection can see `ignored_list_ids`, threading it from `run_cycle` rather than re-deriving it (depends on T030)
- [X] T032 [US2] Add `current_list_id`, `parked` and `parked_list` to `_card_dict` in `src/robot_army/operations.py`, deriving `parked` per [data-model.md](data-model.md) — tracked, state not in (`linked`, `dropped`, `creating`), `current_list_id` in the ignored set — and reading the column name from the configuration rather than from the board, so the listing makes **no board request** and keeps working with the board unreachable (depends on T029)
- [X] T033 [US2] Render the parked condition in the `cards` table in `src/robot_army/operations.py` **alongside** whatever else the card is, not instead of it: a card can be `needs_info` *and* parked, which is what the author produces by writing an ambiguous card and parking it (depends on T032)
- [X] T034 [US2] Render parked in the cards page in `src/robot_army/web/pages.py` using the word **"parked"**, never "held" — `_CARD_STATE_COPY` already renders `needs_info` as "held — the card does not say which repository" and `PollOutcome.held` already counts that, so reusing the word would say one thing for two unrelated conditions; exclude parked cards from the page's outstanding `needs_info` count, because a parked card is not waiting on the author (FR-006, FR-009) (depends on T032)
- [X] T035 [US2] Confirm `robot-army rescan` (`forced=True`) does **not** override the gate in `src/robot_army/intake.py`, and add a comment saying why: rescan exists to re-resolve a card the author edited, and making it a way to act on a card the author deliberately excluded would give the ignore list an exception nobody asked for (depends on T021)

### Tests for User Story 2

- [X] T036 [P] [US2] In `tests/unit/test_ignored_lists.py`, assert the full round trip: a `needs_info` card parked keeps its state and its `reason`, is re-evaluated on release, is `needs_info` again, and receives **no second clarification comment** because the reason did not change (FR-008, FR-009, FR-010) (depends on T029, T030)
- [X] T037 [P] [US2] In `tests/unit/test_ignored_lists.py`, assert a tracked unlinked card moved into an ignored column is **not** transitioned to `dropped` and does not appear in `PollOutcome.dropped` — the regression test for the trap this whole design turns on, since `dropped` is terminal and nothing returns from it (depends on T020, T029)
- [X] T038 [P] [US2] In `tests/unit/test_ignored_lists.py`, assert exactly one `trello.parked` record across several poll cycles with the card unmoved, and one `trello.released` on the way out — the FR-024 property that a parked card does not re-log every cycle (depends on T030)
- [X] T039 [P] [US2] In `tests/unit/test_ignored_lists.py`, assert that **removing** a column from `ignore_lists` makes its tagged cards intake on the next poll with no other action, and that a card recorded as having left the board is **not** revived by any ignore-list change (FR-011, FR-012) (depends on T021)
- [X] T040 [P] [US2] In `tests/unit/test_ignored_lists.py`, assert `robot-army cards` shows a parked card as both parked and `needs_info`, that the JSON view carries `parked` and `parked_list`, and that the listing makes no board request (depends on T032, T033)
- [X] T041 [P] [US2] In `tests/unit/test_ignored_lists.py`, assert a card with `current_list_id` NULL — the pre-migration row — is treated as not parked and evaluated normally (depends on T032)

**Checkpoint**: Parking is reversible. User Stories 1 and 2 together are the feature.

---

## Phase 5: User Story 3 - Ignoring a column never abandons work already in flight (Priority: P3)

**Goal**: A card with a recorded issue is unaffected by the ignore list in either direction. This
story is almost entirely tests, which is correct — it is a statement about what must **not** happen.

**Independent Test**: Take a card through to a linked issue with a running session, add its current
column to the ignore list, and confirm the session, the issue, the mapping and the subsequent board
moves are all unaffected.

- [X] T042 [US3] Confirm no lifecycle path in `src/robot_army/intake.py` — `on_session_active`, the done-list move, the abandonment return to `origin_list_id` — consults `ignored_list_ids`, and add a comment at the move helper stating that the ignore list gates intake only, so a move *into* an ignored column is ordinary and expected (FR-014) (depends on T021)
- [X] T043 [P] [US3] In `tests/unit/test_ignored_lists.py`, assert a `linked` card keeps its mapping, its issue and its session when its column is added to `ignore_lists` and when it is dragged into an ignored column (FR-013) (depends on T021)
- [X] T044 [P] [US3] In `tests/unit/test_ignored_lists.py`, assert the full lifecycle still runs for a linked card with **every** column ignored — moved to in-progress on confirmation, to done on issue close, returned to origin on abandonment (FR-014, SC-007) (depends on T042)
- [X] T045 [P] [US3] In `tests/unit/test_config.py`, assert a configuration listing `in_progress_list` and `done_list` in `ignore_lists` loads without error, and in `tests/unit/test_ignored_lists.py` assert it changes nothing observable — the two settings act on disjoint sets of cards (FR-015) (depends on T002)
- [X] T046 [P] [US3] In `tests/unit/test_ignored_lists.py`, assert a card moved into an ignored column while in `creating` still resumes its creation and produces exactly one issue — the spec's mid-creation edge case, and the one that would break the §11 invariant if the gate were placed one branch earlier (depends on T021)

**Checkpoint**: The blast radius is bounded and proven.

---

## Phase 6: User Story 4 - The configuration is checkable before the daemon runs (Priority: P4)

**Goal**: A renamed or mistyped column is reported by name, with the board's actual columns listed
beside it, and refuses ingestion rather than silently widening intake back to milestone 003's.

**Independent Test**: Configure an ignored column that does not exist on the board and confirm
`doctor` names it, lists the columns that do exist, and refuses ingestion while dispatch continues.

- [X] T047 [US4] In `check_board` in `src/robot_army/intake.py`, append one `BoardCheck` per configured name via the existing `_present()` helper, after the tag and lifecycle-column checks and in the order the author wrote them — `_present()` already produces the message FR-017 asks for, naming what is missing and listing what the board has (depends on T014)
- [X] T048 [US4] Add `"ignored_lists"` to the `trello.board.check` audit record's detail in `src/robot_army/intake.py`, so the record answers "which columns were being ignored?" without re-reading the configuration file — the other half of the reconstruction standard the aggregate poll count relies on (FR-022) (depends on T047)
- [X] T049 [P] [US4] In `tests/unit/test_board_preconditions.py`, assert a configured name that exists passes and is named; that one that does not fails, names the missing column and lists the board's actual columns; and that a name differing only in letter case fails rather than matching (FR-016, FR-017, FR-019) (depends on T047)
- [X] T050 [P] [US4] In `tests/unit/test_board_preconditions.py`, assert a failing ignored-column check makes `BoardStatus.ok` false and that `poll_board` therefore returns `skipped_reason="board preconditions failed"` — and that GitHub polling and dispatch of the author's own issues are untouched, which is the half most likely to regress unnoticed (FR-018) (depends on T047)
- [X] T051 [P] [US4] In `tests/unit/test_board_preconditions.py`, assert an empty `ignore_lists` appends **zero** checks, so the board section reports exactly what milestone 003 reported, and that `trello.board.check` carries an empty `ignored_lists` rather than omitting the field (depends on T047, T048)
- [X] T052 [P] [US4] In `tests/unit/test_ignored_lists.py`, assert that where the board has **two** columns of the configured name, cards in **both** are excluded — the property `lists_by_id` exists for, and one CI can only prove against a constructed `BoardInfo` (FR-019b) (depends on T014)

**Checkpoint**: All four stories complete.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T053 [P] Add `ignore_lists` to the `[trello]` section of `share/config.example.toml` with a comment saying it is empty by default, that it gates intake only so a card with an issue is never affected, and that a name not on the board is a startup failure rather than a warning
- [X] T054 [P] Document `ignore_lists` in the "The intake board" section of `README.md`, in the register the file uses — what it does for the author, and why parking is reversible where untagging is not
- [X] T055 [P] Add `current_list_id` to the `cards` table documentation in `docs/state.md`, with the four-way table distinguishing it from `origin_list_id`, `placed_list_id` and `pending_move_to`, and note the schema version is now 6
- [X] T056 Add the 006 entry to `docs/roadmap.md` — status, what it changes, the trap it avoids, and an empty *What running it taught* heading for the live round — and move the "whatever survives contact with reality" parking lot from 006 to 007, exactly as 005 moved it from 005 to 006
- [X] T057 Run `uv run ruff check` and `uv run ruff format --check` over the changed files and fix what they report
- [X] T058 Run the full suite with `uv run pytest`; the constitution's gate is that it passes, not a coverage number
- [X] T059 Work through [quickstart.md](quickstart.md) sections 1–3 and 7, which need no board, and confirm each expectation
- [ ] T060 Work through [quickstart.md](quickstart.md) sections 4–6 against a **throwaway** private board with real credentials — these file real GitHub issues — and record what the round taught under the new roadmap heading, in particular the item quickstart names as design-relevant: whether dragging a card between columns moves Trello's `dateLastActivity` stamp, because if it does not, the release path must force one re-evaluation rather than relying on the activity-baseline short circuit (depends on T056)

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)**: no dependencies.
- **Phase 2 (Foundational)**: depends on Phase 1 for `TrelloConfig.ignore_lists`. **Blocks every
  story.** Nothing changes behaviour until it is done, so it is a safe place to stop.
- **Phase 3 (US1)**: depends on Phase 2. This is the MVP.
- **Phase 4 (US2)**: depends on Phase 2 and, in practice, on US1 — see the honest note below.
- **Phase 5 (US3)**: depends on Phase 3 for the gate it asserts nothing about linked cards.
- **Phase 6 (US4)**: depends on T014 only. Genuinely independent of US1–US3 and can be done at any
  point after Phase 2.
- **Phase 7 (Polish)**: depends on the stories being shipped.

### Honest note on story independence

**US1 and US2 are not independently shippable, and the tasks should not pretend otherwise.** US1
alone gives the author a parking column that is a one-way trap for any card that was already tracked:
park a `needs_info` card and, without T029–T031, it stops being refreshed and its parked condition
cannot be seen anywhere. US1 is a complete increment only for cards that were *never* tracked, which
is the common case but not the safe one.

The MVP is therefore **US1 + US2 together**. US3 and US4 are genuinely separable, and US4 could ship
first if the author wanted the configuration checked before anything used it.

The template's parallel-team framing does not apply — one maintainer, and `[P]` here means only that
two tasks do not touch the same file.

### Parallel opportunities

- **Phase 1**: T004 runs alongside T001–T003.
- **Phase 2**: three independent tracks — the board information (T005 → T006 → T007, T008), the
  schema (T009 → T010 → T011, T012), and the predicate (T013 → T014 → T015 → T016). The schema track
  touches no file the board-information track touches.
- **Phase 3**: T024–T028 are all in one new test file and are marked `[P]` because they are
  independent cases, not because they are in different files — write them in any order.
- **Phase 6**: entirely parallel with Phases 3–5 after T014.
- **Phase 7**: T053, T054 and T055 touch three different files and can be done together.

---

## Implementation Strategy

### MVP: User Stories 1 and 2 together

1. Phase 1 → Phase 2. Stop and run the suite: nothing should have changed behaviour.
2. Phase 3 (US1) → Phase 4 (US2).
3. **STOP and VALIDATE**: quickstart sections 4 and 5, against a throwaway board. Section 5 is the
   one that matters — the park/un-park round trip on a card that was already tracked.

### Incremental delivery after the MVP

4. Phase 5 (US3) — the immunity proofs. Cheap, almost all tests, and worth doing before the live
   round rather than after, because the failure it guards against is the expensive one.
5. Phase 6 (US4) — the checks. Could equally have gone first.
6. Phase 7 — documentation, then the live round.

### Sequencing against the rest of the project

This milestone touches the board path and nothing else. It does not block, and is not blocked by,
the verification round recorded in [issue #1](https://github.com/jantman/robot-army/issues/1), which
is about milestone 005's repository derivation. The two can run in either order.

---

## Notes

- `[P]` means the task touches no file another pending task touches. With one maintainer it is a
  safe-to-interleave marker, not a staffing plan.
- Commit after each task or logical group, with a message explaining **why**, per the constitution.
- Test-first is not required. The gate is that the tests exist, are meaningful, and pass.
- Two tests are worth more than the rest: **T037** (a parked card is not dropped) and **T028** (the
  gate's position). If time is short anywhere, it is not on those.
