# Contract: what a retry and a refused dispatch write to the log

Principle III's standard is reconstruction: from the log alone, it must be possible to say
whether a retry was allowed, what the eligibility verdict was, and what the item's content
was refreshed to (SC-006). Today a refused retry writes **nothing** — the refusal is
returned to the caller and disappears. That is the gap FR-010 closes.

## `retry.evaluate`

Written by `operations.retry`, once per invocation that got as far as attempting a read.
Component `cli` (the web front end calls the same function).

| Field | Value |
|---|---|
| `action` | `retry.evaluate` |
| `outcome` | `ok` when the item returned to the queue, `error` on every refusal |
| `entity_type` | `work_item` |
| `entity_id` | the item id |
| `target` | `<owner>/<repo>#<number>` — the same key shape `poll.discovered` uses |
| `dry_run` | the item's own `dry_run`, so a simulated retry is never mistaken for a real one |

`detail` when the read succeeded:

```json
{
  "repo_key": "owner/repo",
  "issue_number": 42,
  "eligible": false,
  "reason": "issue author 'mallory' is not the configured author 'jantman' …",
  "author": "mallory",
  "refreshed": ["title", "body", "labels", "author"]
}
```

`detail` when the read failed:

```json
{
  "repo_key": "owner/repo",
  "issue_number": 42,
  "cause": "issue_unreachable" | "issue_absent",
  "error": "…"
}
```

`eligible` is present exactly when a verdict was reached, so "the verdict was that it is
ineligible" and "no verdict could be reached" are never conflated. `author` is recorded
because it is the fact the whole feature turns on, and reading it back is how a future
reader confirms which login was refused — the reason string alone would leave that to
quoting conventions.

`refreshed` names the columns actually rewritten. It is the log's answer to "what did the
item say when it dispatched", and it is present on the refused path too, because FR-009
refreshes there as well.

## `retry.blocked`

Unchanged in shape but now reached by one more condition: the existing refusal for a
repository precondition. It is written today as part of the `Result`, not the log, so this
contract adds it for the same reason as above.

| Field | Value |
|---|---|
| `action` | `retry.blocked` |
| `outcome` | `error` |
| `entity_type` | `work_item` |
| `entity_id` | the item id |
| `detail` | `{"repo_key": …, "blocked": "<the DispatchBlocked message>"}` |

An item refused before the read reaches `retry.blocked` and never `retry.evaluate`, which
is how the log distinguishes "we did not get as far as asking GitHub" from "we asked"
([R4](../research.md)).

## `state.work_item`

Unchanged, and still the record of the transition itself. A successful retry therefore
writes two records: `retry.evaluate` with the verdict, then `state.work_item` for
`failed → ready`. That is not duplication — the first says why the transition was allowed,
the second says it happened, and only the pair survives an interruption between them
legibly.

## `dispatch.author`

Written by `dispatch._dispatch_item` when it refuses an item on FR-014 or FR-015. Component
`daemon`.

| Field | Value |
|---|---|
| `action` | `dispatch.author` |
| `outcome` | `error` |
| `entity_type` | `work_item` |
| `entity_id` | the item id |
| `detail` | `{"recorded_author": "mallory" \| null, "configured_author": "jantman", "cause": "mismatch" \| "unrecorded"}` |
| `dry_run` | the item's own `dry_run` |

The `_fail` that follows writes its own `state.work_item` for `dispatching → failed`, as
every other dispatch refusal does. This record exists in addition because the refusal is a
security decision and the reason string in the state record is written for the queue page,
not for reconstruction: `recorded_author` alongside `configured_author` is what lets a
future reader see *both* halves of the comparison that failed.

## Justified omissions

**A successful author check writes nothing.** It is a string comparison against a column
already in the row, on the item the very next record names, and it passes on every healthy
dispatch. One line per dispatch answering a question the following `state.work_item`
answers by existing is the omission `_check_recorded_location` already documents, for the
same reason.

**The refreshed content is not logged verbatim.** `refreshed` names which columns changed;
it does not carry the new title and body. Those are attacker-controlled text of unbounded
length, and the log is a plain-text line-per-record file that the maintainer reads in a
terminal. The content itself is one `robot-army show <id>` away and the issue URL is in the
record, so reconstruction is preserved without giving an untrusted party control of the log
file's shape.
