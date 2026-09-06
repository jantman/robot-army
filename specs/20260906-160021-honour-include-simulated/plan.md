# Implementation Plan: Every verb that offers `--include-simulated` honours it

**Branch**: `robot-army/issue-21-include-simulated-is-inert-on-anomalies` | **Date**: 2026-09-06 |
**Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260906-160021-honour-include-simulated/spec.md`

## Summary

`--include-simulated` is offered on six verbs and does nothing on three of them, and `status`
honours it everywhere except the anomaly block it prints last. The fix is not one change but a
decision taken per verb, because the verbs are not alike.

`anomalies` cannot filter because an anomaly does not record whether the run that raised it was
rehearsed. Migration 014 adds a `dry_run` column, `db.raise_anomaly` writes it, and each of the
seventeen call sites passes the value its subject carries or takes the real default —
[research R1, R2](research.md). The partial unique index gains the column too, or a rehearsal
could swallow a real anomaly by getting there first ([R3](research.md)).

`log` can already filter and simply does not: audit records have always carried `simulated` and
`dry_run`, and `_format_record` has always rendered either as `[simulated]`. The filter goes into
`_judge_record` beside the filters already there, which is what keeps a bounded page from coming
back empty ([R5](research.md)).

`repos` loses the flag. Onboarding inspects a real clone; a rehearsed repository row cannot
exist, so a filter there would be over an empty population ([R6](research.md)).

`worktree list` is already correct and gains a regression test.

The set of decorated verbs becomes a named constant, and a test drives every member of it in both
spellings against a state holding rehearsed rows of every kind — so the guarantee is enforced by
a failing test rather than by remembering ([R7](research.md)).

Separately, the issue's secondary defect: `reconcile` gains a pass that retracts a
`card_create_failing` anomaly whose card has since reached `linked`, beside the orphan resolver
that already does the same job for the one other kind whose condition can be positively
re-established as false ([R8](research.md)).

## Technical Context

**Language/Version**: Python 3.11+, standard library first

**Primary Dependencies**: none added. `sqlite3` and `argparse` from the standard library are the
whole of what this touches.

**Storage**: SQLite at `~/.local/state/robot-army/state.db`, plus the append-only audit JSONL at
`~/.local/state/robot-army/log/audit-YYYY-MM-DD.jsonl`. Schema version goes 13 → 14.

**Testing**: `pytest`, run as `uv run pytest`. New tests join `tests/unit/`; the existing
`tests/unit/test_db_scope.py`, `test_listing_withheld.py`, `test_anomaly_resolution.py`,
`test_anomalies_since.py` and `test_migrations.py` are the files this work extends.

**Target Platform**: one Linux machine, one user, no deployment infrastructure.

**Project Type**: single-process daemon with a CLI and a local read/write web interface.

**Performance Goals**: no new per-request cost of consequence. The anomalies table is bounded by
its partial unique index and holds tens of rows; the log reader's byte budget and page bound
(`LOG_SCAN_BUDGET_BYTES`, `LOG_PAGE_SIZE`) are unchanged, and the new predicate is a dict lookup
per record inside a scan that already parses each one.

**Constraints**: the paged log reader's existing two-second budget against 100,000 records
(milestone 001 SC-014) must survive the added filter. It does: the filter is applied inside the
existing scan rather than after it, so the page still stops the moment it is full.

**Scale/Scope**: seventeen `raise_anomaly` call sites, one migration, four operations, three web
functions, one parser change, four guide pages, one amended contract.

## Constitution Check

*GATE: passed before Phase 0, re-evaluated after Phase 1 design. No violations; the Complexity
Tracking table is therefore absent.*

**I. Simplicity First (YAGNI & KISS)** — PASS.

- No new dependency. No new module. No abstraction with one implementation.
- The one new column is a boolean, not a `source_effect_level` string that would record which
  level rehearsed a row — nothing asks the finer question ([R1](research.md)).
- `repos` is the principle applied in the direction that *removes* code: a `dry_run` column there
  would filter a population that is empty by construction, which is precisely "a configuration
  knob with one caller and no second use in hand" ([R6](research.md)).
- Retraction is added for one anomaly kind, not generalised into a registry of resolvers. The
  existing orphan resolver's docstring already argues for exactly this restraint and this
  feature does not overturn it ([R8](research.md)).
- The named constant in `cli.py` is not speculative generality: it has two callers on the day it
  lands — the parser that decorates from it, and the test that enumerates it ([R7](research.md)).

**II. Single-User, Local-First** — PASS. Nothing here reaches the network, adds a service, or
introduces a notion of who is asking. Both new reads are against the local database, and the
retraction pass deliberately needs no network so that a list does not go stale because Trello is
unreachable ([R8](research.md)).

**III. Total Accountability** — PASS, with one gap named as the principle requires.

- One new logged action: `anomaly.resolved` for `card_create_failing`, written before the row
  leaves the open list, carrying the kind, the card, and the state that establishes the condition
  false. It reuses the action name the orphan resolver already writes, so the reconstruction path
  for "why did this anomaly disappear?" stays one query.
- `db.resolve_anomaly`'s `resolved_at IS NULL` guard makes a repeated pass write nothing and
  therefore log nothing (FR-014). No unlogged retry, no swallowed exception, no fallback.
- **The named gap**: nothing in US1–US3 is logged, because nothing in US1–US3 changes state
  outside the process. Every one of those changes is to what a *read* returns, and reads have
  never been recorded in this system — the log records actions, and a listing is not one. The
  new `dry_run` value on an anomaly is not a separate action either; it is a field of the raise,
  which the raising caller already logs. See [R9](research.md).

**IV. Interruption Tolerance** — PASS.

- Migration 014 runs inside the runner's explicit `BEGIN`/`commit`, with `user_version` advanced
  in the same transaction, so a kill leaves version 14 whole or version 13 untouched.
- The retraction pass commits one anomaly at a time under `db.transaction`, so a pass killed
  midway leaves what it reached resolved and logged and the rest for the next pass. It is
  idempotent by the `resolved_at IS NULL` guard — the same shape `_resolve_orphan_anomalies`
  uses, chosen for the same reason.
- No network call is added, so no new timeout or retry bound is required.
- The listing changes carry no state at all.

**V. Public Code, Unsupported Project** — PASS. No credential, hostname or personal datum is
added. `repos` loses a flag with no deprecation path, which this principle expressly permits and
which costs nothing since the flag never did anything. The guide is updated for the author's
future self on the pages `CLAUDE.md` names for these subjects; nothing tutorial-shaped is added.

**Development Workflow** — PASS. Spec, plan, tasks, implement, with this gate. Unit tests are
required for every changed unit of behaviour, and this feature touches persistence (a migration),
a parser, and a state-settling pass — all three of the categories the constitution says need
failure- and interruption-path tests, which [quickstart.md](quickstart.md) and the task list
enumerate.

**`CLAUDE.md`'s two standing obligations**:

- *A change to behaviour updates its guide page.* Four pages, mapped in
  [R10](research.md#r10--surfaces-that-must-move-together): `operating.md` (anomalies, the web,
  recovery), `1-setup.md` (effect levels and what a rehearsal withholds), `audit-log.md` (the
  record's simulated markers), `state.md` (a new column, a new schema version).
- *A change to configuration regenerates the example.* No key is added, removed or renamed, so
  `exampleconfig.py` and `share/config.example.toml` are untouched, and
  `tests/unit/test_example_config_drift.py` will confirm that rather than take it on trust.

## Project Structure

### Documentation (this feature)

```text
specs/20260906-160021-honour-include-simulated/
├── plan.md                       # This file
├── spec.md                       # What and why
├── research.md                   # Phase 0: R1–R10
├── data-model.md                 # Phase 1: the anomalies column, the index, the record
├── quickstart.md                 # Phase 1: how to prove it works
├── contracts/
│   └── simulated-scope.md        # Phase 1: what each verb filters, and what it says
├── checklists/
│   └── requirements.md           # Spec quality gate
└── tasks.md                      # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

Only files that change are listed; this is a change to an existing system, not a new component.

```text
src/robot_army/
├── migrations.py     # SCHEMA_014_SQL: the dry_run column, the rebuilt partial index
├── models.py         # Anomaly.dry_run
├── db.py             # raise_anomaly takes dry_run; list_anomalies takes include_simulated;
│                     #   list_simulated_anomalies; open_card_create_failing_anomalies;
│                     #   a card lookup by (card_id, dry_run)
├── cli.py            # SIMULATED_SCOPED_COMMANDS; repos drops out; anomalies and log wired
├── operations.py     # anomalies(), status()'s anomaly block, read_log(), read_log_page(),
│                     #   _judge_record(), _anomaly_dict()
├── reconcile.py      # _resolve_card_create_anomalies, beside _resolve_orphan_anomalies
├── dispatch.py       # session_id_mismatch passes item.dry_run
├── intake.py         # card_create_failing and card_issue_missing pass card.dry_run
└── web/
    ├── pages.py      # chrome()'s anomaly count; anomalies_view; log_view
    └── server.py     # unchanged — it already passes the value both views discard

tests/unit/
├── test_db_scope.py              # list_anomalies joins LISTING_ACCESSORS
├── test_listing_withheld.py      # anomalies, log and worktree list withheld notes
├── test_anomaly_resolution.py    # card_create_failing retraction, and its refusals
├── test_migrations.py            # 014: default, back-fill reading, rebuilt index
└── (new) the cross-verb guard driving every SIMULATED_SCOPED_COMMANDS member

docs/guide/
├── operating.md      # anomalies now: filtered, and two kinds retract themselves
├── 1-setup.md        # which listings state what they withheld
├── audit-log.md      # the reader excludes rehearsed records by default
└── state.md          # anomalies.dry_run, schema 14

specs/001-minimum-daemon/contracts/cli.md   # the universal rule, amended as 008 amended it
```

**Structure Decision**: the existing single-package layout is kept unchanged. Every change lands
in the module that already owns the behaviour — the scope filter in `db.py` because that is where
`db.py`'s own docstring says the FR-056 guarantee is enforced, the rendering in `operations.py`
because both front ends read it from there, and the retraction in `reconcile.py` because that is
the pass whose job is re-checking what has settled. No file is created in `src/`.
