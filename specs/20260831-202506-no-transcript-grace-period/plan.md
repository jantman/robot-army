# Implementation Plan: Give the Missing-Transcript Check Time to Be Right

**Branch**: `20260831-202506-no-transcript-grace-period` | **Date**: 2026-08-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260831-202506-no-transcript-grace-period/spec.md`

## Summary

`dispatch.py:1044` asks whether a session left a resumable transcript **one line after confirming
the session is running**. The worker writes its transcript when it begins processing, not at exec,
so the file reliably does not exist yet and the `no_transcript` anomaly fires on every healthy
dispatch. The question is right; the moment is wrong.

The fix moves the question off the dispatch path and gives it a clock:

1. **Delete the inline check.** Dispatch stops asking. Confirming a session no longer produces an
   anomaly (FR-001).
2. **Record that the question is open.** One new nullable column, `sessions.transcript_checked_at`.
   `NULL` means "not yet judged"; a timestamp means "answered, never ask again". This is what makes
   the answer survive a restart (FR-012), guarantees one anomaly per session forever rather than one
   per unacknowledged window (FR-005), and keeps the sweep's cost proportional to open questions
   rather than to session history (FR-010).
3. **Answer it during reconciliation**, which already sweeps sessions on a timer and already reasons
   about their age. A session is judged once its transcript is found, or once
   `TRANSCRIPT_GRACE_SECONDS` (300) has elapsed since it was confirmed running — whichever comes
   first, whether it is still running or has ended (FR-002, FR-003, FR-004).
4. **Exempt only sessions that never ran a process**, read from the session row's `pid`, not from
   the effect level. This is issue #33's mistake in a second place: `dry_run` is true at `no-remote`,
   where sessions are real and write real transcripts, so keying on it switched the detector off at
   the one rehearsal level that could have caught this before a live dispatch did (FR-007).
5. **Say something useful when it fires.** The current note asserts a cause — stray `CLAUDE_CODE_*`
   variables — that was verifiably not the cause on the machine where it fired. The replacement names
   both possible causes, says the check cannot tell them apart, records how long it waited, and gives
   the one instruction that is true either way: restart, do not resume (FR-008, FR-009).

The anomaly and the `transcript_checked_at` write happen **in one transaction**, which is what makes
"at most one anomaly per session" hold across a kill mid-pass.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: None added. Standard library plus the project's existing `sqlite3` layer.

**Storage**: SQLite at the documented state path. One additive migration (008): a nullable column and
a partial index on `sessions`.

**Testing**: `pytest`, run via `uv run pytest`. New unit coverage in `tests/unit/`; the autouse
conftest seam pattern is extended so no test reads the real `~/.claude/projects`.

**Target Platform**: Single Linux machine, single user.

**Project Type**: CLI + daemon, single Python package.

**Performance Goals**: The sweep touches only sessions whose transcript question is open — in steady
state, zero to a handful of rows per pass, resolved via a partial index rather than a table scan.

**Constraints**: Must not delay dispatch; must not report before the grace period; must survive an
interruption at any point between dispatch and judgement; must not raise a second anomaly for a
session already reported, including after acknowledgement.

**Scale/Scope**: Two source modules changed (`dispatch.py`, `reconcile.py`), three touched
(`migrations.py`, `models.py`, `paths.py`/`sessions.py` for the test seam), plus README.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design. No violations.*

### I. Simplicity First (YAGNI & KISS)

- One column, one module constant, one sweep function. No new dependency, no new daemon job, no new
  configuration knob.
- **The grace period is a module constant, not config.** It has one caller and no second use in hand.
  Making it configurable would be exactly the speculative knob Principle I forbids; if the value
  proves wrong, the value changes.
- **The sweep runs inside the existing reconciliation pass** rather than on a timer of its own. No
  new concurrency, no new scheduling.
- One deliberate simplification against the spec's own assumption: the spec assumed the check would
  need an age window ("still open, or recently ended") to bound its population. The
  `transcript_checked_at` column bounds it inherently — every session is resolved exactly once and
  then leaves the population forever — so no window is needed and none is built. Fewer moving parts;
  recorded as R2.

### II. Single-User, Local-First

Unchanged. All state stays in the local SQLite database; the check reads a local directory. No
network call, no service, no account.

### III. Total Accountability — *what does this log?*

- **Each session examined leaves one record**, once: `session.transcript_found` when the transcript is
  there, `session.transcript_missing` when it is not, `session.transcript_skipped` for a session that
  never ran a process. Each names the session, its work item, and the seconds waited.
- **Each report additionally writes the anomaly row**, which is durable, append-only, and carries the
  same fields — the record a human actually reads.
- **Each pass reports its counters** (`transcripts_checked`, `no_transcript`) through the existing
  `reconcile.pass` record.
- **The migration's backfill is logged** by the existing migration record.
- **Enumerated gap: none.** Reconstruction from the log alone answers, for every session, whether its
  transcript was accounted for, when, and what was concluded.
- What is *removed* from the log: the dispatch path no longer writes a `no_transcript` anomaly. That
  is the defect, not a gap — the observation moves, it does not disappear.

### IV. Interruption Tolerance — *what happens if it is killed halfway through?*

- **Between dispatch and judgement**: `transcript_checked_at` stays `NULL`, so the next pass — in
  this process or a later one — asks the question that was never answered. Nothing is lost by a
  restart, which is precisely why the state is a column and not an in-memory set.
- **Mid-report**: the `raise_anomaly` and the `UPDATE ... SET transcript_checked_at` are in one
  `db.transaction`. A kill between them rolls back both, so the session is re-examined and reported
  once, never zero times and never twice.
- **Mid-migration**: `user_version` advances as the migration's last statement inside its
  transaction, so an interrupted 008 re-runs whole. The backfill is part of that transaction.
- **Bounded work**: the sweep is a single indexed query per pass with no retry loop and no network
  call.

### V. Public Code, Unsupported Project

README's `no_transcript` bullet is rewritten to describe when it now fires and what it means
(FR-013) — documentation for the author's future self, which is the standard this project holds.

**Post-design re-check**: no gate moved. The design adds one column, one constant, one function, and
one index; every principle's answer above is unchanged by Phase 1.

## Project Structure

### Documentation (this feature)

```text
specs/20260831-202506-no-transcript-grace-period/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── transcript-check.md
├── checklists/
│   └── requirements.md
├── spec.md
└── tasks.md             # /speckit-tasks output — NOT created here
```

### Source Code (repository root)

```text
src/robot_army/
├── dispatch.py       # DELETE the inline check at :1044 and its comment (FR-001)
├── reconcile.py      # ADD _sweep_transcripts + TRANSCRIPT_GRACE_SECONDS; call it from
│                     #   reconcile(); two new ReconcileResult counters
├── migrations.py     # ADD migration 008: column + partial index + backfill
├── models.py         # ADD Session.transcript_checked_at
├── paths.py          # ADD claude_projects_dir() — the seam ~/.claude/projects reads through
└── sessions.py       # transcript_exists() defaults to that seam

tests/
├── conftest.py                        # autouse fixture pointing the seam at tmp_path
└── unit/
    ├── test_transcript_check.py       # NEW: the grace period, both outcomes, once-only
    ├── test_reconcile.py              # the sweep in a full pass
    ├── test_migrations.py             # 008: column, index, backfill leaves no history judged
    └── test_sessions.py               # transcript_exists through the new default
```

**Structure Decision**: Single Python package, unchanged. The feature moves one existing check
between two existing modules and adds one column; it introduces no new layer, package, or process.

## Key Design Decisions

| # | Decision | Why |
|---|----------|-----|
| D1 | Check lives in `reconcile._sweep_transcripts`, called after `_sweep_stale_sessions` and before `_orphan_sweep` | Grouped with the other session-focused sweeps, and after every pass that could have settled a session's state this tick |
| D2 | State is one nullable column, `transcript_checked_at` | Answers "once only", "survives restart", and "bounded population" with one field. See R2 |
| D3 | Clock starts at `confirmed_at`, falling back to `started_at` | `started_at` is written before launch; the worker cannot write a transcript until it is running. Confirmation is when the clock should honestly start |
| D4 | Grace = 300s, a module constant | R1. Ten times the one measurement available, still reports within minutes |
| D5 | Exemption keyed on `session.pid`, never the effect level | FR-007; the same correction issue #33 made in this module |
| D6 | Anomaly + column write in one transaction | The interruption answer, and what makes FR-005 true |
| D7 | Migration backfills existing rows as already-checked | History is not retro-judged; every pre-upgrade session would otherwise be reported at once |

## Complexity Tracking

No Constitution Check violations. Nothing to justify.
