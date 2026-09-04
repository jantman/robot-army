# Data Model: Retry Re-Verifies the Author

One nullable column on one existing table. No new table, no new state, no new transition.

## `work_items.author` — who wrote the issue this item came from

Added by **migration 011**. `SCHEMA_VERSION` becomes `11`.

```sql
ALTER TABLE work_items ADD COLUMN author TEXT;
```

| Value | Meaning | Who writes it |
|---|---|---|
| a login string | the author GitHub reported when the issue was last read | `db.insert_work_item` at discovery; `operations.retry` on every successful re-read |
| `NULL` | **never recorded** — a row written before migration 011 | nobody; there is no backfill |

`NULL` is not "no author" and not "author unknown but probably fine". It is *this row's
provenance cannot be established*, and it is load-bearing: a `ready` row from before this
change may have reached `ready` through the defect being fixed here, and no query answers
which. So `NULL` refuses the dispatch and names `retry` as the recovery ([R7](research.md)),
which re-reads the issue and writes the column for the first time.

**No backfill, deliberately.** Migration 008 backfilled `sessions.transcript_checked_at`
because it could derive the right answer — those sessions really had been judged. Writing
`config.github.author` into this column would be the opposite: an unverified claim in the
one column whose purpose is to hold a verified fact. Migration 005's comment refuses the
same thing about clone paths, in the same words.

**Nullable, not `NOT NULL DEFAULT ''`.** A default would make every pre-migration row
indistinguishable from a row whose issue was written by an author whose login is the empty
string, and would silently give the two the same treatment. SQLite cannot add a `NOT NULL`
column without a default anyway, so the choice is between a lie and a `NULL` that means
something — and this one means something.

**No index.** Nothing queries by author. The only read is by primary key, on an item the
dispatcher already holds.

### Where it is written

| Site | When | Value |
|---|---|---|
| `db.insert_work_item` | the poller creates the row | `issue.author` from the listing |
| `operations.retry` | after a successful re-read, before the verdict is consulted | `issue.author` from `get_issue` |

There is no third writer. `reconcile`, `cleanup` and the board reader never touch it: none
of them reads the issue, and a column written from a source that did not read it is the
fabrication this feature exists to delete.

### Where it is read

| Site | Question |
|---|---|
| `dispatch._dispatch_item` | is this item's author the configured author? (FR-014, FR-015) |
| `dispatch._dispatch_item` | the `author` field of the `Issue` handed to the launch |

The second replaces `author=config.github.author`. After the check above it, the two are
provably equal — which is the point: the value is now equal because it was compared, not
because it was assigned.

## Item content refreshed by a retry

No schema change; these columns already exist. A successful read inside `retry` rewrites
them from the issue as it stands ([R5](research.md)), on both the allowed and the refused
path.

| Column | Source |
|---|---|
| `title` | `issue.title` |
| `body` | `issue.body` |
| `labels` | `states.dumps_labels(list(issue.labels))` — the poller's own encoding |
| `author` | `issue.author` |

`source_url`, `repo_key` and `issue_number` are not refreshed: they identify the issue
rather than describe it, and a read that returned a different one would mean the identifier
was wrong, not the content.

## State transitions

Unchanged. `WORK_ITEM_TRANSITIONS` gains no entries.

`FAILED → READY` remains legal and remains the transition `retry` performs; this feature
adds a precondition to performing it, not a new path. `READY → DISPATCHING → FAILED` is how
a dispatch refused under FR-014 or FR-015 settles, which is the same route
`check_gates` failures already take.

## Interruption

| Killed at | Observable state | Recovered by |
|---|---|---|
| after the read, before the content refresh | item `failed`, stale content, old reason | the next retry: the read is repeated and nothing was written |
| after the content refresh, before the transition | item `failed`, **current** content, old reason | the next retry, which reaches the same verdict; no item is in the queue that should not be |
| after the transition | item `ready` with current content and a verified author | nothing to recover |
| mid-migration 011 | `user_version` still 10, column absent or rolled back | the migration re-runs whole on next start |

The order is chosen so the interrupted state is always the safe one: content can be stale
while an item is blocked, but an item is never in the queue with content nobody re-read
([R5](research.md)).
