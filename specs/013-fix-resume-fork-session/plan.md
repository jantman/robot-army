# Implementation Plan: `resume` That Actually Resumes, and a Failure That Actually Fails

**Branch**: `013-fix-resume-fork-session` | **Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/013-fix-resume-fork-session/spec.md`

## Summary

Three changes, in descending order of size, none of them large.

1. **`--fork-session`.** `build_launch_plan` composes a restoring launch the worker rejects
   outright, so `resume` has never once worked. Appending the one flag the binary's own error
   message names fixes it. Verified against the real binary end to end: the combination is
   accepted, prior context is restored, and — the part that could have failed silently — the
   forked session runs under the id **we** chose, so everything that tracks it keeps working
   ([research.md](./research.md) R1, R2).

2. **Ask the session before declaring it lost.** When the confirmation window elapses,
   `dispatch_item` transitions the session to `lost` on the assumption it is still pre-exit. A
   worker that dies fast has already recorded its own exit from the daemon's process, the state
   gate refuses the contradiction, and the exception escapes — so the work item is never failed
   and sits in `dispatching` until a 15-minute reaper clears it. The fix is to re-read the
   session row at that moment and treat a recorded terminal state as the answer. `reconcile.py`
   already does exactly this, with a comment explaining why; the pattern is being applied to the
   site that missed it, not invented (R3).

3. **A real-binary check.** Every automated test passed on the broken launch, because the
   simulated host never executes the argv it is handed. A parametrised check hands each shape
   `build_launch_plan` can compose to the actual binary with `-p` and empty stdin — ~0.9s per
   shape, no model call — and asserts it gets past argument validation (R5).

Nothing else changes. No schema change, no migration, no new dependency, no change to
`SESSION_TRANSITIONS`, and the non-restoring launch is unchanged byte for byte.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added. `httpx` remains the sole runtime dependency and is not
involved here.

**Storage**: SQLite (`work_items`, `sessions`), the JSONL audit log, and the exit spool —
**all unchanged**. No schema change, no migration.

**Testing**: pytest. New unit coverage for argv composition; new integration coverage for the
confirmation race using the existing simulated host; one new `requires_worker` marker for the
real-binary check, following the existing `requires_git` precedent.

**Target Platform**: single Linux machine with a shell.

**Project Type**: single Python package (`src/robot_army`) — CLI plus daemon plus a small web
interface.

**Performance Goals**: time-to-truth on a failed launch drops from the 900s reaper to inside the
45s confirmation window. The real-binary check costs ~20s (24 shapes × ~0.9s) and skips when the
binary is absent.

**Constraints**: the confirmation-branch read of session state must cross a process boundary —
the daemon writes it, the web worker thread reads it — so it must be a fresh read at that moment,
not a value carried from before the launch.

**Scale/Scope**: three source edits in two files, one new test module, one new marker. Roughly
thirty lines of production code.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 — see below.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | PASS. One appended flag; one guard copied from an existing site; one test. No abstraction, no configuration knob, no new dependency. Three tempting elaborations were rejected in research: teaching `confirm_session` to watch the database (R3), reading the spool from inside dispatch (R4), and a general harness for running real workers (R5). |
| **II. Single-User, Local-First** | PASS. No new state, no network, no service. The real-binary check runs against the binary already installed on this machine and skips when it is not. |
| **III. Total Accountability** | PASS, with the record **improved**. See "What this logs" below. Nothing is swallowed: the FR-008 safety net records the exception and re-raises it. |
| **IV. Interruption Tolerance** | PASS. Every transition stays inside its existing `db.transaction`. The feature's whole subject *is* an interruption path — a worker dying mid-launch — and the change makes that path settle where today it strands. See "What happens if it is killed halfway" below. |
| **V. Public Code, Unsupported** | PASS. No credentials, no personal data. The measured session ids in research are throwaway values from a scratch directory that was deleted. |

### What this logs (required by Development Workflow)

Everything it logged before, plus a genuine gap closed. Today a launch that hits this race
produces one `[error]` line naming an `IllegalTransition` and then **nothing** — no item
transition, no failure reason, no notification. After this change:

| Record | Meaning |
|---|---|
| `dispatch.unconfirmed` | Confirmation elapsed. Detail gains the session's state at that moment and which outcome was taken, so the log distinguishes "never appeared" from "already exited". |
| `state.session` | Only when a transition actually occurs — correctly absent in the already-terminal case, because nothing moved. |
| `state.work_item` | The item's move to `failed`, with a reason naming the exit status. |
| `dispatch.error` | New. An unexpected exception escaped the launch: recorded with detail, then re-raised. |

No new gap in the record is introduced, so Principle III's enumeration requirement has nothing to
enumerate. The real-binary check is test-time only and outside the runtime record by nature.

### What happens if it is killed halfway (required by Development Workflow)

- **Killed mid-launch, before the item settles**: the item stays in `dispatching` and the existing
  `dispatching_max_age_seconds` reaper resolves it — the pre-existing backstop, unchanged. FR-009
  keeps that backstop for exactly this case while forbidding it as the resolution for a failure
  already detected.
- **Killed after settling**: the transition and its audit record committed together, as they
  already do.
- **Exit record not yet drained when confirmation elapses**: `lost` stands; the later record is a
  documented no-op, since `spool._already_applied` treats a terminal session as applied. The item
  still fails, with the weaker reason. Accepted and recorded in R4.
- **A forked worker killed after starting**: identical to any other session. The fork is a normal
  session under an id we chose; nothing about its lifecycle is special.

## Project Structure

### Documentation (this feature)

```text
specs/013-fix-resume-fork-session/
├── plan.md                              # This file
├── spec.md
├── research.md                          # Phase 0 — measured findings R1–R7
├── data-model.md                        # Phase 1 — no schema change; the two behavioural rules
├── quickstart.md                        # Phase 1 — how to prove it, including by hand
├── contracts/
│   ├── worker-launch-shapes.md          # The composed argv ↔ worker binary contract
│   └── confirmation-outcome.md          # Confirmation elapsed → session and item outcomes
├── checklists/
│   └── requirements.md
└── tasks.md                             # Phase 2 — /speckit-tasks, NOT created here
```

### Source code (repository root)

```text
src/robot_army/
├── dispatch.py        # build_launch_plan: append --fork-session (Story 1)
│                      # dispatch_item: confirmation branch reads session state (Story 2)
│                      #                launch section settles the item on any exception (FR-008)
└── states.py          # UNCHANGED — the transition table stays exactly as strict

tests/
├── unit/
│   └── test_launch_shapes.py            # NEW. argv composition + the requires_worker probes
└── integration/
    └── test_dispatch.py                 # confirmation-race cases added to the existing module

pyproject.toml                           # one line: the requires_worker marker
```

**Structure Decision**: the existing single-package layout is unchanged. Both production edits
land in `dispatch.py`, which already owns launch composition and the confirmation branch. The new
test module is unit-level because argv composition is a pure function; the race needs the
simulated host and belongs with the dispatch integration tests that already exercise it.

## Implementation Notes

Detail that belongs to planning rather than to task breakdown.

### Story 1 — the flag

`build_launch_plan` (`dispatch.py:478`) already branches on `resume_session_id`. The flag is
appended inside that branch, so the non-restoring shape cannot move (FR-004). The existing
comment — "A resume is a *new attempt* restoring the prior session's context (FR-047)" — already
describes `--fork-session` precisely; it should be extended to say that the flag is what makes
that true, and that the combination without it is rejected outright.

### Story 2 — the confirmation branch

Inside `if entry is None`, re-read the session row and branch:

- **Terminal already** (`exited_error`, `exited_clean`, `lost`): do not transition. Build the
  failure reason from the recorded exit. Skip `_detect_session_id_mismatch` — with a recorded exit
  the question is answered, and probing would hunt for a rival session that cannot exist (contract
  C4). For `exited_clean`, leave the item to the ordinary end-of-session rules rather than failing
  it.
- **Not terminal**: today's path exactly, wording included.

The FR-008 net wraps the launch section: catch `Exception`, record `dispatch.error` with the
detail, settle the item via `_fail`, then **re-raise**. Re-raising is what keeps this from being
the "bare catch-all handler that continues" Principle III forbids — the caller
(`web._run_slow_action`) already logs and re-raises, so nothing is hidden and the item is settled
either way. `IllegalTransition` remains loud; it simply can no longer strand an item.

### Story 3 — the check

A `requires_worker` marker in `pyproject.toml` alongside `requires_git`, and
`skipif(shutil.which(binary) is None, …)` following the `test_spool_recovery.py` precedent. The
probe and its sentinels are specified in
[contracts/worker-launch-shapes.md](./contracts/worker-launch-shapes.md). The check must be
verifiable by breaking it on purpose — quickstart step 2 — because a check that cannot be made to
fail proves nothing, which is the whole lesson of this feature.

### Two things not to get wrong

- `operations.resume` accepts `interrupted` **and** `awaiting_review`. Story 1's framing talks
  about interrupted items; the guard must not be narrowed to match the prose (R7). FR-011 defers
  to today's behaviour.
- `SESSION_TRANSITIONS` must not gain an `EXITED_* → LOST` edge. That would make the
  contradiction legal instead of resolving it and would overwrite a known exit status with
  "lost" (R3).

## Post-Design Constitution Re-Check

Re-evaluated against the Phase 1 artifacts: **PASS, unchanged.**

The design added no entity, no column, no dependency, and no configuration. The one place it
could have drifted toward complexity — Story 3 — is bounded in writing by FR-013's ceiling, by
R5's rejected alternatives, and by a contract that fixes the probe and its sentinels. Principle
III's record is strictly better than before, since the current behaviour logs an exception and
then loses the item. No Complexity Tracking entries are required.

## Complexity Tracking

None. The Constitution Check passed with no violations.
