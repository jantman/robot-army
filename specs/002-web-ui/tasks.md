---

description: "Task list for Web UI & HTTP API (milestone 002)"
---

# Tasks: Web UI & HTTP API

**Input**: Design documents from `/specs/002-web-ui/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: Included, and not optional here. The constitution's Development Workflow requires unit
tests for every new or changed unit of behaviour, and additional failure-and-interruption tests for
persistence, state machines, and code parsing external input — which a request handler is. It also
says test-first development is **not** mandatory and coverage targets **must not** be adopted, so the
test tasks below are placed beside the code they cover rather than ahead of it. Write them in
whichever order suits the work; the gate is that they exist, are meaningful, and pass.

**Organization**: By user story, in the priority order spec.md assigns, so each story is a shippable
increment. There is one maintainer, so the parallel markers below identify work that does not collide
— not work that needs a second person.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Touches files no other pending task touches; safe to interleave
- **[Story]**: US1–US5, mapping to spec.md's user stories
- Every task names its exact file path

## Path Conventions

Single project, as 001 established: `src/robot_army/`, `tests/unit/`, `tests/integration/`. The new
code lives in `src/robot_army/web/` and `src/robot_army/control.py`.

---

## Phase 1: Setup

**Purpose**: The package skeleton and the one configuration section everything else reads.

- [X] T001 Create the `src/robot_army/web/` package with `__init__.py`, `server.py`, `pages.py`, and `html.py` as empty modules with their docstrings stating what each is for
- [X] T002 [P] Add the `[web]` configuration section — `bind` (default `127.0.0.1`), `port` (default `8420`), `refresh_seconds` (default `10`) — as a `WebConfig` frozen dataclass in `src/robot_army/config.py`, parsed by `parse()` alongside the existing sections
- [X] T003 [P] Extend `tests/unit/test_config.py` with cases for the `[web]` defaults, explicit overrides, and a rejected non-integer port

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The database change every view reads, the HTML machinery, and a server that can render a
read-only page. Nothing here performs an action — the action machinery arrives in Phase 4 with its
first consumer, so it is not built speculatively.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Schema and state

- [X] T004 Append `_migration_002` creating the `dispatch_control` table per [data-model.md](data-model.md) to `src/robot_army/migrations.py`, leaving `_migration_001` untouched and letting `SCHEMA_VERSION` derive from the tuple length
- [X] T005 [P] Add the `DispatchControl` frozen dataclass to `src/robot_army/models.py`, matching the existing model style so `from_row` handles it
- [X] T006 Add `get_dispatch_control(conn)` and `set_dispatch_paused(conn, *, paused, by)` accessors to `src/robot_army/db.py`, with setting an already-held state returning the existing row rather than raising (depends on T004, T005)
- [X] T007 [P] Extend `tests/unit/test_migrations.py` with a case asserting migration 002 runs on a 001-era database, that a killed migration leaves `user_version` unadvanced and re-runs, and that the single-row `CHECK` rejects a second row

### HTML and assets

- [X] T008 [P] Implement `src/robot_army/web/html.py`: `escape` wrappers, element helpers, the page chrome (header, navigation, banner, content container, footer with render time), and the `APP_CSS` and `APP_JS` module constants — no value reaches the output without passing through escaping
- [X] T009 [P] Write the refresh loop into `APP_JS` in `src/robot_army/web/html.py`: re-fetch the current URL every `refresh_seconds`, replace the content container, and leave the page correct-but-static when scripting is off (R2)
- [X] T010 [P] Add `tests/unit/test_web_render.py` covering escaping of a work item title containing HTML, the simulated marker appearing on `dry_run` rows, and an assertion that no rendered page references any host outside `github.com`

### Server core

- [X] T011 Implement the request handler in `src/robot_army/web/server.py`: a literal route table, `GET` dispatch, HTML/JSON negotiation on `Accept` and a `.json` path suffix, `404` for unknown paths and `405` with `Allow` for wrong methods, `Cache-Control: no-store` everywhere except `/static/*`, and a per-request `Context` built with `component="web"` and closed when the request ends (R2, R11)
- [X] T012 Serve `APP_CSS` and `APP_JS` at `/static/app.css` and `/static/app.js` from the module constants in `src/robot_army/web/server.py`, with no request-derived filesystem path anywhere in the module (R12)
- [X] T013 Implement `validate_bind(address)` in `src/robot_army/web/server.py` refusing a globally routable address via `ipaddress.ip_address(...).is_global`, permitting loopback, private ranges and the VPN range, and permitting `0.0.0.0` with a warning (R13, FR-004)
- [X] T014 Add the startup precondition check to `src/robot_army/web/server.py`: config valid, database openable with `PRAGMA user_version == SCHEMA_VERSION` and **never** migrating, bind address permitted, socket bindable — reporting every problem rather than the first, exiting `3` (R11)
- [X] T015 Implement the `chrome` payload in `src/robot_army/web/pages.py`: effect level, daemon running state from `daemon.is_locked` plus heartbeat age and activity, the effect-level mismatch from comparing heartbeat to `ctx.effect_level`, dispatch pause state from T006, unacknowledged anomaly count, `include_simulated`, and `rendered_at` (FR-016 through FR-018, R4)
- [X] T016 Add the `serve` verb to `src/robot_army/cli.py` per [contracts/cli-additions.md](contracts/cli-additions.md), with `--bind`, `--port`, `--config`, the `web.start` audit record naming the effective address, the non-loopback warning, and `SIGTERM`/`SIGINT` finishing in-flight requests and exiting `0`
- [X] T017 [P] Add `tests/unit/test_web_routing.py` covering the route table, unknown path, wrong method, JSON negotiation both ways, and the `no-store` header
- [X] T018 [P] Add `tests/unit/test_web_bind.py` covering a refused public address, an accepted private address, an accepted loopback default, `0.0.0.0` accepted with a warning, and the schema-version mismatch refusal

**Checkpoint**: `robot-army serve` starts, refuses what it should, and serves an empty shell with
correct chrome. No view exists yet.

---

## Phase 3: User Story 1 - See what the daemon is doing, from the couch (Priority: P1) 🎯 MVP

**Goal**: A read-only interface, usable from a phone, showing what is running, what is queued, what
is blocked and why, what is interrupted, whether the daemon is alive, at which effect level, and how
many anomalies are outstanding.

**Independent Test**: With sessions running, items queued, and at least one interrupted item, load
the interface on a 390-pixel-wide viewport with no terminal available and confirm every fact is
legible and correct without zooming or scrolling sideways — quickstart Scenario 1.

**Why this alone is worth shipping**: it is the thing the author cannot get today, and it puts
nothing at risk, because nothing here can change state.

- [X] T019 [P] [US1] Implement the active view in `src/robot_army/web/pages.py`: work item, repository, issue number and title with a link, checkout path, branch, session state, start time and elapsed, from `operations.status(state="active")` joined to `db.latest_session_for_item` (FR-011)
- [X] T020 [P] [US1] Implement the queue view in `src/robot_army/web/pages.py`: `ready` in dispatch order with position, `dispatching` with age against the configured maximum, and blocked items each carrying its specific `blocked_reason` or `failure_reason` (FR-012)
- [X] T021 [P] [US1] Implement the interrupted **listing** in `src/robot_army/web/pages.py` — items, when they ended, their branch and checkout, and the `worktree_missing` condition. The four resume signals belong to User Story 2 and are not computed here (FR-014 is completed in Phase 4)
- [X] T022 [P] [US1] Implement the anomalies view in `src/robot_army/web/pages.py` from `operations.anomalies`, with enough detail per anomaly to act on it (FR-017)
- [X] T023 [US1] Register `/`, `/active`, `/queue`, `/interrupted`, and `/anomalies` and their `.json` forms in the route table in `src/robot_army/web/server.py`, with `/` redirecting to `/active` (depends on T019–T022)
- [X] T024 [US1] Honour `?include_simulated=1` on every view in `src/robot_army/web/server.py` and `pages.py`, passing it to the `db` accessors whose default already excludes simulated rows, and marking every simulated row visibly (FR-019)
- [X] T025 [P] [US1] Write the phone stylesheet into `APP_CSS` in `src/robot_army/web/html.py`: single column, no horizontal scroll at 390 pixels, tables that scroll inside their own container, touch targets large enough to hit without zooming (FR-007, SC-013)
- [X] T026 [P] [US1] Add `tests/unit/test_web_views.py` asserting each view's payload shape against a seeded database, that simulated rows are absent by default and present-and-marked when asked for, and that a blocked item renders its reason
- [X] T027 [US1] Add `tests/integration/test_web_end_to_end.py` binding a real ephemeral port and driving `/active`, `/queue.json`, and an unknown path with `urllib.request` (R15)

**Checkpoint**: The MVP. The interface is worth using from the couch, and cannot change anything.

---

## Phase 4: User Story 2 - Decide what to do with an interrupted item (Priority: P2)

**Goal**: See the four resume-decision signals for each interrupted item, then resume, restart, or
abandon it — from a phone, with a confirmation, without ever acting on a stale view or double-acting
on a double tap.

**Independent Test**: Interrupt three sessions by different means, then from a phone alone resume
one, restart one, and abandon one, confirming each produced exactly the intended effect and the other
two were untouched — quickstart Scenarios 3, 4 and 5.

**This phase introduces the action machinery** that Stories 3 and 5 then reuse. It is built here, with
its first consumers, rather than in Phase 2 without any.

### Resume signals

- [X] T028 [US2] Split `operations.resume_signals` in `src/robot_army/operations.py` into a local part (uncommitted changes, commits on branch) recomputed every call and a remote part (issue closed, open pull request) behind a 60-second in-process cache keyed by item and branch, each returned value carrying its age (R9, FR-013)
- [X] T029 [US2] Add the four signals and `signals_age_seconds` to the interrupted view in `src/robot_army/web/pages.py`, completing FR-013 (depends on T021, T028)
- [X] T030 [P] [US2] Add `tests/unit/test_resume_signals.py` asserting the local signals are recomputed on every call, the remote ones are served from cache within the window and refetched after it, and that a cached value renders with a visible age

### Action machinery

- [X] T031 [US2] Implement `POST` handling in `src/robot_army/web/server.py`: form body parsing with `urllib.parse`, the audit intent record written **before** the action and the outcome after (FR-038), `303 See Other` back to the referring view with a `?msg=` banner key, and the JSON refusal shape from [contracts/http-api.md](contracts/http-api.md) carrying the equivalent terminal exit code
- [X] T032 [US2] Implement the three structural refusals in `src/robot_army/web/server.py`: `503` when no daemon holds the lock and the action needs one (FR-005), `409` naming both levels on an effect-level mismatch (R4), and `409` carrying the `IllegalTransition` reason when the item is no longer in a valid state (FR-027)
- [X] T033 [US2] Enforce the invariant that no error response is returned without a corresponding audit record, in `src/robot_army/web/server.py` (FR-039)
- [X] T034 [US2] Implement `GET /item/<id>/confirm/<action>` in `src/robot_army/web/pages.py` and `server.py`: name the item and the action, re-validate against current state, and render no form at all when the action is no longer legal (R8, FR-026)
- [X] T035 [US2] Implement the single-worker thread in `src/robot_army/web/server.py` for `resume` and `restart`, returning `303` immediately and leaving the item's own state to tell the story; a thread lost to a killed process is reconciliation's problem, not the server's (R3)

### Item detail and actions

- [X] T036 [US2] Implement the item detail view in `src/robot_army/web/pages.py` from `operations.show`: source links, full state history with timestamps, every session attempt with exit code and signal number, the resume signals where they apply, and the list of actions currently legal for the item's state (FR-015, FR-029)
- [X] T037 [US2] Register `/item/<id>`, `/item/<id>/confirm/<action>`, and the `resume`, `restart`, and `abandon` `POST` routes in `src/robot_army/web/server.py`, each calling the existing `operations.*` function and reporting its returned `Result` rather than assuming its pre-check succeeded (FR-021, R7)
- [X] T038 [P] [US2] Add `tests/unit/test_web_actions.py` covering: an action against a state that changed after render is refused with the reason; three concurrent identical resumes produce exactly one session row; a reload after `POST` re-issues a `GET`; and the confirm page refuses rather than offering a form when the item moved
- [X] T039 [P] [US2] Add `tests/unit/test_web_effect_guard.py` asserting mutations are refused with `409` when the heartbeat names a different effect level, that read views keep working throughout, and that a stale or absent heartbeat falls back to the configured level
- [X] T040 [US2] Extend `tests/integration/test_web_end_to_end.py` with the confirm-then-post round trip against a real port, asserting the `303` and that the item advanced

**Checkpoint**: The interface can be acted on safely. Stories 1 and 2 both work independently.

---

## Phase 5: User Story 3 - Take control when something is going wrong (Priority: P3)

**Goal**: Cancel one session without touching the others; pause dispatch durably; force a poll or a
reconciliation that actually happens; acknowledge an anomaly; put a failed item back in the queue.

**Independent Test**: With several sessions running, cancel exactly one and confirm the others
continue; pause dispatch and confirm an eligible item is held rather than dispatched; force a poll
and confirm it happened; resume dispatch and confirm the held item dispatches — quickstart Scenarios
6, 7 and 8.

### Pausing dispatch

- [X] T041 [US3] Add `pause_dispatch(ctx, *, by)` and `unpause_dispatch(ctx, *, by)` to `src/robot_army/operations.py`, writing through `db.set_dispatch_paused` inside `db.transaction` with an audit record, and treating a redundant pause as a reported no-op rather than an error (FR-033)
- [X] T042 [US3] Make `daemon.job_dispatch` in `src/robot_army/daemon.py` read `dispatch_control` first and return without dispatching while paused, leaving polling, eligibility evaluation, reconciliation, and the heartbeat running (FR-033, FR-034)
- [X] T043 [P] [US3] Add the `dispatch_paused` field to `health.Heartbeat` and `write_heartbeat` in `src/robot_army/health.py`, defaulting to `False` so an older heartbeat still parses, and pass it from the daemon's tick (FR-036)
- [X] T044 [P] [US3] Show the pause state and when it was set in `operations.status` output and its `--json` payload in `src/robot_army/operations.py` (FR-036)
- [X] T045 [US3] Add the `pause` and `unpause` verbs to `src/robot_army/cli.py` per [contracts/cli-additions.md](contracts/cli-additions.md), working whether or not the daemon is running
- [X] T046 [US3] Register `POST /dispatch/pause` and `POST /dispatch/unpause` in `src/robot_army/web/server.py` and surface the controls plus the paused banner in `src/robot_army/web/pages.py` (FR-035)
- [X] T047 [P] [US3] Add `tests/unit/test_pause.py` covering: dispatch held while paused with items still accumulating in `ready`; the pause surviving a simulated daemon restart; the heartbeat carrying it; polling and reconciliation unaffected; and a rolled-back pause leaving dispatch running

### Forcing a job across processes

- [X] T048 [US3] Implement `src/robot_army/control.py`: `request_job(layout, name)` creating the marker atomically and `take_requests(layout)` unlinking and returning pending names, with `poll` and `reconcile` as the only valid names and an unrecognised file logged once rather than deleted (R5, data-model.md)
- [X] T049 [US3] Drain the request markers at the top of each tick in `src/robot_army/daemon.py`, setting the existing `Job.forced` flag through `Daemon.request()` (depends on T048)
- [X] T050 [US3] Change `operations.poll_now` and `operations.reconcile_now` in `src/robot_army/operations.py` to write a request marker when a daemon holds the lock — replacing the current message that only states the interval — and to report honestly that the job was *requested* and will run within one tick, keeping the direct-execution path unchanged when no daemon is running (R5, FR-023)
- [X] T051 [US3] Register `POST /poll` and `POST /reconcile` with an optional `repo` field in `src/robot_army/web/server.py`, with the controls in `src/robot_army/web/pages.py`
- [X] T052 [P] [US3] Add `tests/unit/test_job_requests.py` covering: the marker written and consumed exactly once; a re-request while one is pending being a harmless no-op; an unrecognised filename ignored and logged; and the no-daemon path still executing directly

### Cancel, acknowledge, retry

- [X] T053 [US3] Register `POST /item/<id>/cancel` in `src/robot_army/web/server.py` calling `operations.cancel(force=True)` — the HTTP confirmation has already happened and the terminal prompt would have nothing to read (FR-021, contracts/http-api.md)
- [X] T054 [P] [US3] Register `POST /item/<id>/retry` in `src/robot_army/web/server.py` calling `operations.retry`, rendering the refusal reason when the blocking condition still holds (FR-022)
- [X] T055 [P] [US3] Register `POST /anomalies/<id>/acknowledge` in `src/robot_army/web/server.py` calling `operations.anomalies(acknowledge=id)`, with the control on the anomalies view in `src/robot_army/web/pages.py` (FR-024)
- [X] T056 [US3] Extend `tests/unit/test_web_actions.py` with cancel affecting exactly one session, retry refused while blocked, and acknowledge removing an anomaly from the outstanding count

**Checkpoint**: The interface is useful during an incident, not only during calm inspection.

---

## Phase 6: User Story 4 - Reconstruct what happened (Priority: P4)

**Goal**: Read the audit log from the interface, filtered, newest first, bounded, with GitHub
repositories, issues, and pull requests as followable links.

**Independent Test**: Pick a completed work item and determine from the interface alone what happened
to it — every transition, every outward-facing action, the outcome of each — without a terminal and
without re-running anything. Quickstart Scenario 11.

- [X] T057 [US4] Add backwards paging to the audit reader in `src/robot_army/operations.py`: newest daily file first, collect and reverse, stop once the page is full, returning an opaque cursor — reusing the existing filtering and skip-and-count rather than reimplementing them (R14, FR-044)
- [X] T058 [US4] Implement the audit view in `src/robot_army/web/pages.py`: timestamp, component, action, target and outcome, newest first, with the active filter always visible and the unparseable-line count rendered (FR-042, FR-044)
- [X] T059 [US4] Render GitHub repositories, issues, and pull requests in audit records as links in `src/robot_army/web/pages.py`, constructed from data already in the record with no additional source-system call (FR-043)
- [X] T060 [US4] Register `/log` and `/log.json` with `item`, `since`, `outcome`, and `cursor` parameters in `src/robot_army/web/server.py`, parsing durations with the existing `operations.parse_duration`
- [X] T061 [P] [US4] Add `tests/unit/test_web_log.py` covering: a truncated final line producing records plus a non-zero skipped count; each filter narrowing correctly; paging returning disjoint pages across a file boundary; and a `web`-component record rendering as such
- [X] T062 [P] [US4] Add a performance assertion to `tests/unit/test_web_log.py` that a first page returns in under 2 seconds against a synthesised 100,000-record log (SC-014)

**Checkpoint**: The record is readable from the phone, with the links already made.

---

## Phase 7: User Story 5 - Sit down and take over a session (Priority: P5)

**Goal**: Open a terminal window attached to a running session, from the interface, without hunting
for the window or reconstructing the command.

**Independent Test**: With a session running and the terminal instance available, attach from the
interface and confirm a window appears showing that session's live state, with the session still
running afterwards. Quickstart Scenario 9.

- [X] T063 [US5] Add `attach(ctx, item_id)` to `src/robot_army/operations.py`: refuse unless the latest session is `running`, call `Display.open()` with `SessionHost.attach_command(handle)` as the argv titled for the item, change no session state, and turn a `BoundaryError` into a visible refusal naming the missing terminal socket (R10, FR-025)
- [X] T064 [P] [US5] Add the `attach` verb to `src/robot_army/cli.py` per [contracts/cli-additions.md](contracts/cli-additions.md), exiting `3` when there is no running session and `1` when no terminal socket answers
- [X] T065 [US5] Register `POST /item/<id>/attach` in `src/robot_army/web/server.py` and offer the control only for items with a running session in `src/robot_army/web/pages.py` (FR-029)
- [X] T066 [P] [US5] Add `tests/unit/test_attach.py` against the simulated display covering: refusal for a non-running session; no state change on success; a second attach also succeeding; and a boundary failure surfacing as a refusal rather than an exception

**Checkpoint**: All five stories work independently.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: The documentation the constitution requires, the checks it requires, and the human round
CI cannot perform.

- [X] T067 [P] Document the `dispatch_control` table and the `requests/` markers in `docs/state.md`, including their rows in the interruption table
- [X] T068 [P] Document the `web` component and the read-request audit exemption in `docs/logging.md`, matching the enumeration in [plan.md](plan.md)
- [X] T069 [P] Add running the interface to `README.md`: both processes started by hand after graphical login, the `[web]` configuration, and the plain statement that anything able to reach the port has full control
- [X] T070 [P] Correct the force-poll behaviour described in `specs/001-minimum-daemon/contracts/cli.md`, which promised a signal that had no cross-process caller, pointing at [research.md](research.md) R5
- [X] T071 [P] Add the `[web]` section to the configuration contract in `specs/001-minimum-daemon/contracts/config.md`
- [X] T072 Add `tests/unit/test_web_secrets.py` asserting no rendered page or JSON payload contains the configured token, across every view including error paths (FR-020, SC-012)
- [X] T073 Add an offline assertion to `tests/unit/test_web_render.py` that no view emits a `src` or `href` to any host other than `github.com` (SC-009)
- [X] T074 Verify by enumeration in `tests/unit/test_web_routing.py` that every `POST` route has a terminal equivalent in `src/robot_army/cli.py` (FR-006, SC-011)
- [X] T075 Run `uv run ruff check src tests` and resolve findings without suppressing a rule to avoid a real problem
- [X] T076 Run `uv run pytest` and confirm the full suite passes — implementation is not complete until it does
- [ ] T077 Walk [quickstart.md](quickstart.md) Scenarios 1 through 13 against a real daemon, a real kitty, and a real repository
- [ ] T078 Do the human round: a real phone, on the couch, with the daemon running — see what is active, resume something interrupted, pause dispatch, and read back in the audit log exactly what you just did

### On T077 and T078

**These two are the author's to do and are deliberately left open.** They need a real kitty
with a control socket, a real GitHub token against real repositories, real sessions to
interrupt, and a physical phone — none of which an implementation pass can substitute for
without pretending. Ticking them from a test harness would be exactly the dishonesty the
constitution's "review is self-review" clause depends on not happening.

What *was* verified mechanically, so the human round starts from a working system rather
than a debugging session:

| Quickstart scenario | Covered by |
|---|---|
| 1 — the couch view | `test_web_views.py`, plus a real 390px viewport measured in a browser: no horizontal page scroll on any view, smallest text 13px, smallest touch target 44px |
| 2 — the daemon is down | `test_web_views.py`, `test_web_actions.py`, and a live `serve` refusing a resume with `503` |
| 4 — the view is stale when you tap | `test_web_actions.py` |
| 5 — double tap | `test_web_actions.py`, three concurrent resumes producing one session |
| 6 — pause dispatch | `test_pause.py`, including durability across a simulated restart |
| 8 — force a poll | `test_job_requests.py`, marker written and drained exactly once |
| 11 — reconstruct what happened | `test_web_log.py`, including a truncated final line |
| 12 — no secrets, no outside world | `test_web_secrets.py`, `test_web_render.py` |
| 13 — a public bind address is refused | `test_web_bind.py`, and `serve --bind 8.8.8.8` exiting `3` |

Scenarios 3, 7, 9 and 10 — interrupting a real session, cancelling one of two running
sessions, attaching a kitty tab, and a real effect-level disagreement between two running
processes — have unit coverage of their logic but need real sessions to exercise end to end.

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)**: no dependencies
- **Phase 2 (Foundational)**: depends on Phase 1 — **blocks every user story**
- **Phase 3 (US1, P1)**: depends on Phase 2. Depends on no other story
- **Phase 4 (US2, P2)**: depends on Phase 2. Reuses US1's interrupted listing (T021) for T029, and is
  where the action machinery is built
- **Phase 5 (US3, P3)**: depends on Phase 2, and on Phase 4's action machinery (T031–T035) for its web
  controls. Its terminal halves — T041–T045, T048–T050 — depend on neither and can be done first
- **Phase 6 (US4, P4)**: depends on Phase 2 only. Fully independent of US2, US3 and US5
- **Phase 7 (US5, P5)**: depends on Phase 2, and on Phase 4's action machinery for its web control.
  Its operation and CLI verb (T063, T064) depend on neither
- **Phase 8 (Polish)**: depends on whichever stories were built

### The one cross-story dependency worth naming

Stories 3 and 5 add `POST` routes, and `POST` handling is built in Story 2. That is deliberate: the
alternative was building the action machinery in Phase 2 with no consumer, which Principle I forbids.
If Story 3 is wanted before Story 2, its terminal commands (`pause`, `unpause`, forced poll) deliver
most of its value with no web dependency at all — and by the Operating Constraints those commands
have to exist regardless.

### Within each story

Payloads before views, views before routes, routes before their tests reach through them. Tests sit
beside their code rather than ahead of it, per the constitution's explicit position that test-first is
not mandatory.

### Parallel opportunities

- T002 and T003 in Setup
- T005, T007, T008, T009, T010 in Foundational — the model, the migration test, the HTML module, and
  the render test touch disjoint files
- T017 and T018 in Foundational, once T011–T016 are in
- T019 through T022 in US1: four view functions, each self-contained, all in `pages.py` — do them
  in one sitting to avoid colliding in that file
- T030, T038, T039 in US2
- T043, T044, T047, T052, T054, T055 in US3
- T061 and T062 in US4
- T064 and T066 in US5
- T067 through T071 in Polish: five documentation files, no overlap

Note the honest limit: `pages.py` and `server.py` are each touched by many tasks. Tasks marked `[P]`
against those files are parallel in the sense of *independent*, not of *simultaneously editable*.

---

## Implementation Strategy

### MVP first (User Story 1)

1. Phase 1 — Setup.
2. Phase 2 — Foundational. This is the largest single block and it blocks everything.
3. Phase 3 — User Story 1.
4. **Stop and validate**: quickstart Scenario 1 on a real phone. A read-only interface that tells the
   truth is already worth having, and nothing in it can hurt anything.

### Incremental delivery

| After | You can |
|---|---|
| US1 | See everything from the couch. Nothing can be changed |
| US2 | Decide interrupted items from the phone — the daily use this milestone exists for |
| US3 | Handle an incident: cancel one session, pause dispatch, force a poll |
| US4 | Read the record from the phone with links already made |
| US5 | Take over a session at the desk in one tap |

Each is a stopping point, not a milestone with an obligation attached. If US4 and US5 never get
built, nothing already built becomes wrong — the terminal already covers both.

### One maintainer

There is no parallel-team strategy. The `[P]` markers mark work that does not collide, which matters
for ordering a session's work and for knowing what can be picked up after an interruption — not for
staffing.

---

## Notes

- Every `POST` route calls an existing `operations.*` function. If a route needs logic that is not
  there, the logic goes **in `operations.py`**, not in the web package — that rule is what keeps the
  two front ends from diverging (FR-047), and it is why four new operations appear in this list
- Commit atomically, with messages explaining why rather than what
- The constitution's two questions have to be answerable for every task that touches state: what does
  this log, and what happens if it is killed halfway
- Stop at any checkpoint. Each phase leaves the system working
