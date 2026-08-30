# Data Model: Say on the issue which machine and which session picked it up

**No schema change. `SCHEMA_VERSION` does not move. No migration.** Every value the comment
publishes either already exists in a column, is already a local variable at the call site, or is
read from the kernel.

## Where each published fact comes from

| Fact | Origin | Already stored? |
|---|---|---|
| Host | `os.uname().nodename`, via the new `dispatch.host_name()` | No, and no column is added — it is written into the `dispatch.confirmed` audit detail instead (research R5) |
| Session name | `plan.title`, from `prompt.session_name(repo_key, issue_number)` | No column. Deterministic from `work_items.repo_key` + `work_items.issue_number` |
| Session id | `sessions.session_id` | Yes |
| Attempt | `sessions.attempt`, assigned by `db.next_attempt` before launch | Yes |
| Predecessor | `resume_session_id` (a resume), else `sessions.session_id` of the highest attempt below ours | Yes |
| Branch | `work_items.branch` (local `branch`) | Yes |
| Worktree | `work_items.worktree_path` (local `worktree_path`) | Yes |
| Reason (failure variant) | The failure string already passed to `_comment_failure` | In `work_items.failure_reason` |

## Entities, as this feature sees them

**Work item** (`work_items`) — the issue. Supplies `repo_key`, `issue_number`, `branch`,
`worktree_path`, and `dry_run` (which only decides how a comment failure is logged; the boundary
itself is chosen by effect level).

**Session** (`sessions`) — one attempt. Read here for `session_id` and `attempt`; `attempt` is
the *only* thing that distinguishes a first dispatch from a reassignment, and it is already
correct because `db.next_attempt` is `MAX(attempt) + 1` over the item's rows.

**Comment** — not persisted by us. It lives on GitHub, and its durable local trace is the
`github.comment` intent/outcome pair plus the `dispatch.confirmed` record that carries the same
facts.

## New read

```sql
SELECT * FROM sessions
 WHERE work_item_id = ? AND attempt < ?
 ORDER BY attempt DESC LIMIT 1
```

Exposed as `db.previous_session_for_item(conn, item_id, attempt)`. The `attempt < ?` bound is
load-bearing: our own row is already inserted by the time the comment is written, so the
existing `db.latest_session_for_item` would return *this* session and produce a comment claiming
a session supersedes itself (research R3).

Returns `None` when there is no earlier row — a rebuilt database, or pruned history — which the
comment renders as "no earlier session is on record" rather than inventing a predecessor
(FR-010).

## Audit records

| Record | Change |
|---|---|
| `dispatch.confirmed` | `detail` gains `host`, `session_name`, `attempt`, and `supersedes` (the last only when a predecessor was found without a resume). `resumed_from` unchanged. |
| `github.comment` | Unchanged. The real writer records an intent/outcome pair; the simulated writer records one `simulated` record carrying the whole intended body. |
| `github.comment` (error) | Unchanged shape, still written by `_safe_comment` when a post fails. |

## State machines

Untouched. No transition is added, removed or reordered, and no comment is written from inside a
transaction.
