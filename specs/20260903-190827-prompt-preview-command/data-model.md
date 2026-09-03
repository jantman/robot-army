# Data Model: Prompt Preview Command

**No schema change.** No migration, no new table, no new column, no new configuration key.
This feature reads existing state and existing files and prints a string. `SCHEMA_VERSION`
is untouched.

What follows is the in-memory resolution the operation performs — the entities are all
existing ones, and the value of this document is the *rules* by which each input is chosen.

## Inputs, and how each is resolved

| Input | Type | Source | When it is absent |
|---|---|---|---|
| `repo_key` | `str` | positional argument | — |
| `issue_number` | `int` | positional argument | — |
| repository | `RepoConfig` | `repos.resolve(conn, config, repo_key)` | `None` means **not onboarded** — refuse, exit `3` |
| issue | `Issue` | `boundaries.issue_reader.get_issue(repo_key, number)` | `None` (HTTP 404) means unavailable — refuse, exit `1` |
| work item | `WorkItem \| None` | `db.find_work_item(source="github", source_id=f"{repo_key}#{number}", dry_run=False)` | `None` is ordinary: the issue has never been dispatched |
| context root | `Path \| None` | see below | `None` means no instructions and no Spec Kit block |
| branch | `str` | see below | never absent |

### Context root

The directory `.claude/robot-army.md` and `speckit.detect` are read from ([R1](research.md)):

1. `Path(item.worktree_path)` when the work item exists, records a worktree path, and that
   path is a directory that exists.
2. Otherwise `repository.path` — the onboarded clone — when that is a directory that exists.
3. Otherwise `None`.

Each of the three is reported on the diagnostic stream by a note naming the path and which
case it is ([R2](research.md)), and each is recorded in the `prompt.preview` audit record.

### Branch

1. `item.branch` when the work item exists and records one.
2. Otherwise `prompt.branch_name(config.worker.branch_prefix, issue_number, issue.title)` —
   the same derivation `worktree.prepare` performs.

## Output

| Field | Where it goes | Contents |
|---|---|---|
| prompt | `Result.lines`, hence stdout on success | the single string `prompt.compose` returns |
| notes | the caller's stream, hence stderr; also `Result.data["notes"]` | one line naming the context root, plus any warning |
| payload | `Result.data` | `repo_key`, `issue_number`, `branch`, `context_root`, `context_source`, `item_id`, `instructions` (bool), `speckit` (bool), `prompt` |
| code | `Result.code` | `0`, `1`, `2` or `3` per [contracts/cli.md](contracts/cli.md) |

## Composition

Unchanged from dispatch, and deliberately not re-described here — the section order,
separators and truncation rule live in `prompt.compose` and are held to a golden string by
`tests/unit/test_speckit_prompt.py`. The preview calls that function with the same four
arguments `dispatch.build_launch_plan` passes:

```text
prompt.compose(issue, repo_key=..., branch=..., instructions=..., speckit_block=...)
```

The only difference between the two call sites is where `instructions` and `speckit_block`
were read from, which is the subject of the table above. If the printed text ever differs
from a dispatch's for the same inputs, the defect is in this feature, never in `compose`.

## State transitions

None. The command is read-only: no work item changes state, no session is created, no
branch or worktree is made, and nothing is written to the issue source. The only durable
effect of a run is its own audit record ([contracts/audit-records.md](contracts/audit-records.md)).
