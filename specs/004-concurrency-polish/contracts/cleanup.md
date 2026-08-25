# Contract: Worktree Cleanup

Two guards, two steps, four outcomes. The guards are different from each other, and assuming otherwise
is how this goes wrong (R12).

## Trigger

`reconcile._cleanup_worktrees`, immediately after `_resolve_closed_issues` in the same pass (R10).
Runs only when `[cleanup] on_issue_close = true`, which defaults to `false` — the Operating
Constraints require irreversible actions to be unreachable by default.

**Eligible**: `state == done`, `worktree_path` is set, `cleanup_state` is `NULL` or `skipped`, and no
live session for the item.

**Not eligible, and recorded as such**: a live session (`skipped`, FR-027); an item in any other
state; an item already `done`, `retained`, or `branch_retained`.

`robot-army cleanup [<item-id>]` runs the same function under the same guards (FR-029), and does so
whether or not the automatic path is enabled.

## Guard 1 — the worktree: git's refusal, taken as-is

```
vcs.remove_worktree(worktree_path, force=False, clone_path=clone)
```

`force` is never passed. `git worktree remove` refuses on a dirty tree, **including merely untracked
files**, and `boundaries/git.py` already returns that refusal as a `RemovalResult` rather than raising
— an expected and useful outcome, not an error.

Refused → `cleanup_state = retained`, `cleanup_reason` = git's own message, item surfaced. The branch
half is not attempted: a dirty worktree means the branch may hold the only copy of something.

Never retried in a way that would eventually force it (FR-025). `retained` is a decision, and only the
explicit command reconsiders it.

## Guard 2 — the branch: our own containment check

Git's branch guard is the **wrong** guard here. `git branch -d` accepts only a branch merged into the
clone's current `HEAD`, or into its upstream if one is set. The normal case is a PR merged on GitHub
while the author's clone has a stale `main` checked out and the robot branch has no upstream — so `-d`
refuses every time and `robot-army/*` branches accumulate in every repository, which is the exact
failure planning §6 warns about.

```
remote = vcs.default_remote(clone) or "origin"
vcs.fetch(clone, remote, base_branch)              # or the check reads a stale ref

contained = vcs.commits_ahead(clone, f"{remote}/{base_branch}", branch) == 0
pushed    = vcs.commits_ahead(clone, f"{remote}/{branch}",      branch) == 0

if contained or pushed:
    vcs.delete_branch(clone, branch, force=True)   # see below
else:
    cleanup_state = "branch_retained"
```

**`force=True` here does not mean "skip the guard".** It means a stronger guard than git's has already
passed: every commit on the branch is provably contained in a ref that lives on the remote. This is
worth stating loudly because `force` reads as danger everywhere else in this codebase, and because the
inverse mistake — passing `-D` without the check — silently destroys unpushed work.

**`commits_ahead` returning `None` is "could not determine" and never satisfies either test** (R11).
Its previous `return 0` on failure meant "no information" to the resume-signal caller and "safe to
delete" to this one; the signature change is what keeps those two readings apart.

## Outcomes

| `cleanup_state` | Worktree | Branch | Written when |
|---|---|---|---|
| `done` | removed | removed | Both guards passed |
| `branch_retained` | removed | kept | Worktree clean, containment unproven or undeterminable |
| `retained` | kept | kept | Git refused the worktree |
| `skipped` | kept | kept | A session was still live |

Every removal is an `audit.action` pair written **before** the attempt and again with its outcome
(FR-028). `cleanup.considered` records the decision even when nothing is removed, so "why is this
499 MB still here?" is answerable from the log alone.

## Boundaries and limits

- Cleanup touches only paths under `config.worktree_root` (FR-031). The author's own clone is read for
  `fetch`, `rev-list`, and `branch -D`, and is never removed from.
- A worktree whose directory has vanished is not a cleanup failure: `_sweep_worktrees` already raises
  `prunable_worktree`, and `robot-army worktree prune` clears git's record (FR-030).
- Effect levels follow worktree *creation*, not board writes (FR-039): simulated at `plan`, real at
  `local` and above. The simulated `VersionControl` already logs both calls and returns structurally
  valid results.
