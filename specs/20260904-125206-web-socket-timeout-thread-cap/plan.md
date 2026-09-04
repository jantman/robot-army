# Implementation Plan: Bounded waits and bounded concurrency for the web interface

**Branch**: `speckit/20260904-125206-web-socket-timeout-thread-cap` | **Date**: 2026-09-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/20260904-125206-web-socket-timeout-thread-cap/spec.md`

## Summary

The web interface speaks HTTP/1.1 with keep-alive, never sets a socket timeout, and starts an
unbounded thread per connection — and every request it serves opens its own SQLite connection
and audit-log file handle. A client that connects and goes quiet therefore pins a thread, a
socket, and (once routed) two more descriptors, permanently. Descriptor exhaustion arrives
long before memory does, and when it does the interface stops rendering at exactly the moment
it is worth having.

Two bounds fix it, both small:

1. **`Handler.timeout = REQUEST_TIMEOUT_SECONDS`** (15). `StreamRequestHandler.setup()` turns
   that one attribute into `settimeout()` on the connection, which bounds every wait —
   request line, headers, body, the keep-alive gap, and response writes — and
   `BaseHTTPRequestHandler.handle_one_request` already closes the connection quietly on
   `TimeoutError`. One attribute, no new code paths (research R1, R2).
2. **A counted admission gate at `process_request`** on a `ThreadingHTTPServer` subclass. At
   `MAX_CONCURRENT_CONNECTIONS` (32) in flight, a newly accepted connection gets a pre-built
   `503` written with one non-blocking `send`, then is closed — before any thread is started,
   so before any `Context`, SQLite connection, or audit handle exists (research R4, R5).

Accounting is deliberately cheap: one stderr line per saturation episode, and a cumulative
count folded into the existing `web.stop` audit record. Per-refusal audit records would open
the very resources this feature bounds, so they are an enumerated Principle III exception
(research R7, and the Constitution Check below).

Per-worker connection pooling — the issue's third suggestion — is out of scope. The cap is
what made the per-request cost safe; a pool would add moving parts to solve a closed problem.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: standard library only — `http.server`, `socketserver`, `socket`,
`threading`. No new third-party dependency, and none is needed.

**Storage**: unchanged. No schema change, no new file, no new configuration key. The only
persistence touched is one added field inside the existing `web.stop` audit record.

**Testing**: `pytest`, run as `uv run pytest`. Two new modules —
`tests/unit/test_web_connection_limits.py` and
`tests/integration/test_web_connection_limits.py` — with the integration module binding an
ephemeral port in the shape `tests/integration/test_web_end_to_end.py` already uses.

**Target Platform**: one Linux machine, loopback by default (`127.0.0.1:8420`).

**Project Type**: single Python package (`src/robot_army`) with a CLI and a small web
interface. No frontend build, no service tier.

**Performance Goals**: none, in the throughput sense — the module docstring's own "not because
there is load to serve" still holds. The goal is a *ceiling*: at saturation, roughly 32
sockets plus at most 32 SQLite connections plus at most 32 audit handles, under 100
descriptors against a typical `RLIMIT_NOFILE` of 1024.

**Constraints**: the refusal path runs on the accept loop's own thread, so it must not block;
it must not allocate a `Context`; and it must remain correct while other threads mutate the
in-flight count.

**Scope/Scale**: about 80 lines in one module (`src/robot_army/web/server.py`), two test
modules, and one README paragraph.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design — see the re-check at the end.*

### I. Simplicity First (YAGNI & KISS)

**Pass.** The whole feature is one class attribute, one `ThreadingHTTPServer` subclass holding
an integer and a lock, and one pre-built byte string. No new dependency: `socket` and
`threading` are already imported by this module. Three simplifications were chosen
deliberately and are recorded in `research.md`:

- No worker pool and no queue (R4) — a counter and a lock, because refusing is the right
  answer when there is no load to serve.
- No per-worker connection pooling (spec Assumptions) — the cap already bounds what pooling
  would have bounded.
- No `handle_timeout` implementation (R1) — it is a `BaseServer` method that `serve_forever()`
  never calls, so writing one would be dead code.

**FR-012 is a constitutional requirement, not a preference**: the bound and the cap are module
constants, not `[web]` configuration keys. Each would have exactly one caller and no second
use in hand.

### II. Single-User, Local-First

**Pass.** Nothing here assumes more than one user or reaches off the machine. It adds no
account, no authentication, and no authorization; the bind address remains the access policy,
exactly as FR-003 of milestone 002 requires. No hosted service, no network dependency, no new
persistent state, no secret.

### III. Total Accountability

**Pass, with one enumerated exception.**

**What this logs.** The `web.start` and `web.stop` records are unchanged in every other
respect; `web.stop` gains `detail.refused_over_capacity`, the number of connections this run
turned away. Every request that is actually served logs exactly what it logs today — no
audit path for an admitted request changes. Reaching capacity prints to stderr once per
saturation episode.

**The enumerated exception (required by Governance and by Principle III's exception path).**
*Individual connection refusals are not written to the audit log, and neither are individual
connection timeouts.* Both are, on their face, "an error response was returned" (milestone
002's FR-039/FR-040). They go unrecorded because writing an audit record requires building a
`Context`, which opens a SQLite connection and an audit-log file handle — the precise pair of
descriptors this feature exists to bound. A per-refusal record would make the log the
amplifier of the flood it documents, and would fail under exactly the descriptor exhaustion it
was meant to describe. The risk this trades away is small and bounded: a refusal changes no
state, touches no data, and is fully summarised by a count. The reconstruction standard is
still met at the granularity that matters — from `web.start`, `web.stop`, and the count
between them, the log answers "did this run turn connections away, and how many". What it
cannot answer is which connection at which millisecond.

**Not a silent failure.** `handle_error` suppression (research R8) is limited to
`TimeoutError`, `ConnectionResetError`, and `BrokenPipeError` — three exception types that all
mean "the connection ended". Every other exception still prints its traceback exactly as it
does today, because the ban on swallowed exceptions is about *our* failures, and a client
hanging up is not one.

### IV. Interruption Tolerance

**Pass.**

**What happens if it is killed halfway through.** Nothing is lost, because nothing here is
persistent: the counter, the saturation flag, and the refusal total live only in the running
process and are meaningless after it. A `SIGTERM` mid-flood takes the same path it takes
today. The one thing that improves is shutdown itself: today a wedged connection can hold a
handler thread with no bound, and the timeout puts a 15-second ceiling on it.

The principle's "every network call MUST set an explicit timeout ... unbounded retry loops and
indefinite blocking are forbidden" is the clause this feature exists to satisfy. It is the
server side of the same rule, and it was the one place in the codebase still blocking
indefinitely.

**Atomicity** is not engaged: no file is written. The counter's own consistency is held by a
lock covering nothing but the arithmetic — never a socket operation, never a thread start —
so it cannot deadlock and cannot be left inconsistent by a failure inside a handler
(the decrement is in a `finally`, FR-008).

### V. Public Code, Unsupported Project

**Pass.** No credential, no hostname, no personal data. Nothing published, nothing packaged.
Changing a constant later is a breaking change to nobody, which is why the constants are
constants. The README gains a short paragraph written for the author's future self: what the
two numbers are and what a `503` from this interface means.

### Development Workflow

**Pass.** This plan carries the Constitution Check; unit tests accompany every changed unit of
behaviour; the code being changed parses external input and is exactly the "failure and
interruption paths, not only success paths" case the workflow section calls out, which is why
the tests drive silence, partial requests, over-declared bodies, and saturation rather than
only the happy path.

## Project Structure

### Documentation (this feature)

```text
specs/20260904-125206-web-socket-timeout-thread-cap/
├── plan.md                          # This file
├── spec.md                          # Requirements (FR-001..FR-015, SC-001..SC-009)
├── research.md                      # Phase 0: R1..R9, every decision and what was rejected
├── data-model.md                    # Phase 1: constants, in-process state, the changed record
├── quickstart.md                    # Phase 1: five by-hand validations
├── contracts/
│   └── connection-limits.md         # Phase 1: C1..C4, the client-visible contract
├── checklists/
│   └── requirements.md              # Spec quality checklist
└── tasks.md                         # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
src/robot_army/web/
└── server.py            # the only source file changed
                         #   + REQUEST_TIMEOUT_SECONDS, MAX_CONCURRENT_CONNECTIONS,
                         #     OVER_CAPACITY_RESPONSE   (module constants, FR-012)
                         #   + BoundedThreadingHTTPServer(ThreadingHTTPServer)
                         #       process_request         — the admission gate (FR-004..FR-006)
                         #       process_request_thread  — releases the slot (FR-008)
                         #       handle_error            — connection endings are not errors
                         #   ~ Handler.timeout           — the bound (FR-001..FR-003)
                         #   ~ build_server              — builds the bounded class, v4 and v6
                         #   ~ serve                     — refusal count into web.stop (FR-010),
                         #                                 and two comments corrected (R6)

tests/
├── unit/
│   └── test_web_connection_limits.py       # constants, refusal bytes, counter arithmetic
└── integration/
    └── test_web_connection_limits.py       # a real port: silence, partial request,
                                            # over-declared body, saturation, release

README.md                # one paragraph: the two bounds and what a 503 means
```

**Structure Decision**: single project, existing layout, one source module touched. The web
package is already split `server.py` (sockets and routing) / `pages.py` (views) / `html.py`
(rendering); both bounds are socket-level, so `server.py` is the only correct home and no new
module is justified.

## Complexity Tracking

No Constitution Check violation to justify. The one judgement worth recording is not a
violation but a departure from the issue's own suggestion:

| Decision | Issue suggested | Chosen | Why |
|---|---|---|---|
| Concurrency cap | 16 ("generous") | 32 | A browser opens up to six connections per origin and this page refreshes itself on a timer, so a few tabs can hold a dozen or more live connections; a false `503` on the operator's own page would be a worse bug than the one being fixed. 32 still bounds saturation to under 100 descriptors against a typical limit of 1024 — the ceiling does its job with an order of magnitude to spare. |

## Post-Design Constitution Re-Check

Re-evaluated against the Phase 1 artifacts (`data-model.md`, `contracts/connection-limits.md`,
`quickstart.md`).

- **I. Simplicity First** — the design added nothing beyond what the summary promised: three
  constants, four fields, three overridden methods. `data-model.md` has no persistent entity
  and `contracts/` has no new endpoint. Still passing.
- **II. Single-User, Local-First** — the contract explicitly restates that this is not
  authentication and not a per-client limit; nothing is keyed by source address, so no
  per-client state exists to grow. Still passing.
- **III. Total Accountability** — the exception did not widen during design. It remains
  exactly two things: no record per refusal, no record per timeout. `contracts/` C3 states in
  a table what the operator sees for every event, including the ones that produce nothing,
  so the gap is documented where a reader will find it. Still passing.
- **IV. Interruption Tolerance** — design confirmed the slot release must cover completion,
  timeout, client disconnect, and unhandled failure alike; that became FR-008 and a `finally`
  in the structure above. Shutdown is bounded rather than unbounded as a side effect. Still
  passing.
- **V. Public Code** — no artifact contains a credential, a hostname, or personal data; the
  quickstart uses the shipped defaults only. Still passing.

**Gate: passed.** Ready for `/speckit-tasks`.
