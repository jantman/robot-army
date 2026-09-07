# Data Model: The session cap every surface shows is the one being enforced

No database table changes. No migration. Two in-memory structures gain a field each, and one
on-disk state file gains a key.

## `heartbeat.json` — the daemon's published cap

`robot_army.health.Heartbeat`, written atomically on every beat.

| Field | Type | Meaning |
|---|---|---|
| `max_concurrent_sessions` | `int \| None` | The global session cap this daemon is enforcing. `None` — the default, so a heartbeat written by an older build still parses — means *not published*. |

Everything else in the file is unchanged. The value comes from
`config.daemon.max_concurrent_sessions` of the configuration the daemon loaded at startup, and
is therefore constant for the life of the process.

**Read rule.** A reader accepts the value only when it is an `int` (and not a `bool`) of at
least 1, **and** the heartbeat's `pid` is the pid in the lock file. Absent, wrongly typed, or
out of range is *not published*, never *a cap of zero*; a pid that does not match the lock
holder means the file belongs to a daemon that has already exited.

## `CapacitySnapshot` — the cap a fraction is reported against

`robot_army.capacity.CapacitySnapshot`. Still never stored; still one observation.

| Field | Type | Change | Meaning |
|---|---|---|---|
| `global_cap` | `int` | meaning narrowed | **The cap in force**: the daemon's published cap when one was supplied, the reading process's configured cap otherwise. Every fraction, every `at_capacity` test, and every planner decision made from this snapshot uses it. |
| `configured_cap` | `int \| None` | new | The reading process's own configured cap, **present only when it differs from `global_cap`**. `None` means there is nothing to report — either the two agree, or no enforced cap could be learned. |

Derived:

- `at_capacity` — unchanged in definition (`total >= global_cap`), and therefore now decided
  against the enforced cap wherever one is known.
- `cap_disagreement` → `str | None` — the one sentence, present exactly when
  `configured_cap` is not `None`. The terminal and the web both render this string; neither
  composes its own.
- `describe()` — gains the disagreement as a trailing clause when there is one.

## Resolution

`health.published_cap(report, *, running, lock_holder) -> int | None` is the only place that
turns a health report into a cap.

| Daemon holds the lock | Heartbeat readable | Its pid is the lock holder's | Cap field usable | Result |
|---|---|---|---|---|
| no | — | — | — | `None` — nothing is enforcing anything |
| yes | no | — | — | `None` — genuinely unknown |
| yes | yes | no, or either pid unreadable | — | `None` — a restart is in progress, or cannot be ruled out |
| yes | yes (fresh **or** stale) | yes | no | `None` — not published |
| yes | yes (fresh **or** stale) | yes | yes | that integer |

`capacity.snapshot(conn, *, config, enforced_cap=None, ...)` turns that into the two fields
above:

```text
global_cap     = enforced_cap if enforced_cap is not None else config.daemon.max_concurrent_sessions
configured_cap = config.daemon.max_concurrent_sessions   if the two differ
                 None                                     otherwise
```

`enforced_cap=None` is the daemon's own call path (R8) as well as the "cannot be learned"
path, and both want the configured cap with no disagreement reported — which is what the
rule above produces without a second parameter to distinguish them.

## Machine-readable payload

`operations._capacity_dict`, which both `robot-army capacity`/`status --json` and the web
chrome render from:

| Key | Change | Meaning |
|---|---|---|
| `global_cap` | meaning narrowed | The cap in force, as above. A consumer reading only this key gets the right number without knowing anything about this feature. |
| `configured_cap` | new | Present and non-`None` only when this process's configuration disagrees with the cap in force. |
| `cap_disagreement` | new | The sentence, or `null`. So a consumer never has to build prose from two integers. |

`robot-army capacity` assembles its own document carrying the same three keys. It has no
`--json` flag on the CLI — `status --json` is where that payload is read — but the two must
not be free to disagree.

## What does not change

- `ordering` takes its cap from the snapshot exactly as before, and the daemon's snapshot is
  built with no `enforced_cap`, so the daemon's numbers are identical.
- `dispatch.check_launch_gate` gains the same optional `enforced_cap`, threaded through
  `dispatch_item`/`_dispatch_item`, so a gate running outside the daemon measures against the
  same number the surfaces report. The daemon passes nothing and is unchanged.
- No table, no column, no migration, no configuration key.
- The per-repository limits (`[repos.*] max_sessions`) are untouched: they are enforced by
  the same daemon from the same file and are not reported as a fraction anywhere.
