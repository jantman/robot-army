# Contract: `show`'s failure and blocker lines

The `failure` line is unchanged. The lines below replace today's `blocked : <stored>` line.
`<id>` is the item id. `F` is the stored `failure_reason` and `B` the stored `blocked_reason`.

## A failed item

| Case | Lines |
|---|---|
| Blocked, same as `B` (or `B` empty) | `  blocked    : <reason> (checked now)` |
| Blocked, differs from a non-empty `B` | `  blocked    : <reason> (checked now; not the reason recorded when it failed)` |
| Nothing local blocks | `  blocked    : nothing on this machine blocks it now (checked now) — ` `` `robot-army retry <id>` `` ` re-reads the issue before returning it to the queue` |
| Check could not complete | `  blocked    : could not be checked now: <error>` |

In every case, if `B` is non-empty and not identical to `F`, one further line follows:
`  recorded   : <B>`.

`<reason>` is exactly `retry`'s refusal text for the same condition. That is the gate's
`DispatchBlocked` message, or for an unresolved repository, `retry`'s "does not resolve to a
clone any more" sentence.

## Any other state

The check is not run. If `B` is non-empty:
`  blocked    : <B> (recorded, not re-checked)`. Otherwise no blocker line, as today.

## Payloads

- `show --json` and `GET /item/<id>` (JSON) gain `current_blocker` (shape in
  [data-model.md](../data-model.md)). `item.failure_reason` and `item.blocked_reason` are
  unchanged.
- The web item page's `blocked` entry renders from `current_blocker`, using the same wording as
  the terminal. Its `failure` entry is unchanged.

## Side effects

None beyond the version-control boundary's `git.subprocess` command records. There is no
anomaly, no column write, and no record of `show`'s own.

## `retry`

Unchanged: the same checks, order, lines, exit codes, `retry.blocked` records and anomalies.
