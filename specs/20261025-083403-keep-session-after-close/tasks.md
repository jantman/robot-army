---

description: "Task list for keeping a session open after its issue closes"
---

# Tasks: Keep a session open after its issue closes

**Input**: Design documents from `specs/20261025-083403-keep-session-after-close/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: required. Not by request — by the constitution's Development Workflow section and
`CLAUDE.md`: every new or changed unit of behaviour ships with unit tests, and persistence and
state machines also need failure- and interruption-path tests. Every behaviour task below is
preceded by the test that pins it; write the test, see it fail, then implement.

**Organization**: by user story, so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: US1, US2, US3, mapping to the stories in spec.md

**Anchors**: line numbers are as of commit `cbc5609` and drift as tasks land. Find the named
function first; the number is a hint.

---

## Phase 1: Setup

**Purpose**: establish the baseline this feature is measured against.

- [X] T001 Confirm the suite is green before any edit: `uv run pytest > <scratch>/baseline.txt` (redirect to the session scratchpad; read the summary line) — record any pre-existing failure in this file before continuing, so it is not mistaken for a regression
  - *Done: baseline 4287 passed, 1 skipped, no failures.*

No project initialisation, no dependency, no tooling change.

---

## Phase 2: Foundational (blocks every story)

**Purpose**: the `item_keeps` table, its model and its accessors. Every story reads a keep, so
none can start until these exist. Shape: [data-model.md](./data-model.md); reasoning: research R1.

### Tests

- [X] T002 [P] Add a migration-016 test to `tests/unit/test_migrations.py`, beside the existing hold-table test for migration 015/`item_holds`: after migrating a fresh database, `item_keeps` exists with columns `work_item_id INTEGER PRIMARY KEY`, `kept_at TEXT NOT NULL`, `kept_by TEXT NOT NULL`; a foreign key to `work_items(id)` with `ON DELETE CASCADE` (deleting the work item with `PRAGMA foreign_keys=ON` deletes the keep); and `SCHEMA_VERSION` equals 16. Also migrate a database already at 15 holding a work item and a hold, and assert both survive and `item_keeps` is empty (no backfill)
  - *Deviation: the hold tables are migration **010**, not 015 (015 is `poll_state.etag_request`); the 016 tests sit at the end of the file beside 015's. The existing `test_the_schema_version_derives_from_the_ladder_length` pinned `== 15` and now pins 16. A killed-016 interruption test was added.*
- [X] T003 [P] Create `tests/unit/test_keeps.py` with the db-layer tests, using the same fixtures `tests/unit/test_holds.py` uses (read its top ~60 lines for the `conn`/item factories):
  - `set_item_keep(conn, id, by="cli")` returns `(Keep, True)`; a second call with `by="web"` returns `(Keep, False)` carrying the **original** `kept_at` and `kept_by="cli"`
  - `clear_item_keep` returns the removed `Keep`, then `None` on a second call
  - `list_item_keeps` returns `{}` on an empty table and `{id: Keep}` after a set
  - `is_kept` is `True`/`False` accordingly
  - deleting the work item (via `db.purge_simulated` on a simulated item, which is the only deleting path) removes its keep
  - `count_kept_sessions_by_repo`: counts only `starting`/`running` session rows whose item is `done` **and** kept, grouped by `repo_key`; a kept `active` item's running row, an unkept `done` item's running row, and a kept `done` item's `exited` row are all excluded

### Implementation

- [X] T004 Add `SCHEMA_016_SQL` and `_migration_016` to `src/robot_army/migrations.py`, modelled on the `item_holds` DDL at `migrations.py:432-462` and `_migration_015` at line 804, and append `_migration_016` to `MIGRATIONS` (line 810; `SCHEMA_VERSION = len(MIGRATIONS)` at line 828 follows). DDL: `CREATE TABLE item_keeps (work_item_id INTEGER PRIMARY KEY REFERENCES work_items(id) ON DELETE CASCADE, kept_at TEXT NOT NULL, kept_by TEXT NOT NULL)`. Give the SQL constant a docstring-comment in the house style saying why it is a separate table and not a scope on `item_holds` (FR-012; research R1)
- [X] T005 [P] Add `@dataclass(frozen=True) class Keep: kept_at: str; kept_by: str` to `src/robot_army/models.py` beside `class Hold` (line 465), with a short why-docstring: presence is the fact; no expiry by design (clarification 2a)
- [X] T006 Add to `src/robot_army/db.py`, beside the hold accessors (`list_item_holds` line 1310 … `clear_repo_hold` line 1394): `list_item_keeps(conn) -> dict[int, Keep]`, `set_item_keep(conn, item_id, *, by) -> tuple[Keep, bool]` (`INSERT … ON CONFLICT DO NOTHING`, then read back), `clear_item_keep(conn, item_id) -> Keep | None`, `is_kept(conn, item_id) -> bool`, and `count_kept_sessions_by_repo(conn) -> dict[str, int]` (one joined query: sessions in `starting`/`running` ⨝ `work_items` with `state = 'done'` ⨝ `item_keeps`, grouped by `repo_key`; include simulated rows exactly as `capacity.snapshot` counts them). Do **not** reuse `_set_hold`/`_clear_hold` (lines 1334-1376): they would need `held_at`/`held_by` column names, which FR-012 forbids (research R1); say so in a one-line comment
- [X] T007 Run `uv run pytest tests/unit/test_migrations.py tests/unit/test_keeps.py` — T002 and T003 pass

**Checkpoint**: keeps can be stored and read. Stories can begin.

---

## Phase 3: User Story 1 — Merge, then verify in the same session (Priority: P1) 🎯 MVP

**Goal**: a kept item's session survives its issue closing — no retirement, no tab close, no
automatic worktree reclaim — until the keep is released or the session is exited; release
returns the item to today's rules unchanged.

**Independent Test**: keep an item, close its issue with a merged PR, run several reconcile
passes: worker, row, tab and worktree all survive; `unkeep` and the next pass retires it with
`signal: merged_pull_request`.

**Contracts**: [cli.md](./contracts/cli.md) (`keep`, `unkeep`), [web.md](./contracts/web.md)
(routes, controls), [audit.md](./contracts/audit.md) (`keep.item`, `unkeep.item`)

### Tests for User Story 1

- [X] T008 [US1] Add operation and CLI tests for `keep`/`unkeep` to `tests/unit/test_keeps.py`, following `tests/unit/test_holds.py`'s tests of `hold_item`/`unhold_item`:
  - `keep <id>` on `ready`, `active`, `failed`, and `done`-with-a-live-session-row all exit 0 and write the row with `kept_by="cli"`; output matches the two "Placed" rows of [cli.md](./contracts/cli.md) (the `done`+live wording vs. the "takes effect when the issue closes" wording)
  - a redundant `keep` exits 0 and says `was already kept, since <time> by <by>` with the original time; no second row
  - `keep` on an unknown id exits 1 with the `_no_such_item` message
  - `unkeep` of an unkept item exits 0 and says `was not kept; nothing to release`
  - `unkeep` of a kept `done` item with an open row prints the release line plus `Its session is retired on the next reconciliation pass that finds it idle.`, and its `unkeep.item` outcome record carries `open_sessions` = the open row's `session_id`s (FR-008)
  - audit: `keep.item` intent `detail` has `by`, `was_kept`, `state`, `live_session`; outcome has `kept_at`, `kept_by`, `placed` (contract [audit.md](./contracts/audit.md))
  - **(F)** monkeypatch `db.set_item_keep` to raise inside the operation: no row exists afterwards and the `keep.item` outcome record is `error` (interruption path)
  - **(F)** a keep written while no daemon runs (operation called directly on the db) is honoured by the next `reconcile` call — covered jointly with T009's first case; reference it here
  - `--json` on `keep` and `unkeep` emits the `data` shapes in [cli.md](./contracts/cli.md)
- [X] T009 [P] [US1] Add to `tests/unit/test_session_retirement.py`, reusing its existing merged/idle fixtures:
  - a kept, merged, idle `done` item is **not** retired across three passes, and **no** audit record of any action names its session on those passes (the C6 silence)
  - after `clear_item_keep`, the next pass retires it with `signal: merged_pull_request` and no extra wait (SC-002)
  - a kept, unmerged `done` item idle beyond `RETIRE_IDLE_SECONDS` is not retired
  - a keep on an `active` item changes nothing before `done`
  - **clarification 3a**: with dispatch paused and both an item hold and a repo hold on the item, and **no** keep, a merged idle `done` item **is** retired
  - an unkept item's retirement is unchanged (existing tests stay unmodified — SC-003)
- [X] T010 [P] [US1] Add to `tests/unit/test_window_closing.py`: a kept `done` item with an open `running` row keeps its marked window across passes; once that row is closed (simulate the session's exit record) with the keep still in place, the next pass closes the window (spec US1 scenario 3; research R4 — no code change, the test pins `_close_finished_windows`'s open-row guard at `reconcile.py:2006`)
- [X] T011 [P] [US1] Add to `tests/unit/test_cleanup.py`, with `on_issue_close = true`:
  - a kept `done` item with an open row is absent from `db.list_cleanup_candidates`, and a cleanup pass writes **no** `cleanup.considered` record for it
  - a kept `done` item with **no** open row is present and is reclaimed (an inert keep does not block disk — research R5)
  - `cleanup <id>` on a kept, live item still records `skipped` with the "still live" reason (FR-011)
- [X] T012 [P] [US1] Add to `tests/unit/test_web_actions.py`, following the existing `POST /item/<id>/hold` tests: `POST /item/<id>/keep` and `/unkeep` succeed, write/remove the row with `kept_by="web"`, and redirect with message `kept`/`released`; a cross-origin POST is refused and writes nothing; the item page for a keepable item renders a `keep` control, and a kept item renders `release`; each `/active` row renders the control
  - *Deviation: the release redirects with message `unkept`, not `released`. `released` is already the hold's banner (`web/html.py` BANNERS: "Hold released. The work dispatches on the next tick…"), and `test_web_routing.test_every_redirect_message_has_a_banner_to_render` requires a banner per message; reusing it would show a hold's sentence for a keep (FR-012). New `kept` and `unkept` banners were added.*

### Implementation for User Story 1

- [X] T013 [US1] Add `keep_item(ctx, item_id, *, by="cli") -> Result` and `unkeep_item(ctx, item_id, *, by="cli") -> Result` to `src/robot_army/operations.py`, beside `hold_item` (line 4377) and `unhold_item` (line 4435) and shaped like them: one `db.transaction`, wrapped in `ctx.audit.action("keep.item"/"unkeep.item", entity_type="work_item", entity_id=item_id, detail=…)`; unknown item → `_no_such_item`; messages and `data` exactly per [cli.md](./contracts/cli.md); `live_session` is "an open `starting`/`running` row exists for the item" (reuse `cleanup.live_sessions` or `db.list_sessions_for_item` filtered on state); `unkeep_item` collects `open_sessions` **before** clearing. Leave the FR-010 refusal for T034; until then a terminal item with no session is accepted. Docstrings say why: a keep starts and signals nothing, so it needs no daemon and no effect guard
- [X] T014 [US1] Add `keep` and `unkeep` subparsers to `src/robot_army/cli.py` beside `hold`/`unhold` (lines 344-362), each taking a positional integer `id` only (no `--repo`, no mutually exclusive group); add both names to the universal-flag tuple at ~line 393-402 so `--json` works; add dispatch entries beside `"unhold"` (~line 661) calling `operations.keep_item(ctx, args.id)` / `operations.unkeep_item(ctx, args.id)`
  - *Deviation: the positional is named `item_id`, matching every other per-item verb (`attach`, `abandon`, …), not `id`.*
- [X] T015 [US1] In `_retire_finished_sessions` in `src/robot_army/reconcile.py` (line 1150): read `keeps = db.list_item_keeps(conn)` once at the top; immediately after the `item.state is not WorkItemState.DONE` test (~line 1200) add `if item.id in keeps: continue`, **before** the pid, liveness and idleness reads. Extend the docstring's silence paragraph (lines 1184-1189) with one sentence: a kept item is the same "not yet", for an operator-chosen length of time, and the `keep.item`/`unkeep.item` pair reconstructs it. Do **not** touch `_retire_signal` (line 1115) (research R2)
- [X] T016 [US1] In `db.list_cleanup_candidates` (`src/robot_army/db.py:399`) add a clause excluding an item that has an `item_keeps` row **and** an open (`starting`/`running`) session row (`AND NOT EXISTS (… item_keeps k JOIN sessions s …)`); both conditions, never the keep alone. Extend its docstring with why: a keep can last days, and a `skipped` record every pass is the C6 shape; an inert keep must not retain a 499 MB worktree (research R5). Do not change `cleanup.eligible` or `clean_item` (`cleanup.py:124-182`); `cleanup <id>` does not use this list (`cleanup.py:549-553`)
- [X] T017 [US1] Add `_item_keep_action(keeping: bool)` to `src/robot_army/web/server.py` shaped like `_item_hold_action` (line 1165): through `_perform`, `require_item` and the operation inside the body, not effect-guarded, no daemon required, redirect to the referrer falling back to `/item/<id>`, messages `kept`/`released`. Register `POST ("item", "<id>", "keep")` with `terminal="keep"` and `POST ("item", "<id>", "unkeep")` with `terminal="unkeep"` beside the hold routes (lines 1410-1413)
- [X] T018 [US1] Add a `_item_keep_control(item_id, kept: bool)` to `src/robot_army/web/pages.py` modelled on `_item_hold_control` (line 613): a form button `keep`, or `release` with class `primary`, posting to the T017 routes. Render it on the item page (item view near line 1926, beside the hold control in the action bar) and on each row of `active_view` (line 798). Pass the keep set in from `db.list_item_keeps` once per render, not per row
  - *Deviation: the item page has no hold control to sit beside (holds are on `/queue`), so the keep control sits directly after the action bar, outside it — the bar is the state machine's legal transitions and a keep is not one. On `/active` it is a new last column, `keep`.*
- [X] T019 [US1] Run `uv run pytest tests/unit/test_keeps.py tests/unit/test_session_retirement.py tests/unit/test_window_closing.py tests/unit/test_cleanup.py tests/unit/test_web_actions.py` — T008–T012 pass and no existing test in those files changed

**Checkpoint**: MVP. A kept session survives the merge; release restores today's behaviour.

---

## Phase 4: User Story 2 — See what is being kept, and why the machine is full (Priority: P2)

**Goal**: every keep is listable and releasable from CLI and web; capacity reasons name kept
sessions; a kept `done` session raises no `orphan_session`, and one raised before the keep is
retracted.

**Independent Test**: keep a session and finish its item: `keeps` and `/active` list it,
`status` shows `(1 kept)`, the repo-cap reason says `1 kept`, and `anomalies` is empty.

**Contracts**: [cli.md](./contracts/cli.md) (`keeps`), [web.md](./contracts/web.md) (`/active`
kept section), [capacity.md](./contracts/capacity.md), [audit.md](./contracts/audit.md)
(`anomaly.resolved` reason)

### Tests for User Story 2

- [X] T020 [US2] Add `keeps` listing tests to `tests/unit/test_keeps.py` (after T008; same file): columns `item, repo, issue, state, session, kept, by`; session column wording `live <session-id>` (open row), `none yet` (non-terminal, no row), `no live session` (terminal, no row); an empty table prints `nothing is kept`; `--json` matches the `data` shape in [cli.md](./contracts/cli.md) including `age_seconds` and `open_session_id`
- [X] T021 [P] [US2] Add to `tests/unit/test_capacity_reporting.py`: with one kept live `done` session among two of ours, `snapshot().breakdown` is `2 ours (1 kept), 0 other`, `kept == 1`, `kept_per_repo == {repo: 1}`, and `components` is unchanged and still sums to `total`; with no kept sessions every string is byte-identical to today's (SC-003); on the degraded `/proc` path (where `ours` is empty) no `kept` annotation appears
- [X] T022 [P] [US2] Add to `tests/unit/test_holds_ordering.py`: with a repository at its cap and one of its running sessions kept, the repo-cap hold detail reads `repository o/n: 1 of 1 sessions (configured), 1 kept`; the global-cap detail carries the breakdown with `(1 kept)`; with no kept session both strings are unchanged
- [X] T023 [P] [US2] Add `reclaim_stale_session` tests to `tests/unit/test_session_retirement.py` (if T009 is in progress, add after it — same file), next to the existing issue-#28 reclaim tests:
  - a kept `done` item with a live worker returns `"left"` and raises **no** anomaly
  - **(F)** a kept `done` item whose worker is dead returns `"reclaimed"` and the row closes (reboot case: no leaked slot)
  - a kept `abandoned` item with a live worker still returns `"reported"` and raises `orphan_session`
  - an unkept `done` item with a live worker is unchanged (`"reported"`)
- [X] T024 [P] [US2] Add to `tests/unit/test_anomaly_resolution.py`: an open `orphan_session` whose `detail.work_item_id` names a `done`, kept item with its session row still open is resolved on the next pass with an `anomaly.resolved` record carrying the reason `the work item is done and kept by the operator, so this session is accounted for`; the same anomaly for an unkept item stays open; one for a kept item that is not `done` stays open
- [X] T025 [P] [US2] Add to `tests/unit/test_web_actions.py` (after T012; same file): `/active` HTML always has the kept section, with `Nothing is kept.` when empty; it lists a kept `done` item with a live session, a kept `ready` item, and a kept terminal item with no session, each with a release control; `/active` JSON carries `"keeps": [...]` in the `keeps --json` row shape; the capacity pill text contains `(1 kept)`
  - *Note: the pill test points `capacity.snapshot` at a fake registry and `/proc` (monkeypatched partial), because a session only counts as `ours` with a live registry entry.*

### Implementation for User Story 2

- [X] T026 [US2] Add `list_keeps(ctx) -> Result` to `src/robot_army/operations.py` beside `list_holds` (line 4609), producing the table and `data` in [cli.md](./contracts/cli.md); add a sentence to its docstring citing `list_holds`' "nothing sweeps a hold" reasoning (`operations.py:4617-4620`) for why inert keeps are listed and never cleared (research R7). Add a `keeps` subparser to `src/robot_army/cli.py` beside `holds` (line 362), add `"keeps"` to `READ_COMMANDS` (line 36), and add the dispatch entry beside `"holds"` (~line 662)
  - *Note: the row builder (`_keep_rows`) and the session wording (`keep_session_label`) are shared with the `/active` kept section, so the two surfaces cannot word a keep differently.*
- [X] T027 [US2] In `src/robot_army/capacity.py`: add `kept_per_repo: dict[str, int] = field(default_factory=dict)` to `CapacitySnapshot` (line 54) and a `kept` property summing it; fill it in `snapshot()` (line 207, near where open rows are listed ~line 284) from `db.count_kept_sessions_by_repo(conn)`; in `breakdown` (line 129) render the `ours` term as `N ours (K kept)` only when `kept > 0` and `ours` is populated; leave `components` (line 111) untouched (contract [capacity.md](./contracts/capacity.md); research R6)
- [X] T028 [US2] In `src/robot_army/ordering.py`, in the repo-cap hold detail (~line 395-403, the `f"repository {item.repo_key}: {running} of {cap} sessions ({source})"` string), append `, {n} kept` when `capacity.kept_per_repo.get(item.repo_key, 0) > 0`. The global-cap detail (~line 383-386) needs no edit; it inherits `breakdown`
- [X] T029 [US2] In `src/robot_army/reconcile.py`: (a) in `reclaim_stale_session` (line 246), **inside** the alive branch (~line 318, before `db.raise_anomaly`), return `"left"` when `item is not None and item.state is WorkItemState.DONE and db.is_kept(conn, item.id)`; comment why it is inside the alive branch (a dead kept session must still be reclaimed — issue #28) and why `done` only (`operations.abandon` calls this too); (b) in `_resolve_orphan_anomalies` (line 1699), add the retraction from T024: for each open `orphan_session`, if `detail.work_item_id` names a `done`, kept item whose session row is still open, resolve it with the reason string from [audit.md](./contracts/audit.md), without the pid/start-time re-check; extend that function's "only this kind, only this condition" docstring with the second condition and why (research R3). Do not touch `_sweep_superseded_sessions` or `_orphan_sweep`
  - *Note: the retraction lives in a small helper, `_kept_and_open`, and runs before the pid re-check. A malformed or missing `work_item_id` reads as "not kept", leaving the anomaly to the pid re-check rather than retracting it on a guess.*
- [X] T030 [US2] In `src/robot_army/web/pages.py` `active_view` (line 798): add the always-present "kept" section below the running table, built from `operations.list_keeps` data (not re-queried), with the row shape in [web.md](./contracts/web.md) and a release control per row (T018's control); add `"keeps"` to the view's JSON. It must not filter on `state = active` (the filter at ~line 809 applies to the running table only)
- [X] T031 [US2] Run `uv run pytest tests/unit/test_keeps.py tests/unit/test_capacity_reporting.py tests/unit/test_holds_ordering.py tests/unit/test_session_retirement.py tests/unit/test_anomaly_resolution.py tests/unit/test_web_actions.py` — T020–T025 pass

**Checkpoint**: keeps are visible everywhere, and none produces a false anomaly.

---

## Phase 5: User Story 3 — Decide after the fact, as long as the session is still there (Priority: P3)

**Goal**: a keep placed on a `done` item whose worker still runs is honoured; one placed where
no session exists or can start is refused honestly with the resume route.

**Independent Test**: `keep` a `done` item with a live row → accepted and survives the next
pass; `keep` a `done` item whose session ended → `EXIT_PRECONDITION` and the resume line.

**Contract**: [cli.md](./contracts/cli.md) refusal row; [web.md](./contracts/web.md) refusal

### Tests for User Story 3

- [X] T032 [US3] Add to `tests/unit/test_keeps.py` (after T020; same file):
  - `keep` on a `done` item with no open row but an earlier, exited session exits `EXIT_PRECONDITION` (3), prints `work item <id> is done and has no running session; there is nothing to keep.` then `Its transcript survives: claude --resume <session-id>` with the **latest** session's id, writes no row, and writes `keep.item` with `outcome: error` and a `reason`
  - the same on an `abandoned` item
  - a `done` item that never had a session: refused, and the resume line is omitted
  - a `done` item with a live row is accepted (US3 scenario 1), and a following reconcile pass does not retire it
- [X] T033 [P] [US3] Add to `tests/unit/test_web_actions.py` (after T025; same file): `POST /item/<id>/keep` on a terminal item without a session renders the refusal through the existing `Refusal` path with the resume line, writes no row, and the `_perform` intent record is followed by an error outcome; the item page for such an item renders **no** keep control (contract: control appears only where `keep` would not be refused)

### Implementation for User Story 3

- [X] T034 [US3] In `keep_item` in `src/robot_army/operations.py` (from T013): before placing, if the item state is `done` or `abandoned` and it has no open `starting`/`running` session row, raise/return the refusal with `EXIT_PRECONDITION` (`operations.py:78`), shaped like `abandon`'s state refusal (`operations.py:3693-3700`) and inside the `ctx.audit.action` so the outcome is `error`; take the latest session id from `db.list_sessions_for_item` for the resume line, omitting it when there is none. Docstring: why only terminal-and-sessionless is refused (pre-dispatch and retried items get a session later — research R8)
  - *Note: the refusal is raised as a private `_KeepRefused` inside the `keep.item` audit pair (so the outcome is `error` with `detail.reason`), then caught and returned as `EXIT_PRECONDITION` with the resume line from `db.latest_session_for_item`. The predicate is `keep_refusal_reason` (T035). A keep on an `abandoned` item that still has a live row is accepted, per R8's predicate, even though `reclaim_stale_session` still reports that worker as an orphan.*
- [X] T035 [US3] In `src/robot_army/web/pages.py`, make the item-page keep control (T018) conditional on the same predicate as T034 — expose it as a small helper in `operations.py` (e.g. `keep_refusal_reason(conn, item) -> str | None`) used by both, so the page and the command cannot disagree
  - *Note: a kept item always shows `release` on its page, even when the keep is inert and `keep` would now be refused, so an inert keep stays releasable there (`pages._item_keep_cell`).*
- [X] T036 [US3] Run `uv run pytest tests/unit/test_keeps.py tests/unit/test_web_actions.py` — T032–T033 pass

**Checkpoint**: all three stories complete.

---

## Phase 6: Polish & documentation

**Purpose**: the guide pages `CLAUDE.md` requires, and the full suite. No configuration key
changes, so `configuration.md`, `exampleconfig.py` and `share/config.example.toml` are **not**
touched.

- [X] T037 [P] Update `docs/guide/5-outcome.md`: add a subsection under "The session's ending" (after "What says 'now'", ~line 167-231) titled "Keeping a session after the merge" — why (post-merge verification in a configuration-management repository), `keep`/`unkeep`, that it must be placed before the merge in the normal case because merged-path retirement runs on the next pass, that it never expires, that exiting the session ends it, and that a kept session holds a slot; add one sentence to "The tab" (~line 253-274) that a kept session's tab stays because its row is open; add one sentence to "Cleaning up" (~line 368-371) that a kept, live item is not offered to automatic cleanup or bare `cleanup` (so no `skipped` record per pass) while `cleanup <id>` still reports it
- [X] T038 [P] Update `docs/guide/3-selection.md`: in the capacity reasons, show the `(N kept)` breakdown and the repo-cap `, N kept` suffix with the fix (release one with `unkeep`); in "Holding specific work" (~line 286-292), pair "It never stops a running session" with: neither a hold nor a pause keeps one alive after its issue closes either — that is `keep`, a separate statement (clarification 3a)
- [X] T039 [P] Update `docs/guide/operating.md`: add `keep`, `unkeep` and `keeps` rows to the command table(s), each saying where it is reachable (CLI and web) and what it refuses (`keep`: unknown item; terminal item with no session) — `tests/unit/test_operating_reference.py` (`test_every_subcommand_is_in_a_command_table`, `test_every_command_row_says_where_it_is_reachable`, `test_a_command_row_says_what_it_refuses`) parses these tables; add the `/active` "kept" section and the keep control to the web-interface section; in the anomalies section note that `orphan_session` is not raised for a kept `done` session and one raised before the keep is retracted; if the state table lists verbs per state, add `keep` where it is accepted (every state except terminal-without-session)
  - *Note: `keep`/`unkeep` and `keeps` are both marked **Both** (the `/active` kept section is `keeps`' web surface). In the state table `keep` is added to `ready`, `active`, `awaiting_review`, `interrupted`, `failed`, and to `done` "while its session still runs"; it is left off `dispatching` (whose cell is "wait") and `abandoned` (accepted with a live row, but retirement never touches an abandoned item, so it would have no effect).*
- [X] T040 [P] Update `docs/guide/audit-log.md`: document `keep.item` and `unkeep.item` (detail fields from [audit.md](./contracts/audit.md)), the new `anomaly.resolved` reason for `orphan_session`, the two deliberate silences (withheld retirement; excluded cleanup candidate), and how to tell a retirement that followed a release: join `session.retire` to the preceding `unkeep.item`'s `open_sessions` on session id (FR-008), with a `jq` example in the page's existing style
  - *Note: the keep actions are a new `## The keep actions` section before "What is deliberately not logged", and the two silences are two new rows in that table.*
- [X] T041 [P] Update `docs/guide/state.md`: add `### item_keeps — sessions I have kept past their issue` after the `item_holds`/`repo_holds` section (line 273), with columns, the cascade, no expiry, a `sqlite3` query line like line 289's, and reboot behaviour (the keep survives; the dead worker's row is reclaimed and the keep becomes inert)
- [X] T042 Run `uv run pytest > <scratch>/full.txt` and read the summary: the whole suite must pass, including `tests/unit/test_operating_reference.py`, `tests/unit/test_named_commands.py` (every command named in source and hold reasons exists — the `unkeep` named in messages must resolve), `tests/unit/test_done_single_writer.py` (unchanged: no new writer of `done`), `tests/unit/test_web_routing.py`, and the README line-count test. Fix any failure at its cause
  - *Done: 4367 passed, 1 skipped, 0 failed. `ruff check src tests` clean.*
- [ ] T043 Walk [quickstart.md](./quickstart.md)'s live section on the real machine — **operator task for Jason**, not for an implementing agent: it merges a real PR. Record the outcome in this file

---

## Dependencies & Execution Order

- **Phase 1 → Phase 2 → stories.** T004 before T006 (accessors need the table); T005 before T006 (accessors return `Keep`).
- **US1 (Phase 3)** depends only on Phase 2. **MVP.**
- **US2 (Phase 4)** depends on Phase 2; T030's release control reuses T018's control, and T026 reuses T013's module position — so in practice start US2 after US1's implementation tasks, though its tests can be written earlier.
- **US3 (Phase 5)** depends on T013 (`keep_item` exists) and T018 (the control it makes conditional).
- **Phase 6** after all stories; T037–T041 are independent of each other.

**Same-file chains (never parallel with each other)**:

- `tests/unit/test_keeps.py`: T003 → T008 → T020 → T032
- `tests/unit/test_session_retirement.py`: T009 → T023
- `tests/unit/test_web_actions.py`: T012 → T025 → T033
- `src/robot_army/operations.py`: T013 → T026 → T034 → T035
- `src/robot_army/cli.py`: T014 → T026
- `src/robot_army/reconcile.py`: T015 → T029
- `src/robot_army/db.py`: T006 → T016
- `src/robot_army/web/pages.py`: T018 → T030 → T035

## Parallel Opportunities

- **Phase 2**: T002, T003 and T005 together.
- **US1 tests**: T009, T010, T011, T012 together (four files), alongside T008.
- **US1 implementation**: T015 (reconcile.py), T016 (db.py), T017 (server.py) together after T013/T014; T018 after T017.
- **US2 tests**: T021, T022, T023, T024 together (four files), alongside T020.
- **US2 implementation**: T027 (capacity.py) and T029 (reconcile.py) together; T028 after T027.
- **Docs**: T037–T041 together (five files).

Example, US1 tests in one batch:

```text
T009 tests/unit/test_session_retirement.py
T010 tests/unit/test_window_closing.py
T011 tests/unit/test_cleanup.py
T012 tests/unit/test_web_actions.py
```

## Implementation Strategy

1. **MVP = Phase 1 + 2 + US1.** That alone meets the request: keep before merging, verify
   in the session, release. Stop and validate with T019 and the quickstart's steps 1–5.
2. **US2** removes the two costs of the MVP — an invisible kept session and a false
   `orphan_session` on every kept item — and should ship in the same PR; without it, the
   MVP raises an anomaly for every kept session.
3. **US3** is the honest refusal; small, last.
4. **Phase 6** docs are part of done, not follow-up: `test_operating_reference.py` fails
   without T039.
