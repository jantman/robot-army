# Phase 1 Data Model: Reclaiming leaked session slots

**Feature**: [spec.md](./spec.md) | **Research**: [research.md](./research.md)

## Schema changes

**None.** No table, no column, no index, no migration. `SCHEMA_VERSION` is unchanged.

Every field this feature reads and writes already exists:

| Table | Column | Role here |
|---|---|---|
| `sessions` | `state` | `starting`/`running` is what occupies a slot; `lost` is what releases it |
| `sessions` | `ended_at` | stamped by the existing `_SESSION_STAMP_COLUMN` mapping for `lost` |
| `sessions` | `session_id` | the join key to a registry entry, for the liveness check |
| `sessions` | `pid`, `proc_start` | liveness identity; `pid = 0` for a simulated session |
| `sessions` | `dry_run` | a simulated session has no process to be alive |
| `sessions` | `work_item_id` | the join to the item whose state decides legitimacy |
| `work_items` | `state` | `dispatching`/`active` are the only states that may hold an open session |
| `anomalies` | `kind` | `orphan_session`, already in `ANOMALY_KINDS` — no new kind |

## The invariant

> **A session row in `starting` or `running` is legitimate only while its work item is in
> `dispatching` or `active`. In every other work item state it is stale, and its capacity slot
> must be released.**

Derived in [research R5](./research.md#r5--which-item-states-may-legitimately-hold-an-open-session-row)
from the dispatch path, which is the only code that opens a session row.

| Work item state | Open session row is… | On finding one |
|---|---|---|
| `dispatching` | legitimate — launch in flight | leave it |
| `active` | legitimate — confirmed and working | leave it |
| `ready` | stale | reclaim |
| `awaiting_review` | stale | reclaim |
| `interrupted` | stale — **the reported case** | reclaim |
| `failed` | stale | reclaim |
| `done` | stale | reclaim |
| `abandoned` | stale — **the second reported case** | reclaim |

## The two outcomes of finding a stale row

Exactly one applies, decided by whether the session's worker process can be observed alive.

| Condition | Outcome | Why |
|---|---|---|
| No live process (always true for a simulated session, `pid = 0`) | `transition_session(… target=LOST)` with a reason naming the route | The slot is released; the row keeps its place in the item's history |
| A live process matches the session id and is alive | **Do not transition.** Raise `orphan_session` | FR-005. Closing it would make the reported count lower than the number of live workers — the one direction of capacity error that causes harm |

Liveness is `RegistryEntry.alive()` — pid **and** `proc_start`, so a recycled pid cannot read as
a live session. No new detection method is introduced.

## State transitions used

Both already exist in `SESSION_TRANSITIONS` and are unchanged
([research R2](./research.md#r2--no-transition-table-change-is-needed)):

```
starting ──▶ lost
running  ──▶ lost
```

`lost` stamps `ended_at` through the existing `_SESSION_STAMP_COLUMN` map, so the end time is not
written by hand at any call site.

**Work item state is never changed by reclamation** (FR-011). `cancel` still moves its item to
`interrupted` and `abandon` still moves its item to `abandoned`, exactly as today; the sweep moves
no item at all.

## Effect on the capacity snapshot

`capacity.snapshot` builds `live_rows` from `db.list_sessions(states=[STARTING, RUNNING])`. A row
transitioned to `lost` leaves that set, which removes it from:

- the `unmatched` term of `total` — the whole of a simulated session's slot, since it never has a
  registry entry; and
- `_per_repo`, which counts `live_rows` grouped by their item's `repo_key`.

No code in `capacity.py` changes. The count follows from the row's state, which is why FR-006 is
satisfied structurally rather than by a second update.

What does **not** change is described in
[research R8](./research.md#r8--what-this-feature-does-not-reclaim-stated-so-it-is-not-mistaken-for-a-bug):
a stale registry file left by a dead live worker still counts, because `scan.entries` is not
filtered by liveness. Simulated sessions have no registry entry, so the reported case is unaffected.

## Idempotency

Re-running the sweep over already-reclaimed state does nothing and writes nothing (FR-008), by
construction rather than by a guard:

- `lost` is not in `[STARTING, RUNNING]`, so a reclaimed row is not selected on the next pass.
- `transition_session` returns early when source equals target, so even a direct re-assertion
  writes no audit record.
- `db.raise_anomaly` is absorbed by the partial unique index, so a persistently live worker under
  a finished item produces one anomaly, not one per 60-second pass.
