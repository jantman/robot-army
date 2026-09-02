# Phase 0 Research: Containment Proved From the Remote, Not From a Stale Ref

**Feature**: `specs/20260901-175718-containment-from-remote` | **Date**: 2026-09-01

Every git behaviour below was **measured** against `git version 2.55.0` in a throwaway repository
with a real bare remote, not recalled. Code line numbers are from `9c7a56b`.

---

## R1 — The defect, located exactly

`cleanup.py:_branch_is_contained` (`cleanup.py:294-341`) proves containment two ways:

```python
in_base = ahead(f"{remote}/{base}")
if in_base == 0:
    return True, f"every commit is contained in {remote}/{base}"
pushed = ahead(f"{remote}/{branch}")
if pushed == 0:
    return True, f"the branch is pushed and up to date with {remote}/{branch}"
```

The only fetch in the function is `vcs.fetch(clone, remote, base)` — the base branch, and nothing
else. `{remote}/{branch}` on the second test therefore resolves to `refs/remotes/<remote>/<branch>`,
a **local** ref that this code never refreshes.

The function's own docstring says the fetch "is required, not defensive. Without it the check reads
a stale remote-tracking ref and answers a question about the past." That reasoning is correct and
was applied to only one of the two tests.

**Decision**: the fix belongs entirely inside this function plus one new boundary read. The
worktree guard, the live-session guard, the already-missing-directory path, and the base test are
untouched.

---

## R2 — `git fetch --prune <remote> <base>` does not prune anything else

Measured. Push a branch, delete it on the remote, then run the fetch cleanup actually runs:

```
$ git fetch --prune origin main
From ../bare
 * branch            main       -> FETCH_HEAD
$ git rev-parse refs/remotes/origin/feat
556f4ef6197da5ffae9e7e210baccb2d1b54a2d7      # still there
```

This matches git's documented rule that command-line refspecs limit what `--prune` considers. The
`--prune` already present in `boundaries/git.py:67` is therefore no defence at all for any ref
outside the base's refspec.

**Consequence**: the stale ref survives every cleanup pass, indefinitely, and the second
containment test reads it as the remote's answer.

---

## R3 — Refreshing the branch's own tracking ref does not work either

The obvious fix is to fetch the branch too, with a refspec that also prunes it:

```
$ git fetch --prune origin '+refs/heads/feat:refs/remotes/origin/feat'
fatal: couldn't find remote ref refs/heads/feat
$ git rev-parse refs/remotes/origin/feat
556f4ef6197da5ffae9e7e210baccb2d1b54a2d7      # STILL there
```

Measured: when the remote no longer has the branch, the fetch **fails and prunes nothing**. The
stale ref survives, so correctness would depend on the caller remembering that the fetch failed and
never asking about the ref afterwards. That is a coupling between two statements several lines
apart, in a function whose whole subject is that a value read one way means something else read
another way — the exact shape of the R11 bug this module already carries a warning about.

**Decision**: reject. Do not try to make the local ref trustworthy.

**Alternatives considered**: fetching the branch without `--prune` and comparing before/after (same
coupling, plus it leaves a ref the next reader can misuse); deleting the tracking ref first and
re-fetching (a write to the clone for a read-only question, and a crash between the two loses the
only surviving reference to the work — precisely the outcome being prevented).

---

## R4 — `git ls-remote` asks the remote and touches nothing

Measured, all three cases:

| Situation | Exit | Output |
|---|---|---|
| remote has the branch | `0` | `<sha>\trefs/heads/<branch>` |
| remote does not have the branch | `0` | *(empty)* |
| remote unreachable | `128` | `fatal: ... repository exists.` |

Three distinguishable answers from one call, no local ref written, no object transferred, no
working tree touched. The empty-but-successful case is exactly the "the remote does not have this
branch" evidence FR-007 asks to be distinguishable from "commits exist ahead of it", and it is
distinguishable *by exit code* from "could not ask", which FR-004 requires to keep the branch.

**Decision**: add one boundary read, `remote_branch_head(clone, remote, branch)`, implemented as
`git ls-remote <remote> refs/heads/<branch>`. It returns the sha, or `None` when the remote answered
and does not have the branch, and raises when the remote could not be asked — the same tri-state the
three measured cases produce naturally.

**Alternatives considered**: `git ls-remote --exit-code` (folds "absent" and "failed" into non-zero,
destroying the distinction FR-007 needs); parsing `git remote show` (a porcelain command, and it
contacts the remote to build a much larger answer).

**Note on the pattern argument**: the branch name is passed as the full `refs/heads/<branch>` path
and the returned ref name is required to equal it exactly. `ls-remote` patterns match on `/`
boundaries from the right, so a fully-qualified pattern cannot match a different branch — and the
equality check means a surprising match is discarded rather than trusted. This is what the spec's
"must not be constructible into something other than a single branch lookup" edge case asks for.

---

## R5 — The remote's sha has to be resolvable locally, or nothing is proved

`ls-remote` returns a sha. Containment then needs "every commit on the local branch is reachable
from that sha", which is `commits_ahead(clone, <sha>, branch) == 0` — the existing boundary read,
given a raw sha instead of a ref name.

Measured: `rev-list --count <unknown-sha>..<branch>` fails with `fatal: Invalid revision range`, and
`commits_ahead` already runs with `check=False` and maps a failed run to `None`. So a remote sha
this clone has never seen answers `None`, which the module already treats as unproven.

That is the right answer rather than a workaround. If the remote's tip is a commit we do not have,
we cannot demonstrate our commits are reachable from it without fetching objects — and fetching
objects is the write this design is avoiding. The case only arises when someone else pushed to the
robot's branch, which is rare, conservative to refuse, and recoverable (the branch is kept).

**Decision**: guard the `commits_ahead` call with a `rev_parse` of the sha so the retention reason
can say *why* — "the remote's branch is at a commit this clone does not have" is a different and
more useful sentence than "commits exist ahead".

**And the guard has to peel.** Measured after review caught it: `git rev-parse --verify` on a bare
forty-hex string validates that the argument names a single revision, which is a question about
syntax, and answers for a commit the clone has never seen.

```
$ git rev-parse --verify 1234567890123456789012345678901234567890
1234567890123456789012345678901234567890          # exit 0, and the object is not here
$ git rev-parse --verify 1234567890123456789012345678901234567890^{commit}
fatal: Needed a single revision                   # exit 128
```

Since the value being checked is always a full sha, the unpeeled form can never answer `None`, and
the guard it protects would never fire — the case would fall through to `commits_ahead`, fail
there, and be reported as "could not compare" instead of the sentence written for it. The peel is
`<sha>^{commit}`, and `tests/unit/test_git_boundary.py` pins the distinction at the boundary rather
than leaving it as a comment two files from the caller.

---

## R6 — Where "unproven" has to keep meaning "keep"

The function's existing contract is that every unresolved doubt keeps the branch, and each new
failure mode has to join it explicitly rather than by omission:

| New failure | Answer |
|---|---|
| `ls-remote` raised (unreachable, timeout) | pushed test unproven |
| `ls-remote` returned no ref | remote does not have the branch; pushed test fails |
| remote sha unresolvable locally | pushed test unproven |
| `commits_ahead` returned `None` | pushed test unproven — the existing R11 rule |
| `commits_ahead` returned `> 0` | commits are unpublished; pushed test fails |

Only `commits_ahead == 0` on a sha the remote reported in this call proves the branch published.

**Decision**: express the pushed test as one small helper returning `(proved, evidence)` so that
every one of the five rows is a visible `return` rather than a fallthrough. The base test keeps its
current shape and its current failure rule untouched (FR-005).

---

## R7 — Timeout class for the new call

`ls-remote` contacts the network, so `QUICK_TIMEOUT` (30 s) is the wrong bound; it is the class of
call `FETCH_TIMEOUT` (300 s) exists for. It transfers no objects, so 300 s is generous — but the
module's rule is that an unbounded network call is the failure that nothing observes, and choosing
the smaller bound to be tidy would introduce a new way for a slow-but-working remote to be read as
"could not ask", which keeps branches that should have been reclaimed.

**Decision**: `FETCH_TIMEOUT`.

---

## R8 — The simulated boundary

`SimulatedVersionControl` (`git.py:276`) answers `rev_parse` with forty zeroes and `commits_ahead`
with `0`, deliberately, so a simulated cleanup reaches the same decision the real one would rather
than retaining everything. The new read has to follow that rule or `plan`-level cleanup would start
retaining every branch — a divergence the simulated boundaries exist to avoid.

**Decision**: `remote_branch_head` logs its intent and returns the same forty zeroes, which
`rev_parse` then resolves and `commits_ahead` then answers `0` for. The simulated chain reaches
`done` exactly as it does today.

---

## R9 — What this logs, and what happens if it is killed halfway

Required by the constitution's Development Workflow section.

**Logs**: one new `git.ls_remote` action per branch considered, recorded through
`AuditLog.action` before the subprocess runs and completed with its outcome, exactly as
`git.fetch` is. The decision's `cleanup.retained` / `cleanup.considered` records already carry the
evidence string, which this feature makes more specific. No new unlogged action is introduced, so
Principle III needs no exception here.

**Killed halfway**: `ls-remote` is a read. It writes nothing to the clone and nothing to the
database, so a kill during it leaves no partial state at all — strictly less than the existing
fetch, which can leave a partially updated object store that git itself makes safe. The item's
`cleanup_state` is unchanged, so the next pass reconsiders it from the beginning. The existing
guarantee that no subprocess runs inside a database transaction is preserved: the new call sits in
the same place in the flow as the existing fetch, before any `db.transaction`.

---

## R10 — The `prunable_worktree` finding, and why it is not fixed here

`reconcile._sweep_worktrees` (`reconcile.py:981-984`) filters to items whose state is **not**
`done` or `abandoned`. Scenario 10's hand-deleted-directory item is `done`, because its issue is
closed — that is how the scenario stages it and how the lifecycle produces it. Measured with
`[cleanup] on_issue_close = false`: git reports the worktree `prunable`, and `robot-army anomalies`
reports nothing.

The exclusion is not gratuitous. `worktree_path` stays on the record after a successful cleanup, so
including terminal items would raise an anomaly for every worktree cleanup legitimately removed.
The narrow fix — include terminal items whose cleanup record does not already account for the
missing directory — still misreports items removed with `robot-army worktree remove`, because that
command (`operations.py:1665-1700`) writes no cleanup record and does not clear `worktree_path`. So
the real fix starts on the manual removal path, which was changed for #79 three commits ago.

**Decision**: out of scope, recorded in the verification document, left for its own issue. It is a
visibility gap; nothing is destroyed by it.
