# Contract: `robot-army reset`

**Feature**: [../spec.md](../spec.md) · **Date**: 2026-09-18

## Synopsis

```text
robot-army reset <item-id> [--force] [--yes]
```

Discard a work item's checkout and branch, re-read its issue from GitHub, re-check that it is
still eligible, and put it back in the queue.

`--force`  override git's refusal over uncommitted or untracked work in the checkout.
           Faces `worktree remove`'s typed-item-id prompt.
`--yes`    skip reset's own confirmation. Does **not** imply `--force` and never overrides git.

## Sequence

Each step runs only if the one before it passed. The destruction is last but one, deliberately:
nothing is deleted until the item is known to be requeueable.

1. **The item exists.** Otherwise exit `EXIT_FAILED` — `no work item with id <id>`.
2. **The state is accepted.** `interrupted`, `awaiting_review`, `failed`. Otherwise exit
   `EXIT_PRECONDITION`, naming the state and, where one exists, the command to reach for
   instead (`cancel` for `active`; nothing for `done` and `abandoned`).
3. **Local conditions.** The repository resolves to a clone, and `dispatch.check_gates` passes.
   A refusal records `reset.blocked` and exits `EXIT_PRECONDITION` without spending a read.
4. **The read.** `issue_reader.get_issue(repo_key, issue_number)`. Never falls back to the
   stored copy. A transport failure records `reset.evaluate` with `cause: issue_unreachable`;
   an absent or invisible issue with `cause: issue_absent`. Both exit `EXIT_FAILED`.
5. **The refresh.** `title`, `body`, `labels` and `author` are written from the read, in one
   transaction, before the verdict is consulted.
6. **The verdict.** `poll.evaluate(issue, …)` — the poller's own function, author check
   included. Ineligible: the reason is written to `blocked_reason` and `failure_reason`,
   `reset.evaluate` records `eligible: false`, and it exits `EXIT_PRECONDITION`. **Nothing has
   been destroyed at this point.**
7. **The confirmation.** Without `--force` and without `--yes`, reset asks
   `Discard <path> and branch <branch>, and put item <id> back in the queue? [y/N] `. Any answer
   but `y`/`yes` exits `EXIT_FAILED` with `aborted`. With `--force`, `worktree remove`'s
   typed-item-id prompt in step 8 is the question instead, and reset asks nothing.
8. **The discard.** `worktree_remove(ctx, item_id, force=force)`, whole, with all four of its
   guards. Skipped entirely when the item has no `worktree_path` — a reset does not fail for
   want of something to delete. The removal clears `worktree_path`, since the item is not
   terminal.
9. **The requeue.** `transition_work_item(…, target=READY, extra_columns={"failure_reason":
   None, "blocked_reason": None})`, in one transaction with its audit record.

## Refusals

| Condition | Exit | Message names |
|---|---|---|
| no such item | `EXIT_FAILED` | the id |
| state not accepted | `EXIT_PRECONDITION` | the state, and `cancel` when the item is `active` |
| repository does not resolve | `EXIT_PRECONDITION` | `robot-army onboard <key> --reapprove` |
| a dispatch gate still blocks it | `EXIT_PRECONDITION` | the blocker's own sentence |
| issue unreadable / absent | `EXIT_FAILED` | which of the two, and the error |
| issue not eligible | `EXIT_PRECONDITION` | the verdict's reason |
| confirmation declined or abandoned | `EXIT_FAILED` / `EXIT_CHECK_FAILED` | `aborted`, or that input ended |
| a session row is still open | `EXIT_PRECONDITION` | the session, and `robot-army cancel <id>` |
| git refuses over uncommitted work | `EXIT_FAILED` | that `--force` overrides it |
| a removal is already on record | `EXIT_PRECONDITION` | the cleanup record and when |

A refusal at any step leaves the item in the state it was in. Steps 5 and 6 are the exception
and are not a refusal's doing: the content columns reflect the read that did happen, which is
`retry`'s existing and deliberate behaviour.

## Exit codes

`0` on success — including when the worktree was removed but git kept an unmerged branch, which
is reported as a `WARNING` line. Reset treats "the worktree is gone" as the question, not the
removal's exit code, because that case sets a non-zero code with the destruction already done.

Non-zero as tabulated above. The command exits non-zero on every refusal.

## Simulation

Below the effect level at which version control is real, the removal reports what it *would*
do and touches nothing, naming the level that would make it real — `worktree remove`'s existing
wording, reached through the same code. The state change and the refresh still happen, on a row
that is already a dry-run row.

## `--json`

`data` carries: `item_id`, `state` (before), `eligible`, `worktree_removed`, `branch_deleted`,
`requeued`, and on a refusal `refused_by` with the reason. Prompts are written to stderr, so a
`--json` document is never interleaved with a question.
