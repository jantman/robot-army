# Implementation Plan: Web UI & HTTP API

**Branch**: `002-web-ui` | **Date**: 2026-08-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-web-ui/spec.md`

## Summary

A second front end onto the operations milestone 001 already built. `robot-army serve` runs a
standard-library HTTP server that renders the same `operations.*` results as HTML for a phone and as
JSON for a script, and turns the same functions the CLI calls into POST endpoints. It adds one
genuinely new capability — pausing dispatch — to the daemon, the database, and both interfaces at
once.

The three design decisions that shape everything else:

1. **No new runtime dependency.** `http.server.ThreadingHTTPServer` plus a ~150-line HTML builder,
   against roughly a dozen routes with no authentication and one user. A framework would remove
   boilerplate this size does not have (R1).
2. **One renderer, two representations.** Every handler produces the `Result` an operation already
   returns; HTML and JSON are two renderings of the identical payload, and the auto-refresh
   re-fetches the page rather than re-implementing rendering in the browser (R2).
3. **The web process performs actions itself, exactly as the CLI does** — same functions, same
   guards, same audit log with a different component name. Long ones run on a worker thread so an
   HTTP request never waits on worktree preparation (R3).

## Technical Context

**Language/Version**: Python 3.14 (unchanged; `requires-python = ">=3.14"`)

**Primary Dependencies**: none added. `httpx` remains the sole runtime dependency and is not used by
this feature. Server, routing, HTML escaping, form parsing, and address validation are
`http.server`, `urllib.parse`, `html`, and `ipaddress`.

**Storage**: the existing SQLite database at `~/.local/state/robot-army/state.db`, plus one new
single-row table (`dispatch_control`) added by migration 002. The web process **never migrates** —
it opens, verifies `PRAGMA user_version`, and fails a precondition if the schema is not the version
it expects. The daemon owns the schema.

**Testing**: pytest, as in 001. Unit tests for routing, rendering, escaping, redaction, action
re-validation, bind-address validation, and pause persistence; an integration test that starts a
real server on an ephemeral port and drives it with `urllib.request`.

**Target Platform**: the author's Linux desktop. Reached from the phone over the local network,
and from outside the house over the author's existing VPN into that same network.

**Project Type**: single project — a new `robot_army.web` package and a new CLI verb, inside the
existing package. Not a separate frontend/backend split; there is no build step and no JavaScript
toolchain.

**Performance Goals**: a page renders in well under a second against a database of a few hundred
work items. The audit view returns a bounded page against a 100,000-record log in under 2 seconds
(SC-014) by reading daily files newest-first and stopping once the page is full.

**Constraints**: no asset may be fetched from a third-party host (SC-009); no response may contain a
secret (SC-012); no view may spend GitHub rate limit per auto-refresh (R9); the server holds no
authoritative state, so killing it loses nothing (FR-045).

**Scale/Scope**: one user, one browser at a time in practice, roughly a dozen routes, six views.
Concurrency exists to keep one slow action from blocking one fast page, not to serve load.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design. Re-check result at the bottom.*

### I. Simplicity First (YAGNI & KISS)

| Check | Result |
|---|---|
| New third-party dependencies justified by work removed | **Pass** — none added (R1) |
| No speculative generality | **Pass with one item tracked** — the JSON representation has no consumer today beyond `curl`; see Complexity Tracking |
| Single process, plain files, obvious control flow the default | **Pass with two items tracked** — a second process and a worker thread, both justified below |
| Fewest moving parts wins between two adequate designs | **Pass** — one renderer, no template engine, no client-side framework, no build step |

### II. Single-User, Local-First

| Check | Result |
|---|---|
| No authentication, authorization, accounts, or roles built | **Pass** — FR-003 decides this explicitly; the network is the boundary |
| State on the local filesystem, no hosted service required | **Pass** — same database, same log directory |
| Secrets from environment or git-ignored files, never in logs | **Pass** — and extended: never in a rendered page either (R12) |
| No public IP, reverse proxy, or deployment infrastructure assumed | **Pass** — and enforced: a globally routable bind address is refused at startup (R13) |

The tension worth naming: the interface listens on the LAN with no access control, so the
operating-system user is no longer the trust boundary — the network is. This is the author's
decision, recorded in the spec (FR-003) and in `docs/roadmap.md`. Principle II's actual prohibition,
building authentication, is honoured. The mitigation is that the effective bind address is announced
loudly at startup and in the audit log, and that a public address is refused outright.

### III. Total Accountability

| Check | Result |
|---|---|
| Every outward-facing action logged when it occurs | **Pass** — every mutating request goes through `ctx.audit.action(...)`, the same intent/outcome pair the CLI produces |
| Records carry timestamp, component, action, target, params, outcome | **Pass** — `component: "web"`, via `build_context(component="web")`, which already takes the argument |
| No silent failure | **Pass** — an HTTP error response is never returned without a corresponding record; this is a route-level invariant with a test |
| Documented exceptions enumerated | **One, below** |

**Enumerated Principle III exception (FR-040).** Read-only requests — every `GET` — are not
individually audited. They change no state outside the process, so the principle's own scope does not
reach them; the exception is recorded here because the request *volume* makes the omission visible
(an auto-refreshing page issues a GET every 10 seconds, and logging those would bury the record this
project exists to keep readable). Nothing a GET does is unreconstructable: the data it read is in the
database and the log it rendered from.

### IV. Interruption Tolerance

| Check | Result |
|---|---|
| Atomic writes to persistent state | **Pass** — every mutation reuses `db.transaction` (`BEGIN IMMEDIATE`); the pause row is one UPDATE; job-request markers are atomic file creates and unlinks |
| Restartable, idempotent, incomplete work detected | **Pass** — the server holds nothing, so restart is free. Repeated actions are absorbed by `transition_work_item`'s legality check (R7) |
| Explicit timeouts and bounded retries on every network call | **Pass** — inherited; the web adds no new outward calls except the resume-signal PR lookup, which is the existing bounded `open_pr_for_branch` |
| Precautions reasonable, not extreme | **Pass** — no session affinity, no server-side state, nothing to recover |

**What this logs**: every POST, as an intent/outcome pair naming the route, the item, and the actor
component. **What happens if it is killed halfway**: an in-flight action is either committed or
rolled back by the existing transaction; a resume killed between the `dispatching` transition and
launch confirmation is handled by exactly the machinery 001 already has — the confirmation window
elapses and reconciliation finds it. The browser sees a dropped connection and the item page tells
the truth on reload.

### V. Public Code, Unsupported Project

| Check | Result |
|---|---|
| No credentials, personal data, or private addresses committed | **Pass** — bind address and port are configuration, defaulted to loopback |
| No stable public API maintained | **Pass** — stated in FR-009 and repeated in the API contract |
| Documentation written for the author's future self | **Pass** — `quickstart.md`, plus updates to `docs/state.md`, `docs/logging.md`, and `README.md` |
| No packaging or release pipeline | **Pass** — one new console entry point verb, no artifact |

### Operating Constraints

| Check | Result |
|---|---|
| Every capability reachable and observable from the terminal | **Pass** — `serve`, `pause`, `unpause`, and `attach` are added as CLI verbs in the same change, so no web control is web-only |
| Commands exit non-zero on failure | **Pass** — the new verbs use the existing exit-code table |
| Persistent data plain text or SQLite | **Pass** — one new table, one marker file per forced job |
| Irreversible or outward-facing actions confirmed and logged before execution | **Pass** — GET confirm page then POST (R8), audit intent written first |

**Gate result: PASS.** Three items carry justification in Complexity Tracking; none is a violation
requiring redesign.

## Project Structure

### Documentation (this feature)

```text
specs/002-web-ui/
├── plan.md              # This file
├── research.md          # Phase 0 — R1..R15, every decision and what was rejected
├── data-model.md        # Phase 1 — the new table, the view models, interruption behaviour
├── quickstart.md        # Phase 1 — runnable validation scenarios
├── contracts/
│   ├── http-api.md      # Routes, representations, status codes, error shape
│   └── cli-additions.md # serve / pause / unpause / attach, and the two changed verbs
├── checklists/
│   └── requirements.md  # From /speckit-specify
└── tasks.md             # Phase 2 — NOT created by /speckit-plan
```

### Source Code (repository root)

```text
src/robot_army/
├── web/                     # new
│   ├── __init__.py
│   ├── server.py            # routing, request/response, action dispatch, worker thread
│   ├── pages.py             # one function per view: payload dict -> HTML
│   └── html.py              # escaping, element helpers, page chrome, embedded CSS and JS
├── control.py               # new — dispatch pause state and cross-process job requests
├── operations.py            # + pause_dispatch, unpause_dispatch, attach; poll_now/reconcile_now
│                            #   changed to actually force a running daemon
├── db.py                    # + dispatch_control accessors
├── migrations.py            # + _migration_002 (dispatch_control table)
├── daemon.py                # + drain job-request markers each tick; + honour the pause
├── health.py                # + dispatch_paused on the heartbeat
├── config.py                # + [web] section: bind, port, refresh_seconds
└── cli.py                   # + serve, pause, unpause, attach

tests/
├── unit/
│   ├── test_web_routing.py      # route table, 404/405, method guards
│   ├── test_web_render.py       # escaping, simulated marking, secret redaction, no external URLs
│   ├── test_web_actions.py      # re-validation, double submit, confirm-then-post
│   ├── test_web_bind.py         # public address refused, loopback default, announcement
│   ├── test_web_effect_guard.py # daemon/web effect-level mismatch refuses mutations
│   ├── test_pause.py            # durability across restart, dispatch honours it, heartbeat carries it
│   └── test_job_requests.py     # marker written, drained once, absent daemon reported
└── integration/
    └── test_web_end_to_end.py   # real server on an ephemeral port, driven with urllib

docs/
├── state.md                 # + dispatch_control, + requests/ markers
├── logging.md               # + the `web` component, + the GET exemption
└── README.md                # + running the interface
```

**Structure Decision**: one new package (`robot_army.web`) and one new small module
(`robot_army.control`), inside the existing single project. There is no frontend/backend split
because there is no frontend build — HTML is produced in Python and the only JavaScript is an
embedded refresh loop of a few lines. Everything the web package does that changes state goes through
`operations.*`, which is the rule that keeps the two front ends from diverging (FR-047).

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| A second long-running process (`serve`) alongside the daemon | FR-005: the audit log and the interrupted list must stay readable when the daemon is down, which is exactly the incident that makes them worth reading. The author chose this explicitly during specification | Serving from inside the daemon is one fewer process, but makes the interface die with the thing it is meant to explain, and turns "connection refused" — indistinguishable from the phone's own network trouble — into the only signal |
| A worker thread for `resume` and `restart` | A dispatch can legitimately run for minutes (worktree preparation is bounded at 15 minutes by `dispatching` max age). An HTTP request from a phone cannot wait that long, and a dropped connection mid-dispatch must not look like a failed action | Doing it inline blocks the request and the browser gives up before the work does. Handing the action to the daemon through a queue table needs at-least-once semantics for a non-idempotent action, a new drain step, and stops working when the daemon is down — three new failure modes to avoid one thread |
| A 60-second in-process cache for the GitHub-derived resume signals | An auto-refreshing interrupted view with 5 items would otherwise spend 1,800 GitHub calls an hour re-asking a question whose answer cannot change from anything happening locally (R9) | Recomputing every render burns rate limit the daemon needs for polling. Not showing the signal at all fails FR-013. Storing it in the database would make it a stored copy, which FR-013 forbids for the reason 001 documents |

The JSON representation is listed under Principle I above as tracked rather than as a violation: it
is one `json.dumps` of a payload every operation already returns, it is what makes `curl | jq`
debugging possible against a running interface, and it is what FR-001 asks for. It is not a second
implementation of anything. Its honest limit — that the HTML pages render server-side rather than
consuming it — is recorded in research.md R2 rather than papered over.

## Post-Design Constitution Re-Check

Re-run after the Phase 1 artifacts were written.

- **No new dependency appeared during design.** The routes, the HTML builder, and the audit-log
  reader all landed in the standard library. **Pass.**
- **The new table stayed at one row and three columns**, and the pause it holds is read in exactly
  one place in the daemon. No settings framework grew out of it. **Pass.**
- **The action surface did not grow.** Every POST in `contracts/http-api.md` maps to a function in
  `operations.py`, and the four that did not exist there are added to `operations.py` rather than
  written in the web package. **Pass.**
- **The Principle III exception is still exactly one** — read requests — and is documented in this
  plan and in `docs/logging.md`. **Pass.**
- **One design change was made under Principle I during Phase 1**: the interrupted list originally
  called `resume_signals` per row, which pulled GitHub into every auto-refresh. Splitting the signals
  by cost (R9) removed the problem without adding a mechanism. **Pass.**

**Re-check result: PASS.** No violation requires redesign; the three tracked items above stand as
written.
