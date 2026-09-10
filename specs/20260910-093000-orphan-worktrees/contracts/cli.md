# Contract: the three commands, and the anomaly

## `robot-army purge-simulated [--yes] [--remove-worktrees] [--json]`

1. Counts simulated rows. None → `no simulated rows to purge`, exit 0, nothing asked.
2. Finds simulated work items whose `worktree_path` is an existing directory ("offered").
3. Row question (unless `--yes`), listing each offered worktree:

   ```
   Delete 3 simulated work item(s), 2 simulated session(s) and 0 simulated card(s)?
     these rows own 2 worktree(s) still on disk:
       /home/me/worktrees/demo/issue-24  robot-army/24-fix-thing   80.1 MB
       /home/me/worktrees/demo/issue-25  robot-army/25-other        1.2 MB
   [y/N]
   ```

   Anything but `y`/`yes` → `aborted`, exit 1, nothing removed or deleted.
4. Worktree question, only when something is offered and neither flag answers it:
   `Also remove those 2 worktree(s) and their branches? [y/N]`. `--remove-worktrees` answers
   yes; `--yes` alone answers no without asking (research R10).
5. If yes: each offered worktree is removed through the shared core, one `worktree.remove`
   pair each, **before** any row is deleted. Session guard: open sessions other than
   simulation-hosted ones refuse (R2). Git's dirty refusal stands; nothing is forced.
6. Rows are deleted in one transaction, as today.
7. Output: the purge counts; `removed worktree <path>` / `deleted branch <b>` per removal; then,
   for every offered directory still on disk:

   ```
   left on disk: /home/me/worktrees/demo/issue-25 — <why>
     remove it with: robot-army worktree remove /home/me/worktrees/demo/issue-25
   ```

   Exit 0 when the purge happened and every accepted removal succeeded; 1 (`EXIT_FAILED`) when
   the rows were purged but an accepted removal was refused, so a script notices disk it
   expected back.

JSON `data`: `purged` (counts, as today), `worktrees_offered`, `worktrees_removed`,
`worktrees_left` (lists of paths), `remove_worktrees` (bool).

## `robot-army worktree remove TARGET [--force] [--json]`

`TARGET` all digits → the item-id form, unchanged except R3 (a directory that survives a
reported removal is a refusal). Otherwise → the path form:

| Check, in order | Refusal (`refused_by`) | Exit |
|---|---|---|
| inside `worktree_root` | `outside_root` | 3 (`EXIT_PRECONDITION`) |
| no work item claims it | `claimed` — "use `robot-army worktree remove <id>`" | 3 |
| is a directory | `not_a_directory` — "if git still records it, `robot-army worktree prune`" | 1 |
| an onboarded clone lists it | `not_a_worktree` | 3 |
| no live worker inside it (unless `--force`) | `live_worker` | 3 |
| git removes it (unless `--force`) | `git` | 1 |
| the directory is then gone | `directory_survived` | 1 |

`--force` prompts `Type the directory name (issue-29) to force-remove <path> ...:`; any other
answer → `aborted`. The branch git listed is deleted after the worktree, as in the item-id form;
a surviving branch is the same `WARNING`, exit 1.

JSON `data`: `path`, `worktree_removed`, `branch_deleted`, `refused_by`, `refused_reason`,
`branch`, `repo_key`.

## `robot-army worktree list [--include-simulated] [--json]`

Claimed rows as today, then orphans: item `—`, path, branch or `—`, condition `unclaimed`,
size, cleanup `—`. "no worktrees recorded" only when there are neither. JSON entries gain
`claimed`; an orphan has `item_id: null`, `simulated: null`, `cleanup_state: null`.

## `orphan_worktree` anomaly

See data-model.md. Listed by `anomalies`, `status`, and `/anomalies` through the existing
enumeration of `ANOMALY_KINDS`. Resolves itself.
