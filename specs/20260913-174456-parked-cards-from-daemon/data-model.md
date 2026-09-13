# Data Model: Parked Is Judged by the Daemon's Ignore List

No database table changes. No configuration key changes.

## Heartbeat (`heartbeat.json`) — one new field

| Field | Type | Written as | Meaning |
|---|---|---|---|
| `ignore_lists` | list of strings, or `null` | `config.trello.ignore_lists` as a list; `null` with no `[trello]` section | The column names this daemon is parking cards from. Fixed for the life of the process. |

- Defaults to `null` in the dataclass, so a heartbeat written by an older build still parses.
- Written atomically with the rest of the file (unchanged `write_heartbeat`).

## Published ignore list (derived, per reading)

`health.published_ignore_lists(report, *, running, lock_holder) -> tuple[str, ...] | None`

| Condition | Result |
|---|---|
| No daemon holds the lock | `None` |
| No heartbeat, or its pid is not the lock holder's (or either is unreadable) | `None` |
| `ignore_lists` missing, `null`, not a list, or containing a non-string or empty string | `None` |
| A list of non-empty strings (possibly empty) | that list, as a tuple |

Stale heartbeats from the lock holder are believed, as for the cap.

## Ignore list in force (derived, per listing)

`operations.resolve_ignore_lists(config, published) -> (in_force, configured_if_different)`

| `published` | Own list (`config.trello.ignore_lists`, or `()`) | `in_force` | `configured_if_different` |
|---|---|---|---|
| `None` | any | own | `None` |
| equal to own, as sets | any | own | `None` |
| different from own, as sets | any | published | own |

## Card row as listed (`_card_dict`) — unchanged shape

`parked` and `parked_list` keep their meaning; `parked` is now computed against the list in force
rather than against the listing process's own configuration.

## `cards` payload — three new keys

| Key | Type | Meaning |
|---|---|---|
| `ignore_lists` | list of strings | The list parkedness was judged against. |
| `configured_ignore_lists` | list of strings, or `null` | This process's own list, only when it differs from `ignore_lists`. |
| `ignore_list_disagreement` | string, or `null` | The mismatch sentence (research R5), rendered verbatim by both surfaces. |

Absent from the not-configured payload, which lists no cards.
