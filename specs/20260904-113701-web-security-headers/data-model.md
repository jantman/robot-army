# Data Model: security headers on every web response

**Feature**: `specs/20260904-113701-web-security-headers`

This feature stores nothing and persists nothing. There is no database change, no migration, no
file format, and no state machine. What follows is the one new constant and the one changed
in-memory structure.

## `SECURITY_HEADERS` (new)

A module-level constant in `src/robot_army/web/server.py`, beside the existing `NO_STORE`.

| Field | Type | Value |
| --- | --- | --- |
| `Content-Security-Policy` | `str` | `frame-ancestors 'none'; default-src 'self'; base-uri 'none'; form-action 'self'` |
| `X-Frame-Options` | `str` | `DENY` |
| `X-Content-Type-Options` | `str` | `nosniff` |
| `Referrer-Policy` | `str` | `no-referrer` |

Typed `dict[str, str]`. Immutable in practice — nothing writes to it — and never copied by
reference into a response: the merge in `__post_init__` produces a new dict each time, so a
handler mutating `response.headers` cannot reach back into the constant.

**Validation rules**: none at runtime. The values are literals in the source; there is no input to
validate and no branch that could select a different set. The tests are what hold the values to
the contract.

**Lifecycle**: created at import, read on every response construction, never modified.

## `Response.headers` (changed)

`Response` is unchanged in shape — `status`, `body`, `content_type`, `headers` — and remains
`@dataclass(slots=True)`. What changes is what `headers` contains immediately after construction.

| Before | After |
| --- | --- |
| exactly the dict the caller passed, or `{}` | `{**SECURITY_HEADERS, **caller_headers}` |

**Merge rule**: security headers first, caller's headers second, so a name set by the caller wins.
No caller sets any of the four names today; the ordering states which way a future collision
resolves rather than leaving it to whichever line was written last.

**Invariants**:

1. Every `Response`, however constructed, carries all four names.
2. A header name appears at most once — the structure is a dict keyed by name, so two conflicting
   values for one header are not representable.
3. Every header a caller passes survives with its value intact.

**Relationships**: `Handler._respond` writes `Content-Type` and `Content-Length` directly to the
wire and then iterates `response.headers`. The four names are disjoint from those two, so nothing
is written twice.

## Not in the model

- No configuration entry. The headers are constant (see [research.md](./research.md), Decision 3).
- No audit record. This feature performs no action outside the process, so there is nothing for
  the log to reconstruct (see [research.md](./research.md), Decision 4).
- No per-route or per-request variation. Any such field would be a knob with one caller.
