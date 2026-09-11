# Data model: Capacity breakdown that sums to its total

Nothing is stored. The only entity is the in-memory `CapacitySnapshot`.

## `CapacitySnapshot` (changed)

| Field | Type | Meaning |
|---|---|---|
| `total` | `int` | unchanged |
| `ours` | `tuple[str, ...]` | unchanged — registry entries under the worktree root |
| `others` | `int` | unchanged — the remaining registry entries |
| `simulated` | `int` | **new** — open session rows with no registry entry that carry the simulated host's signature (`Session.hosted_by_simulation`) |
| `in_flight` | `int` | **new** — every other open session row with no registry entry: a real launch not yet registered, or a session that ended and has not been reconciled |

Both new fields default to `0`, and are `0` on an unobservable snapshot.

**Invariant** (observable snapshots): `total == len(ours) + others + simulated + in_flight`.

**FR-008**: both new fields describe the system's own rows and are bare counts; the snapshot
still holds no session id, pid or handle for anything it did not start.

## `components` (new property)

The breakdown as ordered `(label, count)` pairs: `ours` and `other` always, `simulated` and
`in flight` when non-zero. Read by every one-line renderer so they agree on wording and order.

## `dispatch.at_capacity` audit detail (changed)

Gains `simulated` and `in_flight` beside `live_sessions`, `ours`, `others`, so that
`live_sessions == ours + others + simulated + in_flight` holds in the log as on screen.
