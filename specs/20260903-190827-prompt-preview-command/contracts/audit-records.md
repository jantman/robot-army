# Contract: what a preview writes to the log

One `prompt.preview` record per invocation, on every path, plus the `speckit.detect` record
`dispatch.speckit_block` already writes when it gets far enough to run. See
[R3](../research.md) for the keying and [R4](../research.md) for the two justified gaps.

## `prompt.preview`

Written by `operations.prompt_preview`, component `cli`.

| Field | Value |
|---|---|
| `action` | `prompt.preview` |
| `outcome` | `ok` on exit `0`, `error` on every other exit |
| `entity_type` | `issue` |
| `entity_id` | `<owner>/<repo>#<number>` — the same key shape `poll.rejected` uses |

`detail` on success:

```json
{
  "repo_key": "owner/repo",
  "issue_number": 42,
  "branch": "robot-army/issue-42-add-a-thing",
  "branch_source": "recorded" | "derived",
  "context_root": "/home/…/worktrees/repo/issue-42",
  "context_source": "worktree" | "clone" | "none",
  "item_id": 17,
  "instructions": true,
  "speckit": false
}
```

`item_id` is omitted when no work item row exists. `instructions` and `speckit` are booleans
saying whether each optional section was included — **never the text of either**, for the
reason [R4](../research.md) gives.

`detail` on failure carries `refused: true` and a `cause` naming which of the four conditions
in [cli.md](cli.md) applied, with whatever of the fields above had been resolved by then. A
malformed slug has no `repo_key` and no `issue_number`; `entity_id` is the raw argument pair
so the record still says what was asked for.

## `speckit.detect`

Unchanged in shape from dispatch, except for its key when there is no work item
([R3](../research.md)):

| Caller | `entity_type` | `entity_id` |
|---|---|---|
| dispatch, and a preview of a tracked issue | `work_item` | the item id |
| a preview of an issue with no row | `repo` | `repo_key` |

`component` distinguishes the two callers for a tracked issue: `daemon` for a dispatch, `cli`
for a preview.

## Not written

- The composed prompt text, the issue body, the repository's instructions, and the Spec Kit
  block's text. Enumerated and justified in [R4](../research.md).
- A successful `GET /repos/{owner}/{repo}/issues/{n}`. Failures and retries are logged by
  `GitHubReader._request` as they always were; the `prompt.preview` record covers the ask
  and the outcome.
