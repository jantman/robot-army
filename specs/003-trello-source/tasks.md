---

description: "Task list for Trello Source (milestone 003)"
---

# Tasks: Trello Source

**Input**: Design documents from `/specs/003-trello-source/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: Included, and not optional here. The constitution's Development Workflow requires unit
tests for every new or changed unit of behaviour, and *additional* failure-and-interruption tests for
persistence, state machines, and code parsing external input — this milestone is all three at once,
and the card description it parses is semi-untrusted text. It also says test-first is **not**
mandatory and coverage targets **must not** be adopted, so test tasks sit beside the code they cover
rather than ahead of it. Write them in whichever order suits the work; the gate is that they exist,
are meaningful, and pass.

**Organization**: By user story, in the priority order spec.md assigns, so each story is a shippable
increment. One maintainer, so `[P]` marks work that does not collide — not work that needs a second
person.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Touches files no other pending task touches; safe to interleave
- **[Story]**: US1–US4, mapping to spec.md's user stories
- Every task names its exact file path

## Path Conventions

Single project, as 001 and 002 established: `src/robot_army/`, `tests/unit/`, `tests/integration/`.
New code lands in `src/robot_army/boundaries/trello.py`, `src/robot_army/intake.py`, and
`src/robot_army/cardstates.py`.

---

## Phase 1: Setup

**Purpose**: The module skeletons and the one configuration section everything else reads.

- [X] T001 Create `src/robot_army/boundaries/trello.py`, `src/robot_army/intake.py`, and `src/robot_army/cardstates.py` as empty modules whose docstrings state the split the plan's Structure Decision makes — `trello.py` is the only code that knows the API exists, `intake.py` the only code that knows what a card means
- [X] T002 Add the `[trello]` section as a `TrelloConfig` frozen dataclass in `src/robot_army/config.py` per [contracts/config.md](contracts/config.md) — `board_id`, `label`, `in_progress_list`, `done_list`, `poll_seconds` (default 300), `timeout_seconds`, `max_retries`, `api_base`, and the four credential keys — parsed by `parse()` alongside the existing sections and **absent by default**, so `config.trello is None` means the source is inert (FR-001)
- [X] T003 Add `[trello]` load-time validation to `src/robot_army/config.py`: required `board_id`, exactly one of `key_env`/`key_file` and of `token_env`/`token_file`, literal-credential rejection with the same message the `[github]` equivalents use, `0600` mode on any `*_file`, and unknown keys as an error (depends on T002)
- [X] T004 [P] Extend `tests/unit/test_config.py` with cases for the `[trello]` defaults, an absent section leaving `config.trello` as `None`, each validation failure above, and a `key_env` holding a literal credential being refused

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The schema, the card state machine, the boundary, the effect wiring, the board
preconditions, and a poll cycle that tracks cards **and writes nothing anywhere**. That last point is
the checkpoint: at the end of this phase the system can see the board and has created nothing.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Schema and state

- [X] T005 Append `_migration_003` creating the `cards` table with `idx_cards_identity`, the partial `idx_cards_issue`, and `idx_cards_state` exactly as [data-model.md](data-model.md) specifies, to `src/robot_army/migrations.py`, leaving the earlier migrations untouched and letting `SCHEMA_VERSION` derive from the tuple length
- [X] T006 [P] Add the `Card` frozen dataclass to `src/robot_army/models.py`, matching the existing model style so `from_row` handles it, with `repo_key`, `issue_number`, `issue_url`, `origin_list_id`, `placed_list_id`, `pending_move_to`, `comment_posted_at`, `intent_at`, and `archived_at` all optional
- [X] T007 Add card accessors to `src/robot_army/db.py` — `find_card`, `get_card_by_id`, `list_cards`, `insert_card`, `update_card_columns`, and `find_card_by_issue` — honouring the existing `include_simulated` scoping convention (depends on T005, T006)
- [X] T008 [P] Extend `tests/unit/test_migrations.py` with a case asserting migration 003 runs on a 002-era database, that a killed migration leaves `user_version` unadvanced and re-runs, and — the point of the table — that **both** unique indexes reject a second row, so a create path that skipped its mapping check raises `IntegrityError` rather than duplicating
- [X] T009 Implement the card state machine in `src/robot_army/cardstates.py`: a `CardState` StrEnum (`discovered`, `needs_info`, `creating`, `linked`, `dropped`), a frozen `CARD_TRANSITIONS` set matching data-model.md's table exactly, and a single `transition_card()` gate that writes its audit record inside the same transaction as the state change, as `states.transition_work_item` already does
- [X] T010 [P] Add `tests/unit/test_card_states.py` enumerating every legal transition and asserting the illegal ones are refused — specifically that `creating` has no exit to `needs_info` or `dropped`, and that `linked` is terminal even for an archived card

### The boundary

- [X] T011 Add the `Card` and `BoardInfo` value types and the `CardSourceReader` / `CardSourceWriter` protocols to `src/robot_army/boundaries/__init__.py` per [contracts/card-source.md](contracts/card-source.md), exporting them from `__all__`, with `last_activity` carried as the string the API returned rather than a parsed datetime
- [X] T012 Implement `TrelloCardReader` in `src/robot_army/boundaries/trello.py` with `board_info`, `poll`, `get_card`, and `card_comments`, over `httpx`, mirroring `GitHubReader`'s explicit connect/read timeouts and bounded backoff with jitter, honouring `Retry-After` on `429`, and raising `TransportError` — never converting a failure into an empty card list
- [X] T013 Authenticate in `src/robot_army/boundaries/trello.py` with the `Authorization: OAuth oauth_consumer_key="…", oauth_token="…"` header, never the query string, and ensure no log line carries a full URL with a query string — R3 explains why this is the single most dangerous difference from the GitHub client (depends on T012)
- [X] T014 Implement `TrelloCardWriter` and `SimulatedCardWriter` in `src/robot_army/boundaries/trello.py` with `comment` and `move`, both returning the card's refreshed `last_activity` alongside their result so the caller can update the baseline in the same transaction (R9); the simulated writer emits an audit record naming the call and its full arguments and returns a structurally valid result
- [X] T015 Add `create_issue(repo_key, title, body) -> Issue` to `IssueSourceWriter` in `src/robot_army/boundaries/__init__.py` and implement it on `GitHubWriter` in `src/robot_army/boundaries/github.py`, returning the created issue as GitHub reported it, with **no parameter that could carry a label** — the human gate is absent from the interface rather than defended by a rule (FR-015)
- [X] T016 Implement `SimulatedIssueWriter.create_issue` in `src/robot_army/boundaries/github.py` returning a structurally valid `Issue` with a recognisable high-offset fake number and a well-formed URL, because returning `None` or raising would let the simulated path diverge from the real one at the point the requirement exists to prevent (depends on T015)
- [X] T017 [P] Add `tests/unit/test_trello_secrets.py` asserting that neither the API key nor the token appears in any audit record, log line, or exception message produced by a successful call, a `401`, and a transport failure
- [X] T018 [P] Add `tests/unit/test_simulated_writers.py` asserting `SimulatedIssueWriter.create_issue` and `SimulatedCardWriter` return structurally valid results and emit audit records carrying their full arguments

### Effect wiring

- [X] T019 Add `card_reader` (real at every level) and `card_writer` (real at `live` only) to `REAL_AT` and the two matching selections to `wire()` in `src/robot_army/effects.py`, with no `SimulatedCardReader` in existence so a bug that tries to fake board reads fails to import
- [X] T020 [P] Extend `tests/unit/test_effects.py` to assert the two new table rows at all four levels, that `wire()` returns the expected implementation for each, and that the existing "no effect-level branch outside `effects.py`" grep still passes with `trello.py` and `intake.py` present (depends on T019)

### Board preconditions

- [X] T021 Implement `check_board(...)` in `src/robot_army/intake.py` performing R10 and R11's four checks — reachable and authenticated, `permission_level == "private"`, configured label present, both lifecycle lists present — returning a structured per-check result rather than a bare boolean, and **recording** the board's member list without gating on it (FR-004a)
- [X] T022 Call `check_board` at daemon startup in `src/robot_army/daemon.py`, and on failure disable **ingestion only** with an anomaly naming the failed check, leaving polling and dispatch of issues the author wrote themselves entirely unaffected (depends on T021)
- [X] T023 Extend `doctor` in `src/robot_army/operations.py` to report all four board checks individually plus the member list as information, exiting `4` if any check fails, so the board can be verified without starting the daemon (depends on T021)
- [X] T024 [P] Add `tests/unit/test_board_preconditions.py` covering each of the four checks failing individually, that a failure disables ingestion without disabling dispatch, that the anomaly names which check failed, and — the case the author corrected this design into — that a private board with **extra members ingests normally** while the member list is recorded (FR-004a)

### The read-only poll cycle

- [X] T025 Implement `poll_board(...)` in `src/robot_army/intake.py`: read cards through the boundary, upsert a `cards` row in `discovered` for each tagged card, and store bookkeeping in `poll_state` under the synthetic key `trello:board:<board_id>` with the same backoff shape `poll.poll_repo` uses (depends on T007, T012)
- [X] T026 Handle board failure in `src/robot_army/intake.py` so `TransportError` is recorded with its cause, increments `consecutive_failures`, extends backoff, and raises an anomaly at the same threshold GitHub uses — never reported as "no cards found" (FR-009) (depends on T025)
- [X] T027 Register the board poll as a `Job` on `trello.poll_seconds` in `Daemon._build_jobs` in `src/robot_army/daemon.py`, ordered after `poll` and before `dispatch`, and skipped entirely when `config.trello is None` (depends on T025)
- [X] T028 [P] Add board reachability and last-poll age to the heartbeat in `src/robot_army/health.py` so a degraded board is visible in `robot-army health` and on every web view
- [X] T029 [P] Add `tests/unit/test_intake_poll.py` covering: an unconfigured installation making no board request at all (FR-001, quickstart scenario 1), a successful poll creating one row per tagged card, an untagged card creating nothing, a transport failure recording and backing off rather than returning empty, and the anomaly threshold

**Checkpoint**: cards on the board are tracked in the database; nothing has been created in any
repository and nothing has been written to the board.

---

## Phase 3: User Story 1 - Capture a task anywhere, find an issue waiting (Priority: P1) 🎯 MVP

**Goal**: a tagged card naming one known repository becomes exactly one GitHub issue, unlabelled,
with a comment on the card linking to it — and nothing dispatches.

**Independent Test**: add one card naming a repository, wait one poll interval, confirm a matching
issue exists, that it is not labelled for dispatch, that the card links to it, and that no session
was started. Then label it by hand and confirm exactly one work item appears by the ordinary path.

- [X] T030 [US1] Implement repository resolution in `src/robot_army/intake.py` per R8: scan the card's title and description for `github.com/<owner>/<name>` URLs, bare `<owner>/<name>` references, and filesystem paths, keep only those that map to a key in `config.repos`, deduplicate by resolved key, and return resolvable only when exactly one survives
- [X] T031 [P] [US1] Add `tests/unit/test_repo_resolution.py` with adversarial card text: a pasted log containing `src/robot_army` and `docs/roadmap.md` must resolve to **nothing**, a URL plus the same repository's local path must resolve to one, two different configured repositories must be ambiguous, and an unconfigured `owner/name` must resolve to nothing with a reason naming it
- [X] T032 [US1] Implement issue composition in `src/robot_army/intake.py`: the card's title becomes the issue title, the card's description is carried as **quoted** content and never interpreted as configuration, command, or directive (FR-013), and the body always contains the card's URL, which R6's recovery depends on
- [X] T033 [US1] Define the marker comment format in `src/robot_army/intake.py` as a fixed prefix followed by the issue URL, matched by prefix rather than parsed, with the prefix as a module constant so the writer and the recovery reader cannot drift
- [X] T034 [US1] Implement the normal-operation duplicate check in `src/robot_army/intake.py`: consult the `cards` mapping row first and do nothing if one exists, without reading the board's comments at all — §11's "don't parse comments as the authoritative source in normal operation" as a call-site rule. This lands **before** the create path, so no revision of the tree ever polls a linked card into a second issue (depends on T007, T009)
- [X] T035 [US1] Implement the four-step creation in `src/robot_army/intake.py` per R6, entered only after the T034 guard returns clear — commit the `creating` intent row with `repo_key` and `intent_at`; call `create_issue`; write the mapping and transition to `linked`; comment on the card and record `comment_posted_at` — each step in its own transaction so every seam is separately resumable (depends on T009, T015, T030, T032, T033, T034)
- [X] T036 [P] [US1] Add `tests/unit/test_card_dedup.py` asserting that with a mapping row present no issue is created and `card_comments` is **never called**, and that repeated evaluation of a linked card is a no-op
- [X] T037 [US1] Handle creation failure in `src/robot_army/intake.py` so the row stays in `creating` with `reason` set and `create_failures` incremented, retried on a later pass, raising an anomaly at a threshold, and **never** leaving a comment on the card claiming an issue exists (FR-019) (depends on T035)
- [X] T038 [US1] Emit the audit records `trello.evaluated`, `trello.issue.create`, and `trello.card.comment` from `src/robot_army/intake.py` as intent/outcome pairs written before each call, naming the card and, where one exists, the repository and issue (depends on T035)
- [X] T039 [P] [US1] Add `tests/integration/test_card_to_issue.py` driving the happy path against fake boundaries: one tagged resolvable card produces one issue, one marker comment, and a `linked` row
- [X] T040 [P] [US1] Add to `tests/integration/test_card_to_issue.py` the two gate assertions: the created issue **never** carries the dispatch label whatever the card says, and board ingestion creates **no** `work_items` row — the human gate is structural, not conventional. Then label the issue and poll GitHub, asserting exactly **one** `work_items` row appears by the ordinary path (FR-018), so both halves of the gate are tested rather than only the refusal (depends on T039)
- [X] T041 [P] [US1] Add to `tests/integration/test_card_to_issue.py` the effect-level case: at `no-remote` the card is read and evaluated for real, nothing is created, nothing is written to the board, the log carries every would-be write with full arguments, and the row is `dry_run` (FR-039, SC-009)

**Checkpoint**: cards become issues, issues stay unlabelled, and a labelled issue dispatches by the
ordinary path. This is a shippable increment on its own.

---

## Phase 4: User Story 2 - A card that doesn't say enough is held, not guessed at (Priority: P2)

**Goal**: unresolvable cards are held with a reason, commented on exactly once, re-evaluated
automatically when the author edits them, and visible in both interfaces.

**Independent Test**: add a card with no repository reference, confirm no issue exists anywhere and it
is surfaced as awaiting clarification; edit it to name a repository and confirm an issue appears
within one poll interval with no further human action.

- [X] T042 [US2] Implement the `needs_info` path in `src/robot_army/intake.py`: transition unresolvable cards, set `reason` to a message specific enough to fix the card (naming the ambiguity or the unconfigured reference), and never create an issue for them (FR-021)
- [X] T043 [US2] Implement the one-comment rule in `src/robot_army/intake.py` by comparing `reason` against `commented_reason` — comment only when they differ, then record the new value — so a card held for weeks accumulates one comment, not one per poll (FR-022) (depends on T042)
- [X] T044 [US2] Refresh `last_activity` from every one of our own writes in the same transaction that records the write, in `src/robot_army/intake.py`, closing R9's self-sustaining re-evaluation loop (depends on T014, T043)
- [X] T045 [P] [US2] Add `tests/unit/test_card_activity.py` asserting that a poll immediately following our own comment triggers no re-evaluation, and that an edit by the author does trigger one — the trap and its closure, tested from both sides
- [X] T046 [US2] Implement automatic re-evaluation in `src/robot_army/intake.py` when a `needs_info` card's `last_activity` differs from the stored baseline, following the ordinary creation path when it now resolves (FR-023) (depends on T044)
- [X] T047 [US2] Implement the `dropped` path in `src/robot_army/intake.py` for a card that loses its tag, is archived, or is deleted **before** it is linked; a `linked` card instead records `archived_at` and keeps its mapping, because dropping it would let a re-tagged card create a second issue (FR-025)
- [X] T048 [US2] Add a `cards` operation to `src/robot_army/operations.py` returning card id, title, state, resolved repository and issue, reason, and time in state, exiting `3` with an explanatory message when `[trello]` is unconfigured rather than printing an empty table
- [X] T049 [US2] Add the `cards` verb to `src/robot_army/cli.py` with `--state`, `--include-simulated`, and `--json`, and add it to `READ_COMMANDS` (depends on T048)
- [X] T050 [US2] Add a `rescan` operation to `src/robot_army/operations.py` that forces re-evaluation through the existing `control.py` job-request marker, exiting `1` for an untracked card, `2` for a card not in `needs_info`, and `3` when no daemon is running to service it (depends on T046)
- [X] T051 [US2] Add the `rescan` verb to `src/robot_army/cli.py`, with `--all-needs-info` (depends on T050)
- [X] T052 [US2] Drain the rescan job request in `Daemon.tick` in `src/robot_army/daemon.py`, reusing the marker mechanism 002 already built for `poll` and `reconcile` without modifying it (depends on T050)
- [X] T053 [US2] Add `GET /cards` to `ROUTES` and a `view_cards` handler in `src/robot_army/web/server.py`, with the `.json` suffix and the standard view chrome — effect level, heartbeat age, pause state, anomaly count (depends on T048)
- [X] T054 [US2] Add the cards view renderer to `src/robot_army/web/pages.py`, excluding simulated rows by default and marking them visibly when included (depends on T053)
- [X] T055 [US2] Add `POST /card/{id}/rescan` to `src/robot_army/web/server.py` with the confirm-then-post pattern every other mutating route uses (depends on T050, T053)
- [X] T056 [US2] Join `cards` to work items on `(repo_key, issue_number)` against `work_items.source_id` in `src/robot_army/operations.py`, exposing the card URL on the work-item payload without adding a column to `work_items` (R16), and render it beside the issue URL in `robot-army show` in `src/robot_army/cli.py` (FR-017, FR-048)
- [X] T057 [P] [US2] Render the card link beside the issue link in the work-item detail and listing views in `src/robot_army/web/pages.py`, so a work item whose issue came from a card is identifiable as such wherever work items are shown (FR-017, FR-048) (depends on T056)
- [X] T058 [P] [US2] Extend `tests/unit/test_web_routing.py` and `tests/unit/test_web_render.py` with the cards view and the rescan route, including escaping of card text and the simulated-row marking
- [X] T059 [P] [US2] Add `tests/integration/test_card_needs_info.py`: an unresolvable card creates nothing anywhere and is listed with its reason; five further polls add no second comment; an edit naming the repository resolves it with no human action; an ambiguous card naming two configured repositories is held rather than resolved to either

**Checkpoint**: unresolvable cards are safely parked, self-heal on edit, and are visible from the
terminal and the phone.

---

## Phase 5: User Story 3 - The board tells the truth about what is happening (Priority: P3)

**Goal**: the card's list reflects reality — in progress while a session runs, done when the issue
closes, back where it came from when the work is abandoned — and never overrides a move the author
made by hand.

**Independent Test**: take one card through card → issue → label → dispatch → close and confirm its
list at each stage; take a second to abandonment and confirm it returns to its original list with a
comment; move a third by hand and confirm the system refuses to move it and comments instead.

- [X] T060 [US3] Implement `on_session_active(...)` in `src/robot_army/intake.py` moving the card to the configured in-progress list and recording `origin_list_id` and `placed_list_id`, called from the point in `src/robot_army/dispatch.py` where a session is **confirmed** running rather than where it is launched (FR-027)
- [X] T061 [US3] Implement `on_issue_closed(...)` in `src/robot_army/intake.py` moving the card to the configured done list with an outcome comment, called from wherever the issue-closed observation already lands in `src/robot_army/reconcile.py` (FR-028)
- [X] T062 [US3] Implement `on_work_abandoned(...)` in `src/robot_army/intake.py` returning the card to `origin_list_id` with a comment naming the reason, so a card never sits in the in-progress list claiming to be busy when nothing is (FR-029)
- [X] T063 [US3] Implement the manual-move refusal in `src/robot_army/intake.py`: read the card's current `idList` before any move and, if it differs from `placed_list_id`, do **not** move — comment with what would have been done instead (FR-030, R12) (depends on T060)
- [X] T064 [US3] Write `pending_move_to` before every move attempt and clear it after, in `src/robot_army/intake.py`, so an interrupted move is identified as ours rather than mistaken for the author's on the next pass (depends on T063)
- [X] T065 [US3] Emit `trello.card.move` and `trello.card.move_refused` audit records from `src/robot_army/intake.py` before the call and again with the outcome, naming the card, both lists, and the result (depends on T063)
- [X] T066 [P] [US3] Add `tests/unit/test_card_lifecycle_guard.py` covering the manual-move refusal, and the `pending_move_to` case where the card is already in the target list — which must be recognised as our own interrupted move, not as a move by the author
- [X] T067 [P] [US3] Add `tests/integration/test_card_lifecycle.py` driving a card through active → closed and confirming its list at each stage, a second card through abandonment confirming it returns to its origin list with a reason, and a manually moved card confirming refusal plus comment
- [X] T068 [P] [US3] Add a case to `tests/integration/test_card_lifecycle.py` asserting that a missing configured list is caught by the startup precondition rather than discovered mid-lifecycle after the issue already exists (depends on T024)

**Checkpoint**: the board is an honest status surface, and it never fights the author.

---

## Phase 6: User Story 4 - One card, one issue, no matter what happened (Priority: P4)

**Goal**: the §11 invariant holds under repeated polling, under a kill at every seam of the creation
sequence, and under total loss of the database.

**Independent Test**: poll the same card repeatedly across a restart and confirm one issue; kill the
daemon between each pair of creation steps in turn and confirm no duplicates; delete the database and
confirm the marker comment restores the mapping without creating a second issue.

- [X] T069 [US4] Add a bounded repository-issue listing call to `GitHubReader` in `src/robot_army/boundaries/github.py`, taking a `since` parameter and an author filter, with the same explicit timeout and backoff policy as its neighbours — the immediately consistent endpoint R6 requires, never search
- [X] T070 [US4] Implement `creating`-row recovery in `src/robot_army/intake.py` per R6: list issues in the target repository created since `intent_at`, authored by us, and adopt one whose body contains this card's URL — using the **listing** endpoint, never search, because the search index lags by minutes and would miss exactly the issue a crash orphaned (depends on T069)
- [X] T071 [US4] Implement marker-comment restoration in `src/robot_army/intake.py`: when no mapping row exists, read the card's comments for the marker prefix and restore the mapping from it before creating anything — the recovery half of R7's ordering, reached only when the mapping is absent (depends on T033, T034)
- [X] T072 [US4] Implement the deferred-comment retry in `src/robot_army/intake.py` for a `linked` row with `comment_posted_at` NULL, checking for an existing marker comment first so a retry cannot double-post (depends on T035)
- [X] T073 [US4] Run the recovery sweep — unfinished `creating` rows and missing marker comments — at daemon startup and at the head of each board poll in `src/robot_army/intake.py`, so an interruption is resolved on the next pass rather than at the next restart (depends on T070, T072)
- [X] T074 [US4] Detect, during the same recovery sweep, a `linked` row whose issue no longer resolves — deleted, transferred, or otherwise gone — in `src/robot_army/intake.py`, and raise an anomaly through `db.raise_anomaly` naming the card and the missing issue while **keeping the mapping intact**, so the card never triggers a fresh creation (FR-037) (depends on T073)
- [X] T075 [US4] Emit `trello.recovered` audit records from `src/robot_army/intake.py` naming which recovery path fired and what it found, so a recovery is visible in the log rather than silent (depends on T073)
- [X] T076 [P] [US4] Add `tests/integration/test_card_interruption.py` killing the sequence at each of its three seams in turn and asserting no duplicate: after the intent with no issue (retry creates one), after the issue with no mapping (listing adopts it), after the mapping with no comment (comment posted once)
- [X] T077 [P] [US4] Add to `tests/integration/test_card_interruption.py` the database-loss case: with the mapping rows discarded, re-polling a previously processed card restores the mapping from its marker comment and creates **no** second issue (FR-034)
- [X] T078 [P] [US4] Add to `tests/integration/test_card_interruption.py` the repetition case: one hundred consecutive polls of one unchanged card, across a simulated restart, yield exactly one issue and one marker comment (SC-002)
- [X] T079 [P] [US4] Add `tests/unit/test_card_invariant.py` asserting the reverse-direction guard required by FR-036 — an attempt to create a card for an issue that already has one is refused by the mapping check, so the card → issue → card loop is structurally impossible even though nothing builds that direction
- [X] T080 [P] [US4] Add to `tests/unit/test_card_invariant.py` a case asserting that a simulated row does not occupy the live row's identity, so a `no-remote` run followed by a `live` run of the same card performs the real creation (FR-041), and a case asserting that a `linked` row whose issue has vanished raises an anomaly and creates nothing (FR-037)

**Checkpoint**: the invariant the milestone exists to protect is proven under the failures that
threaten it.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T081 [P] Document the `cards` table, its state machine, and the synthetic `trello:board:<id>` poll-state key in `docs/state.md`
- [X] T082 [P] Document the ten new `trello.*` actions and the enumerated Principle III exception — individual board reads within a cycle are not separately audited — in `docs/logging.md`
- [X] T083 [P] Document configuring the board, the private-board requirement, and the credential env vars in `README.md`
- [X] T084 Record the residual double-failure gap from R6 — crash between issue creation and mapping *combined with* database loss — in `docs/state.md` beside the recovery description, so a future reader meets the limit where the mechanism is explained rather than only in the plan
- [X] T085 Mark the disposable-board test in `tests/integration/test_card_to_issue.py` as skipped without real credentials, for the reason `docs/roadmap.md` already records about CI's ceiling, and make the skip message say what it would have verified
- [X] T086 Run `ruff` and the type checker clean across `src/robot_army/intake.py`, `src/robot_army/cardstates.py`, `src/robot_army/boundaries/trello.py`, and every file this milestone touched
- [X] T087 Run the full suite under `tests/` and confirm it passes, which the constitution makes the completion gate, explicitly including every test milestones 001 and 002 already had — an unchanged GitHub path is FR-045, and the only way to know is that their tests still pass untouched
- [ ] T088 Work through all nine scenarios in [quickstart.md](quickstart.md) against a disposable board, including scenario 5's kill matrix and scenario 7's shared-board refusal, and record what was actually observed — the roadmap is explicit that CI raises the floor and does not replace this round

  **Not done, and it cannot be automated away.** This task needs a real disposable Trello
  board, real Trello and GitHub credentials, and a running kitty instance, none of which
  exist in the implementation environment. It is the round the roadmap says CI raises the
  floor for rather than replaces, and FR-042 makes it the gate on believing the rest.

  What *was* verified against a real process (not fakes), so the manual round can start from
  a known point rather than from nothing:

  | Scenario | Verified locally | Still needs the board |
  |---|---|---|
  | 1 — unconfigured touches no board | `cards` exits `3` before any board client exists; `wire()` returns `None` for both board seams; no `board` job is registered | The full `run --once` with `grep -c trello` = 0 |
  | 2 — card to issue, gate intact | Against fakes end to end, including the labelled-issue half | A real card, a real issue, a real comment |
  | 3 — held, one comment, self-heals | Against fakes, including 20 consecutive polls producing one comment | Real `dateLastActivity` semantics, which is what R9 is actually about |
  | 4 — the board tells the truth | Against fakes through `dispatch` and `reconcile` | A real session and a real close |
  | 5 — one card one issue under a knife | Each of the three seams, reproduced as state; 100 polls across a simulated restart | A real `kill -9`, which is the point of the scenario |
  | 6 — nothing written below `live` | Against fakes at all three simulated levels | — |
  | 7 — a shared board stops ingestion | Every check failing individually, and extra members *not* failing | A real board flipped to public |
  | 8 — no credential in any record | **Yes, for real**: a real `TrelloCardReader` against a closed port, then `grep -r` over every byte of state — key and token absent, records carry method and path only | The same grep after a real 401 |
  | 9 — unreachable is not empty | **Yes, for real**: `doctor` exits `4`, the cause is recorded, `poll_state` backs off, an anomaly is raised at the threshold | Three real ticks against a black hole |

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks every user story**
- **US1 (Phase 3)**: depends on Foundational only
- **US2 (Phase 4)**: depends on Foundational; T046's resolution path reuses US1's creation path, so US2 is best done after US1 rather than beside it
- **US3 (Phase 5)**: depends on Foundational; needs a linked card to move, which US1 produces
- **US4 (Phase 6)**: depends on US1's four-step sequence existing — it hardens that sequence rather than adding one
- **Polish (Phase 7)**: depends on whichever stories were taken

### Within each story

- Models before accessors, accessors before services, services before surfaces
- Tests sit beside the code they cover; the constitution forbids mandating test-first
- A story is finished when its checkpoint holds, not when its last task is ticked

### Parallel opportunities

- **Phase 1**: T004 alongside T002/T003
- **Phase 2**: T006 and T008 alongside T005; T017, T018, T020, T024, T028, T029 are each in their own file
- **Phase 3**: T031, T036, T039–T041 are separate test files from the implementation they cover
- **Phase 4**: T045, T057, T058, T059 alongside the implementation tasks
- **Phase 5**: T066–T068 alongside T060–T065
- **Phase 6**: T076–T080 alongside T069–T075
- **Phase 7**: T081, T082, T083 are three different documents

The honest caveat: with one maintainer these markers identify work that will not collide in the same
file, not work that will happen simultaneously.

---

## Parallel Example: User Story 1

```bash
# The implementation, in order — each depends on the last
Task: "T030 repository resolution in src/robot_army/intake.py"
Task: "T032 issue composition in src/robot_army/intake.py"
Task: "T034 the duplicate guard in src/robot_army/intake.py"
Task: "T035 the four-step creation in src/robot_army/intake.py"

# The tests, in three separate files, safe to interleave with the above
Task: "T031 tests/unit/test_repo_resolution.py"
Task: "T036 tests/unit/test_card_dedup.py"
Task: "T039 tests/integration/test_card_to_issue.py"
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 — Setup
2. Phase 2 — Foundational, ending at the checkpoint where cards are tracked and **nothing has been
   created anywhere**. This is a genuinely safe place to stop and look at the board.
3. Phase 3 — US1
4. **Stop and validate**: quickstart scenarios 1, 2, and 6.

At that point cards become issues and the human gate holds. Everything after this is about the system
being honest when things go wrong.

### Incremental delivery

1. Setup + Foundational → the board is visible, nothing is written
2. US1 → capture works (MVP)
3. US2 → ambiguous cards are safe and self-healing
4. US3 → the board stops lying about what is running
5. US4 → the invariant is proven under crash and database loss

US4 is last by priority but it is not optional: FR-042 makes it the gate on believing any of the
above. A dry run cannot demonstrate it, which is why T088 exists and why one test is allowed to skip
in CI.

---

## Notes

- `[P]` = different files, no dependency on a pending task
- `[Story]` maps a task to a spec.md user story for traceability
- Commit after each task or logical group; messages explain **why**, per the constitution
- Two hazards found during planning have tasks of their own and are easy to reintroduce if either is
  skipped: T013 (credentials must never enter a URL, because `audit.py` redacts by field name) and
  T044 (our own card comment changes `dateLastActivity`, which is the rescan trigger)
- The one requirement changed during planning is FR-020 — `needs_info` lives on the card, not the
  work item. `work_items` is untouched by this milestone, and any task that appears to need a column
  on it is a sign the design drifted
