# Phase 1 Data Model: Containment Proved From the Remote, Not From a Stale Ref

**Feature**: `specs/20260901-175718-containment-from-remote` | **Date**: 2026-09-01

## No migration

`SCHEMA_VERSION` is unchanged, and no column changes meaning. This feature changes *where an answer
is read from*, not what is stored. Everything it needs is already on the record:

| Column | Table | Written by | Used here for |
|---|---|---|---|
| `branch` | `work_items` | `worktree.prepare` | the branch whose containment is in question |
| `worktree_path` | `work_items` | `worktree.prepare` | unchanged; the worktree guard's subject |
| `repo_key` | `work_items` | intake | resolving the clone the question is asked in |
| `cleanup_state` | `work_items` | `db.record_cleanup` | unchanged: `done`, `branch_retained`, `retained`, `skipped` |
| `cleanup_reason` | `work_items` | `db.record_cleanup` | **the evidence string — gains new values, no new format** |
| `cleaned_at` | `work_items` | `db.record_cleanup` | unchanged |

The four cleanup outcome values are unchanged. A branch kept because the remote no longer has it is
`branch_retained`, the outcome that already means "worktree reclaimed, branch kept, containment
unproven" — it is a new *reason*, not a new *state*, and inventing a fifth outcome for it would
change what every existing reader of `cleanup_state` has to handle for no gain.

## New boundary read

### `VersionControl.remote_branch_head(clone_path, remote, branch) -> str | None`

Asks the remote, right now, what it has at `refs/heads/<branch>`.

| Answer | Meaning | Source |
|---|---|---|
| a sha string | the remote has the branch, there | `ls-remote` exit 0, one matching line |
| `None` | the remote answered, and does not have the branch | `ls-remote` exit 0, no matching line |
| raises `BoundaryError` | the remote could not be asked | `ls-remote` non-zero, or timeout |

The tri-state is the point. Two of these keep the branch and one of them can prove it publishable,
and collapsing "does not have it" into "could not ask" would lose the distinction FR-007 requires
in the recorded reason. Declared on the `VersionControl` protocol beside `commits_ahead`, whose
docstring already explains at length why a read that folds two meanings into one value is how this
module gets branch deletion wrong.

Timeout: `FETCH_TIMEOUT`, not `QUICK_TIMEOUT` — it is a network call (R7).

**Simulated implementation**: logs `git.ls_remote` and returns `"0" * 40`, matching
`SimulatedVersionControl.rev_parse`, so the simulated chain resolves that sha and answers `0`
commits ahead and reaches `done` exactly as it does today (R8).

## New in-process shape

### `cleanup._pushed_to_remote(vcs, *, clone, remote, branch) -> tuple[bool, str]`

The second containment test, extracted from `_branch_is_contained` so that each of its five
outcomes is a visible `return` rather than a fallthrough:

| Condition | Returns |
|---|---|
| `remote_branch_head` raised | `(False, "could not ask <remote> whether <branch> is published (<err>)")` |
| `remote_branch_head` returned `None` | `(False, "<remote> does not have <branch>")` |
| the sha does not resolve in this clone | `(False, "<remote>/<branch> is at <sha>, which this clone does not have")` |
| `commits_ahead` returned `None` | `(False, "git could not compare <branch> with <sha>")` |
| `commits_ahead` returned `n > 0` | `(False, "<n> commit(s) on <branch> are not on <remote> at <sha>")` |
| `commits_ahead` returned `0` | `(True, "every commit is on <remote>/<branch>, which is at <sha> now")` |

Only the last authorises a delete. The sha appears in both the proving and the refusing sentences
because it is the whole substance of the fix: the record has to show *which* commit the remote
reported at the moment of the decision, not merely that a ref name was consulted (FR-008, US3).

### `_branch_is_contained`, after

Unchanged in signature, in its base-branch fetch, and in its base failure rule (FR-005). Its body
becomes: fetch the base; ask the base test; if that fails, ask `_pushed_to_remote`; combine the two
evidence strings for the retention reason so a reader can see both answers rather than one.

## Nothing else moves

- `eligible`, `live_sessions`, `LIVE_SESSION_STATES` and the `skipped` outcome: untouched (FR-010).
- The worktree guard, including `force=False` and the already-missing-directory recovery: untouched.
- `delete_branch(force=True)`: unchanged. What changes is what has to be true before it is reached.
- `worktree.condition`'s use of `commits_ahead`, which maps `None` to `0` deliberately for a
  display signal: untouched, and unaffected — this feature adds no caller to it.
