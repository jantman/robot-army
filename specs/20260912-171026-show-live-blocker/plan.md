# Implementation Plan: `show` Reports What Blocks an Item Now, Not What Blocked It Then

**Branch**: `robot-army/issue-63-show-reports-a-stale-blocked-reason` | **Date**: 2026-09-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260912-171026-show-live-blocker/spec.md`

## Summary

`show` prints `blocked_reason` as it was stored at the moment of failure. Once the condition is
fixed, it goes on sending the maintainer to fix it again, while `retry`, checking live, names
what actually blocks the item now.

The fix is one function, `operations._local_blocker`. It performs `retry`'s pre-read checks,
`repos.resolve` then `dispatch.check_gates`, and both `retry` and `show` call it (R2). For a
`failed` item, `show` reports that verdict as the current blocker, marked checked now, and says
when it differs from the reason recorded at the failure. The web item page renders the same
verdict from the same payload. `show` passes a new `check_gates(raise_anomalies=False)`, so
rendering never writes an anomaly (R3). `retry` keeps the default and is otherwise unchanged.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none added

**Storage**: no schema change; `failure_reason` and `blocked_reason` keep their writers and are
now presented as history.

**Testing**: `pytest` via `uv run pytest`. The retry tests' `ctx` fixture (stubbed `is_trusted`,
`failed_item` on the demo repository) is the template for the new `show` tests. The `web`
fixture covers the item page.

**Target Platform**: one Linux machine

**Project Type**: single Python package (CLI daemon plus local web interface)

**Performance Goals**: none. The check reads the database, the filesystem and a few local
`git` commands for one item, and only for `failed` items (R7).

**Constraints**:
- `show` changes no state: no anomaly, no column write, and no record of its own (FR-007, R8).
- No network request.
- `retry`'s behaviour is byte-for-byte unchanged (FR-008). Its existing tests are the proof.

**Scale/Scope**:
- Source: `dispatch.py` (`raise_anomalies`), `operations.py` (`LocalBlocker`, `_local_blocker`,
  `retry`, `show`), `web/pages.py` (the item page's `blocked` entry)
- Tests: a new `tests/unit/test_show_blocker.py`; a web item-page test; one `check_gates` test
  for `raise_anomalies=False`
- Docs: `docs/guide/operating.md` (reading `show` and the item page when an item is stuck)

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design — see the second table.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | One function and one two-field dataclass, both with two callers, plus one keyword argument whose default is today's behaviour. No module, dependency, table or config key. The alternative, a second read-only copy of the location checks, is the two-copies failure that caused the bug (R2, R3). |
| **II. Single-User, Local-First** | Unchanged, and no network request is added. |
| **III. Total Accountability** | **What this logs:** nothing new. `show` is an inspection and changes no state. The `git` commands the check runs are recorded by the boundary as `git.subprocess`, as every `git` command is and as `show`'s resume signals already are (R8). **Deliberately unlogged:** the anomaly `check_gates` would raise for a moved or replaced clone is not raised when `show` asks. The condition is still reported by the next dispatch or `retry` that meets it, which is who reports it today; `show` only reads. There are no silent fallbacks: a check that cannot complete is reported as such and never replaced by the stored sentence (R4, FR-006). |
| **IV. Interruption Tolerance** | **If it is killed halfway:** nothing is written, so there is nothing to be halfway through. No network call is added. The `git` calls use the boundary's existing `QUICK_TIMEOUT`. |
| **V. Public Code, Unsupported Project** | `show --json` gains a key; there are no outside consumers. |
| **Operating Constraints** | Reachable from the terminal (`show`) with the web page mirroring it. Nothing outward-facing is added. |
| **Development Workflow** | Spec → plan → tasks → implement. Unit tests cover every row of the output contract: blocked-same, blocked-differs, clear, could-not-check (the failure path), unresolved repository, non-failed with a stored reason. They also cover no anomaly written by `show` for a moved clone, anomaly still written by `retry` for the same clone, the JSON key, and the web page. The existing `retry` suite runs unmodified. |

**Verdict: PASS.** No violation, so Complexity Tracking is omitted.

## Project Structure

### Documentation (this feature)

```text
specs/20260912-171026-show-live-blocker/
├── plan.md               # this file
├── spec.md
├── research.md           # R1–R8
├── data-model.md         # stored vs computed; LocalBlocker; current_blocker
├── quickstart.md
├── contracts/
│   └── show-output.md    # every case of the blocker line and the payload key
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks output
```

### Source Code (repository root)

```text
src/robot_army/
├── dispatch.py     # check_gates / _check_recorded_location / _raise_location_anomaly
│                   # take raise_anomalies (default True)
├── operations.py   # LocalBlocker, _local_blocker(); retry() calls it; show() renders the
│                   # current blocker and adds result.data["current_blocker"]
└── web/pages.py    # the item page's "blocked" entry renders current_blocker

tests/unit/
├── test_show_blocker.py        # new: every contract case, no anomaly, retry agreement
├── test_web_views.py           # the item page's blocked entry and JSON key
└── test_operations_retry.py    # unchanged, and must stay green

docs/guide/
└── operating.md    # show's blocked line is checked now; failure is history
```

**Structure Decision**: no new module. The shared check sits beside `retry` in `operations`,
where both of its callers are.

## Phase 1 re-check (post-design)

| Question | Answer |
|---|---|
| Did the design add a dependency? | No. |
| A module, a table, a state file, a config key? | None. No config key, so `share/config.example.toml` needs no regeneration. |
| An abstraction with one implementation? | No. `_local_blocker` has two callers, which is the reason it exists. `LocalBlocker` is a return value, not an interface. |
| Anything on by default that was not? | No. |
| A new audit action or record shape? | No, so `audit-log.md` is untouched. |
| Which documentation? | `operating.md`, which covers recovery and the web interface, per `CLAUDE.md`'s table. |

**Verdict: PASS, unchanged.**
