# Contract: connection bounds on the web interface

**Feature**: `specs/20260904-125206-web-socket-timeout-thread-cap`
**Surface**: the HTTP interface served by `robot-army serve`

The interface's contract with a client gains two rules. Neither changes the response to any
request that is served; both change what happens to a client that is served nothing.

---

## C1 — A silent client is given up on

**Rule**: If a connection makes no progress for `REQUEST_TIMEOUT_SECONDS` (15), the interface
closes it.

"No progress" means no bytes readable from, or writable to, the connection's socket — at any
of these points:

| Point | Today | Under this contract |
|---|---|---|
| Waiting for the first request line | Blocks forever | Closed after 15s |
| Waiting for the rest of the headers | Blocks forever | Closed after 15s |
| Reading a declared request body | Blocks forever | Closed after 15s |
| Waiting for the next request on a kept-alive connection | Blocks forever | Closed after 15s |
| Writing a response to a client that stops reading | Blocks forever | Closed after 15s |

**Client-visible effect**: the connection is closed with no response. There is no timeout
status code, because in the cases that matter the request was never complete enough to answer.

**Not covered**: the interface's own work. A view that takes 30 seconds to compute still
completes and still returns its response; the bound is on waiting for the client, never on
the server's own computation (spec edge case 1).

**Terminal output**: none. A timed-out connection produces no stderr line and no traceback
(FR-003).

---

## C2 — Connections beyond the cap are refused, not queued

**Rule**: While the interface is already serving `MAX_CONCURRENT_CONNECTIONS` (32)
connections, a newly accepted connection is answered with the response below and closed. It is
never queued and never waits.

```http
HTTP/1.1 503 Service Unavailable
Content-Type: application/json; charset=utf-8
Content-Length: 58
Connection: close
Cache-Control: no-store
Retry-After: 1

{"ok": false, "reason": "too many connections; try again"}
```

- `Connection: close` and an actual close, because the interface will not read this
  connection's request and must not leave its bytes to be misparsed as the next request — the
  same reasoning the existing `413` path already applies to an over-large body.
- `Retry-After: 1`, because a slot is released as soon as any in-flight connection ends, and
  the honest advice is "immediately".
- `Cache-Control: no-store`, matching every other response this interface emits.
- The body is JSON regardless of what the client asked for, because the refusal happens before
  a single byte of the request is read: `Accept` is not yet known.

**Delivery is best-effort.** The refusal is written with one non-blocking `send` from the
accept loop's own thread. If the socket would block, the connection is closed without the
response rather than stalling accepts for every other client (research R5). A refused client
always gets a closed connection; it usually, but not by contract, gets these bytes.

**Guarantees on the refusal path**: no thread is started, no database connection is opened, no
audit-log file handle is opened, and no work is queued (FR-006).

**Not covered**: which connection gets refused. There is no fairness rule and no ordering
guarantee beyond "whatever the accept loop sees while the count is at the cap".

---

## C3 — What the operator sees

| Event | Where | Shape |
|---|---|---|
| First refusal of a saturation episode | stderr | `robot-army: at capacity (32 connections); refusing new connections` |
| Subsequent refusals in the same episode | nowhere | (bounded by episodes, not refusals — FR-009) |
| Total refusals for the run | audit log, `web.stop` | `detail.refused_over_capacity: <int>` |
| A connection timing out | nowhere | — |
| A client hanging up mid-response | nowhere | — |
| A genuine failure inside a request | stderr | unchanged: the traceback it prints today |

---

## C4 — What does not change

- Every response to a served request: status, headers, body, and audit records, byte for byte.
- The `413` refusal of an over-large body, including that it does not drain and does close
  (FR-014).
- Startup preconditions, the startup banner, the loopback warning, and the `web.start` record.
- The bind address remaining the access policy. This contract bounds resources; it is not
  authentication and not a per-client rate limit.
- Configuration. `[web]` gains no keys (FR-012).
