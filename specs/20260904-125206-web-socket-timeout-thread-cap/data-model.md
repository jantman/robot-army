# Data Model: bounded waits and bounded concurrency

**Feature**: `specs/20260904-125206-web-socket-timeout-thread-cap`
**Date**: 2026-09-04

This feature adds no persistent data. Nothing is written to SQLite, no schema changes, no new
files on disk. What follows is the in-process state the two bounds need, and the one existing
durable record that gains a field.

## Module constants (`robot_army.web.server`)

| Name | Value | Meaning | Requirement |
|---|---|---|---|
| `REQUEST_TIMEOUT_SECONDS` | `15` | Seconds of no progress on a connection's socket — waiting for a request line, headers, a body, a follow-up keep-alive request, or the client to accept response bytes — after which the connection is closed. | FR-001, FR-002, FR-012 |
| `MAX_CONCURRENT_CONNECTIONS` | `32` | The most connections served at once. Connections accepted beyond this are refused and closed without being served. | FR-004, FR-012, FR-013 |
| `OVER_CAPACITY_RESPONSE` | `bytes` | The complete, pre-serialised HTTP refusal written to a socket turned away at capacity. A constant because building it must not allocate or format under flood. | FR-005, FR-006 |

Both numbers are constants, not configuration (FR-012). Tests set them with `monkeypatch` to
small values, which is also the check that each is a single constant rather than a literal
repeated at several call sites.

## In-process state (`BoundedThreadingHTTPServer`)

One instance per running server; all of it dies with the process and none of it is persisted.

| Field | Type | Lifetime | Invariant |
|---|---|---|---|
| `_capacity_lock` | `threading.Lock` | Server | Guards every field below. Held only for the counter arithmetic — never across a socket operation, and never across starting a thread. |
| `_in_flight` | `int` | Server | `0 <= _in_flight <= MAX_CONCURRENT_CONNECTIONS`. Incremented when a connection is admitted, decremented exactly once when its serving thread ends — by completion, timeout, client disconnect, or an unhandled failure (FR-008). |
| `_saturated` | `bool` | Server | `True` from the first refusal until `_in_flight` next falls below the cap. Exists so the terminal message is one per saturation episode, not one per refusal (FR-009). |
| `refused_over_capacity` | `int` | Server | Monotonically increasing count of connections refused for capacity during this run. Read once, at shutdown, into the `web.stop` record (FR-010). Public because `serve()` is its only reader. |

### Connection lifecycle

```
accept()
   │
   ├── _in_flight >= cap ──► refuse: send OVER_CAPACITY_RESPONSE, drain, close
   │                          refused_over_capacity += 1
   │                          if not _saturated: print to stderr; _saturated = True
   │                          (no thread, no Context, no SQLite, no audit handle)
   │
   └── otherwise ──────────► _in_flight += 1;  if _in_flight < cap: _saturated = False
                             thread: serve the connection (one or more requests)
                             finally: _in_flight -= 1
```

The slot is held for the whole connection, not for one request, because one thread serves
every request on a kept-alive connection (research R4).

## Changed durable record

`web.stop` — written by `serve()` when the process is signalled — gains one field. Nothing
else about it changes, and no new record type is introduced.

```json
{"action": "web.stop", "outcome": "ok",
 "detail": {"reason": "signal", "refused_over_capacity": 0}}
```

`refused_over_capacity` is `0` on every ordinary run, which is the point: a non-zero value is
the only durable trace that the cap ever engaged, and it is recoverable from the log alone
without re-running anything (SC-006).

## Deliberately absent

- **No record per refused connection.** Enumerated as a Principle III exception in `plan.md`;
  reasoned in `research.md` R7.
- **No per-client state.** Nothing is keyed by source address, so nothing accumulates and
  nothing needs ageing out.
- **No persistence across runs.** The counter starts at zero on every start; the durable trace
  is the `web.stop` record of the run that saw the refusals.
