# Implementation Plan: Retire a finished item's session

**Branch**: `speckit/20260905-121903-retire-finished-sessions` | **Date**: 2026-09-05 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260905-121903-retire-finished-sessions/spec.md`

## Summary

The successful path has no ending. A worker finishes, opens the pull request, and idles; the issue
closes; the item goes `done`; and the still-live worker is reported as `orphan_session` while its
row goes on holding a capacity slot and blocking the worktree from ever being reclaimed. Nothing in
the system owns the moment when the work has been accepted and the session should stop.

This adds that moment. A new reconciliation sweep, `_retire_finished_sessions`, terminates the
worker of a `done` item once the worker has been idle long enough, then closes the row through the
**existing** decision helper so the slot is released. Two smaller repairs go with it: the by-hand
stop path settles correctly for an item in a terminal state instead of reporting an ending that
never happened, and an `orphan_session` anomaly whose process is gone resolves itself.

Four research findings shape the whole design, and each removed work rather than adding it:

1. **`reconcile._resolve_closed_issues` is the only writer of `WorkItemState.DONE` in the
   codebase.** So "`done`" already *means* "the issue was observed closed". No new column, no
   second API call, no matching on a transition's reason string. It becomes an invariant a test
   must pin.
2. **The session registry already carries `status` and `statusUpdatedAt`.** Both finished sessions
   on the machine read `"status": "idle"` right now, and item 54's `statusUpdatedAt` of
   `1788616076697` lands 13 **milliseconds** from the last record in that session's transcript.
   That is an idle clock accurate to the second, already inside the file the daemon parses every
   pass. (Item 45's runs 3 minutes early against its transcript, which R2 measures and dismisses:
   3 minutes of slack against a 1800-second threshold changes no decision.)
3. **Retirement needs no new state transition.** `reconcile.reclaim_stale_session` already decides
   what an open row under a non-session-bearing item is, and already takes the safe branch when the
   process survives. Retirement terminates, then calls it with a reason naming retirement.
4. **`boundaries.dtach.SessionHost.terminate` is already the confirmed, audited, pid-identity-guarded
   path.** Retirement is a new *caller*, not a new mechanism.

## Technical Context

**Language/Version**: Python 3.11+, standard library first

**Primary Dependencies**: none added. `sqlite3`, `pathlib`, `json` from the standard library;
existing internal boundaries for the session host and `/proc`

**Storage**: SQLite at `~/.local/state/robot-army/state.db`. One migration, schema 11 → 12

**Testing**: `pytest`, `uv run pytest`. Existing `tests/conftest.py` fixtures — `seed_item`,
`seed_session`, `write_registry`, `write_proc` — cover everything this feature needs to fake

**Target Platform**: one Linux machine, one user

**Project Type**: single-process daemon plus CLI plus a read-mostly web interface

**Performance Goals**: the new sweep is bounded by the number of open session rows, which the
concurrency cap bounds in turn. No new network calls on any path. One extra `/proc` read per
unresolved `orphan_session` anomaly per pass

**Constraints**: reconciliation must never raise for an operational condition; every irreversible
act logged before it happens; writes atomic

**Scale/Scope**: three concurrent sessions by configuration, tens of work items, single-digit
anomalies

## Constitution Check

*GATE: passed before Phase 0 research; re-checked after Phase 1 design — see the bottom of this
file.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | **Passes, and the design got smaller during research.** No new configuration key (see the spec's assumption), no new anomaly kind, no new session state, no new state-machine edge, no new boundary, no new dependency. The quiet threshold is a module constant with one caller, following `TRANSCRIPT_GRACE_SECONDS`, whose docstring already argues the case. Three findings above each deleted a mechanism the naive design would have needed. |
| **II. Single-User, Local-First** | **Passes.** Everything is local: `/proc`, `~/.claude/sessions`, SQLite, a signal to a process on this machine. No network call is added anywhere. |
| **III. Total Accountability** | **Passes, with one documented gap.** Termination is logged before the signal by `SessionHost.terminate`'s own `audit.action` context. Retirement adds `session.retire` (intent, before) and its outcome; anomaly resolution adds `anomaly.resolved`. **The documented gap**: a worker that is not yet idle writes nothing at all — not a record, not a column. This is deliberate and is FR-004. A 60-second loop logging "still busy" for a session the maintainer is using would produce ~1,440 records a day carrying one bit, and the condition is re-derivable at any moment from the registry. It follows the precedent `_sweep_transcripts` set for exactly the same shape of decision. Enumerated here as Principle III requires. |
| **IV. Interruption Tolerance** | **Passes.** Every state change commits inside a transaction with its audit record. The two interruption windows are named and answered in research R7: a kill between the signal and the row transition leaves a dead process under an open row, which the existing `_sweep_stale_sessions` reclaims on the next pass; and a late exit record arriving after the row closed already settles quietly and is unlinked — planning predicted a bug there and implementation measured that there is none, so the change is a test rather than a fix (R7). No network call, so no timeout or retry policy is added. |
| **V. Public Code, Unsupported Project** | **Passes.** No credential is read, written or logged. The registry's `.key` files are not touched — the existing `FORBIDDEN_SUFFIX` guard and its test stand unchanged, and the new field is read from the same already-parsed `*.json` payload. No compatibility shim: the migration moves forward only. |
| **Operating Constraints** | **Passes.** Everything is reachable from the terminal: `robot-army reconcile` drives retirement, `robot-army cancel` is the by-hand path, `robot-army anomalies` shows the result. Retirement is irreversible in the sense that a process cannot be un-killed, so it is logged before execution — but it is **not** "irreversible or outward-facing" in the sense that requires explicit configuration: it destroys no data. The transcript survives in full and the session stays resumable, which is precisely the distinction between this and `cleanup.on_issue_close`. That argument is the one thing in the spec flagged for challenge, and it is restated in Complexity Tracking below. |
| **Development Workflow** | **Passes.** Unit tests for every changed unit. The migration, the state settling and the registry parse are persistence, state-machine and external-input-parsing code respectively, so each additionally carries failure- and interruption-path tests. The two questions the constitution demands are answered explicitly: **what does this log** — `session.retire`, `session.terminate` (existing), `anomaly.resolved`, plus two new counters on the existing `reconcile.pass` record; **what happens if it is killed halfway** — research R7. |

**Result: PASS.** No violation to justify. One documented Principle III gap, enumerated above.

## Project Structure

### Documentation (this feature)

```text
specs/20260905-121903-retire-finished-sessions/
├── plan.md              # This file
├── spec.md
├── research.md          # Phase 0 — nine findings, all measured
├── data-model.md        # Phase 1 — the one migration and the one parsed field
├── quickstart.md        # Phase 1 — four scenarios, two runnable on the machine as it stands
├── contracts/
│   ├── session-retirement.md
│   └── anomaly-resolution.md
└── tasks.md             # /speckit-tasks output — not created here
```

### Source Code (repository root)

```text
src/robot_army/
├── reconcile.py       # + RETIRE_IDLE_SECONDS, _retire_finished_sessions,
│                      #   _resolve_orphan_anomalies; two counters on ReconcileResult;
│                      #   two new calls in reconcile(), placed as C1 and C4 require
├── sessions.py        # RegistryEntry gains status_updated_at + an idle_for() helper;
│                      #   the "never used for control decisions" comment is rewritten
├── migrations.py      # migration 12: anomalies.resolved_at + the partial index rebuilt
├── db.py              # resolve_anomaly(); list_anomalies() excludes resolved rows
├── models.py          # Anomaly gains resolved_at
├── spool.py           # UNCHANGED — the late-record path already worked (R7); tests only
├── operations.py      # cancel() settles a terminal item's session honestly
└── web/pages.py       # unchanged — it calls db.list_anomalies, which now filters

tests/unit/
├── test_session_retirement.py     # new — the decision table and the sweep
├── test_anomaly_resolution.py     # new — the re-check, the index, the migration
├── test_slot_reclamation.py       # extended — retirement's interaction with #28's sweep
├── test_spool.py                  # extended — the late-record path
└── test_cancel.py                 # extended — cancel under a terminal item

docs/guide/
├── 5-outcome.md       # the session's ending — a new section before "Cleaning up"
├── operating.md       # the orphan_session entry, and that anomalies now resolve
├── state.md           # the anomalies table's new column
└── audit-log.md       # session.retire, anomaly.resolved, the two reconcile counters
```

**Structure Decision**: no new module. Retirement belongs in `reconcile.py` beside the four sweeps
it is ordered against, and splitting it out would put the ordering argument in one file and the
code it constrains in another. `sessions.py` gains one parsed field because that is where the
registry is parsed. `docs/guide/configuration.md` and `share/config.example.toml` are deliberately
**not** in the list: no configuration key changes, so neither the annotation step nor the
regeneration step in CLAUDE.md is triggered.

## Key design decisions

Full reasoning in [research.md](./research.md); the contracts are normative. The short version:

**Where the sweep runs.** After `_resolve_closed_issues`, before `_cleanup_worktrees`, and
therefore before `_sweep_stale_sessions`. All three halves are load-bearing. After the closed-issue
pass, because that pass is what produces the `done` items this sweep acts on, and running in the
same pass is what makes a merge take effect within one tick. Before cleanup, because a session
retired this pass frees its worktree for reclamation in the *same* pass rather than the next.
Before `_sweep_stale_sessions`, because that is what makes FR-009 free: a row this sweep has
already closed is `left` when #28's sweep reaches it, so the `orphan_session` that fires today is
never reached rather than being suppressed by a special case.

**How "idle" is decided.** `status == "idle"` in the session registry entry, and
`statusUpdatedAt` older than `RETIRE_IDLE_SECONDS` (1800). Any other status, a missing status, a
missing timestamp, an entry that is not there, a registry that could not be read — every one of
them means *not retired*. The unknown direction is always the safe direction, which is what lets
this depend on an undocumented file at all. The existing `KNOWN_VERSIONS` gate already refuses a
registry whose shape we have not seen.

**What retirement does not do.** It does not touch the work item's state, the worktree, the branch,
the transcript, or the anomaly table. It ends a process and closes a row.

**Why 1800 seconds.** Measured: the two finished sessions on this machine had been idle 84 and
198 minutes when this was written. 30 minutes is far longer than any pause inside an agent's turn, and
being wrong costs almost nothing in the one direction that matters — a session ended while the
maintainer was reading it is fully recoverable with `claude --resume`, because the transcript is
untouched. Erring long costs only a slot that comes back later. A constant, not a key, for the same
reason `TRANSCRIPT_GRACE_SECONDS` is one: one caller, no second use in hand.

## Complexity Tracking

No constitutional violation requires justification. Two decisions sit close enough to a principle
that leaving them unargued would be the violation:

| Decision | Why | Simpler alternative rejected because |
|---|---|---|
| Reading `status` / `statusUpdatedAt` from the registry, reversing a comment that says `status` is "displayed and never used for control decisions" | It is the only precise idle clock available, it is already in a file the daemon parses every pass, and it was verified against the transcript to the millisecond (R2) | Transcript mtime was tried first and **measured wrong**: for both live sessions the file's mtime ran 29 and 163 minutes *ahead* of the last record inside it, so it reports activity that did not happen. Parsing the transcript's last record is exact but means seeking inside a 2 MB JSONL file every pass to learn something the registry states outright |
| No configuration key | Retirement destroys nothing — the transcript survives and the session stays resumable — so the reasoning that keeps `cleanup.on_issue_close` off by default does not transfer. A key here would have exactly one caller and no second use, which Principle I names directly | A default-off key would leave #138 unfixed on this machine's configuration, which is the whole point of the issue |

## Post-design Constitution re-check

Re-read after Phase 1. **Still PASS**, and one item moved in the right direction: the design as
built adds no state-machine edge, no anomaly kind, no boundary and no dependency, and the only
schema change is a single nullable column plus the rebuild of one partial index. The documented
Principle III gap is unchanged and remains a single, named case (FR-004). The `.key` prohibition in
`sessions.py` is untouched, and the test asserting no code path opens one still holds because the
new field is read from the same parsed `*.json` payload as every existing field.
