# Implementation Plan: Reclaiming capacity slots held by sessions that are no longer running

**Branch**: `issues/28` | **Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/015-reclaim-session-slots/spec.md`

## Summary

One rule, constructed once and applied at three call sites.

> A session row in `starting` or `running` is legitimate only while its work item is in
> `dispatching` or `active`. Anywhere else it is stale, and its capacity slot must come back.

1. **A decision helper in `reconcile.py`.** Given an open session row, its item, and an
   observation of the machine, it does one of three things: leave a legitimate row alone, decline
   to close a row whose worker is genuinely alive and raise `orphan_session` for it, or reclaim
   the row by transitioning it to `lost`. The three-way shape is what keeps FR-005 from being an
   afterthought — closing a row under a live worker would make the reported capacity *lower* than
   the number of running workers, which is the one direction of capacity error that causes real
   harm.

2. **A sweep in the reconciliation pass**, between the closed-issue pass and the orphan sweep. It
   applies the helper to every open session row in the database, which is what makes the invariant
   true regardless of route and reclaims rows leaked before this feature existed (FR-004). It adds
   one counter, `reclaimed`, because the issue's `checked 0` was the misleading part of the
   report.

3. **The same helper at `operations.cancel` and `operations.abandon`**, inside the transaction
   that already moves the work item. This is the maintainer's answer to FR-012: the slot is
   released before the command returns, with or without a daemon — which matters because the
   rehearsal workflow the bug was found in is CLI-only and has nothing sweeping on a timer.

Nothing else changes. **No schema change, no migration, no new dependency, no new configuration
knob, no new command, no new anomaly kind, and no new edge in either transition table.**
`capacity.py` is untouched: the count follows from the session's state, so FR-006 holds
structurally rather than by a second update.

Everything below was measured against this checkout rather than inferred; see
[research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added. `httpx` remains the sole runtime dependency and is not
involved.

**Storage**: SQLite (`work_items`, `sessions`, `anomalies`) and the JSONL audit log — **all
unchanged**. `SCHEMA_VERSION` does not move. Every column read or written already exists; see
[data-model.md](./data-model.md).

**Testing**: pytest. Unit coverage for the decision helper's three branches and for the two
command sites; integration coverage for the sweep inside a full reconciliation pass, using the
existing `write_registry` / `write_proc` / `seed_session` fixtures. No new marker and no new
fixture is required — the fakes this needs are the ones `tests/unit/test_capacity.py` already uses.

**Target Platform**: single Linux machine with a shell.

**Project Type**: single Python package (`src/robot_army`) — CLI plus daemon plus a small web
interface.

**Performance Goals**: none meaningful. The sweep costs one `list_sessions` over rows in two
states plus one `get_work_item` per open row — bounded by the global session cap (5 by default),
not by the size of the database.

**Constraints**: the liveness check must use the observation already taken in the pass rather than
re-scanning, so that two parts of one pass cannot disagree about what is running. At the command
sites there is no pass, so `cancel` and `abandon` take an optional `registry_dir` exactly as
`resume` and `restart` already do.

**Scale/Scope**: three source edits in two files (`reconcile.py`, `operations.py`), one new unit
test module, and cases added to two existing test modules. Roughly sixty lines of production code.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 — see below.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | PASS. One helper, one sweep, two call sites. No abstraction layer, no configuration knob, no new dependency, no new command. Five tempting elaborations were rejected in research and are named in the table below. |
| **II. Single-User, Local-First** | PASS. No new state, no network call, no service. The feature only reads state already on this machine and writes rows that already exist. |
| **III. Total Accountability** | PASS, with the record **improved** in two places. See "What this logs" below. Nothing is swallowed. |
| **IV. Interruption Tolerance** | PASS. Every transition happens inside an existing `db.transaction`, so the state change and its audit record commit together or not at all. The feature is idempotent by construction, so a killed pass simply resumes on the next one. See "What happens if it is killed halfway" below. |
| **V. Public Code, Unsupported** | PASS. No credentials, no personal data. The probe modules used for research were throwaway and were deleted; their measured output is quoted in research.md and contains only synthetic ids and `tmp_path` directories. |

### Rejected elaborations (Principle I)

| Tempting | Why it was rejected |
|---|---|
| Narrowing `_orphan_sweep`'s `row.state is RUNNING` guard instead of raising the anomaly at the new site | The guard also suppresses cases outside this feature's scope, and re-deriving which is a bigger question than #28 (R4) |
| A new anomaly kind for "live worker under a finished item" | `orphan_session` already means exactly that; a second kind would split one concept across two rows in `robot-army anomalies` (R4) |
| Adding `LOST → EXITED_ERROR` so a late exit record can correct the state | Makes a contradiction legal instead of resolving it; R3 shows it is also unnecessary (R2, R3) |
| Teaching `capacity.py` to filter registry entries by liveness | Out of scope, and the unfiltered count errs in the safe direction by design (R8) |
| Making `purge-simulated` operate on one item, as the issue's "escape hatch" section invites | This feature removes the need to reach for it. A finer-grained purge is a second way to do the same thing (spec Assumptions) |

### What this logs (required by Development Workflow)

Everything it logged before, plus two genuine gaps closed.

| Record | When | Meaning |
|---|---|---|
| `state.session` | every reclamation | `running`/`starting` → `lost`, with a reason naming **which route** closed it — cancellation, abandonment, or the sweep. That distinction survives nowhere else. |
| `reconcile.pass` | every pass | Gains `reclaimed`, the count of rows closed. FR-009 exists because the issue's `checked 0` read as "nothing to do" while a slot was being held. |
| `orphan_session` anomaly | a live worker under a finished item | **New in practice.** Measured today: this condition produces `orphans: 0` and no anomaly at all (R4). |

**No new gap in the record is introduced**, so Principle III's enumeration requirement has nothing
to enumerate. One pre-existing, already-documented omission is inherited rather than added: a pass
in which every open row is legitimate writes nothing, the same silence `_observe_speckit` already
justifies — with a 60-second cycle, the alternative is a log whose lines almost all say that
nothing changed.

### What happens if it is killed halfway (required by Development Workflow)

- **Killed mid-sweep**: rows already reclaimed are committed with their audit records; rows not yet
  reached are still open and are picked up by the next pass. There is no cross-row state to lose,
  because each row is decided independently.
- **Killed between `terminate` and the transaction in `cancel`**: the process is stopped but the
  row is still open — today's behaviour exactly, and now with a sweep that resolves it. This is the
  case that makes the belt-and-braces answer to FR-012 worth having rather than redundant.
- **Killed mid-transaction**: SQLite rolls back. The item's move and the session's close are in one
  transaction, so the pair cannot be observed half-applied.
- **A late exit record arrives after a reclamation**: `spool.apply_record` returns `"duplicate"`
  and the work item is untouched. Measured (R3), fixed in [C5](./contracts/slot-reclamation.md).
- **Re-run after any interruption**: idempotent by construction — a `lost` row is no longer
  selected, `transition_session` no-ops when source equals target, and the anomaly index absorbs
  re-detection (FR-008).

## Project Structure

### Documentation (this feature)

```text
specs/015-reclaim-session-slots/
├── plan.md                          # This file
├── spec.md
├── research.md                      # Phase 0 — measured findings R1–R8
├── data-model.md                    # Phase 1 — no schema change; the invariant and its two outcomes
├── quickstart.md                    # Phase 1 — four scenarios, including the dangerous one
├── contracts/
│   └── slot-reclamation.md          # The decision, the three call sites, and what each records
├── checklists/
│   └── requirements.md
└── tasks.md                         # Phase 2 — /speckit-tasks, NOT created here
```

### Source code (repository root)

```text
src/robot_army/
├── reconcile.py       # NEW helper: the three-way decision for one open session row
│                      # NEW sweep: applies it to every open row; ReconcileResult.reclaimed
│                      # _orphan_sweep and _resolve_closed_issues UNCHANGED
├── operations.py      # cancel: close the row it just stopped, in the existing transaction
│                      # abandon: close the item's open row, in the existing transaction
│                      # both gain `registry_dir`, as resume/restart already have
├── states.py          # UNCHANGED — neither transition table gains an edge
├── capacity.py        # UNCHANGED — the count follows from the session's state
└── models.py          # UNCHANGED — orphan_session is already in ANOMALY_KINDS

tests/
├── unit/
│   ├── test_slot_reclamation.py     # NEW. The three branches; the two command sites; idempotency
│   └── test_capacity.py             # a case asserting a reclaimed row stops counting
└── integration/
    └── test_dispatch_capacity.py    # the end-to-end story: cancel, then the held item dispatches
```

**Structure Decision**: the existing single-package layout is unchanged. The helper and the sweep
belong in `reconcile.py` because that module's stated job is making recorded state match physical
reality, and a `running` row under a finished item is exactly state that drifted. The command sites
call into it rather than reimplementing the rule; `operations.py` already imports `reconcile`
(`operations.py:38`), so no new edge is added to the module graph (R7).

## Implementation Notes

Detail that belongs to planning rather than to task breakdown.

### The helper

Three branches, in this order — the liveness check must come **before** the decision to close, not
after:

```
item.state in (dispatching, active)   -> leave
registry entry for session_id, alive  -> raise orphan_session, do not transition
otherwise                             -> transition_session(target=LOST, reason=<route>)
```

The reason string is a parameter, not a constant, because the three call sites must be
distinguishable in the log ([C3](./contracts/slot-reclamation.md)).

### The sweep's position in the pass

After `_resolve_closed_issues`, before `_orphan_sweep`. Both halves matter. Running it after the
active-item sweep and the closed-issue pass means items those moved out of `active` are seen in
their settled state and rows they already closed are not re-examined. Running it before
`_orphan_sweep` leaves that sweep's inputs unchanged, which is what lets `_orphan_sweep` stay
byte-for-byte as it is.

### The command sites

In `cancel`, the session close goes inside the **existing** `db.transaction` that moves the item to
`interrupted`, so the two commit together. Same in `abandon` for the move to `abandoned`. Neither
command may move an item it does not already move today (FR-011).

`abandon` is reachable from `ready`, `awaiting_review`, `interrupted` and `failed` — never from
`active` — so in practice it will rarely find an open row that is not the leak this feature is
fixing. It still consults liveness, because FR-005 is a property of the rule, not of the caller.

### Three things not to get wrong

- **`interrupted` is not a terminal state.** The issue's comment proposes the invariant as "no
  session row may remain `running` under a work item in a **terminal** state", but `TERMINAL_WORK_ITEM_STATES`
  is only `{done, abandoned}` — and the reported case leaves the item `interrupted`. An
  implementation written against `TERMINAL_WORK_ITEM_STATES` would miss the very bug being fixed.
  The rule is an allow-list of `dispatching`/`active`, not a deny-list of terminal states (R5).
- **A closed row must stay resumable.** `operations.resume` reads
  `db.latest_session_for_item(...).session_id` and does not require it to be open, so reclaiming
  does not break resume — but the row must be transitioned, never deleted (FR-010).
- **A live worker under a `done` item will now raise an anomaly where none is raised today.** That
  happens when an issue is closed while its worker is still running. It is the correct outcome and
  it is a visible behaviour change worth expecting rather than debugging (R6).

## Post-Design Constitution Re-Check

Re-evaluated against the Phase 1 artifacts: **PASS, unchanged.**

The design added no entity, no column, no dependency, no command, and no configuration. The place
it could most easily have drifted toward complexity — FR-005's liveness requirement — is bounded in
writing by [C1](./contracts/slot-reclamation.md), reuses `RegistryEntry.alive()` rather than
introducing a second detection method, and reuses an anomaly kind that already exists. Principle
III's record is strictly better than before: two conditions that are silent today now leave a
record. No Complexity Tracking entries are required.

## Superseded in part by issue #34 (recorded 2026-08-30, after the merge)

Issue #34's fix landed on `main` while this branch was being written, and it changed
`operations.cancel` to verify the process is actually gone and then close the session row
itself. That is strictly stronger than what this plan proposed for the cancel site — a
confirmed termination beats a liveness check — so on merge the cancel wiring here was
**dropped in favour of main's**, along with the `registry_dir`/`proc_root` parameters it
needed.

What #34 does **not** do, and what this feature therefore still delivers:

- **`abandon` still leaked.** It stops no process, so it cannot use #34's confirmation, and
  it still needs the rule. Unchanged from this plan.
- **Nothing reclaimed rows already leaked.** #34 fixed the cancel route going forward and
  added no sweep, so a database that was leaking before it landed still is — including the
  maintainer's, per issue #28's own comment.
- **A live worker under a finished item was still invisible.** `_orphan_sweep` skips any
  entry whose row is `running`, and #34 did not touch that.

Two tests were dropped as superseded rather than rewritten: the live-worker cancel case,
which #34 answers better by refusing to settle an unconfirmed stop at all, and a worktree
assertion already covered elsewhere. The surviving cancel test asserts the *capacity*
consequence, which `tests/unit/test_cancel.py` does not.

## Complexity Tracking

None. The Constitution Check passed with no violations.
