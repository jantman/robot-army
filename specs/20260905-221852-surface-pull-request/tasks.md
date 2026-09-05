---

description: "Task list for surfacing a work item's pull request in the web UI"
---

# Tasks: Surface the pull request in the web UI

**Input**: Design documents from `specs/20260905-221852-surface-pull-request/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md),
[research.md](./research.md), [data-model.md](./data-model.md),
[contracts/pull-request-discovery.md](./contracts/pull-request-discovery.md)

**Tests**: Required. The constitution's Development Workflow makes unit tests mandatory for
every new or changed unit of behaviour, and requires failure- and interruption-path tests for
persistence and for code parsing external input — which the refresh pass is both of.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: The user story the task serves (US1, US2, US3)

## Path Conventions

Single package at the repository root: `src/robot_army/`, `tests/unit/`, `docs/guide/`.

---

## Phase 1: Setup

**Purpose**: Nothing to initialise. The project, its dependencies and its test layout all
exist, and this feature adds no dependency (plan.md, Technical Context).

- [X] T001 Confirm the baseline is green before touching anything: `uv run pytest`

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: The column, the model field, and the boundary read. Every user story reads what
these produce, so none of them can start until all three land.

**⚠️ Blocks Phases 3, 4 and 5.**

### Storage

- [X] T002 Add `SCHEMA_013_SQL` and `_migration_013` to `src/robot_army/migrations.py`, adding
      `pull_requests TEXT` and `pull_requests_at TEXT` to `work_items`, and append
      `_migration_013` to `MIGRATIONS`. The SQL comment must argue why the column is nullable
      rather than `NOT NULL DEFAULT '[]'` — `NULL` is *never looked up* and `'[]'` is *looked
      up, none found*, and collapsing them would report "no pull request" on the strength of
      never having asked. Follow `SCHEMA_012_SQL`'s comment as the model; no index (nothing
      queries by it), no backfill (data-model.md, R4)
- [X] T003 [P] Add `pull_requests: str | None = None` and `pull_requests_at: str | None = None`
      to `WorkItem` in `src/robot_army/models.py`, with a `pull_request_list` property
      mirroring `label_list` that returns `[]` for both `NULL` and unparseable JSON. Docstring
      the three-state distinction and point the reader at `pull_requests is None` for telling
      "never looked" from "none found", as `speckit_baseline` does
- [X] T004 [P] Add `tests/unit/test_migrations.py` coverage: migration 013 applied to a
      populated pre-013 database leaves existing rows readable with both columns `NULL`, and
      `SCHEMA_VERSION` is 13

### The boundary read

- [X] T005 In `src/robot_army/boundaries/github.py`, give `GitHubReader._graphql` a
      keyword-only `operation: str = "project"` used to build the partial-response audit action
      (`github.{operation}.partial`), leaving the board callers on the existing
      `github.project.partial` name unchanged (R2). Docstring why: a pull-request failure
      logged as a project failure answers the wrong question
- [X] T006 In `src/robot_army/boundaries/github.py`, add the `_PULL_REQUESTS` GraphQL document
      exactly as contracts C1 gives it, and replace `open_pr_for_branch` with
      `pull_requests_for(repo_key, issue_number, branch) -> list[PullRequest]` implementing C1's
      five construction steps in order: both node lists, drop nodes missing `number` or `url`,
      lower-case `state`, de-duplicate on `number` keeping the first, sort by `number`. Call
      `_graphql(..., operation="pull_requests")`. Comment why `includeClosedPrs: true` and the
      explicit `states:` list are both load-bearing — without either, a merged pull request
      disappears
- [X] T007 Replace `open_pr_for_branch` with `pull_requests_for` in the `IssueSourceReader`
      protocol in `src/robot_army/boundaries/__init__.py`, and update the `PullRequest`
      docstring to state that `state` is `open`/`merged`/`closed`, normalised at the boundary
      so nothing above it sees GitHub's upper-case enum (data-model.md)
- [X] T008 Replace `open_pr_for_branch` with `pull_requests_for` on the fake reader in
      `tests/conftest.py`, defaulting to returning `[]`
- [X] T009 Add `tests/unit/test_pull_requests.py` covering C1: a pull request found by the
      branch route only; by the issue route only; by both, appearing once; `MERGED` and
      `CLOSED` normalised to lower case; a node missing `url` dropped; the result sorted by
      number; and a transport failure raising `TransportError` rather than returning `[]`

**Checkpoint**: the read works and is stored-shaped, but nothing calls it and nothing shows it.

---

## Phase 3: User Story 1 — See the pull request on a work item's page (P1) 🎯 MVP

**Goal**: An item's own page names every pull request it has, distinguishes "none" from "not
checked", and stays honest when GitHub cannot be reached.

**Independent test**: Give a work item a branch and a fake reader returning one open pull
request, run one reconcile pass, and load `/item/<id>`. The pull request appears with a working
link. Re-run with the reader raising, and the stored answer survives with its age shown.

### The refresh pass

- [X] T010 [US1] Add `_refresh_pull_requests(conn, *, boundaries, audit)` to
      `src/robot_army/reconcile.py` implementing contracts C2: the five candidate rules in
      order, and a returned count of items whose set changed. Docstring why the candidate rule needs no interval and no cap —
      it terminates itself once every stored pull request is `merged` or `closed` (R4)
- [X] T011 [US1] Implement C3's write inside `_refresh_pull_requests`: serialise with
      `json.dumps(..., separators=(",", ":"))`; when the text equals what is stored, write it
      back with a fresh `pull_requests_at` and record nothing; when it differs, in one
      `db.transaction`
      write the `work_item.pull_requests` audit record *then* `db.record_pull_requests`,
      following `speckit.record_phase`'s order. Catch
      `TransportError` only, log `reconcile.pull_requests_check` at `error`, and write neither
      column
- [X] T012 [US1] Call `_refresh_pull_requests` from `reconcile.reconcile()` **before**
      `_resolve_closed_issues`, and add `pull_request_changes` to `ReconcileResult` and to
      `summary()`. Comment why the position is load-bearing: the ordinary ending is merge →
      issue closes → item done, so refreshing first records `merged` instead of freezing at
      `open` (R4)

### Reading it back

- [X] T013 [US1] Add `pull_request_view(item) -> dict` to `src/robot_army/operations.py`
      returning `pull_requests`, `pull_requests_at` and `pull_requests_known` per contracts C4.
      No network, no cache — it reads the row. Docstring that `pull_requests_known` is the field
      FR-016 turns on and that no caller may infer the three states from the list alone
- [X] T014 [US1] In `src/robot_army/operations.py`, remove the `open_pr_for_branch` call and the
      `open_pull_request` key from `remote_resume_signals`, leaving the live `issue_closed`
      check and its cache alone; merge `pull_request_view` into `resume_signals`. Docstring why
      the removal is a correctness change and not just a speed one: one source of truth that
      cannot disagree with itself, at identical freshness (R5, C5)

### Rendering it

- [X] T015 [US1] Add a `pull request` row to the `<dl>` in `pages.item_view`
      (`src/robot_army/web/pages.py`), immediately after `branch`, via a new `_pull_requests_cell`
      helper: every known pull request as `#144 (merged)` linked through `github_link`, `none`
      when known and empty, `not checked` in the `empty` class when not known, and a
      `class="meta"` line giving `pull_requests_at` through `when()` (C4)
- [X] T016 [US1] Carry `pull_requests`, `pull_requests_at` and `pull_requests_known` in
      `item_view`'s `View.data` under the same names the HTML renders, so the JSON and the HTML
      cannot drift (FR-017)

### Tests

- [X] T017 [P] [US1] Add `tests/unit/test_reconcile_pull_requests.py` covering C2's candidate
      rules: a simulated item and a branchless item cause no boundary call at all; `active`,
      `awaiting_review` and `interrupted` items are refreshed; a `done` item whose stored pull
      request is `open` **is** refreshed; a `done` item whose pull requests are all
      `merged`/`closed` is not; and two items sharing a repository, issue and branch cost one
      lookup
- [X] T018 [P] [US1] Extend `tests/unit/test_reconcile_pull_requests.py` with C3's failure and
      interruption paths: a `TransportError` leaves both columns byte-identical, advances
      nothing, and writes `reconcile.pull_requests_check` at `error`; a pass in which the set is
      unchanged writes no `work_item.pull_requests` record but does advance `pull_requests_at`;
      a change writes the record and the columns in one transaction, so a failure inside it
      leaves neither; and a pass abandoned partway leaves earlier items updated and later items
      untouched
- [X] T019 [P] [US1] Add `pull_request` cases to `tests/unit/test_web_pages.py` asserting all
      three rendering states on `/item/<id>` — `not checked`, `none`, and a linked `#144
      (merged)` — plus the same three keys present in the view's JSON payload
- [X] T020 [US1] Update `tests/unit/test_resume_signals.py` for T014: `remote_resume_signals`
      no longer returns `open_pull_request` and makes no pull-request call, while
      `resume_signals` carries the three stored keys

**Checkpoint**: US1 is independently shippable. The pull request is discovered, stored, and
visible where the "what did this produce?" question is asked.

---

## Phase 4: User Story 2 — Spot pull requests across the lists (P2)

**Goal**: A pull request is visible at a glance in the listings, without opening each item.

**Independent test**: With one active item that has a pull request and one that does not, load
`/active` and confirm exactly one row carries a link and the other a plain placeholder.

- [X] T021 [US2] Add a compact `_pull_request_badge` helper to `src/robot_army/web/pages.py`
      per contracts C4: the **last** pull request by number as a link labelled `#144`, plus
      ` +N` when more are known, `—` when known and empty, `?` in the `empty` class when not
      known. Comment why "last by number" is FR-015's *one of them* — numbers are issued in the
      order pull requests were opened, so the highest is the current attempt
- [X] T022 [US2] Add a `PR` column to `pages.active_view` after `spec-kit`, sourcing it from
      `pull_request_view` on each row, and carry the three keys in each row of `View.data`
- [X] T023 [US2] Leave `/queue` without a column, and say so in a comment in `pages.queue_view`:
      `ready` rows have never been dispatched and so have no branch, and an always-empty column
      on the busiest page is worse than a click (SC-007, R6)
- [X] T024 [P] [US2] Extend `tests/unit/test_web_pages.py`: `/active` renders a link for an item
      with a pull request, a placeholder for one without, `?` for one never checked, and `+N`
      for one with several; and `/queue` renders no pull-request column in any of its tables

**Checkpoint**: the listings answer "which of these produced something?" at a glance.

---

## Phase 5: User Story 3 — Trust what the page says (P3)

**Goal**: Every surface distinguishes the three states and shows how old its answer is, and no
page render needs GitHub.

**Independent test**: Render every view against a boundary whose every method raises. No page
fails, and each shows its stored answer with an age.

- [X] T025 [US3] Replace the `open PR` row in `pages._signals_cell` with a `pull requests` row
      rendered by `_pull_request_badge`, and its footnote with `pull requests confirmed <age>`
      or `pull requests never checked`. Keep the two existing footnotes — the checkout pair and
      the `issue_closed` pair are still reused on their own windows and still each carry their
      own age (FR-019)
- [X] T026 [US3] Update `pages._signal_row` to source the pull request from `pull_request_view`
      rather than from `remote_resume_signals`, and carry the three keys in the row it returns
- [X] T027 [P] [US3] Add an `unreachable` case to `tests/unit/test_web_pages.py`: render
      `/active`, `/queue`, `/interrupted` and `/item/<id>` with a boundary whose every method
      raises `TransportError`, and assert every page renders and none makes a pull-request call
      (SC-004, C5)
- [X] T028 [P] [US3] Extend `tests/unit/test_web_pages.py` for `/interrupted`: the three states
      render distinctly in the signals block, and the footnote reports the stored confirmation
      age rather than a cache age

**Checkpoint**: every surface is honest about what it knows and when it learned it.

---

## Phase 6: Documentation & polish

**Purpose**: CLAUDE.md's two rot-prone obligations, and the suite.

- [X] T029 [P] Update [`docs/guide/operating.md`](../../docs/guide/operating.md): the web
      interface's new `PR` column and item field, the three states and what each means, and the
      note that the resume-decision signals now read a stored answer rather than calling GitHub
      while the page renders
- [X] T030 [P] Update [`docs/guide/state.md`](../../docs/guide/state.md): `work_items` gains
      `pull_requests` and `pull_requests_at`, with the `NULL` / `'[]'` / populated table from
      data-model.md and the rule that a failed lookup advances neither
- [X] T031 [P] Update [`docs/guide/audit-log.md`](../../docs/guide/audit-log.md): the
      `work_item.pull_requests`, `reconcile.pull_requests_check` and
      `github.pull_requests.partial` actions in the existing table, and the enumerated
      Principle III omission — an unchanged pass writes no record — beside
      `_observe_speckit`'s, which it copies
- [X] T032 Confirm no configuration key was added, so `exampleconfig.py` and
      `share/config.example.toml` need no regeneration and
      `tests/unit/test_example_config_drift.py` stays green
- [X] T033 Run the full suite: `uv run pytest`. It must pass before the feature is complete
- [X] T034 Walk [quickstart.md](./quickstart.md) §5 once against real GitHub with `gh api
      graphql`, confirming the document in `github.py` is byte-for-byte the one that answers

---

## Dependencies

```
Phase 1  T001
   │
Phase 2  T002 ─┬─ T003 [P] ─┬─ T004 [P]
               └─ T005 ── T006 ── T007 ── T008 ── T009
   │  (blocks everything below)
   ▼
Phase 3 (US1)  T010 ── T011 ── T012 ── T013 ── T014 ── T015 ── T016
                                                          └─ T017 [P] T018 [P] T019 [P] T020
   │
   ├─────────────► Phase 4 (US2)  T021 ── T022 ── T023 ── T024 [P]
   │                                 │
   └─────────────► Phase 5 (US3)  T025 ── T026 ── T027 [P] T028 [P]
                                      (T025 needs T021's badge helper)
   ▼
Phase 6  T029 [P] T030 [P] T031 [P] ── T032 ── T033 ── T034
```

**Story independence**: US1 stands alone and is the MVP. US2 and US3 both build on US1's stored
answer and its `pull_request_view`; US3's T025 additionally reuses US2's badge helper, so US2
comes first. Neither US2 nor US3 changes anything US1 established.

## Parallel opportunities

- **Phase 2**: T003 and T004 run alongside the T005→T009 boundary chain — different files, no
  shared state.
- **Phase 3**: T017, T018 and T019 are three separate test files' worth of work and share
  nothing; T020 touches a fourth.
- **Phase 6**: T029, T030 and T031 are three independent guide pages.

## Implementation strategy

**MVP is Phase 2 + Phase 3.** That is the whole of the issue's ask for the surface where the
question is actually asked — the item's own page — with discovery, storage, honesty about the
three states, and the failure path all present. Phases 4 and 5 are reach and trust over a fact
already established.

Ship in phase order. Each checkpoint above is a working state with the suite green; nothing
between them leaves the interface claiming something it cannot back up.


---

## What implementation changed, and why

Two design decisions in the plan did not survive contact, and both were narrowed rather than
grown. Recorded here because the artefacts above have been corrected to match, and a reader
comparing them against the commit history should know which way the correction went.

**The per-pass cache was removed.** C2 originally cached lookups on `(repo_key, issue_number,
branch)`, copying `_resolve_closed_issues`. Writing the test that would prove it hit showed it
never can: `idx_work_items_identity` is unique on `(source, source_id, dry_run)` and
`source_id` is `repo#issue`, so two non-simulated work items cannot share an issue. A cache
that cannot hit is a claim no test can back, so it went, and
`test_each_candidate_costs_exactly_one_lookup` pins what is actually true instead.

**The write got its own db function.** The plan said `db.update_work_item_columns`, which
always stamps `updated_at`. This pass runs every 60 seconds for every live item, so that would
have pushed `updated_at` forward once a minute for every item in the system — turning a column
meaning "when this item last changed" into "when the daemon last looked". `db.record_pull_requests`
writes the two columns and nothing else, and `test_the_refresh_does_not_move_updated_at` holds
it there.

One thing the plan asserted that turned out to be only half true, now stated correctly in C3:
the audit record and the column write share a transaction, but the audit log is an append-only
**file**, so a rollback cannot unwrite the record. The surviving failure is a record for a
change that did not land — corrected by the next pass — which is the right way round, since the
alternative ordering risks a committed change with no record at all.


---

## What review found, and what changed

A `/code-review high` pass over the finished branch raised seven items. Six were real; all
seven were acted on. The two that changed the design are written up as R11 and R12 in
[research.md](./research.md), and the contract was corrected to match.

| # | Finding | Change |
|---|---|---|
| 1 | A terminal item with a stored `[]` was never re-checked, so a pull request opened after the item went `done` rendered as a confident `none` for ever | Third candidate clause: an empty set is re-checked while a session is still running (R12) |
| 2 | `show` printed the stored pull requests under "computed now, **never stored**", as a raw Python repr | A `pull req.` line in the item block; the three stored keys are excluded from the signals block |
| 3 | `headRefName` dropped the owner qualification `head=owner:branch` had, so a fork's branch of the same name could be shown as this item's pull request | `headRepositoryOwner` requested and filtered, branch route only (R11) |
| 4 | De-duplicating by number alone collided a linked pull request in another repository with this repository's | Keyed on URL; sorted by `(number, url)` |
| 5 | `pull_request_list` checked "is it a list" but not its elements, so `[144]` would `AttributeError` and abort a whole reconciliation pass | The guard reaches the elements |
| 6 | The candidate query materialised every historical item once a minute | The whole rule moved into `db.list_pull_request_candidates` (R13) |
| 7 | A comment about lower-casing `state` sat above the `url=` line | Moved |

Findings 1, 3 and 4 each have a test that fails without the fix. Finding 5's test asserts the
thing that actually matters — that one unreadable column does not take down the pass around it.
