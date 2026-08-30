# Phase 0 Research: Reclaiming leaked session slots

**Feature**: [spec.md](./spec.md) | **Date**: 2026-08-30 | **Source**: [#28](https://github.com/jantman/robot-army/issues/28)

Every finding below was measured against this checkout by running code, not inferred from
reading it. The probes were throwaway test modules under `tests/unit/`, run with
`uv run pytest -s`, and deleted afterwards. Baseline before any change: **1714 passed, 1 skipped**.

---

## R1 — The leak reproduces exactly as filed, on both reported routes

**Question**: does the reported behaviour still hold, and is the issue's account of it accurate?

**Measured**. A simulated item in `active` with a `running` session, `pid = 0`:

```
CANCEL rc= 0 ['stopped session sess-1-1 via the process group; item 1 is now interrupted …']
ITEM STATE: interrupted | SESSION: running ended_at= None
CAPACITY total= 1 per_repo= {'jantman/robot-army': 1}

RECONCILE: {'checked': 0, 'interrupted': 0, … 'orphans': 0, …}
AFTER RECONCILE SESSION: running ended_at= None
CAPACITY AFTER total= 1 per_repo= {'jantman/robot-army': 1}

ABANDON rc= 0 ['item 1 abandoned. …']
AFTER ABANDON item= abandoned session= running ended_at= None
CAPACITY AFTER ABANDON total= 1 per_repo= {'jantman/robot-army': 1}
```

**Decision**: the issue's account is exact, including `checked 0` — reconciliation examines
nothing because its session sweep iterates `WorkItemState.ACTIVE` (`reconcile.py:139`) and
`cancel` has already moved the item off that list.

**Rationale for the shape of the fix**: two independent facts have to be true for the slot to
come back, and today neither is. `cancel` does not close the row, and no sweep can reach it. The
maintainer's answer to FR-012 fixes both: close it at the command, and assert the invariant in
reconciliation regardless.

---

## R2 — No transition-table change is needed

**Question**: is closing an open session row as `lost` already legal, from both open states?

**Measured**:

```
running->lost legal: True
starting->lost legal: True
lost->exited_error legal: False
```

**Decision**: `SESSION_TRANSITIONS` is **not** touched. `STARTING → LOST` and `RUNNING → LOST`
already exist for reconciliation's own use, and they are exactly the two edges this feature needs.

**Alternative rejected**: adding `LOST → EXITED_ERROR` so a late exit record could overwrite the
reclaimed state with a real exit status. Rejected for the reason 013's R3 gave for the mirror
case: it makes a contradiction legal instead of resolving it. R3 below shows it is also
unnecessary.

---

## R3 — Closing the row at cancel time is race-safe

**Question**: FR-002 closes the row when the command runs. For a *live* session the wrapper may
still deliver an exit record afterwards. Do the two paths fight?

**Measured**. Close a `running` session as `lost`, then feed the spool a real exit record for it:

```
LATE EXIT RECORD OUTCOME: duplicate
SESSION: lost ended_at= 2026-08-30T16:37:33Z | ITEM: active
```

**Decision**: no coordination is required, and no new guard is written.

**Rationale**: two pre-existing mechanisms already cover it, and neither was written for this
feature. `spool._already_applied` treats an `exit` as applied once the session state is terminal,
and its terminal set already includes `LOST`. Separately, `spool.apply_record` only moves the work
item when `item.state is WorkItemState.ACTIVE`, so a record arriving after `cancel` cannot disturb
the `interrupted` item either. The cost is the one 013's R4 already accepted and recorded: the
history keeps `lost` rather than the truer `exited_error 143`, and the real exit status is not
written. That is a weaker reason in the log, not a wrong state, and it is bounded to sessions
cancelled while live.

---

## R4 — A live worker under a non-running item is invisible today

**Question**: FR-005 forbids closing a row whose process is alive and requires that case to be
reported. Does the existing orphan sweep already report it?

**Measured**. A real (non-simulated) session, `pid 777`, alive in `/proc`, registry entry present,
cwd under the worktree root — under an item in `interrupted`:

```
RECONCILE: {'checked': 0, …, 'orphans': 0, …}
ANOMALIES: []
CAPACITY total= 1 ours= ('live-1',) per_repo= {'demo': 1}
```

**It is not reported.** `_orphan_sweep` (`reconcile.py:460`) skips any entry whose session row is
`RUNNING`:

```python
if row is not None and SessionState(row["state"]) is SessionState.RUNNING:
    continue
```

That guard suppresses precisely the condition this feature is about. Capacity is correct here —
the worker really is running and really does hold a slot — but nothing tells the maintainer that
an `interrupted` item has a live worker in its worktree, which is the M0 F17 case the orphan sweep
exists for.

**Decision**: the new sweep raises the `orphan_session` anomaly itself for the alive case, and
`_orphan_sweep` is left **unchanged**. The kind already exists in `ANOMALY_KINDS`; no new kind is
introduced. The `detail` distinguishes the two sites.

**Alternative rejected**: narrowing `_orphan_sweep`'s guard to also require the item be active.
It is a one-line change and looks tidier, but the guard also suppresses cases this feature has no
business changing — most concretely, an item on attempt 2 whose attempt-1 row is still `RUNNING`
with a live process, which is not what #28 is about. Re-deriving which of those the guard was
protecting is a bigger and riskier question than adding one call at a site whose meaning is exact.
Two call sites for one anomaly kind is acceptable because the partial unique index already makes
re-raising a no-op, so they cannot double-report.

---

## R5 — Which item states may legitimately hold an open session row

**Question**: the invariant needs a precise legitimate pairing, or it will sweep away live work.

**Derived from the dispatch path**, which is the only code that opens a session row.
`dispatch.dispatch_item` moves the item to `DISPATCHING` (`dispatch.py:675`), inserts the session
row, and only later moves the session to `RUNNING` (`dispatch.py:932`) and the item to `ACTIVE`
(`dispatch.py:939`). So the two legitimate windows are:

| Work item state | Session state | Meaning |
|---|---|---|
| `dispatching` | `starting` | launch in flight, worker has not confirmed |
| `active` | `running` | confirmed and working |

**Decision**: an open session row (`starting` or `running`) is stale whenever its work item is in
any state other than `dispatching` or `active`. `ready`, `awaiting_review`, `interrupted`,
`failed`, `done` and `abandoned` all imply no session is in flight.

**Note on `dispatching`**: it needs no extra protection from this sweep beyond being on the
allow-list. `dispatching_max_age_seconds` (900s) already reaps an item stuck there, and that reaper
moves it to `failed` — at which point this sweep may legitimately close its row on the next pass.

---

## R6 — The routes that do *not* leak, checked so the fix is not over-scoped

**Question**: the issue names three routes. Are there others, and is `_resolve_closed_issues` one?

`_resolve_closed_issues` moves an item `ACTIVE → DONE` without closing its session row, which
looks like a fourth leak. **It is not**, and both halves were measured:

- **Simulated items**: the function skips them outright (`if item.dry_run: continue`, FR-055), so
  it never moves a simulated item at all. Measured: `closed_done: 0`, item stayed `active`.
- **Live items**: the active-item sweep runs earlier in the same pass and has already closed a
  dead session as `lost`. Measured: `ITEM: done | SESSION: lost ended_at= …`, `CAPACITY total= 0`.

**Decision**: no change to `_resolve_closed_issues`. The three routes in the issue are the three
routes.

**One consequence to accept, not fix**: a live item whose issue is closed *while its worker is
still running* reaches `done` with a genuinely live session. Under FR-005 the new sweep will
decline to close that row and will raise `orphan_session` for it. That is a new anomaly for a
situation that produces none today — and it is the correct outcome, because a worker still editing
files under an item marked `done` is exactly what M0 F17 says must not pass silently.

---

## R7 — Where the shared rule lives

**Question**: FR-012 puts the rule at three call sites — `cancel`, `abandon`, and the sweep. How
is it constructed once?

**Decision**: one helper in `reconcile.py`, called by the sweep and imported by `operations`.

**Rationale**: `operations.py` already imports `reconcile` (`operations.py:38`) and already calls
into it for the manual `reconcile` verb, so the dependency direction is established and no new
edge is added to the module graph. Putting the rule in `states.py` was rejected — that module is
the transition gate and knows nothing about registries or liveness. Putting it in `sessions.py`
was rejected — that module reads the registry and deliberately does not import `db`.

**On the liveness check at the command sites**: `cancel` and `abandon` must honour FR-005 too, so
both need to consult the registry. `operations.resume` and `operations.restart` already take an
optional `registry_dir: Path | None = None` for exactly this kind of plumbing; `cancel` and
`abandon` gain the same parameter rather than a new mechanism.

---

## R8 — What this feature does *not* reclaim, stated so it is not mistaken for a bug

`capacity.snapshot` counts `len(scan.entries) + len(unmatched)` and applies **no liveness filter to
registry entries**. For a live session, a registry file left behind by a dead worker therefore
keeps counting toward the cap after this feature closes the database row.

**Decision**: out of scope, and deliberately so. The bias is the safe direction the module is
built around — "the count never errs downward" — the registry belongs to the worker rather than to
this system, and #28 is about simulated sessions, which never have a registry entry at all. Their
slot is held entirely through the `unmatched` term, which is exactly the term closing the row
removes.

---

## Summary of decisions

| # | Decision | Alternatives rejected |
|---|---|---|
| R2 | Reclaim as `lost`; `SESSION_TRANSITIONS` untouched | adding `LOST → EXITED_ERROR` |
| R3 | No race guard; rely on `spool._already_applied` and the `ACTIVE` guard | coordinating cancel with the spool |
| R4 | The new sweep raises `orphan_session`; `_orphan_sweep` untouched | narrowing `_orphan_sweep`'s guard; a new anomaly kind |
| R5 | Open row is legitimate only under `dispatching` or `active` | a terminal-states-only rule, which misses `interrupted` — the reported case |
| R6 | Three routes, not four; `_resolve_closed_issues` unchanged | — |
| R7 | One helper in `reconcile.py`, three call sites | a helper in `states.py` or `sessions.py`; three independent implementations |
| R8 | Stale registry files stay out of scope | filtering `scan.entries` by liveness in `capacity` |

No NEEDS CLARIFICATION remains. FR-012 was answered by the maintainer during `/speckit-specify`.
