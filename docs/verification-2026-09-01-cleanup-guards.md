# Verification: the cleanup guards, against a real repository

**2026-09-01 · issue #105 · `specs/004-concurrency-polish/quickstart.md` scenario 10 · three
guards hold, one defect found and fixed, one gap recorded**

Scenario 10 is the one quickstart scenario whose failure mode is unrecoverable. Cleanup deletes
branches, and a commit on a branch deleted there is gone — there is no remote to recover it from,
and the worktree is removed in the same operation. Everything else left unverified in #1 fails
loudly and reversibly. This one fails silently, permanently, and looks like a successful cleanup
while it does it.

Automated coverage (`tests/integration/test_cleanup.py`, T061) drives the same four cases against a
fixture. What it could not establish is the thing that decides the dangerous case: **that the
containment evidence authorising a `force` delete was read from the remote we think it was.** It
was not. That is what this run found.

## Result

| Item | Staged | Required | Observed |
|---|---|---|---|
| a | an untracked file in the worktree | `retained` | `retained` — git refused; worktree and branch both intact; git's own message recorded as the reason |
| b | a commit on an unpushed branch | `branch_retained`, **commits reachable** | `branch_retained` — worktree reclaimed, branch kept, the commit still on the branch |
| c | a live session | `skipped` | `skipped` — nothing touched; cleaned to `done` on the next pass once the session ended |
| d | a hand-deleted worktree directory | a `prunable_worktree` anomaly | **no anomaly** — see Finding 2 |

**Zero removals that should have been kept**, in the four staged cases. `robot-army cleanup` applied
the same guards as the automatic pass. `robot-army worktree remove` on item c refused with the #79
message and exit 3, so that fix holds on the real command as well as in its tests.

Two findings came out of the run. The first is the unrecoverable one and is fixed on this branch.
The second is a visibility gap and is left for its own issue.

---

## Finding 1 — containment was proved from a stale local ref (fixed)

Cleanup proves a branch safe to delete two ways: every commit is contained in the published base,
or the branch itself is pushed and up to date under its own name. The first fetches the base and is
sound. **The second asked about `<remote>/<branch>` without ever fetching it.** That name resolves
to `refs/remotes/<remote>/<branch>` — a local cache of what the remote said the last time anything
asked — and the fetch that does happen is scoped to the base branch, so it neither refreshes nor
prunes it.

Measured, in the same disposable environment, against `git 2.55.0`:

```
$ git fetch --prune origin main          # what cleanup runs
From ../bare
 * branch            main       -> FETCH_HEAD
$ git rev-parse refs/remotes/origin/feature
556f4ef...                                # the deleted branch's tracking ref, untouched
```

Staged the ordinary sequence — branch pushed, then the branch deleted on the remote, done against
the remote so the clone's tracking ref was left as a real clone's would be — and ran cleanup:

```
remote_still_has_branch : false
clone_stale_tracking_ref: b055db0…                       ← the "proof"
decision                : done — "the branch is pushed and up to date with origin/<branch>"
branch_still_in_clone   : false                          ← force-deleted
```

After a routine `gc` on the remote the commit was gone from it entirely. The only thing still
holding that work was the stale tracking ref that had been mistaken for proof — and the next full
`git fetch --prune` removes that too. A force-push that rewinds the remote branch reaches the same
place by the same route.

**Fixed** by asking the remote during the check (`git ls-remote`), which answers three
distinguishable ways — a commit, no such branch, or could-not-ask — and writes nothing to the
clone. Only the first can prove anything; the other two keep the branch. Spec, plan and reasoning:
[`specs/20260901-175718-containment-from-remote/`](../specs/20260901-175718-containment-from-remote/).

Two obvious fixes were measured and rejected before that one. `git fetch --prune` limits pruning to
the refspecs it is given, so widening the existing fetch does not help; and fetching the branch's
own refspec **fails without pruning** when the remote no longer has the branch, leaving the stale
ref in place and correctness depending on the caller remembering that the fetch failed.

---

## Finding 2 — item d's expectation cannot be met (not fixed)

Scenario 10 requires a hand-deleted worktree directory to surface as a `prunable_worktree` anomaly.
It does not, and as written it cannot: `reconcile._sweep_worktrees` filters to items whose state is
**not** `done` or `abandoned`, and scenario 10's item d is `done` by construction, because its issue
is closed. That is not a contrived setup — it is how the lifecycle produces the case.

Measured with `[cleanup] on_issue_close = false`, the shipped default and the currently recommended
setting:

```
$ robot-army --config … reconcile
prunable_worktrees   0
$ robot-army --config … anomalies
no outstanding anomalies
$ git -C clones/demo worktree list
…/worktrees/demo/issue-104  d204976 [robot-army/issue-104-…]  prunable
```

Git knows. Nothing else says so. With cleanup **on**, the pass resolves the item to `done` in the
same tick and no anomaly is needed — which is why the main run above shows the case passing
harmlessly — but that is not the configuration this is run in.

Left for its own issue, deliberately. Nothing is destroyed by it; it is disk that goes unreported.
And the narrow fix is not narrow: `worktree_path` stays on the record after a successful cleanup, so
simply including terminal items in that sweep would raise an anomaly for every worktree cleanup
legitimately removed. Distinguishing them needs `robot-army worktree remove` to record what it did
first — it currently writes no cleanup record and does not clear `worktree_path` — and that is the
manual removal path, changed for #79 three commits earlier. It is its own piece of work, and is
filed as #113.

---

## Appendix

### The environment

Disposable, and hermetic on purpose. `$ROOT` is a throwaway directory; every path below is under it.

- **`$ROOT/remote.git`** — a bare repository, initialised `-b main`. A real remote: the containment
  check fetches and asks, and a fetch against nothing proves nothing.
- **`$ROOT/clones/demo`** — a clone with one commit, `origin` pointing at the bare repository, and
  `main` pushed and fetched.
- **`$ROOT/home`** — `HOME` is redirected here for every command. This is not tidiness. The session
  registry is resolved as `~/.claude/sessions` and the worktree root and clone root come from the
  config, so without the redirection a mistake in the run could reach the real registry or the real
  worktrees. With it, the run cannot see either.
- **`$ROOT/config.toml`** — `effect_level = "live"`, `[cleanup] on_issue_close = true`, one
  repository `demo`, worktree root and state directory under `$ROOT`.

No network and no GitHub token: nothing in `reconcile` or `cleanup` reads an issue.

### How the four items were staged

Four work items inserted as `done` with worktrees created by the product's own `worktree.prepare`,
so the branches and directories are the ones it would have made. The item state was written
directly, standing in for the transition a closed issue produces, which needs a GitHub read this
environment has no network for. Then:

- **a** — `scratch.txt` written into the worktree and left untracked.
- **b** — a file committed on the branch and never pushed. Its sha recorded, to be checked with
  `git log` afterwards rather than by reading `cleanup_reason` back: the question is whether the
  commits survive, not whether an intention to keep them was recorded.
- **c** — a real `sleep 3600` started in the worktree with `start_new_session=True`, and a registry
  file written naming its pid and its `/proc` start time. A fabricated pid would have been answered
  "not alive" and the guard would never have been reached.
- **d** — `rm -rf` on the worktree directory.

### The run

```bash
robot-army --config "$ROOT/config.toml" status      # 4 done; capacity 1/4 — the live session counts
robot-army --config "$ROOT/config.toml" reconcile   # cleaned 1, retained 2, one skipped
robot-army --config "$ROOT/config.toml" cleanup     # the explicit path, same guards
robot-army --config "$ROOT/config.toml" worktree remove 3    # refuses: exit 3
git -C "$ROOT/clones/demo" log --oneline robot-army/<item-b-branch>
```

Item b, after everything:

```
cba177f the only copy of this work
973b151 initial
```

### What the record said

```
item 1: retained        — fatal: '…/issue-101' contains modified or untracked files
item 2: branch_retained — worktree removed; branch kept — 1 commit(s) exist on
                          robot-army/issue-102-… that are not on origin/main or
                          origin/robot-army/issue-102-…
item 3: skipped         — session scenario10-live-session is still live
item 4: done            — worktree removed; branch removed — every commit is contained in
                          origin/main
```

Item 2's wording is pre-fix. After Finding 1 the second half of that sentence names the commit the
remote reported rather than a ref that was never fetched.

`robot-army anomalies` raised `orphan_session` for item c — a live worker under a work item that is
no longer running one, its session row left open on purpose — which is correct and is what the #79
guard keys on.

### Standing advice

`[cleanup] on_issue_close` stays `false` until the operator decides otherwise. Finding 1 is fixed,
which removes the reason it was unsafe to enable; whether to enable it is a separate decision, and
this document does not make it.
