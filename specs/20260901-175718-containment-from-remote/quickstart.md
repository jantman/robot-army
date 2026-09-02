# Quickstart: Validating Containment From the Remote

**Feature**: `specs/20260901-175718-containment-from-remote` | **Date**: 2026-09-01

Scenarios 1–3 run under `pytest`. Scenario 4 is the by-hand re-run of
[`specs/004-concurrency-polish/quickstart.md`](../004-concurrency-polish/quickstart.md) scenario 10,
which is what issue #105 asked for and what
[`docs/verification-2026-09-01-cleanup-guards.md`](../../docs/verification-2026-09-01-cleanup-guards.md)
records.

## Prerequisites

`git` on `PATH`; the tests are marked `requires_git`. No network and no GitHub token: every remote
here is a local bare repository, which is a real remote for every question this feature asks.

---

## 1 — A branch deleted on the remote is not "pushed"

The defect, stated as a test.

```bash
uv run pytest tests/integration/test_cleanup.py -k stale_tracking_ref -q
```

Stage it by hand to watch it: push the branch, then delete it **in the bare repository** — not
through the clone, which would prune the tracking ref and hide the bug — and run cleanup.

**Expected**: `branch_retained`. The branch survives in the clone, its commits are still reachable
from it, and `cleanup_reason` says the remote does not have the branch. Before the fix this
scenario reported *"the branch is pushed and up to date with origin/<branch>"* and deleted it.

---

## 2 — A rewound remote branch is not "pushed" either

```bash
uv run pytest tests/integration/test_cleanup.py -k rewound -q
```

Push the branch, force the remote branch back to an earlier commit, run cleanup.

**Expected**: `branch_retained`, with a reason naming how many commits are not on the remote's
commit. The same stale ref that caused scenario 1 says "up to date" here too.

---

## 3 — Every way of not knowing keeps the branch

```bash
uv run pytest tests/integration/test_cleanup.py -k containment -q
```

**Expected**, one case each:

| Situation | Outcome |
|---|---|
| remote unreachable | `branch_retained`, reason names the failure |
| remote has the branch at the same commit | `done`, reason names that commit |
| remote has the branch, we lack that commit | `branch_retained`, reason says so |
| branch merged, so contained in the base | `done` on base evidence, unaffected by this feature |

And the one that matters: **no case deletes a branch holding a commit the remote does not have.**

---

## 4 — Scenario 10, by hand, against a real repository

The verification issue #105 exists for. Full transcript in
[`docs/verification-2026-09-01-cleanup-guards.md`](../../docs/verification-2026-09-01-cleanup-guards.md);
in outline:

Build a disposable clone with a real bare `origin` and its own `HOME` — the redirected `HOME` is
not decoration, it is what makes the real session registry at `~/.claude/sessions` and the real
worktree root unreachable from the run. Stage four `done` items: an untracked file in one worktree,
an unpushed commit on the second, a genuinely running process registered against the third, and the
fourth's directory removed with `rm -rf`. Then:

```bash
robot-army --config "$ROOT/config.toml" reconcile
robot-army --config "$ROOT/config.toml" status
robot-army --config "$ROOT/config.toml" cleanup
git -C "$ROOT/clones/demo" log --oneline robot-army/<item-b-branch>
```

**Expected**: `retained`, `branch_retained`, `skipped`, and — for the fourth — see the verification
document, which records why the anomaly scenario 10 asks for cannot be raised for a `done` item and
why that is left to its own issue.

The last command is the one that matters and the reason it is run directly rather than by reading
`cleanup_reason` back: the question is whether the commits survive, not whether an intention to
keep them was recorded.

---

## What this cannot check

The remotes here are local bare repositories. That is a real remote for containment — it is asked
over the same protocol machinery and answers the same three ways — but it is not GitHub, and the
one thing it cannot exercise is authentication failure being distinguishable from absence. That
distinction is load-bearing: an auth failure must read as "could not ask" and keep the branch. It
is covered by exit code rather than by output parsing, precisely so that it does not depend on
which flavour of failure the remote returns.
