# Contract: Time Display

**Feature**: `010-local-timezone-display` | **Date**: 2026-08-29

Two contracts. The first is the function every display site calls. The second is the
enumeration of those sites — which exists because FR-005 ("no surface may show UTC while
another shows local") is only checkable if "every surface" is a list rather than an intention,
and because no existing test would catch a site that was missed (research [R7](../research.md)).

---

## 1. `robot_army.timefmt`

### `parse_stamp(stamp: str | None) -> datetime | None`

Parses a stored instant. Returns a timezone-aware `datetime` in UTC, or `None` if `stamp` is
`None`, empty, or not in `%Y-%m-%dT%H:%M:%SZ`. Never raises.

Absorbs the private `web.pages._parse`, which is deleted. `pages.age_seconds` imports this one.

### `local(stamp: str | None) -> str | None`

The only producer of a displayed timestamp in the system.

| Input | Output |
|---|---|
| `"2026-08-30T01:31:07Z"`, host in `America/New_York` | `"2026-08-29 21:31:07 -04:00"` |
| `"2026-01-15T01:31:07Z"`, host in `America/New_York` | `"2026-01-14 20:31:07 -05:00"` |
| `"2026-08-30T01:31:07Z"`, host in `Asia/Kolkata` | `"2026-08-30 07:01:07 +05:30"` |
| `"2026-08-30T01:31:07Z"`, host zone unresolvable | `"2026-08-30 01:31:07 +00:00"` |
| `None` | `None` |
| `""` | `""` |
| `"not a timestamp"` | `"not a timestamp"` |
| `"2026-08-30T01:31:07"` (no `Z`) | `"2026-08-30T01:31:07"` |

**Guarantees**

1. **Never raises**, for any input of any type that reaches it as a string or `None`.
2. **Never returns `None` for a non-`None` input**, so a caller may interpolate the result
   directly and a corrupt value stays visible.
3. **Resolves the offset for the instant being converted**, not for the current moment — a
   January stamp displayed in August carries January's offset.
4. **Is pure with respect to its arguments and the process environment.** It reads the host
   zone and nothing else; it holds no cache, so a test that changes `TZ` and calls
   `time.tzset()` sees the change immediately.

**Format**: `%Y-%m-%d %H:%M:%S %:z`. The space in place of the stored format's `T` is
deliberate — see research [R8](../research.md).

---

## 2. The display sites

Every one of these renders local after this change. **This table is the definition of "every
surface" for FR-005 and SC-001**, and each row is expected to have a test.

### Terminal — `src/robot_army/operations.py`

All ten build `Result.lines`. None touches `Result.data`.

| # | Line | Command | Current text |
|---|------|---------|--------------|
| C1 | 296 | `status` | `f"PAUSED since {control_state.paused_at} (by {control_state.paused_by})"` |
| C2 | 364 | `status` | `f"…:{anomaly.entity_id or ''} @ {anomaly.detected_at}"` |
| C3 | 549 | `show` (Spec Kit line) | `f" (since {item.speckit_phase_at})"` |
| C4 | 639 | `show` | `f"  cleaned at : {item.cleaned_at}"` |
| C5 | 646 | `show` (history, 6 rows) | `f"  {when}  {what}"` over `_history(item)` |
| C6 | 659 | `show` (sessions) | `f"       started {session.started_at} ended {session.ended_at or '—'}"` |
| C7 | 1649 | `pause` / `resume` (no-op) | `f" — paused at {after.paused_at} by {after.paused_by}"` |
| C8 | 1656 | `pause` | `f"dispatch paused at {after.paused_at} by {by}"` |
| C9 | 2200 | `anomalies` | `f"{anomaly.entity_id or '—'}  detected {anomaly.detected_at}"` |
| C10 | 2473 | `log`, `log --follow` | `f"{record.get('ts')} {marker} …"` in `_format_record` |

**C5 note**: `_history()` returns `(stamp, label)` pairs and is *also* the source of
`result.data["history"]` (line 614). The conversion goes in the loop at 646, not in
`_history()`, or the JSON payload takes local times with it.

**C10 note**: `_format_record` is shared by `read_log` and `follow_log`, so one edit covers
both the paged and the streaming reader. `read_log`'s `result.data` holds the raw records and
is not touched.

### Web — `src/robot_army/web/pages.py`

| # | Line | Site | Notes |
|---|------|------|-------|
| W1 | 106 | `when()` | One funnel for **seven** call sites: 690 (active, `started_at`), 831 (queue, `updated_at`), 1014 (`ended_at`), 1126 (anomalies, `detected_at`), 1471 (item history), 1486–1487 (sessions). One edit covers all seven. |
| W2 | 1738 | the log view's record `ts` | `span(record.get("ts", "—"), class_="ts mono")` — not routed through `when()` because a log record carries no relative age. |

**One deliberate exception, found while writing the tests**: an audit record's `detail`
payload is rendered by `_record_detail` (`pages.py:1618`) and by `_format_record`'s
`json.dumps` tail in the terminal, and it can contain timestamps — a `dispatch.pause`
record carries `paused_at`. These stay UTC. `detail` is free-form JSON written by whatever
raised the record, so converting inside it would need a heuristic to decide what is a
timestamp, would corrupt a field that merely resembled one, and would make the page
disagree with the file it is quoting. It is the record shown as the record, not a display
of a time, and it is already unambiguous because it carries `Z`. Both interfaces quote it
verbatim, so FR-005's "no surface may disagree with another" still holds.

`when()` keeps its shape: absolute beside relative. It becomes
`2026-08-29 21:31:07 -04:00 (3h 12m ago)`. `human_age` and `age_seconds` are unchanged (FR-006)
and continue to compute from the stored UTC value.

### Web — `src/robot_army/web/html.py`

| # | Line | Site | Current text |
|---|------|------|--------------|
| W3 | 302 | the paused pill in `_chrome_bar` | `since = chrome.get("dispatch_paused_at") or "unknown time"` |
| W4 | 440 | the page footer | `span(f"rendered {rendered_at}", class_="rendered")` |

**These two are the R3 case.** Both read from the chrome dict, which `server._render` merges
into the JSON body. The dict keeps UTC; the conversion happens in these two lines.

---

## 3. What must NOT change

Each of these is a test as much as a rule.

| Surface | Rule | Requirement |
|---|---|---|
| Every `*_at` column, heartbeat `ts`, audit `ts` | UTC, `%Y-%m-%dT%H:%M:%SZ` | FR-010, FR-011 |
| `audit-YYYY-MM-DD.jsonl` file names | the UTC day | FR-011 |
| `Result.data` for every command | UTC | FR-012 |
| `{**view.data, **chrome}` JSON body, including `rendered_at` and `dispatch_paused_at` | UTC | FR-012, R3 |
| `pages.age_seconds`, `human_age`, `operations._age_seconds`, `health.check` | read the stored value | FR-006, FR-013 |
| `ORDER BY` in every query; `read_log` ordering | the stored column | FR-013 |
| `poll` backoff, `capacity`, `ordering.plan` | untouched | FR-013 |
| `--since 30s/10m/2h/1d` | same grammar, same meaning | FR-014 |
| `app.js` | untouched — it computes age from `Date.now()` at load and never parses a rendered stamp | FR-006 |
| Exit codes | unchanged | Operating Constraints |

---

## 4. Documentation

`docs/logging.md` line 59 defines `ts` as "UTC ISO 8601, `Z` suffix. Always." That stays true
and gains the other half of the rule beside it: the record is UTC, what the CLI and the web
interface print is the host's local time, and the two are different on purpose (FR-018).
