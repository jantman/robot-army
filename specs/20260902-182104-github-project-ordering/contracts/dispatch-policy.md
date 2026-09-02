# Contract: Ordering and the new hold

**Feature**: [../spec.md](../spec.md) | **Research**: [../research.md](../research.md) R10, R11

`ordering.plan` remains the only producer of dispatch order, remains pure, and continues to do
no I/O beyond reading the database. Everything below is computed from rows the poll already
wrote.

## The permutation

`plan` keeps its existing sort **verbatim**, then permutes within each governed repository:

```
1. items = sorted(ready_items, key=order_key(...))          # unchanged
2. for each repo with a board (last_read_at is not None):
       slots = [i for i, item in enumerate(items) if item.repo_key == repo]
       mine  = sorted((items[i] for i in slots), key=board_key)
       for i, item in zip(slots, mine): items[i] = item
```

```python
def board_key(item: WorkItem) -> tuple[Any, ...]:
    if item.board_position is not None:
        return (0, item.board_position, item.id)
    return (1, item.discovered_at, item.id)
```

Two properties this shape guarantees, and a new sort key would not:

- **FR-002**: the *slots* a repository occupies are computed before the permutation and
  written back unchanged, so the global order mode decides how repositories interleave exactly
  as it does today. `repo-priority` keeps its meaning; `oldest-first` keeps its meaning.
- **FR-007**: `board_key` is total — every branch ends in `item.id` — so two renders of
  unchanged state produce the same list and the queue does not shuffle.

The `(0, …)` / `(1, …)` split is FR-008: items the board ranked come first, in board order;
items the board does not mention follow, in the order they would have had anyway.

Cost is one extra pass over a list of tens of rows plus one `db.list_repo_projects` scan per
plan — resolved once for the whole plan, the way `repos.resolved_all` and `unfinished_by_repo`
already are, rather than once per queued item.

## `HoldReason.OFF_COLUMN`

```python
PAUSED
CAPACITY_UNOBSERVABLE
GLOBAL_CAP
REPO_CAP
AWAITING_MERGE
NOT_ONBOARDED
OFF_COLUMN          # new
PREPARATION_FAILED
```

Declaration order **is** the precedence. R11 argues the placement; the short form is that a
missing clone blocks everything and so outranks it, while parking a card is a more recent and
more deliberate statement by the author than residue from an attempt they have since stepped
back from, so it outranks that.

**Applies when, and only when**, all of these hold:

1. `effective_project_ordering(repo)` is true;
2. `repo_projects.last_read_at` is not NULL — no board knowledge, no gate (FR-014);
3. `item.board_column` is not NULL and does not equal the resolved dispatch column.

An item with `board_column` NULL under a read board is **not** held. It is not on the board,
the board expresses no opinion about it, and FR-008 orders it after everything the board
ranked.

**Detail text**: `repository jantman/robot-army: #48 is in 'Backlog', not the dispatch column
'Ready' — move it there, or set project_ordering = false for this repository`. One sentence,
naming the column it is in, the column it needs to be in, and both ways out.

## Reporting

| Surface | Addition |
|---|---|
| `robot-army status` | `queue[].hold` gains `off_column`; a new `projects` list, one row per repository — project, column, each one's source, `last_read_at`, its age, `unresolved_reason`, and how many of its items are held off-column |
| `robot-army capacity` | `_repo_settings` rows gain `project_ordering` and `project_explicit`, beside `cap`/`cap_explicit`, following that function's established pattern |
| `robot-army doctor` | the `project: *` checks in [config.md](config.md) |
| web `/queue` | the ready table renders the new reason through the existing `hold_detail` cell — no new column. The heading gains a count: `ready (12) — in dispatch order · 30 held off-column`, which is FR-030: a repository whose whole backlog is parked must not read as a repository with no work |
| web chrome | nothing. `chrome` runs on every page render and its cost is already argued down to one capacity snapshot; a board line there would put a per-repository scan on every request to say something `/queue` already says |

`status`'s `projects` list is built from `db.list_repo_projects` plus config resolution, in the
same place and the same shape as `_repo_settings` — one function, no network, callable with the
board unreachable, which is the whole point of storing the resolution rather than deriving it.

## Where the board is read

Inside `poll_repo`, **after** the per-issue loop and **before** the final `save_poll_state`
transaction. That position is deliberate: items discovered in this very pass already have rows
by then, so they receive their board facts in the same pass rather than spending one cycle
misclassified as "not on the board".

The write is one transaction per repository covering both the `work_items` updates and the
`repo_projects` upsert, so a killed process rolls back to the previous snapshot whole.

Skipped when: `project_ordering` is off for the repository, the repository is not onboarded,
or `repo_projects.backoff_until` is in the future. A skip for backoff is recorded, not silent.

Failure handling mirrors `poll_repo`'s existing shape exactly — `consecutive_failures + 1`,
`backoff = min(2 ** failures, 900)`, the state saved in its own transaction, the error recorded
outside it — and leaves the previous snapshot in force and marked stale rather than clearing it
(FR-025).
