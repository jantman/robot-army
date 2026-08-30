# Implementation Plan: A `--since` Window on `anomalies`

**Branch**: `robot-army/issue-24-verification-round-add-a-since-filter` | **Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/012-anomalies-since-filter/spec.md`

## Summary

`robot-army anomalies` gains an optional `--since DURATION` that narrows the listing to
anomalies detected inside that window. The duration text is parsed by the function `log
--since` already uses — the same call, not a copy — so the two commands accept and reject
exactly the same strings with the same messages. The filter is applied in the operation
layer over the already-fetched anomaly rows rather than pushed into SQL, because the honest
handling of an uninterpretable stored timestamp (keep it visible, never silently drop it) is
expressible in Python and is not expressible in a `WHERE detected_at >= ?` string comparison.

Everything else about the command is untouched: no `--since` means the current behaviour, byte
for byte, and `--acknowledge` still acknowledges and still writes its audit record.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: None added. `httpx` remains the sole runtime dependency and is not
touched. The work uses `datetime` from the standard library, already imported in the module.

**Storage**: SQLite at the documented state path; the `anomalies` table, read-only. **No schema
change and no migration** — `detected_at` already exists on every row.

**Testing**: pytest. New unit tests in `tests/unit/`; the existing suite must pass unmodified.

**Target Platform**: Single Linux machine with a shell.

**Project Type**: Single-package CLI (`src/robot_army/`) with a read-only web view alongside it.

**Performance Goals**: None beyond "instant at the terminal". The anomaly table is bounded by
the partial unique index on `(kind, entity_type, entity_id)` for unacknowledged rows, so the
list being filtered is small by construction; a Python-side filter over it costs nothing worth
measuring.

**Constraints**: The unfiltered output must not change. The comparison must treat timestamps
as instants, not as strings the local timezone could shift (milestone 010 made display local;
the stored value and every comparison against it stay UTC).

**Scale/Scope**: Roughly 25 lines of source across three files, plus tests and one doc line.

## Constitution Check

*GATE: passed before Phase 0, re-evaluated after Phase 1 design — see the re-check at the end.*

### I. Simplicity First (YAGNI & KISS)

**PASS.** One optional argument on one existing command. No new module, class, abstraction, or
dependency. The duration parser is reused by direct call, not extracted behind an interface —
there are two callers in one module and no third in hand, so a `DurationFilter` strategy would
be exactly the speculative generality this principle forbids. The filter is a comparison inside
the existing `anomalies()` function.

One judgement call is recorded rather than assumed: filtering in Python rather than in SQL.
That is the *simpler* of the two here, not the more elaborate one — the SQL form would need
the cutoff rendered back into the stored string format and would still fail FR-010 silently.
See [research.md](./research.md) R2.

### II. Single-User, Local-First

**PASS.** No new state, no network, no configuration, no secrets. Reads a local SQLite table
the command already reads.

### III. Total Accountability

**PASS, with nothing to except.** This feature adds no action that changes state outside the
process. It adds a filter to a read.

- **What does this log?** Nothing new. `anomalies` is a read command and reads are not
  audited today; that predates this feature and this feature does not widen it.
- The one state-changing path the command has, `--acknowledge`, already writes an
  `anomaly.acknowledge` audit record. FR-006 leaves that path untouched, so the record it
  writes is unchanged in content and in timing.
- **No documented exception is claimed**, because no action goes unlogged that would
  otherwise have been logged.
- The principle's *silent failure* clause is what drives FR-010: a row whose `detected_at`
  cannot be interpreted must not vanish from an anomaly listing because a filter could not
  judge it. The design keeps it visible.

### IV. Interruption Tolerance

**PASS.** Read-only. No writes, no checkpoints, no network calls, nothing to make atomic.

- **What happens if it is killed halfway through?** Nothing: the command has produced partial
  stdout and no durable effect. The pre-existing `--acknowledge` write is a single SQLite
  statement inside the existing transaction helper and is not modified.

### V. Public Code, Unsupported Project

**PASS.** No credentials or personal data. The CLI surface is explicitly not a stable API, so
adding an optional flag needs no deprecation or compatibility work. `README.md` and
`docs/logging.md`-adjacent CLI documentation are updated for the author's future self
(FR-011).

### Development Workflow

**PASS.** This plan exists and carries the Constitution Check. FR-012 requires unit tests for
the new behaviour, and the full suite must pass before the feature is complete. The filter is
code parsing external input (a user-supplied duration string) and therefore carries failure-path
tests, not only success-path ones, as the workflow section requires.

**Result: no violations. The Complexity Tracking table below stays empty.**

## Project Structure

### Documentation (this feature)

```text
specs/012-anomalies-since-filter/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── cli-anomalies.md # Phase 1 output — the command's contract
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/robot_army/
├── cli.py           # CHANGED: register --since on the anomalies subparser; pass it through
├── operations.py    # CHANGED: anomalies() takes since=; parse_duration moves above it
├── db.py            # UNCHANGED: list_anomalies keeps its current signature
├── models.py        # UNCHANGED: Anomaly already carries detected_at
├── migrations.py    # UNCHANGED: no schema change
└── web/
    ├── pages.py     # UNCHANGED: the web anomaly view stays unfiltered (spec Assumptions)
    └── server.py    # UNCHANGED

tests/unit/
└── test_anomalies_since.py   # NEW: the filter's success and failure paths

README.md            # CHANGED: one line in the "When something looks wrong" block
```

**Structure Decision**: The existing single-package layout is kept as-is. This feature touches
three files under `src/robot_army/` and adds one test module; there is no new directory, no new
package, and no new layer. `db.list_anomalies` deliberately keeps its signature — pushing the
window into SQL is rejected in research.md R2 — so the data-access layer is not part of the
change at all.

## Complexity Tracking

> No Constitution Check violations. Nothing to justify.

## Constitution Check — post-design re-evaluation

Re-run after Phase 1 produced [research.md](./research.md), [data-model.md](./data-model.md),
[contracts/cli-anomalies.md](./contracts/cli-anomalies.md) and [quickstart.md](./quickstart.md).

| Principle | Verdict | What the design actually turned out to be |
|---|---|---|
| I. Simplicity First | **PASS** | The design shrank rather than grew. `db.list_anomalies` is unchanged (R2), no new module was needed for the shared parser (R1), and no schema change appeared (data-model.md). Net: one optional parameter, one predicate, one moved block. |
| II. Single-User, Local-First | **PASS** | Unchanged by the design phase. No new state, network, config or secret. |
| III. Total Accountability | **PASS** | Confirmed, and R4 strengthened it: the design's only non-obvious branch exists *because* of the silent-failure clause. Still no new action to log and no exception claimed. |
| IV. Interruption Tolerance | **PASS** | R5 improved this. Validating the duration before the acknowledgement means a usage error cannot leave a half-done command behind — the one irreversible step is now unreachable from a rejected invocation. |
| V. Public Code, Unsupported Project | **PASS** | The contract document states in its own header that it is not a stability promise, which is the honest framing for this project. |
| Development Workflow | **PASS** | Both mandated questions are answered above and repeated here: **it logs nothing new**, and **killed halfway it leaves nothing behind**. Failure-path tests are required by FR-012 and enumerated in quickstart.md §3 and §4. |

**No violations surfaced during design. Complexity Tracking remains empty.**

One item is deferred rather than dropped, and named so it is not mistaken for an oversight: a
matching `?since=` filter on the web `/anomalies` view (R7). It is out of scope by the spec's
own Assumptions, and the operation-layer parameter added here is the seam it would use if the
maintainer ever wants it.
