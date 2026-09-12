# Implementation Plan: The Label Is Re-checked Before Dispatch, Not Only at Discovery

**Branch**: `robot-army/issue-62-changing-github-label-does-not-de-scope` | **Date**: 2026-09-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260912-073115-label-rechecked-at-dispatch/spec.md`

## Summary

`poll.evaluate` refuses an issue that lacks `[github] label`, but only while deciding whether a
row should exist. Once a row reaches `ready` nothing asks again, so a label change leaves
everything already queued eligible, and it dispatches as soon as there is capacity.

`ordering` gains a `not_labelled` hold reason. `plan` holds any `ready` item whose stored labels
do not include the configured label, and treats labels it cannot read as missing. The reason
ranks directly below `held` and above every capacity reason, so a full machine does not present
the cap as the thing to raise. It is not a launch hold: `resume` and `restart` only start work
already begun, and `select_and_dispatch`, the only path that starts a `ready` item, walks
`plan` (research R2).

Two supporting changes make the hold liftable and visible:
- **Refresh on re-poll.** When a poll's listing contains an issue whose row is `ready`, the
  row's stored labels are refreshed if they differ, recorded as `poll.labels_refreshed`.
  Labelling an issue under the new label then lifts its hold on the next plan.
- **Startup warning.** Daemon startup writes one `daemon.label_warning` record when any
  `ready` item does not carry the configured label.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none added

**Storage**: no schema change. `work_items.labels` gains a second writer in the poll, beside
`retry`.

**Testing**: `pytest` via `uv run pytest`. `tests/conftest.seed_item` writes
`labels='["robot-army"]'`; tests change the label with `update_work_item_columns` or configure
a different `[github] label`.

**Target Platform**: one Linux machine

**Project Type**: single Python package (CLI daemon plus local web interface)

**Performance Goals**: none. The labels are on rows `plan` already loads; one JSON decode per
queued item.

**Constraints**:
- `plan` stays pure. No I/O beyond the database, because the web interface calls it on every
  render.
- The steady-state poll writes nothing new (spec US2 AS2).

**Scale/Scope**:
- Source: `ordering.py`, `poll.py`, `daemon.py`
- Tests: unit tests for ordering, poll and the daemon; an integration test for dispatch
- Docs: `2-intake.md`, `3-selection.md`, `audit-log.md`

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design — see the second table.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | One enum value, one pure function in `ordering`, one branch in the poll's existing-row path, one startup helper beside `warn_about_environment`. No module, dependency, table or config key. The label comparison stays Python's `in`, exactly as `poll.evaluate` writes it. A shared helper for a membership test would be an abstraction over an operator (R1). |
| **II. Single-User, Local-First** | Unchanged. |
| **III. Total Accountability** | **What this logs:** a label refresh is a write to persistent state and gets `poll.labels_refreshed` with the old and new labels. The startup mismatch gets `daemon.label_warning`. A pass stalled by the new hold is recorded by the existing `dispatch.at_capacity` / `dispatch.hold_ended` pair, whose signature already includes the reason, so the change from `global_cap` to `not_labelled` is news and is recorded (R5). **Deliberately unlogged:** computing the hold. It is a pure read that changes nothing outside the process, like every other hold reason; the log records its *effect* on a pass, as above. |
| **IV. Interruption Tolerance** | **If it is killed halfway:** the hold is computed on read and never stored, so there is nothing to be halfway through. The refresh is one `UPDATE` in a transaction, and its audit record is written inside that transaction before commit, the shape `poll.discovered` already has. Killed between the two, the log says a refresh happened that the database does not hold. The next poll sees the same difference and refreshes again, so it converges and nothing is lost. The startup warning is a read and a log line. No network call is added. |
| **V. Public Code, Unsupported Project** | Two new audit actions and one new hold value; no outside consumers. |
| **Operating Constraints** | The hold shows on `status` and the web queue through `plan`; the warning is in the log `robot-army log` reads. Nothing outward-facing is added. A new behaviour *withholds* dispatch, it never starts one. |
| **Development Workflow** | Spec → plan → tasks → implement. Unit tests cover the hold, its precedence against every other reason, malformed labels (the failure path of a stored-data parser), the refresh (differs / identical / non-`ready` row), and the startup warning (some / none). An integration test shows `select_and_dispatch` skipping a de-scoped item and dispatching the one behind it. |

**Verdict: PASS.** No violation, so Complexity Tracking is omitted.

## Project Structure

### Documentation (this feature)

```text
specs/20260912-073115-label-rechecked-at-dispatch/
├── plan.md               # this file
├── spec.md
├── research.md           # R1–R6
├── data-model.md         # the stored labels' writers; the new hold reason
├── quickstart.md
├── contracts/
│   └── output.md         # hold detail, the two audit records
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks output
```

### Source Code (repository root)

```text
src/robot_army/
├── ordering.py   # HoldReason.NOT_LABELLED; label_hold(); _hold_for splices it in after
│                 # paused/held and before the capacity reasons
├── poll.py       # the existing-row branch refreshes a ready row's labels when they differ
└── daemon.py     # warn_about_label(), called from Daemon.startup beside the other warnings

tests/unit/
├── test_label_hold.py          # the reason, its detail, malformed labels, precedence
├── test_poll.py                # the refresh: differs, identical, not ready
tests/integration/
├── test_daemon_loop.py         # the startup warning, some and none
└── test_dispatch_capacity.py   # a de-scoped item is skipped; the one behind it dispatches

docs/guide/
├── 2-intake.md       # changing the label: queued items are held, and how to release them
├── 3-selection.md    # the reasons table and a short section on the new hold
└── audit-log.md      # poll.labels_refreshed, daemon.label_warning
```

**Structure Decision**: no new module. The hold lives with the other hold reasons in
`ordering`, and the refresh goes in the poll branch that already handles re-seeing a known
issue.

## Phase 1 re-check (post-design)

| Question | Answer |
|---|---|
| Did the design add a dependency? | No. |
| A module, a table, a state file, a config key? | None. No config key, so `share/config.example.toml` needs no regeneration. |
| An abstraction with one implementation? | No. `label_hold` is a function with one caller, named so its precedence is one readable line in `_hold_for`, as every other reason there is. |
| Anything on by default that was not? | No. The change only withholds dispatch. |
| Which documentation? | `2-intake.md` (the label gate), `3-selection.md` (a hold reason), `audit-log.md` (two actions), per `CLAUDE.md`'s table. |

**Verdict: PASS, unchanged.**
