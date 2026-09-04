---

description: "Task list for bounded waits and bounded concurrency in the web interface"
---

# Tasks: Bounded waits and bounded concurrency for the web interface

**Input**: Design documents from `/specs/20260904-125206-web-socket-timeout-thread-cap/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/connection-limits.md](contracts/connection-limits.md),
[quickstart.md](quickstart.md)

**Tests**: Required, and not optional here. The constitution's Development Workflow says
"Every new or changed unit of behavior MUST ship with unit tests", and adds that "code parsing
external input MUST additionally carry tests exercising their failure and interruption paths".
This feature is entirely about the failure paths of code parsing external input, so the tests
are the deliverable as much as the code is (spec FR-015).

**Organization**: Grouped by the three user stories in `spec.md`. US1 (the bound) and US2 (the
cap) are independently implementable and independently valuable — shipping only US1 is a real
fix. US3 (the record) depends on US2 having something to count.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel — different files, no dependency on an incomplete task.
- **[Story]**: `US1`, `US2`, `US3`; absent for setup, foundational, and polish tasks.
- Every task names the exact file it touches.

## Path Conventions

Single Python project. Source at `src/robot_army/`, tests at `tests/unit/` and
`tests/integration/`. The only source file this feature changes is
`src/robot_army/web/server.py`.

---

## Phase 1: Setup

**Purpose**: Confirm the ground is where the plan says it is before changing it.

- [ ] T001 Verify the working tree is on `speckit/20260904-125206-web-socket-timeout-thread-cap` and that `uv run pytest -q` passes on the untouched tree, so any later failure is attributable to this feature (repository root)
- [ ] T002 Confirm the three upstream facts the design rests on, by reading `/usr/lib/python3.14/socketserver.py` (`StreamRequestHandler.setup` sets the socket timeout only when `timeout is not None`; `_Threads.append` drops daemon threads) and `/usr/lib/python3.14/http/server.py` (`handle_one_request` catches `TimeoutError` around `method()`); if any differs from [research.md](research.md) R1/R2/R6, stop and revise the plan before writing code

**Checkpoint**: A green baseline and a verified set of assumptions.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The module constants and the server subclass both stories build on.
No user story work can begin until this phase is complete.

- [ ] T003 Add the three module constants to `src/robot_army/web/server.py`, beside the existing `MAX_BODY_BYTES` block and in the same commented style: `REQUEST_TIMEOUT_SECONDS = 15`, `MAX_CONCURRENT_CONNECTIONS = 32`, and `OVER_CAPACITY_RESPONSE` built from the `503` in [contracts/connection-limits.md](contracts/connection-limits.md) C2 (compute `Content-Length` from the body rather than hard-coding it, so the two cannot drift)
- [ ] T004 Add `class BoundedThreadingHTTPServer(ThreadingHTTPServer)` to `src/robot_army/web/server.py` with `__init__` initialising `_capacity_lock`, `_in_flight = 0`, `_saturated = False`, and `refused_over_capacity = 0` per [data-model.md](data-model.md), and a class docstring stating why the count is per connection rather than per request
- [ ] T005 Change `build_server` in `src/robot_army/web/server.py` to base both the IPv4 and the IPv6 server class on `BoundedThreadingHTTPServer` instead of `ThreadingHTTPServer`, leaving the address-family branch and its docstring otherwise untouched

**Checkpoint**: The server is the bounded class everywhere it is constructed, with the counter present and unused. Behaviour is unchanged, and the suite still passes.

---

## Phase 3: User Story 1 — The interface survives connections that go quiet (Priority: P1) 🎯 MVP

**Goal**: A client that connects and says nothing, or stops mid-request, or promises a body it
never sends, is given up on within 15 seconds and its resources returned.

**Independent Test**: Open a connection to a live server, send nothing (or a partial request),
and observe the server close it within the bound; then serve a normal request over a new
connection and see it succeed unchanged.

### Tests for User Story 1

- [ ] T006 [P] [US1] Create `tests/integration/test_web_connection_limits.py` with a `live_server` fixture modelled on the one in `tests/integration/test_web_end_to_end.py`, parameterised so the test module can override `REQUEST_TIMEOUT_SECONDS` and `MAX_CONCURRENT_CONNECTIONS` via `monkeypatch` before `build_server` is called, and a module docstring saying why a real socket is required here
- [ ] T007 [US1] Add to `tests/integration/test_web_connection_limits.py` a test that connects and sends nothing, asserting the server closes the connection (a `recv` returning `b""`) within a short overridden bound and comfortably before a generous ceiling — spec scenario 1, SC-001
- [ ] T008 [P] [US1] Add to `tests/integration/test_web_connection_limits.py` a test that sends a partial request line and asserts the connection is closed within the bound — spec scenario 2
- [ ] T009 [P] [US1] Add to `tests/integration/test_web_connection_limits.py` a test that sends a complete `POST` declaring a `Content-Length` far larger than the bytes it then sends, and asserts the connection is closed within the bound rather than held — spec scenario 3, FR-002
- [ ] T010 [P] [US1] Add to `tests/integration/test_web_connection_limits.py` a test that completes one request over a keep-alive connection, then goes silent, asserting the idle connection is closed within the bound — spec scenario 4
- [ ] T011 [P] [US1] Add to `tests/integration/test_web_connection_limits.py` a test that a normal request served over a connection that stays inside the bound returns exactly the status, headers, and body it returns today — spec scenario 5, FR-007
- [ ] T012 [P] [US1] Add to `tests/integration/test_web_connection_limits.py` a test asserting that a timed-out connection writes nothing to `stderr` — no traceback, no line — using `capsys` or a captured stream, per FR-003 and contract C1

### Implementation for User Story 1

- [ ] T013 [US1] Set `timeout = REQUEST_TIMEOUT_SECONDS` on `Handler` in `src/robot_army/web/server.py`, beside `protocol_version`, with a comment naming the mechanism (`StreamRequestHandler.setup` → `settimeout`) and the reason keep-alive makes it mandatory rather than optional — FR-001, FR-002
- [ ] T014 [US1] Confirm the dynamically built `BoundedHandler` subclass in `build_server` (`type("BoundedHandler", (Handler,), {"app": app})`) inherits `timeout` rather than shadowing it, in `src/robot_army/web/server.py`; the tests in T007–T010 are the check
- [ ] T015 [US1] Add `handle_error` to `BoundedThreadingHTTPServer` in `src/robot_army/web/server.py`, returning silently for `TimeoutError`, `ConnectionResetError`, and `BrokenPipeError` and delegating to `super().handle_error` for everything else, with a comment stating that the ban on silent failure is about our failures and a client hanging up is not one — FR-003, research R8

**Checkpoint**: The slowloris is closed. This alone is a shippable fix; stop here and validate with quickstart steps 1 and 2 if desired.

---

## Phase 4: User Story 2 — A flood is refused rather than absorbed (Priority: P2)

**Goal**: The interface serves at most `MAX_CONCURRENT_CONNECTIONS` connections at once and
turns the rest away immediately, without starting a thread or opening a database connection
for them.

**Independent Test**: With the cap overridden to a small number, open more simultaneous
connections than the cap; connections beyond it receive an immediate `503` and are closed,
while those within it are served normally.

### Tests for User Story 2

- [ ] T016 [P] [US2] Create `tests/unit/test_web_connection_limits.py` asserting the shape of `OVER_CAPACITY_RESPONSE` against contract C2 — the `503` status line, `Connection: close`, `Cache-Control: no-store`, `Retry-After`, the JSON content type, and a `Content-Length` that equals the actual body length
- [ ] T017 [P] [US2] Add to `tests/unit/test_web_connection_limits.py` a test driving `BoundedThreadingHTTPServer.process_request` with a fake socket object and a stubbed `process_request_thread`, asserting that admission increments the count, that the call beyond the cap sends the refusal bytes and never starts a thread, and that `refused_over_capacity` advances — FR-004, FR-006
- [ ] T018 [P] [US2] Add to `tests/unit/test_web_connection_limits.py` a test that a handler raising an exception still releases its slot, asserting `_in_flight` returns to zero — FR-008
- [ ] T019 [US2] Add to `tests/integration/test_web_connection_limits.py` a test that, with the cap overridden to a small number, holds that many connections open and asserts the next connection receives a well-formed `503` with `Connection: close` and is then closed — spec scenario 1, contract C2
- [ ] T020 [US2] Add to `tests/integration/test_web_connection_limits.py` a test that releasing one held connection lets a newly opened connection be served normally — spec scenario 2, SC-002
- [ ] T021 [P] [US2] Add to `tests/integration/test_web_connection_limits.py` a test asserting that a refused connection opens no database connection and no audit-log file handle — by counting `operations.build_context` calls (or the audit records written) across a saturation episode — spec scenario 3, FR-006
- [ ] T022 [P] [US2] Add to `tests/integration/test_web_connection_limits.py` a test that the existing `413` refusal of an over-large declared body still refuses without reading and still closes the connection — FR-014, guarding against a regression from the new body-read path

### Implementation for User Story 2

- [ ] T023 [US2] Implement `process_request` on `BoundedThreadingHTTPServer` in `src/robot_army/web/server.py`: under the lock, refuse when `_in_flight >= MAX_CONCURRENT_CONNECTIONS` (incrementing `refused_over_capacity`) or otherwise increment `_in_flight` and clear `_saturated`; outside the lock, either call the refusal helper or delegate to `super().process_request`, decrementing again if starting the thread raises — FR-004, FR-005, FR-008
- [ ] T024 [US2] Implement the refusal helper on `BoundedThreadingHTTPServer` in `src/robot_army/web/server.py`: set the socket non-blocking, one `send` of `OVER_CAPACITY_RESPONSE`, one `recv` to drain what the client already sent, then `shutdown_request` — every step guarded against `OSError`, with a comment explaining that this runs on the accept loop's own thread so it must never block, and that the drain is what keeps the close a FIN rather than an RST — FR-005, FR-006, research R5
- [ ] T025 [US2] Implement `process_request_thread` on `BoundedThreadingHTTPServer` in `src/robot_army/web/server.py` as a `try/finally` around `super().process_request_thread` that decrements `_in_flight` exactly once however the connection ended — FR-008

**Checkpoint**: Both bounds hold. Descriptors are bounded regardless of how many clients connect (SC-003).

---

## Phase 5: User Story 3 — The operator can tell that it happened (Priority: P3)

**Goal**: Saturation is visible on the terminal once per episode, and the run's total refusals
are recoverable from the audit log alone.

**Independent Test**: Drive a live server past its cap, stop it, and read back both the
captured terminal output and the `web.stop` audit record.

**Depends on**: US2 — there is nothing to report until there is a refusal to report.

### Tests for User Story 3

- [ ] T026 [P] [US3] Add to `tests/unit/test_web_connection_limits.py` a test that many refusals within one saturation episode produce exactly one terminal message, and that dropping below the cap and saturating again produces a second — spec scenario 1 and 3, FR-009
- [ ] T027 [US3] Add to `tests/integration/test_web_connection_limits.py` a test that a run which refused connections writes a `web.stop` audit record whose `detail` carries `refused_over_capacity` with the right count, and that an ordinary run records `0` — spec scenario 2, FR-010, SC-006
- [ ] T028 [P] [US3] Add to `tests/unit/test_web_connection_limits.py` a test asserting no audit record is written on the refusal path itself, holding the enumerated Principle III exception in place so a later change cannot reintroduce the amplifier by accident — FR-011

### Implementation for User Story 3

- [ ] T029 [US3] In the refusal path in `src/robot_army/web/server.py`, print `robot-army: at capacity (<N> connections); refusing new connections` to `stderr` on the transition into saturation only, setting `_saturated` under the same lock that decides admission — FR-009, contract C3
- [ ] T030 [US3] Extend the `web.stop` audit record in `serve()` in `src/robot_army/web/server.py` with `"refused_over_capacity": server.refused_over_capacity`, leaving every other field of the record untouched — FR-010, [data-model.md](data-model.md)

**Checkpoint**: All three stories functional. The feature is complete.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T031 Correct the two shutdown claims in `src/robot_army/web/server.py` that were already false: the `server_close()` comment saying it "Joins in-flight request threads" (it does not — `_Threads.append` drops daemon threads and `ThreadingHTTPServer` sets `daemon_threads = True`), and the SIGTERM stderr line promising to finish in-flight requests; say what actually happens, and that the new bound is what puts a ceiling on shutdown — research R6
- [ ] T032 [P] Extend the module docstring of `src/robot_army/web/server.py` so the two bounds join the three properties already listed there as requirements rather than implementation choices, since a future reader removing either would reopen RA-13
- [ ] T033 [P] Add a short paragraph to `README.md`, in the section covering the web interface, saying what the two numbers are, that a `503` from this interface means the connection cap and not a failure, and where the refusal count is recorded — written for the author's future self, per Principle V
- [ ] T034 Run `uv run pytest -q` and confirm the whole suite passes, including every pre-existing web test unchanged — SC-008, and the constitution's "implementation is not complete until the unit test suite passes"
- [ ] T035 Run `uv run ruff check` and `uv run ruff format --check` (or whatever the repository's configured lint entry point is) over the changed files and fix anything reported
- [ ] T036 Walk [quickstart.md](quickstart.md) steps 1 through 4 against a live `robot-army serve`, confirming each expected outcome including the descriptor count staying bounded under a held flood

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup. Blocks all three stories — both bounds need the constants, and US2 and US3 need the server subclass.
- **US1 (Phase 3)**: depends on Phase 2. Independent of US2 and US3.
- **US2 (Phase 4)**: depends on Phase 2. Independent of US1 — the two touch different methods of different classes.
- **US3 (Phase 5)**: depends on Phase 2 and on US2.
- **Polish (Phase 6)**: depends on every story intended to ship.

### Within Each User Story

Tests are written before the implementation they cover and must fail first — not as a
methodology, but because a test for "the connection closes eventually" that passes before the
timeout exists is a test of nothing.

### Parallel Opportunities

- T006 must land before T007–T012 (they all extend the same new file); once it has, T008–T012 are independent additions.
- T016, T017, T018 create and extend a different file from T019–T022 and can proceed alongside them.
- T013 and T015 (US1 implementation) touch different parts of `server.py` from T023–T025 (US2 implementation) and can be written in parallel, though they are best committed separately.
- T032 and T033 touch different files and are independent of each other.

## Parallel Example: User Story 1

```bash
# After T006 creates the module, these four are independent additions to it:
Task: "T008 partial request line is closed within the bound"
Task: "T009 over-declared Content-Length is closed within the bound"
Task: "T010 idle keep-alive connection is closed within the bound"
Task: "T011 a normal request is unchanged"
```

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 (Setup) → Phase 2 (Foundational) → Phase 3 (US1).
2. **Stop and validate**: quickstart steps 1 and 2.
3. This is already the fix for the reported defect's cheapest exploit and the only one an accident triggers. It is shippable on its own.

### Incremental Delivery

1. Setup + Foundational → the constants and the bounded server exist, behaviour unchanged.
2. US1 → the bound → validate → the slowloris is closed.
3. US2 → the cap → validate → the resource ceiling is a fact rather than a race.
4. US3 → the record → validate → the defence stops being invisible.
5. Polish → the false comments corrected, the README paragraph, the full suite, the quickstart.

### Commit Strategy

One commit per phase at least, and per logical group within a phase where the phases are
large. Messages explain why, not what, per the constitution's Development Workflow.

## Notes

- `[P]` means a different file and no dependency on an incomplete task.
- The two module constants exist partly so the tests can shrink them; a test that has to sleep
  15 seconds to prove a 15-second bound is a test nobody will keep.
- Do not add a `[web]` configuration key for either number (FR-012). If a future need appears,
  that is a new feature with a new plan.
- Do not implement `handle_timeout`; it is a `BaseServer` method `serve_forever()` never calls
  (research R1).
