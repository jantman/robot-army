# Phase 0 Research: Web UI & HTTP API

Every decision this design rests on, why it was taken, and what was rejected. Numbered so the plan,
the contracts, and the code comments can point at a reason rather than repeat it.

Findings from milestone 001 are cited as `001 R<n>`; findings from the M0 spike as `M0 F<n>`.

---

## R1 — The HTTP server is `http.server.ThreadingHTTPServer`

**Decision.** Standard library. `ThreadingHTTPServer` with a `BaseHTTPRequestHandler` subclass, a
literal route table, `urllib.parse` for queries and form bodies, `html.escape` for output.

**Rationale.** Principle I makes the standard library the default and requires a new dependency to be
justified *by the work it removes*. The work here is: about a dozen routes, no authentication, no
content negotiation beyond one `Accept` check, no sessions, no ORM, one user. A framework removes
routing and templating boilerplate that at this size is roughly 120 lines. It adds a dependency tree
(Flask brings Werkzeug, Jinja2, click, itsdangerous, blinker; Starlette brings anyio and an ASGI
server) to a project whose entire runtime dependency list is currently one entry, chosen with a
written justification.

Threading rather than the single-threaded `HTTPServer` because one slow request — an interrupted
view computing git status across several worktrees — must not stall the page loading behind it.

**Honest limits, named rather than discovered later.** `http.server` is documented as a basic
implementation not intended for hostile input. That is acceptable here for the same reason
FR-003 is acceptable: the exposure model already grants full control to anything that can reach the
port, so hardening the parser protects nothing that is not already given away. Two things follow from
it and are treated as requirements rather than hopes:

- **No filesystem path is ever derived from a request.** `SimpleHTTPRequestHandler` is not used and
  no static directory is served. CSS and JavaScript are module constants at fixed routes (R12), so
  path traversal is structurally impossible rather than defended against.
- **The bind address is validated** (R13), so the "basic implementation" is never reachable from
  somewhere the author did not intend.

**Alternatives considered.** Flask (rejected: dependency tree, and its ergonomics buy little at this
route count). Starlette/FastAPI (rejected: same, plus an async model this synchronous, subprocess-
and-SQLite-shaped codebase has no use for). `wsgiref` (rejected: still needs a server, and adds a
protocol layer without removing the handler work).

---

## R2 — One renderer, two representations; the refresh re-fetches HTML

**Decision.** Every route computes a payload by calling an `operations.*` function and taking its
`Result`. `Accept: application/json`, or a `.json` suffix on the path, returns `Result.data` as JSON.
Anything else returns HTML rendered in Python from the same dict. Auto-refresh re-fetches the current
page and swaps the content container.

**Rationale.** `operations.Result` already carries a machine-readable `data` payload alongside a
human rendering — 001 built it that way precisely so this milestone would be a second caller rather
than a reimplementation. Serving that dict as JSON is one `json.dumps`. Having the browser render it
instead would mean a second renderer, in a second language, that must be kept consistent with the
first — the exact duplication FR-047 exists to prevent.

**The limit worth stating.** FR-001 says the interface is "backed by an HTTP API that the interface
itself consumes". In this design the pages and the JSON come from the same handler and the same
payload, but the HTML is rendered server-side, so the pages do not literally fetch the JSON. The
requirement's purpose — that there is one source of truth behind both, and that a script can get at
the same data — is met. Its literal wording is not, and pretending otherwise by adding a client-side
renderer would cost a second implementation to satisfy a sentence rather than a need. Planning §13's
original reason for the API, "CLI/TUI clients later against the same API", was already better served
by 001's operations layer, which such a client would call directly.

**Alternatives considered.** A single-page application fetching JSON (rejected: a second renderer, a
build step, and a blank page when JavaScript fails). `<meta http-equiv="refresh">` (rejected: loses
scroll position and any open confirm dialog on a phone, and reloads the whole document every
interval). A push connection — websocket or server-sent events (rejected: the data changes on the
order of a poll interval; a persistent connection from a sleeping phone is a reconnection problem
in exchange for latency nobody needs).

---

## R3 — The web process performs actions itself, on a worker thread when they are slow

**Decision.** A POST calls the same `operations.*` function the CLI calls, in the web process, with a
`Context` built per request and `component="web"`. `resume` and `restart` are handed to a
single-worker thread; the response is an immediate `303` back to the item page, which shows the item
in `dispatching`. Everything else runs inline — they are single transactions or a single
`terminate` call.

**Rationale.** 001 already established that a second process may act: `robot-army resume` works today
while the daemon runs, and the guards that make it safe live in the operation, not in the daemon.
Giving the web a *different* execution path would mean the same verb behaving two ways, which is what
FR-047 forbids.

The thread exists for one measured reason: `dispatch.dispatch_item` prepares a worktree and runs
preparation hooks, bounded by the `dispatching` max age of 15 minutes. No phone will hold an HTTP
request that long, and a dropped connection must not be ambiguous with a failed action. Returning
immediately and letting the item's own state tell the story is both faster and more honest — it is
also exactly what the daemon's dispatch looks like from the outside.

**What keeps it safe.** `dispatch_item`'s first act is a transition to `dispatching` inside
`db.transaction` (`BEGIN IMMEDIATE`). A second concurrent resume — from a double tap, another tab,
or a terminal — finds the item no longer `interrupted` and is refused by
`states.transition_work_item`'s legality check. The database, not the web process, is the arbiter
(R7).

**Known, pre-existing, and deliberately not fixed here**: `resume` and `restart` bypass the
concurrency cap, because `select_and_dispatch` enforces it and they do not go through it. That is
001's behaviour for the CLI verbs and it is defensible — resuming is a human decision about a
specific item, not the dispatcher choosing work. The web inherits it unchanged rather than quietly
introducing a different rule. It is worth revisiting in milestone 004, where the concurrency model is
the subject.

**Alternatives considered.** A command queue table drained by the daemon (rejected: at-least-once
delivery for a non-idempotent action, a new drain step, new interruption semantics, and no function
at all when the daemon is down — three failure modes introduced to avoid one thread). Marking intent
on the work item and letting the dispatcher act (rejected: changes resume semantics, needs a new
column to carry the session id to resume from, and still dies with the daemon).

---

## R4 — The web refuses to mutate when its effect level disagrees with the daemon's

**Decision.** The web process resolves its effect level from configuration, like any other command.
Before performing any mutating action it reads `heartbeat.json`; if the heartbeat is fresh and names
a different effect level, the action is refused with that reason, and the mismatch is displayed on
every page until it clears.

**Rationale.** The daemon can be started with `--effect-level plan` while the configuration file says
`live`. Nothing in 001 detects the resulting divergence, because until now the only other actor was a
terminal command the author was typing deliberately. A tap on a phone is not that. Without this
guard, the interface would happily launch real sessions and write real GitHub comments for a daemon
the author believes is doing nothing.

FR-016 already requires the effect level on every view; this makes the same fact load-bearing rather
than decorative. When the daemon is not running, there is nothing to disagree with and the configured
level applies.

**Alternatives considered.** Adopting the daemon's level from the heartbeat rather than refusing
(rejected: silently running at a level the operator did not configure is the same class of surprise,
in the other direction, and the heartbeat can be stale). A shared level file (rejected: the heartbeat
already carries it — FR-063 — and a second source of truth is a divergence waiting to happen).

---

## R5 — Forcing a poll uses a durable request marker, not a signal

**Decision.** `state_dir/requests/<job>` is an empty marker file, created atomically. The daemon
checks the directory at the top of each tick, unlinks any marker it finds, and sets that job's
existing `forced` flag. `poll` and `reconcile` are the two job names.

**Rationale.** This closes a real gap in 001 rather than adding a feature. `contracts/cli.md`
promises that `robot-army poll` "signals it to poll on its next tick" when the daemon is running;
`operations.poll_now` in fact only prints how often the daemon polls. `Daemon.request()` exists and
is correct, but nothing outside the process can call it. FR-023 requires a real force-poll control,
so the mechanism has to exist for the web anyway — and once it does, the CLI verb stops
over-promising.

A file rather than a signal because: the daemon may be mid-tick, and a marker waits without needing a
handler; the PID would have to be read from the lock file and signalled, which is the "identify the
process by weaker evidence" pattern this project has already been bitten by (M0 F17, FR-039); and a
marker is trivially testable without a running process. Base tick is 5 seconds, so "immediate" means
"within one tick", which the response says rather than implying instantaneity.

**Honest scope.** The response to a forced poll is necessarily *"requested"*, not *"here is what it
found"* — the daemon reports the result into the audit log, which the interface then shows. FR-023's
"MUST report what each found" is satisfied through the audit view and the item list, not by a
synchronous response. When no daemon is running, the operation polls directly as it does today, and
the response is synchronous and complete.

**Alternatives considered.** `SIGUSR1`/`SIGUSR2` to the lock holder (rejected: PID-from-file
signalling, no durability, and a signal handler in a loop whose entire design is "one thread, no
interleaving"). A row in the database (rejected: heavier than an empty file for a flag whose whole
lifetime is one tick, and it would need its own cleanup).

---

## R6 — The pause lives in a one-row table, not a settings key-value store

**Decision.** Migration 002 adds `dispatch_control`, a table constrained to a single row, holding
`paused`, `paused_at`, and `paused_by`. `daemon.job_dispatch` reads it first and returns without
dispatching when set. `health.write_heartbeat` carries `dispatch_paused`.

**Rationale.** Durability across restart and reboot is FR-035, and it is the whole point: a pause
that lapses when the daemon restarts is worse than no pause, because the author believes work is held
when it is not. The database gives that for free, atomically, alongside the data it governs.

One row and three columns rather than a general settings store because there is exactly one setting
and Principle I forbids configuration machinery with one caller and no second use in hand. If a
second control ever needs persisting, adding a column is a three-line migration.

`paused_by` records which interface set it — `web` or `cli` — so the audit question "who stopped
dispatch" is answerable from the state as well as from the log.

**Alternatives considered.** A marker file like R5's (rejected: a pause is durable, transactional
state, not a one-tick request; it would need its own atomic write and its own staleness reasoning).
A configuration-file setting (rejected: `docs/state.md` draws the line — configuration is what the
author declares, the database is what the system observed, and a pause is an operational act, not a
declaration; it would also need the daemon to re-read config at runtime).

---

## R7 — The state machine is the idempotency guard; POST/redirect/GET is the browser half

**Decision.** No nonces, no idempotency keys, no de-duplication table. Every action re-reads the
work item and, where it changes state, does so through `states.transition_work_item` inside
`db.transaction`. A second submission finds an illegal transition and is refused with an explanation.
Every POST answers `303 See Other`, so a reload never re-posts.

**Rationale.** 001 already made this work: `transition_work_item` raises `IllegalTransition` for any
move the state machine does not allow, and treats a re-assertion of the *same* state as a no-op
rather than an error — which is exactly the right behaviour for a duplicate `abandon`. The transition
happens under `BEGIN IMMEDIATE`, so two concurrent writers serialise rather than interleave. FR-027
(re-validate at submission) and FR-028 (no duplicate effects) are therefore the same mechanism seen
from two angles, and it is a mechanism that already exists and is already tested.

The one place needing care is the pair of operations that *read* then *act* outside a transaction —
`resume` and `restart` check `item.state` before calling `dispatch_item`. The check is advisory; the
transition inside `dispatch_item` is authoritative. The web must therefore report the operation's
returned `Result`, never assume its pre-check succeeded.

**Alternatives considered.** A single-use token in each confirm form (rejected: needs server-side
state, which FR-045 forbids, or a signed cookie, which needs a secret this design deliberately does
not have). A de-duplication window keyed on item and action (rejected: solves with bookkeeping what
the state machine already solves with meaning).

---

## R8 — Confirmation is a page, not a dialog

**Decision.** A control that stops, starts, or discards work is a link to `GET /item/<id>/confirm/
<action>`, which renders what is about to happen, re-validated against current state, with a form
whose POST performs it.

**Rationale.** FR-026 requires a confirmation step distinct from the control that initiates it. A
separate page gives that with no JavaScript, survives a phone rotating or sleeping between the two
steps, and — the part that matters — is the natural place to show the FR-027 re-validation. If the
item changed after the list was rendered, the confirm page says so instead of the action failing
after the fact.

001 used typed confirmations for the genuinely destructive `worktree remove --force`. That
ergonomic does not survive a phone keyboard, which is one of the reasons FR-031 keeps checkout
removal out of this interface entirely; nothing offered here is destructive in that sense.

**Alternatives considered.** A JavaScript `confirm()` (rejected: no record of what was shown, breaks
without scripting, and cannot re-validate). A two-tap control on one page (rejected: needs state
between taps, and "tap twice" is exactly what a phone does by accident).

---

## R9 — Resume signals are split by cost

**Decision.** Two of the four FR-013 signals — uncommitted changes, commits on branch — are local
git calls and are recomputed on every render, including every auto-refresh. The other two — issue
closed, open pull request — reach GitHub and are computed on render but cached in-process for 60
seconds, keyed by item and branch.

**Rationale.** This was the one design problem Phase 1 surfaced. An interrupted view with five items,
auto-refreshing every 10 seconds, would make 1,800 GitHub calls an hour asking a question that cannot
change as a result of anything happening on this machine. That competes directly with the polling
budget FR-008 exists to protect.

The split follows the actual semantics. The local signals are volatile precisely because the author
may be in the worktree with an editor open — `docs/state.md` says they are computed on demand and
never stored "because a stored copy would be wrong the moment I touched the directory", and that
reasoning applies to them and not to the remote ones. FR-013's prohibition on a stored copy is
honoured in the sense it was written: nothing is persisted, and the volatile signals are never
served from anything but a fresh call.

The cache is per-process, non-authoritative, bounded by time and by the number of interrupted items,
and lost on restart. Each rendered value carries its age, so a stale one is visible rather than
implied.

**Alternatives considered.** Recomputing everything every render (rejected: the rate-limit arithmetic
above). Omitting the remote signals from the list and showing them only on the item page (rejected:
fails FR-013 as written, and the whole point is deciding across several interrupted items at once).
Persisting them (rejected: FR-013, and they would need invalidation).

---

## R10 — Attach opens a new terminal tab running the host's attach command

**Decision.** A new `operations.attach(ctx, item_id)` reads the item's latest session, refuses unless
it is `running`, and calls `Display.open()` with `SessionHost.attach_command(handle)` as the argv,
titled for the item. It never touches session state.

**Rationale.** Both halves already exist and were measured in M0: `DtachHost.attach_command` returns
`dtach -a <socket>`, and `KittyDisplay.open` launches a tab running an argv. M0 confirmed
reattachment repaints fully and that two viewers may attach at once, so no "is something already
attached" check is needed — FR-025's tolerance requirement is satisfied by the host's measured
capability rather than by logic.

Failure is a `BoundaryError` from the display probe, which becomes a visible refusal. `Display.is_open`
and `find_by_var` are available if a future version wants to report that a window for this session
already exists; nothing here needs it, and the `Display` protocol has no focus operation, so opening
a second viewer is both the simple path and the measured-good one.

**Alternatives considered.** A terminal emulator in the browser (rejected: a large dependency and a
websocket, to duplicate what Remote Control already gives the phone — the spec excludes it). Printing
the attach command for the author to paste (rejected: that is what `robot-army show` already does;
the control exists to save exactly that step).

---

## R11 — The web process opens the database read-write but never migrates

**Decision.** `db.connect()` directly, then verify `PRAGMA user_version == SCHEMA_VERSION` as a
startup precondition and refuse to serve if it does not match. `open_database()`, which migrates, is
not used. One connection per request, closed with the `Context`.

**Rationale.** Two processes racing to run the same migration is a failure mode worth removing rather
than surviving; the daemon owns the schema and the interface follows it. A version mismatch means the
author upgraded the code and has not restarted the daemon, which is worth a clear refusal rather than
a subtly wrong page.

Read-write rather than read-only because the interface does mutate — pausing, abandoning,
acknowledging. WAL mode (already set by `db.connect`) is what makes concurrent reads work while the
daemon holds a write connection; 001 chose it for exactly this reason and named `robot-army status`
against a running daemon as the case.

Per-request connections because `sqlite3` connections are not shareable across threads by default and
`ThreadingHTTPServer` gives each request its own. They are cheap: a `connect` plus three pragmas.

**Alternatives considered.** A shared connection with `check_same_thread=False` and a lock (rejected:
serialises every read behind every write for no gain at this scale). A read-only connection plus a
separate writer (rejected: two connection lifecycles to reason about, for one user's occasional POST).

---

## R12 — Assets are module constants at fixed routes; nothing is read from disk

**Decision.** One CSS string and one small JavaScript string live as constants in `web/html.py`,
served from `/static/app.css` and `/static/app.js` with long cache headers. No request ever
contributes to a filesystem path.

**Rationale.** SC-009 requires the interface to work with the machine offline, which rules out any
web font, CDN stylesheet, or icon set — so the assets are small enough to embed anyway. Embedding
them also removes the entire class of path-traversal bug that `SimpleHTTPRequestHandler` is
periodically patched for (R1), by removing the capability rather than guarding it.

Inline `<style>` was considered and rejected in favour of separate routes only because a phone
re-fetching a page every 10 seconds should not re-download the stylesheet each time.

**Secrets.** FR-020 is enforced at the boundary of what may be rendered: the payloads come from
`operations.*`, which already pass through `audit.redact` on their way to the log, and the audit view
renders records that were redacted at write time. The token is never in a payload to begin with, and
a test asserts that no rendered page contains the configured token value.

---

## R13 — The bind address is validated and announced

**Decision.** New `[web]` configuration: `bind` (default `127.0.0.1`), `port` (default `8420`),
`refresh_seconds` (default `10`). At startup the resolved address is written to the audit log as
`web.start` and printed. If `bind` parses as a globally routable address, the server refuses to start
with exit `3`. If it is anything other than loopback, a one-line warning states that anything able to
reach it has full control.

**Rationale.** FR-004 requires both the announcement and the refusal. The announcement matters
because, under FR-003's model, the bind address *is* the security policy — it is the one fact about
this design that must never be silent. `ipaddress.ip_address(...).is_global` gives the refusal in one
line, and correctly permits `192.168.*`, `10.*`, `100.64.*` (the VPN range), and `0.0.0.0`.

`0.0.0.0` cannot be classified — it means "every interface", including any the machine gains later —
so it warns rather than refuses. Refusing it would push the author toward pinning an address that a
DHCP lease can change, which trades a real ergonomic problem for a theoretical safety one.

Port 8420 is arbitrary, above 1024 so no privilege is needed, and outside the ranges the author's
other tooling uses.

**Alternatives considered.** Binding a unix socket and requiring a proxy (rejected: Principle II
forbids assuming deployment infrastructure). Defaulting to `0.0.0.0` (rejected: an unconfigured
install would be reachable, which the Operating Constraints rule out for anything consequential).

---

## R14 — The audit view reads daily files newest-first and stops when the page is full

**Decision.** Page backwards: newest daily file first, each file read and its lines collected, then
reversed, until the requested page size is satisfied or files run out. Filters (item, time window,
outcome) are applied while scanning. Unparseable lines are skipped and counted, and the count is
rendered.

**Rationale.** `docs/logging.md` fixes the format: one JSON object per line, one file per day, never
deleted automatically. SC-014 requires a bounded page in under 2 seconds against 100,000 records; a
day's file is at most a few megabytes and reading one or two of them satisfies any first page.
`operations.read_log` already implements the filtering and the skip-and-count behaviour FR-044
requires — the web reuses it and adds paging rather than reimplementing the reader.

The skip-and-count is not defensive programming: a partially written final line is expected, because
the process can die between the write and the flush, and 001 decided that refusing to read the log
over one truncated line is the wrong trade.

**Alternatives considered.** Indexing the log into SQLite (rejected: a second copy of the record of
truth, and an indexer to keep it current, to speed up a view nobody loads in a loop). Loading the
whole log and filtering in memory (rejected: SC-014, and it grows without bound by construction).

---

## R15 — Every route is a function of a payload, so tests do not need a socket

**Decision.** The route table maps method and path to a handler that returns a
`(status, headers, payload)` triple. Rendering and routing are unit-testable as pure functions;
exactly one integration test binds a real ephemeral port.

**Rationale.** The constitution requires tests on failure and interruption paths, not only success
paths, and requires them for code parsing external input — which a request handler is. Keeping the
socket out of most tests is what makes the failure cases (bad method, unknown item, illegal
transition, effect-level mismatch, daemon down) cheap enough to write exhaustively.

The one end-to-end test exists because the parts a pure-function test cannot reach — that the server
actually binds, that a real browser-shaped request round-trips, that a 303 redirect lands — are
precisely the parts that break silently. This mirrors 001's own split, where the single test needing
a live session registry was the one that caught the worst bug in the milestone.
