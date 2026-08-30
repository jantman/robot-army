# Implementation Plan: A Stop That Is Confirmed, Not Assumed

**Branch**: `issues/34` | **Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/014-confirm-session-termination/spec.md`

## Summary

`cancel` believes its own stop command. `boundaries/dtach.py:183` reads `if result.ok:
return` — and `systemctl --user stop` exits 0 for a unit that is already inactive, killing
nothing. The correct process-group fallback sits directly below, unreachable in exactly the
case that needs it. The maintainer is told the session stopped, the item is marked
`interrupted`, and the worker keeps running unsupervised in a state no sweep will ever visit.

The fix is to confirm the effect instead of trusting the exit status, using machinery this
repository already has: `procinfo.is_alive(pid, proc_start)` — the pid-plus-start-time guard
of FR-038 — against the `pid` and `proc_start` already recorded on every session row.
`terminate` becomes a confirm-after-every-rung ladder returning a `TerminationOutcome`;
`cancel` changes state only on `confirmed`, reports what was observed rather than what was
attempted, and exits non-zero when the session survives. The rule the project has now learned
twice — a launch's exit status is not evidence (FR-025, M0 F16), a stop's exit status is not
evidence (this issue) — is written into `contracts/boundaries.md` so the next boundary
operation inherits it.

No schema migration. No new dependency. One new dataclass, one contract amendment, and the
deletion of one `return`.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added. `httpx` remains the sole runtime dependency; everything
here is stdlib (`os`, `signal`, `pathlib`) plus existing internal modules.

**Storage**: SQLite via `db.py`. **No migration** — `sessions.pid` and `sessions.proc_start`
already exist (`migrations.py:67-68`) and are already populated at confirmation
(`dispatch.py:925`).

**Testing**: pytest. New `tests/unit/test_terminate_confirmation.py` (the ladder, driven off a
fixture `/proc` tree in the style of `tests/unit/test_procinfo.py`) and
`tests/unit/test_cancel.py` (reporting, state, exit codes). Existing
`tests/unit/test_web_actions.py` and `tests/integration/test_spool_recovery.py` extended.

**Target Platform**: single Linux machine, user systemd session, kitty + dtach.

**Project Type**: single-project CLI/daemon with a small local web interface.

**Performance Goals**: not a throughput feature. The bound that matters is latency: a cancel
settles — success or reported failure — within a bounded, documented time (SC-007). Worst case
is roughly 35 s across all rungs; the common case is unchanged from today plus one `/proc`
read.

**Constraints**: confirmation must not widen what is killed (FR-013); simulated effect levels
must take the same branch as a successful real stop (FR-014); nothing may be swallowed
(Principle III).

**Scale/Scope**: one session at a time, on demand. Two production files carry the change
(`boundaries/dtach.py`, `operations.py`), plus the protocol, the simulated host, the test stub,
and two contract documents.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | Passes. Nothing is added that has no present caller: one dataclass with one producer and one consumer, one module constant, one deleted `return`. The existing fallback is reused rather than replaced. A new `SessionState.TERMINATED` was considered and rejected (research R4) as eight edits for a distinction the transition reason already carries. A configuration knob for the confirmation bound was rejected for the same reason. |
| **II. Single-User, Local-First** | Passes. `/proc` and the user systemd session; no network, no new state, no new path. |
| **III. Total Accountability** | Passes, and is the point of the feature. The `session.terminate` record gains what each rung returned *and what was observed after it*, which is precisely the gap the issue found: today it records `systemctl.stop [ok] exit 0` and stops. Nothing is swallowed — an unconfirmed stop is a returned outcome that the caller reports and exits non-zero on, never a caught-and-continued exception. No new unlogged action is introduced, so no Principle III exception needs to be declared. |
| **IV. Interruption Tolerance** | Passes. Every rung and every confirmation is bounded (research R2). The state change happens in one transaction after confirmation, so a kill mid-cancel leaves either "nothing changed, worker may be dead" — which reconciliation's `ACTIVE` sweep still visits, because the item was not moved — or the settled state. The pre-existing spool race is handled by re-reading before settling (research R5), the pattern milestone 013 established. |
| **V. Public Code, Unsupported Project** | Passes. `SessionHost.terminate` changes signature with no compatibility shim, which is explicitly permitted; the contract document is amended in the same change. |

### What this logs (required by Development Workflow)

- `session.terminate` — one record per cancel, carrying `scope`, `pid`, `proc_start`, the
  per-rung list (`method`, `exit` or `signal`, `ok`, `alive_after`, `waited_s`), `escalated`,
  `confirmed`, and the outcome reported to the maintainer.
- `systemctl.stop` — unchanged, written by `subproc.run`. Its `exit: 0` is no longer the end
  of the story; the `alive_after` observation recorded beside it is.
- `state.session` — the `LOST` transition with reason `stopped by cancel (<method>); process
  confirmed gone`. **Absent** when the stop was not confirmed, which is itself the evidence
  that nothing was settled.
- `state.work_item` — the existing `ACTIVE → INTERRUPTED` record, unchanged in wording,
  emitted only after confirmation.

Nothing goes unlogged. No exception under Principle III is claimed.

### What happens if it is killed halfway (required by Development Workflow)

- **Killed during the scope stop or the group signal**: the signal was or was not delivered;
  either way no state changed, the item is still `ACTIVE`, and reconciliation's session sweep
  visits it on the next tick and settles it against `/proc` — the normal recovery path.
- **Killed between confirmation and the settle**: the worker is dead, the item is still
  `ACTIVE`, the session row still says `RUNNING`. Reconciliation observes no live process and
  no exit record and writes `LOST` + `interrupted`, which is the same destination by a
  different route.
- **Killed during the settle**: the transition is one `db.transaction`; SQLite rolls it back.
  The previous case applies.
- **The exit spool wins the race**: handled explicitly rather than by luck — the row is
  re-read before the settle (research R5, contract K2).

There is no half-settled state reachable, because the only write happens in a single
transaction after the only observation that authorises it.

## Project Structure

### Documentation (this feature)

```text
specs/014-confirm-session-termination/
├── plan.md                              # This file
├── research.md                          # Phase 0 — R1..R9
├── data-model.md                        # Phase 1 — no migration; TerminationOutcome
├── quickstart.md                        # Phase 1 — how to prove it against a real session
├── contracts/
│   └── termination-outcome.md           # Phase 1 — C1..C10, K1..K5
├── checklists/
│   └── requirements.md
├── spec.md
└── tasks.md                             # /speckit-tasks output — not created here
```

### Source code (repository root)

```text
src/robot_army/
├── boundaries/
│   ├── __init__.py       # + TerminationOutcome dataclass; SessionHost.terminate signature
│   └── dtach.py          # the ladder: confirm after every rung; delete `if result.ok: return`
│                         # SimulatedSessionHost.terminate returns a confirmed outcome
├── operations.py         # cancel(): settle only on confirmed; re-read before settling;
│                         #           report what was observed; EXIT_FAILED when it survives
└── procinfo.py           # unchanged — is_alive(pid, proc_start) is used as-is

share/robot-army-session-wrapper.sh      # unchanged
src/robot_army/web/server.py             # unchanged — _report already refuses non-OK results

tests/
├── unit/
│   ├── test_terminate_confirmation.py   # NEW — C1..C10 against a fixture /proc tree
│   ├── test_cancel.py                   # NEW — reporting, state, exit codes, K1..K5
│   └── test_web_actions.py              # + an unconfirmed cancel surfaces as a failure
├── integration/
│   └── test_spool_recovery.py           # + the exit record wins the race with a cancel
└── conftest.py                          # StubSessionHost.terminate returns an outcome

specs/001-minimum-daemon/contracts/boundaries.md   # the general rule + amended SessionHost
```

**Structure Decision**: unchanged from the existing single-project layout. The change is
confined to the session-host boundary and its one caller; no module is added and no module
moves.

## Implementation Notes

### Story 1 — the ladder (`boundaries/dtach.py`)

Replace the `if result.ok: return` early exit with: record the exit, observe
`procinfo.is_alive(pid, expected_start, root=proc_root)` under a 5 s bound, and only return if
the observation says gone. Otherwise mark `escalated`, record that the reported success was
contradicted, and fall through to `_signal_group`, which is kept as written — its internal
SIGTERM → poll → SIGKILL sequence is correct; it was simply unreachable.

Confirm again after `_signal_group` and build the `TerminationOutcome` from what was observed,
never from what was returned. Check "already gone" **before** the first rung, so a session that
died on its own is C5 rather than a pointless `systemctl` call.

### Story 1 — the caller (`operations.py:cancel`)

Order matters: terminate → check `confirmed` → *re-read* session and item → settle in one
transaction → report. An unconfirmed outcome returns `EXIT_FAILED` before any transition is
attempted, with the surviving pid and the attach command in the message. The `interrupted`
transition's reason wording does not change; only when it happens does.

### Story 2 — the report

The three shapes are pinned in [contracts/termination-outcome.md](./contracts/termination-outcome.md)
K3. The escalation line is worth writing out in full rather than collapsing into the success
line: "the scope reported success but the session was still running" is the sentence that
tells the maintainer this build caught the bug the issue describes.

### Story 3 — the rule

A short block in `contracts/boundaries.md`'s preamble naming both instances (M0 F16 for
launching, issue #34 for stopping), plus the amended `SessionHost` signature and contract
notes. Story 3 is P3 and separable: if it is dropped, Stories 1 and 2 still close the defect,
but the contract amendment for the changed signature is **not** optional — that belongs to
Story 1.

### Three things not to get wrong

1. **`proc_start` is not optional.** `is_alive(pid, None)` degrades to a bare existence check
   (`procinfo.py:120`), which is the pid-reuse bug in a different costume. Pass the recorded
   start time every time; a mismatch means *our* process is gone (C9), not that a stranger is
   alive.
2. **Do not confirm with the socket probe.** `SessionHost.is_alive(handle)` answers "is a
   dtach master accepting connections", not "is the worker running". They are different
   questions and this feature needs the second one.
3. **Do not force the settle.** Re-read first. The daemon drains the exit spool in its own
   process while the cancel runs, and a worker killed by our own SIGTERM can record its
   ending before we get there — the milestone 013 race, in a new place.

## Post-Design Constitution Re-Check

Re-evaluated after Phase 1. No principle moved from pass to fail, and the design got smaller
during Phase 0 rather than larger: `HostHandle` gained no field, no session state was added,
no configuration knob was introduced, and `web/server.py` turned out to need no change at all
because `_report` already refuses non-`EXIT_OK` results. The one deliberate complexity — a
structured return value where there was `None` — is what makes truthful reporting possible at
all, and it has exactly one producer and one consumer.

## Complexity Tracking

No Constitution Check violations. Nothing to justify.
