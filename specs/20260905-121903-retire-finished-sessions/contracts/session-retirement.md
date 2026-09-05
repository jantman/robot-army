# Contract: session retirement

Normative. Where this and the prose in `plan.md` differ, this wins.

## C1 — When it runs

| | |
|---|---|
| Caller | `reconcile.reconcile()`, once per pass |
| Position | **after** `_resolve_closed_issues`, **before** `_cleanup_worktrees` (and therefore before `_sweep_stale_sessions`) |
| Population | every session row in `starting` or `running`, via `db.list_sessions(include_simulated=True, states=[STARTING, RUNNING])` |
| Bound | the number of open rows, which the concurrency cap bounds in turn. Never a scan of session history |

All three halves of the position are load-bearing and each is tested separately (R4):

- after the closed-issue pass, so the items it acts on exist in this pass;
- before cleanup, so a retired session's worktree is reclaimed in the **same** pass;
- before `_sweep_stale_sessions`, so FR-009 holds without a suppression flag — that sweep reaches
  an already-closed row and returns `left`.

## C2 — The decision, in order

Evaluated per session row. The **first** rule that matches decides, and every rule but the last
means *leave everything exactly as found* — no transition, no anomaly, no audit record (C6).

| # | Condition | Outcome |
|---|---|---|
| 1 | the work item is absent, or its state is not `done` | LEAVE |
| 2 | `session.pid` is falsey (`NULL` or `0` — no process was ever recorded) | LEAVE. A simulated row is `_sweep_stale_sessions`'s business, not this sweep's (C8) |
| 3 | no registry entry for `session.session_id` | LEAVE |
| 4 | the entry's process is not alive (`entry.alive()`) | LEAVE — `_sweep_stale_sessions` reclaims it later this pass |
| 5 | `entry.idle_for()` is `None` — status is not `"idle"`, or `statusUpdatedAt` is absent, malformed, or in the future | LEAVE |
| 6 | `entry.idle_for() < RETIRE_IDLE_SECONDS` | LEAVE (FR-004) |
| 7 | otherwise | **RETIRE** (C3) |

Rules 3, 4 and 5 are the "unknown is safe" rules: every way of failing to establish that the worker
is idle results in the worker being left alone. Being wrong about the registry can delay a
retirement; it can never cause one.

`RETIRE_IDLE_SECONDS = 1800`. A module constant with one caller, following
`TRANSCRIPT_GRACE_SECONDS`. If the value proves wrong, the value changes.

## C3 — Retiring

In order:

1. **Log the intent**, before anything is signalled: `session.retire`, entity `session`, carrying
   `item_id`, `session_id`, `pid`, `proc_start` and `idle_s`. Principle III: an irreversible act is
   logged before it happens.
2. **Do not choose a host at all.** Use `boundaries.session_host` unconditionally.

   *Revised during implementation.* This clause originally required the same record-driven
   discriminator `operations.cancel` uses, "so the two sites cannot drift". Writing it that way
   trips `test_only_cancel_selects_a_host_from_a_record`, whose own docstring says that if a second
   module ever needs record-driven selection, that is the moment to ask whether the selection
   belongs back in the wiring. Asked — and the answer is that this sweep does not need it: a
   simulated row is `pid = 0` by construction, and C2 rule 2 has already skipped every one of them,
   so the simulated branch would be unreachable. An unreachable branch that selects an
   implementation is exactly the drift FR-053 exists to prevent, and duplicating the discriminator
   to prevent drift between two sites when one of them can never run is the worse trade.

   FR-011 is satisfied by *never consulting the effect level*, which this does by construction. If
   the `pid` guard is ever loosened the failure is safe rather than silent: `terminate` refuses a
   recorded pid of 0 outright and sends nothing, because `getpgid(0)` answers about the caller.
3. **Terminate**: `host.terminate(handle, session.scope, expected_start=session.proc_start)`.
   Nothing about that call is re-implemented here — the pid-identity guard, the implausible-pid
   refusal, the scope-then-process-group escalation and the confirmation all belong to it (R5).
4. **Settle**, inside one `db.transaction`:
   `reclaim_stale_session(..., reason="retired: the work item is done and its worker had been idle
   for <N>s")`.
5. **Log the outcome**, with the termination's `method`, `confirmed`, `escalated` and, when present,
   `refused_reason`.

## C4 — Outcomes

`_retire_finished_sessions` returns a count of confirmed retirements. Every row lands in exactly
one of:

| Outcome | Condition | Effect |
|---|---|---|
| `retired` | terminate returned `confirmed=True`, and `reclaim_stale_session` returned `reclaimed` | row `LOST` with `ended_at`; slot released; counted |
| `refused` | terminate returned a `refused_reason` (an implausible pid) | **nothing signalled**; row untouched; logged; not counted |
| `survived` | terminate returned `confirmed=False` | row untouched, slot still held, `orphan_session` raised by `reclaim_stale_session`'s `reported` branch. FR-007: "I tried and could not" is never "it is gone" |
| `already_settled` | the row reached a terminal state between the decision and the settle — the daemon drained an exit record in its own process meanwhile | `reclaim_stale_session` returns `left`; recorded as an ordinary outcome, **not** a failure (FR-008) |

`terminate` returning `method="already_gone"` — the process died on its own between the scan and
the signal — is a `retired`, not a failure. Nothing was signalled and the row still closes.

## C5 — Counters

`ReconcileResult` gains `retired: int` and `anomalies_resolved: int`, both surfaced by `summary()`
and therefore by the existing `reconcile.pass` audit record and `robot-army reconcile` (FR-027).
No new audit action for the pass itself.

## C6 — What a non-retirement writes

**Nothing.** Not a record, not a column, not an anomaly. This is the documented Principle III gap
(FR-004, and the Constitution Check's third row): a 60-second loop logging "still busy" for a
session the maintainer is using would write ~1,440 records a day carrying one bit, and the
condition is re-derivable from the registry at any moment. Precedent: `_sweep_transcripts` writes
nothing for a session inside its grace period, for the same reason and in the same shape.

## C7 — Untouched, and asserted by test

Read the diff and confirm each is unchanged:

- `capacity.py` — a live process still counts (FR-013)
- `cleanup.py` — both guards unchanged (FR-015)
- `states.py` — no new state, no new edge in either transition table
- `models.py::ANOMALY_KINDS` — no new kind
- ~~`_orphan_sweep` — unchanged~~ **This clause was wrong and is withdrawn.** Review of PR #140
  showed the sweep reads the pass's opening `scan` snapshot and never re-checked liveness, so
  none of its three guards catches a session retired earlier in the same pass: the pid was
  never in `claimed_pids` (only `active` items claim), the cwd is under the worktree root, and
  the row is `lost` rather than `running`. It therefore raised a fresh `orphan_session` against
  every worker retirement had just killed. `_resolve_orphan_anomalies` cleared it later in the
  same pass, which is why the listing looked right while `result.orphans` counted a phantom and
  the log gained a raise/resolve pair per successful item. The sweep now re-checks
  `entry.alive(proc_root=...)` before raising, which makes its own docstring true and cannot
  suppress a genuine report — an orphan is by definition a process that is still running.
- `_resolve_closed_issues` — unchanged
- `sessions.parse_entry`'s `.key` refusal and `KNOWN_VERSIONS` gate — unchanged

## C8 — The invariant this depends on

`reconcile._resolve_closed_issues` is the **only** writer of `WorkItemState.DONE` (R1). That is what
lets rule 1 of C2 be the whole of FR-001's "and whose issue was observed closed".

A test must pin it: grep the source tree for writes of `WorkItemState.DONE` and assert there is
exactly one, naming this contract in the failure message. Without it, a second route to `done` added
later would silently widen retirement's precondition to something the maintainer never agreed to.
The codebase already uses this technique twice — to keep `operations.cancel` the only place that
picks a session host from stored state, and to keep the effect level out of `reconcile.py`.
