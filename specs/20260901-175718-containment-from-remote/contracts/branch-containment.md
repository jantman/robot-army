# Contract: Branch Containment

**Feature**: `specs/20260901-175718-containment-from-remote`
**Governs**: `cleanup._branch_is_contained`, `cleanup._pushed_to_remote`, and
`VersionControl.remote_branch_head`. Supplements — does not replace —
[`specs/004-concurrency-polish/contracts/cleanup.md`](../../004-concurrency-polish/contracts/cleanup.md),
whose outcomes, guards and effect-level rules are unchanged, and
[`specs/20260901-164616-guard-worktree-remove/contracts/worktree-removal.md`](../../20260901-164616-guard-worktree-remove/contracts/worktree-removal.md),
which governs the manual path and is untouched.

## What authorises a branch delete

**C1.** A branch MAY be deleted only when at least one containment test **proved** on evidence
obtained during this check. There are exactly two tests and no third.

**C2. Base containment.** Every commit on the branch is contained in `<remote>/<base>`, where
`<remote>/<base>` was fetched by this check immediately beforehand. Unchanged from today, including
its rule that a failed fetch keeps the branch.

**C3. Published under its own name.** The remote, asked during this check, reports a commit at
`refs/heads/<branch>`, that commit resolves in this clone, and no commit on the local branch is
absent from it.

**C4.** A remote-tracking ref MUST NOT be read as the remote's answer for C3. `<remote>/<branch>`
is a cache written by whatever last pushed or fetched it; the check that authorises an
irreversible delete asks the remote instead.

**C5.** Neither test may be satisfied by a value that means "could not determine". `commits_ahead`
returning `None`, a remote that cannot be reached, and a sha this clone does not hold are each
**unproven**, and unproven keeps the branch.

## The remote read

**C6.** `remote_branch_head(clone, remote, branch)` returns the sha the remote reports at
`refs/heads/<branch>`; returns `None` when the remote answered and has no such ref; and raises when
the remote could not be asked. These three MUST stay distinguishable — C7 and FR-007 both depend on
telling "does not have it" from "could not ask".

**C7.** The reason recorded for a retention MUST say which of the following happened: the remote
does not have the branch; the remote has it at a commit this clone does not hold; commits exist on
the branch that are not on it; the remote could not be asked. A single "containment unproven"
covering all four is a contract violation.

**C8.** The read MUST NOT write to the clone. No local branch, no working tree, no checked-out ref,
no remote-tracking ref, no object transfer. It is a question, and the fix depends on it staying one
(FR-009).

**C9.** The read MUST be timeout-bounded and MUST be written to the durable action record before it
runs and completed with its outcome, as `git.fetch` already is (FR-008).

**C10.** The branch is named to the remote as a fully-qualified `refs/heads/<branch>`, and a
returned ref name that is not exactly that MUST be discarded rather than trusted.

## Evidence in the record

**C11.** The evidence string recorded with a deleted branch MUST identify the commit the remote
reported, not only the ref name consulted. "the branch is pushed and up to date with
`origin/<branch>`" is insufficient on its own: it is exactly what the defective code said while
reading a stale ref.

**C12.** From the action record alone, without re-running anything, a reader MUST be able to tell
which of the two tests decided any given branch.

## Unchanged

**C13.** The four cleanup outcomes — `done`, `branch_retained`, `retained`, `skipped` — their
meanings, and which of them the automatic pass reconsiders.

**C14.** The worktree guard: `force` is never passed, git's refusal is the answer, a dirty tree
stops before the branch half is attempted, and an already-missing directory is not a failure.

**C15.** The live-session guard and its `skipped` outcome, including the reason string
`clean_item` routes on.

**C16.** Effect levels: simulated at `plan`, real at `local` and above. The new read follows the
same rule and answers the simulated chain so that a simulated cleanup reaches the same outcome it
reaches today.
