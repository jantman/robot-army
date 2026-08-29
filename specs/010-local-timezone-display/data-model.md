# Data Model: Times Are Read in the Local Timezone

**Feature**: `010-local-timezone-display` | **Date**: 2026-08-29

**No persisted data changes.** No table, column, index, or migration is added, altered, or
removed. `SCHEMA_VERSION` does not move. The audit record shape and the audit file naming are
untouched. This document exists to state the three concepts the feature introduces and, more
importantly, the boundary between them — because the whole risk in this change is a value
crossing that boundary in the wrong direction.

---

## Entities

### Stored instant

**What it is**: a moment in time as the system records it.

| Property | Value |
|---|---|
| Representation | `str`, formatted `%Y-%m-%dT%H:%M:%SZ` |
| Zone | UTC, always |
| Resolution | whole seconds |
| Produced by | `states.utcnow()`, `cardstates.utcnow()`, `audit`, `health`, `poll`, `reconcile` |
| Lives in | every `*_at` column, the heartbeat `ts`, every audit record `ts` |
| Changed by this feature | **no** |

Every decision the system makes reads this and only this: ordering (`ORDER BY` on the stored
column), ages (`_age_seconds`, `pages.age_seconds`), staleness (`health.check`), backoff
windows (`poll`), and capacity. FR-013 is the rule that none of them may ever read a
*Displayed timestamp* instead.

### Displayed timestamp

**What it is**: the text a person reads for a stored instant. It exists only in output and is
never stored, compared, sorted, parsed back, or transmitted to a program.

| Property | Value |
|---|---|
| Representation | `str`, formatted `%Y-%m-%d %H:%M:%S %:z` |
| Example | `2026-08-29 21:31:07 -04:00` |
| Width | 26 characters (see research [R8](research.md) for the one theoretical exception) |
| Zone | the host's, resolved for the instant being displayed |
| Produced by | `timefmt.local()` — the only producer |
| Lives in | `Result.lines`, and the markup built by `web/pages.py` and `web/html.py` |
| Never lives in | `Result.data`, `View.data`, the chrome dict, any file, any database column |

### Host timezone

**What it is**: the zone the operating system reports for the running process.

| Property | Value |
|---|---|
| Source | `TZ` if set, otherwise `/etc/localtime` |
| Read by | `datetime.astimezone()` with no argument |
| Configured by this project | **never** (FR-008) |
| Unresolvable | falls back to UTC and renders `+00:00`; no exception (FR-009, verified in [R2](research.md)) |
| Resolution timing | per instant, not per process — so DST is correct for historical stamps |

---

## The boundary

The feature is one rule, and every requirement in the spec is a consequence of it:

```text
  database / audit files / heartbeat
              │
              │  stored instants — UTC, unchanged
              ▼
      operations.py, web/server.py, web/pages.py
              │
      ┌───────┴────────┐
      │                │
      ▼                ▼
  Result.data      Result.lines
  View.data        pages.* markup
  chrome dict      html.py markup
      │                │
      │                │  ← timefmt.local() is called HERE and only here
      ▼                ▼
   --json           a person
   HTTP JSON        a terminal
   (UTC)            (local)
```

**The one crossing that is easy to get wrong** is the chrome dict. It is built in
`pages.chrome_for()` as data, and `server._render` merges it into the JSON body
(`{**view.data, **chrome}`), *and* `html.py` prints two of its keys for a person. It therefore
sits on both sides of the line. It stays UTC, and `html.py` converts at the moment of
rendering. See research [R3](research.md).

---

## Validation rules

These are properties of `timefmt.local()`, and each maps to a requirement:

| Input | Output | Requirement |
|---|---|---|
| A valid stored stamp | the same instant, local, with offset | FR-001, FR-002, FR-003 |
| `None` | `None` — the caller supplies its own absent-value marker | FR-016 |
| `""` | `""` | FR-016 |
| A string that is not a stamp | the input, verbatim | FR-015 |
| Any input, host zone unresolvable | rendered at `+00:00` | FR-009 |
| Any input | never raises | FR-015 |

`timefmt.local()` returning its input verbatim rather than a placeholder is deliberate: a
corrupt row must remain visible to the reader as the corrupt value it is, which is Principle
III's prohibition on silent failure applied to the rendering layer.

---

## State transitions

None. This feature introduces no state and participates in no state machine.
