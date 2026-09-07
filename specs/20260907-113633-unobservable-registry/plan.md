# Implementation Plan: An Observation That Saw Nothing Is Not Evidence That Nothing Is Alive

**Branch**: `robot-army/issue-44-reconciliation-reads-an-unobservable` | **Date**: 2026-09-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260907-113633-unobservable-registry/spec.md`

## Summary

Reconciliation asks the session registry whether a session is there and treats "not there" as a
fact, without ever asking whether the registry could be read at all. `sessions.RegistryScan`
answers that second question — it carries `directory_missing` and `degraded` precisely so the
two can be told apart, and its docstring says one means "the machine is idle" while the other
means "this observation is worthless" — and `capacity.py` acts on both. `reconcile.py` names
neither. Measured (research.md R1): three running items and a registry directory that is
absent, unlistable, or of an unrecognised version, and one pass interrupts all three.

The fix is to consult the flags the scan already carries. Nothing new is observed, no fallback
is attempted, and no configuration is added — a pass that could not see declines to conclude
that anything died, and the next pass that can see concludes normally.

Phase 0 then found the same false conclusion in two further sweeps, and both are worse in kind
than the reported one (R5). `_sweep_stale_sessions` (#28) and `_sweep_superseded_sessions` (#33)
do not interrupt items; they close session rows, and a closed row is a returned capacity slot.
Closing a row whose worker is alive reports fewer running sessions than exist — the under-count
#28 established as the only capacity error that does real harm, reached here from the opposite
direction. That is User Story 2, measured at `reclaimed=1` and `superseded=1` in every blind
condition.

Five changes, in three files:

1. **One predicate** — `_registry_unobservable(scan)`, returning the reason a pass cannot draw
   conclusions or `None`. Modelled on `capacity._registry_unusable`, and deliberately *not*
   shared with it: its third clause differs, for a reason written at the guard (R3).
2. **Three guards** — one immediately above the only registry-dependent conclusion in the
   active-item sweep (R6), one inside `reclaim_stale_session` (R7), one in the superseded sweep.
   Each tests the pass's blindness **and** the absence of an entry for the session in hand
   (R12): `unknown_versions` is a per-file failure, so a blind pass can still hold a directly
   observed entry, and a guard keyed on the pass alone leaked the row of every worker
   retirement terminated.
3. **One counter and one flag in the record** — `liveness_withheld`, plus `directory_missing` in
   the scan summary, so three genuinely different passes stop writing the same line (R10).
4. **One anomaly kind** — `registry_unobservable`, raised once per blind condition and retracted
   by the first pass that can see, in the same shape as the two kinds that already retract
   themselves (R8).
5. **One line of output from `abandon`**, which shares the guarded function and would otherwise
   leave a slot subscribed without saying so (R7).

Everything below was measured against this checkout; see [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added. `httpx` remains the sole runtime dependency and is not
involved.

**Storage**: SQLite (`sessions`, `work_items`, `anomalies`) and the JSONL audit log. **No schema
change** — `SCHEMA_VERSION` does not move. The new anomaly kind needs no migration: the partial
unique index is `(kind, COALESCE(entity_type,''), COALESCE(entity_id,''), dry_run)`, which
already dedupes a kind carrying no entity (R8).

**Testing**: pytest. Unit coverage for the predicate across all four registry conditions and for
each of the three guards; the anomaly's raise, its de-duplication across repeated passes, and
its retraction. Integration coverage inside a full pass. Every fixture needed already exists —
`seed_item`, `seed_session`, `write_registry`, `write_proc` — and R1, R5 and R7 were all
measured with them.

**Target Platform**: single Linux machine with a shell.

**Project Type**: single Python package (`src/robot_army`) — CLI plus daemon plus a small web
interface.

**Performance Goals**: none meaningful. The predicate reads three fields of an object the pass
already holds. The retraction resolver adds one indexed query per pass, in the shape of the two
that already run.

**Constraints**: `reconcile.py` may not name `EffectLevel` — `test_only_effects_py_knows_the_effect_level_exists`
greps the file's text, comments included — so nothing here may reason about, or mention, the
effect level (FR-012). The behaviour on a *usable* observation must be byte-for-byte what it is
today, which is what the existing suite is for.

**Scale/Scope**: `reconcile.py` (the predicate, three guards, one counter, one resolver),
`sessions.py` (one key in `summarise`), `models.py` (one entry in `ANOMALY_KINDS`), `db.py` (one
narrow query helper), `operations.py` (one output line), two documentation pages, one new unit
test module and additions to two existing test modules.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 — see below.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | PASS. One predicate, three guards, one counter, one anomaly kind that mirrors two existing ones. No new column, migration, dependency, command, module, or configuration knob. Seven tempting elaborations rejected, in research.md R11 and below. |
| **II. Single-User, Local-First** | PASS. No new state, no network call, no service. Reads a filesystem observation the pass already took and writes rows that already exist. |
| **III. Total Accountability** | PASS, and this feature's *purpose* is partly to close a record gap #33's plan enumerated as an accepted omission. See "What this logs". One new gap is enumerated below rather than left silent. |
| **IV. Interruption Tolerance** | PASS. The change is entirely subtractive at the write sites — it declines to write — and every write it does add is inside an existing `db.transaction`. Idempotent by construction. See "What happens if it is killed halfway". |
| **V. Public Code, Unsupported** | PASS. No credentials or personal data. Phase 0 probes were throwaway files under `tests/unit/`, deleted after measurement; their output is quoted in research.md and contains only synthetic ids and `tmp_path` paths. |

### Rejected elaborations (Principle I)

| Tempting | Why it was rejected |
|---|---|
| Sharing `capacity._registry_unusable` | Its third clause is deliberately different (R3): a count tolerates a partly-refused registry, a death conclusion does not. One shared function would make one of the two callers silently wrong the next time either is edited, and neither call site would say so |
| A `/proc` fallback, as capacity performs | `scan_via_proc` cannot recover session identifiers, so nothing it returns can join to a session row. It answers capacity's question, not this one; running it would cost a full enumeration to learn nothing (FR-006) |
| A new field on `RegistryScan` | All three flags needed are already there. The judgement is the consumer's — `capacity.py` makes its own, and that is the right shape |
| Three counters, one per guarded sweep | They would always be zero together. FR-008 asks how many conclusions were withheld, not which sweep would have drawn them, and the sweep is recoverable from the state each row is left in |
| Escalation after N consecutive blind passes | State persisting between passes, to say what the standing anomaly already says. A knob with one caller and no second use in hand |
| Treating `unreadable` files as blindness | R4. A truncated file is the ordinary result of reading while the worker writes, so this would switch the liveness sweep off at random on a healthy busy machine — the failure #33 exists to prevent |
| Guarding `_retire_finished_sessions` or `_orphan_sweep` | R5. Neither draws a false conclusion from an empty scan. A guard there would be code with nothing to do, telling a later reader it had something to do |

### What this logs (required by Development Workflow)

| Record | When | Meaning |
|---|---|---|
| `reconcile.pass` | every pass, as today | Gains `liveness_withheld` and, through `sessions.summarise`, `directory_missing`. Together with the existing `degraded` and `unknown_versions` these separate three passes that write the same line today: an idle machine, a blind machine with items running, and a blind machine with nothing running (R10) |
| `registry_unobservable` anomaly | first pass that cannot observe | **New kind.** Carries the condition that made the observation unusable and how many conclusions were withheld (FR-011). Repeats are absorbed by the partial unique index, exactly as `registry_version_unknown` and `capacity_unobservable` already are |
| the same anomaly, resolved | first pass that *can* observe | `resolved_at` set, so it leaves the listing without a maintainer acknowledging it. The third kind to retract itself, in the shape of the two that already do |
| `state.session` / `state.work_item` | **fewer of them** | The whole point. A blind pass writes none of the transitions it writes today |
| `registry_version_unknown` anomaly | unchanged | Already raised by `scan_registry`; this feature does not touch it. It says the identification path degraded, which is a different fact from a pass having concluded nothing |

### The Principle III gaps this feature enumerates

One is closed and one is opened; both are named here because an undocumented gap is a violation
and a documented one is not.

**Closed**: #33's plan enumerated, as an accepted omission, that no record distinguished
"observed dead" from "could not observe" — explicitly on the ground that closing it was tracked
as this issue. The pass summary and the new anomaly close it.

**Opened, and accepted**: a withheld conclusion writes **no per-decision record**. Three open
rows on a blind machine would put 4,320 records a day into the log, each carrying one bit, and
the decision is re-derivable from the registry at any instant. The count in the pass summary
preserves the aggregate and the anomaly preserves the condition. `_retire_finished_sessions`
declines to record its own "not yet" for the same reason and sets the precedent; `abandon` is
the one caller outside the pass, and it says so in its output instead (R7).

**Accepted residual risk, not a record gap**: a registry file truncated at the instant of a pass
can still cost one item one false interruption (R4). It is already visible — `unreadable` is in
the pass summary — and closing it needs a second observation, which is a different feature.

### What happens if it is killed halfway (required by Development Workflow)

- **Killed mid-pass while blind**: nothing was being written by the guarded branches, so there
  is nothing to half-apply. Rows not yet reached are unchanged and reached next pass.
- **Killed between raising the anomaly and finishing the pass**: the anomaly is committed in its
  own transaction and stands on its own. The next pass either re-detects the condition (the
  index absorbs the repeat) or retracts it.
- **Killed between retracting the anomaly and finishing the pass**: the retraction is committed;
  a later blind pass raises a fresh row, which is correct — it is a new occurrence.
- **The registry returns mid-pass**: the pass holds one snapshot and decides consistently from
  it, which is the property `reconcile` already relies on. A pass that opened blind stays blind
  for its whole length and the next one concludes normally.
- **Re-run after any interruption**: idempotent. Withholding writes nothing to undo, and both
  anomaly operations are guarded by `IS NULL` predicates that make a repeat a genuine no-op.

## Project Structure

### Documentation (this feature)

```text
specs/20260907-113633-unobservable-registry/
├── plan.md                        # This file
├── spec.md
├── research.md                    # Phase 0 — measured findings R1–R11
├── data-model.md                  # Phase 1 — no schema change; the predicate's inputs
├── quickstart.md                  # Phase 1 — validation scenarios
├── contracts/
│   └── observability-guard.md     # The predicate, the three guards, and what each records
├── checklists/
│   └── requirements.md
└── tasks.md                       # Phase 2 — /speckit-tasks, NOT created here
```

### Source code (repository root)

```text
src/robot_army/
├── reconcile.py       # NEW: _registry_unobservable(scan) -> str | None
│                      # CHANGED: the active-item sweep withholds when blind
│                      # CHANGED: reclaim_stale_session gains a "withheld" outcome
│                      # CHANGED: _sweep_superseded_sessions withholds when blind
│                      # NEW: ReconcileResult.liveness_withheld
│                      # NEW: _resolve_registry_anomalies, the third retraction pass
│                      # _retire_finished_sessions and _orphan_sweep UNCHANGED (R5)
├── sessions.py        # CHANGED: summarise() reports directory_missing
├── models.py          # CHANGED: registry_unobservable joins ANOMALY_KINDS
├── db.py              # NEW: open_registry_unobservable_anomalies, beside its two siblings
├── operations.py      # CHANGED: abandon says when it left a row open, and why
├── capacity.py        # UNCHANGED — its own rule is deliberately different (R3)
├── migrations.py      # UNCHANGED — no schema change, SCHEMA_VERSION does not move
└── states.py          # UNCHANGED — no new edge; this feature removes traversals

docs/guide/
├── operating.md       # CHANGED: the new anomaly kind, and what a blind pass does
└── audit-log.md       # CHANGED: what reconcile.pass gains

tests/
├── unit/
│   ├── test_registry_observability.py   # NEW. The predicate, the three guards, the anomaly
│   ├── test_session_liveness.py         # CHANGED: the empty-directory invariant made explicit
│   └── test_anomaly_resolution.py       # CHANGED: the third retractable kind
└── integration/
    └── test_reconcile_pass.py           # CHANGED: a whole blind pass, and recovery after it
```

**Structure Decision**: the existing single-package layout is unchanged. The predicate belongs in
`reconcile.py` rather than in `sessions.py` because it is a judgement about what *this consumer*
may conclude, not a fact about the scan — which is exactly why `capacity.py` holds its own and
why the two differ. No new module, and no new edge in the module graph.

## Post-Design Constitution Re-Check

Re-evaluated against the Phase 1 artifacts: **PASS, unchanged.**

The design added no entity, no column, no dependency, no command, and no configuration key. The
two places it could most easily drift are bounded in writing: the predicate's three clauses are
fixed by [contracts/observability-guard.md](./contracts/observability-guard.md), and the guard
placement in the active-item sweep is fixed there too, because FR-004's requirement that
registry-independent conclusions survive is satisfied by *position* rather than by a test
remembering to check it (R6).

Principle III's record is strictly better than before: this feature exists in part to close a
gap a previous plan enumerated, and the one gap it opens is bounded, argued, and follows an
existing precedent. No Complexity Tracking entries are required.

## Complexity Tracking

Not required — the Constitution Check records no violations.
