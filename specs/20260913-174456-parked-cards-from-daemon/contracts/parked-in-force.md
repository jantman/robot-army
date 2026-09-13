# Contract: Parked Is Judged by the Daemon's Ignore List

Each case is one unit test. "Daemon" means a process holding the lock whose heartbeat carries its
own pid, unless the case says otherwise.

## The heartbeat

| # | Given | Then |
|---|---|---|
| H1 | `write_heartbeat(..., ignore_lists=("Icebox",))` | the file's `ignore_lists` is `["Icebox"]` |
| H2 | `write_heartbeat(...)` without it | the file's `ignore_lists` is `null`, and `check` still reports healthy |
| H3 | the daemon's own heartbeat, `[trello] ignore_lists = ["Icebox"]` | carries `["Icebox"]` |
| H4 | the daemon's own heartbeat, no `[trello]` section | carries `null` |

## Believing a published list

| # | Given | `published_ignore_lists` |
|---|---|---|
| B1 | no daemon running, heartbeat carries `["Icebox"]` | `None` |
| B2 | daemon running, heartbeat carries `["Icebox"]` | `("Icebox",)` |
| B3 | daemon running, heartbeat carries `[]` | `()` |
| B4 | daemon running, stale heartbeat of the lock holder carrying `["Icebox"]` | `("Icebox",)` |
| B5 | heartbeat pid ≠ lock holder, or either unreadable | `None` |
| B6 | field missing, `null`, `"Icebox"`, `[""]`, `[1]`, `{"a": 1}`, `true` | `None` |

`published_cap`'s existing tests pass unchanged (the pid-match rule is shared, not altered).

## Resolving the list in force

| # | Own | Published | In force | Configured-if-different |
|---|---|---|---|---|
| R1 | `("Icebox",)` | `None` | `("Icebox",)` | `None` |
| R2 | `()` | `("Icebox",)` | `("Icebox",)` | `()` |
| R3 | `("Icebox", "Later")` | `("Later", "Icebox")` | own | `None` |
| R4 | no `[trello]` | `("Icebox",)` | `("Icebox",)` | `()` |

## The listings

Board: `card-1` `needs_info` in `Icebox`; `card-2` `needs_info` in `Inbox`.

| # | Surface | Own list | Daemon publishes | Then |
|---|---|---|---|---|
| L1 | web `/cards` | `()` | `("Icebox",)` | `needs_info == 1`, `parked == 1`; body has `awaiting clarification (1)`; `card-1`'s reason cell starts `parked in 'Icebox'`; exactly one rescan control, for `card-2`; the mismatch sentence is on the page |
| L2 | web `/cards` | `("Icebox",)` | nothing (no daemon) | as L1, with no mismatch sentence (today's behaviour) |
| L3 | web `/cards` | `("Icebox",)` | `()` | `parked == 0`, `needs_info == 2`, mismatch sentence present |
| L4 | `robot-army cards` | `()` | `("Icebox",)` | `card-1`'s line reads `parked in 'Icebox'`; mismatch sentence printed after the table; payload carries `configured_ignore_lists == []` |
| L5 | `robot-army cards` | `("Icebox",)` | `("Icebox",)` | output identical to today's; `ignore_list_disagreement` is `null` |
| L6 | web request | — | — | the view is handed the request's single reading; `cards` takes no reading of its own when one is passed |

## The mismatch sentence

```
IGNORE LIST MISMATCH: the running daemon is parking cards in {in_force!r}, and this process is
configured for {own!r}. Parked cards are shown as the daemon decides, because the daemon is what
parks them. One of the two has been running since before the configuration changed — restart that
one and they will agree.
```

Lists are rendered as Python lists of their names, sorted, so the sentence is stable across reads.
