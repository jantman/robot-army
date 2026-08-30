# Phase 0 Research: A Stop That Is Confirmed, Not Assumed

**Feature**: `specs/014-confirm-session-termination` | **Date**: 2026-08-30

All findings below were established by reading this repository, not by external research.
Every mechanism the fix needs already exists here; the defect is that termination does not
use them.

---

## R1 — What "confirmed gone" is observed against

**Decision**: `procinfo.is_alive(session.pid, session.proc_start)` — the recorded pid checked
together with the recorded `/proc/<pid>/stat` field 22 start time.

**Rationale**: This is the project's existing FR-038 liveness primitive
(`src/robot_army/procinfo.py:is_alive`), already used by `RegistryEntry.alive` and by
reconciliation's session sweep. Both halves of the session's identity are already persisted:
`sessions.pid` and `sessions.proc_start` are columns (`migrations.py:67-68`), written at
confirmation from the registry entry (`dispatch.py:925`). The pid recorded is the *worker*
process — in the issue's reproduction, pid 3029744, `claude`, cwd
`/home/jantman/worktrees/robot-army/issue-24` — which is exactly the thing that must be dead
for a cancel to mean anything.

Including `proc_start` is not optional decoration: it is the PID-reuse guard the spec's
FR-004 asks for. Without it, a recycled pid belonging to something unrelated reads as
"still alive" (and would be signalled), or a vanished-and-reused pid reads as "gone".

**Alternatives considered**:

- `SessionHost.is_alive(handle)` — the dtach socket probe. Rejected: it answers a different
  question. It tells you whether a dtach *master* is accepting connections, not whether the
  worker inside it is running, and stale sockets are a known condition here. It also cannot
  be identity-checked.
- `systemctl --user is-active <scope>` — rejected by the issue's own evidence: the scope
  reported `inactive` while the process was still in its cgroup and still accumulating CPU.
  That is the failure, not the detector.
- Reading `/proc/<pid>/cgroup` to see whether the process left the scope — more work, more
  parsing, and it answers a weaker question than "is this process still there".

---

## R2 — The escalation ladder, and where confirmation sits in it

**Decision**: `terminate` becomes a confirm-after-every-rung loop:

1. If a scope is recorded: `systemctl --user stop <scope>`, then **confirm**.
2. If still alive (or no scope): `_signal_group` — SIGTERM to the process group, poll, then
   SIGKILL — then **confirm**.
3. Report the settled outcome. Never return on the strength of an exit status.

The existing fallback code is correct as written and is kept; the change is that it becomes
reachable, and that a confirmation follows each rung instead of an exit-status check.

**Rationale**: `boundaries/dtach.py:159-193` today does `if result.ok: return`. That single
line is the defect. `systemctl --user stop` exits 0 for a unit that is already inactive,
which is precisely the state the scope is in when the kitty window has been closed but the
worker inside it survived — so the common case takes the "success" branch and the correct
fallback is never reached.

**Bound**: confirmation polls for at most `TERMINATE_CONFIRM_TIMEOUT = 5.0` seconds per rung
at `CONFIRM_POLL_INTERVAL`-scale intervals (0.1s), a module constant beside the existing
`PROBE_TIMEOUT` and `TERMINATE_TIMEOUT`. A configuration knob was rejected under Principle I:
one caller, no second use in hand. `_signal_group`'s own internal SIGTERM→SIGKILL deadline
(10s) is unchanged, which makes the worst case for a cancel roughly 5 + 15 + 10 + 5 seconds
and bounded at every step (SC-007).

**Alternatives considered**:

- Confirm only once, at the very end, after trying both rungs unconditionally. Rejected:
  signalling a process group we did not need to signal is a wider blast radius than FR-013
  wants, and the record would no longer say which rung actually did the work.
- A new, more forceful mechanism (`systemctl kill --signal=SIGKILL`, cgroup freezing).
  Rejected: the existing SIGTERM→SIGKILL fallback is sufficient once it is reachable, and
  the spec's assumptions rule out inventing a new killing mechanism.

---

## R3 — `terminate` returns an outcome instead of `None`

**Decision**: `SessionHost.terminate(handle, scope, *, proc_root=None) -> TerminationOutcome`,
a frozen dataclass in `boundaries/__init__.py` beside `HostHandle`:

```
TerminationOutcome:
    confirmed: bool          # the tracked process is observed gone
    method: str              # "systemd_scope" | "process_group_signal" | "already_gone" | "none"
    escalated: bool          # the first rung reported success and did not kill it
    detail: dict[str, Any]   # exits, signals, waits — what goes in the record
```

**Rationale**: `operations.cancel` cannot report truthfully (FR-010) or refuse to change state
(FR-006, FR-007) on a return value of `None`. The alternative — raising `BoundaryError` on an
unconfirmed stop — loses the distinction between "the stop failed" and "the stop reported
success and the process is still there", which is the very distinction FR-002 and the record
must carry. Raising is kept for the genuinely impossible case (no scope *and* no pid), which
is what it already does.

`HostHandle` gains no `proc_start` field. `cancel` is the only caller that has a recorded
start time, and it already constructs the handle itself; the expected start is passed as a
keyword argument to `terminate` alongside `proc_root`. Adding a field to a frozen dataclass
that five other construction sites would leave `None` is the speculative shape Principle I
rejects.

**Contract impact**: this changes the `SessionHost` protocol, so
`specs/001-minimum-daemon/contracts/boundaries.md` must be amended in the same change (see
R8), and `SimulatedSessionHost` plus `tests/conftest.py:StubSessionHost` must return the same
shape (R6).

---

## R4 — The session record after a confirmed cancel

**Decision**: transition the session to `SessionState.LOST` with the reason
`stopped by cancel (<method>); process confirmed gone`, and only after re-reading the row
(R5).

**Rationale**: `LOST` is legal from both states a cancel can act on — `STARTING → LOST` and
`RUNNING → LOST` are in `SESSION_TRANSITIONS` (`states.py:74-81`), while `STARTING →
EXITED_*` is not. Its established meaning in this codebase is "ended, with no exit record to
say how", which is exactly what a killed session is; reconciliation writes it for the same
observation (`reconcile.py:172`). The transition reason carries the fact that this was a
deliberate cancel, so nothing is lost from the record.

**Alternatives considered**:

- A new `SessionState.TERMINATED`. Rejected under Principle I: it touches the enum, the
  transition table, `TERMINAL_SESSION_STATES`, `_SESSION_STAMP_COLUMN`, `spool._already_applied`,
  `spool.py:126`, `reconcile.py:158` and the display paths — eight edits to encode a
  distinction the transition reason already states in words, on a session row the maintainer
  reads next to a work item whose own reason says "cancelled by the maintainer".
- Synthesising `EXITED_ERROR` with `exit_code=143`. Rejected outright: fabricating an exit
  code the system never observed is the same class of lie this feature exists to remove.

**Known consequence, accepted**: the wrapper (`share/robot-army-session-wrapper.sh`) has no
signal trap, but if bash survives its child it emits an `exit` record with `signal: 15`.
Once the session row is terminal, `spool._already_applied` classifies that record as a
duplicate and drops it, so the decoded signal never lands in the row. The cancel's own audit
record carries which signal was sent, so the fact is still reconstructable from the log —
which is the standard Principle III sets.

---

## R5 — The cross-process race, already solved once

**Decision**: before settling anything, re-read the session row and the work item. If the
session is already in `TERMINAL_SESSION_STATES`, or the item is no longer `ACTIVE`, treat the
cancel as the "already gone" success and skip the transitions rather than forcing them.

**Rationale**: this is the same race milestone 013 fixed on the launch side, in the same
shape and for the same reason: the daemon drains the exit spool in its own process while a
CLI or web `cancel` runs in another. A worker that dies from our own SIGTERM can have its
exit record applied before we reach the settle. Forcing the transition then raises
`IllegalTransition`, which the state gate is right to raise and which would surface to the
maintainer as a failure of a cancel that in fact succeeded perfectly.

`dispatch.py:852-861` is the precedent to follow verbatim, comment and all:

> Ask the session what it knows before concluding anything about it. The exit spool is
> drained by the daemon, in its own process, while this call was waiting.

**Alternatives considered**: catching `IllegalTransition` and swallowing it. Rejected —
Principle III forbids the swallowed exception, and "check, then act, and say which branch you
took" is both cheaper and honest.

---

## R6 — Simulated and stub hosts

**Decision**: `SimulatedSessionHost.terminate` returns
`TerminationOutcome(confirmed=True, method="simulated", escalated=False)` after discarding the
socket from its `_alive` set, and performs no `/proc` observation.
`tests/conftest.py:StubSessionHost.terminate` does the same, and gains a switch for tests that
need the unconfirmed outcome.

**Rationale**: FR-014, and `contracts/boundaries.md`'s existing rule that the simulated path
must take the same branch as the real one. A simulated session has no process; polling `/proc`
for its pid (which is `0` by construction, `dtach.py:286`) would send every simulated cancel
down the failure branch — the exact divergence `confirm_session` documents at
`dtach.py:266-277` and refuses to allow.

---

## R7 — Testing seams

**Decision**: `terminate` takes `proc_root: Path | None = None` and `sleep`/`clock` injection
in the same style `confirm_session` already uses, and confirmation is factored into a small
module-level helper so the "reported success, still alive" case can be driven from a fixture
`/proc` tree without spawning real processes.

**Rationale**: `procinfo` already takes `root=` for exactly this (`research.md R20` of 001),
and `tests/unit/test_procinfo.py` establishes the fixture-tree pattern. The regression test
FR-016 asks for — a scope stop that returns 0 while the pid is still present — is then a pure
unit test with no real process and no systemd.

Test placement: a new `tests/unit/test_terminate_confirmation.py` for the boundary ladder, and
cancel's reporting and state behaviour in `tests/unit/test_web_actions.py` (web surface) plus
a new `tests/unit/test_cancel.py` (terminal surface, all four outcomes). Integration coverage
of the settle-race goes in `tests/integration/test_spool_recovery.py`, which already owns the
spool/daemon interleaving fixtures.

---

## R8 — Where the general rule is written down (FR-015)

**Decision**: `specs/001-minimum-daemon/contracts/boundaries.md`, as a rule in the preamble
covering every boundary operation, plus the amended `SessionHost` section.

**Rationale**: the repository root has no agent guidance file (only `README.md`), and the
constitution points at that file only "when one is present". `contracts/boundaries.md` is
where a maintainer adding an outward-facing operation is already reading — it is the document
that lists each boundary's methods and their contract notes — so the rule sits where it will
be seen at the moment it applies. The rule states the pair the project has now learned twice:

> An outward-facing call's exit status is not evidence of its effect. `kitty @ launch` returns
> 0 and a valid window id for a session that never started (M0 F16); `systemctl --user stop`
> returns 0 for a unit that is already inactive while a process still runs in its cgroup
> (issue #34). Any boundary operation whose effect is observable MUST confirm the effect
> independently before reporting it.

Creating a new root-level guidance file was rejected: one rule does not justify a document,
and Principle V rules out documentation written for anyone but the author.

---

## R9 — Cancel's failure exit code and message

**Decision**: `EXIT_FAILED` (the code `cancel` already returns for a `BoundaryError`), with a
message naming the session id, the surviving pid, and the attach command from
`SessionHost.attach_command`.

**Rationale**: `EXIT_PRECONDITION` means "you asked for something that is not allowed here";
this is an attempted action that did not take effect, which is `EXIT_FAILED`. The web surface
needs no change to display it: `web/server.py:_report` already raises `Refusal` for any
non-`EXIT_OK` result, which is what makes FR-012 mostly a test rather than a code change.

---

## Unknowns remaining

None. No `NEEDS CLARIFICATION` markers were carried in from the specification, and every
mechanism this plan depends on — `procinfo.is_alive`, `sessions.proc_start`, the transition
gate, the spool's terminal-state idempotency, the effect-level wiring — is present and in use
today.
