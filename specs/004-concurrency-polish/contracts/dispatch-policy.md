# Contract: Dispatch Policy

The capacity snapshot, the dispatch order, and the hold reasons. One producer each, consumed by the
dispatcher and by every surface (R1, R8).

## `capacity.snapshot(conn, *, config, registry_dir=None, proc_root=None) -> CapacitySnapshot`

Pure observation. Writes nothing except an anomaly and an audit record when observation fails.

**Algorithm**

1. `scan = sessions.scan(registry_dir=..., proc_root=...)` — live entries only.
2. If `scan.degraded` or `scan.directory_missing`, re-scan via
   `sessions.scan_via_proc((Path(config.worker.binary).name,), proc_root=...)`.
3. If that also yields zero PIDs — impossible on a running machine, therefore an enumeration failure —
   return `observable=False` with a reason, record `capacity.unobservable`, and raise the
   de-duplicated anomaly of the same kind.
4. `ours` = registry entries whose `cwd` is under `config.worktree_root`, by session id.
5. `total` = `len(scan.entries)` + our `starting`/`running` rows whose `session_id` matches no entry
   (R3 — the launch window).
6. `others` = `len(scan.entries) - len(ours)`, as an integer only (R5).
7. `per_repo` = live sessions grouped by the work item's `repo_key`, simulated included (FR-004).

**Guarantees**

- Never under-counts. Every doubt resolves upward, and an unresolvable doubt sets `observable=False`.
- Carries no handle to a session the system did not start.
- On the degraded path, session ids are unavailable, so `ours` is empty and `total` is the raw process
  count plus our un-matchable rows. This over-counts by at most the number of our live sessions —
  again the safe direction, and `degraded=True` says so on every surface.

## `ordering.plan(conn, *, config, capacity) -> list[QueueEntry]`

Pure function. No I/O beyond reading the database.

1. Read `ready` items (`db.list_work_items`, which keeps its `ORDER BY id` as the stable input).
2. Sort by `order_key(item, config.repo(item.repo_key), config.dispatch.order)` (R7).
3. Assign 1-based positions in that order.
4. Assign each entry the **first** applicable `HoldReason` in precedence order (R9), or `None`.

**Precedence, and why it is this order**

| Reason | Applies when | Ranks here because |
|---|---|---|
| `paused` | `dispatch_control.paused` | Freeing capacity would change nothing; showing a capacity reason would send the author to fix the wrong thing (US3 AS4) |
| `capacity_unobservable` | `not capacity.observable` | The cap numbers are not trustworthy, so showing them would be worse than showing nothing |
| `global_cap` | `capacity.total >= capacity.global_cap` | The machine-wide limit binds before any repository's |
| `repo_cap` | `per_repo[key] >= effective cap` | Effective cap is `min(repo.max_sessions, daemon.max_concurrent_sessions)` (R17) |
| `not_onboarded` | The existing onboarding/trust block | Milestone 001's check, unchanged, now reported through one vocabulary |
| `preparation_failed` | The item's own failure reason | An item-specific condition, reported last because it is not a queueing condition at all |

## `dispatch.select_and_dispatch` — the changed contract

```
plan = ordering.plan(conn, config=config, capacity=capacity.snapshot(...))
for entry in plan:
    if entry.hold is PAUSED or entry.hold is CAPACITY_UNOBSERVABLE:
        break                      # nothing in this pass can proceed
    if entry.hold is GLOBAL_CAP:
        break                      # no later item can fit either
    if entry.hold is not None:
        continue                   # FR-012, FR-020 — a repo cap blocks one repo, not the queue
    dispatch_item(...)
    capacity = capacity.snapshot(...)   # FR-009 — re-observed before each dispatch
```

`break` versus `continue` is the whole of FR-012 and FR-020. A global condition ends the pass; a
per-item one skips that item. Re-snapshotting inside the loop is what stops a batch from collectively
exceeding the cap and what stops two overlapping passes each seeing the same free slot.

`db.count_live_sessions` is retired. Its docstring's FR-055 reasoning — that simulated sessions count
— survives in `capacity.snapshot`, which counts them for both caps.

## The capacity hold record (R16)

Held in process memory: `(total, others, global_cap, head_item_id)`.

- Signature changes → write `dispatch.at_capacity`.
- Signature unchanged → write nothing.
- Hold clears → write `dispatch.hold_ended` with duration, passes spanned, and what freed it.

A documented summarisation under Principle III's retention clause, not an exception to it: the hold's
existence, cause, extent, start, and end are all recorded. See plan.md's Principle III section.

## Terminal and web surfaces

| Surface | Shows |
|---|---|
| `robot-army capacity` | `total`, `global_cap`, ours/others split, per-repo counts, `degraded`/`observable` flags, ordering mode in force |
| `robot-army status` | The above as a one-line summary, plus the queue with positions and hold reasons |
| `GET /queue` | `ordering.plan` rendered directly — position, hold reason, and detail per row |

The web view stops deriving its own order. Its current comment justifying `ORDER BY id` by asserting
agreement with the dispatcher becomes unnecessary, because the agreement becomes identity (R8).
