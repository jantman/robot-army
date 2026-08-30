# Contract — The Liveness Decision

The rule reconciliation applies to one session record, and what each outcome records. Written as
a contract because the same rule is reached from two places in one pass and must not be
re-derived differently in each.

---

## C1 — The discriminator

> A session's liveness is checked **iff the session had a process**.

Expressed against the record: the check is skipped when the stored process identifier is absent
(`NULL` or `0`), and performed otherwise.

**It is not** "iff the record is flagged simulated". Those agree at `plan`, `local` and `live`
and disagree at `no-remote` (research.md R1), which is the entire defect.

**Forbidden**: consulting, importing, or deriving `EffectLevel` in `reconcile.py`. T147 asserts
this mechanically over the whole package.

## C2 — The current attempt

For each `active` item, applied to `db.latest_session_for_item`:

```
session has no process identifier    -> skip; count skipped_never_real
registry entry found and alive       -> claim its pid; leave
session already records an exit      -> leave
otherwise                            -> session -> LOST, item -> INTERRUPTED
```

Order is load-bearing. The exit-record check must precede the death conclusion, so a spool
record applied earlier in the same tick is not overwritten by a slower observation.

## C3 — Superseded attempts

For each `active` item, applied to every **other** open record it owns
(`db.list_sessions_for_item`, state `starting` or `running`):

```
registry entry found and alive  -> claim its pid; raise orphan_session; LEAVE THE ROW OPEN
no process identifier           -> leave (it never had a process; C1)
otherwise                       -> session -> LOST
```

**The middle outcome is the dangerous one and the reason this is a contract.** Closing a record
whose worker is visibly alive would make the reported capacity *lower* than the number of
running workers, oversubscribing the one subscription the cap exists to protect. An under-count
is the only direction of capacity error that causes harm. The row stays open because the slot
really is taken.

**A superseded attempt never transitions the work item** (FR-018). The item's state follows C2
alone. Without this, resuming an item would interrupt it via the ghost of the attempt the resume
replaced.

## C4 — Exactly one report per worker

A live superseded worker is reported by C3 and **not** additionally by `_orphan_sweep`, because
that sweep skips any entry whose session record is still `running` — which C3 deliberately
leaves it as. The two rules compose without either knowing about the other.

`_orphan_sweep` is therefore **unchanged, byte for byte**, as it was under #28. Its guard is not
narrowed. Verified in R10: the superseded case yields `orphans=0` from that sweep and exactly one
`orphan_session` row from C3.

## C5 — Ordering within the pass

```
active-item sweep      (C2 + C3)   <- moves records off `running`
closed-issue pass
_sweep_stale_sessions  (#28)       <- sees them already settled; declines
_orphan_sweep                      <- inputs unchanged
```

C2 and C3 run **before** #28's sweep. A record this feature closes is no longer `starting` or
`running`, so `reclaim_stale_session` returns `"left"` on its first branch and cannot act twice.
Reversing this order would either double-report a worker or hide it.

## C6 — What each outcome records

| Outcome | Record | Contents |
|---|---|---|
| current attempt found dead | `state.session`, `state.work_item` | `running → lost`; `active → interrupted`, reason naming the absent evidence |
| superseded attempt found dead | `state.session` | `running → lost`, reason naming it **superseded**, not current — the distinction survives nowhere else |
| superseded attempt found alive | `orphan_session` anomaly | pid, cwd, `work_item_id`, `attempt`, and a note saying the row is left open on purpose |
| session skipped as never-real | `reconcile.pass` | `skipped_never_real`, incremented — the only trace, and deliberately not a per-session record: with a 60-second loop that would be a log of lines saying nothing happened |
| any pass | `reconcile.pass` | `checked`, `skipped_never_real`, `superseded` as separate figures |

## C7 — Transactions

Each record's change is made inside its own `db.transaction`, as `transition_session` requires.
The current attempt's session transition and its item transition share one transaction, so the
pair cannot be observed half-applied. Anomalies are raised in their own transaction, outside any
notification call (R14).
