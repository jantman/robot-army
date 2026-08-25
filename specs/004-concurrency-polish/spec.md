# Feature Specification: Concurrency & Polish

**Feature Branch**: `004-concurrency-polish` *(not created — no `before_specify` git hook is configured in this project)*

**Created**: 2026-08-25

**Status**: Draft

**Input**: User description: "the next milestone in our roadmap"

**Scope note**: This is milestone 004 of the roadmap in [`docs/roadmap.md`](../../docs/roadmap.md),
corresponding to M4 in the planning document (§5 Priority & Ordering, §6 Cleanup, §10 Concurrency,
and the notifications line of §14). It is the milestone where the system stops assuming it is the
only thing running on the machine.

Everything here is **policy over observations milestone 001 already makes**. 001 built the session
registry scan, the global cap, the per-repo configuration table, and the worktree boundary. This
milestone does not add a new source, a new interface, or a new kind of work. It makes the cap
honest about sessions the daemon did not start, gives repositories a cap and an order of their own,
tells the author where in line a held item sits, reclaims the disk that finished work leaves behind,
and says something out loud when a run starts, finishes, or fails.

**No new external system is introduced.** GitHub, Trello, kitty, git, and the local filesystem are
the same set milestone 003 finished with, and notifications reuse the health channel built in 001.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - My own work and the robot's work share one quota (Priority: P1)

The author sits down and starts two Claude sessions of their own. The daemon, which knows nothing
about those sessions today, currently dispatches its own on top of them and all four contend for one
subscription. After this story, the daemon counts every Claude session on the machine — its own and
the author's — against the global cap, holds work in `ready` when the machine is full, and says so
in the log and in both interfaces. When the author closes their sessions, the held work goes on its
own.

**Why this priority**: This is the one behaviour in the milestone that prevents an actively bad
outcome rather than an inconvenient one. The cap that exists today is a cap on the daemon's own
sessions, which means it protects nothing on the machine where the author actually works. With only
this story shipped, the system is already meaningfully better behaved and nothing else has changed.

**Independent Test**: With the global cap set to two, start two Claude sessions by hand outside the
daemon, make one item eligible, and confirm nothing is dispatched, the reason is visible from the
terminal, and dispatch happens on its own within one dispatch interval of closing one of them.

**Acceptance Scenarios**:

1. **Given** a global cap of N and N live Claude sessions on the machine of which some are not the
   daemon's, **When** dispatch runs, **Then** nothing is dispatched, the eligible items stay in
   `ready`, and one record naming the counts and the cap is written.
2. **Given** the same state, **When** one out-of-band session exits, **Then** the next dispatch pass
   dispatches one item with no human action.
3. **Given** a simulated (dry-run) session running, **When** capacity is computed, **Then** it counts
   against the cap exactly as a live one does.
4. **Given** an out-of-band session, **When** the daemon acts on capacity, **Then** it never
   terminates, signals, resumes, or otherwise touches that session.
5. **Given** the session registry is unreadable, reports a version the system does not know, or is
   missing entries, **When** capacity is computed, **Then** the system does not dispatch on an
   under-count: it uses the fallback observation path, and if that is also unavailable it holds
   dispatch and records the degradation rather than proceeding.
6. **Given** more live sessions than the cap allows — because the cap was lowered, or the author
   started several by hand — **When** dispatch runs, **Then** running work is left alone and only new
   dispatch is withheld.

---

### User Story 2 - Two sessions never fight over one repository (Priority: P2)

Three issues are labelled in the same repository at once. Today all three can be dispatched together
into three worktrees of the same clone, competing for the same ports, the same dev server, and the
same submodule fetches. After this story a repository carries its own cap — one by default — and the
second and third items wait their turn.

**Why this priority**: The planning document names the collision this prevents, and it is the reason
the per-repo cap exists at all. It ranks below the global cap because the failure it prevents is
messy rather than expensive, and because a global cap of two already limits the blast radius.

**Independent Test**: Label two issues in one repository with a global cap of two and a per-repo cap
of one, and confirm exactly one is dispatched, the other is held with the repository named as the
reason, and it dispatches when the first finishes.

**Acceptance Scenarios**:

1. **Given** a per-repo cap of one and an active session for a repository, **When** a second item for
   that repository becomes eligible, **Then** it is held in `ready` and the reason names the
   repository, not the global cap.
2. **Given** a repository at its cap and another repository below both caps, **When** dispatch runs,
   **Then** the held item does not block the other repository's item from being dispatched.
3. **Given** a repository whose only session is simulated, **When** a live item for the same
   repository is considered, **Then** the simulated session counts against the per-repo cap.
4. **Given** a repository with no cap of its own configured, **When** capacity is computed, **Then**
   the documented default applies and is reported as a default rather than as an explicit setting.
5. **Given** both caps would allow dispatch, **When** dispatch runs, **Then** behaviour is
   indistinguishable from milestone 003.

---

### User Story 3 - I can see what is waiting, where it is in line, and why (Priority: P3)

The author looks at the terminal or the web page and sees not just that four items are `ready`, but
that item A is next, item B is third, that item C is held because its repository is busy rather than
because the machine is full, and how many sessions are running of how many allowed — including how
many of those are the author's own.

**Why this priority**: Holding work is only acceptable if the hold is legible. Without this, the two
stories above turn a visible queue into an invisible stall, which is precisely the silent no-op the
planning document warns about. It ranks below them because they are what create the hold in the first
place.

**Independent Test**: Fill the machine to capacity, make three items eligible, and confirm both
interfaces show a stable ordering with positions, a per-item reason for being held, and a capacity
summary that distinguishes the daemon's sessions from the author's.

**Acceptance Scenarios**:

1. **Given** several eligible items held at capacity, **When** they are listed in the terminal or the
   web interface, **Then** each shows its position in the dispatch order and the specific reason it is
   not running.
2. **Given** a capacity summary is shown, **When** it is read, **Then** it states sessions running,
   the global cap, and how many running sessions are not the daemon's.
3. **Given** an item held by the per-repo cap and an item held by the global cap, **When** both are
   listed, **Then** their reasons are distinguishable without consulting the log.
4. **Given** dispatch is paused, **When** items are listed, **Then** the pause is shown as the reason
   in preference to a capacity reason, so the author is not misled into freeing capacity that would
   change nothing.
5. **Given** the ordering the queue reports, **When** the next dispatch runs and nothing else has
   changed, **Then** the item it dispatches is the one the listing named as next.

---

### User Story 4 - Work runs in the order I chose (Priority: P4)

The author has one repository that matters more than the others. They configure the ordering mode so
that everything eligible in that repository runs before anything else, or they leave it on strict
oldest-first and get global fairness. Either way the order is stated, not emergent.

**Why this priority**: The planning document is explicit that this is worth little engineering and is
revisitable, and the system is usable without it. It is in this milestone because the queue-position
story above forces the ordering to become an explicit, inspectable decision rather than an accident of
row order.

**Independent Test**: With items eligible in three repositories, run under each ordering mode and
confirm the dispatch order matches the mode's stated rule, and that the mode in force is reported.

**Acceptance Scenarios**:

1. **Given** strict oldest-first ordering, **When** items across several repositories are eligible,
   **Then** they are dispatched in order of when the work item was created, regardless of repository.
2. **Given** repository-priority ordering, **When** items are eligible in a higher-priority and a
   lower-priority repository, **Then** every eligible item in the higher-priority repository is
   dispatched before any item in the lower-priority one.
3. **Given** repository-priority ordering and two repositories of equal priority, **When** items in
   both are eligible, **Then** the tie is broken by the oldest-first rule.
4. **Given** a repository with no priority configured, **When** ordering is computed, **Then** the
   documented default priority applies.
5. **Given** any ordering mode, **When** the mode is in force, **Then** it is reported in the terminal
   and the web interface, and a change to it takes effect without restarting the daemon losing or
   reordering work already running.
6. **Given** the item at the head of the order cannot be dispatched for a reason of its own — its
   repository is at its cap, its repository is not onboarded, its worktree preparation failed — **When**
   dispatch runs, **Then** later items are still considered, so one stuck item cannot block the queue.

---

### User Story 5 - Finished work stops eating the disk (Priority: P5)

A worktree measured at 499 MB is left behind by every completed item. The author turns on cleanup;
from then on, when an item's issue is closed, its worktree and its branch are removed — unless there
is anything in that worktree that has not been saved anywhere else, in which case it is kept and the
author is told why.

**Why this priority**: It is the only story here that deletes anything, and deletion is worth less
than correctness. Disk is recoverable by hand today with an existing command; the cost of getting this
wrong is unrecoverable work.

**Independent Test**: Take one item to a closed issue with a clean worktree whose commits are pushed
and confirm both worktree and branch are gone; take a second with an uncommitted change and confirm
both survive, with the reason recorded and visible.

**Acceptance Scenarios**:

1. **Given** cleanup is not explicitly enabled, **When** any item completes or its issue is closed,
   **Then** nothing is removed. Cleanup is never on by default.
2. **Given** cleanup is enabled and an item's issue is observed closed with no session running for it,
   **When** cleanup runs, **Then** the worktree is removed and then its branch is removed, as two
   recorded steps, and the item's record retains what was removed.
3. **Given** a worktree with uncommitted or untracked changes, **When** cleanup runs, **Then** neither
   the worktree nor the branch is removed, the item is surfaced as retained with the reason, and no
   forcing option is used on the system's own initiative.
4. **Given** a branch carrying commits that exist nowhere else, **When** cleanup runs, **Then** the
   branch is not removed even if the worktree was, and the retained branch is surfaced.
5. **Given** a session is still running for the item, **When** cleanup would run, **Then** it does not
   run, and this is recorded rather than silently skipped.
6. **Given** a worktree whose directory has been deleted outside the system, **When** it is observed,
   **Then** it is surfaced as such and the existing prune path clears git's record of it.
7. **Given** any removal, **When** it is attempted, **Then** it is recorded before the attempt and
   again with its outcome, naming exactly what was removed.
8. **Given** the author wants to reclaim disk now, **When** they ask from the terminal, **Then**
   cleanup runs immediately against everything currently eligible for it, under the same guards.

---

### User Story 6 - The system tells me when something happens (Priority: P6)

The author is not watching. A run starts, a run finishes, a run fails, or a card is waiting on them —
and a message arrives on the same channel that already carries the health signal.

**Why this priority**: The planning document calls this "stretch, but cheap once the health channel
exists". It is last because nothing depends on it and the information is already available by asking.

**Independent Test**: Enable notifications for failures only, force one failure and one success, and
confirm exactly one message is sent, carrying enough to identify the item.

**Acceptance Scenarios**:

1. **Given** notifications are configured for a set of event kinds, **When** an event of a configured
   kind occurs, **Then** exactly one message is sent identifying the item, the repository, the event,
   and where to look.
2. **Given** an event kind that is not configured, **When** it occurs, **Then** no message is sent.
3. **Given** notifications are not configured at all, **When** any event occurs, **Then** behaviour is
   exactly as in milestone 003 and no outbound request is made.
4. **Given** the notification channel is failing or slow, **When** an event occurs, **Then** the
   failure is recorded, dispatch and reconciliation are unaffected, and the send is not retried
   indefinitely.
5. **Given** a backlog is worked through and many events occur close together, **When** they are sent,
   **Then** the volume is bounded by a documented rule rather than being one message per event without
   limit.
6. **Given** any notification, **When** it is composed, **Then** it contains no credential.

---

### Edge Cases

- The session registry reports an entry whose process is dead, or whose process start time no longer
  matches — a recycled PID must never be counted as a live session, and must never be mistaken for the
  author's.
- Every live session on the machine is the author's own and there are more of them than the cap. The
  daemon must hold quietly and repeatedly without treating each pass as a new incident worth alarming
  about.
- A session belonging to the daemon exists on the machine with no matching active row — an orphan.
  Capacity must count it, because it is consuming quota, while it remains an anomaly for reconciliation
  rather than a number quietly absorbed.
- The global cap is lower than the per-repo cap, or a per-repo cap exceeds the global cap. The
  configuration is contradictory but harmless; the effective limit is the lower one and the situation
  is reported at load rather than discovered at dispatch.
- A repository's configured priority names a repository that is not configured, or the ordering mode
  names a mode that does not exist. Configuration must fail at load, loudly, not fall back silently.
- Queue position is read, then the world changes — an item is cancelled, a session dies, the author
  labels something older — before the next dispatch. The position is a report of the current order, not
  a promise, and must not be persisted as though it were a property of the item.
- An issue is closed, cleanup removes the worktree, and the issue is then reopened. Reopening must not
  be treated as a state the system cannot describe.
- Cleanup runs while the author has a terminal open inside the worktree, or is attached to its session.
- The disk is full when a hook runs; cleanup is the thing that would have prevented it. The failure must
  name the cause rather than reporting an unrelated hook error.
- Two dispatch passes overlap, or a dispatch pass and a manual dispatch overlap. The cap must not be
  exceeded by a race between two capacity checks.
- The notification channel and the health webhook are the same endpoint and the health signal is
  failing. Neither may starve or suppress the other.

## Requirements *(mandatory)*

### Capacity Accounting

- **FR-001**: The system MUST compute capacity from the live sessions present on the machine, not from
  its own records alone. Every Claude session running as the operating-system user MUST be counted
  against the global cap, whether the system started it or not.
- **FR-002**: Sessions MUST be identified by the same exact means milestone 001 established — the
  session registry, guarded by its version field, with a dead-process and start-time check — and MUST
  NOT be identified by matching command lines under any circumstances.
- **FR-003**: The system MUST classify each counted session as its own or out-of-band by the location
  the session is running in, and MUST report the two counts separately wherever capacity is reported.
- **FR-004**: Simulated sessions MUST count against both the global and the per-repo cap, because they
  consume the same subscription quota and the same worktree.
- **FR-005**: A session the system started that has no corresponding active record MUST be counted as
  consuming capacity, and MUST continue to be surfaced as an anomaly. Counting it MUST NOT resolve or
  suppress the anomaly.
- **FR-006**: The system MUST NOT terminate, signal, resume, attach to, or otherwise act upon a session
  it did not start. Out-of-band sessions are observed and counted only.
- **FR-007**: When the observation of live sessions is degraded — the registry is unreadable, reports an
  unknown version, or is absent — the system MUST fall back to the secondary observation path, and if
  no reliable count is available MUST withhold dispatch and record the degradation. It MUST NOT dispatch
  on an assumption that capacity exists.
- **FR-008**: When live sessions already exceed the cap, the system MUST withhold new dispatch only. It
  MUST NOT stop, kill, or interfere with work already running.
- **FR-009**: Capacity MUST be evaluated immediately before each individual dispatch, so that a batch of
  eligible items cannot collectively exceed the cap, and so that two overlapping dispatch attempts
  cannot each observe the same free slot.

### Per-Repository Limits

- **FR-010**: Each repository MUST have a maximum number of concurrent sessions, configurable per
  repository, with a documented default that applies where none is set.
- **FR-011**: An item MUST NOT be dispatched when its repository is at its cap, and MUST remain in
  `ready` rather than entering any new state.
- **FR-012**: A repository at its cap MUST NOT prevent items in other repositories from being
  dispatched.
- **FR-013**: The reason an item is held MUST distinguish the global cap, the per-repo cap, and a
  dispatch pause from one another, and MUST be available without reading the log.
- **FR-014**: Per-repository configuration MUST be validated when configuration is loaded. Contradictory
  or unresolvable values — a per-repo cap above the global cap, a priority referring to nothing, an
  unknown ordering mode — MUST be reported at load time, and unresolvable ones MUST prevent startup
  rather than being discovered mid-dispatch.

### Ordering & Queue

- **FR-015**: The order in which eligible items are considered for dispatch MUST be an explicit,
  configurable policy rather than an artefact of storage order.
- **FR-016**: The system MUST support at least two ordering modes: strict oldest-first across all
  repositories, and repository-priority ordering in which all eligible work in a higher-priority
  repository is considered before any work in a lower-priority one, ties broken oldest-first.
- **FR-017**: The ordering mode in force MUST be reported in the terminal and in the web interface.
- **FR-018**: Every eligible item that is not running MUST be reportable with its position in the
  current order and the reason it is not running.
- **FR-019**: Queue position MUST be derived at the moment it is reported and MUST NOT be stored as a
  property of the item, so that it cannot become stale or disagree with the dispatcher.
- **FR-020**: An item that cannot be dispatched MUST NOT prevent later items in the order from being
  considered in the same pass.
- **FR-021**: The system MUST NOT implement aging, starvation avoidance, or dynamic re-prioritisation.
  Starvation of a low-priority repository under repository-priority ordering is an accepted, documented
  consequence of choosing that mode.

### Worktree Cleanup

- **FR-022**: Automatic cleanup MUST be off unless explicitly enabled in configuration. It MUST NOT be
  reachable by default.
- **FR-023**: When enabled, cleanup MUST be triggered by an item's issue being observed closed with no
  session running for that item.
- **FR-024**: Cleanup MUST be two recorded steps — removing the worktree, then removing its branch — and
  MUST record which steps completed, so that a partial cleanup is describable rather than ambiguous.
- **FR-025**: The system MUST NOT force the removal of a worktree that has uncommitted or untracked
  changes. Where removal is refused, the item MUST be surfaced as retained with the reason, and MUST NOT
  be retried in a way that would eventually force it.
- **FR-026**: The system MUST NOT remove a branch whose commits exist nowhere else. Where the worktree
  was removable but the branch was not, the branch MUST be retained and surfaced.
- **FR-027**: Cleanup MUST NOT run for an item with a live session, and declining to run for that reason
  MUST be recorded.
- **FR-028**: Every removal MUST be recorded before it is attempted and again with its outcome, naming
  the worktree path, the branch, and the result.
- **FR-029**: The author MUST be able to trigger cleanup immediately from the terminal, against
  everything currently eligible or against one named item, under the same guards as the automatic path.
- **FR-030**: A worktree whose directory no longer exists MUST be surfaced as such and MUST be
  reconcilable through the existing prune path, without cleanup treating it as a failure.
- **FR-031**: Cleanup MUST NOT touch the author's own clone of a repository, only worktrees the system
  created under its own worktree root.

### Notifications

- **FR-032**: The system MUST be able to send a message on significant events — at minimum dispatch,
  completion, failure, and an item awaiting the author — over the same channel the health signal uses.
- **FR-033**: Which event kinds notify MUST be configurable, and no event kind MUST notify unless
  configured. With nothing configured, no outbound request is made.
- **FR-034**: Each message MUST identify the item, its repository, the event, and where the author can
  look for detail.
- **FR-035**: A notification failure MUST be recorded and MUST NOT fail, delay, or retry the operation
  that triggered it. Retries MUST be bounded.
- **FR-036**: The volume of notifications MUST be bounded by a documented rule, so that working through
  a backlog cannot produce an unbounded stream of messages.
- **FR-037**: No credential and no secret MUST appear in any notification.

### Effect Levels & Dry Run

- **FR-038**: Capacity observation MUST be real at every effect level, exactly as polling is, since a
  simulated capacity check tells the author nothing.
- **FR-039**: Worktree and branch removal MUST follow the same effect-level rule as worktree creation:
  simulated at the planning level, real where worktree creation is real, and enforced at the boundary
  rather than at call sites.
- **FR-040**: Notification sends MUST be treated as outward-facing writes and MUST be simulated below
  the live effect level, emitting a record naming the call and its full arguments and returning a
  structurally valid result.
- **FR-041**: Nothing in this milestone may make the `dry_run` flag govern resource accounting. The flag
  governs reporting and outward-facing effects only.

### Accountability & Observability

- **FR-042**: Every dispatch decision — taken or withheld — MUST be recorded with the counts and limits
  that produced it, such that the reason any item did or did not run at a given moment is reconstructible
  from the log alone.
- **FR-043**: A capacity hold that persists across many passes MUST remain reconstructible without
  writing one indistinguishable record per pass forever; the retention or summarisation rule MUST be
  documented and MUST NOT discard the fact that the hold occurred.
- **FR-044**: Every capability this milestone adds — capacity and queue inspection, ordering mode
  inspection, immediate cleanup, and notification configuration — MUST be reachable from the terminal,
  and the web interface MUST NOT be a prerequisite for any of it.
- **FR-045**: Commands added here MUST exit non-zero on failure.
- **FR-046**: Adding these policies MUST NOT change observable behaviour when a single item is eligible,
  the machine is idle, cleanup is disabled, and notifications are unconfigured — the milestone 003
  behaviour MUST be recoverable by configuration alone.

### Key Entities

- **Capacity Snapshot**: What the system observed about the machine at one moment — sessions running,
  how many are its own, how many are not, the global limit, and the per-repository counts. Derived on
  demand, never stored as truth.
- **Repository Policy**: The per-repository settings that govern dispatch — its concurrent-session cap
  and its priority — alongside the base branch, environment, hooks, and worker settings that already
  exist. Absent values fall back to documented defaults.
- **Ordering Mode**: The named, configured rule that turns the set of eligible items into a sequence.
- **Queue Entry**: An eligible item's position in the current order together with the reason it is not
  running. A view, not a record.
- **Cleanup Outcome**: What was attempted for one item's worktree and branch, which steps completed,
  and — where a step was declined — the reason it was declined. Durable, because it is the answer to
  "why is this 499 MB still here?".
- **Notification Event**: An occurrence the author asked to be told about, its kind, the item it
  concerns, and whether the send succeeded.
- **Work Item, Session, Repository, Audit Record, Anomaly, Card**: As defined in milestones 001 and 003
  and unchanged here.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the global cap set to N, the number of Claude sessions running on the machine never
  exceeds N as a result of the system's dispatch, measured across a run in which the author starts and
  stops their own sessions at arbitrary times.
- **SC-002**: When capacity is freed by an out-of-band session exiting, held work is dispatched within
  one dispatch interval with no human action.
- **SC-003**: Across a run in which capacity is exhausted and restored repeatedly, the system terminates
  zero sessions it did not start.
- **SC-004**: With a per-repo cap of one, the number of concurrent sessions in a single repository never
  exceeds one, measured across at least twenty dispatches with several items eligible in that repository
  throughout.
- **SC-005**: For every eligible item that is not running, the author can determine the reason and its
  position in line from the terminal alone, without reading the audit log and without consulting the
  code.
- **SC-006**: The item the queue listing names as next is the item the next dispatch selects, in one
  hundred consecutive checks where nothing changed in between.
- **SC-007**: Under repository-priority ordering, no item in a lower-priority repository is dispatched
  while any item in a higher-priority repository is eligible and dispatchable.
- **SC-008**: With cleanup enabled, disk occupied by worktrees of items whose issues are closed and whose
  work is saved elsewhere returns to zero without human action, while the number of worktrees removed
  that contained unsaved work is zero.
- **SC-009**: Across a run that includes at least one dirty worktree, one branch with unpushed commits,
  one running session, and one externally deleted directory, cleanup destroys nothing it should have kept
  and each retention is explained where the author will see it.
- **SC-010**: With notifications configured for a subset of event kinds, the number of messages sent for
  unconfigured kinds is zero, and no message contains a credential — verified across a run that includes
  at least one failure of the notification channel itself.
- **SC-011**: Below the live effect level, a full cycle including cleanup and notifications produces zero
  outbound messages and zero removals not permitted at that level, while the log shows every suppressed
  action with its full arguments.
- **SC-012**: The dispatch decision at any past moment — what ran, what was held, and against which
  counts and limits — is reconstructible from the audit log alone, without re-running anything.
- **SC-013**: With cleanup disabled and notifications unconfigured, a full cycle produces the same
  observable behaviour as milestone 003 for a single eligible item on an idle machine.

## Assumptions

- **The global cap's mechanism is settled; its value is configuration.** The planning document's §16
  leaves the *number* open and its §10 settles the *method*. This specification takes the method as
  given and treats the number as a documented default the author changes in configuration, because no
  amount of specification determines it — only using the system does.
- **Out-of-band means "not started by this system", determined by where the session is running.** A
  session whose working directory is under the system's worktree root is its own; anything else is the
  author's. This is the classification milestone 001 already makes for reconciliation, reused rather
  than re-derived.
- **Counting is exact, and a degraded observation withholds dispatch rather than guessing.** The
  alternative — assume capacity when the registry cannot be read — trades a visible stall for an
  invisible over-dispatch, and the stall is the better failure.
- **The per-repo default cap is one.** The planning document says "probably 1, to avoid worktree and
  dev-server collisions", and every measured collision risk in §6 is per-clone rather than per-worktree.
  A repository that tolerates more says so in its own configuration.
- **Ordering modes are global, not per repository.** A per-repository choice of ordering mode is a
  contradiction — the modes describe how repositories relate to each other. Repositories carry a
  priority; the mode decides whether that priority is consulted.
- **Starvation is accepted and not mitigated.** The planning document accepts it explicitly and defers
  aging until it becomes annoying. Building aging now would be speculative generality with one caller.
- **Queue position is a view, never a stored field.** A stored position is a second source of truth that
  can disagree with the dispatcher, and reconciling the two would be work the system does not need.
- **Cleanup is triggered by issue close, and is opt-in.** Of the four triggers the planning document
  lists — issue close, PR merge, age, manual only — issue close is the one it argues for on the strength
  of the 499 MB measurement, and it is the one that follows the artefact the author actually acts on. PR
  merge is not used, because not all work produces a PR and a merged PR does not mean the author is
  finished. Age is not used, because time says nothing about whether work is done. Opt-in rather than
  default follows from the constitution's rule that irreversible actions must not be reachable by
  default; the manual command remains available whether or not the automatic path is enabled.
- **"Saved elsewhere" is the test for removing a branch, and git's own refusal is the test for removing a
  worktree.** The worktree guard is free — git refuses on a dirty tree, including merely untracked files,
  and the system simply never forces it. The branch guard is not free and has to be checked, because
  deleting a branch whose commits exist nowhere else destroys work irrecoverably and silently.
- **Notifications reuse the health channel's transport and vendor-neutral shape.** A second delivery
  mechanism would be a new dependency for a stretch feature, which Principle I forbids without a
  demonstrated need. The channel built in milestone 001 already reaches the author.
- **Notification bounding is by suppression of repeats, not by queueing or scheduling.** A durable outbound
  queue with its own retry and persistence would be more machinery than the feature is worth.
- **Per-repository overrides that already exist are not re-specified.** Base branch, environment
  injection, post-create hooks, permission mode, and model are milestone 001's and unchanged. This
  milestone adds only the cap and the priority.
- **All of this runs inside the existing daemon process**, on the existing loops, with no new process,
  scheduler, or worker.

## Out of Scope

- Aging, fair-share scheduling, deadline scheduling, preemption, and any policy that reorders or stops
  work already dispatched.
- Terminating, throttling, or negotiating with the author's own sessions in any way.
- Subscription usage or rate-limit awareness beyond counting concurrent sessions — the planning document
  defers this and the concurrency cap covers the practical need.
- Multi-machine dispatch and any notion of capacity beyond this one machine.
- Cleanup triggered by PR merge, by age, or by any signal other than issue close and the manual command.
- Removing branches or worktrees in the author's own clones, and any operation on the main clone.
- Archiving, compressing, or backing up a worktree instead of removing it.
- A notification channel other than the one the health signal already uses, message templating,
  formatting per vendor, and two-way interaction from a notification.
- Per-repository ordering modes, per-repository notification settings, and per-board or per-source
  concurrency limits.
- Kitty control socket hardening, multi-machine dispatch, and scheduled or proactive work — milestone 005.
