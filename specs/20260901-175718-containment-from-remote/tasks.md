---

description: "Task list for: Containment Proved From the Remote, Not From a Stale Ref"
---

# Tasks: Containment Proved From the Remote, Not From a Stale Ref

**Input**: Design documents from `specs/20260901-175718-containment-from-remote/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/branch-containment.md](./contracts/branch-containment.md),
[quickstart.md](./quickstart.md)

**Tests**: **Required, not optional.** The constitution's Development Workflow makes unit tests
mandatory for every new or changed unit of behaviour, and this feature's central success criterion
(SC-001) *is* a test assertion: a branch holding a commit the remote does not have is never
deleted. Test tasks precede the implementation they cover in each phase, because the assertions are
the specification here.

## The assertion that matters

Everywhere a task says "the branch survives", assert **both** that the branch still exists in the
clone **and** that the commit that existed only on it is still reachable from it. The first alone is
the weaker claim — a branch can survive a pass that never considered it. See
`specs/004-concurrency-polish/quickstart.md` scenario 10, which makes the same point about reading
`cleanup_reason` back instead of asking git.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US4)

## Path Conventions

Single project: `src/robot_army/`, `tests/`, `docs/` at repository root.

---

## Phase 1: Setup

**Purpose**: Establish the baseline, so that any red test later is attributable to this feature.

- [X] T001 Capture the baseline: run `uv run pytest` and `uv run ruff check src tests` from the repository root and record that both are green before any edit. If either is red, stop and report — this feature must not be built on top of an unexplained failure.

---

## Phase 2: Foundational

**Purpose**: The boundary read every user story depends on. **Blocking** — nothing in Phase 3 or
later can be written against a boundary that does not exist, and if this read collapses "the remote
does not have it" into "the remote could not be asked", FR-007 and contract C6 cannot be satisfied
by anything downstream.

- [ ] T002 Declare `remote_branch_head(self, clone_path: str, remote: str, branch: str) -> str | None` on the `VersionControl` protocol in `src/robot_army/boundaries/__init__.py`, beside `commits_ahead`, with a docstring stating the three-way answer: a sha when the remote has the branch, `None` when the remote answered and does not have it, and a raise when the remote could not be asked. Say plainly why the three must not be collapsed — `commits_ahead`'s own docstring is the precedent and the cautionary tale.

- [ ] T003 Implement `remote_branch_head` on `GitVersionControl` in `src/robot_army/boundaries/git.py` as `git ls-remote <remote> refs/heads/<branch>`, run with `check=True` so an unreachable remote raises, wrapped in `self._audit.action("git.ls_remote", ...)` so the request is recorded before it runs and completed with its outcome (contract C9), at `FETCH_TIMEOUT` (research R7). Parse the output as `<sha>\t<refname>` lines and return the sha only for a line whose refname is exactly `refs/heads/<branch>`; return `None` for no matching line (contract C10). Record the resolved sha, or its absence, in the action detail.

- [ ] T004 Implement `remote_branch_head` on `SimulatedVersionControl` in `src/robot_army/boundaries/git.py`: log `git.ls_remote` with its arguments and return `"0" * 40`, matching the class's existing `rev_parse`. Comment why — a simulated cleanup must reach the outcome the real one would, and returning `None` here would make every `plan`-level cleanup retain its branch (research R8).

- [ ] T005 [P] Add `tests/unit/test_git_boundary.py` coverage for all three answers against a real bare repository: a branch the remote has (returns its sha), a branch it does not (returns `None`), and a remote that does not exist (raises). Assert the `git.ls_remote` record exists in the audit log for each, and assert the clone's refs are byte-for-byte identical before and after the call — contract C8, and the property the whole fix rests on.

**Checkpoint**: the remote can be asked, honestly, without writing to the clone.

---

## Phase 3: User Story 1 — A branch is only "pushed" if the remote says so now (P1) 🎯 MVP

**Goal**: The pushed-under-its-own-name containment test is decided by what the remote reports
during the check, never by a leftover remote-tracking ref.

**Independent test**: push a branch, delete it in the bare remote without touching the clone, run
cleanup. The branch survives, its commit is still reachable from it, and the reason says the remote
does not have the branch.

- [ ] T006 [US1] Add `test_a_branch_deleted_on_the_remote_is_not_treated_as_pushed` to `tests/integration/test_cleanup.py`: a finished item whose branch was pushed with `git push origin <branch>:<branch>` and then removed from the **bare repository** with `git update-ref -d refs/heads/<branch>` — deliberately not through the clone, which would prune the tracking ref and hide the defect. Assert the decision is `BRANCH_RETAINED`, the branch is still in `branches(published)`, the commit is still reachable from it, and `refs/remotes/origin/<branch>` being stale did not authorise anything. **Confirm this test fails against the current `_branch_is_contained` before writing T009** — that failure is the reproduction, and a test that passes both before and after proves nothing.

- [ ] T007 [P] [US1] Add `test_a_rewound_remote_branch_is_not_treated_as_pushed` to `tests/integration/test_cleanup.py`: push the branch, then force the bare repository's ref back to an earlier commit. Assert `BRANCH_RETAINED` and that the commits missing from the remote are still reachable in the clone.

- [ ] T008 [P] [US1] Add unit coverage in `tests/unit/test_cleanup.py` for the five outcomes of the pushed test, using the file's existing fake `VersionControl`: remote raised, remote returned `None`, sha unresolvable locally, `commits_ahead` returned `None`, `commits_ahead` returned `> 0` — each keeps the branch — and `commits_ahead` returned `0`, which is the only one that deletes it.

- [ ] T009 [US1] Add `_pushed_to_remote(vcs, *, clone, remote, branch) -> tuple[bool, str]` to `src/robot_army/cleanup.py` per [data-model.md](./data-model.md): ask `remote_branch_head`; on a raise return unproven naming the failure; on `None` return "the remote does not have this branch"; resolve the reported sha with `rev_parse` and return unproven if this clone does not hold it; otherwise `commits_ahead(clone, sha, branch)`, with `None` unproven, `> 0` refusing and naming the count, and `0` proving — with the sha in the evidence string either way. Every one of the six a visible `return` (research R6).

- [ ] T010 [US1] Rewrite the second half of `_branch_is_contained` in `src/robot_army/cleanup.py` to call `_pushed_to_remote` instead of `ahead(f"{remote}/{branch}")`. Delete the `{remote}/{branch}` read entirely — leaving it available is leaving the defect available. Leave the base fetch, the base test, and the base failure rule untouched (FR-005). Update the function's docstring, which currently explains why the fetch matters and then applies that reasoning to only one of the two tests.

- [ ] T011 [US1] Update the module docstring of `src/robot_army/cleanup.py` so its account of the branch guard says containment is checked against the remote *as asked at the time of the check*, and name the stale-ref failure the way the `commits_ahead` note names the `return 0` failure — as a specific measured bug, so the next reader knows why it reads the way it does.

**Checkpoint**: the dangerous row of scenario 10 is closed. This alone is the MVP.

---

## Phase 4: User Story 2 — A remote that cannot be asked keeps the branch (P2)

**Goal**: Every new way of not knowing joins the existing rule that unproven keeps the branch.

**Independent test**: point the clone's remote at a path that does not exist and run cleanup on an
item whose branch was pushed. The branch is kept and the reason names the failure.

- [ ] T012 [US2] Extend `test_a_failed_containment_fetch_keeps_the_branch_and_is_reconsidered` in `tests/integration/test_cleanup.py`, or add a sibling, so the unreachable-remote case is asserted after the base fetch fails **and** after it succeeds — the second is the new path, where the base fetch works but `ls-remote` for the branch does not.

- [ ] T013 [P] [US2] Add coverage in `tests/integration/test_cleanup.py` for a clone with no configured remote at all: the branch is kept, because nothing can be proved published without a remote.

- [ ] T014 [US2] Verify in `src/robot_army/cleanup.py` that `_pushed_to_remote`'s exception handling is as broad as the existing fetch's and documented for the same reason — the boundary can fail in more ways than it declares, and which way it failed changes nothing, because every failure means unproven and unproven keeps the branch.

**Checkpoint**: no new route from "could not ask" to "yes".

---

## Phase 5: User Story 3 — The evidence in the record names what was actually checked (P3)

**Goal**: The record alone distinguishes a sound deletion from the unsound one this feature removes.

**Independent test**: read the audit log and `cleanup_reason` after each of the four retention causes
and after a deletion; each is separately identifiable without re-running anything.

- [ ] T015 [US3] Assert in `tests/integration/test_cleanup.py` that a branch deleted on pushed evidence records a reason containing the commit the remote reported, and that a `git.ls_remote` action for that branch precedes the `git.delete_branch` record in the audit log.

- [ ] T016 [P] [US3] Assert in `tests/integration/test_cleanup.py` that the four retention reasons are distinguishable from one another in `cleanup_reason`: the remote does not have the branch; the remote has it at a commit this clone lacks; commits exist ahead of it; the remote could not be asked (contract C7).

- [ ] T017 [US3] Confirm `_branch_is_contained` combines the base evidence and the pushed evidence into the retention reason so a reader sees both answers rather than one, and that neither string exceeds what `robot-army show` renders usefully.

**Checkpoint**: `robot-army show <id>` answers "why is this branch still here?" precisely.

---

## Phase 6: User Story 4 — The verification itself survives without being re-run (P3)

**Goal**: The repository answers "have the cleanup guards been checked against a real repository,
and what was found?"

**Independent test**: read the committed document; it states the environment, each guard's outcome,
and every finding including the unfixed one.

- [ ] T018 [US4] Write `docs/verification-2026-09-01-cleanup-guards.md` recording the scenario 10 run: how the disposable environment was built (real bare `origin`, redirected `HOME` so the real session registry and worktree root were unreachable, four `done` items with worktrees made by the product's own preparation path, a genuinely running process for the live-session case), what each of the four guards decided with the observed output, and both findings. Follow the shape of `docs/incident-2026-08-31-desktop-session-killed.md`. Use placeholder paths, not the throwaway ones (constitution Principle V).

- [ ] T019 [US4] Record the `prunable_worktree` finding in that document with its measurement — `[cleanup] on_issue_close = false`, directory hand-deleted, git reports the worktree prunable, `robot-army anomalies` reports nothing — and why it is left for its own issue: the sweep excludes terminal items for a reason, and the narrow fix needs `robot-army worktree remove` to record what it did first (research R10).

- [ ] T020 [P] [US4] Update the `## Cleaning up` section of `README.md`: the branch guard's description currently says "the base ref is fetched and the branch is deleted only if every commit on it is provably on the remote — contained in the published base, or pushed and up to date". Say instead that the remote is asked, during the check, what it holds for that branch, and that a remote-tracking ref is not taken as its answer. Keep the section's voice.

**Checkpoint**: #105's question is answered by the repository, not by memory.

---

## Phase 7: Polish

- [ ] T021 Run the full suite: `uv run pytest` and `uv run ruff check src tests`. Both green.

- [ ] T022 Revert the `_branch_is_contained` change locally, confirm T006 fails, and restore it — SC-003 is a claim about the test, and the only way to know it holds is to check.

- [ ] T023 Re-run scenario 10 end to end against a fresh disposable environment with the fix in place, and confirm the four outcomes are unchanged from the pre-fix run — this feature must close the stale-ref hole without disturbing the three guards that were already correct (FR-010).

- [ ] T024 Confirm no `NEEDS CLARIFICATION` marker, placeholder path, or throwaway directory name survives anywhere in `specs/20260901-175718-containment-from-remote/` or `docs/`.

---

## Dependencies

```text
T001  Setup
  └─ T002 → T003 → T004 → T005      Foundational (blocking)
        └─ Phase 3 (US1)  T006 → T009 → T010 → T011,  T007 ∥ T008
              └─ Phase 4 (US2)  T012 ∥ T013 → T014
              └─ Phase 5 (US3)  T015 ∥ T016 → T017
        Phase 6 (US4)  T018 → T019,  T020        independent of Phases 3–5
              └─ Phase 7  T021 → T022 → T023 → T024
```

- **US1 blocks US2 and US3**: both describe behaviour of the function US1 introduces.
- **US4 is independent**: the verification document records a run that already happened, and the
  README correction describes the design US1 implements — write it after T010 so it is not
  describing an intention.
- **T006 before T009 is not optional.** The test has to be seen failing against the current code;
  that is the reproduction.

## Parallel opportunities

- T007 and T008 alongside T006 (different assertions, and T008 is a different file).
- T013 alongside T012; T016 alongside T015.
- T020 alongside T018 and T019 (different files).

## Implementation strategy

**MVP is Phase 3.** With T002–T011 done, no branch holding a commit the remote does not have can be
deleted, which is the whole of the unrecoverable failure mode and the reason issue #105 exists.
Phases 4 and 5 harden and explain it; Phase 6 stops the question needing to be asked again.

**Do not enable `[cleanup] on_issue_close` as part of this feature.** That decision is the
operator's, after this lands, and the verification document says so.
