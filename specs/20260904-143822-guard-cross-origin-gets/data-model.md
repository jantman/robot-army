# Phase 1 data model

**Feature**: `specs/20260904-143822-guard-cross-origin-gets`

Nothing here is persisted. Every entity below is either an in-memory dict entry that dies with
the process, a field added to a payload that already exists, or a change to an opaque token the
client hands back. There is no schema change and no migration.

---

## 1. Local signal cache entry

**Where**: `operations._LOCAL_SIGNAL_CACHE`, a module-level dict guarded by
`_LOCAL_SIGNAL_LOCK`, beside the existing `_REMOTE_SIGNAL_CACHE`.

| Part | Shape | Notes |
|---|---|---|
| key | `(item_id: int, repo_key: str, worktree_path: str, branch: str, base_ref: str)` | Every input `worktree.condition` is given. A change to any of them is a different question (FR-007). |
| value | `(computed_at: float, signals: dict[str, Any])` | `computed_at` is `_monotonic()`, the same seam the remote cache uses so a test can move the clock without sleeping. |

**Cached value contents**: `worktree_present`, `uncommitted_changes`, `commits_on_branch` — the
three keys `local_resume_signals` returns today, and nothing else.

**Never cached**: an observation that raised `BoundaryError`. Its `worktree_error` is returned
to the caller and no entry is written, so the next render retries (FR-008). This mirrors the
`TransportError` rule the remote half already applies.

**Lifetime**: `LOCAL_SIGNAL_TTL_SECONDS = 5.0`. An entry older than that is ignored on read and
purged on the next insert.

**Bound** (FR-012): every insert first purges entries whose age exceeds the TTL, so the dict
holds at most the distinct items observed within one five-second window. The purge is shared
with `_REMOTE_SIGNAL_CACHE` through one helper, since the two differ only in their TTL.

**Invalidation** (FR-010): `forget_resume_signals(item_id)` drops every entry — local and
remote — whose key names that item. Called from `_perform` after a successful action on a
`work_item`. `clear_resume_signal_cache()` keeps its existing meaning and now clears both dicts.

---

## 2. Local signal payload fields

`local_resume_signals` returns one added key, which `resume_signals` therefore also returns:

| Field | Type | Meaning |
|---|---|---|
| `local_signals_age_seconds` | `int` | `0` when observed just now; otherwise the whole seconds since. Always present, so a consumer never has to distinguish "fresh" from "the field is missing". |

This is deliberately a *second* age field rather than a merge with the existing
`signals_age_seconds` (which belongs to the GitHub-derived pair). The two halves have different
TTLs and different failure modes; one number would have to misreport one of them.

`_signal_row` in `web/pages.py` passes it through under the same name, and `_signals_cell`
renders it as its own footnote beside the existing GitHub one:

- age `0` → *"checkout signals read just now"*
- age `n` → *"checkout signals `n`s old (cached)"*

---

## 3. Log page cursor

**Where**: the opaque `cursor` string on `/log` and on `operations.read_log_page`, produced by
`_encode_cursor` and read by `_decode_cursor`.

| | Before | After |
|---|---|---|
| payload | `{"f": <file name>, "n": <matches consumed from it>}` | `{"f": <file name>, "b": <byte offset>}` |
| meaning of the number | how many matching records earlier pages already took from this file | the file position the next page scans *backwards* from; the next page covers `[0, b)` |
| `0` means | nothing consumed — start at the end of this file | this file is finished — start at the end of the file before it |
| resuming cost | re-reads and re-judges every earlier page's records | resumes at the offset |
| stable under append | no | yes, for every file including today's |

**Encoding is unchanged**: JSON, UTF-8, URL-safe base64, unpadded.

**Compatibility**: a cursor in the old shape has no `"b"` key, so `_decode_cursor` returns
`None` and the request restarts from the newest page — the behaviour its docstring already
documents for a cursor naming a page that no longer exists. This is a deliberate break under
Principle V; there is no shim.

---

## 4. Log page payload fields

`read_log_page` returns two added keys beside the existing `records`, `filters`,
`skipped_lines`, `unparseable_lines`, `has_more`, `next_cursor` and `page_size`:

| Field | Type | Meaning |
|---|---|---|
| `truncated` | `bool` | The scan stopped at the byte budget before the page was full. `has_more` is `True` and `next_cursor` names where to continue. |
| `bytes_scanned` | `int` | Total bytes read from audit files while producing this page. Present on every page, so the budget is observable rather than inferred. |

`log_view` renders a line when `truncated` is true, above the "older records →" link:

> *The scan stopped after 8 MB without filling this page. There may be older matching records —
> follow "older records" to keep looking.*

An untruncated page is unchanged in every respect.

---

## 5. Refused-read counter

| Part | Shape | Notes |
|---|---|---|
| `WebApp.refused_cross_site` | `int`, guarded by a `threading.Lock` | Incremented in `handle()` when a non-mutating request is refused by the origin check. Per process, lost on restart. |

Folded into the existing `web.stop` audit record's `detail`, beside `refused_over_capacity`:

```json
{"reason": "signal", "refused_over_capacity": 0, "refused_cross_site": 41}
```

It lives on `WebApp` rather than on the server class because `handle()` receives the `WebApp`
and never the server, and because a test drives `handle()` directly with no socket at all
(R15) — a counter on the server would be untestable from where the increment happens.

---

## 6. Capacity snapshot, threaded through a render

Not a new entity — the existing `capacity.CapacitySnapshot`, passed rather than recomputed.

| Where | Before | After |
|---|---|---|
| `handle()` | — | computes it once, beside `level` and `include_simulated` |
| `pages.chrome(...)` | computes its own | takes `capacity: CapacitySnapshot \| None = None`; computes only when not given |
| route handler `params` | — | carries `"capacity"` |
| `pages.queue_view(...)` | computes its own | takes `capacity: CapacitySnapshot \| None = None`; computes only when not given |

The `None` defaults exist so that a direct caller — a test, or `queue_view` invoked outside a
request — still works. In a served request the value is always supplied, which is what makes
"one observation per render" true rather than merely likely.

---

## 7. Constants

| Name | Module | Value | Why that value |
|---|---|---|---|
| `LOCAL_SIGNAL_TTL_SECONDS` | `operations` | `5.0` | Below the default `[web] refresh_seconds = 10`, so an open page still observes the worktree afresh on every refresh; above the burst window, so a flood collapses to one observation per item per five seconds (R6). |
| `LOG_SCAN_BLOCK_BYTES` | `operations` | `65536` | Larger than any single audit record, so the "record spans a block boundary" path is exercised by a test rather than by production; small enough that a page filling from the newest records reads one block (R4). |
| `LOG_SCAN_BUDGET_BYTES` | `operations` | `8 * 1024 * 1024` | Spans the newest two to three daily files, so every unfiltered page and every recent-item filter fills long before it; reached only by a filter matching nothing far back in history (R4). |

All three are module constants with the reasoning at the definition, consistent with
`REMOTE_SIGNAL_TTL_SECONDS` and with the connection bounds in `web/server.py`. None becomes
configuration: there is one caller, one meaning, and no second use in hand (Principle I).
