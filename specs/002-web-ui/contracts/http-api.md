# Contract: HTTP Interface

Served by `robot-army serve`. Every route is a function of a payload assembled by an `operations.*`
call — see [cli-additions.md](cli-additions.md) for the terminal equivalents that FR-006 requires
each of these to have.

**This is not a stable public API** (FR-009). It is versioned by the commit that produced it. No
deprecation cycle, no compatibility shim, no consumer outside this repository.

---

## Universal rules

- **Representations.** `Accept: application/json`, or a `.json` suffix on any `GET` path, returns
  `Result.data` as JSON. Anything else returns HTML. Both come from the same handler and the same
  payload (R2).
- **Reads are `GET`, writes are `POST`.** No `GET` changes state. A `GET` on a write route is `405`
  with `Allow: POST`; an unknown path is `404`. Both render as a page, not a bare status line.
- **Every `POST` answers `303 See Other`** with a `Location`, so a reload never re-posts (R7). The
  redirect target carries a `?msg=` key naming what happened, rendered as a banner.
- **Every mutating request is audited** as an intent/outcome pair with `component: "web"`, written
  before the action (FR-038, FR-039). `GET` requests are not individually audited — the one
  enumerated Principle III exception, justified in plan.md.
- **Simulated rows are excluded unless `?include_simulated=1`** (FR-019). When included, every
  simulated row carries a visible marker in HTML and `"simulated": true` in JSON.
- **Every response carries chrome**: effect level, daemon liveness and its age, pause state, anomaly
  count, and the render time (FR-016, FR-017, FR-018).
- **No response contains a secret** (FR-020).
- **Cache-Control: no-store** on everything except `/static/*`, which is immutable.

### Refusal shape

A refused action returns `303` to the referring view with an explanatory `?msg=`, and in JSON:

```json
{"ok": false, "reason": "work item 42 is active; resume requires 'interrupted' or 'awaiting_review'",
 "code": 3, "item_id": 42}
```

`code` is the exit code the equivalent terminal command would have returned, from 001's table:
`1` operation failed, `2` usage error, `3` precondition not met. Using the same numbers means a
refusal reads identically whichever front end produced it.

**Three refusals are structural rather than incidental**, and each names its cause:

| Condition | Response | Requirement |
|---|---|---|
| No daemon holds the lock, and the action needs one | `503`, `reason` naming that the daemon is not running | FR-005 |
| The daemon's heartbeat reports a different effect level | `409`, `reason` naming both levels | R4 |
| The item is no longer in a state where the action is legal | `409`, `reason` from `IllegalTransition` | FR-027 |
| A browser reports the request as coming from another site | `403`, `reason` naming the origin | see plan.md, "Added after implementation" |

The `403` was added after implementation and is the one refusal with no requirement behind
it. A forged request reaches the port through the author's own browser rather than over the
network, so FR-003's model does not cover it. Clients sending neither `Origin` nor
`Sec-Fetch-Site` — `curl`, and every script in quickstart.md — are unaffected.

---

## Views

| Route | Renders | Payload from |
|---|---|---|
| `GET /` | Redirect to `/active` | — |
| `GET /active` | Running sessions: item, repo, issue link, worktree, branch, session state, start, elapsed | `operations.status(state="active")` + latest session per item |
| `GET /queue` | `ready` in dispatch order, `dispatching` with age against the max, and blocked items with their reason | `operations.status` |
| `GET /interrupted` | Interrupted items with the four FR-013 signals | `operations.status(state="interrupted")` + resume signals (R9) |
| `GET /item/<id>` | Everything about one item: source links, state history, every session attempt with exit code and signal, resume signals, and the actions currently legal | `operations.show` |
| `GET /anomalies` | Unacknowledged anomalies with enough detail to act | `operations.anomalies` |
| `GET /log` | The audit log, newest first, bounded | `operations.read_log` + paging (R14) |

`GET /log` accepts `?item=<id>`, `?since=<duration>` (`30s`, `10m`, `2h`, `1d` — parsed by the
existing `operations.parse_duration`), `?outcome=ok|error|pending`, and `?cursor=<opaque>` for the
next page. The active filter is always rendered, and `skipped_lines` reports records that could not
be parsed (FR-044).

Every view accepts `?include_simulated=1`.

---

## Confirmations

| Route | Renders |
|---|---|
| `GET /item/<id>/confirm/<action>` | What is about to happen, re-validated against current state, with the `POST` form |

`<action>` is one of `resume`, `restart`, `abandon`, `cancel`, `retry`. If the item is no longer in a
state where the action is legal, this page says so and offers no form — which is where FR-027's
re-validation becomes visible rather than arriving as a failure after the tap (R8).

`pause`, `unpause`, `attach`, `acknowledge`, `poll`, and `reconcile` need no confirmation: none of
them stops, starts, or discards work. `attach` opens a window; `pause` is reversible and its whole
purpose is caution.

---

## Actions

Each maps to exactly one `operations.*` function. The web package contains no action logic of its own
(FR-047).

| Route | Operation | Notes |
|---|---|---|
| `POST /item/<id>/resume` | `operations.resume` | Worker thread; `303` immediately (R3). Requires `interrupted` or `awaiting_review` |
| `POST /item/<id>/restart` | `operations.restart` | Worker thread; new session id, new attempt |
| `POST /item/<id>/abandon` | `operations.abandon` | Non-destructive: the worktree is left in place |
| `POST /item/<id>/cancel` | `operations.cancel(force=True)` | Stops that session's process tree only (FR-050). `force=True` because the HTTP confirmation already happened; the terminal prompt would have nothing to read |
| `POST /item/<id>/retry` | `operations.retry` | Refuses with the reason if the block still holds (FR-022) |
| `POST /item/<id>/attach` | `operations.attach` | New; opens a terminal tab attached to a `running` session (R10). No state change |
| `POST /anomalies/<id>/acknowledge` | `operations.anomalies(acknowledge=id)` | |
| `POST /dispatch/pause` | `operations.pause_dispatch` | New; durable (FR-035) |
| `POST /dispatch/unpause` | `operations.unpause_dispatch` | New |
| `POST /poll` | `operations.poll_now` | Writes the request marker when a daemon is running; polls directly when not (R5) |
| `POST /reconcile` | `operations.reconcile_now` | Same delegation |

`POST /poll` and `POST /reconcile` accept an optional `repo=<key>` form field.

**Deliberately absent** (FR-030, FR-031, FR-032): repository onboarding and fingerprint re-approval,
worktree or branch removal, simulated-row purging, concurrency limit adjustment, and anything that
starts or stops the daemon itself. Each remains a terminal command, and each is absent for a stated
reason rather than an oversight.

---

## Static assets

| Route | Content |
|---|---|
| `GET /static/app.css` | Stylesheet, a module constant |
| `GET /static/app.js` | The refresh loop, a module constant |

No request contributes to a filesystem path anywhere in the server (R12), so there is no path
traversal to defend against. Nothing is fetched from a third-party host, so every view works with the
machine offline (SC-009).

`app.js` re-fetches the current URL every `refresh_seconds` and replaces the content container. With
scripting disabled every page is still correct, merely static until reloaded.

---

## Startup and preconditions

`robot-army serve` refuses to start, exit `3`, naming every problem rather than the first:

1. Configuration is invalid.
2. The database is unreadable, or its `PRAGMA user_version` is not the version this code expects —
   the web never migrates (R11).
3. `[web] bind` parses as a globally routable address (FR-004).
4. The address and port cannot be bound.

On success it writes a `web.start` audit record and prints the **effective** address and port. If the
address is not loopback it additionally prints a one-line warning that anything able to reach it has
full control — which under FR-003 is the accepted model, and therefore the one fact that must never
be silent.

`SIGTERM` and `SIGINT` stop accepting connections, let in-flight requests finish, and exit `0`. A
worker thread mid-dispatch is not waited for: the item is left in `dispatching` and reconciliation
resolves it, which is the same path any interrupted dispatch already takes.
