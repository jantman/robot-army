# Implementation Plan: Containment Proved From the Remote, Not From a Stale Ref

**Branch**: `issues/105` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/20260901-175718-containment-from-remote/spec.md`

## Summary

Issue #105 asked for scenario 10 to be run by hand before any automatic cleanup is enabled. It has
been. Three guards hold and the manual removal guard from #79 refuses correctly; the run found the
one defect of the unrecoverable class the issue was opened to catch.

Cleanup proves a branch safe to delete two ways. The first — every commit is contained in the
published base — fetches the base and is sound. The second — the branch is pushed and up to date
under its own name — reads `refs/remotes/<remote>/<branch>`, a **local** ref that the check never
fetches, and the fetch it does perform is scoped to the base branch so it neither refreshes nor
prunes it (measured: R2). A branch that was pushed and has since been deleted on the remote
therefore proves "published" from a leftover cache, and `force=True` deletes it.

The fix replaces the local read with a question put to the remote at the time of the check:
`git ls-remote <remote> refs/heads/<branch>`, which answers three distinguishable ways — a sha, no
such ref, or could-not-ask — and writes nothing to the clone (R4). Containment is then proved
against the commit the remote just named, or not proved at all.

Also in scope: the verification run is written down so the question #105 asks does not have to be
re-asked, and the stale-ref case becomes a test.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.13"`)

**Primary Dependencies**: none added. The `git` binary, through the existing `VersionControl`
boundary and `subproc.run`.

**Storage**: SQLite, unchanged. No migration, no schema version bump, no column meaning changed.

**Testing**: pytest. New coverage in `tests/integration/test_cleanup.py` (real git, real bare
remote, marked `requires_git`) and `tests/unit/test_cleanup.py` (the fake `VersionControl`).

**Target Platform**: one Linux machine with a shell.

**Project Type**: single-package CLI plus daemon.

**Performance Goals**: one extra `ls-remote` per branch that reaches the second containment test —
that is, per finished item whose branch is not already contained in the base. Bounded by
`FETCH_TIMEOUT`. Cleanup is not on a latency path.

**Constraints**: the read must not write to the clone (FR-009); every unresolved doubt keeps the
branch; no subprocess inside a database transaction.

**Scale/Scope**: two source files changed, one protocol declaration, two test files, one document.

## Constitution Check

*GATE: passed before Phase 0, re-evaluated after Phase 1 design. No violations; Complexity Tracking
is therefore empty and omitted.*

**I. Simplicity First.** One new boundary method and one extracted helper. No new dependency, no
new configuration key, no abstraction with one caller and no second use in hand. The rejected
alternative (R3) was *more* code and less correct. The helper is extracted rather than inlined for
a stated reason — five outcomes as five visible returns — not for generality.

**II. Single-User, Local-First.** No new network surface beyond a read of a remote the product
already fetches from. No new state, no new path, no secret handled. `ls-remote` output is a sha and
a ref name; the remote URL is not printed by this feature, so the existing rule that URLs may embed
credentials and must be normalised before recording is not newly engaged.

**III. Total Accountability.** The new read is recorded through `AuditLog.action` before it runs
and completed with its outcome, the same way `git.fetch` is. The evidence string recorded with
every decision gains the commit the remote reported, which makes the record *more* reconstructible
than today — the current string names a ref and cannot distinguish a sound deletion from the
unsound one this feature removes. **No unlogged action is introduced; no Principle III exception is
claimed.**

**IV. Interruption Tolerance.** The new call is a read that writes nothing to the clone and nothing
to the database (R9), so a kill during it leaves strictly less partial state than the existing
fetch. `cleanup_state` is untouched, so the next pass reconsiders the item from the beginning. The
call sits where the existing fetch sits, before any `db.transaction`, preserving the existing
guarantee that no subprocess runs inside one. Timeout is explicit (`FETCH_TIMEOUT`, R7).

**V. Public Code, Unsupported Project.** No credential, hostname or personal data enters the
repository. The verification document describes a disposable environment rather than shipping it,
and its paths are the throwaway ones, rewritten to placeholders.

**Development Workflow.** Spec, plan, tasks, implement. Unit tests for the new helper's five
outcomes and integration tests against real git for the two hazards; both required, since this is
persistence-adjacent logic whose failure path *is* the feature. The full suite must pass.

**Constitution Check re-evaluated after Phase 1**: unchanged. The design added one protocol method
and one module-private function; nothing in `data-model.md` or `contracts/branch-containment.md`
introduces state, configuration, or an unlogged action.

## Project Structure

### Documentation (this feature)

```text
specs/20260901-175718-containment-from-remote/
├── plan.md                        # This file
├── spec.md
├── research.md                    # Phase 0 — every git behaviour measured, not recalled
├── data-model.md                  # Phase 1
├── quickstart.md                  # Phase 1
├── contracts/
│   └── branch-containment.md      # Phase 1
├── checklists/
│   └── requirements.md
└── tasks.md                       # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
src/robot_army/
├── cleanup.py                     # _branch_is_contained rewritten; _pushed_to_remote added
└── boundaries/
    ├── __init__.py                # VersionControl protocol: remote_branch_head declared
    └── git.py                     # GitVersionControl + SimulatedVersionControl implementations

tests/
├── unit/
│   └── test_cleanup.py            # the five outcomes, against the fake VersionControl
└── integration/
    └── test_cleanup.py            # real git, real bare remote: deleted and rewound branches

docs/
└── verification-2026-09-01-cleanup-guards.md   # the scenario 10 run, and both findings
```

**Structure Decision**: no new module. The change is a correction inside the function that already
owns this decision, plus the boundary read it needs. `cleanup.py` stays the only place that decides
whether a branch may be deleted.

## Implementation Order

1. **Boundary first.** `remote_branch_head` on the protocol, on `GitVersionControl`, and on
   `SimulatedVersionControl`. Unit-testable on its own against a real bare repository, and the
   three-way answer is the whole foundation — if it collapses "absent" into "failed" the rest
   cannot satisfy FR-007.
2. **`_pushed_to_remote`.** The five outcomes, each a visible return, each with its own evidence
   sentence.
3. **`_branch_is_contained`.** Swap the second test for the helper; leave the base test and its
   failure rule alone.
4. **Tests.** Unit for the five outcomes; integration for deleted-on-remote and rewound-on-remote
   against real git. Confirm the integration tests fail against the pre-fix function.
5. **Documentation.** The verification document, and the README/roadmap lines that currently tell
   the operator not to run cleanup.

## Risks

- **A retention that should have been a deletion.** The conservative direction, by design, but it
  is not free: a branch kept forever is disk the operator has to reclaim by hand. The case it adds
  is "the remote's branch is at a commit we do not have", which needs someone to have pushed to the
  robot's branch from elsewhere. Accepted, and `robot-army cleanup <id>` reconsiders it.
- **One extra network round-trip per considered branch.** Only for branches that failed the base
  test, which are the minority, and cleanup runs on a reconciliation tick rather than in response
  to anything waiting.
- **A future edit reintroducing the ref read.** The reason the contract states C4 as a prohibition
  and the quickstart names the exact sentence the defective code printed.
