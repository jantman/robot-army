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

`recorded_worktree` appears only when the item recorded a worktree that was **not** the
directory read — i.e. it has been reclaimed. Without it the record says `clone` and a reader
cannot tell a reclaimed worktree from an issue that never had one, which is the same
distinction the stderr note draws.

`item_id` is omitted when no work item row exists. `instructions` and `speckit` are booleans
saying whether each optional section was included — **never the text of either**, for the
reason [R4](../research.md) gives.

`detail` on failure carries `refused: true` and a `cause` naming which of the four conditions
in [cli.md](cli.md) applied, plus every field above that had been resolved by the time it
refused. "Whatever had been resolved" proved too loose to check against — the first
implementation carried the pair on the transport failures only, and the divergence was caught
in review rather than by a test — so the rule is now stated per cause:

| `cause` | Fields carried beyond `refused` and `cause` |
|---|---|
| `malformed_arguments` (bad key) | none — there is no repository key, which is the fault being reported |
| `malformed_arguments` (bad number) | `repo_key`, `issue_number` |
| `not_onboarded` | `repo_key`, `issue_number` |
| `issue_unavailable` | `repo_key`, `issue_number`, and `error` when a boundary raised |

`entity_id` carries the raw argument pair in every case, including the malformed key, so the
record always says what was asked for. It is an identifier, though, not a pair of fields:
reconstruction must never require splitting it on `#`, which is why the fields are recorded
separately wherever they exist.

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
