# Implementation Plan: Refuse to Signal an Unverified PID During Termination

**Branch**: `20260831-184927-guard-terminate-pid` | **Date**: 2026-08-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260831-184927-guard-terminate-pid/spec.md`

## Summary

`_signal_group` in `boundaries/dtach.py:354` takes the pid off the session row and signals its
process group. It holds no opinion about what a valid pid is. A recorded pid of `1` resolves to
process group `1`, and `killpg(1, sig)` is `kill(-1, sig)` — every process the caller may signal.
On 2026-08-31 that ended the maintainer's desktop session, took robot-army's own daemon and web
service with it one second in, and then reported the cancel as confirmed, because the only thing
confirmation asks is whether the recorded pid is gone. It was.

The fix is three guards over data the system already records, plus one routing correction:

1. **Reject impossible pids on sight** — `None`, `0`, `1` — before any rung runs.
2. **Reject an impossible process group** — `pgid <= 1`.
3. **Refuse to signal anything unidentified** — a recorded pid with no recorded `proc_start` is a
   bare number, and `procinfo.is_alive`'s documented degradation to a bare existence check must not
   apply here. This is what let pid `1` through: `is_alive(1, None)` is `True` (measured).
4. **Route a simulated session record to the simulated host** regardless of the configured effect
   level, closing the one path — dispatch at `local`, raise the level, cancel — that reaches this
   code through ordinary operation with no hand-edited database.

None of these three checks is redundant with the others, and research R1 shows why each candidate
one-liner fails alone: `pgid <= 1` misses pid `0` (whose `getpgid(0)` is the *caller's* group,
measured as `1743559`), and identity validation misses pid `1` (whose `/proc/1` start time is a
real value, measured as `17`).

Refusal is a returned `TerminationOutcome`, not an exception — `method="refused"`, one new field
`refused_reason` — so `cancel` and the web action path get structure rather than a string, and
014's K1 ("change no state unless `confirmed`") already forbids settling on it. `_signal_group`
itself additionally **raises** if ever reached with a rejected value: unreachable when `terminate`
is correct, and the point is that it stays unreachable when someone later changes `terminate`.

No schema migration. No new dependency. Two new fields, one new module-level guard function, one
new test module, and a contract that says a recorded pid is not evidence of a process.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`).

**Primary Dependencies**: none added. `httpx` remains the sole runtime dependency; everything here
is stdlib (`os`, `signal`) plus existing internal modules (`procinfo`, `audit`, `effects`).

**Storage**: SQLite via `db.py`. **No migration.** `sessions.pid`, `sessions.proc_start` and
`sessions.dry_run` already exist (`migrations.py:60-78`) and are already populated by ordinary
operation (`dispatch.py:920-927`, `db.insert_session`).

**Testing**: pytest. One new module `tests/unit/test_signal_refusal.py` targeting `_signal_group`
directly with spied `os.killpg`/`os.getpgid`; extensions to `tests/unit/test_terminate_confirmation.py`
(the refusal cases of the ladder, off the existing fixture `/proc` harness),
`tests/unit/test_cancel.py` (message, exit code, unchanged state) and `tests/unit/test_effects.py`
(the wiring identity). **No test may deliver a real signal to a real process.**

**Target Platform**: single Linux machine, user systemd session, kitty + dtach.

**Project Type**: single-project CLI/daemon with a small local web interface.

**Performance Goals**: not a throughput feature. The bound that matters is that a refusal returns
immediately rather than spending the ten-second SIGTERM→SIGKILL escalation a delivered signal costs
(SC-002).

**Constraints**: no legitimate cancel may become a refusal (SC-005, research R6); `already_gone`
must stay distinct from `refused` (FR-004); the scope rung's behaviour is untouched — #67 stays
open and this feature must not appear to close it.

**Scale/Scope**: one cancel at a time, on demand. Three production files carry the change
(`boundaries/dtach.py`, `boundaries/__init__.py`, `effects.py`), plus `operations.py` for the
message, the `SessionHost` protocol docstring, the test stub, and two contract documents.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 — see "Post-design re-check" below.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | Passes with one item recorded in Complexity Tracking. The guards are three comparisons and a dictionary of reasons, not an abstraction: no policy object, no configurable pid allowlist, no injection seam beyond what the test needs. `refused_reason` is a sentence rather than an enum because there are three producers, all in one function, and a code would have to be translated back into that sentence at both ends. The one addition that needs justifying is `Boundaries.simulated_session_host` (below). |
| **II. Single-User, Local-First** | Passes. `/proc` and the local session; no network, no new state, no new path, no new configuration. The operating-system user remains the trust boundary — which is precisely what the incident violated in the other direction, by acting on everything inside it. |
| **III. Total Accountability** | Passes. Every refusal is written to the existing `session.terminate` intent/outcome pair, carrying the session, the rejected field and value, the reason, and `signals_sent: 0`. No new action name is introduced, because a reader asking "what happened when I cancelled item 29" searches for the termination action. Nothing is swallowed: a refusal is a returned outcome the caller reports and exits non-zero on, and the primitive's raise propagates. **No Principle III exception is claimed.** |
| **IV. Interruption Tolerance** | Passes trivially, and the reasoning is in R10. A refusal writes nothing — no signal, no transition, no file — so there is no half-refused state to reach. The intent record is flushed before the check, so a kill between intent and outcome leaves the crash signature Principle IV asks for, with the item still `ACTIVE` and therefore still visited by reconciliation's active sweep. |
| **V. Public Code, Unsupported Project** | Passes. `TerminationOutcome` and `Boundaries` gain fields with no compatibility shim, which is explicitly permitted; both contracts are amended in the same change. |

### What this logs (required by Development Workflow)

- **`session.terminate`** — unchanged action, unchanged intent (`scope`, `pid`, `proc_start`). Its
  outcome gains `refused: true`, `refused_reason: "<sentence>"`, and `signals_sent: 0`. On the
  ordinary path the record is byte-for-byte what it is today.
- **`state.session` / `state.work_item`** — **absent** on a refusal, and that absence is the
  evidence that nothing was settled. Present and unchanged on a confirmed stop.
- **`daemon.start`** (startup, its `boundaries` field via `Boundaries.describe()`, `daemon.py:505-511`) — gains
  `simulated_session_host`, so the startup record still names every wired implementation rather
  than every implementation *except* the one that can be selected later.
- **`systemctl.stop`** — not emitted on a refusal, because the scope rung does not run
  (contract S5).

Nothing this feature adds goes unlogged.

### What happens if it is killed halfway (required by Development Workflow)

- **Killed during the guard**: nothing has been attempted and nothing has been written except the
  flushed intent. The item is still `ACTIVE`; reconciliation's session sweep visits it on the next
  tick and settles it against `/proc`, the ordinary recovery path.
- **Killed after the guard, on the ordinary path**: identical to today. Milestone 014's analysis
  applies unchanged — this feature adds no new write and no new window.
- **Killed during startup wiring**: `wire()` constructs objects and holds no state; the next start
  wires again from the same configuration.

There is no state this feature can leave half-written, because on the path it adds it writes
nothing at all.

## Project Structure

### Documentation (this feature)

```text
specs/20260831-184927-guard-terminate-pid/
├── plan.md                       # This file
├── research.md                   # Phase 0 — R1..R11, all measured
├── data-model.md                 # Phase 1 — no migration; two new fields
├── quickstart.md                 # Phase 1 — how to prove it WITHOUT reproducing the incident
├── contracts/
│   └── signal-refusal.md         # Phase 1 — S1..S9, S-C1..S-C10, S-K1..S-K4
├── checklists/
│   └── requirements.md
├── spec.md
└── tasks.md                      # /speckit-tasks output — not created here
```

### Source Code (repository root)

```text
src/robot_army/
├── boundaries/
│   ├── __init__.py               # TerminationOutcome gains refused_reason;
│   │                             #   SessionHost.terminate docstring records S1-S3
│   └── dtach.py                  # THE CHANGE: _refuse_reason() guard; terminate() checks
│                                 #   before any rung; _signal_group() re-validates and raises
├── effects.py                    # Boundaries.simulated_session_host + wire() + describe()
└── operations.py                 # cancel(): host selection by record; the refusal message

tests/
├── conftest.py                   # StubSessionHost.terminate accepts/returns refused outcomes
└── unit/
    ├── test_signal_refusal.py    # NEW — _signal_group directly, os.killpg spied, zero calls
    ├── test_terminate_confirmation.py  # extended — S-C1..S-C8 on the ladder
    ├── test_cancel.py            # extended — S-K1..S-K4: message, exit code, unchanged state
    └── test_effects.py           # extended — wiring identity, describe() completeness
```

**Structure Decision**: unchanged single-project layout. The feature touches the boundary that owns
process termination (`boundaries/dtach.py`), the protocol and outcome type it reports through
(`boundaries/__init__.py`), the wiring that selects it (`effects.py`), and its one caller
(`operations.py`). Nothing new is created outside `tests/`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| `Boundaries.simulated_session_host` — a second field for one boundary, making session-host selection depend on the session **record** as well as the configured effect level, where every other boundary is chosen by level alone (`REAL_AT`) | FR-011. A session created as simulated stays simulated for its whole life no matter what the configuration later becomes, so the level alone cannot answer "which host owns this row". Without it, the ordinary go-live sequence — dispatch at `local`, raise the level, cancel — hands a `pid=0` row to the real host; the guards make that harmless, but "harmless refusal" is the wrong answer for a session that should simply stop cleanly (FR-013). | **Constructing the simulated host inline in `cancel`** hides a boundary selection from `Boundaries.describe()`, so the startup record would stop naming every implementation in play — a Principle III regression traded for a Principle I saving. **Branching inside `DtachHost.terminate` on `HostHandle.simulated`** makes the real host responsible for simulating, the exact divergence `contracts/boundaries.md` forbids. **Making `REAL_AT` record-aware** turns the table from data back into branches, which is the thing its comment says it exists to avoid. |

One field, one branch, one caller, and the `describe()` entry that keeps the startup record honest.
The tension is real and is recorded here rather than left for a reader to find.

## Post-design re-check (after Phase 1)

Re-run against the completed artifacts:

- **I. Simplicity** — the design shrank during Phase 0. `refused_reason` replaced a candidate
  `RefusalKind` enum (data-model), and the injectable-signal seam that Scenario 1 first seemed to
  need turned out to be unnecessary: `monkeypatch` on `robot_army.boundaries.dtach.os` reaches the
  primitives without a production-side hook (research R8). Nothing was added that has no present
  caller. **Still passes**, with the one entry above.
- **II. Single-user** — unchanged; no new surface.
- **III. Accountability** — Phase 1 added `signals_sent: 0` to the outcome detail, because "we
  refused" and "we refused and sent nothing" are the same claim only if the reader trusts the code,
  and this whole feature exists because that trust was misplaced once. **Still passes.**
- **IV. Interruption** — unchanged; the refusal path writes nothing.
- **V. Public code** — two contracts amended in the same change as the code they govern.
  **Still passes.**

**Gate: PASSED.** No unresolved `NEEDS CLARIFICATION`; the spec's one open question was answered
before planning began (option B, now FR-011–FR-013).
