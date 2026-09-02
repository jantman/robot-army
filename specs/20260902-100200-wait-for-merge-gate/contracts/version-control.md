# Contract: `VersionControl.fast_forward`

```python
def fast_forward(self, clone_path: str, remote: str, branch: str) -> FastForwardResult: ...
```

Advance the clone's **local** `branch` to `remote/branch`, or decline and say why. Called by
`worktree.prepare` after its fetch, only for repositories with `wait_for_merge` in force.

## Outcomes

| `outcome` | Meaning | `reason` | `before` / `after` |
|---|---|---|---|
| `updated` | the branch moved | `None` | both set, `before != after` |
| `already_current` | the branch was already at the remote head | `None` | both set and equal |
| `skipped` | a precondition failed; nothing was attempted | the specific precondition | `before` if readable |
| `failed` | git was invoked and refused or errored | git's message | `before` if readable |

Four outcomes rather than a boolean, for the reason `remote_branch_head`'s docstring gives
about its own three answers: *declined, and here is why* and *did nothing* are different
facts, and only the first tells the author why their clone is still behind.

## Preconditions, checked in this order

Each failing check yields `skipped` with that check's reason, and **nothing is attempted**:

1. `remote` is configured — otherwise there is nothing to fast-forward to.
2. `HEAD` is a symbolic ref to `branch`. A detached `HEAD`, an interrupted rebase, or the
   author simply working on another branch all fail here, and the reason names the branch
   actually checked out.
3. The working tree is clean — `git status --porcelain` is empty, untracked files included.
   This is the check that protects uncommitted work.
4. No operation is in progress — none of `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`,
   `rebase-merge`, `rebase-apply`, `BISECT_LOG` exists in the git directory.
5. `remote/branch` resolves. If the fetch produced nothing to advance to, there is nothing to
   do.
6. `branch` is an ancestor of `remote/branch` — `git merge-base --is-ancestor`. A diverged
   local branch is skipped, never rebased and never reset.

If every check passes and the two shas are equal, the result is `already_current` and git is
not invoked to move anything.

## The update

`git merge --ff-only <remote>/<branch>`, run in the clone. `--ff-only` is passed even though
check 6 has already established the fast-forward is possible: it is the last line of defence,
and its refusal is caught as `failed` rather than as a crash.

## Guarantees

- **Never forces.** No `--force`, no `reset --hard`, no `update-ref` on a checked-out branch.
- **Never discards a commit.** Check 6 makes divergence a skip.
- **Never touches a file the author changed.** Check 3 makes a dirty tree a skip, and
  `--ff-only` on a clean tree changes only files the merge brings in.
- **Never fails a dispatch.** Every outcome, including `failed`, is recorded and returned; the
  caller proceeds. The session's worktree is created from `remote/branch` regardless (FR-019).
- **Only for repositories that asked.** A repository without `wait_for_merge` in force never
  has this called on it (FR-020).

## The simulated implementation

`SimulatedVersionControl.fast_forward` logs the call and returns
`FastForwardResult(outcome="skipped", reason="simulated boundary makes no change")`. It writes
nothing, consistent with every other verb on that class.
