# Implementation Plan: Parked Is Judged by the Daemon's Ignore List

**Branch**: `robot-army/issue-74-the-web-cards-page-counts-parked-cards` | **Date**: 2026-09-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260913-174456-parked-cards-from-daemon/spec.md`

## Summary

`/cards` already leaves parked cards out of `awaiting clarification`, prefixes their reason and
withholds rescan — when it knows they are parked. It did not, because `card_is_parked` judged the
card against the web process's own configuration, read once at startup, before `Icebox` was ignored.
The daemon is the process that parks cards, so, as issue #30 did for the session cap, the daemon now
publishes its ignore list on the heartbeat (R1) and every other surface judges parkedness against
the published list when it can be believed — daemon holding the lock, heartbeat its own (R2) — and
against its own configuration otherwise. The web hands the request's single reading to the listing
(R3); the terminal takes its own. When the two lists differ, both listings say so in one sentence
(R5).

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none added

**Storage**: no schema change. `heartbeat.json` gains `ignore_lists` (list or `null`).

**Testing**: `pytest` via `uv run pytest`. `test_ignored_lists.py`'s `listing_context`,
`with_ignore_lists`, `run` and `cycle` build the board state; `test_health.py`'s `report_with` builds
heartbeats. Tests that need a daemon's reading pass `published_ignore_lists` directly, so no lock is
taken in a unit test.

**Target Platform**: one Linux machine

**Project Type**: single Python package (CLI daemon plus local web interface)

**Performance Goals**: none; one heartbeat read per `cards` or `show` command, zero extra per web
request.

**Constraints**:
- With daemon and surface in agreement, or no daemon, both listings are byte-for-byte unchanged
  (FR-007).
- One reading of the daemon per web request (FR-005).
- `published_cap`'s behaviour is unchanged; its pid rule is shared, not copied.

**Scale/Scope**:
- Source: `health.py` (`Heartbeat.ignore_lists`, `write_heartbeat`, `published_ignore_lists`, shared
  holder check), `daemon.py` (`_heartbeat`), `operations.py` (`card_is_parked`,
  `resolve_ignore_lists`, `_published_ignore_lists`, `cards`, `_card_for_item`, mismatch sentence),
  `web/server.py` (reading into `params`, `view_cards`), `web/pages.py` (`cards_view` banner)
- Tests: `tests/unit/test_health.py` (H1–H2, B1–B6, daemon H3–H4 where the daemon test lives),
  `tests/unit/test_ignored_lists.py` (R1–R4, L1–L5), a routing test for L6
- Docs: `docs/guide/state.md` (heartbeat field), `docs/guide/2-intake.md` (parked section)

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design. See the second table.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | One heartbeat field, one publisher beside `published_cap` sharing its holder check, one resolver beside `_resolve_cap`'s pattern. No new module, dependency, config key or flag. Rereading the config file and storing a flag were rejected in R1; threading the reading through `show` was rejected in R4 as more than it removes. |
| **II. Single-User, Local-First** | Unchanged: one local file gains a field. |
| **III. Total Accountability** | **What this logs:** nothing new — no action changes state outside a process. The heartbeat is not an audit record and was never logged per write. **Deliberately unlogged:** nothing. |
| **IV. Interruption Tolerance** | **If it is killed halfway:** the heartbeat is still written write-fsync-rename, so no partial file is observable; an older heartbeat without the field is read as *not published* and the surface uses its own configuration. Listings are read-only. No network call is added. |
| **V. Public Code, Unsupported Project** | The heartbeat and `cards --json` gain fields; no outside consumers. Column names are the author's own board's, already in the config and the audit log; no credential. |
| **Operating Constraints** | Observable at the terminal (`robot-army cards`, `jq .ignore_lists heartbeat.json`). Nothing outward-facing is added. |
| **Development Workflow** | Spec → plan → tasks → implement. Unit tests for every changed unit, including the parser's refusal cases (B5, B6) for a file written by another process. |

**Verdict: PASS.** No violation, so Complexity Tracking is omitted.

## Project Structure

### Documentation (this feature)

```text
specs/20260913-174456-parked-cards-from-daemon/
├── plan.md               # this file
├── spec.md
├── research.md           # R1–R6
├── data-model.md         # heartbeat field; published and in-force lists; payload keys
├── quickstart.md
├── contracts/
│   └── parked-in-force.md   # cases H1–H4, B1–B6, R1–R4, L1–L6
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks output
```

### Source Code (repository root)

```text
src/robot_army/
├── health.py          # Heartbeat.ignore_lists; write_heartbeat; published_ignore_lists; _holders_heartbeat
├── daemon.py          # _heartbeat publishes config.trello.ignore_lists
├── operations.py      # card_is_parked(card, ignore_lists); resolve_ignore_lists; _published_ignore_lists;
│                      # cards(published_ignore_lists=...); _card_for_item; the mismatch sentence
└── web/
    ├── server.py      # one reading → params["ignore_lists"]; view_cards passes it
    └── pages.py       # cards_view passes it on and renders the mismatch banner

tests/unit/test_health.py          # H1–H2, B1–B6
tests/unit/test_ignored_lists.py   # R1–R4, L1–L5, daemon heartbeat H3–H4
tests/unit/test_web_routing.py     # L6 (if the routing tests are where handler params are exercised)
docs/guide/state.md                # the heartbeat carries ignore_lists
docs/guide/2-intake.md             # parked is the daemon's decision; the mismatch banner
```

**Structure Decision**: no new module. The published-value reader lives beside `published_cap` in
`health.py` and the resolver in `operations.py` beside `_enforced_cap`, where the issue #30 pattern
already lives.

## Phase 1 re-check (post-design)

| Question | Answer |
|---|---|
| Did the design add a dependency? | No. |
| A module, a table, a state file, a config key? | No new file or key; `heartbeat.json` gains a field, documented in `state.md`. No config key, so `share/config.example.toml` needs no regeneration. |
| An abstraction with one implementation? | No. The shared holder check has two callers (`published_cap`, `published_ignore_lists`). |
| Anything on by default that was not? | No. |
| A new audit action or record shape? | No. `audit-log.md` is unchanged. |
| Which other guide pages? | `2-intake.md` (card handling) and `state.md` (a state file's shape). |

**Verdict: PASS, unchanged.**
