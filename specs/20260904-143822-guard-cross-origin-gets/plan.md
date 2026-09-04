# Implementation Plan: Guard cross-origin GETs, and stop the read views being expensive

**Branch**: `speckit/20260904-143822-guard-cross-origin-gets` | **Date**: 2026-09-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/20260904-143822-guard-cross-origin-gets/spec.md`

## Summary

`check_same_origin` is called from exactly one place — inside `_perform`, which only `POST`
reaches. Every read view is therefore unguarded, and `check_host` lets any IP-literal `Host`
through, so a page on another site can loop `fetch('http://127.0.0.1:8420/interrupted',
{mode:'no-cors'})` and the work happens even though the response is opaque to it. The work is
not small: `/interrupted` forks `git` several times *per displayed item, per request*; `/log`
reads whole `audit-*.jsonl` files into memory and a filter matching nothing reads every one of
them; every rendered page enumerates the process table, and `/queue` does it twice.

Four changes, in that order of importance:

1. **`check_same_origin` moves up to `handle()` for every non-mutating method**, immediately
   after `check_host`. `POST` is deliberately excluded and keeps its existing call inside
   `_perform`, because `_perform` writes the intent record *before* any check runs and a
   pre-routing refusal has no `Context` to write with — moving it would delete the record that
   a forged POST arrived (research R1). The rule itself is unchanged and the function is reused
   (R2). Refused reads are counted on `WebApp` and the total folded into `web.stop`; per-refusal
   records are an enumerated Principle III exception, on the same argument the connection cap
   made (R3).

2. **`local_resume_signals` gains a five-second, in-process, TTL-bounded cache**, keyed on every
   input `worktree.condition` is given, with failures uncached and the age carried in the
   payload as `local_signals_age_seconds`. Five seconds is below the default ten-second page
   refresh, so an open page still observes the worktree afresh on every refresh; what it removes
   is the burst (R6).

3. **`_scan_file_backwards` reads 64 KiB blocks from the end** instead of
   `read_text().splitlines()`, and a request stops after 8 MiB read across all files. The cursor
   payload changes from "(file, matches consumed)" to "(file, byte offset)" so a truncated scan
   is resumable rather than a dead end — which also removes the O(n) re-scan the current cursor
   performs on every page turn (R4, R5).

4. **`handle()` takes the capacity snapshot once** and hands it to both the chrome and the route
   handler, exactly as it already does for the effect level. `/queue` stops observing the
   process table twice, and the chrome pill and the queue's capacity block can no longer
   disagree within one page (R7).

Out of scope, and named so the boundary is deliberate: RA-46 (the snapshot takes the SQLite
write lock on a GET) — this halves the number of times that happens and does not change that it
happens. Per-request rate limiting — the refusal in (1) is the answer to the flood, and a rate
limit would also throttle the maintainer's own auto-refresh. Any change to which routes exist or
what any view renders, beyond the two age footnotes and the truncation notice.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: standard library only — `json`, `pathlib`, `threading`, `time`,
`base64`. All are already imported by the modules being changed. No new dependency, and none is
needed.

**Storage**: unchanged. No schema change, no migration, no new file, no new configuration key.
The only persisted change is one added field inside the existing `web.stop` audit record. The
audit log's *format* is untouched; only how it is read changes.

**Testing**: `pytest`, run as `uv run pytest`. Three existing modules gain cases
(`tests/unit/test_web_routing.py`, `tests/unit/test_resume_signals.py`,
`tests/unit/test_web_log.py`) and one new module,
`tests/unit/test_web_read_cost.py`, holds the counting tests — how many version-control
observations, how many capacity observations, how many bytes read — which are assertions about
cost rather than about any one view.

**Target Platform**: one Linux machine, loopback by default (`127.0.0.1:8420`).

**Project Type**: single Python package (`src/robot_army`) with a CLI and a small web interface.

**Performance Goals**: expressed as ceilings, not throughput. A cross-site read costs zero
subprocesses, zero file reads and zero process enumerations. A rendered page costs one capacity
observation. An item's local signals cost at most one `worktree.condition` per five seconds. A
log page reads at most 8 MiB.

**Constraints**: the read refusal must fire before `app.context()` — the whole point is that it
opens nothing. It must not weaken the POST path or displace its audit record. The log page's
records, their order, and the disjointness of successive pages must be unchanged.
`_REMOTE_SIGNAL_CACHE`'s existing behaviour and its tests must survive the shared purge helper.

**Scope/Scale**: roughly 150 lines across four source modules
(`web/server.py`, `web/pages.py`, `operations.py`, and nothing else), one new test module, cases
added to three existing ones, and one README bullet plus an amendment to two others.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design — see the re-check at the end.*

### I. Simplicity First (YAGNI & KISS)

**Pass.** No new dependency, no new module, no new configuration knob. Three of the four
changes are smaller than what they replace:

- The origin check adds no rule and no function — it calls the one that exists, from a second
  place, with its prose generalised.
- The log scan replaces `read_text().splitlines()` with a block loop, and replaces a cursor
  field with a different cursor field. It removes a quadratic re-scan rather than adding
  machinery.
- The capacity change removes a call.

The one genuinely new structure is a second in-memory dict beside `_REMOTE_SIGNAL_CACHE`,
following its shape exactly — same lock discipline, same TTL idea, same "failures are not
cached" rule — with the expiry purge factored into one helper both use. Two dicts rather than
one because the two halves have different TTLs for different reasons, which is the fact the
existing split already encodes.

Three temptations were declined and recorded in `research.md`: an LRU or size cap on the caches
(R6 — a purge on insert already bounds them), a separate lenient read-side check (R2), and
`mmap` for the backwards scan (R4).

### II. Single-User, Local-First

**Pass.** The origin check identifies nobody, authorises nobody, and holds no state per client —
it asks one question about one header, which is what keeps it from being the authentication
Principle II forbids. Nothing new is persisted; both caches are per-process and lost on restart.
No hosted service, no network dependency, no deployment assumption. The two new constants live
in code, not in configuration, consistent with `REMOTE_SIGNAL_TTL_SECONDS` and with the
connection bounds added by the previous feature.

### III. Total Accountability

**Pass, with one enumerated exception.**

**What this logs.** A cross-site **POST** is unchanged: `_perform` writes its intent record
before any check and turns the refusal into that pair's `error` outcome. `web.stop` gains
`refused_cross_site`, the count of read requests this run turned away, beside the existing
`refused_over_capacity`. Nothing else in this feature performs an action that changes state
outside the process — the caches write nothing, the log scan reads only, and the capacity change
removes an observation rather than adding one.

**The exception.** A refused cross-site *read* gets no individual audit record. Writing one
requires a `Context`, which is a SQLite connection plus an open audit file handle; the refusal
fires before any context exists, deliberately, because opening those is the work being refused.
A record per refusal would therefore open, per refused request, precisely the resources the
refusal exists to avoid — making the log amplify the flood it documents. This is the identical
argument the connection cap (RA-13) made for `refused_over_capacity`, and this feature follows
that precedent rather than inventing a second policy for the same situation. The cost is named
exactly: from the log alone a reader learns *whether* this run turned cross-site reads away and
*how many*, but not their paths, times, or claimed origins.

**Silent failure.** Two places could have become silent and are not. A truncated log scan is
reported in the response payload (`truncated`) and on the page, so an empty page is never
mistaken for an empty history. A cached local signal carries `local_signals_age_seconds` and the
page renders it, so a reused observation is visible as reused rather than implied to be current.
A failed `worktree.condition` is still surfaced as `worktree_error` and is never cached, so the
next render retries and a recovery is visible.

### IV. Interruption Tolerance

**Pass.** Everything here is a read. Killed halfway, nothing is left inconsistent: no file is
written, no state is mutated, both caches vanish with the process — which is correct, since a
signal observed before a restart says nothing about the worktree after it. The backwards scan
must tolerate a daily file the daemon is appending to and a final line that was half-written
when a process died; both already fall out as one unparseable line, counted and reported. No
network call is added, so the "every network call sets a timeout" rule is untouched — and the
feature *reduces* network calls under a flaky link, because refused reads never reach
`remote_resume_signals` at all.

### V. Public Code, Unsupported Project

**Pass.** No credential, hostname, or personal data. The cursor format changes without a
compatibility shim, which Principle V explicitly permits: an old cursor decodes to `None` and
restarts from the newest page, the behaviour `_decode_cursor` already documents. Documentation
is for the author's future self — the README's existing security list gains one bullet and two
amendments, stating the two constants and what freshness they buy.

## Project Structure

### Documentation (this feature)

```text
specs/20260904-143822-guard-cross-origin-gets/
├── plan.md              # This file
├── research.md          # Phase 0: R1–R7
├── data-model.md        # Phase 1: the cache entries, the cursor, the payload fields
├── quickstart.md        # Phase 1: how to see each of the four changes work
├── contracts/
│   └── read-cost.md     # Phase 1: what a read costs, and what is refused
├── checklists/
│   └── requirements.md  # from /speckit-specify
└── tasks.md             # /speckit-tasks output — not created here
```

### Source Code (repository root)

```text
src/robot_army/
├── web/
│   ├── server.py        # the read-side check in handle(); the refusal counter on WebApp;
│   │                    # one capacity snapshot per render; web.stop gains a field
│   └── pages.py         # chrome() and queue_view() take the snapshot; _signals_cell renders
│                        # the local age; log_view renders the truncation notice
└── operations.py        # the local-signal cache and its purge; _scan_file_backwards reads
                         # backwards in blocks; the cursor carries a byte offset; the byte
                         # budget and the truncated flag

tests/
├── unit/
│   ├── test_web_read_cost.py      # NEW: the counting tests (subprocesses, snapshots, bytes)
│   ├── test_web_routing.py        # the read-side refusal, and every path that must still work
│   ├── test_resume_signals.py     # the local cache: TTL, key, failures, age, invalidation
│   └── test_web_log.py            # backwards scan, byte budget, truncation, cursor
└── integration/
    └── test_web_end_to_end.py     # one round-trip proving a cross-site GET is refused on a
                                   # real socket

README.md                          # the "Read this part" security list
```

**Structure Decision**: no new module and no new package. Each change lands in the module that
already owns the behaviour: request admission in `web/server.py`, rendering in `web/pages.py`,
and both the signal computation and the log reader in `operations.py` — which is where FR-047's
"every action calls an `operations.*` function" already puts them. The one new file is a test
module, and it is new because its subject is *cost* rather than any single view: a test that
counts `git` invocations across a render does not belong in the render tests or the signal
tests, and putting it in either would hide it from the other.

## Complexity Tracking

> No Constitution Check violations. The table is left empty deliberately rather than removed, so
> a later reader can see the gate was evaluated and not skipped.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| *(none)* | — | — |

## Post-Design Constitution Re-check

Re-evaluated after `data-model.md` and `contracts/read-cost.md` were written. Nothing in the
design moved a gate:

- **I**: the design added no entity that is not a dict entry or a field on an existing payload.
  The cursor gained a field and lost one. Net line count is expected to be roughly flat.
- **II**: no state that outlives the process, and nothing per-client.
- **III**: the contract document states the one exception in the same words as the plan, so the
  two cannot drift; and it states the two non-silent failures as client-visible contract items
  (`truncated`, `local_signals_age_seconds`) rather than as implementation detail.
- **IV**: the contract's "what a killed request loses" line is "nothing", and that survived the
  design — the byte budget is per request and holds no state between requests.
- **V**: the cursor break is stated in the contract as a break, with the restart behaviour named.
