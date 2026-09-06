# Implementation Plan: A merged pull request retires the session

**Branch**: `robot-army/issue-149-a-merged-pr-should-retire-the-session` | **Date**: 2026-09-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260906-140852-merged-pr-retires-session/spec.md`

## Summary

Retirement waits 30 minutes of worker idleness before it will end a finished session. On the
ordinary successful path the worker goes quiet, the maintainer merges within a few minutes, the
issue closes, and the item reaches `done` — so `done` always arrives *inside* that window.
Retirement declines, correctly by its own rules, and `_sweep_stale_sessions` runs eight lines
later, finds an open row under a `done` item, and raises `orphan_session`. For the next ~29
minutes the anomaly stands, the capacity slot is held, the tab stays open and the worktree
reports `skipped`. The gate has never once been crossed by an item completing normally.

This makes the quiet period conditional on whether an explicit completion signal exists. **An
item in `done` with a merged pull request is retired as soon as its worker is observed idle,
with no minimum duration.** An item in `done` with no merged pull request keeps
`RETIRE_IDLE_SECONDS = 1800` exactly as it is — there, the idleness heuristic is still the only
evidence available and its existing justification stands.

Four findings shape the work, and each of them removed something rather than adding it:

1. **The signal is already stored, already fresh, and already parsed.** `_refresh_pull_requests`
   runs first in the pass, deliberately, so on the ordinary path the item's `pull_requests`
   column says `merged` and was written by this same pass. `WorkItem.pull_request_list` already
   collapses every failure mode — `NULL`, `[]`, unparseable, not-a-list, non-object elements —
   to `[]` (R1, R2).
2. **Only the *duration* requirement is conditional.** `entry.idle_for()` returning `None` keeps
   its meaning on both paths, which is what preserves "every unknown delays a retirement, never
   causes one" and keeps a worker from being ended mid-tool-call (R3).
3. **No floor on the merged path.** Arithmetic, not preference: the worker had been idle 47
   seconds when its item went `done`, so any non-zero floor declines on the one pass that
   matters and the anomaly is raised anyway (R4).
4. **The tab, the worktree and the slot need no code.** All three are gated on the session row
   being closed, so retiring 30 minutes earlier moves all three 30 minutes earlier (R6). This is
   why #149 says fixing this fixes most of #81.

The change is roughly twenty lines of source: one derived property, one decision helper, one
audit key, and the correction of two comments that this change makes false.

## Technical Context

**Language/Version**: Python 3.11+, standard library first

**Primary Dependencies**: none added, and none touched. No boundary gains a method

**Storage**: SQLite at `~/.local/state/robot-army/state.db`. **No migration.** The column read
was added by migration 013 and is unchanged

**Testing**: `pytest`, `uv run pytest`. `tests/unit/test_session_retirement.py` already has the
fixtures; the pull request set is seeded with one `db.record_pull_requests` call and needs no
GitHub double

**Target Platform**: one Linux machine, one user

**Project Type**: single-process daemon plus CLI plus a read-mostly web interface

**Performance Goals**: unchanged. The new read is one already-parsed column per open session
row, and open rows are bounded by the concurrency cap. **No network call is added on any path**

**Constraints**: reconciliation must never raise for an operational condition; every
irreversible act logged before it happens

**Scale/Scope**: three concurrent sessions by configuration, tens of work items

## Constitution Check

*GATE: passed before Phase 0 research; re-checked after Phase 1 design — see the bottom of this
file.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | **Passes, and the design is a subtraction.** No configuration key, no new state, no transition, no migration, no boundary, no dependency, no anomaly kind, no cache, no interval. Two named things are added: `WorkItem.has_merged_pull_request` and a module-level decision helper in `reconcile.py`. Neither is an abstraction over a second implementation — they are names for conditions the code must express somewhere, and R9 argues the placement. The alternative is not less code but the same code further from its reason. Three of the four downstream effects the issue asks for (tab, worktree, slot) are delivered by **changing nothing**. |
| **II. Single-User, Local-First** | **Passes.** Everything read is local: a SQLite column, `/proc`, `~/.claude/sessions`. No hosted service, no network call. FR-004 makes "no network call to establish the signal" a requirement rather than a happy accident. |
| **III. Total Accountability** | **Passes, with the same single documented gap as the feature it amends.** Retirement is still logged **before** the signal, and the record now says which of the two conditions authorised it (FR-009) — which *improves* reconstructability: with one gate `idle_s` implied the reason, with two it no longer does. **The documented gap, unchanged and re-enumerated as Principle III requires**: a decision *not* to retire writes nothing at all — not a record, not a column (FR-010). A 60-second loop reporting "still busy" about a session the maintainer is using would write ~1,440 records a day carrying one bit, and the condition is re-derivable from the registry and the stored column at any instant. Precedent: `_sweep_transcripts`, and `_record_pull_requests`'s own unchanged-set omission. |
| **IV. Interruption Tolerance** | **Passes.** No write is added, so no new interruption window exists. The three existing windows are restated and answered in R7: killed before the signal (the intent record is on disk; the next pass asks again), killed between the signal and the transition (a dead process under an open row, which `_sweep_stale_sessions` reclaims), and the worker ending itself in between (`reclaim_stale_session` returns `left`, an ordinary outcome). No network call, so no timeout or retry policy changes. |
| **V. Public Code, Unsupported Project** | **Passes.** No credential is read, written or logged. The audit record gains one enumerated string, not a URL, a token or a name. No compatibility shim: the old behaviour is replaced on one path, not kept alongside it. |
| **Operating Constraints** | **Passes.** Everything stays reachable from the terminal — `robot-army reconcile` drives it, `robot-army anomalies` shows the result. Ending a process is irreversible from this side and is therefore logged before execution. It remains **not** the kind of irreversible act that must require explicit configuration: it destroys nothing. The transcript survives in full and `claude --resume` brings the session back, which is exactly the distinction between retirement and `cleanup.on_issue_close`. This change makes retirement happen *sooner*, not *more destructively*, so that argument transfers unweakened. |
| **Development Workflow** | **Passes.** Unit tests for every changed unit, including the decision table's new rows and the two paths' audit records. The decision is state-machine-adjacent and the column is externally-sourced input, so both carry failure-path tests: every malformed shape of the stored column, and every way `idle_for()` can answer `None`. The constitution's two questions: **what does this log** — `session.retire` gains `signal`; nothing else changes. **What happens if it is killed halfway** — R7, unchanged from the feature this amends because no write is added. |

**Result: PASS.** No violation to justify. One documented Principle III gap, inherited and
re-enumerated above.

## Project Structure

### Documentation (this feature)

```text
specs/20260906-140852-merged-pr-retires-session/
├── plan.md              # This file
├── spec.md
├── research.md          # Phase 0 — ten findings
├── data-model.md        # Phase 1 — the finding that there is no schema change
├── quickstart.md        # Phase 1 — five validation scenarios
├── checklists/
│   └── requirements.md
├── contracts/
│   └── retirement-signal.md   # amends the earlier session-retirement contract
└── tasks.md             # /speckit-tasks output — not created here
```

### Source Code (repository root)

```text
src/robot_army/
├── models.py          # + WorkItem.has_merged_pull_request, beside pull_request_list
└── reconcile.py       # the gate splits: idle_for() is None stays a hard LEAVE, the
                       #   duration becomes conditional; + _retire_signal(); _retire_one
                       #   gains the signal and puts it in the detail; the two false
                       #   comments (line 511 and RETIRE_IDLE_SECONDS) are corrected

tests/unit/
├── test_session_retirement.py   # extended — the new decision rows, both signals in the
                                 #   record, and the full-pass no-anomaly assertion
└── test_reconcile_pull_requests.py  # extended — has_merged_pull_request over every
                                 #   column shape, beside the existing unreadable-column
                                 #   tests for pull_request_list

docs/guide/
├── 5-outcome.md       # the retirement section states the 30-minute rule as if it were the
                       #   only one; the tab section inherits the correction
└── audit-log.md       # session.retire's detail gains `signal`
```

**Structure Decision**: no new module and no new test file. The gate belongs in `reconcile.py`
beside the ordering argument it depends on; the predicate belongs in `models.py` beside the
property it reads. `docs/guide/configuration.md`, `src/robot_army/exampleconfig.py` and
`share/config.example.toml` are deliberately **absent**: no configuration key changes, so
neither of CLAUDE.md's two configuration steps is triggered. `docs/guide/state.md` is absent for
the same reason — no table, no state file and no reboot behaviour changes.

## Key design decisions

Full reasoning in [research.md](./research.md); [the contract](./contracts/retirement-signal.md)
is normative. The short version:

**The decision table gains two rows and loses none.** Rules 1–5 are untouched, and rule 5 —
`idle_for()` is `None` — keeping its position *ahead* of the merged-pull-request rule is the
whole of FR-002. A merged pull request removes the duration requirement; it never removes the
idleness one. Being wrong about the registry must still only ever delay a retirement.

**Any merged pull request counts, not the newest.** A retried item can carry a closed-unmerged
attempt alongside the merged one, and the merged one is still the maintainer's acceptance.

**"Never looked up" reads as "not merged".** Not as unknown, and not as a reason to ask GitHub.
It is the direction that delays a retirement, and it matches `pull_request_list`'s own
documented choice to answer `[]` for both "none found" and "never asked".

**The delayed merge works too, for free.** `db.list_pull_request_candidates` already refreshes a
`done` item whose stored pull request is still `open` — a clause written for a different reason
in #143. So an issue closed by hand and merged an hour later is retired on the pass after the
merge, not left to the 30-minute clock. That also means the backlog currently on the machine
needs no backfill: those items are `done` with merged sets already recorded.

**Two comments are corrected in the same commit as the code.** The claim that retire-before-sweep
makes "no anomaly on the successful path" free was necessary but never sufficient, and
`RETIRE_IDLE_SECONDS`'s "erring long is nearly free" is false on the merged path — the cost
there was an anomaly on every successful item, which is the thing #138 was filed about. Shipped
reasoning that argues for behaviour the code no longer has is worse than no comment (FR-012,
D6).

## Complexity Tracking

No constitutional violation requires justification. Two decisions sit close enough to a
principle that leaving them unargued would itself be the violation:

| Decision | Why | Simpler alternative rejected because |
|---|---|---|
| `has_merged_pull_request` as a property in `models.py` with one caller | It is a fact about the item derivable from the item's own column, and it must be named somewhere. `models.py` already carries this exact shape — `cleanup_pending` is a derived predicate with the same profile — and the "three states, not two" docstring that explains what `[]` means is three lines above it | A private helper in `reconcile.py` separates the predicate from the property and the docstring it depends on. Inlining the comprehension at the call site puts the "never looked up reads as no" rule in a loop body where no test can name it |
| No floor on the merged path | R4: the measured idle time when the item went `done` was 47 seconds, so any non-zero floor declines on the pass that matters and the anomaly is raised regardless. FR-011 asks for never-raised, not briefly-raised | A 60-second floor — the value #149 floated — reproduces the reported bug exactly. A floor below 47 seconds is not a number anyone would defend. The case it was meant to cover (merging while still reading the session) is already covered by retirement destroying nothing: the transcript survives and `claude --resume` returns |

## Post-design Constitution re-check

Re-read after Phase 1. **Still PASS**, and the design shrank during research rather than
growing: Phase 0 started expecting to need a freshness rule for the stored column and found that
`list_pull_request_candidates`'s existing second clause already provides one, and expected to
need work for the tab and the worktree and found both already gated on the session row.

The scope of the change is one property, one helper, one audit key and two comment corrections.
The documented Principle III gap is unchanged and remains the single named case it was. No
schema change means no interruption window is added, so Principle IV's answer is inherited
rather than re-derived.
