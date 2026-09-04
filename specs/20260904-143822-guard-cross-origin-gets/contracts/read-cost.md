# Contract: what a read is refused for, and what a served read costs

**Feature**: `specs/20260904-143822-guard-cross-origin-gets`
**Surface**: the HTTP interface served by `robot-army serve`, and the `operations` functions
behind its read views.

Five rules. The first changes which requests are answered at all; the rest change what
answering one costs, and two of them add a field the client can read.

---

## C1 — A read a browser reports as coming from another site is refused

**Rule**: every request, of every method, whose `Sec-Fetch-Site` header is present and is
neither `same-origin` nor `none` is refused with `403` and exit code `3`
(`EXIT_PRECONDITION`); so is every request whose `Origin` netloc differs from its `Host`.

This is the rule `check_same_origin` already applies. What changes is its reach: it now runs
for every method, not only `POST`.

| Client | `Sec-Fetch-Site` | Before | After |
|---|---|---|---|
| `curl`, `wget`, a script | *absent* | served | **served** — unchanged, and load-bearing |
| Address bar, bookmark, `Ctrl-R` | `none` | served | **served** |
| A link or form on a page this server rendered | `same-origin` | served | **served** |
| `fetch()` from another site | `cross-site` | **served** (GET) / refused (POST) | **refused** |
| A subresource from another site | `cross-site` | **served** (GET) | **refused** |
| Same registrable domain, other origin | `same-site` | **served** (GET) / refused (POST) | **refused** |

**Where it fires**: in `handle()`, immediately after `check_host` and **before** routing, before
`WebApp.context()`, and therefore before any SQLite connection, audit file handle, capacity
observation, `git` subprocess, or audit-file read exists for that request.

**Method exclusion**: `POST` is not checked here. It keeps its existing check inside `_perform`,
which writes the intent record before any check runs. This is not an oversight and not a
weakening: a cross-site POST is refused exactly as it is today, with a `403` and a full audit
pair. Checking it early would refuse it *without* the record — the record being the only way a
forged POST would ever be noticed.

**Static assets are covered.** `/style.css` and `/app.js` are refused cross-site too. They are
served from module constants and cost nothing, so this buys little; it is done because "every
read, one rule" is a smaller thing to hold than "every read except two".

**Client-visible effect**: the standard refusal — an HTML page or a JSON body carrying
`{"ok": false, "reason": ..., "code": 3}`, chosen by `Accept`, with the usual security headers.
No new status code and no new shape.

---

## C2 — A refused read is counted, not individually recorded

**Rule**: no audit record is written for a request refused under C1 when the method is
non-mutating. The count for the run appears once, in the `web.stop` record's `detail`, as
`refused_cross_site`.

**Why this is an enumerated Principle III exception rather than a gap**: writing an audit record
requires a `Context`, which is a SQLite connection plus an open audit file handle. C1 fires
before any context exists, deliberately — opening those is the work being refused. A record per
refusal would open, per refused request, precisely the resources the refusal exists to avoid,
making the log amplify the flood it documents. This is the same argument the connection cap
(RA-13) made for `refused_over_capacity`, and this contract follows that precedent rather than
setting a second policy for the same situation.

**What the log still answers**: whether this run turned cross-site reads away, and how many.
**What it does not**: their paths, their times, or their claimed origins. That is the cost, and
it is the whole of it.

**A cross-site POST is unaffected** and keeps its `web.<action>` intent-and-error pair.

---

## C3 — A rendered page observes the machine once

**Rule**: producing one HTTP response performs at most one `capacity.snapshot` — one session
registry read and one `/proc` enumeration.

| Response | Before | After |
|---|---|---|
| `/queue` | 2 | **1** |
| Any other routed view | 1 | 1 |
| A refused or redirected `POST` | 1 | 1 |
| `404`, `405`, schema `503`, static asset | 0 | 0 |
| A read refused under C1 | 1 | **0** |

**Second effect, not incidental**: the capacity shown in the page chrome and the capacity shown
in `/queue`'s own body now come from the same observation, so they cannot disagree within one
page. Before this change they were two observations taken moments apart and could.

---

## C4 — An item's local signals are observed at most once per five seconds

**Rule**: `worktree.condition` — several `git` subprocesses — runs at most once per five seconds
per distinct `(item, repository, worktree path, branch, base ref)`. Every returned payload
carries `local_signals_age_seconds`.

| Field | Value | Meaning |
|---|---|---|
| `local_signals_age_seconds` | `0` | observed during this request |
| | `n > 0` | reused from an observation `n` seconds ago |

**Freshness guarantees**, in the order they matter:

1. An observation that **failed** is never reused. The next request tries again, so a checkout
   that becomes readable is reported as readable on the next render rather than five seconds
   later.
2. **Acting on an item** drops its cached signals, local and remote, so the page rendered after
   an action reflects the action.
3. A **key change** — the worktree reclaimed, the branch renamed, the base branch reconfigured —
   is a different key and is observed afresh. A cached value is never re-attributed to a
   different subject.
4. The five seconds is **below** the default `[web] refresh_seconds = 10`, so a page left open
   observes the worktree afresh on every refresh. A maintainer editing files in the worktree
   sees the change on the next refresh, as they do today.

**Rendered**: `/interrupted` and `/item/<id>` show *"checkout signals read just now"* or
*"checkout signals `n`s old (cached)"*, beside the GitHub line that already says the same about
its own half. A reused value is visible as reused; it is never implied to be current.

---

## C5 — A log page reads bounded bytes, and says so when it stops

**Rule**: producing one page of `/log` reads at most `LOG_SCAN_BUDGET_BYTES` (8 MiB) from audit
files, in `LOG_SCAN_BLOCK_BYTES` (64 KiB) blocks from the end of each file. No audit file is
ever read whole into memory.

**Unchanged**: which records a page returns, their newest-first order, the page size and its
1000-record ceiling, the `skipped_lines` count and its explanation, and the disjointness of
successive pages under a cursor. An unfiltered first page returns exactly what it returns today.

**Added fields**:

| Field | Type | Meaning |
|---|---|---|
| `truncated` | `bool` | the budget was reached before the page filled |
| `bytes_scanned` | `int` | bytes read from audit files for this page |

**When truncated**: `has_more` is `true` and `next_cursor` names where the scan stopped, so the
history stays reachable — a truncated page is a page boundary, not a dead end. The page renders
a line saying the scan stopped early, so an empty result is never mistaken for an empty history.

**Cursor format change** (a deliberate break, Principle V): the cursor payload becomes
`{"f": <file>, "b": <byte offset>}`. A cursor issued by a previous version has no `"b"` key, so
it is unreadable and the request restarts from the newest page — the behaviour `_decode_cursor`
already documents for a cursor naming a page that no longer exists. A cursor is a paging token
in a URL, never stored, so the only client affected is a browser tab left open across the
upgrade, which recovers by showing the newest page.

**Side effect worth naming**: because the cursor now carries a position rather than a count of
records already consumed, page *k* of a file no longer re-reads and re-judges pages 1..*k*−1.
Paging through a file becomes linear in the file rather than quadratic in the number of pages.

---

## What a killed request loses

Nothing. Every rule here concerns reading. No file is written, no row is changed, and both
in-memory caches die with the process — which is correct, since an observation made before a
restart says nothing about the worktree after it. A request killed mid-scan leaves the audit
files exactly as it found them.
