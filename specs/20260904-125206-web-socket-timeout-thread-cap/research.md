# Research: bounded waits and bounded concurrency for the web interface

**Feature**: `specs/20260904-125206-web-socket-timeout-thread-cap`
**Date**: 2026-09-04

Everything below was checked against the interpreter this project actually requires
(`requires-python = ">=3.14"`; the machine here runs CPython 3.14.7) and against the code in
`src/robot_army/web/server.py` as of `b1aa08d`.

---

## R1 — Where the unbounded wait actually lives

**Decision**: Set the class attribute `timeout` on `Handler`. Do not implement
`handle_timeout`.

**Rationale**: `socketserver.StreamRequestHandler.setup()` (3.14, line 809) reads:

```python
def setup(self):
    self.connection = self.request
    if self.timeout is not None:
        self.connection.settimeout(self.timeout)
```

`timeout` defaults to `None` (line 803) and `http.server` never overrides it, so the
connection socket has no timeout and every `rfile.readline()` / `rfile.read(n)` /
`wfile.write()` blocks indefinitely. Setting `Handler.timeout` calls `settimeout` on the
connection, which makes *every* socket operation for that connection — request line,
headers, body, the keep-alive wait for the next request, and response writes — raise
`TimeoutError` after that many seconds of no progress. That covers FR-002 in one line, with
no per-call-site changes.

`handle_timeout` — which the issue suggests implementing — is a method of
`socketserver.BaseServer`, not of the request handler, and it is only ever called from
`BaseServer.handle_request()`. This server runs `serve_forever()`, which does not call
`handle_request()`, so a `handle_timeout` would never fire. There is nothing to implement
there, and implementing it would be dead code the constitution's Principle I forbids.

**Alternatives considered**:

- *Per-read deadlines around each `rfile` call.* Rejected: it only covers the call sites this
  module wrote, and misses `readline` for the request line and headers inside
  `BaseHTTPRequestHandler`, which is where the cheapest slowloris lives.
- *`socket.setdefaulttimeout()`.* Rejected: process-global, so it would silently apply to the
  GitHub client, Trello, and the health socket. A defence that reaches that far is a bug
  waiting for a slow API.

## R2 — The timeout is already handled cleanly upstream

**Decision**: Rely on `BaseHTTPRequestHandler.handle_one_request`'s existing `TimeoutError`
handler; add nothing.

**Rationale**: `handle_one_request` (3.14 `http/server.py`, line 459) wraps the whole request
— including the `method()` call, and therefore this module's `rfile.read(length)` for POST
bodies — in:

```python
except TimeoutError as e:
    self.log_error("Request timed out: %r", e)
    self.close_connection = True
    return
```

`log_error` delegates to `log_message`, which `Handler` already overrides to a no-op, so a
timed-out connection closes quietly with no traceback and no stderr line — exactly FR-003.
A timed-out write is caught by the same clause, and `StreamRequestHandler.finish()` already
swallows a socket error on its final flush (`TimeoutError` is a subclass of `OSError`).

## R3 — Timeout value: 15 seconds

**Decision**: `REQUEST_TIMEOUT_SECONDS = 15`.

**Rationale**: The number has to sit above the page's own refresh cadence and below anything
a human would call "stuck". `WebConfig.refresh_seconds` defaults to 10, so 15 leaves an
idle-but-live browser connection alone between refreshes rather than churning it. It also
bounds the shutdown path (R6). Nothing about the value is delicate: any value in roughly
10–60 seconds closes the same hole.

**Alternatives considered**: 5 seconds (churns keep-alive connections against a 10-second
refresh for no gain); 60 seconds (a wedged connection outlives most of the incidents the
interface exists to be readable during).

## R4 — Cap the connections, not the requests

**Decision**: Bound the number of simultaneously *served connections* at
`MAX_CONCURRENT_CONNECTIONS = 32`, admitted or refused at `process_request`.

**Rationale**: `ThreadingMixIn.process_request` starts one thread per accepted connection and
`BaseHTTPRequestHandler.handle()` loops on that one thread for the life of a keep-alive
connection. The thread, the socket, and the descriptors are therefore per *connection*, not
per request — so the connection is the thing to count. Counting requests would leave an idle
keep-alive connection uncounted while it still holds a thread.

Refusing at `process_request` rather than inside the handler is what makes FR-006 true: the
thread is never started, so `WebApp.context()` is never called, so no SQLite connection and no
`AuditLog` file handle are opened for a refused connection.

**Why 32 and not the 16 the issue proposed**: 16 is a defensible ceiling for the attack and a
thin one for ordinary use. A browser opens up to six connections per origin, and this page
refreshes itself on a timer, so several tabs on the interface can plausibly hold a dozen or
more live connections between them; a false `503` on the operator's own page would be a worse
bug than the one being fixed. At 32 the descriptor cost at full saturation is roughly 32
sockets plus at most 32 SQLite connections plus at most 32 audit file handles — under 100
descriptors against a typical `RLIMIT_NOFILE` of 1024, so the ceiling still does its job with
an order of magnitude to spare. The value is a module constant with a comment saying exactly
this; moving it is a one-line edit.

**Alternatives considered**:

- *A bounded worker pool with a queue.* Rejected under Principle I: it replaces a counter and
  a lock with a pool, a queue, and a shutdown protocol, and it converts a fast, honest refusal
  into a slow, ambiguous wait. There is no load to serve here.
- *Per-source-address limits.* Rejected: the whole reported attack arrives from one address
  (the loopback interface, driven by a browser), so an address-keyed limit defends nothing and
  adds state.

## R5 — Delivering the refusal without becoming the next bottleneck

**Decision**: On refusal, put the socket in non-blocking mode, `send` one pre-built byte
string, make one non-blocking `recv` to drain whatever the client already sent, then
`shutdown_request`. Every step is wrapped and every socket error is ignored.

**Rationale**: `process_request` runs on the accept loop's own thread. A blocking `sendall`
to a client that never reads would stall accepts for every other client — the refusal would
become the denial of service. Non-blocking is O(1): the refusal is about 200 bytes and fits in
any socket send buffer, so in practice it goes out in one call, and if it would block we drop
it and close, which is the outcome that matters.

The single drain `recv` is why the response usually arrives intact: closing a socket that
still has unread data in its receive queue makes Linux send an RST, and an RST can cause the
peer to discard the response bytes it has already buffered. Draining first means the close is
an ordinary FIN. Delivery remains best-effort by design — under a flood the point is to
release the descriptor, not to guarantee the attacker parses our JSON — and the spec's
acceptance test connects without sending, which is both the deterministic case and the honest
one.

**Alternatives considered**: `sendall` with a one-second socket timeout (stalls the accept
loop, one second per refused connection); closing with no response at all (a browser then
shows an opaque connection failure instead of a 503, and FR-005 asks for the refusal).

## R6 — What the timeout also fixes, and one comment that was already wrong

**Decision**: Correct the shutdown comment in `serve()` while changing the code it describes.

**Rationale**: `serve()` currently says `server_close()` "Joins in-flight request threads."
It does not. `ThreadingMixIn.process_request` only records a thread when `block_on_close` is
true, and `_Threads.append` (3.14 `socketserver.py`, line 649) returns early for daemon
threads:

```python
def append(self, thread):
    self.reap()
    if thread.daemon:
        return
    super().append(thread)
```

`ThreadingHTTPServer` sets `daemon_threads = True`, so nothing is ever tracked and
`server_close()` joins an empty list. In-flight requests are not finished on shutdown; they
die with the process. The stderr line printed on SIGTERM — "finishing in-flight requests" —
overstates the same thing. Both are corrected to say what actually happens. This is not scope
creep: it is the paragraph directly above the lines this feature changes, and leaving a false
claim next to a true one is how the next reader gets misled.

## R7 — Accounting that a flood cannot turn into the attack

**Decision**: One stderr line per saturation episode; a cumulative `refused_over_capacity`
count carried into the existing `web.stop` audit record; no audit record per refusal.

**Rationale**: Principle III's default is to record everything, and the exception path exists
for exactly this shape. Writing an audit record per refused connection would mean opening a
SQLite connection and an audit file handle per refusal — the precise resource this feature
exists to bound — so the record would amplify the flood it documents. A counter is a machine
word; the episode message is bounded by the number of times the server actually recovers and
saturates again, not by the number of refusals.

The reconstruction standard in Principle III is still met at the granularity that matters:
from `web.start`, `web.stop`, and the refusal count between them, the log answers "did this
run turn connections away, and how many". What it does not answer is which connection was
refused at which millisecond — and that is the gap enumerated in the Constitution Check.

**Alternatives considered**: an audit record per saturation episode (still unbounded if an
attacker makes the server flap across the cap, and it needs a `Context` at the worst possible
moment); recording nothing at all (a defence that acts silently is indistinguishable from a
bug when the operator later finds a page that would not load).

## R8 — Peer resets should not print tracebacks either

**Decision**: Override `handle_error` on the server class to swallow `TimeoutError`,
`ConnectionResetError`, and `BrokenPipeError`, and to print everything else exactly as
`socketserver` does today.

**Rationale**: `ThreadingMixIn.process_request_thread` routes any exception escaping the
handler to `BaseServer.handle_error`, which prints a full traceback to stderr. A client that
hangs up mid-response is not a failure of this program, and under the flood this feature
defends against, one traceback per dropped connection is itself an amplifier — stderr is
usually the journal. Suppression is limited to the three exception types that mean "the
connection ended"; a genuine bug in a handler still prints, unchanged, because Principle III's
ban on silent failure is about *our* failures.

## R9 — Testing a socket-level property

**Decision**: Two new test modules — a unit module for the pieces that are decidable without a
socket (the refusal bytes, the counter's admit/release arithmetic) and an integration module
that binds an ephemeral port, reusing the `live_server` fixture shape already in
`tests/integration/test_web_end_to_end.py`.

**Rationale**: The module docstring's own rule is that everything decidable is decided in
`handle`, which is a pure function, and that a real port is bound only for what a
pure-function test cannot reach. A socket timeout and an accept-time refusal are exactly that:
neither is observable from `handle(app, request)`. Tests override the two module constants to
small values (a fraction of a second, a cap of 2) so the suite stays fast — which is also a
live check that both are single constants rather than scattered literals.

**Alternatives considered**: asserting on `settimeout` with a mock socket (proves the call, not
the behaviour, and would pass if `handle_one_request` stopped catching `TimeoutError`).
