---

description: "Task list for the retry author re-check (issue #119, RA-01)"
---

# Tasks: Retry Re-Verifies the Author

**Input**: Design documents from `specs/20260903-203351-retry-author-recheck/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: Required. Not by the template's default but by the constitution — "Every new or
changed unit of behavior MUST ship with unit tests", and, because this touches a state
machine and code parsing external input, "tests exercising their failure and interruption
paths, not only their success paths". Test-first is explicitly *not* mandatory here, so
tests are placed inside their story's phase rather than ahead of it; write them in whichever
order suits the change.

**Organization**: grouped by user story. US1 is the security fix and stands alone; US2, US3
and US4 each add something the spec asks for and each can be dropped without breaking US1.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different file, no dependency on an incomplete task
- **[Story]**: which user story the task serves

## Path Conventions

Single Python package: `src/robot_army/`, `tests/` at the repository root.

---

## Phase 1: Setup

**Purpose**: establish that what breaks later was broken by this change.

- [ ] T001 Record a green baseline: run `uv run pytest -q`, `uv run ruff check src tests`
      and `uv run mypy src` on `speckit/20260903-203351-retry-author-recheck` and note the
      test count, so a later failure is attributable rather than argued about

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the `work_items.author` column and everything that writes it at discovery.

**⚠️ Blocks US2 and US4.** US1 does not depend on this phase and can be implemented and
tested before it if that is the preferred order — the refusal in US1 reads the *live* issue,
never the column.

- [ ] T002 Add `SCHEMA_011_SQL` and `_migration_011` to `src/robot_army/migrations.py`,
      append `_migration_011` to `MIGRATIONS` (making `SCHEMA_VERSION` 11), with
      `ALTER TABLE work_items ADD COLUMN author TEXT` and a comment saying what `NULL`
      means and why there is no backfill, per [data-model.md](data-model.md) and
      [R7](research.md)
- [ ] T003 [P] Add `author: str | None = None` to `WorkItem` in `src/robot_army/models.py`,
      with a comment distinguishing "never recorded" from "no author"
- [ ] T004 Add an `author: str` keyword parameter to `db.insert_work_item` in
      `src/robot_army/db.py` and include the column in its `INSERT` statement (depends on
      T002, T003)
- [ ] T005 Pass `author=issue.author` from `poll.poll_repo`'s `db.insert_work_item` call in
      `src/robot_army/poll.py` (depends on T004)
- [ ] T006 Add `author: str = "jantman"` to `seed_item` in `tests/conftest.py` and pass it
      through to `db.insert_work_item`, defaulting to the login the `config` fixture already
      sets as `github.author` so existing dispatch tests keep testing what they were written
      to test ([R10](research.md)) (depends on T004)
- [ ] T007 [P] Add a migration test to `tests/unit/test_migrations.py`: `SCHEMA_VERSION` is
      11, the column exists after migrating, a row written by migration 010's schema
      survives with `author IS NULL`, and re-running `migrate` is a no-op (depends on T002)

**Checkpoint**: the column exists, the poller fills it, and nothing reads it yet.

---

## Phase 3: User Story 1 — Retry cannot smuggle someone else's issue into the queue (Priority: P1) 🎯 MVP

**Goal**: `retry` re-reads the issue and re-runs `poll.evaluate`; any failing eligibility
condition refuses the retry and leaves the item blocked with a current reason.

**Independent Test**: an item failed on the author condition is refused by
`robot-army retry <id>` and by `POST /item/<id>/retry`, stays `failed`, and the refusal
names the author condition.

- [ ] T008 [US1] In `operations.retry` (`src/robot_army/operations.py`), after
      `dispatch.check_gates` passes, read the issue via
      `ctx.boundaries.issue_reader.get_issue(item.repo_key, item.issue_number)`; refuse on
      `BoundaryError` with cause `issue_unreachable` and exit `EXIT_FAILED`, and refuse on a
      `None` return with cause `issue_absent` and the same exit code, using the wording in
      [contracts/retry.md](contracts/retry.md). Never fall back to the item's stored copy
      ([R3](research.md))
- [ ] T009 [US1] In the same function, call `poll.evaluate(issue, config=ctx.config,
      repo_key=item.repo_key, onboarded=True)` and refuse with `EXIT_PRECONDITION` when the
      verdict is ineligible, quoting `Eligibility.reason` verbatim as its own line and
      writing that reason to the item's `blocked_reason` (FR-005). Do not read
      `blocked_reason` to decide anything ([R2](research.md)) (depends on T008)
- [ ] T010 [US1] Add the audit records from
      [contracts/audit-records.md](contracts/audit-records.md) to `operations.retry`:
      `retry.blocked` for a refusal reached before the read, and `retry.evaluate` carrying
      `eligible`, `reason`, `author` and `refreshed` for every invocation that attempted one
      — on the allowed path as well as both refused ones (depends on T009)
- [ ] T011 [P] [US1] New `tests/unit/test_operations_retry.py` covering, one test each: the
      author condition refuses; a removed label refuses and reports the label rather than
      the stored reason; a closed issue refuses; an item that failed for a non-eligibility
      reason is still re-evaluated (FR-007); an unreachable source refuses without consulting
      stored content; an absent issue refuses; an eligible issue reaches `ready` with
      `failure_reason` and `blocked_reason` cleared; a refusal rewrites `blocked_reason` to
      the current reason; and the three audit records appear with the fields the contract
      names (depends on T010)
- [ ] T012 [P] [US1] Extend `tests/unit/test_web_actions.py`: an author-rejected item posted
      to `/item/<id>/retry` gets `409` with the author reason in the body and stays `failed`,
      proving both front ends share the refusal (SC-001) (depends on T010)

**Checkpoint**: the bypass is closed from both front ends. This is a shippable fix on its
own.

---

## Phase 4: User Story 2 — Retry dispatches the issue as it stands (Priority: P2)

**Goal**: the read US1 already performs also refreshes the item's stored content, on both
outcomes.

**Independent Test**: edit an issue's title, body and labels after discovery, retry the
item, and find the item carrying the new values — whether the retry was allowed or refused.

- [ ] T013 [US2] In `operations.retry`, immediately after a successful read and *before*
      the verdict is consulted, write `title`, `body`, `labels`
      (`states.dumps_labels(list(issue.labels))`) and `author` through
      `db.update_work_item_columns` in its own transaction, so the refused path refreshes
      too (FR-009) and an interruption between the refresh and the transition leaves the
      item blocked with accurate content rather than queued with stale content
      ([R5](research.md)) (depends on T008, and on T002 for the column)
- [ ] T014 [P] [US2] Add to `tests/unit/test_operations_retry.py`: a successful retry stores
      the freshly read title, body, labels and author; a *refused* retry stores them as well;
      and the stored `labels` round-trips through `WorkItem.label_list` to the issue's tuple
      (depends on T013)

**Checkpoint**: US1 and US2 both hold; an item returned to the queue carries content that
was read, not replayed.

---

## Phase 5: User Story 3 — The interface describes the check it actually performs (Priority: P2)

**Goal**: the web confirmation and the CLI help both say the issue is re-read and its
eligibility re-checked, author included.

**Independent Test**: read both strings; each names the re-read and the author.

- [ ] T015 [P] [US3] Replace the `retry` `ActionSpec` description in
      `src/robot_army/web/pages.py` with the wording in
      [contracts/retry.md](contracts/retry.md). Leave `confirm`, `item_states` and
      `legal_actions` untouched — the button still shows on author-rejected rows, and
      [contracts/retry.md](contracts/retry.md) says why hiding it would be worse
- [ ] T016 [P] [US3] Update the `retry` parser's `help` in `src/robot_army/cli.py` to say
      the same thing
- [ ] T017 [P] [US3] Add a test asserting both strings mention re-reading the issue and
      checking eligibility including the author — in `tests/unit/test_web_actions.py` for
      the `ActionSpec` and alongside the existing CLI parser tests for the help text
      (depends on T015, T016)

**Checkpoint**: a maintainer can tell a fixed build from a broken one by reading the
confirmation.

---

## Phase 6: User Story 4 — The dispatch path carries an author it actually read (Priority: P3)

**Goal**: `_dispatch_item` compares `item.author` against `config.github.author` and refuses
on mismatch or absence, instead of asserting the configured author into the `Issue` it
builds.

**Independent Test**: a `ready` item whose recorded author is foreign, and one whose author
is `NULL`, are each refused into `failed` with no worktree, branch or session created.

- [ ] T018 [US4] In `dispatch._dispatch_item` (`src/robot_army/dispatch.py`), after the
      `repos.resolve` refusal and the `DISPATCHING` transition but **outside** the
      `if not skip_gates:` block ([R8](research.md)), refuse via `_fail(..., blocked=True)`
      when `item.author is None` (naming `retry` as the recovery, FR-015) or when
      `item.author != config.github.author` (FR-014), returning `False` before any worktree
      work begins (depends on T003)
- [ ] T019 [US4] In the same file, replace `author=config.github.author` in the `Issue`
      constructed for the launch with `author=item.author`, and add the `dispatch.author`
      audit record from [contracts/audit-records.md](contracts/audit-records.md) carrying
      `recorded_author`, `configured_author` and `cause` (depends on T018)
- [ ] T020 [P] [US4] Add to `tests/integration/test_dispatch.py`: a foreign recorded author
      is refused, the item is `failed` with the author named in `blocked_reason`, and
      `version_control` was asked to create no worktree; a `NULL` author is refused with a
      reason naming `retry`; a matching author dispatches exactly as before; and the
      `dispatch.author` record carries both halves of the comparison (depends on T019, T006)

**Checkpoint**: two independent refusal points, and no line in the tree asserts an author it
did not read.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T021 [P] Update `docs/security-analysis.md`: mark RA-01 resolved in the High table and
      add a **Resolved** note to its section naming the commit's mechanism; add a note to
      RA-04 saying the retry path is closed and the poll-to-dispatch path is not, so the
      finding is not read as fully closed (FR-018)
- [ ] T022 [P] Add a `work_items.author` section to `docs/state.md` in the style of the
      existing spec-kit and board column sections: what the column holds, what `NULL` means,
      why there is no backfill, and a `sqlite3` line for reading it
- [ ] T023 [P] Update the `retry` mentions in `README.md` so the described behaviour matches
      the new one
- [ ] T024 Run `uv run pytest -q`, `uv run ruff check src tests` and `uv run mypy src`; all
      three must pass before the feature is complete (Development Workflow)
- [ ] T025 Walk [quickstart.md](quickstart.md) against a real installation, or record which
      scenarios were exercised only by the suite and why

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none
- **Foundational (Phase 2)**: blocks US2's author refresh (T013) and all of US4. Does **not**
  block US1
- **US1 (Phase 3)**: independent of Phase 2; the MVP
- **US2 (Phase 4)**: depends on T008 (the read) and T002 (the column)
- **US3 (Phase 5)**: depends on nothing but is only *true* once US1 lands, so ship it with or
  after US1, never before
- **US4 (Phase 6)**: depends on Phase 2
- **Polish (Phase 7)**: after every story that is being shipped

### Within Each Story

`operations.py` and `dispatch.py` each carry several tasks that touch the same function, so
those are strictly sequential. Tests are marked `[P]` because they live in different files
from the code they exercise.

### Parallel Opportunities

- T003 and T007 alongside T002's other dependents
- T011 and T012 together once T010 lands
- All of T015, T016 together (three different files), then T017
- T021, T022 and T023 together — three documents, no shared lines

## Parallel Example: User Story 1

```bash
# After T010, the two test tasks touch different files:
Task: "Unit tests for the six retry checks in tests/unit/test_operations_retry.py"
Task: "Web-path refusal test in tests/unit/test_web_actions.py"
```

---

## Implementation Strategy

### MVP (US1 only)

1. T001
2. Phase 3 — T008 through T012
3. **Stop and validate**: an author-rejected item cannot be retried from either front end
4. Ship. This alone closes RA-01

### Incremental delivery

1. MVP as above → the bypass is closed
2. Phase 2 + US2 → the queue stops replaying stale content
3. US3 → the interface stops describing a check that no longer works that way
4. US4 → a second, independent refusal point, and the fabricated author is gone
5. Phase 7 → the documents agree with the code

### Notes

- Single maintainer; the parallel markers describe what *may* be reordered, not a staffing
  plan
- Commit per story, message explaining why rather than what (Development Workflow)
- Every phase leaves the tree shippable — no story removes a control another story adds
