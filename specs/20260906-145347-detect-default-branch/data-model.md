# Phase 1 data model

No table, no column, no migration. Everything below is in-process, computed at each use from
two inputs that already exist: the configuration file and the clone on disk.

## `BaseRef` (new, `repos.py`)

Frozen, slotted, like `RepoIdentity` and `Verification` beside it.

| Field | Type | Meaning |
|---|---|---|
| `ref` | `str` | the branch new work is based on and compared against. Never empty |
| `source` | `str` | which rule decided: `repo_config`, `detected`, `worker_config`, `default`. The token that reaches the audit record |
| `detail` | `str` | the same answer as a sentence, for a screen: `[repos."x/y"] base_branch`, `detected from origin/HEAD`, `[worker] base_branch; origin/HEAD is not set`, `the default; origin/HEAD is not set` |

`source` and `detail` are both kept for the reason `Verification` keeps `cause` beside
`refusal`: one is counted later, the other is read now, and deriving either from the other at
each call site is how they come to disagree.

### Resolution order

1. `[repos."<key>"] base_branch`, when the section states it → `repo_config`
2. the clone's `refs/remotes/<remote>/HEAD` → `detected`
3. `[worker] base_branch`, when the file states it → `worker_config`
4. `"main"` → `default`

Step 2 is skipped, not failed, when the clone has no remote, when the ref is absent, or when
git cannot answer. Steps 3 and 4 then say so in `detail`, because a fallback the reader cannot
see is indistinguishable from a detection that agreed.

## Changed meanings of existing fields

| Field | Was | Is |
|---|---|---|
| `RepoConfig.base_branch` (section form) | the section's value, or `worker.base_branch` copied in at parse time | the section's value, or `""` meaning *not stated* |
| `WorkerConfig.base_branch` | `"main"` by default | `""` by default, meaning *not stated*; `"main"` is applied by rule 4 above |
| `Config.base_branch_for` | the section value or the worker value | **removed.** Its callers resolve, which needs a clone it never had |

`RepoConfig.base_branch` in the *resolved* form (`repos.resolve`) is unchanged in type and
keeps carrying the section's answer, which is what makes rule 1 above readable from it.

## What is deliberately not stored

The detected branch. A stored copy would be the first cached property of the clone in this
system — trust, the remote URL, and the settings fingerprint are all re-read at every use — and
it could disagree with the clone it was copied from without anything noticing. See research R2.
