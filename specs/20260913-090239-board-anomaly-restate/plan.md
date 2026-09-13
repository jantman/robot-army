# Implementation Plan: A Board Anomaly Always Names the Board's Current Failure

**Branch**: `robot-army/issue-73-board-precondition-anomalies-dedupe` | **Date**: 2026-09-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260913-090239-board-anomaly-restate/spec.md`

## Summary

`board_disabled_anomaly` raises its anomaly with `raise_anomaly`, an `INSERT OR IGNORE` against
the open-row unique index `(kind, entity, dry_run)`. A board that fails for a new reason while its
earlier anomaly is unacknowledged therefore changes nothing, and the list goes on naming the old
reason (issue #73).

The fix is confined to that one call site. It looks up the board's open row. With none, it raises
as before. With one whose detail equals the new detail, it does nothing. With one that differs, it
rewrites the row's detail and detection time in place and records `anomaly.restated` carrying the
previous failed checks (R1–R5). Every other anomaly kind keeps `INSERT OR IGNORE`, because for them
the first detail is evidence worth keeping (R4).

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none added

**Storage**: SQLite `anomalies` table, no schema change. One new audit action.

**Testing**: `pytest` via `uv run pytest`. `tests/unit/test_board_preconditions.py` already drives
`board_disabled_anomaly` with `conn` and `audit` fixtures; the new database functions get their
own unit tests beside the existing anomaly tests.

**Target Platform**: one Linux machine

**Project Type**: single Python package (CLI daemon plus local web interface)

**Performance Goals**: none. One indexed lookup per failed board check, which happens at startup.

**Constraints**:
- An unchanged failure writes nothing, neither row nor record (FR-004, SC-002).
- An acknowledged or resolved row is never rewritten (FR-006), enforced in the `UPDATE`'s own
  `WHERE`, not only by the lookup.
- `trello.board.check` is unchanged (FR-007).

**Scale/Scope**:
- Source: `db.py` (`open_anomaly`, `restate_anomaly`), `intake.py` (`board_disabled_anomaly`)
- Tests: `tests/unit/test_board_preconditions.py` (contract C1–C7, the issue's sequence);
  database-level tests for the two functions, including the lost race with acknowledgement and an
  interrupted transaction
- Docs: `docs/guide/operating.md` (the board anomaly is restated), `docs/guide/audit-log.md`
  (`anomaly.restated`), `docs/guide/state.md` (what `detected_at` means for a restated row)

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design. See the second table.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | Two small database functions with one caller between them and one changed function. No column, migration, dependency, config key or flag on `raise_anomaly`. The alternatives, keying the unique index on failed check names or adding a `restated_at` column, each need a migration and were rejected (R1, R3). |
| **II. Single-User, Local-First** | Unchanged. |
| **III. Total Accountability** | **What this logs:** every restatement writes `anomaly.restated` with the anomaly id, the board, the previous and new failed checks and the previous detection time, because the update overwrites the database's only copy of the old reason (R5). **Deliberately unlogged:** a failure identical to the open anomaly's writes no `anomaly.*` record, because it changes no state. It is still recorded by the unchanged `trello.board.check` error record, so nothing new is unlogged. |
| **IV. Interruption Tolerance** | **If it is killed halfway:** lookup, update and record share one `BEGIN IMMEDIATE` transaction. Killed before commit, the row keeps its old text and the next start restates it. Killed between the record's append and the commit, the log has one restatement the database lacks, and the next start writes it again: a duplicate line, never a missing one (R6). No network call is added. |
| **V. Public Code, Unsupported Project** | No outside consumers. The record carries board check names and details, which name labels and columns on the author's board, the same text `trello.board.check` already logs. No credential. |
| **Operating Constraints** | Visible through `robot-army anomalies` and `robot-army log`. Nothing outward-facing is added. |
| **Development Workflow** | Spec → plan → tasks → implement. Unit tests cover every contract case, the issue's sequence, repeated identical failures, a lost race with acknowledgement, and a rolled-back transaction. |

**Verdict: PASS.** No violation, so Complexity Tracking is omitted.

## Project Structure

### Documentation (this feature)

```text
specs/20260913-090239-board-anomaly-restate/
├── plan.md               # this file
├── spec.md
├── research.md           # R1–R7
├── data-model.md         # the row's transitions; the anomaly.restated record
├── quickstart.md
├── contracts/
│   └── board-anomaly-restatement.md   # cases C1–C7
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks output
```

### Source Code (repository root)

```text
src/robot_army/
├── db.py        # open_anomaly(); restate_anomaly()
└── intake.py    # board_disabled_anomaly: raise, restate, or leave alone

tests/unit/test_board_preconditions.py   # contract cases; the issue's sequence
tests/unit/test_anomaly_resolution.py    # the two functions, the race, the rollback
docs/guide/operating.md                  # the board anomaly is restated, not duplicated
docs/guide/audit-log.md                  # anomaly.restated
docs/guide/state.md                      # detected_at on a restated row
```

**Structure Decision**: no new module. The decision to restate lives in `intake`, which owns what a
board anomaly says. The database functions only read the open row and rewrite it under the open
guard.

## Phase 1 re-check (post-design)

| Question | Answer |
|---|---|
| Did the design add a dependency? | No. |
| A module, a table, a state file, a config key? | None. No config key, so `share/config.example.toml` needs no regeneration. |
| An abstraction with one implementation? | `open_anomaly` and `restate_anomaly` each have one caller today. They are plain queries named after what they do, beside `resolve_anomaly` and `acknowledge_anomaly`, not an abstraction; inlining SQL into `intake` would be the first raw SQL outside `db.py`. |
| Anything on by default that was not? | No. |
| A new audit action or record shape? | Yes, `anomaly.restated`. `audit-log.md` documents it, per `CLAUDE.md`'s table. |
| Which other guide pages? | `operating.md` (how the anomaly list behaves) and `state.md` (a row's `detected_at` can now move). |

**Verdict: PASS, unchanged.**
