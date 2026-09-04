# Phase 1 Data Model: identifiers, and the record they name

**Feature**: `specs/20260904-180332-trust-env-session-id` | **Date**: 2026-09-04

There is no schema change here. What changes is where two values come from and what shapes
they are permitted to have. This document pins both, because those two facts are the whole
feature.

---

## Session id

| Property | Value |
|---|---|
| Issued by | `dispatch_item` as `str(uuid.uuid4())` (`src/robot_army/dispatch.py:1092`), then handed to `build_launch_plan` |
| Delivered to the wrapper by | `ROBOT_ARMY_SESSION_ID`, passed as `--env` by the session launcher |
| Accepted shape | `^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$` |
| Used for | The record filename `<session-id>.<event>.json`, and the record's `session_id` field |
| On a value that fails the shape check | Refuse: message on standard error, exit 2, no file, no worker |

**Sources, before and after.** This is the change stated as a table, because the ordering is
the defect:

| | Before | After |
|---|---|---|
| Environment variable | read first, then discarded | the only source |
| `--session-id <value>` in argv | overrode the environment | ignored |
| `--session-id=<value>` in argv | overrode the environment | ignored |
| Last match wins | yes — and the last argument is the prompt | not applicable |
| Validated before use in a path | no | yes |

**Why the shape is the canonical UUID and not a looser character class**: research D2, which
also records the measurement showing the looser form accepts 36 dashes, and the check that
`[[ =~ ]]` anchors do not permit a trailing-newline bypass.

---

## Item id

| Property | Value |
|---|---|
| Issued by | SQLite, as the work item's row id |
| Delivered to the wrapper by | The first positional argument |
| Accepted shape | `^[0-9]+$` |
| Used for | The session log filename `<item-id>.log`, and the record's `item` field |
| On a value that fails the shape check | Refuse: message on standard error, exit 2, no file, no worker |

Nothing untrusted reaches this value today. It is validated because it is the same class of
defect one edit away, and because the check costs one line (research D3).

---

## Exit record

**Unchanged** — same fields, same `schema` value of `1`, same two events. It is listed here
only to make the boundary of the change explicit: no consumer of these records needs
updating, and records written by an older wrapper remain readable.

| Field | Source | Changed? |
|---|---|---|
| `schema` | constant `1` | no |
| `event` | `start` or `exit` | no |
| `item` | item id, JSON-escaped | validated now; format unchanged |
| `session_id` | session id, JSON-escaped | source and validation changed; format unchanged |
| `ts`, `started`, `ended` | `date -u` | no |
| `pid`, `ppid`, `cwd` | the shell | no |
| `argv` | every argument, JSON-escaped | escaping widened (below) |
| `exit`, `signal` | the worker's status, signal decoded from 128+N | no |

### Escaping rule

Applies to every string the record carries — `item`, `session_id`, `cwd`, and each element
of `argv`.

| Input | Emitted as | Status |
|---|---|---|
| `\` | `\\` | existing |
| `"` | `\"` | existing |
| newline (0x0a) | `\n` | existing |
| carriage return (0x0d) | `\r` | existing |
| tab (0x09) | `\t` | existing |
| 0x01-0x08, 0x0b, 0x0c, 0x0e-0x1f | `\u00XX` | **new** |
| 0x00 | — | cannot occur in an argument |
| 0x7f and above | verbatim | unchanged; JSON permits them |

The required property is not merely that the record parses, but that it round trips: what a
strict reader decodes must equal what the wrapper was given (FR-006). Verified for all 31
reachable control characters at once in research D4.

---

## State transitions

None introduced. The work item's states and the daemon's handling of a record are untouched.
The only reachable new outcome is *no record at all*, when the wrapper refuses — and that is
not a new state, it is the existing "session produced no record" case that reconciliation
already covers.
