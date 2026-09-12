# Implementation Plan: Capacity breakdown that sums to its total

**Branch**: `robot-army/issue-61-capacity-breakdown-does-not-sum-to-its` | **Date**: 2026-09-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260911-160717-capacity-breakdown-sum/spec.md`

## Summary

`capacity.snapshot` computes `total = registry entries + session rows with no registry entry`,
but reports only the registry half as `ours` and `others`. The rows with no registry entry are
counted and never shown.

The snapshot gains two integers that partition those rows: `simulated` (the simulated host's
signature, `Session.hosted_by_simulation`) and `in_flight` (every other open row the registry
does not know). By construction `total == len(ours) + others + simulated + in_flight`, and every
surface that renders the breakdown renders the new terms: the `capacity` block always, the
one-line forms (`describe()`, the web pill, a global-cap hold detail) when non-zero. The JSON
from `capacity` and `status`, and the `dispatch.at_capacity` audit record, carry both counts.
Nothing that decides dispatch changes.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none added

**Storage**: none changed — the snapshot is never stored

**Testing**: `pytest` via `uv run pytest`; `tests/unit/test_capacity.py` builds registry and
`/proc` fixtures under `tmp_path` and seeds rows with `seed_session(..., pid=0, dry_run=True)`
for the simulated signature

**Target Platform**: one Linux machine

**Project Type**: single Python package (CLI daemon plus local web interface)

**Performance Goals**: none — the rows are already loaded; partitioning them is a list pass

**Constraints**: the count must not change (FR-007); the snapshot must still carry no handle to
an out-of-band session (FR-008)

**Scale/Scope**: `capacity.py`, `operations.py`, `ordering.py`, `dispatch.py`, `web/html.py`;
tests; `3-selection.md` and `audit-log.md`

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design — see the second table.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | Two `int` fields on an existing dataclass and one property (`components`) returning the non-zero terms in order, read by the three one-line renderers so they cannot word or order them differently. No module, dependency, config key, or table. |
| **II. Single-User, Local-First** | Unchanged. |
| **III. Total Accountability** | **What this logs:** nothing new is *done*, so nothing new is logged; the existing `dispatch.at_capacity` record gains `simulated` and `in_flight` so a hold read back from the log sums to its `live_sessions` as the screen does. Rendering capacity changes nothing outside the process and remains unlogged, as today. **Nothing is deliberately unlogged** that was logged before. |
| **IV. Interruption Tolerance** | **If it is killed halfway:** there is nothing to be halfway through — the snapshot is a pure read, never stored. No write, no network call. |
| **V. Public Code, Unsupported Project** | JSON and the audit record gain keys; existing keys keep their meaning. No outside consumers either way. |
| **Operating Constraints** | Every surface touched is already reachable from the terminal; the terminal carries the full breakdown. |
| **Development Workflow** | Spec → plan → tasks → implement. Unit tests assert the sum invariant across the populations (registry ours/other, simulated rows, real unregistered rows, degraded `/proc`), and for each renderer. |

**Verdict: PASS.** No violation, so Complexity Tracking is omitted.

## Project Structure

### Documentation (this feature)

```text
specs/20260911-160717-capacity-breakdown-sum/
├── plan.md               # this file
├── spec.md
├── research.md           # R1–R5
├── data-model.md         # the snapshot's two new fields and its invariant
├── quickstart.md
├── contracts/
│   └── output.md         # capacity / status / pill / hold detail / audit shapes
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks output
```

### Source Code (repository root)

```text
src/robot_army/
├── capacity.py       # CapacitySnapshot.simulated, .in_flight, .components; snapshot()
│                     # partitions the unmatched rows; describe() renders components
├── operations.py     # capacity: two lines + two JSON keys; _capacity_dict: two keys
├── ordering.py       # global-cap hold detail renders components
├── dispatch.py       # dispatch.at_capacity detail gains the two counts
└── web/html.py       # the capacity pill renders the non-zero new terms

tests/unit/
├── test_capacity.py            # partition + sum invariant, degraded path, describe()
├── test_capacity_reporting.py  # the terminal block sums to its total; JSON keys
├── test_ordering.py            # hold detail names the new terms
└── test_web_render.py or test_web_views.py  # the pill sums
tests/integration/
└── test_dispatch_capacity.py   # the at_capacity record carries the counts

docs/guide/
├── 3-selection.md    # what the four lines mean
└── audit-log.md      # dispatch.at_capacity detail shape
```

**Structure Decision**: no new module. The partition lives where the union is computed, in
`capacity.snapshot`, so the terms and the total come from the same list.

## Phase 1 re-check (post-design)

| Question | Answer |
|---|---|
| Did the design add a dependency? | No. |
| A module, a table, a state file, a config key? | None. No config key, so `share/config.example.toml` needs no regeneration. |
| An abstraction with one implementation? | No. `components` has three readers (`describe`, the hold detail, and — via `_capacity_dict`'s summary and its own keys — the pill). |
| Anything on by default that was not? | No. |
| Which documentation? | `3-selection.md` (capacity reporting) and `audit-log.md` (a record's shape), per `CLAUDE.md`'s table. |

**Verdict: PASS, unchanged.**
