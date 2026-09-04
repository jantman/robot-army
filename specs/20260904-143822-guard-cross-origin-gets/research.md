# Phase 0 research: guarding cross-origin reads, and making the reads cheap

**Feature**: `specs/20260904-143822-guard-cross-origin-gets`

Seven questions had to be settled before the design was writable. Six were choices between
plausible alternatives; one (R3) is a Principle III exception that has to be argued rather than
chosen.

---

## R1 — Where the read-side check goes, and why not in `_perform`

**Decision**: in `handle()`, immediately after `check_host`, applied to every method **except**
the mutating ones. `POST` keeps going through `check_same_origin` inside `_perform`, exactly as
today.

**Rationale**: `server.py`'s own docstring states two properties as requirements, not choices:
"Every mutating request is audited, intent first (FR-038), and no error response is returned
without a corresponding record (FR-039, FR-040). Both follow from every `POST` passing through
`_perform`, which writes the intent before any check runs."

A pre-routing check would run *before* `app.context()` exists — that is the whole point, since
the context is a SQLite connection and an audit file handle. Moving the POST check there would
therefore delete the record that a forged POST arrived, which is the one way such an attempt
would ever be noticed. So the pre-routing check must skip the methods `_perform` covers.

For reads there is no such loss: nothing about a GET is recorded today, so refusing one early
removes no record that existed.

**Alternatives considered**:

- *Check every method up front, and re-check inside `_perform`.* Two evaluations of one rule,
  and the first would still swallow the audit record for a cross-site POST because it fires
  first. No benefit for the duplication.
- *Check inside each view handler.* Twelve call sites, each of which can be forgotten by the
  thirteenth view. `check_host` is applied in `handle()` for precisely this reason.
- *Check after routing but before the context.* Indistinguishable in effect from checking
  before routing, and it means a cross-site request to a route that does not exist gets a 404
  rather than a 403 — leaking which routes exist to the very caller being refused.

---

## R2 — What counts as "another site"

**Decision**: reuse `check_same_origin` unchanged as the rule — allow `Sec-Fetch-Site` values
`same-origin` and `none`, refuse any other value that is present, allow the header's absence,
and refuse an `Origin` whose netloc differs from `Host`. The same function is called from both
places; only its prose generalises from "state-changing request" to "request".

**Rationale**: the issue's fix line says "refuse when `Sec-Fetch-Site` is present and is
`cross-site`", which would admit `same-site`. But `check_host` already refuses every `Host`
that is not an IP literal or `localhost`, so an honest `same-site` label cannot arise here:
`same-site` means same registrable domain, different origin, and an IP literal has no
registrable domain to share. The value can therefore only appear on a request that is already
being refused for a different reason, or on a forged one. Admitting it would be a second,
subtly weaker rule sitting next to the first — the shape that rots, because the next person to
change one will not know to change the other.

`none` must be allowed: it is what a browser sends for a top-level navigation the user started
themselves, which is how the maintainer opens the interface from the address bar or a bookmark.

Absence must be allowed for the reason already written into `check_same_origin`: `curl` sends
neither header, the quickstart drives every control with it, and a client that could forge the
header can reach the port directly anyway — which is the exposure model FR-003 already accepts.

**Alternatives considered**:

- *Allow a cross-site top-level navigation (`Sec-Fetch-Mode: navigate`).* This would let the
  maintainer click a link to the interface from another site. It is an extra rule for a case
  that does not arise — nothing links to `http://127.0.0.1:8420` — and it would reopen the
  cheapest half of the attack, since a page can navigate a hidden iframe. Rejected. (Framing is
  separately refused by `frame-ancestors 'none'`, but the rule should not depend on that.)
- *A separate, more lenient `check_read_origin`.* Two functions, two messages, two sets of
  tests, one rule. Rejected on Principle I.

---

## R3 — Accounting for a refused read (Principle III exception)

**Decision**: count refused cross-site requests on `WebApp` and fold the total into the
existing `web.stop` audit record, beside `refused_over_capacity`. No per-refusal record.

**Rationale, and why this is an exception rather than a gap**: writing an audit record requires
a `Context`, which is a SQLite connection plus an open audit file handle. The refusal fires
before any context exists, deliberately — that is the work being refused. A record per refusal
would open, per refused request, exactly the resources the refusal exists to avoid opening,
making the log amplify the flood it documents. This is the identical argument the connection
cap (RA-13) made for `refused_over_capacity`, and this feature follows that precedent rather
than inventing a second policy for the same situation.

What the record still answers, from the log alone: whether this run turned cross-site requests
away, and how many. What it does not answer: which paths, at what times, from what origin. That
is the enumerated cost, and it is accepted because the alternative is a denial-of-service
amplifier in the audit log.

A cross-site **POST** is unaffected and keeps its full `web.<action>` intent-and-error pair.

**Alternatives considered**:

- *One record per refusal.* Rejected above.
- *One record per saturation episode, as the connection cap prints to stderr.* There is no
  natural episode boundary for a request-level refusal, and inventing one is more machinery
  than a counter. The stderr line the connection cap prints is not copied here for the same
  reason.

---

## R4 — Reading a daily audit file backwards

**Decision**: read each file in `LOG_SCAN_BLOCK_BYTES` (64 KiB) blocks from the end, carrying
the partial line at the front of each block into the next, and stop the whole request after
`LOG_SCAN_BUDGET_BYTES` (8 MiB) read across all files.

**Rationale for the block size**: 64 KiB is comfortably larger than any single audit record
(the longest carry a prompt preview, still kilobytes), so the "record longer than one block"
path is exercised by a test rather than by production. It is small enough that a page that
fills from the newest few records reads one block and stops — which is the common case, and the
case SC-014's existing two-second budget is measured against.

**Rationale for the budget**: a day's file is a few megabytes, so 8 MiB spans the newest two to
three days — every unfiltered page and every recent-item filter fills long before it. It is
reached only by a filter matching nothing far back in history, which is the abuse case and the
patient case, and both are served correctly by stopping and offering the next page.

**Rationale for the direction**: the current `path.read_text().splitlines()` allocates the whole
file plus a list of every line in it, to return at most a thousand records from the end. Reading
backwards is not an optimisation of that; it is the operation the function's own name and
docstring already describe.

**Partial final line**: unchanged. A file whose last line was being written when the process
died yields one unparseable line, counted in `skipped_lines` and reported on the page, exactly
as today.

**Alternatives considered**:

- *`mmap` and scan backwards.* Faster, and wrong at the boundary this project actually hits: the
  newest file is being appended to by the daemon while it is read, and a mapping's length is
  fixed at map time. Block reads through a normal file object have no such edge.
- *Index the log into SQLite.* Already rejected by the existing test's docstring: a second copy
  of the record of truth plus an indexer to keep it current, to speed up a view nobody loads in
  a loop.
- *Cap the number of files instead of the bytes.* A cap on files is a cap on nothing — one file
  can be arbitrarily large.

---

## R5 — Making truncation resumable, and what the cursor has to carry

**Decision**: the cursor payload becomes `{"f": <file name>, "b": <byte offset>}`, where `b` is
an absolute offset from the start of the file and the next page scans backwards over
`[0, b)`. `b == 0` means "that file is finished, start at the one before it". The old
`{"f", "n"}` payload is not accepted; `_decode_cursor` already returns `None` for anything it
cannot read, which restarts from the newest page — the behaviour its docstring documents for a
cursor naming a page that no longer exists, which is exactly what a cursor from the previous
version is.

**Rationale**: without a resumable position, a truncated scan is a dead end — a filter matching
nothing beyond the budget could never reach the older records, which would be a functional
regression dressed as a fix. With one, truncation becomes a page boundary like any other.

**Why an absolute offset rather than the current count of matches**: two reasons, and the second
is the better one.

1. It is stable under appends. The daemon writes to today's file between the two requests of a
   page turn; "the Nth match counting from the end" names a different record after an append,
   while "the byte at position P" does not.
2. It removes an O(n) re-scan. Today, page *k* of a file re-reads and re-judges every record of
   pages 1..*k*−1 to skip them. With an offset the scan resumes where it stopped, so paging
   through a file is linear in the file rather than quadratic in the number of pages.

Disjointness, which is what the existing tests assert, follows by construction: page boundaries
fall on line boundaries and each page covers a byte range strictly below the previous page's.

**Alternatives considered**:

- *Keep the match count and add the offset beside it.* Two positions that can disagree, and the
  count would still have to be maintained for a mechanism nothing reads. Rejected.
- *Truncate without a cursor, and tell the reader to narrow the filter.* Cheaper, and it makes
  the audit log's own history unreachable through the interface that exists to read it.

---

## R6 — Reusing the local worktree signals

**Decision**: an in-process, TTL-bounded cache beside the one that already exists for the
GitHub-derived pair. `LOCAL_SIGNAL_TTL_SECONDS = 5.0`.

**Rationale for five seconds**: it sits deliberately *below* the default
`[web] refresh_seconds = 10`. That means an open page auto-refreshing on its timer observes the
worktree afresh on every refresh — no freshness is lost for the person watching the page, which
is the objection `local_resume_signals`' current docstring raises ("a stored copy would be wrong
the moment I touched the directory"). What five seconds does remove is the *burst*: a caller
issuing requests in a loop gets one observation per item per five seconds instead of several
`git` subprocesses per request. The two properties are exactly the requirement, and picking a
number above the refresh interval would trade the first away to buy nothing extra.

**Key** (FR-007): `(item id, repo key, worktree path, branch, base ref)`. Every input
`worktree.condition` is given. An item whose worktree was reclaimed or whose branch changed is a
different key, so it is observed afresh rather than served an answer about somewhere else. This
is the same rule the remote cache already applies with `(item id, branch)`, extended to the
inputs the local half actually uses.

**Failures are not cached** (FR-008): a `BoundaryError` from `worktree.condition` becomes
`worktree_error`, and caching it would suppress the retry that would have shown it recovering.
This is the reasoning `remote_resume_signals` already applies to `TransportError`, written down
once and applied to both halves.

**Age is carried** (FR-009): `local_signals_age_seconds`, rendered as its own footnote beside
the existing "GitHub signals Ns old (cached)" line. Two separately-aged halves get two lines;
merging them into one number would have to lie about one of them.

**Invalidation** (FR-010): `_perform` clears both caches for the entity it acted on, after the
body succeeds. It is the single choke point every POST passes through and it already knows the
entity type and id, so this is one call in one place rather than a rule each action has to
remember. A slow action whose work completes on the worker thread afterwards is covered by the
five-second bound rather than by a second invalidation point — the page that would show the
stale value is at least one refresh away.

**Bounding** (FR-012): expired entries are purged on insert, in a helper shared by both caches.
That bounds each to the number of distinct items observed within one TTL window, which is
bounded by the number of rows the views render. No LRU, no maximum size, no eviction policy — a
purge is three lines and there is no second requirement asking for more.

**Alternatives considered**:

- *Compute the signals only for the item being expanded* — the issue's second suggestion.
  Rejected in the spec's Assumptions and restated here: it changes how the interrupted view is
  read, for a saving the cache already gets, and it would leave `/item/<id>` — which shows one
  item and has the same cost per render — unhelped.
- *Cache with no TTL and invalidate on every action.* Wrong for the case the current docstring
  is written about: the maintainer edits a file in the worktree, which no action passes through.
- *Persist the cache.* Forbidden by FR-013 of the original milestone and by FR-011 here, and it
  would be wrong across a restart for the same reason.

---

## R7 — One capacity observation per render

**Decision**: `handle()` computes `capacity_mod.snapshot(...)` once, beside `level` and
`include_simulated`, and hands it to both `pages.chrome(capacity=...)` and the route handler via
`params["capacity"]`. `queue_view` takes it as a keyword. Both keep a `None` default that falls
back to computing one, so direct callers and existing tests are unchanged.

**Rationale**: `handle()` already carries the argument for this, written about the effect level:
"Resolved once, here, and handed to both the chrome and the handler. Deriving it twice would
read the heartbeat and the lock twice and could — across a daemon starting mid-request — answer
differently in the two halves of one page." Capacity is the same shape of fact with the same
hazard: `/queue` observes the process table twice per render today, and the two observations can
disagree, so the chrome pill and the queue's own capacity block can contradict each other on one
page. Fixing the cost fixes the correctness problem for free.

**Scope note**: this does not touch RA-46 ("page renders can take the SQLite write lock"). The
snapshot is still taken on a GET; it is taken once instead of twice. Halving it is what this
finding asks for.

**Alternatives considered**:

- *Put the raw snapshot inside the chrome payload.* The chrome payload is serialised into the
  `?json` representation; a `CapacitySnapshot` is not JSON, and adding a private key that has to
  be stripped before serialising is worse than a parameter.
- *Cache the snapshot per request in a thread-local.* Invisible coupling in place of a visible
  parameter, on a code path that already threads two other per-request facts explicitly.
