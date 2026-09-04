# Implementation Plan: Retry Re-Verifies the Author

**Branch**: `speckit/20260903-203351-retry-author-recheck` | **Date**: 2026-09-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260903-203351-retry-author-recheck/spec.md`

## Summary

Two checks added, one fabricated value deleted, one nullable column, one sentence corrected
in two places.

`operations.retry` gains a call to `boundaries.issue_reader.get_issue` and a call to
`poll.evaluate` — the poller's own function, not a copy of it. `dispatch._dispatch_item`
stops writing `author=config.github.author` into the `Issue` it constructs and starts
reading `item.author`, refusing when it does not match. Migration 011 adds the column that
makes the second of those possible without a network call on the dispatch path.

The shape is dictated by one observation: `poll.evaluate` is already a pure function of an
`Issue`, a `Config`, a repository key and a boolean. It touches no connection, no boundary
and no clock. So FR-003 — "the two callers can never disagree about what makes an issue
eligible" — costs an import that already exists rather than a refactor
([R1](research.md)).

Three decisions carry the rest:

- **The verdict is re-derived, never read out of `blocked_reason`** ([R2](research.md)).
  The issue offers a string match on the stored reason as an "at minimum" fix; it would put
  a security boundary at the mercy of whoever next edits an f-string, and it is wrong in
  both directions — permanently refusing an item whose configured author has since changed,
  and waving through an item that reached `failed` for an unrelated reason after today's
  bug already smuggled it to `ready`.
- **The author is persisted, not re-read at dispatch** ([R6](research.md)). A `TEXT` column
  written where the issue is actually read beats a new HTTP call on a path that currently
  makes none and would have to grow its own timeout, retry and backoff story. It also means
  a redispatch behaves like a dispatch.
- **A `NULL` author refuses and names `retry` as the recovery** ([R7](research.md)). No
  backfill: a pre-011 `ready` row may have reached `ready` through the very defect being
  fixed, and no query can tell. Refusing it *into* the operation this feature hardens makes
  the upgrade self-healing.

The author comparison sits outside `_dispatch_item`'s `if not skip_gates:` block
([R8](research.md)). `skip_gates` is passed `True` by nobody today, so this changes no
behaviour — but a check whose documented character is "this cannot be disabled" must not
live under a flag named *skip gates*, in the file where the next reader will trust the
structure around it.

Scope is bounded to RA-01 plus the retry-path half of RA-04. The poll-to-dispatch half —
re-reading an issue that was edited between discovery and its first dispatch — is not
attempted, and FR-018 requires `docs/security-analysis.md` to say so rather than let the
finding read as closed.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added, none newly used. The issue read goes through the
existing `GitHubReader.get_issue`, already reached by `robot-army prompt` and by the Trello
intake.

**Storage**: SQLite. One migration — `_migration_011`, `ALTER TABLE work_items ADD COLUMN
author TEXT`. `SCHEMA_VERSION` 10 → 11. No backfill; see [data-model.md](data-model.md).

**Testing**: pytest. Unit tests in `tests/unit/`, integration coverage in
`tests/integration/test_dispatch.py`. `tests/conftest.py` needs exactly one change — a
defaulted `author` parameter on `seed_item` ([R10](research.md)). `FakeIssueReader` already
implements `get_issue`, records `get_issue_calls`, and carries `raise_on_get_issue`.

**Target Platform**: one Linux machine with a shell.

**Project Type**: single Python package with a CLI entry point and an embedded web server.

**Performance Goals**: one HTTP GET per retry that gets past the local preconditions. The
dispatch-side check is one string comparison against a column already in the loaded row.
Nothing is cached and nothing needs to be — retry is an interactive, confirmed, one-at-a-time
operation.

**Constraints**: the eligibility decision must remain a single implementation (FR-003); no
path may return an item to the queue on stored content (SC-002, SC-005); pre-existing rows
must remain readable (FR-017).

**Scale/Scope**: 8 source files (`operations.py`, `dispatch.py`, `db.py`, `migrations.py`,
`models.py`, `poll.py`, `cli.py`, `web/pages.py`), `tests/conftest.py` plus the six test
modules that call `db.insert_work_item` directly, and 3 documentation files. No new module,
no new dependency, no new configuration key, no new state, no new transition.

## Constitution Check

*GATE: passed before Phase 0; re-evaluated after Phase 1 design — see below.*

### I. Simplicity First (YAGNI & KISS)

**Pass.** No new module, no abstraction, no configuration knob, no dependency. The one new
persisted concept is a nullable column, and it exists because FR-014 needs a fact to compare
against — the alternative was an HTTP client in the dispatch path ([R6](research.md)).
`poll.evaluate` is *called*, not generalised: no strategy interface, no registry, no
"eligibility provider". Where two designs satisfied a requirement the one with fewer moving
parts won every time — most visibly in rejecting a machine-readable rejection-code column in
favour of simply asking the source again ([R2](research.md)).

### II. Single-User, Local-First

**Pass.** No accounts, no roles, no multi-tenancy. The author check is not authorization
between users of this system; it is a comparison against one configured value, and this
change makes that comparison real rather than adding a new one. All state stays in the
existing local SQLite file. No secret is read, written or logged.

### III. Total Accountability

**Pass, and it improves.** Today a refused retry writes **nothing** to the log — the reason
is returned to the caller and lost. `retry.evaluate`, `retry.blocked` and `dispatch.author`
close that gap; see [contracts/audit-records.md](contracts/audit-records.md) for the full
record shapes.

*What this logs.* Every retry that attempts a read writes one `retry.evaluate` carrying the
verdict, the author, and which columns were refreshed. Every retry refused before the read
writes one `retry.blocked`. Every dispatch refused on the author writes one
`dispatch.author` carrying both halves of the failed comparison. Successful transitions
continue to write `state.work_item`.

*Gaps this plan claims, as the Governance section requires them to be claimed:*

1. **A passing author check writes nothing.** One string comparison against a column
   already in the loaded row, passing on every healthy dispatch, immediately followed by a
   `state.work_item` record naming the item. Under the reconstruction standard the question
   "did the author check pass?" is answered by the presence of the next record.
   `_check_recorded_location` documents the identical omission for the identical reason.
2. **The refreshed title and body are not logged verbatim.** The record names *which*
   columns were rewritten, not their new contents. Those contents are attacker-controlled
   text of unbounded length and the log is a line-per-record file read in a terminal;
   letting an untrusted party choose the shape of the log would trade one property of the
   record for another. Reconstruction survives: the record carries the issue key and the
   content is one `robot-army show <id>` away.

### IV. Interruption Tolerance

**Pass.** No new network call is unbounded — `get_issue` reaches GitHub through the same
`httpx` client, timeout and retry policy every other read uses. The migration follows the
existing ladder: it runs inside a transaction and advances `user_version` last, so a kill
mid-migration re-runs it whole.

*What happens if it is killed halfway.* The content refresh commits before the
`failed → ready` transition, deliberately. Killed between them, the item is still `failed`
with accurate content and its old reason — corrected completely by the next retry. The
reverse order would leave an item in the queue carrying content nobody re-read, which is the
thing this feature exists to prevent. The full table is in
[data-model.md](data-model.md#interruption).

### V. Public Code, Unsupported Project

**Pass.** No credentials, hostnames or personal data enter the repository. `work_items.author`
holds a GitHub login already present in the issue's public URL. Breaking pre-011 rows out of
the queue is exactly the kind of change this principle permits, and it is documented rather
than smoothed over. `docs/state.md`, `docs/security-analysis.md` and `README.md` are updated
for the author's future self — no contribution guide, no migration shim.

### Development Workflow

**Pass.** Unit tests ship with every changed unit of behaviour. Two clauses apply with extra
force here and are honoured: this touches a state machine and code parsing external input,
so the failure and interruption paths are tested, not only the success path — the unreachable
source, the absent issue, each failing eligibility condition, the `NULL` author, and the
mismatched author each get a test. The migration gets its own, in the existing
`test_migrations` style.

### Re-evaluation after Phase 1

Re-checked against the artifacts above. No gate moved. The design did not acquire an
abstraction, a dependency, or a second implementation of anything; the only addition Phase 1
made was to enumerate the audit records, which strengthened Principle III rather than
straining it.

**Complexity Tracking**: not required — no violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/20260903-203351-retry-author-recheck/
├── plan.md              # This file
├── research.md          # Phase 0 — R1..R10
├── data-model.md        # Phase 1 — the one new column, and what refreshes
├── quickstart.md        # Phase 1 — how to prove it by hand
├── contracts/
│   ├── retry.md         # Phase 1 — check order, refusals, interface text
│   └── audit-records.md # Phase 1 — the three records, and two justified gaps
├── checklists/
│   └── requirements.md
└── spec.md
```

### Source Code (repository root)

```text
src/robot_army/
├── operations.py     # retry(): read the issue, evaluate it, refresh content, log
├── dispatch.py       # _dispatch_item(): compare item.author; drop the fabricated one
├── db.py             # insert_work_item(): accept and store author
├── migrations.py     # _migration_011 + SCHEMA_011_SQL; SCHEMA_VERSION -> 11
├── models.py         # WorkItem.author: str | None
├── poll.py           # unchanged — evaluate() gains a second caller, not an edit
├── cli.py            # retry parser help text
└── web/pages.py      # the retry ActionSpec description

tests/
├── conftest.py                       # seed_item(author=...)
├── unit/test_operations_retry.py     # new: the six checks and their refusals
├── unit/test_migrations.py           # migration 011
├── unit/test_web_actions.py          # the web path refuses too; description text
└── integration/test_dispatch.py      # the author backstop, mismatch and NULL

docs/
├── security-analysis.md   # RA-01 resolved; how much of RA-04 is not
├── state.md               # the new column, and what NULL means
└── README.md              # retry re-reads and re-verifies
```

**Structure Decision**: the existing single-package layout, unchanged. Every file above
already exists except one new unit test module; nothing here proposes a new directory or a
new home for anything.
