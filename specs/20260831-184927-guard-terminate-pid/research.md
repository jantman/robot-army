# Phase 0 Research: Refuse to Signal an Unverified PID

**Feature**: `specs/20260831-184927-guard-terminate-pid` | **Date**: 2026-08-31

Everything below was measured on this machine against the code at `f084664`, not inferred.
Nothing here delivered a signal to anything.

---

## R1 — Exactly which values are dangerous, and why one guard is not enough

**Measured** (`python3 -c` against `src/`, no signals sent):

| Expression | Result | Meaning |
|---|---|---|
| `procinfo.is_alive(1, None)` | `True` | A row with pid `1` and no recorded start time passes the existing pre-check. |
| `procinfo.starttime(1)` | `"17"` | `/proc/1` has a real start time — a hand-written or migrated row *could* carry a matching one. |
| `procinfo.is_alive(1, "999")` | `False` | A *mismatching* start time is already caught, and reported as `already_gone`. |
| `procinfo.is_alive(0, None)` | `False` | `/proc/0` does not exist. |
| `os.getpgid(1)` | `1` | `killpg(1, sig)` is `kill(-1, sig)` — every process this user may signal. |
| `os.getpgid(0)` | `1743559` | **The caller's own group.** `getpgid(0)` means "me", not "pid 0". |

Three conclusions, each of which kills a candidate one-line fix:

1. **Rejecting `pgid <= 1` alone is insufficient.** A recorded pid of `0` resolves through
   `getpgid(0)` to the *caller's* process group — a perfectly ordinary number, well above 1 — and
   signalling it ends the daemon or the maintainer's shell, which is issue #69's own follow-up
   comment. The **input** pid must be rejected, not only the resolved group.
2. **Identity validation alone is insufficient.** `/proc/1` has a start time like any other
   process. A row carrying pid `1` *and* `17` would pass an identity check and reach `killpg(1)`.
   The flat rejection of `0` and `1` must stand on its own, not be folded into the identity check.
3. **The identity check is what makes pid `1` reachable today.** With a recorded start time the
   existing pre-check already refuses (`is_alive(1, "999") → False → already_gone`). It is the
   *absent* `proc_start` degrading `is_alive` to a bare `/proc/<pid>` existence test that lets the
   incident through.

**Decision**: reject the input pid (`None`, `0`, `1`), reject the resolved pgid (`<= 1`), and
reject an absent recorded start time. Three tests, none redundant with the others.

**Alternatives considered**: the issue's suggestion (1) alone (`pgid <= 1`) — rejected by
conclusion 1. Suggestion (2) alone (`proc_start` validation) — rejected by conclusion 2.

---

## R2 — Is pid `0` reachable today?

**No, and only by accident.** `procinfo.is_alive(0, None)` is `False` because `/proc/0` does not
exist, so `terminate`'s milestone-014 pre-check returns `already_gone` before any rung runs. The
cancel reports `confirmed=True` for a session it never touched — a false success, but a harmless
one.

That protection is worth nothing as a design: it depends entirely on `/proc/0` never existing and
on the liveness pre-check staying ahead of the signal path. Neither is written down anywhere as a
requirement, and the pre-check is four months younger than the code it accidentally protects.

**Decision**: pid `0` becomes an explicit refusal, not an incidental `already_gone`. This is a
behaviour change on a path that exists today: a simulated row cancelled at a real effect level
currently reports success and marks the item `interrupted`; under FR-011 it will be routed to the
simulated host and *still* report a confirmed stop, so the maintainer-visible outcome is unchanged
for that case. Only a pid-`0` row that is **not** marked simulated becomes a refusal, and such a
row is malformed by construction.

---

## R3 — Where the guard lives

Three candidate sites:

| Site | Covers | Problem |
|---|---|---|
| `operations.cancel` | The one caller today | FR-008 forbids it: the next caller inherits nothing. |
| `DtachHost.terminate` (top) | Both rungs, before anything acts | Right place for the *decision*; nothing enforces it below. |
| `_signal_group` | The signal itself | Right place for the *assertion*; too late to produce a clean outcome. |

**Decision: both of the latter two, deliberately redundantly.**

- `DtachHost.terminate` validates before any rung and returns a refusal outcome. This is where the
  maintainer-facing verdict is produced.
- `_signal_group` re-validates its input pid and the pgid it resolves, and **raises** rather than
  signalling. This is unreachable if `terminate` is correct; it exists so that `os.killpg` cannot be
  reached with a catastrophic argument by any future path, including one that forgets the first
  check.

The spec asks for exactly this shape ("the two protections are deliberately redundant, because the
report's whole lesson is that one layer of 'this cannot happen' is not enough"). Under Principle I
this is two small guards on one call site, not an abstraction — the second has a present, concrete
justification: an incident that already happened.

**Why `terminate`'s top and not between the rungs**: a row whose pid is `1` is malformed, and its
recorded systemd scope is no more trustworthy than its pid. Stopping that scope is #67's hazard,
not something to attempt on the strength of a row we have just judged untrustworthy. Refusing
before the scope rung is the smaller blast radius.

---

## R4 — The shape of a refusal: raised or returned?

The 014 contract already fixes the vocabulary: **T7** — `BoundaryError` is raised only when there
is *nothing to try*; a stop that was tried and did not take is a returned outcome. A refusal is
neither. It is a third fact: *there was something to try and we declined to try it.*

**Decision**: `TerminationOutcome` gains `refused_reason: str | None`, and a refusal is
`TerminationOutcome(confirmed=False, method="refused", refused_reason=<why>)`. `BoundaryError`
keeps its existing meaning. `_signal_group` still raises, because it is a primitive with no
outcome vocabulary of its own and a raise there can never be mistaken for a stop.

**Rationale**: `cancel` needs structured data — the web action path renders from `Result.data`, and
"refused, because the recorded pid is 1" must be distinguishable in the record from "signalled and
it survived" (FR-004). Raising would collapse both into the existing
`could not stop the session: <text>` string. Returning also keeps K1 intact without a new rule: a
refusal is `confirmed=False`, so the existing "change no state unless confirmed" obligation already
forbids settling on it (FR-005), and `cancel`'s refusal branch only has to improve the *message*.

**Alternatives considered**: raise `BoundaryError` (the issue's wording, "raise rather than
signal") — rejected at the `terminate` level because it discards the rung record and gives the
caller no structure, but **adopted verbatim inside `_signal_group`**, which is where the issue's
sentence actually bites. Reusing `method="none"` — rejected: the contract defines that as "nothing
to confirm against", which is a different fact and is already produced by a different case (C8).

---

## R5 — Ordering against the existing pre-check

`terminate` currently opens with the milestone-014 pre-check: *if the recorded pid is not alive
with its recorded start time, return `already_gone`.* The new guards must run **before** it,
because two of the three rejected cases would otherwise be swallowed by it:

| Row | Pre-check first (today) | Guard first (decided) |
|---|---|---|
| pid `0` | `already_gone`, `confirmed=True` — a false success | refused |
| pid `1`, no `proc_start` | passes → **signals `kill(-1)`** | refused |
| pid `1`, matching `proc_start` | passes → **signals `kill(-1)`** | refused |
| pid `4242`, mismatching `proc_start` | `already_gone` — correct | unchanged, `already_gone` |

Final order inside `terminate`:

1. Reject impossible pids (`None`/`0`/`1`) → refusal.
2. Reject a recorded pid with no recorded start time → refusal.
3. Existing liveness pre-check → `already_gone` where it applies (unchanged).
4. Existing scope rung, then the signal rung (unchanged).

Step 3 keeps its exact present meaning, and `already_gone` stays distinct from a refusal (FR-004).

---

## R6 — Is refusing an absent `proc_start` safe for real rows?

**Yes.** `pid` and `proc_start` are written in the *same* `db.update_session_columns` call, inside
one transaction, at session confirmation (`dispatch.py:920-927`, from the registry entry's
`entry.pid` / `entry.proc_start`). There is no ordinary path that records one without the other.

The rows that carry a pid and no start time are exactly:

- **simulated rows** — `pid=0`, `proc_start=None` by construction
  (`boundaries/dtach.py`, `SimulatedSessionHost.confirm_session`), which FR-011 routes away from
  the real host entirely; and
- **malformed rows** — hand-edited, half-written, or produced by a future bug. These are precisely
  what the guard is for.

**Decision**: refusing costs no legitimate cancel. Any row that loses cancellability this way was
never safely cancellable — it had no identity to signal against.

---

## R7 — How to route a simulated session away from the real host (FR-011–FR-013)

`Boundaries` is wired once at startup and `session_host` is chosen by effect level alone
(`effects.py:229`, `REAL_AT["session_host"]`). Cancelling by session record needs a *per-call*
choice, so something has to change.

| Option | Shape | Verdict |
|---|---|---|
| A | `cancel` constructs `SimulatedSessionHost(ctx.audit)` inline | Rejected — hides a boundary selection from `Boundaries.describe()`, so the startup record no longer names every implementation in play (Principle III). |
| B | Add `simulated_session_host` to `Boundaries`; `cancel` picks by `session.dry_run` | **Chosen.** |
| C | Branch inside `DtachHost.terminate` on `HostHandle.simulated` | Rejected — makes the real host responsible for simulating, which is the divergence `contracts/boundaries.md` forbids, and leaves the real host importable-but-lying. |
| D | Make `REAL_AT` itself record-aware | Rejected — the table is deliberately data, not branches; per-row state has no place in it. |

**Decision (B)**, with one detail that matters: `wire()` constructs the simulated host **once** and
reuses that instance for `session_host` when the level is simulated:

```
simulated_host = SimulatedSessionHost(audit)
host = DtachHost(audit) if is_real("session_host", level) else simulated_host
```

so that at a simulated level `session_host is simulated_session_host`. `SimulatedSessionHost`
carries an `_alive` set; two instances would diverge and a simulated `is_alive` would start
answering differently depending on which field the caller reached for. One object, two names, no
divergence.

`describe()` gains the new field, so the startup record still names every wired implementation.

**Scope note**: this covers *termination*. `reconcile`'s socket sweep also reaches
`boundaries.session_host.is_alive` (`reconcile.py:783`), but that probes orphaned socket **files**
rather than signalling anything, and is not on this feature's path. Left alone.

---

## R8 — Why the test suite did not catch this, and what the regression test must therefore do

`tests/unit/test_terminate_confirmation.py` covers C1–C10 of the 014 contract and passes. It never
touched this bug because its harness does:

```
monkeypatch.setattr("robot_army.boundaries.dtach._signal_group", fake_signal_group)
```

Every existing test replaces the one function that actually delivers signals. `_signal_group` — the
function that calls `os.killpg` — has **no test of its own**. That is the whole reason a
`kill(-1)` sat in a covered, contract-documented code path.

**Decision**: the regression tests target `_signal_group` **directly**, asserting the recorded call
list is **empty** (SC-001). Testing `terminate` alone would again prove only that the refusal branch
is reachable, not that the signal is unreachable.

One trap in doing that: `dtach.py` does a plain `import os` and calls `os.killpg`, so
`monkeypatch.setattr(os, "killpg", spy)` would replace the attribute on the **real** `os` module for
the whole process. Replace the *name in dtach's namespace* instead —
`monkeypatch.setattr("robot_army.boundaries.dtach.os", FakeOs())` — so the spy is scoped to the
module under test and no other test can be affected by it. No production-side injection seam is
needed, which is why none is added (Principle I).

No test in this feature may deliver a real signal to a real process. The suite runs on the machine
the incident destroyed.

---

## R9 — What the maintainer sees, and what the record says

A refusal is an operator-visible defect in stored state, so it must name the row and the field.

- **Terminal** (exit non-zero, FR-006): one line naming the session, the rejected field and value,
  and what to do — e.g. `refused to stop session <id>: the recorded pid is 1, which cannot be a
  session process (signalling it would signal every process you own). item <n> is unchanged; inspect
  the session row.`
- **Record** (FR-007): the existing `session.terminate` action already opens with an intent
  carrying `scope`, `pid` and `proc_start`. Its outcome gains `refused: true`, `refused_reason`, and
  `signals_sent: 0`. No new action name is introduced — the refusal is an outcome of the
  termination action, which is what a reader looking for "what happened when I cancelled item 29"
  will search for.

**No Principle III exception is claimed.** Nothing this feature adds goes unlogged.

---

## R10 — Killed halfway

A refusal writes nothing: no signal, no state transition, no file. The intent record is flushed
before the check, so a process killed between the intent and the outcome leaves an intent with no
outcome — the ordinary crash signature Principle IV asks for — and the work item is still `ACTIVE`,
which reconciliation's active sweep visits on the next tick.

The routing change (FR-011) is a startup-time wiring choice and holds no state.

There is no half-refused state to reach.

---

## R11 — Explicitly out of scope

- **The descendant/process-tree assertion** (issue #69's suggestion 3). It overlaps #67 from the
  other direction and is a materially larger change: it needs a notion of "this session's tree" that
  the system does not currently hold. Recorded in the spec's Assumptions; left for #67.
- **The systemd scope rung's blast radius** (#67). Untouched. Confirming that the recorded target
  died still says nothing about what else died with it; this feature narrows only *what may be
  signalled*.
- **Simulated rows that nothing closes** (#28, closed). Reconciliation now marks them `lost`. FR-011
  addresses the cancel path only.
