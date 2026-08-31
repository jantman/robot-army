# Contract: what may be signalled

**Amends** `specs/014-confirm-session-termination/contracts/termination-outcome.md` and
`specs/001-minimum-daemon/contracts/boundaries.md`. Where they disagree, this document wins for
the rules it states and leaves everything else untouched.

Milestone 014 established that **an exit status is not evidence of an effect**. This document adds
its converse, which the 2026-08-31 incident paid for:

> **A recorded pid is not evidence of a process.**
>
> A number in a database column is a claim, not an identity. Signalling on the strength of the
> claim alone is how `cancel 29 --force` ended every process its user owned and then reported
> success.

---

## Rules

**S1 — No signal without identity.** A signal may be delivered only to a pid that has been
positively identified as this session's process: the recorded pid **and** the recorded start time
from the same session row, matching a live `/proc` entry. A pid with no recorded start time is a
bare number and MUST NOT be signalled. Degrading to a bare existence check is forbidden here, even
though `procinfo.is_alive` permits it for liveness.

**S2 — Impossible pids are rejected on sight.** `None`, `0` and `1` are never session pids and MUST
be refused before any rung runs, independently of S1. This is not redundant with S1: `/proc/1` has a
real start time, so a row carrying pid `1` and a matching one would satisfy S1 (research R1).

**S3 — Impossible process groups are rejected too.** A process group resolving to `1` or lower MUST
NOT be signalled. `killpg(1, sig)` is `kill(-1, sig)`: every process the caller may signal.

**S4 — Refusal is a distinct outcome.** A refusal is
`TerminationOutcome(confirmed=False, method="refused", refused_reason=<sentence>)`. It is neither a
stop, nor an unconfirmed stop, nor `already_gone`. `refused_reason is not None` ⟺
`method == "refused"`, and `method == "refused"` ⇒ `confirmed is False` and `escalated is False`.

**S5 — A refusal delivers no signal and settles nothing.** Zero signals, zero state changes, zero
writes other than the record.

A refusal decided **up front** — S2's impossible pids, S1's missing identity — additionally attempts
*nothing at all*: the systemd scope rung MUST NOT run, because a row whose pid has just been judged
untrustworthy has no more trustworthy a scope.

A refusal discovered **at the signal rung** (S-C8, where only resolving the process group reveals
the problem) is by definition reached after the scope rung, and `detail["rungs"]` MUST show that it
ran. Reporting it as though nothing was attempted would be the same class of falsehood this whole
contract exists to prevent. The two are distinguishable in the record by exactly that: an empty
rung list versus a populated one.

**S6 — The guard is not the caller's job.** The checks live at the boundary, in
`SessionHost.terminate` and again in the signalling primitive beneath it, so that every present and
future caller inherits them (FR-008). A caller-side check is not a substitute.

**S7 — The primitive raises.** The function that calls `os.killpg` MUST re-validate its input pid
and the pgid it resolves, and MUST raise rather than signal if either is rejected. This is
unreachable when `terminate` is correct; it exists so that no future path can reach `os.killpg`
with a catastrophic argument. Belt and braces is the requirement, not an accident of style.

**S8 — Simulated sessions never reach the real host.** Termination of a session whose record says
it is simulated is handled by the simulated host regardless of the configured effect level, decided
from the session record and not from the configuration in force at cancel time (FR-011, FR-012).
Its outcome is the same confirmed simulated stop it would have produced before a go-live (FR-013).

**S9 — Nothing else narrows.** These rules govern *what may be signalled*. They do not change the
scope rung's blast radius, the confirmation window, or the meaning of `confirmed`. #67 remains
open.

---

## Cases every implementation must satisfy

Extends 014's C1–C10; those keep their numbers and their meanings.

| # | Recorded state | Signals delivered | `confirmed` | `method` | `refused_reason` |
|---|---|---|---|---|---|
| S-C1 | `pid=1`, no `proc_start` | **0** | `False` | `refused` | names pid `1` |
| S-C2 | `pid=1`, `proc_start` matches `/proc/1` | **0** | `False` | `refused` | names pid `1` |
| S-C3 | `pid=0`, not simulated | **0** | `False` | `refused` | names pid `0` |
| S-C4 | `pid=None`, no scope | **0** | `False` | — | *(unchanged: `BoundaryError`, 014 T7)* |
| S-C5 | `pid=None`, scope recorded | **0** | `False` | `none` | *(unchanged: 014 C8)* |
| S-C6 | live pid, `proc_start` absent | **0** | `False` | `refused` | names the missing start time |
| S-C7 | live pid, `proc_start` mismatches | **0** | `True` | `already_gone` | *(unchanged: 014 C5)* |
| S-C8 | live pid whose pgid resolves to `1` | **0** | `False` | `refused` | names the process group |
| S-C9 | ordinary live pid + matching `proc_start` | as today | as today | as today | `None` |
| S-C10 | session record marked simulated, real effect level | **0** | `True` | `simulated` | `None` |

`_signal_group` called directly with pid `0` or `1`, or with a pid whose group resolves to `1`,
raises `BoundaryError` and calls neither `os.killpg` nor anything else (S7).

---

## Caller obligations (`operations.cancel`)

Extends 014's K1–K5.

**S-K1** — A refusal changes no state. Already implied by 014 K1 (change nothing unless
`confirmed`); restated because a refusal is the first outcome where the *reason* not to settle is
"we declined to act" rather than "it survived", and the two must not be reported alike.

**S-K2** — A refusal exits non-zero and says which recorded value was rejected and why, naming the
session. The maintainer's next step is to inspect the session row, so the message must give them
the row and the field, not a generic failure.

**S-K3** — A refusal is not the unconfirmed-stop message. The existing wording —
`could not confirm session <id> stopped: pid <n> is still running after signalling the process
group` — is a false statement about a refusal: nothing was signalled and the pid's liveness was
never the question.

**S-K4** — The host is selected from the session record before the handle is built, and the same
host is used for the whole operation, including `attach_command` (S8).

---

## What this deliberately does not promise

Confirming that the recorded target died still says nothing about what else died with it. That is
the blind spot #67 names on the scope rung, and this contract narrows only the signal rung. A
future rule asserting that a pid belongs to this session's own process tree would close both from
the same direction; it is not in this feature (research R11).
