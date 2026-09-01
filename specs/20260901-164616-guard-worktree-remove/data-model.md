# Phase 1 Data Model: Refuse to Remove a Worktree While Its Session Is Open

**Feature**: `specs/20260901-164616-guard-worktree-remove` | **Date**: 2026-09-01

## No migration

`SCHEMA_VERSION` is unchanged. Every fact this feature needs is already recorded and already
populated by ordinary operation:

| Column | Table | Written by | Used here for |
|---|---|---|---|
| `state` | `sessions` | the session state machine | the guard: `starting` / `running` means open |
| `attempt` | `sessions` | `db.next_attempt` at launch | naming *which* attempt in the message |
| `session_id` | `sessions` | dispatch | naming the session in the message and the record |
| `pid` | `sessions` | the session-host boundary | the message, and the liveness lookup |
| `proc_start` | `sessions` | the session-host boundary, **optionally** | identifying that pid (R6) |
| `host_socket` | `sessions` | the session-host boundary | the `dtach -a` line (R10) |
| `worktree_path` | `work_items` | `worktree.prepare` | what is being removed; cleared on success |
| `branch` | `work_items` | `worktree.prepare` | the second half of removal |

Nothing is added, nothing is backfilled, and no column changes meaning.

## New in-process shapes

### `cleanup.LIVE_SESSION_STATES: frozenset[SessionState]`

`{STARTING, RUNNING}`. Lifted from the tuple inline at `cleanup.py:84`. **One definition, two
callers** (FR-014). An allow-list of open states rather than a deny-list of closed ones, for the
reason `reconcile.SESSION_BEARING_STATES` gives about its own set: a new closed state added later
must not silently start counting as live, and a new *open* state must be added here deliberately.

### `cleanup.live_sessions(conn, item_id) -> list[Session]`

Every session row for the item whose state is in `LIVE_SESSION_STATES`, in attempt order (the
order `db.list_sessions_for_item` already returns). Empty list means nothing is running.

`eligible` keeps returning its existing sentence verbatim — see R3; that string is load-bearing at
`cleanup.py:106`.

### `operations.LiveSessionRefusal` (module-private dataclass, or a plain tuple)

What the guard found, computed once and used three times — for the refusal message, for the forced
confirmation prompt, and for the audit detail and payload. Frozen, no behaviour beyond rendering:

| Field | Type | Source |
|---|---|---|
| `session_id` | `str` | `Session.session_id` |
| `attempt` | `int` | `Session.attempt` |
| `state` | `str` | `Session.state` |
| `pid` | `int \| None` | `Session.pid` |
| `liveness` | `str` | one of the four answers in R6 — `running`, `gone`, `unidentified`, `unrecorded` |
| `socket` | `str \| None` | `Session.host_socket` |

`liveness` is a word, not a boolean, because there are four answers and three of them are not
"alive"; a boolean would have to encode "we cannot tell" as one of the two, and choosing which
would be exactly the mistake R6 is about.

Only the **first** open session is rendered, with a count when there is more than one ("and 1
other"), because the operator's next action is the same either way: go and look.

## Changed payload: `worktree_remove`'s `result.data`

| Key | Before | After |
|---|---|---|
| `item_id` | present | unchanged |
| `worktree_removed` | present | unchanged |
| `branch_deleted` | present | unchanged |
| `refused_reason` | git's message, or `None` | **whichever guard refused**, or `None` |
| `refused_by` | — | **new**: `"live_session"`, `"git"`, or absent |
| `live_session` | — | **new**: the object above as a dict, or `None`. Present whether it refused or was overridden |
| `forced_over_live_session` | — | **new**: `bool` |

`refused_by` rather than a second reason field: two reason keys would leave a reader deciding which
one applies (R9). The only consumer is `--json`; `cli.py:466` is the sole caller of this operation.

## Changed record: `worktree.remove`

A new action name in an existing namespace, written as an `intent`/`outcome` pair around the whole
operation (R4). Full shape in [contracts/worktree-removal.md](./contracts/worktree-removal.md).

## State transitions

None. The guard changes no state, and the forced path performs exactly the transition it performs
today (`worktree_path` → `NULL`, `operations.py:1514`). No work item state, no session state, and
no `cleanup_state` is written by any path this feature adds.
