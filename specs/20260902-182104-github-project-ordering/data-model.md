# Phase 1 Data Model: GitHub Project Board Ordering

**Feature**: [spec.md](spec.md) | **Research**: [research.md](research.md)

## Migration 009

Appended to `MIGRATIONS`; never editing an existing one. One transaction, `user_version`
advanced last, so an interrupted upgrade re-runs the whole migration (Principle IV).

```sql
-- Where the board puts this item, as the last successful board read saw it (issue #48).
--
-- Two columns rather than one, and the pair must stay a pair, because four states have to
-- remain distinguishable and collapsing any two of them is a real bug (research R9):
--
--   board_column NULL, repo has never been read   ->  no board knowledge; nothing is gated
--                                                     and the repository orders as it always did
--   board_column NULL, repo HAS been read         ->  read, and this item is not on the board:
--                                                     dispatchable, ordered after everything
--                                                     the board ranked (FR-008)
--   board_column = the dispatch column            ->  board_position is its rank, 1-based
--   board_column = anything else                  ->  parked by the author; held (FR-012),
--                                                     board_position NULL
--
-- The distinction between the first two lives in repo_projects.last_read_at, not here, so
-- that "never read" is one fact about a repository rather than a fact repeated on every row.
--
-- board_position is NULL for every item outside the dispatch column and must NEVER be
-- written as 0 for "unknown". boundaries/__init__.py records what that mistake cost the
-- last time it was made -- commits_ahead folding "could not determine" into 0 -- and here
-- it would silently promote every item of an unread board to the head of its queue.
ALTER TABLE work_items ADD COLUMN board_column   TEXT;
ALTER TABLE work_items ADD COLUMN board_position INTEGER;

-- Which project governs a repository, how that was decided, and how the last read went.
--
-- A table rather than columns on `repos`, because `repos` is an *approval* record: migration
-- 005 is emphatic that it stores what a human approved at a verified location, and nothing
-- re-derives after approval. This is the opposite -- discovered, self-refreshing, and
-- carrying its own failure state -- so putting it there would blur the one record whose
-- value is that it does not change on its own.
--
-- Not `poll_state` either: that table's columns are fixed (etag, last_status, backoff) with
-- nowhere to put a project id, a column name, or which of the two the author chose.
--
-- One row per repository, written by the poll, deleted by nothing. A repository whose
-- project is later unlinked keeps its row with resolved_at NULL and unresolved_reason set,
-- which is what lets `status` say *why* a board stopped governing rather than going quiet.
--
-- last_read_at is the gate for FR-014: no item is ever held for its column while this is
-- NULL, because the system has no knowledge of where anything sits and inventing a hold
-- from ignorance is worse than dispatching.
CREATE TABLE repo_projects (
    repo_key             TEXT PRIMARY KEY REFERENCES repos(repo_key),
    project_id           TEXT,      -- ProjectV2 node id; NULL when unresolved
    project_number       INTEGER,
    project_title        TEXT,
    project_url          TEXT,
    project_source       TEXT,      -- 'discovered' | 'configured'
    column_name          TEXT,      -- the dispatch column; NULL when unresolved
    column_source        TEXT,      -- 'discovered' | 'configured'
    resolved_at          TEXT,      -- when project+column last resolved cleanly
    unresolved_reason    TEXT,      -- why they did not; NULL when resolved
    last_read_at         TEXT,      -- last SUCCESSFUL board read; the FR-014 gate
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    backoff_until        TEXT
);
```

No index beyond the primary key. The table has one row per onboarded repository — single
digits on this machine — and every access is by `repo_key` or a full scan of all of it.

**Backfill**: none, deliberately. Every existing `work_items` row gets `board_column NULL`
and every repository starts with no `repo_projects` row, which is exactly "no board knowledge
yet": nothing is held, nothing is reordered, and the first poll after the upgrade fills it in.
An upgrade that changed dispatch order before reading a board would be changing it from
information it does not have.

## Entities

### `BoardSnapshot` (boundary value type, not persisted)

What one successful read of one project returned. `frozen=True, slots=True`, in
`boundaries/__init__.py`, added to `__all__`.

| Field | Meaning |
|---|---|
| `project_id`, `project_number`, `project_title`, `project_url` | which board answered |
| `column_name` | the dispatch column this snapshot was read against |
| `ranked` | `tuple[BoardEntry, ...]` — issues in the dispatch column, in board order |
| `elsewhere` | `dict[int, str]` — issue number → the column it is parked in |
| `total_items` | how many items the project holds, for the record and for page-bound checks |

`ranked` and `elsewhere` are separate rather than one list with a nullable rank, for R9's
reason: "in the dispatch column at rank 3" and "parked in Backlog" are different facts and a
single representation invites one to be read as the other.

### `BoardEntry`

`issue_number: int`, `repo_key: str`, `position: int` (1-based, dense within the repository's
slice of the column). Position is assigned by robot-army from the sequence GitHub returned —
GitHub exposes order but never a rank (R1) — and is dense **per repository**, so a project
shared by two repositories gives each its own 1..n rather than a sparse global count.

### `ProjectResolution` (boundary value type, not persisted)

`project_id`, `project_number`, `project_title`, `project_url`, `project_source`,
`column_name`, `column_source`, `candidates: tuple[str, ...]`, `reason: str | None`.

`reason` is non-`None` exactly when the resolution failed, and carries the sentence a surface
shows: which projects were found, which column names the board offers, or which configured
value does not exist on the board. `candidates` carries what was seen so the message can name
it rather than assert an ambiguity the author cannot check.

### `RepoProject` (row type in `models.py`)

Mirrors the table one-to-one, registered in `ROW_TYPES`. Accessors follow `poll_state`'s
shape exactly: `db.get_repo_project(conn, repo_key)` returns a default-constructed
`RepoProject(repo_key=repo_key)` rather than `None` when absent, so callers never branch on
existence; `db.save_repo_project(conn, row)` is one `INSERT … ON CONFLICT(repo_key) DO UPDATE`.
`db.list_repo_projects(conn)` returns the whole table as `dict[str, RepoProject]` for
`ordering.plan`, which needs all of it once per plan rather than one query per queued item.

## State transitions

A repository moves between four ordering states. Nothing here is a state machine in the
`states.py` sense — there is no transition table and no guard — it is derived from the row.

```
                      ┌──────────────────────────────────────────┐
   no row / no        │  UNGOVERNED                              │
   project resolved   │  resolved_at NULL                        │
                      │  order = today's; nothing held           │
                      └───────┬──────────────────────────▲───────┘
        resolution succeeds   │                          │  project unlinked, made
                              ▼                          │  ambiguous, or ordering
                      ┌──────────────────────────────────┴───────┐
                      │  RESOLVED, NEVER READ                    │
                      │  resolved_at set, last_read_at NULL      │
                      │  order = today's; nothing held (FR-014)  │
                      └───────┬──────────────────────────────────┘
          first successful    │
          board read          ▼
                      ┌──────────────────────────────────────────┐
      read fails ────►│  GOVERNED                                │◄──── read succeeds
      (row keeps its  │  last_read_at set                        │
      last snapshot,  │  order = board; off-column items held    │
      marks staleness)└──────────────────────────────────────────┘
```

The self-loop on GOVERNED is FR-025 and the reason `last_read_at` records the last **success**
rather than the last attempt: a failed read leaves the previous snapshot in place, in force,
and visibly stale, rather than either discarding it or pretending it is current.

## Validation rules

| Rule | Where enforced | Requirement |
|---|---|---|
| `board_position` is NULL or `>= 1`, never 0 | the poll writes it; a unit test asserts it | R9 |
| `board_position` non-NULL implies `board_column` equals the resolved dispatch column | poll writes both together in one statement | FR-012 |
| No item is held for its column while `last_read_at IS NULL` | `ordering._hold_for` | FR-014 |
| A repository's queue positions are unchanged by board ordering | `ordering.plan`'s permutation | FR-002 |
| Board facts are only written for items whose issue belongs to that repository | the poll filters on `repository.nameWithOwner` | FR-011 |
| Draft items, pull requests, and `REDACTED` items contribute nothing | the poll filters on `item.type == "ISSUE"` and a non-null `content` | edge cases |

## Interruption

**What happens if it is killed halfway through.** The board read is a read; killed during it,
nothing has changed and the previous snapshot still stands. The write that follows is a single
transaction covering both the `work_items` updates for that repository and the
`repo_projects` upsert, so a process killed mid-write rolls back to the previous snapshot
whole — never half of one board and half of another. Killed between the read and the write,
the snapshot is simply lost and the next poll re-reads it, at a cost of one rate-limit point.

There is no partially-applied ordering to detect on startup because the ordering is derived
from the rows at read time and stored nowhere else.
