# Contract: `keep`, `unkeep`, `keeps`

Flat verbs, siblings of `hold`/`unhold`/`holds`. They are item-only. Every one of them accepts
`--json`, and the JSON is the `Result.data` shown below.

## `robot-army keep <id>`

| Case | Exit | Says |
|---|---|---|
| No such item | 1 (`EXIT_FAILED`) | `no work item with id <id>`, the same as `_no_such_item` |
| Item `abandoned`, any sessions | `EXIT_PRECONDITION` | `work item <id> is abandoned and can never reach done, so a keep would never take effect. An abandoned item's session is already left running; \`robot-army cancel <id>\` ends it.` |
| Item `done` with no open session | `EXIT_PRECONDITION` | `work item <id> is <state> and has no running session; there is nothing to keep.` and then, when a session ever existed, `Its transcript survives: claude --resume <session-id>` |
| Already kept | 0 | `work item <id> was already kept, since <local time> by <by>` (the original `kept_at`) |
| Placed, item `done` with a live session | 0 | `work item <id> (#<n> <repo>) kept at <time> by cli` / `Its session will not be retired, its tab not closed and its checkout not reclaimed until released. It holds a session slot meanwhile.` / `Release it with robot-army unkeep <id>.` |
| Placed, any other state | 0 | Same first line, then `The keep takes effect when the issue closes: the session stays open instead of being retired.` and the release line |

`data`: `{"item_id", "kept": true, "kept_at", "kept_by", "redundant", "state", "live_session": bool}`

A keep never starts, stops or signals anything, so it does not need the daemon.

## `robot-army unkeep <id>`

| Case | Exit | Says |
|---|---|---|
| No such item | 1 | as above |
| Not kept | 0 | `work item <id> was not kept; nothing to release` |
| Released | 0 | `work item <id> released; it was kept since <time> by <by>`, then, when a session is open under a `done` item, `Its session is retired on the next reconciliation pass that finds it idle.` |

`data`: `{"item_id", "kept": false, "released": bool, "open_sessions": [session_id, …]}`

## `robot-army keeps`

A read command. It lists every keep in force, **including inert ones**, with columns
`item`, `repo`, `issue`, `state`, `session`, `kept`, `by`. In the `session` column:

- `live <session-id>` when an open row exists;
- `none yet` for a non-terminal item that has no open row;
- `no live session` for a terminal item that has no open row.

When nothing is kept, it says `nothing is kept` in words.

`data`: `{"keeps": [{"item_id", "repo_key", "issue_number", "state", "kept_at", "kept_by", "age_seconds", "open_session_id"}], "count"}`
