# Data Model: Surface the pull request in the web UI

No new table and no new entity of its own. One existing row gains two columns, and one existing
boundary dataclass gains a field.

## `work_items` — two new columns (migration 013)

```sql
ALTER TABLE work_items ADD COLUMN pull_requests TEXT;
ALTER TABLE work_items ADD COLUMN pull_requests_at TEXT;
```

### `pull_requests`

A JSON array of objects, or `NULL`. Each object:

| Key | Type | Meaning |
|---|---|---|
| `number` | int | The pull request's number in the work item's repository |
| `url` | str | Its `https://github.com/…` address, as GitHub returned it |
| `state` | str | `open`, `merged`, or `closed` — lower-cased from GraphQL's `OPEN`/`MERGED`/`CLOSED` |

Ordered by number ascending, so the same set always serialises identically and a no-change pass
is detectable by comparing the stored text with the freshly built text.

**Three states, and they are not two.** This is the same discipline `speckit_baseline` and
`board_column` are documented with, and the reason the column is nullable rather than
`DEFAULT '[]'`:

| Value | Means | Rendered as |
|---|---|---|
| `NULL` | never looked up — a row written before migration 013, an item that has never been dispatched, or a simulated item | "not checked" |
| `'[]'` | looked up successfully; GitHub reports no pull request | "none" |
| `'[{"number":142,…}]'` | these, as of `pull_requests_at` | the links |

Collapsing `NULL` into `'[]'` would tell the maintainer "there is no pull request" on the
strength of never having asked, which is the failure FR-016 exists to prevent.

### `pull_requests_at`

The UTC timestamp of the last **successful** lookup, in the project's usual
`YYYY-MM-DDTHH:MM:SSZ` form. `NULL` while `pull_requests` is `NULL`, and the two are always
written together in one statement.

It advances **only** on success (FR-011). A failed lookup leaves both columns exactly as they
were, so the age the interface shows is the age of the answer rather than the age of the last
attempt.

### Model field

`models.WorkItem` gains `pull_requests: str | None = None` and `pull_requests_at: str | None =
None`, plus a `pull_request_list` property mirroring the existing `label_list`:

```python
@property
def pull_request_list(self) -> list[dict[str, Any]]:
    """``[]`` for both "none found" and "never looked up" — the caller that needs to tell
    them apart reads ``pull_requests is None``, exactly as ``speckit_baseline``'s readers do."""
```

Unparseable JSON returns `[]` rather than raising: a column we cannot read is a column we do
not have, which is `speckit.record_phase`'s rule for the baseline it reads.

## `boundaries.PullRequest` — one new field

```python
@dataclass(frozen=True, slots=True)
class PullRequest:
    number: int
    url: str
    state: str          # now "open" | "merged" | "closed", never GitHub's upper-case enum
```

The dataclass is unchanged in shape. What changes is the *domain* of `state`: today
`open_pr_for_branch` only ever asks for open pull requests and passes REST's `"open"` through.
The GraphQL read normalises `OPEN`/`MERGED`/`CLOSED` to lower case at the boundary, so nothing
above the boundary ever sees GitHub's spelling — the same rule `Repo.verified_origin` follows
for remote URLs.

## State transitions

**None.** This feature adds no work-item state, no session state, and no transition. Nothing
dispatches, blocks, retires, or cleans up differently because of what is stored here; the
columns are read by the interface and by nothing that decides anything. That is the scope
boundary the spec's Assumptions section draws, and it is what keeps the `wait_for_merge` gate
and `_resolve_closed_issues` untouched.

## Lifecycle of the two columns

```
never dispatched      pull_requests = NULL          (no branch: never looked up)
        │
        ▼  dispatched, session running
first refresh         pull_requests = '[]'          pull_requests_at = <now>
        │
        ▼  the session opens a pull request
next refresh          '[{"number":144,"state":"open"}]'
        │
        ▼  the pull request merges
next refresh          '[{"number":144,"state":"merged"}]'
        │
        ▼  the issue closes; the item goes done; every stored PR is terminal
                       no further lookup, ever — the value stands as the record
```

A failed lookup at any point above leaves the row on its current line and logs why.
