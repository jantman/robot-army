# Research: security headers on every web response

**Feature**: `specs/20260904-113701-web-security-headers`
**Date**: 2026-09-04

The spec asks a small question with one interesting decision in it: *where* do the headers get
attached, given FR-005 requires exactly one place and the server has response paths that do not
share a renderer. Everything else is settling values.

## Decision 1 — Attach the headers in `Response.__post_init__`

**Decision**: Add a module constant `SECURITY_HEADERS` and merge it into every `Response` at
construction, in `Response.__post_init__`, with any explicitly passed header winning a
name collision:

```python
def __post_init__(self) -> None:
    self.headers = {**SECURITY_HEADERS, **self.headers}
```

**Rationale**: `Response` is the one type every reply is. There are five construction sites in
`server.py` today — `_render`'s JSON branch, `_render`'s HTML branch, `_render_redirect`, the
static-asset branch of `handle`, and the `413` written directly in `Handler._dispatch` — and
`__post_init__` is the only point all five pass through. It is also the only point a *sixth*,
added later, passes through without its author knowing this feature exists, which is precisely
what FR-005 asks for. `@dataclass(slots=True)` is not frozen, so assigning `self.headers` in
`__post_init__` is legal; `slots=True` does not interfere with `__post_init__`.

Merging security headers *under* the passed dict rather than over it keeps FR-006 true by
construction: `Cache-Control`, `Location`, `Allow` and `Connection` are all passed by callers,
none of them collide with the four names, and if one ever did, the caller's explicit intent at
that site would win rather than being silently overwritten. Because the merge produces one dict
keyed by header name, a duplicate header with two values is not representable — the edge case is
closed by the data structure rather than by a check.

**Alternatives considered**:

- *`Handler._respond`, at the socket.* Also a single point, and it would cover everything. But it
  puts the headers outside the surface `handle()` returns, and R15 — `handle` is a pure function
  from `Request` to `Response`, so that everything decidable is decided without binding a socket
  — is the property that makes the whole web module testable. The existing `NO_STORE` comment
  says exactly this about itself: the header is set where the response is built "which also puts
  it inside R15's testable surface". Moving security headers to the wire would mean the framing
  refusal could only be tested through the one integration test that binds a port.
- *Add them to `NO_STORE`, as the issue's first suggestion.* Cheapest edit, and wrong in two
  directions: it would not reach the static assets (which set `max-age`, not `no-store`) or the
  `413`, and it would conflate "this page must not be cached" with "this response must not be
  framed", so a future response that wants caching would have to choose between them.
- *A `SECURITY_HEADERS` dict spread at each of `_render`, `_render_redirect` and `_bare`, as the
  issue's second suggestion.* This is the shape the issue proposes, and it leaves two of the five
  paths uncovered — the static assets and the `413` — plus every path added later. `_bare` calls
  `_render`, so it is not even a third site, which makes the list look complete while missing
  the two that matter.

## Decision 2 — The four header values

**Decision**: The values the issue proposes, with one deliberate change — `Referrer-Policy`
is `same-origin` rather than `no-referrer`, for the reason given below:

| Header | Value |
| --- | --- |
| `Content-Security-Policy` | `frame-ancestors 'none'; default-src 'self'; base-uri 'none'; form-action 'self'` |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `same-origin` |

**Rationale**: Verified against what the pages actually do, since a policy that refuses something
the interface needs is worse than no policy:

- `default-src 'self'` is the fallback for `script-src`, `style-src`, `img-src`, `font-src` and
  `connect-src`. `html.page` emits exactly two subresources — `<link rel=stylesheet
  href="/static/app.css?v=…">` and `<script src="/static/app.js?v=…" defer>` — both served by
  this server at fixed routes. There is no inline `<script>`, no inline `<style>`, and no `style=`
  or `on*=` attribute anywhere in `html.py`, so no `'unsafe-inline'` is needed. `app.js` calls
  `fetch(window.location.href)`, which is same-origin, so `connect-src` falls through to `'self'`
  and the refresh loop keeps working. The external URLs a page does emit — `github.com` and
  `trello.com`, the two systems this interface reads from — are anchors, and CSP does not
  govern navigation by link.
- `frame-ancestors 'none'` and `X-Frame-Options: DENY` say the same thing to two generations of
  browser. `frame-ancestors` is not covered by `default-src` and must be stated explicitly.
- `base-uri 'none'`: nothing emits a `<base>`, so forbidding one costs nothing and removes the
  trick where injected markup re-points every relative URL on the page.
- `form-action 'self'`: every form on every page posts to this server.
- `nosniff` matters most on the `.json` responses and the two static assets, where a browser
  guessing a type other than the declared one is the whole attack.
- `same-origin`: the audit and item views link out to `github.com` and `trello.com`. Without
  this, following one hands the destination the interface's address, port and the path being
  viewed. **Not `no-referrer`**, which the issue proposed: `_referring_view` reads the
  `Referer` of our own POSTs, and `no-referrer` suppresses it on those too.

  The reachable difference is narrower than it first looks, and worth stating exactly. After
  a *successful* action there is none: every control that renders as a real form already sits
  on the page its fallback names — the item and repository holds and the four dispatch
  controls are on `/queue`, `attach` is on the item view — and every confirm-gated verb POSTs
  from its confirmation page, whose referer `_referring_view` deliberately refuses. What does
  change is a **refused** POST: its chrome builds the visibility toggle from the referring
  view, so a control refused from `/queue` offers a toggle back to `/queue` with the header
  and to `/active` without it. Minor, but it lands on the error page, and any confirm-free
  control added to a list view later would widen it silently.

  `same-origin` withholds the referrer from exactly the destinations this header exists to
  withhold it from, and from nothing else, so the issue's stated purpose is met in full.

**Alternatives considered**: adding `object-src 'none'` and `frame-src 'none'` for completeness —
both are already covered by `default-src 'self'` given the interface embeds nothing, and a longer
policy is a longer thing to be wrong about. `Referrer-Policy: strict-origin-when-cross-origin`
(the common default) still leaks the origin, and there is no case here where an outbound referrer
is wanted. `Permissions-Policy` — the interface uses no powerful features, so there is nothing to
disable and it would be a header written for a hypothetical.

## Decision 3 — Constant, not configurable

**Decision**: `SECURITY_HEADERS` is a module-level constant. No setting, no override, no allowlist
of permitted framing ancestors.

**Rationale**: Principle I. A knob here would have exactly one caller and no second use in hand.
Principle II settles the substantive question behind it: this is a single-user local tool, there
is no second surface that would embed it, so "never framed" is the complete answer rather than a
default someone might need to relax.

## Decision 4 — What this logs, and what happens if it is killed halfway

**Decision**: Nothing is added to the audit log, and there is no halfway.

**Rationale**: Principle III governs actions that change state outside the running process. This
feature performs no action: it adds four constant strings to a response's header map. It writes
no file, runs no command, makes no network request, and touches no database row. There is nothing
to record that the existing request record does not already carry. `GET` requests remain
unaudited under the exception already enumerated as FR-041, and that exception is unchanged.

Principle IV is satisfied trivially: constructing a `Response` is not interruptible in any way
that leaves persistent state behind. A process killed mid-request loses the connection, which is
the pre-existing behaviour of every request, and the next request builds a fresh `Response` with
the headers on it.
