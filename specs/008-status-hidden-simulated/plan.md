# Implementation Plan: Status Never Contradicts Itself About Hidden Simulated Work

**Branch**: `008-status-hidden-simulated` | **Date**: 2026-08-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/008-status-hidden-simulated/spec.md`

## Summary

A rendering fix and two counting accessors. `operations.status` already holds every fact it
needs — a queue built with simulated rows included, and counts and a listing built with them
excluded — and simply never reconciles the two before printing. This adds the missing number:
how many rows this invocation withheld, computed under the same filters the visible listing
used, printed as one short line wherever the command would otherwise claim absence or
undercount, and carried in the machine-readable payload beside the fields it explains. The
queue's simulated rows also gain the `*` marking every other table in the CLI already uses,
so a reader who stops at the queue still knows what those rows are.

Nothing about *which* rows are shown changes. `ordering.plan` keeps `include_simulated=True`
because simulated rows occupy capacity; the listing keeps excluding them because FR-056 of
milestone 001 is deliberate and `purge-simulated` exists precisely so they do not accumulate
as real history. No migration, no new module, no new dependency, no configuration key.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added. Two SQL `COUNT(*)` statements and string formatting.
`httpx` remains the only runtime dependency and is untouched.

**Storage**: the existing SQLite database, read-only for this feature. No migration, no schema
change, no new column. The withheld count is derived per invocation and never persisted.

**Testing**: pytest. A new unit test file exercising the full matrix of (rows present) ×
(`--include-simulated` or not) × (filters or not), asserting the central invariant directly:
no `status` output may both display work items and claim there are none. Extensions to
`tests/unit/test_db_scope.py` for the new accessors, and a rendering test for the queue's
simulated marking.

**Target Platform**: the same single Linux machine, terminal only. No new external surface.

**Project Type**: single Python package (`src/robot_army/`) with a CLI and a web front end.

**Performance Goals**: two additional indexed `COUNT(*)` queries per `status` invocation
against a table holding hundreds of rows at most. `status` already issues health, capacity,
control, anomaly, listing, and ordering queries; two counts are below that noise floor and are
therefore run unconditionally rather than behind a guard that would cost more to read than to
skip.

**Constraints**: the stated withheld count MUST equal the number of rows the reveal flag would
actually surface for that same invocation (FR-004) — a number that is merely *near* the truth
replaces one contradiction with a subtler one. Output with no simulated rows present MUST be
unchanged (SC-004). Exit codes MUST NOT change (FR-012).

**Scale/Scope**: one command's rendering, roughly 30 lines changed in `operations.py`, two new
accessors of about eight lines each in `db.py`, one contract document, one new test file. Two
further commands (`cards`, `worktree list`) receive the same treatment under P3.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Simplicity First (YAGNI & KISS) — **PASS**

- **No new dependency and no new module.** The change lives in the two files that already own
  the behaviour: `db.py` gains counting accessors beside the listing accessors they mirror,
  and `operations.py` prints one more line in three places.
- **No abstraction.** A "reporting scope" object or a shared `Withheld` type threading through
  every listing was considered and rejected: there are three call sites, they do not share a
  shape, and a type with three uses and no fourth in hand is the tax Principle I forbids.
  Research [R3](research.md) records the rejection.
- **No configuration knob.** No option is added to suppress the disclosure. A surface that can
  be configured into lying is not a fix, and a knob with one plausible setting is speculative
  generality.
- **Two designs compared, fewer moving parts won.** Deriving the count by fetching all rows and
  partitioning them in Python needs no new accessor but hand-rolls the `dry_run` predicate that
  `db._scope` exists to own. A dedicated `COUNT(*)` keeps the predicate in one place and is the
  smaller change in total. See [R1](research.md).

### II. Single-User, Local-First — **PASS**

Terminal rendering against the local database. No account, no network call, no new persistent
state, no new path, no secret handled. The command remains runnable with the machine offline.

### III. Total Accountability — **PASS**

**What does this log?** Nothing new, and correctly so. `status` is a pure read: it changes no
state outside the running process, executes no command, sends no request, and writes no file,
so Principle III's obligation is not engaged. It logs nothing today for the same reason. There
is no unlogged *action* here to enumerate as an exception — there is no action.

The feature nonetheless serves the principle's purpose rather than merely escaping its letter.
The standard of correctness in Principle III is reconstruction: being able to say what the
system did and to what. A status surface that reports four queued items and zero work items in
one breath actively obstructs that reconstruction, and the maintainer's only recourse today is
to read the source. Removing the contradiction is accountability work even though it emits no
records.

**Silent failure**: none is introduced. The withheld count has no failure mode of its own — a
`COUNT(*)` against an already-open connection either returns or raises, and a raise propagates
as `status` already propagates database errors, with a non-zero exit.

### IV. Interruption Tolerance — **PASS**

**What happens if it is killed halfway through?** The maintainer sees a truncated line of
terminal output and nothing else. There are no writes to interrupt, no checkpoint to record, no
network call to time out, and no partially written file to become observable to a later run.
Re-running the command is the entire recovery procedure and is trivially idempotent. Principle
IV's precautions would be extreme here, which is exactly the case its final clause anticipates.

### V. Public Code, Unsupported Project — **PASS**

Nothing committed here is a credential, a hostname, or personal data — the fixture rows are
synthetic. The machine-readable payload gains a field and loses none; that is additive by
choice rather than by obligation, because the in-repository consumer (`web/pages.py`, which
calls `operations.status` directly) reads by key and an added key disturbs nothing. No
deprecation shim, no versioned response, no migration path for outside consumers.

**Result: all five principles pass. The Complexity Tracking table is therefore empty and has
been removed.**

## Project Structure

### Documentation (this feature)

```text
specs/008-status-hidden-simulated/
├── plan.md              # This file
├── research.md          # Phase 0 output — the six decisions this design rests on
├── data-model.md        # Phase 1 output — no schema change; the derived quantity
├── quickstart.md        # Phase 1 output — how to prove the fix by hand
├── contracts/
│   └── status-output.md # Phase 1 output — exact text lines and payload fields
├── checklists/
│   └── requirements.md  # Spec quality checklist (/speckit-specify output)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/robot_army/
├── db.py            # + count_simulated_work_items, + count_simulated_cards
├── operations.py    # status(): withheld disclosure ×2, queue simulated marking,
│                    #   payload field; cards(), worktree_list(): same disclosure (P3)
└── cli.py           # unchanged — the flag and the routing already exist

specs/001-minimum-daemon/contracts/
└── cli.md           # `robot-army status` section gains the output guarantee

tests/unit/
├── test_status_withheld.py   # new — the contradiction invariant and the matrix
└── test_db_scope.py          # extended — the new accessors' scope and signatures
```

**Structure Decision**: no new files under `src/`. This is a defect in two existing functions
and it is repaired where it lives. `db.py` is the right home for the counting accessors because
`_scope` — the single definition of what "simulated" means to a query — is already there, and
keeping the predicate in one file is what makes the stated count and the revealed rows provably
the same set. `contracts/cli.md` belongs to milestone 001 and is amended in place rather than
shadowed by a second document describing the same command, since the milestone that introduced
the command still owns its contract.

## Phase 0 — Research

See [research.md](research.md). Six decisions, all resolved; no NEEDS CLARIFICATION remains.

| # | Question | Decision |
|---|---|---|
| R1 | How is the withheld count derived? | A dedicated `COUNT(*)` accessor per table, not a second full fetch and not Python-side filtering |
| R2 | The counts section and the listing have different scopes — one number or two? | Two, because they answer two different questions; conflating them would understate or overstate |
| R3 | Should the disclosure be a shared helper across the three commands? | No — three call sites, three shapes, no fourth in hand |
| R4 | Exact wording and placement of the disclosure | One parenthetical line, appended to the existing absence message or printed beneath the table |
| R5 | How are simulated rows marked in the queue table? | The `*`-suffix-plus-footnote convention the item and worktree tables already use |
| R6 | What shape does the payload field take? | `withheld_simulated: {counts, items}`, keyed to the payload sections it explains |

## Phase 1 — Design

- [data-model.md](data-model.md) — no entity changes and no migration; the withheld count as a
  derived, per-invocation quantity, with its scoping rules stated precisely.
- [contracts/status-output.md](contracts/status-output.md) — the exact lines `status` prints in
  each case, the payload field, and the invariant that binds them.
- [quickstart.md](quickstart.md) — the manual reproduction from issue #13 and the checks that
  prove it fixed, runnable at `effect_level = "plan"` on a scratch database.

### Constitution re-check after design — **PASS, unchanged**

The design added two accessors and one payload key. It added no module, no dependency, no
configuration, no schema change, no persistent state, and no outward-facing action. The
Principle III and IV answers above were written against the finished design and stand as
written: nothing here acts, so nothing here logs; nothing here writes, so nothing here can be
half-written.
