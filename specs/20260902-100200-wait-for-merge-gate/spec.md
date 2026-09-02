# Feature Specification: Per-Repo Concurrency and Wait-for-Merge

**Feature Branch**: `robot-army/issue-47-per-repo-concurrency-and-wait-for-merge`

**Created**: 2026-09-02

**Status**: Draft

**Input**: [jantman/robot-army#47](https://github.com/jantman/robot-army/issues/47) — "Allow configuration either globally or per-repo of: (1) per-repository concurrency limits, specifically to force a given repo into serial rather than parallel operation; (2) an option to wait-for-merge — do not dispatch a new issue in a given repo until the PR(s) for the previous issue(s) have been merged to the default branch by a human, out of band from robot-army. When they are, fetch and pull the default branch before dispatching a new issue. These items are mainly intended to optimize for repos where most issues touch the same areas of the codebase, and therefore we want to work them serially and ensure that each issue begins from a default branch that has the results of the previous issue already merged in."

## Context: what already exists

The issue asks for two things and the first one already ships. Recording that here rather
than rediscovering it during planning:

- `[dispatch] default_repo_max_sessions` sets a global per-repository ceiling and already
  defaults to `1`, which is the serial operation the issue asks for.
- `[repos."owner/name"] max_sessions` overrides it for one repository.
- The effective cap is the lower of that value and `[daemon] max_concurrent_sessions`, and
  the queue reports a `repo_cap` hold naming the repository and both numbers, distinguishing
  a cap the author chose from one they inherited.

What that limit does **not** do is the second half of the issue. A per-repository cap counts
*live sessions*. The moment a session exits, the slot is free and the next issue in that
repository is dispatched — from a default branch that does not yet contain the previous
issue's work, because that work is sitting in an unmerged pull request. For a repository
where every issue touches the same files, that is the collision the issue is written about.

This feature therefore delivers the gate the cap cannot express, and closes the first half by
confirming the existing knobs against tests and documentation rather than by rebuilding them.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One issue at a time, and not the next until the last one landed (Priority: P1)

The author turns on wait-for-merge for a repository where issues overlap. A session finishes
its issue and opens a pull request. Instead of immediately starting the next issue in that
repository, the system holds it — visibly, naming what it is waiting for. The author reviews
and merges the pull request themselves, on their own schedule, entirely outside robot-army.
Merging closes the source issue, the work item becomes done, and the next issue in that
repository is dispatched on the following pass.

**Why this priority**: This is the issue's actual request and the only part not already
delivered. Without it, a serial repository still starts its next issue from stale code.

**Independent Test**: Enable wait-for-merge for one repository, queue two issues for it, let
the first dispatch and its session exit. Confirm the second issue is held and reported as
waiting on the first, then close the first issue and confirm the second dispatches.

**Acceptance Scenarios**:

1. **Given** a repository with wait-for-merge enabled, one work item in `awaiting_review`,
   and another item `ready` in the same repository, **When** the queue is computed,
   **Then** the ready item is held with a reason that names the unfinished item and the
   issue it came from, and is not dispatched.
2. **Given** that same state, **When** the unfinished item's source issue is closed and it
   becomes `done`, **Then** on the next pass the ready item is no longer held for this
   reason and is dispatched.
3. **Given** a repository with wait-for-merge enabled and an unfinished item in it,
   **When** a ready item in a *different* repository is considered in the same pass,
   **Then** that other repository's item is unaffected and dispatches normally.
4. **Given** a repository with wait-for-merge enabled and a work item stuck in `failed`,
   **When** the author abandons that item, **Then** the repository's queue moves again;
   and **When** the author retries it instead, **Then** it is the item that dispatches.
5. **Given** a repository with wait-for-merge **not** enabled, **When** a session in it
   exits and its pull request is still open, **Then** the next issue dispatches exactly as
   it does today.

---

### User Story 2 - The next issue starts from the merged code (Priority: P2)

Having merged the previous issue's pull request, the author expects the next session in that
repository to start from a default branch that contains it — both in the worktree the session
gets and in the author's own clone, which they read and work in themselves.

**Why this priority**: The gate is worth little if the work that was waited for is not
actually present when the next session starts. It is P2 rather than P1 only because the
worktree half of it already holds: worktrees are created from the remote-tracking base ref
after a fetch, so a session already starts from merged code. The clone's own local default
branch is what goes stale.

**Independent Test**: With wait-for-merge enabled and the clone's local default branch behind
the remote, dispatch an item and confirm the clone's local default branch has advanced to the
remote head and the new worktree contains the merged commit.

**Acceptance Scenarios**:

1. **Given** a wait-for-merge repository whose clone is on a clean default branch that is
   behind the remote, **When** an item in it is dispatched, **Then** the clone's local
   default branch is fast-forwarded to the fetched remote head and the update is recorded.
2. **Given** a wait-for-merge repository whose clone has uncommitted changes, is mid-rebase
   or mid-merge, is not on the default branch, or has local commits the remote does not
   have, **When** an item in it is dispatched, **Then** the clone is left untouched, the
   skip and its specific reason are recorded, and the dispatch proceeds normally.
3. **Given** a repository **without** wait-for-merge enabled, **When** an item in it is
   dispatched, **Then** its clone is never fast-forwarded and nothing about today's
   preparation changes.

---

### User Story 3 - Seeing and setting the limits for a repository (Priority: P3)

The author wants to know, for any repository, how many sessions it may run, whether they
chose that number or inherited it, whether wait-for-merge applies to it, and — when the queue
is not moving — which of those two is responsible.

**Why this priority**: Configuration nobody can inspect is configuration nobody trusts, and a
hold with no reason sends the author to the log. Most of this surface already exists for the
concurrency cap; this story extends it to the new setting rather than inventing it.

**Independent Test**: Set wait-for-merge globally on and off for one repository, and confirm
the capacity summary reports the effective value and its source for every repository.

**Acceptance Scenarios**:

1. **Given** wait-for-merge configured globally, **When** the capacity summary is shown,
   **Then** every repository reports the inherited value, and a repository that overrides it
   reports its own value and is distinguishable as having chosen it.
2. **Given** an item held by the concurrency cap and an item held by the merge gate,
   **When** the queue is listed on any surface, **Then** the two holds are reported as
   different reasons, each with the specifics needed to act on it.
3. **Given** a configuration file naming an unknown key inside `[dispatch]` or `[repos.*]`,
   **When** the configuration is loaded, **Then** it is refused with that key named, as it
   is for every other key in those sections.

---

### Edge Cases

- **An unfinished item that will never finish.** A work item stuck in `failed` — say
  preparation failed before a branch existed — holds its repository under this gate, because
  the gate asks whether the repository has unfinished work, not whether that work produced a
  pull request. This is deliberate and it is visible: the hold names the item. The author
  clears it by retrying or abandoning, which are commands that already exist.
- **The gate and the cap both apply.** An item may be held by the concurrency cap and by the
  merge gate at once. One reason is reported, chosen by a fixed precedence, so a surface never
  shows two answers to one question.
- **Ready items do not gate each other.** An item that has never been dispatched is not
  unfinished work; if it were, a repository with two queued issues would deadlock on itself.
- **Simulated items.** A dry-run item is unfinished work in exactly the way a real one is, and
  it gates the same way, because the point of a dry run is to rehearse the real behaviour. The
  gate requires no outward request, so nothing about dry-run isolation changes.
- **Turning the setting off.** Disabling wait-for-merge for a repository releases every hold
  it was causing on the next pass, with no state to unwind, because the hold is computed on
  read and never stored.
- **The clone cannot be inspected.** If the clone's state cannot be determined at all, the
  fast-forward is skipped with that reason recorded, and dispatch proceeds — the worktree is
  built from the fetched remote ref either way, so the session is not harmed by the skip.
- **A repository with no remote.** Nothing is fetched and nothing is fast-forwarded; the skip
  is recorded with that as its reason, as the existing fetch step already does.

## Requirements *(mandatory)*

### Functional Requirements

**Configuration**

- **FR-001**: The system MUST accept a global wait-for-merge setting that applies to every
  repository, defaulting to off so that an existing installation's behaviour is unchanged
  until the author asks for it.
- **FR-002**: The system MUST accept a per-repository wait-for-merge setting that overrides
  the global one, and MUST keep "not set here" distinct from "set here to the same value" so
  a surface can report which setting is responsible.
- **FR-003**: The system MUST refuse to load a configuration that misspells either setting's
  name, naming the offending key, consistent with how every other key in those sections is
  already treated.
- **FR-004**: The system MUST continue to accept the existing global and per-repository
  concurrency limits, with the global default remaining one session per repository, and MUST
  continue to report whether a repository's limit was chosen or inherited.

**The gate**

- **FR-005**: When wait-for-merge is in force for a repository, the system MUST NOT dispatch
  any item in that repository while that repository has an unfinished item — one that has
  been dispatched at least once and has not reached a terminal state.
- **FR-006**: An item that has never been dispatched MUST NOT count as unfinished work for
  the purposes of FR-005.
- **FR-007**: The gate MUST apply per repository only. An unfinished item in one repository
  MUST NOT hold any other repository's work in the same pass.
- **FR-008**: The gate MUST be released by the unfinished item reaching a terminal state, by
  whichever existing path takes it there — the source issue being closed, or the author
  abandoning it.
- **FR-009**: The gate MUST require no request to the source-forge API of its own. It reads
  state the system already maintains.

**Reporting**

- **FR-010**: A held item MUST report a hold reason distinct from every existing reason, with
  detail naming the repository and the unfinished item that is holding it, including that
  item's issue number and its current state.
- **FR-011**: When more than one hold reason applies to an item, exactly one MUST be
  reported, chosen by a fixed and documented precedence.
- **FR-012**: The same hold reason MUST appear on every surface that lists the queue, because
  all of them are computed from one function and no surface may compute its own.
- **FR-013**: Computing the hold MUST remain free of writes and of network access, so that
  rendering a page cannot dispatch, log, or stall.
- **FR-014**: The capacity summary MUST report, per repository, whether wait-for-merge is in
  force and whether that was chosen for the repository or inherited from the global setting.
- **FR-015**: Each time the gate holds an item, and each time a hold is released, the system
  MUST leave a record sufficient to reconstruct which item was held, by what, and for how
  long, using the existing hold-recording path rather than a second one.

**Starting from merged code**

- **FR-016**: When preparing a worktree for a repository with wait-for-merge in force, the
  system MUST fast-forward the clone's local default branch to the remote head it just
  fetched.
- **FR-017**: The fast-forward MUST NOT be performed, and MUST be recorded as a skip with its
  specific reason, when the clone has uncommitted changes, is in the middle of a rebase,
  merge, cherry-pick or bisect, has its default branch checked out nowhere, is on some other
  branch, has no configured remote, or holds commits on its default branch that the remote
  does not — in short, whenever the update would be anything other than a fast-forward of a
  clean checkout.
- **FR-018**: The fast-forward MUST NOT be forced, MUST NOT discard commits, and MUST NOT
  modify any file the author has changed.
- **FR-019**: A skipped or failed fast-forward MUST NOT fail the dispatch. The worktree is
  created from the fetched remote ref regardless, so the session starts from merged code
  either way.
- **FR-020**: Repositories without wait-for-merge in force MUST NOT have their clones
  modified. Nothing about their preparation changes.

### Key Entities

- **Repository setting**: The per-repository resolution of both limits — how many sessions it
  may run, whether wait-for-merge applies, and for each, whether the value was chosen for that
  repository or inherited from the global default.
- **Unfinished item**: A work item in a repository that has been dispatched at least once and
  has not reached a terminal state. The thing the gate counts.
- **Hold**: The reason one queued item is not moving, computed at the moment it is displayed
  and never stored, with a fixed precedence over the other reasons.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a repository with wait-for-merge enabled, at most one issue is ever in
  flight: across a run of ten queued issues, the number of items simultaneously past dispatch
  and short of terminal never exceeds one.
- **SC-002**: An issue in a wait-for-merge repository is dispatched only after its
  predecessor's work is merged: for every dispatch in such a repository, the branch point
  contains the previous item's commits.
- **SC-003**: A held item's reason names what it is waiting for well enough to act on
  without opening the log — the repository, the item, and its issue number are all present.
- **SC-004**: Turning wait-for-merge on or off, globally or for one repository, takes effect
  on the next pass with no restart, no migration, and no stored state to correct.
- **SC-005**: Enabling wait-for-merge for one repository changes nothing about the dispatch
  of any other repository: with the setting on for one of three repositories, the other two
  dispatch at the same rate as with the setting absent.
- **SC-006**: The author's clone is never damaged by the fast-forward: across every state a
  clone can be in — dirty, detached, mid-rebase, ahead of the remote, remote-less — no
  uncommitted change is lost and no commit becomes unreachable.
- **SC-007**: Every hold and every skipped fast-forward is reconstructable from the log alone
  after the fact, without re-running anything.

## Assumptions

- **The gate observes work items, not pull requests.** The issue describes waiting for a pull
  request to be merged; a merged pull request that references its issue closes that issue,
  and a closed source issue already moves its work item to done. Waiting on the work item is
  therefore the same wait, expressed in state the system already maintains and can report,
  and it costs no additional API request per repository per pass. The consequence is
  explicit and accepted: a pull request that does not close its issue leaves the item
  unfinished, and the author closes the issue or abandons the item to release the gate.
- **Unfinished means any non-terminal dispatched item**, including `failed` and
  `interrupted`, not only those awaiting review. A repository asked to work serially has one
  thing in flight, whatever condition that thing is in, and the existing retry and abandon
  commands are how the author decides which.
- **The fast-forward applies only to repositories with wait-for-merge in force.** It is the
  second half of the same request, and touching the author's own clone is not something to
  turn on for repositories that did not ask for it.
- **Merging happens entirely outside robot-army.** The system never opens, approves, merges,
  or closes a pull request as part of this feature. It only notices that the work landed.
- **The existing per-repository concurrency limit is the feature the issue's first item asks
  for.** No second concurrency mechanism is introduced; this feature verifies and documents
  the existing one alongside the new setting.
- **Both settings are read from the existing configuration file** and follow the shape the
  concurrency limit already established: a global default under the dispatch section and a
  per-repository override in that repository's section.
