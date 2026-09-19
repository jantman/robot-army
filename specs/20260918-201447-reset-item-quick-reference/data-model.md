# Data Model: reset

**Feature**: [spec.md](spec.md) · **Date**: 2026-09-18

No table is added and no column is added. Reset is a new path through the existing model, and
this file records which parts of it the path touches and what it promises about each.

## `work_items` — the columns reset writes

| Column | Written by | Value after a successful reset |
|---|---|---|
| `title` | the refresh | the issue's title as just read |
| `body` | the refresh | the issue's body as just read |
| `labels` | the refresh | the issue's labels as just read, serialised |
| `author` | the refresh | the issue's author as just read |
| `worktree_path` | `worktree_remove` | `NULL` — so the next dispatch builds a fresh checkout |
| `state` | `transition_work_item` | `ready` |
| `ready_at` | `transition_work_item` | now |
| `updated_at` | `transition_work_item` | now |
| `failure_reason` | the transition's `extra_columns` | `NULL` |
| `blocked_reason` | the transition's `extra_columns` | `NULL` |

On a **refused** reset, the four content columns are still written — the read happened, and the
stored copy should reflect it — and, when the refusal is an ineligible verdict, `blocked_reason`
and `failure_reason` are written with that verdict's reason. This is `retry`'s existing
behaviour, reached through the same code.

### Columns reset deliberately does **not** write

| Column | Why not |
|---|---|
| `discovered_at` | when the issue was found is a fact; rewriting it to move the item's place in the queue would falsify a record to get an ordering effect (research R6) |
| `branch` | the branch name is derived from the repo and issue number and is rebuilt identically; the *branch* is deleted, its name on the row is not a lie |
| `cleanup_state`, `cleanup_reason`, `cleaned_at` | reset applies only to unfinished items, and `worktree_remove` writes a cleanup record only for terminal ones |
| `dry_run` | a row created under simulation stays simulated for the whole of its life |

## `sessions` — read, never written

Reset reads session rows twice and writes none:

- `cleanup.live_sessions(conn, item_id)`, inside `worktree_remove`, decides whether anything is
  still open in the checkout. A row nothing has closed is a refusal, whether or not its process
  can be seen.
- The rows survive the reset untouched. FR-009 is a promise about *records*: "discard the
  session history" means the next dispatch carries no prior session's context — which it does
  not, because a `ready` item is dispatched fresh — and never that rows are deleted. Deleting
  them would be a Principle III violation dressed as a feature.

## State machine

Two transitions are added to `WORK_ITEM_TRANSITIONS`:

```text
interrupted     → ready      (new)
awaiting_review → ready      (new)
failed          → ready      (already legal; retry's transition)
```

Which makes `ready` reachable from four states. The full item machine afterwards:

| From | To |
|---|---|
| `discovered` | `ready`, `failed` |
| `ready` | `dispatching`, `abandoned` |
| `dispatching` | `active`, `failed` |
| `active` | `awaiting_review`, `failed`, `interrupted`, `done` |
| `awaiting_review` | `done`, `dispatching`, `abandoned`, **`ready`** |
| `interrupted` | `dispatching`, `done`, `abandoned`, **`ready`** |
| `failed` | `ready`, `abandoned` |
| `done` | — terminal |
| `abandoned` | — terminal |

Terminal states stay terminal: reset is refused from `done` and `abandoned`, and from `active`
and the two in-flight states, by its own gate before the state machine is asked.

## Audit records

Three names are added, all following the existing dotted convention. The first two are the
shared read-refresh-evaluate helper's records under reset's verb; the third is the pair around
the whole operation.

| Action | Kind | When | Detail |
|---|---|---|---|
| `reset.blocked` | event, `outcome: error` | a local condition refused it before any network read | `repo_key`, `blocked` — the blocker's sentence |
| `reset.evaluate` | event | after the live read | `repo_key`, `issue_number`, `eligible`, `reason`, `author`, `refreshed` (the columns written); or, when the read itself failed, `cause` = `issue_unreachable` \| `issue_absent` and `error` |
| `reset` | intent + outcome pair | around the whole operation | intent before anything: `item_id`, `force`. Outcome: `worktree_removed`, `branch_deleted`, `requeued`, and `refused_by` when it stopped |

The removal step keeps writing its own `worktree.remove` intent/outcome pair, under its own
name, because a removal is what happened. A reset is therefore reconstructible from the log as:
`reset` intent → `reset.evaluate` → `worktree.remove` intent/outcome → `state.work_item` →
`reset` outcome.

`docs/guide/audit-log.md` gains these three, per the repository's rule that a new audit action
is documented with the record's shape.
