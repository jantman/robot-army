# Implementation Plan: Liveness Is Checked Wherever the Session Is Real

**Branch**: `issues/33` | **Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260830-133818-reconcile-session-liveness/spec.md`

## Summary

Reconciliation decides whether to check a session's liveness by asking whether the *record is
flagged simulated*, when the question it means to ask is whether the *session ever had a
process*. Those two agree at three effect levels and disagree at exactly one — `no-remote`,
where the session host is real but every row is still flagged `dry_run`. The consequence is that
the sweep which notices a dead worker is switched off at the level the quickstart recommends for
rehearsing with real sessions.

The fix is to ask the honest question. The record already answers it: the stored process
identifier comes from the session host boundary, and the simulated host returns `0` by
construction (research.md R2). So the discriminator is a column the row has always carried —
**no schema change, no migration, no backfill**, and no effect level consulted, which matters
because a test mechanically forbids `reconcile.py` from naming one (R6).

Phase 0 measurement then found a second, independent hole and disproved one the spec had
assumed. The liveness sweep looks only at an item's *most recent* session, so a superseded
attempt's still-open record is never loaded; and the sweep for unclaimed live workers passes
over that worker because its record still says `running` — which it does because nothing visits
it. Each blind spot conceals the other, and a live worker from a resumed attempt goes unwatched.
That is now User Story 3, measured in R10 at no additional test cost.

Three changes in one file, plus two fixture corrections:

1. **The discriminator** — skip the liveness check when the record has no process identifier,
   not when it is flagged simulated.
2. **A sweep over superseded sessions** — for each `active` item, examine every open record it
   still owns beyond its current attempt: report the live ones as orphans and leave them open,
   close the dead ones.
3. **Two counters** — one for records skipped as never-real, one for superseded records acted
   on, because today a pass that examined nothing reports the same `checked` as one that
   examined everything (R3, R9).

Everything below was measured against this checkout; see [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added. `httpx` remains the sole runtime dependency and is not
involved.

**Storage**: SQLite (`work_items`, `sessions`, `anomalies`) and the JSONL audit log — **all
unchanged**. `SCHEMA_VERSION` does not move. Every column read is one the row already carries;
see [data-model.md](./data-model.md).

**Testing**: pytest. Unit coverage for the discriminator across all four record shapes and for
the superseded sweep's three branches; integration coverage inside a full reconciliation pass
using the existing `seed_item` / `seed_session` / `write_registry` / `write_proc` fixtures. No
new fixture and no new marker is required — R3, R7 and R10 were all measured with what is
already there.

**Target Platform**: single Linux machine with a shell.

**Project Type**: single Python package (`src/robot_army`) — CLI plus daemon plus a small web
interface.

**Performance Goals**: none meaningful. The superseded sweep adds one `list_sessions_for_item`
per `active` item — bounded by the global session cap (5 by default), not by database size.

**Constraints**: the liveness decision must rest on the observation the pass has already taken,
so two parts of one pass cannot disagree about what is running; and `reconcile.py` may not name
`EffectLevel`, which T147 asserts mechanically.

**Scale/Scope**: one source file (`reconcile.py`), roughly forty lines of production code, one
new unit test module, and a two-call-site fixture correction in `tests/unit/test_reconcile.py`.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 — see below.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | PASS. One changed condition, one helper, two counters. No new column, dependency, command, configuration knob, or anomaly kind. Four tempting elaborations rejected below. |
| **II. Single-User, Local-First** | PASS. No new state, no network call, no service. Reads state already on this machine and writes rows that already exist. |
| **III. Total Accountability** | PASS, with the record improved in three places. See "What this logs". One acceptance is enumerated below rather than left silent. |
| **IV. Interruption Tolerance** | PASS. Every transition happens inside an existing `db.transaction`, so state and audit record commit together. Idempotent by construction: a killed pass resumes on the next one. See "What happens if it is killed halfway". |
| **V. Public Code, Unsupported** | PASS. No credentials or personal data. Phase 0 probes were throwaway files under `tests/unit/`, deleted after measurement; the tree was verified pristine (`git diff` empty, 1780 passed). Their output is quoted in research.md and contains only synthetic ids and `tmp_path` paths. |

### Rejected elaborations (Principle I)

| Tempting | Why it was rejected |
|---|---|
| A `simulated_host` column on `sessions` | A migration and a backfill to store a fact the row already carries. The stored process identifier is `0` exactly when the host was simulated, set through the boundary (R2) |
| Re-deriving the effect level inside `reconcile` | Forbidden by T147, and the whole point of FR-053: a level consulted downstream is a level a new code path can forget (R6) |
| Narrowing `_orphan_sweep`'s `running` guard | #28 declined this and the reasoning still holds — the guard suppresses cases neither feature has characterised. Raising the anomaly at the new site leaves that sweep byte-identical (R10) |
| Guarding the sweep against a vanished registry | Real (R8) and deliberately deferred: it would change `live` behaviour, which FR-013 forbids, and it is a second subject. Tracked as #44 |

### The Principle III acceptance this feature enumerates

A vanished or unreadable session registry reads to reconciliation as "every session is dead",
and this feature makes that reachable at `no-remote` as well as `live` (R8). No record
distinguishes "observed dead" from "could not observe" — `scan.degraded` and
`directory_missing` exist and `capacity.py` acts on them, but `reconcile.py` does not consult
either. **This is a known, accepted gap in the record, not an oversight**: it is pre-existing at
`live`, this feature makes the two levels behave alike rather than adding a new behaviour, and
closing it is tracked as #44. Naming it here is what Principle III's exception path
requires.

### What this logs (required by Development Workflow)

| Record | When | Meaning |
|---|---|---|
| `state.session` | a real session found dead | `running` → `lost`, unchanged in form; now actually reached at `no-remote` |
| `state.work_item` | the same | `active` → `interrupted`, likewise |
| `state.session` | a dead superseded attempt | `running` → `lost`, with a reason naming it as superseded rather than current |
| `orphan_session` anomaly | a live superseded attempt | **New in practice.** Measured today: this condition produces `orphans: 0` and no anomaly at all (R7) |
| `reconcile.pass` | every pass | Gains `skipped_never_real` and `superseded`. FR-009 exists because `checked: 2, interrupted: 0` read as a clean pass while a dead session was being ignored |

### What happens if it is killed halfway (required by Development Workflow)

- **Killed mid-sweep**: items already reconciled are committed with their audit records; those
  not yet reached are unchanged and picked up next pass. Each item is decided independently.
- **Killed between the session transition and the item transition**: both live in one
  `db.transaction`, so SQLite rolls back and the pair cannot be observed half-applied.
- **A late exit record arrives after a session was marked lost**: `spool.apply_record` returns
  `"duplicate"` — its terminal set already includes `lost` — and the work item is untouched.
  Measured by #28 (its R3) and unchanged here.
- **Re-run after any interruption**: idempotent. A `lost` row is no longer open, a closed row is
  not revisited, and the anomaly index absorbs re-detection.

## Project Structure

### Documentation (this feature)

```text
specs/20260830-133818-reconcile-session-liveness/
├── plan.md                       # This file
├── spec.md
├── research.md                   # Phase 0 — measured findings R1–R10, decisions D1/D2
├── data-model.md                 # Phase 1 — no schema change; the discriminator and the rule
├── quickstart.md                 # Phase 1 — validation scenarios, including the dangerous one
├── contracts/
│   └── liveness-decision.md      # The decision, its inputs, and what each outcome records
├── checklists/
│   └── requirements.md
└── tasks.md                      # Phase 2 — /speckit-tasks, NOT created here
```

### Source code (repository root)

```text
src/robot_army/
├── reconcile.py       # CHANGED: the skip keys on the absent process identifier
│                      # NEW: a sweep over each active item's superseded open sessions
│                      # NEW: ReconcileResult.skipped_never_real, .superseded
│                      # _orphan_sweep and _sweep_stale_sessions UNCHANGED
├── db.py              # UNCHANGED — list_sessions_for_item already exists
├── migrations.py      # UNCHANGED — no schema change, SCHEMA_VERSION does not move
├── states.py          # UNCHANGED — running -> lost is already a legal edge
├── capacity.py        # UNCHANGED — the count follows from the session's state
└── models.py          # UNCHANGED — orphan_session is already in ANOMALY_KINDS

tests/
├── unit/
│   ├── test_session_liveness.py  # NEW. The four record shapes; the superseded branches
│   └── test_reconcile.py         # CHANGED: two call sites given a truthful simulated shape
└── integration/
    └── test_reconcile_pass.py    # the whole pass: no-remote death, and a resumed item's ghost
```

**Structure Decision**: the existing single-package layout is unchanged. Both changes belong in
`reconcile.py` because that module's stated job is making recorded state match physical reality,
and both are cases where it was declining to look. No new module, and no new edge in the module
graph.

## Post-Design Constitution Re-Check

Re-evaluated against the Phase 1 artifacts: **PASS, unchanged.**

The design added no entity, no column, no dependency, no command, and no configuration. The two
places it could most easily have drifted are both bounded in writing: the liveness rule is fixed
by [C1](./contracts/liveness-decision.md) and reuses `RegistryEntry.alive()` rather than
introducing a second detection method, and the superseded rule reuses an anomaly kind that
already exists. Principle III's record is strictly better than before — two conditions that are
silent today now leave one — and the single gap that remains is enumerated above rather than
inherited quietly. No Complexity Tracking entries are required.

## Complexity Tracking

Not required — the Constitution Check records no violations.
