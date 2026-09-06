# Implementation Plan: Unique simulated issue numbers

**Branch**: `robot-army/issue-22-simulated-issue-numbers-collide-and-the` | **Date**: 2026-09-06 |
**Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260906-145911-unique-simulated-issue-numbers/spec.md`

## Summary

A simulated issue number is drawn from a counter that starts at zero in every process, so every
run mints the same sequence and a colliding card is retried with the number that is next in that
sequence rather than one that is free. The fix is to allocate from the record instead of from a
count: `create_issue` reads the highest simulated issue number recorded for that repository and
returns the one above it, floored at the recognisable base. The counter `comment()` shares with it
is separated, and the reason recorded when a mapping is nevertheless refused is rewritten to
describe what the next pass actually does.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: standard library only for this change (`sqlite3`). No new dependency.

**Storage**: the existing SQLite database; `cards` and `idx_cards_issue` unchanged, no migration.

**Testing**: pytest — `uv run pytest`

**Target Platform**: one Linux machine, one user

**Project Type**: single project (`src/robot_army/`, `tests/`)

**Performance Goals**: one card is filed in one attempt regardless of how many simulated cards the
repository already holds. Allocation costs one indexed `SELECT` per card filed.

**Constraints**: the simulated and real paths must stay structurally identical — the boundary
signature `create_issue(repo_key, title, body) -> Issue` does not change. No behaviour at `live`
changes.

**Scale/Scope**: three source files, one new database helper, and the tests that construct the
writer. Tens of cards per board; the allocation query returns one row.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design — see the re-check below.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | The change removes state rather than adding it: a per-process counter becomes a query against the record that already decides the answer. No new abstraction, no interface, no configuration knob, no dependency. The rejected alternatives in [research.md](research.md) R2 are rejected precisely on this ground — a `Callable` allocator would be an interface with one implementation. One new database helper with one caller is the concrete need, not generality. |
| **II. Single-User, Local-First** | Nothing added is networked, multi-user, or hosted. The allocator reads the same local SQLite file everything else reads, and the single-instance lock is what lets it assume no concurrent writer. |
| **III. Total Accountability** | No new action changes state outside the process, so no new record is required. Allocation is a read; the write it feeds is the existing `github.issue.create` record, which already carries the number under `detail.would_return.number`, marked `simulated: true`. Every failure path keeps its existing records: the refusal still lands as a card reason and, past the threshold, as a `card_create_failing` anomaly. **No gap is being introduced, so none needs justifying.** |
| **IV. Interruption Tolerance** | See "What happens if it is killed halfway through" below. Allocation is a `SELECT`; the four-step creation sequence, its intent row, and its transaction boundaries are untouched. |
| **V. Public Code, Unsupported Project** | No credential, no personal data. The boundary signature is internal, so changing `wire()`'s parameters owes nobody a deprecation. `docs/guide/2-intake.md` is updated because the guide is the documentation, per CLAUDE.md. |

### The two mandatory questions

**What does this log?** Nothing new, and that is the answer rather than an omission. The one action
that reaches outside the process on this path is the issue creation, which is simulated here and
already recorded as `github.issue.create` with `simulated: true`, the full title and body, and the
number it would have returned. Reading the highest recorded number is a query against our own
database — no state outside the process changes, so Principle III's trigger does not fire. The
records that *do* change are the ones already written on failure: the card's `reason` and the
`card_create_failing` anomaly detail carry the rewritten message, so a log read after the fact
describes the recovery that actually happens.

**What happens if it is killed halfway through?** The allocation is a single read with no
side effect; a process killed during it has changed nothing and the next run allocates afresh from
the same record. Killed between the allocation and the mapping write, the card stays in `creating`
with its intent row committed — the existing dangerous window, unchanged in width — and the next
pass allocates a new number rather than reusing the one that was in flight, because the number
lives in a local variable and never in the record until the mapping commits. A number allocated and
never written simply leaves a gap in the sequence, which the `MAX` allocation is indifferent to.
The mapping write itself remains one transaction, so it either takes the number or leaves it free.

## Project Structure

### Documentation (this feature)

```text
specs/20260906-145911-unique-simulated-issue-numbers/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output — the decisions and what was rejected
├── data-model.md        # Phase 1 output — what the allocation reads and writes
├── quickstart.md        # Phase 1 output — how to prove it works
├── contracts/
│   └── simulated-issue-number.md   # The allocation contract
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/robot_army/
├── boundaries/
│   └── github.py        # SimulatedIssueWriter: allocate from the record; separate the
│                        # comment counter (currently :1098-1157)
├── db.py                # + the helper that reads the highest simulated number for a repo
├── effects.py           # wire() gains the connection it passes to SimulatedIssueWriter (:250)
├── daemon.py            # wire_boundaries passes the connection it already holds (:722, :759)
├── operations.py        # build_context passes the connection it already holds (:191)
└── intake.py            # _mapping_conflict's message and _perform_creation's comment (:1205-1270)

tests/
├── unit/
│   ├── test_simulated_writers.py   # allocation behaviour, including across a "restart"
│   ├── test_github.py              # constructor call sites
│   ├── test_card_invariant.py      # constructor call sites
│   ├── test_db.py                  # the new helper, including the empty and gapped cases
│   └── test_effects.py             # wire()'s new parameter
└── integration/
    ├── test_card_to_issue.py       # a repo already holding simulated rows files in one pass
    ├── test_card_interruption.py   # the collision test, which can no longer use a restart
    └── test_effect_levels.py       # wire()'s new parameter

docs/guide/2-intake.md   # a paragraph in "One card, one issue"
```

**Structure Decision**: The existing single-project layout. No new module: the allocation belongs
in the writer that mints the number, and the query belongs beside the other card queries in
`db.py`.

## Design notes carried into tasks

1. **The allocation.** `max(SIMULATED_ISSUE_BASE, MAX(issue_number)) + 1` over `cards` where
   `repo_key` matches and `dry_run = 1`. Floored at the base so a repository with no simulated rows
   yet starts where it always did, and so a `NULL` maximum needs no separate branch.
2. **The connection is required**, not optional — research R3. Every construction site passes one.
3. **`comment()` keeps its own counter**, used only for URL distinctness — research R4.
4. **The guard stays, the message changes.** `_mapping_conflict` describes allocation, not
   incrementing; `_perform_creation`'s comment stops asserting that the counter having advanced
   guarantees a fresh number, and says instead why the guard remains worth keeping.
5. **`test_card_interruption.py`'s collision test must force the collision differently.** It
   currently relies on the restarting counter, which is the defect. The collision it exercises —
   an `IntegrityError` degrading to a retry rather than aborting the pass — is still worth testing,
   so it should insert the conflicting row directly, or stub the writer, rather than depend on the
   bug.

## Complexity Tracking

No Constitution Check violation. Nothing to justify.

## Post-Design Constitution Re-check

Re-checked against [data-model.md](data-model.md) and
[contracts/simulated-issue-number.md](contracts/simulated-issue-number.md):

- **I** — the design added one SQL helper and one constructor parameter, and deleted a field. Net
  simpler than what it replaces.
- **III** — the contract's failure cases all resolve to records that already exist. No new silent
  path: the one remaining way to reach the `IntegrityError` guard is still recorded as a card
  reason and still counts towards the anomaly threshold.
- **IV** — the contract states the allocation is a read with no side effect and that an allocated
  number is not reserved, which is what makes an interrupted run cost nothing but a gap.
- **II** and **V** — unaffected by the design phase.

Gate passes. No entry in Complexity Tracking.
