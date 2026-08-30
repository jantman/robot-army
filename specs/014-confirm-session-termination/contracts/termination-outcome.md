# Contract: `SessionHost.terminate` and its outcome

**Feature**: `specs/014-confirm-session-termination`

Amends the `SessionHost` section of
[`specs/001-minimum-daemon/contracts/boundaries.md`](../../001-minimum-daemon/contracts/boundaries.md).
That document is the authority; this file states the delta and the cases every implementation
must satisfy.

---

## Signature

```
SessionHost:
    terminate(handle, scope=None, *, expected_start=None, proc_root=None)
        -> TerminationOutcome
```

Was `terminate(handle, scope=None) -> None`.

```
TerminationOutcome(confirmed: bool, method: str, escalated: bool, detail: dict)
```

## Rules

**T1 — An exit status is never evidence.** No implementation may return on the strength of a
stop command's return code. Every rung is followed by an independent observation of whether
the tracked process still exists.

**T2 — Identity is checked.** The observation is `pid` **and** the recorded start time
together. A pid whose start time differs is a different process: it is neither evidence of
life nor a legitimate signal target.

**T3 — Escalation, not return.** A rung that reports success while the process is still alive
records the contradiction in `detail`, sets `escalated=True`, and falls through to the next
rung. It does not return.

**T4 — Bounded.** Each confirmation is bounded in time. A confirmation that does not complete
within its bound yields `confirmed=False`, never `confirmed=True`.

**T5 — Nothing to confirm against ⇒ unconfirmed.** No recorded pid means `confirmed=False`
with `method="none"`, even if a stop command returned 0.

**T6 — Blast radius unchanged.** Confirmation is achieved by observing harder, never by
killing wider. The scope remains an opaque handle read at confirmation time and never
recomputed (M0 F18); the fallback still signals exactly one process group (FR-050).

**T7 — Raises only when there is nothing to try.** `BoundaryError` is raised when there is
neither a recorded scope nor a recorded pid — the pre-existing behaviour. An unconfirmed stop
is a returned outcome, not an exception, because the caller must distinguish "could not try"
from "tried and it survived".

**T8 — Simulated hosts confirm by construction.** `SimulatedSessionHost` returns
`confirmed=True, method="simulated"` and performs no `/proc` observation, so a simulated cancel
takes the same branch a real successful one does.

---

## Cases every implementation must satisfy

| # | Situation | `confirmed` | `method` | `escalated` |
|---|---|---|---|---|
| C1 | Scope stop returns 0; pid gone afterwards | `True` | `systemd_scope` | `False` |
| C2 | Scope stop returns 0; **pid still alive**; group signal ends it | `True` | `process_group_signal` | `True` |
| C3 | Scope stop non-zero; group signal ends it | `True` | `process_group_signal` | `False` |
| C4 | No scope recorded; group signal ends it | `True` | `process_group_signal` | `False` |
| C5 | Process already gone before anything is tried | `True` | `already_gone` | `False` |
| C6 | Everything tried; pid still alive at the bound | `False` | last rung tried | as observed |
| C7 | No scope and no pid | — | — | raises `BoundaryError` |
| C8 | Scope recorded, no pid | `False` | `none` | `False` |
| C9 | Pid present but start time differs (reuse) | `True` | `already_gone` | `False` |
| C10 | Simulated host | `True` | `simulated` | `False` |

C2 is the issue's reproduction and the regression test FR-016 requires.

C9 deserves its own note: a recycled pid means *our* process is gone. It is a success, and the
unrelated process must not be signalled.

---

## Caller obligations (`operations.cancel`)

**K1** — Change no state unless `confirmed` is true.

**K2** — Re-read the session row and the work item after a confirmed stop, before settling. A
session already terminal, or an item already out of `ACTIVE`, means the exit spool won the
race: skip the transitions, still report success.

**K3** — Report the outcome that occurred, in these shapes:

| Outcome | Line |
|---|---|
| C1 / C5 / C10 | `stopped session <id> via <method>; confirmed gone. item <n> is now interrupted and its worktree is untouched` |
| C2 | `<scope> reported success but the session was still running; stopped it by signalling the process group; confirmed gone. item <n> is now interrupted …` |
| C6 / C8 | `could not confirm session <id> stopped: pid <p> is still running. item <n> is unchanged. attach with: dtach -a <socket>` |

**K4** — Exit non-zero (`EXIT_FAILED`) on an unconfirmed outcome. The web surface inherits this
through `_report`, which refuses any non-`EXIT_OK` result.

**K5** — Never state as fact an effect that was not observed. "stopped session … via systemd
scope" is forbidden as a report of an unconfirmed stop; it is the sentence this feature exists
to delete.
