---

description: "Task list for GitHub Project board ordering"
---

# Tasks: GitHub Project Board Ordering

**Input**: Design documents from `specs/20260902-182104-github-project-ordering/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included and **not optional**. The constitution's Development Workflow requires unit
tests for every new or changed unit of behaviour, and additionally requires failure and
interruption paths to be tested for code that parses external input — which is exactly what
`_graphql` and the board snapshot builder do. T014 in particular is not a nicety: a GraphQL
failure arrives as HTTP 200 and, untested, degrades into a silently empty board.

**Organization**: Grouped by the spec's three user stories.

- **[US1]** the order follows the board, and a parked card is held
- **[US2]** the board is found without being described
- **[US3]** knowing why the queue is in the order it is in

The split that makes these independent: **US1 works from an explicitly configured project and
column**; US2 adds discovery on top. So US1 is a complete, shippable increment on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)

## Path Conventions

Single project: `src/robot_army/`, `tests/unit/`, `tests/integration/` at the repository root.

---

## Phase 1: Setup

**Purpose**: Nothing to initialise. The project, its dependencies, and its lint and test
configuration already exist, and this feature adds no dependency (plan.md, Technical Context).

- [X] T001 Confirm the baseline is green before changing anything: `uv run pytest` and `uv run ruff check` both pass on the current branch

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The schema, the configuration surface, the value types, and the one GraphQL helper
every story depends on. No product behaviour changes in this phase — after it, dispatch order
is still exactly what it is today.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 Add `SCHEMA_009_SQL`, `_migration_009`, and the `MIGRATIONS` entry in `src/robot_army/migrations.py` — two nullable `work_items` columns and the `repo_projects` table exactly as data-model.md specifies, carrying the four-states comment and the no-backfill rationale
- [X] T003 [P] Test migration 009 in `tests/unit/test_migrations.py`: it applies, is idempotent, advances `user_version` to 9, backfills nothing, and an interrupted run re-applies whole
- [X] T004 Add `WorkItem.board_column: str | None` and `WorkItem.board_position: int | None`, the `RepoProject` row dataclass, and its `ROW_TYPES` entry in `src/robot_army/models.py`
- [X] T005 Add `get_repo_project`, `save_repo_project` (single upsert), `list_repo_projects`, and the board-facts writer in `src/robot_army/db.py` — `get_repo_project` returns a default-constructed row rather than `None` when absent, following `get_poll_state`
- [X] T006 [P] Test the new accessors in `tests/unit/test_db_repo_projects.py`: default-on-absent, upsert replaces rather than duplicates, and the board-facts writer clears `board_column`/`board_position` for items the snapshot no longer mentions
- [X] T007 [P] Record in `tests/unit/test_db_scope.py` why `list_repo_projects` is exempt from the `include_simulated` rule — `repo_projects` has no `dry_run` column and one row per repository, so the rule that guards work-item listings does not apply
- [X] T008 Add `[dispatch] project_ordering`, `[repos.*] project_ordering`, `project`, `project_column`, the `RECOGNISED_DISPATCH_COLUMNS` constant, `Config.effective_project_ordering`, and the `_KNOWN_KEYS`/`_REPO_KEYS` entries in `src/robot_army/config.py` per contracts/config.md
- [X] T009 [P] Test the four keys in `tests/unit/test_config.py`: defaults, per-repo override and its `explicit` flag, a misspelled key in `[dispatch]` and in `[repos.*]` both refused as problems, a non-bool `project_ordering` refused, and `project` accepted as both a number and a board URL
- [X] T010 Carry `project_ordering`, `project`, and `project_column` through `repos.resolve` in `src/robot_army/repos.py`
- [X] T011 [P] Test in `tests/unit/test_repos.py` that `resolve` carries the three fields rather than dropping them, and that the onboarding record still wins `path` only
- [X] T012 Add `BoardEntry`, `BoardSnapshot`, and `ProjectResolution` frozen slotted dataclasses, the two `IssueSourceReader` protocol methods, and the `__all__` entries in `src/robot_army/boundaries/__init__.py` — `ranked` and `elsewhere` stay separate fields per data-model.md
- [X] T013 Add `GitHubReader._graphql(document, variables)` in `src/robot_army/boundaries/github.py`: POST to `/graphql` relative to `api_base`, raise `TransportError` naming `errors[0].type` and message, and record `github.project.partial` before raising when a response carries both data and errors
- [X] T014 [P] Test `_graphql`'s failure shapes in `tests/unit/test_github_project.py` using `httpx.MockTransport`: **HTTP 200 with an `errors` array raises rather than returning an empty result** (`INSUFFICIENT_SCOPES`, `FORBIDDEN`, a null path), a 401 REST-style body is passed through, and a clean response returns `data`
- [X] T015 Add `resolve_project` and `read_board` to `FakeIssueReader` in `tests/conftest.py` with the call-recorder convention every other method follows
- [X] T016 [P] Confirm `tests/unit/test_effects.py` still passes unchanged: `GitHubReader` satisfies the widened protocol, and `github.py` still holds exactly one `Simulated*` name

**Checkpoint**: schema, config, value types and the GraphQL helper exist. Behaviour is unchanged.

---

## Phase 3: User Story 1 - The order follows the board (Priority: P1) 🎯 MVP

**Goal**: With a project and column named explicitly in configuration, a repository's ready
items dispatch in the board's order, an issue parked in another column is held, and an issue
absent from the board still dispatches after everything the board ranked.

**Independent Test**: Set `project` and `project_column` for one repository, place three
labelled issues in the dispatch column in an order that differs from their creation order,
and confirm `robot-army status` and the dispatcher both present them in board order. Move one
card to `Backlog` and confirm it is held with a reason naming that column.

- [X] T017 [US1] Implement `read_board` in `src/robot_army/boundaries/github.py` per contracts/project-source.md: the items half of the document, `orderBy: {field: POSITION, direction: ASC}` passed explicitly, `pageInfo` paging bounded at 20 pages that **raises rather than truncating**, and the five-step filter (non-`ISSUE` types, null `content`, foreign repository, null Status, case- and space-insensitive column match)
- [X] T018 [P] [US1] Test `read_board` in `tests/unit/test_github_project.py`: draft issues, pull requests and `REDACTED` items contribute nothing; an item from another repository is ignored; a null Status is *parked*, not dispatchable; ranks are dense per repository and 1-based; order survives assembly across two pages; exceeding the page bound raises
- [X] T019 [US1] Implement the **configured** branch of `resolve_project` in `src/robot_army/boundaries/github.py`: a number resolved against the repository's owner, a `github.com/{users,orgs}/…/projects/N` URL parsed for owner type and number, and a configured column matched against the board's Status options
- [X] T020 [US1] Add the per-repository board pass to `poll_repo` in `src/robot_army/poll.py`, **after** the per-issue loop and **before** the final `save_poll_state`, so items discovered in the same pass get their board facts immediately: one transaction covering the `work_items` updates and the `repo_projects` upsert, `consecutive_failures`/`min(2 ** n, 900)` backoff mirroring the existing failure path, and the `poll.board` / `poll.board.fallback` records
- [X] T021 [P] [US1] Test the board pass in `tests/unit/test_poll_board.py`: facts written for the repository's items; items no longer on the board have their facts cleared; a failed read leaves the previous snapshot in force and records the fallback; a repository in backoff is skipped and the skip is recorded; the write is one transaction so a raise mid-pass leaves the previous snapshot whole
- [X] T022 [US1] Add `board_key` and the within-repository permutation to `ordering.plan` in `src/robot_army/ordering.py` — the existing sort runs first and unchanged, then each governed repository's items are reassigned to the slots that sort gave them; resolve `db.list_repo_projects` once per plan alongside `repos.resolved_all`
- [X] T023 [US1] Add `HoldReason.OFF_COLUMN` between `NOT_ONBOARDED` and `PREPARATION_FAILED` in `src/robot_army/ordering.py`, extend the enum docstring with R11's argument for that rank, and add the `_hold_for` clause with its three conditions and its one-sentence detail naming both columns and both ways out
- [X] T024 [P] [US1] Test ordering in `tests/unit/test_ordering.py`: board order is followed; **the slots a repository occupies are unchanged under both `oldest-first` and `repo-priority`**; off-board items sort after ranked ones; `board_key` is total so two plans of unchanged state are identical; `OFF_COLUMN` fires only when the three conditions hold; **nothing is held while `last_read_at` is NULL**; a `board_position` of NULL never behaves as 0
- [X] T025 [US1] Add an end-to-end case to `tests/integration/test_dispatch_capacity.py`: a board-ordered repository dispatches its top card first, and a parked card is never selected

**Checkpoint**: US1 is complete and shippable. A configured board governs dispatch order.

---

## Phase 4: User Story 2 - The board is found without being described (Priority: P2)

**Goal**: A repository with one linked project and one recognised column is ordered by it with
no configuration at all; anything ambiguous is reported rather than guessed.

**Independent Test**: Point the system at a repository with one linked Kanban-template project
and confirm the board governs with an empty `[repos.*]` section. Then make the answer ambiguous
— link a second project — and confirm it declines to guess, says why, and still dispatches.

- [X] T026 [US2] Implement the **discovery** branch of `resolve_project` in `src/robot_army/boundaries/github.py`: the `repository.projectsV2` half of the document, exactly-one-candidate resolution, and the `github.project.discover` record carrying candidates, the choice, and each half's source
- [X] T027 [US2] Implement automatic column selection in `src/robot_army/boundaries/github.py`: `field(name: "Status")` options matched case- and space-insensitively against `RECOGNISED_DISPATCH_COLUMNS`, resolving on exactly one match and populating `ProjectResolution.reason` and `.candidates` on zero or more than one
- [X] T028 [P] [US2] Test discovery in `tests/unit/test_github_project.py`: one linked project resolves; two are ambiguous and name both; zero is reported; a board with only `Ready` resolves and one with only `Todo` resolves; a board with both is ambiguous; a board with neither is reported; a configured project or column absent from the board is reported naming what was asked for and what the board offers
- [X] T029 [US2] Persist the resolution in the board pass in `src/robot_army/poll.py`: `project_*`/`column_*` and their sources on success, `unresolved_reason` and a NULL `resolved_at` on failure, combining discovery and the board read into one request once a project id is known
- [X] T030 [P] [US2] Test the automatic behaviour in `tests/unit/test_poll_board.py`: a cleanly resolving repository is governed with no configuration; `project_ordering = false` consults no project and holds nothing; a repository that becomes ambiguous keeps its row, gains an `unresolved_reason`, and falls back to the configured order rather than stalling

**Checkpoint**: US1 and US2 both work independently.

---

## Phase 5: User Story 3 - Knowing why the queue is in the order it is in (Priority: P3)

**Goal**: Every repository's ordering state — board, column, how each was decided, when it was
last read, and why it is not governed — is answerable from the terminal and the web without
reading the log, and with the board unreachable.

**Independent Test**: With one repository governed by a board, one ambiguous, and one with no
board at all, confirm each reports its own state from `robot-army status` and from `/queue`.

- [X] T031 [US3] Add the `projects` list to `operations.status` in `src/robot_army/operations.py`: one row per repository with project, column, each one's source, `last_read_at` and its age, `unresolved_reason`, and how many of its items are held off-column — built from `db.list_repo_projects` plus config, with no network call
- [X] T032 [US3] Add `project_ordering` and its `explicit` flag to `_repo_settings` in `src/robot_army/operations.py`, beside `cap`/`cap_explicit`, and render them in `robot-army capacity`
- [X] T033 [US3] Add a `view_sort_conflicts` read in `src/robot_army/boundaries/github.py`: the project's board views, their `sortByFields`, and whether any card in the dispatch column has a value for a sort field — the precise condition under which the screen and the dispatch order disagree (R2)
- [X] T034 [US3] Add the `project: *` checks to `operations.doctor` in `src/robot_army/operations.py` per contracts/config.md — token, project, column, view sort, freshness — gated on a project being configured or discovered, following the existing board block's shape. The token check distinguishes `INSUFFICIENT_SCOPES`, `FORBIDDEN`, and an empty `x-oauth-scopes` (a **fine-grained** token, which cannot read a user-owned board at all) and says which the author is holding
- [X] T035 [P] [US3] Test `status` and `capacity` in `tests/unit/test_status_projects.py`: a governed, an unresolved and an unconfigured repository each report their own state; the off-column count is present; **nothing contacts GitHub**
- [X] T036 [P] [US3] Test the doctor checks in `tests/unit/test_doctor_projects.py`: each failure shape reports its own message; a fine-grained token is named as such rather than reported generically; a view sort whose field has **no** values in the dispatch column produces no warning
- [X] T037 [US3] Render the new state in `src/robot_army/web/pages.py` `queue_view`: the `off_column` reason through the existing `hold_detail` cell, an off-column count in the ready heading (FR-030), and a staleness note when a repository's last board read failed — all mirrored into the view's `data` dict so `?json` agrees
- [X] T038 [P] [US3] Test the queue view in `tests/unit/test_web_views.py`: the reason renders, the count appears, the staleness note appears only when a read failed, and the JSON body carries the same facts
- [X] T039 [US3] Add a case to `tests/integration/test_web_end_to_end.py`: `/queue` renders correctly with an issue reader that raises on **any** call, proving FR-005 — rendering makes no network request

**Checkpoint**: all three stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: The documentation this feature owes, and the validation that it is true. The three
documentation tasks are deliverables, not tidying — plan.md's Principle V check names them, and
T042 carries a Principle III obligation: an enumerated gap justified only in a plan is not a
documented exception.

- [X] T040 [P] Update `README.md`: the **classic** PAT with `read:project` requirement beside the existing `ROBOT_ARMY_GITHUB_TOKEN` line, which today shows no scope guidance at all; that a fine-grained token cannot read a user-owned board; the four config keys beside the existing `[dispatch]` block; and a board-ordering section shaped like "Working a repository serially" covering what governs the order, what a parked card does, and how to turn it off
- [X] T041 [P] Update `docs/state.md`: a `repo_projects` section and a `work_items` board-columns section in the shape the `cards` and spec-kit-columns sections already use, including the four distinguishable states and the "Interrupted at X → result" rows for the board pass
- [X] T042 [P] Update `docs/logging.md`: a "## The issue #48 actions" section for `github.project.discover`, `github.project.read`, `github.project.partial`, `poll.board` and `poll.board.fallback`, and the enumerated Principle III gap — the board's item sequence is not logged on every read — added to "What is deliberately not logged" with its justification
- [ ] T043 Walk `quickstart.md` end to end against a real board, including the failure scenarios in steps 6 and 7
- [X] T044 Run `uv run pytest` and `uv run ruff check`; both must pass before the feature is complete (constitution, Development Workflow)
- [X] T045 Append a "Post-implementation reconciliation" section to `plan.md` recording where the built code differs from what this plan promised, and why — following the precedent set by the previous milestone

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001)**: no dependencies
- **Foundational (T002–T016)**: blocks every user story. Nothing here changes behaviour
- **US1 (T017–T025)**: depends on Foundational only
- **US2 (T026–T030)**: depends on Foundational. Independent of US1 in principle; in practice
  T029 touches the same function as T020, so if both are in flight, land T020 first
- **US3 (T031–T039)**: depends on Foundational. Reads state US1 and US2 write, so it is
  testable in isolation with fixture rows but only *useful* once one of them has landed
- **Polish (T040–T045)**: depends on the stories being complete

### Within Foundational

- T002 → T004 → T005 → T006 (schema before models before accessors before their tests)
- T008 → T009, T008 → T010 → T011
- T012 → T013 → T014, T012 → T015

### Within US1

- T017 → T018; T017 and T019 → T020 → T021
- T022 → T023 → T024 (the permutation before the hold, both before their tests)
- T020 and T023 → T025

### Parallel Opportunities

- **Foundational**: T003, T006, T007, T009, T011, T014 and T016 are all `[P]` — different
  files, each depending only on its own implementation task. T008/T010 (config and repos) can
  proceed alongside T012/T013 (boundaries) by different hands entirely
- **US1**: T018, T021 and T024 are `[P]` once their implementations land
- **US2**: T028 and T030 are `[P]`
- **US3**: T035, T036 and T038 are `[P]`. T031/T032/T034 all touch `operations.py` and are
  **not** parallel with one another
- **Polish**: T040, T041 and T042 are three different files and fully parallel

---

## Parallel Example: Foundational

```bash
# Once T002, T005, T008, T010 and T013 have landed, their tests run together:
Task: "Test migration 009 in tests/unit/test_migrations.py"
Task: "Test the new accessors in tests/unit/test_db_repo_projects.py"
Task: "Test the four keys in tests/unit/test_config.py"
Task: "Test resolve carries the three fields in tests/unit/test_repos.py"
Task: "Test _graphql's failure shapes in tests/unit/test_github_project.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. T001 — confirm the baseline is green
2. T002–T016 — Foundational. **Behaviour is unchanged at this checkpoint**, which is the point:
   the schema and configuration land without altering a single dispatch decision
3. T017–T025 — US1
4. **STOP and VALIDATE**: quickstart steps 1–4 against a real board with `project` and
   `project_column` set explicitly

That is a genuinely useful system: the author names the board once per repository and the
queue follows it. Everything after this removes typing or adds visibility.

### Incremental Delivery

1. Foundational → nothing changes
2. **US1** → a configured board governs dispatch order (MVP)
3. **US2** → the board is found without being named
4. **US3** → the state is explainable from the terminal and the web
5. Polish → the documentation the feature owes

### Risk Notes

- **T014 is the highest-value test in this list.** A GraphQL failure is an HTTP 200 with an
  `errors` array, invisible to `_request`, and untested it degrades into an empty board that
  looks exactly like a board with nothing in it. Write it before `read_board` exists
- **T034's token check is worth pulling forward** if the author has not yet confirmed their
  token can read projects, even though it belongs to US3 — every other task assumes a token
  that works, and quickstart step 0 exists for the same reason
- **T024's slot-preservation assertion is the one that pins FR-002.** If the permutation is
  ever replaced with a sort key, that test is what will catch it changing `repo-priority`'s
  meaning

---

## Notes

- `[P]` tasks touch different files and depend on no incomplete task
- Every task names the file it changes; none is a research task in disguise
- Commit after each task or logical group; messages explain why, not what
- Stop at any checkpoint to validate a story independently
