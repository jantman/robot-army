---

description: "Task list for guarding cross-origin GETs and cutting the cost of the read views"
---

# Tasks: Guard cross-origin GETs, and stop the read views being expensive

**Input**: Design documents from `/specs/20260904-143822-guard-cross-origin-gets/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/read-cost.md](contracts/read-cost.md)

**Tests**: Required, not optional. The constitution's Development Workflow says "Every new or
changed unit of behavior MUST ship with unit tests", and adds that "code parsing external input
MUST additionally carry tests exercising their failure and interruption paths" — which the
backwards log scanner and the origin check both are. Tests are written before the change they
cover in each phase, so each one is seen to fail first.

**Organization**: one phase per user story, in the spec's priority order. US1 is the finding;
US2, US3 and US4 each stand alone and can be delivered in any order after the foundation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: the user story the task serves
- Every task names the exact file it touches

## Path Conventions

Single project. Source at `src/robot_army/`, tests at `tests/unit/` and `tests/integration/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: nothing is installed or scaffolded — this project is already set up. The one task
here establishes the file the counting tests live in, because three later phases add to it and
creating it three times would conflict.

- [ ] T001 Create `tests/unit/test_web_read_cost.py` with a module docstring naming its subject — the *cost* of a served read, counted rather than asserted by construction — plus imports and the shared fixtures the counting tests need: a `counting_condition` helper that wraps `operations.worktree.condition` and records calls, and a `counting_snapshot` helper that wraps `robot_army.capacity.snapshot`. No test cases yet.

**Checkpoint**: `uv run pytest tests/unit/test_web_read_cost.py` collects zero tests and passes.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the two constants and the one payload-shaping decision that more than one story
reads. Both are trivial in isolation and both must exist before the stories that consume them.

**⚠️ CRITICAL**: no user story work begins until this phase is complete.

- [ ] T002 [P] Add `LOCAL_SIGNAL_TTL_SECONDS = 5.0` to `src/robot_army/operations.py`, beside `REMOTE_SIGNAL_TTL_SECONDS`, with a comment giving the reasoning from research R6: below the default `[web] refresh_seconds = 10` so an open page still observes the worktree afresh on every refresh, above the burst window so a flood collapses to one observation per item per five seconds.
- [ ] T003 [P] Add `LOG_SCAN_BLOCK_BYTES = 65536` and `LOG_SCAN_BUDGET_BYTES = 8 * 1024 * 1024` to `src/robot_army/operations.py`, near `LOG_PAGE_SIZE`, each with the reasoning from research R4 at the definition.
- [ ] T004 Extract the expiry purge shared by both signal caches into `operations._purge_expired(cache, ttl, now)` in `src/robot_army/operations.py`, and call it from the existing `_REMOTE_SIGNAL_CACHE` insert. Behaviour of the remote cache must not change; `tests/unit/test_resume_signals.py` must still pass untouched.

**Checkpoint**: `uv run pytest tests/unit/test_resume_signals.py` passes with no test edited.

---

## Phase 3: User Story 1 — A visited page cannot make the interface do work (Priority: P1) 🎯 MVP

**Goal**: a read the browser reports as cross-site is refused before any work is done on it —
no `git`, no audit file read, no process enumeration, no SQLite connection, no audit handle.

**Independent Test**: send `GET /interrupted` with `Sec-Fetch-Site: cross-site` and assert a
`403` and zero version-control observations; send the same request with the header absent, with
`none`, and with `same-origin`, and assert `200` for each.

### Tests for User Story 1

- [ ] T005 [P] [US1] In `tests/unit/test_web_routing.py`, add a `CROSS_SITE_READ` header dict and cases asserting `403` for `GET` on `/active`, `/queue`, `/interrupted`, `/item/1`, `/log`, `/anomalies`, `/cards`, `/style.css` and `/app.js`, in both the HTML and the `?json` representations, and that the body carries the standard refusal shape (`ok: false`, `code: 3`).
- [ ] T006 [P] [US1] In `tests/unit/test_web_routing.py`, add cases asserting `200` for the three honest read paths — no origin headers at all (the documented `curl` path), `Sec-Fetch-Site: none` (address bar or bookmark), and `Sec-Fetch-Site: same-origin` with a matching `Origin` — each with a docstring naming why that path must never be refused.
- [ ] T007 [P] [US1] In `tests/unit/test_web_routing.py`, add a case asserting a read whose `Origin` netloc differs from its `Host` is refused, and one asserting `Sec-Fetch-Site: same-site` is refused, with a docstring recording R2's reason: `check_host` already requires an IP literal or `localhost`, so an honest `same-site` cannot arise here.
- [ ] T008 [US1] In `tests/unit/test_web_read_cost.py`, add the test that gives this story its name: seed several interrupted items, issue a cross-site `GET /interrupted`, and assert zero calls to `operations.worktree.condition`, zero calls to `capacity.snapshot`, and that no audit file was read — proving the refusal precedes the work rather than merely accompanying it.
- [ ] T009 [P] [US1] In `tests/unit/test_web_actions.py`, add a case asserting a cross-site `POST` is still refused **and still leaves its audit pair** — the property the early check must not displace. Assert the `web.<action>` record exists with outcome `error`.
- [ ] T010 [P] [US1] In `tests/integration/test_web_end_to_end.py`, add one round-trip over a real socket asserting a cross-site `GET` is refused with `403`, alongside the existing cross-site `POST` case.

### Implementation for User Story 1

- [ ] T011 [US1] In `src/robot_army/web/server.py`, generalise `check_same_origin`'s docstring and refusal message from "state-changing request" to "request", keeping the rule itself byte-for-byte unchanged, and adding a paragraph recording R1: why the `POST` call site stays inside `_perform` and this function is now also called before routing.
- [ ] T012 [US1] In `src/robot_army/web/server.py`, add `refused_cross_site: int` and its lock to `WebApp.__init__`, with a comment stating why the counter lives on `WebApp` rather than on the server class (`handle` receives the app and never the server; a socketless test drives `handle` directly).
- [ ] T013 [US1] In `src/robot_army/web/server.py`, call `check_same_origin(request)` inside `handle`'s existing `try` block beside `check_host` — before routing, before `app.context()` — for every method **not** in the mutating set, incrementing `app.refused_cross_site` on refusal and returning the same `_bare(pages.refusal_view(...))` response the rebinding refusal already returns.
- [ ] T014 [US1] In `src/robot_army/web/server.py`, add `refused_cross_site` to the `web.stop` audit record's detail beside `refused_over_capacity`, and extend that call site's comment to state the Principle III exception in the same words as `contracts/read-cost.md` C2, so the two cannot drift.

**Checkpoint**: US1 is complete and independently demonstrable — quickstart check 1 passes end
to end, and the finding is closed even if nothing else in this feature ships.

---

## Phase 4: User Story 2 — The interrupted view stops forking git per card (Priority: P2)

**Goal**: `worktree.condition` runs at most once per five seconds per item, with the age visible
and acting on an item clearing it.

**Independent Test**: render `/interrupted` twice within five seconds against ten items and
count the version-control observations; the second render must add none. Advance the clock past
the TTL and confirm they are made again.

### Tests for User Story 2

- [ ] T015 [P] [US2] In `tests/unit/test_resume_signals.py`, replace `test_the_local_signals_are_recomputed_on_every_call` with `test_the_local_signals_are_reused_inside_the_window` — three calls, one observation — and rewrite its docstring to record what changed and why the old rule was right until the flood made it expensive.
- [ ] T016 [P] [US2] In `tests/unit/test_resume_signals.py`, add a case moving `operations._monotonic` past `LOCAL_SIGNAL_TTL_SECONDS` and asserting the observation is made again.
- [ ] T017 [P] [US2] In `tests/unit/test_resume_signals.py`, add cases asserting the cache key covers every input `worktree.condition` is given: changing the branch, the worktree path, and the base ref each force a fresh observation (FR-007).
- [ ] T018 [P] [US2] In `tests/unit/test_resume_signals.py`, add a case asserting a `BoundaryError` is reported as `worktree_error` and **not** cached — the next call tries again and a recovery is visible (FR-008).
- [ ] T019 [P] [US2] In `tests/unit/test_resume_signals.py`, add a case asserting `local_signals_age_seconds` is `0` on a fresh observation and the elapsed whole seconds on a reused one (FR-009), and one asserting `resume_signals` carries both age fields without either overwriting the other.
- [ ] T020 [P] [US2] In `tests/unit/test_resume_signals.py`, add cases for `forget_resume_signals(item_id)`: it drops that item's local and remote entries and leaves another item's alone (FR-010); and for the purge, that entries older than the TTL do not accumulate across many distinct keys (FR-012).
- [ ] T021 [P] [US2] In `tests/unit/test_web_views.py`, add a case asserting `/interrupted` renders the checkout-signal age footnote — "read just now" when fresh, "Ns old (cached)" when reused — beside the existing GitHub one.
- [ ] T022 [US2] In `tests/unit/test_web_read_cost.py`, add the counting test: ten interrupted items, two renders of `/interrupted` inside the window, and at most the observations of a single render (SC-004).
- [ ] T023 [P] [US2] In `tests/unit/test_web_actions.py`, add a case asserting a successful `POST` on an item drops that item's cached signals, so the page rendered after the action reflects it (FR-010).

### Implementation for User Story 2

- [ ] T024 [US2] In `src/robot_army/operations.py`, add `_LOCAL_SIGNAL_CACHE` and `_LOCAL_SIGNAL_LOCK` beside the remote pair, with a docstring stating the key from `data-model.md` §1 and why the two caches stay separate rather than merging (different TTLs, for different reasons — the split the existing code already encodes).
- [ ] T025 [US2] In `src/robot_army/operations.py`, rewrite `local_resume_signals` to read the cache, observe on a miss, cache only a successful observation, purge expired entries on insert via `_purge_expired`, and return `local_signals_age_seconds` on every path. Replace the "**Recomputed on every call**" docstring with the reasoning that replaces it, keeping the original sentence's point — the maintainer may be in the worktree with an editor open — and explaining why five seconds preserves it.
- [ ] T026 [US2] In `src/robot_army/operations.py`, add `forget_resume_signals(item_id)` dropping both caches' entries for that item, and extend `clear_resume_signal_cache` to clear both dicts.
- [ ] T027 [US2] In `src/robot_army/web/server.py`, call `operations.forget_resume_signals(entity_id)` from `_perform` after the body succeeds, when `entity_type` is `work_item`, with a comment naming it as the single choke point every `POST` passes through.
- [ ] T028 [US2] In `src/robot_army/web/pages.py`, pass `local_signals_age_seconds` through `_signal_row` and render it in `_signals_cell` as its own footnote, with a comment stating why it is a second line rather than merged with the GitHub age.
- [ ] T029 [US2] In `tests/conftest.py`, confirm the three web fixtures' `clear_resume_signal_cache()` calls now clear both caches, and add a comment saying so — a local cache surviving between tests would make them order-dependent.

**Checkpoint**: US2 complete — quickstart check 2 passes; `/interrupted` and `/item/<id>` show
the checkout-signal age.

---

## Phase 5: User Story 3 — A log query that matches nothing stays cheap (Priority: P3)

**Goal**: no audit file is read whole; a request stops after 8 MiB and says so, and the stop is
a resumable page boundary rather than a dead end.

**Independent Test**: build a log directory of several large daily files, request a page with a
filter matching nothing, and assert `bytes_scanned <= LOG_SCAN_BUDGET_BYTES`, `truncated` true,
and that following `next_cursor` continues rather than restarting.

### Tests for User Story 3

- [ ] T030 [P] [US3] In `tests/unit/test_web_log.py`, add a case asserting a file is never read whole: monkeypatch `Path.read_text` to fail and assert a page still renders, with a docstring naming the allocation the old implementation made.
- [ ] T031 [P] [US3] In `tests/unit/test_web_log.py`, add cases for the block boundary: a record longer than `LOG_SCAN_BLOCK_BYTES` is returned whole, and a file whose size is an exact multiple of the block size reads correctly — the two off-by-one shapes a backwards block reader gets wrong.
- [ ] T032 [P] [US3] In `tests/unit/test_web_log.py`, add a case asserting a partially written final line is still counted as one unparseable line and reported, unchanged from today — the interruption path the constitution requires be tested.
- [ ] T033 [P] [US3] In `tests/unit/test_web_log.py`, add cases for the byte budget: a filter matching nothing across files summing to more than the budget returns `truncated: true`, `has_more: true`, a `next_cursor`, and `bytes_scanned <= LOG_SCAN_BUDGET_BYTES`; and following that cursor returns records the first page did not.
- [ ] T034 [P] [US3] In `tests/unit/test_web_log.py`, add a case asserting a cursor in the old `{"f","n"}` shape restarts from the newest page rather than erroring (Principle V, and `_decode_cursor`'s documented behaviour).
- [ ] T035 [P] [US3] In `tests/unit/test_web_log.py`, add a case asserting a cursor whose offset is `0` advances to the previous file rather than re-reading the one it names.
- [ ] T036 [P] [US3] In `tests/unit/test_web_log.py`, add a case asserting an append to today's file between two page requests does not cause a record to repeat — the property the byte offset buys over the match count.
- [ ] T037 [P] [US3] In `tests/unit/test_web_log.py`, add a case asserting `/log` renders the truncation notice when `truncated` is true and does not when it is false, so an empty page is never mistaken for an empty history.
- [ ] T038 [US3] In `tests/unit/test_web_read_cost.py`, add the measured test for SC-005: a log directory of at least 100 MB, a filter matching nothing, completing under two seconds with `bytes_scanned` inside the budget.

### Implementation for User Story 3

- [ ] T039 [US3] In `src/robot_army/operations.py`, add `_read_lines_backwards(path, *, end_offset, block=LOG_SCAN_BLOCK_BYTES)` yielding `(line, start_offset)` newest-first over `[0, end_offset)`, carrying the partial line at the front of each block into the next, with a docstring stating why blocks rather than `mmap` (R4: the newest file is being appended to while it is read).
- [ ] T040 [US3] In `src/robot_army/operations.py`, rewrite `_scan_file_backwards` on top of it: take `end_offset` and a byte budget, return `(records, next_end_offset, skipped, bytes_read)`, drop the `skip`/`matched` machinery, and keep the `OSError` path that counts a file that vanished between the glob and the read.
- [ ] T041 [US3] In `src/robot_army/operations.py`, change `_encode_cursor`/`_decode_cursor` to the `{"f","b"}` payload per `data-model.md` §3, keeping the "unreadable restarts from the newest page" behaviour and extending its docstring to say a cursor from the previous version is exactly that case.
- [ ] T042 [US3] In `src/robot_army/operations.py`, rewrite `read_log_page`'s file loop to carry a byte budget across files, resume at the cursor's offset, advance to the previous file when an offset reaches `0`, and add `truncated` and `bytes_scanned` to the returned payload.
- [ ] T043 [US3] In `src/robot_army/web/pages.py`, render the truncation notice in `log_view` above the "older records →" link when `payload["truncated"]`, naming the budget in the message.

**Checkpoint**: US3 complete — quickstart check 3 passes; the existing paging and filtering tests
pass unchanged except where the cursor shape is the subject.

---

## Phase 6: User Story 4 — One machine observation per rendered page (Priority: P3)

**Goal**: a render observes the machine once, and the two places that show capacity read the
same observation.

**Independent Test**: render `/queue` and assert exactly one `capacity.snapshot`, and that the
chrome's capacity block and the queue body's capacity block are equal.

### Tests for User Story 4

- [ ] T044 [P] [US4] In `tests/unit/test_web_read_cost.py`, add cases asserting exactly one `capacity.snapshot` per render for `/queue`, `/active`, `/interrupted` and `/item/<id>`, and **zero** for a `404`, a `405`, and a static asset.
- [ ] T045 [P] [US4] In `tests/unit/test_web_render.py`, add a case asserting the chrome's capacity block and `/queue`'s own capacity block report identical numbers in the `?json` representation — the correctness half of this story, not just the cost half.
- [ ] T046 [P] [US4] In `tests/unit/test_web_render.py`, add a case asserting `pages.chrome` and `pages.queue_view` still work when called directly with no snapshot supplied, so the `None` default is covered rather than assumed.

### Implementation for User Story 4

- [ ] T047 [US4] In `src/robot_army/web/pages.py`, give `chrome` a `capacity: CapacitySnapshot | None = None` keyword, computing one only when not given, with a comment explaining that in a served request it is always supplied and the default exists for direct callers.
- [ ] T048 [US4] In `src/robot_army/web/pages.py`, give `queue_view` the same keyword and use it for both `ordering_mod.plan(...)` and the rendered capacity block.
- [ ] T049 [US4] In `src/robot_army/web/server.py`, compute the snapshot once in `handle` beside `level` and `include_simulated`, hand it to `pages.chrome`, and put it in the handler `params` under `"capacity"`; extend the existing "resolved once, here" comment to cover it, since the argument it already makes about the effect level is the same argument.
- [ ] T050 [US4] In `src/robot_army/web/server.py`, pass `params["capacity"]` from `view_queue` into `pages.queue_view`, alongside the `chrome_payload` it already threads for the same reason.

**Checkpoint**: all four stories independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T051 [P] Update `README.md`'s "Read this part" list: amend the same-origin bullet to say the check now covers reads as well as state changes and why absent headers are still allowed; amend the connection-bounds bullet to mention `refused_cross_site` beside `refused_over_capacity` in `web.stop`; and add one bullet stating what a read now costs — one capacity observation, one checkout observation per item per five seconds, at most 8 MB of audit log per page — with the two constants' location named.
- [ ] T052 [P] Update `docs/state.md` where it says the local resume signals are "computed on demand and never stored", so the document and the code agree about the five-second reuse and the visible age.
- [ ] T053 Run `uv run ruff check src tests` and `uv run ruff format --check src tests`; fix anything raised.
- [ ] T054 Run the full suite, `uv run pytest`, and confirm every test passes with none skipped or xfailed by this feature.
- [ ] T055 Walk `quickstart.md` checks 1 through 5 against a live `robot-army serve` and correct any command or expected output that does not match what actually happens.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001)**: no dependencies.
- **Foundational (T002–T004)**: after Setup. Blocks US2 (needs T002, T004) and US3 (needs T003).
  US1 and US4 do not depend on it and could start immediately, but the phase is small enough
  that ordering around it costs more than doing it.
- **US1 (T005–T014)**: after Foundational. Independent of US2, US3, US4.
- **US2 (T015–T029)**: after Foundational. Independent of US1, US3, US4.
- **US3 (T030–T043)**: after Foundational. Independent of US1, US2, US4.
- **US4 (T044–T050)**: after Foundational. Independent of US1, US2, US3.
- **Polish (T051–T055)**: after every story that is going to ship.

### Within Each Story

Tests are written first and seen to fail, then the implementation. Inside the implementation
blocks the order is: constants and data structures, then the function that uses them, then the
call site, then the rendering.

Two ordering constraints are real rather than conventional:

- T013 depends on T011 and T012 — it calls the generalised function and increments the counter.
- T042 depends on T039, T040 and T041 — it is the loop that drives all three.

### Parallel Opportunities

- T002 and T003 are different constants in the same module and can be written together; T004
  touches the remote cache and should follow them.
- Within each story, every task marked `[P]` is a different file or a different test module and
  can be written in parallel. The unmarked ones inside a story touch a file another task in that
  story also touches.
- The four stories touch overlapping files (`server.py` for US1 and US4, `operations.py` for
  US2 and US3), so they parallelise cleanly by *person* only if each takes a whole story.

---

## Parallel Example: User Story 1

```bash
# The refusal tests and the honest-path tests are independent files or independent
# functions in one file; write them together:
Task: "T005 cross-site read refusals in tests/unit/test_web_routing.py"
Task: "T006 the three honest read paths in tests/unit/test_web_routing.py"
Task: "T007 origin/host mismatch and same-site in tests/unit/test_web_routing.py"
Task: "T009 a cross-site POST still leaves its audit pair in tests/unit/test_web_actions.py"
Task: "T010 one real-socket round-trip in tests/integration/test_web_end_to_end.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. T001 (Setup) → T002–T004 (Foundational) → T005–T014 (US1).
2. **STOP and validate**: quickstart check 1 end to end.
3. At this point RA-14's security half is closed. A visited page gets a `403` before any work
   happens, so the three remaining stories are about the maintainer's own page being cheap
   rather than about an attacker — which is why they are P2 and P3, not P1.

### Incremental Delivery

1. Setup + Foundational → ready.
2. US1 → the finding is closed → validate with quickstart 1.
3. US2 → the busiest read view stops forking `git` per card → validate with quickstart 2.
4. US3 → the log stops reading whole files → validate with quickstart 3.
5. US4 → one machine observation per page → validate with quickstart 4.
6. Polish → documentation, lint, full suite, quickstart walk.

Each step leaves the tree green and every previous step working.

---

## Notes

- `[P]` means a different file with no dependency on an incomplete task.
- Commit at each checkpoint at the latest; the constitution wants atomic commits whose messages
  explain *why*, and each phase here has one reason.
- Two rules must survive this feature intact and are asserted rather than assumed: a cross-site
  `POST` keeps its audit pair (T009), and an unfiltered first page of `/log` returns exactly the
  records it returns today (existing tests in `tests/unit/test_web_log.py`, unmodified).
- No task adds a configuration key, a dependency, a migration, or a route.
