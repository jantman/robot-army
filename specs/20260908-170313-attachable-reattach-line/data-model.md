# Data Model: A Reattach Line Only Where Reattaching Would Reach That Session

**No persistent shape changes.** No table, column, index, or state file is added, removed, or
altered; `SCHEMA_VERSION` does not move; there is no migration. This page records what the
feature *reads*, because the whole design turns on what two existing fields can and cannot
prove.

## Read from the `sessions` row

| Field | Type | What it proves here | What it does **not** prove |
|---|---|---|---|
| `state` | `SessionState` | Whether this attempt could be the thing behind a socket. A terminal state (`exited_clean`, `exited_error`, `lost`) settles it: nothing reopens a session row, so it is not what is listening | That the socket is reachable. An open state is routinely stale — a killed host leaves `running` behind until reconciliation catches it |
| `host_socket` | `str \| None` | Where this item's host was told to listen | That the path exists, that anything is listening, or that what is listening is *this* attempt. The path is derived from the work item id (`Layout.socket_for`), so every attempt on one item records the same string |

`pid`, `proc_start` and `scope` are deliberately not consulted. They can identify the row's own
process and are the right tool for a different question — see plan.md's rejected elaborations
and research.md R3.

## Derived, per attempt, at render time

**Attachability** — one of four values, held in no field and stored nowhere:

| Value | How it is reached |
|---|---|
| `no socket recorded` | `host_socket` is falsy |
| `not this attempt's` | `state in TERMINAL_SESSION_STATES` |
| `answering` / `not answering` | the session host boundary's `is_alive` returned |
| `could not tell` | that call raised `BoundaryError` |

It is computed for the line and discarded. Nothing caches it, nothing writes it, and a second
`show` a second later may legitimately reach a different value — which is the point: the line
describes the machine now, not what a record once said about it.

## Referenced, unchanged

- `states.TERMINAL_SESSION_STATES` — already `{exited_clean, exited_error, lost}`.
- `boundaries.HostHandle` — constructed for the probe from `host_socket` and `pid`, in the same
  shape `attach` constructs it (`operations.py:3810`).
- `_session_dict` — its keys are frozen by this feature, not extended (contract I4).
