# Feature Specification: Holding Items and Repositories Out of Dispatch

**Feature Branch**: `robot-army/issue-117-dispatch-holds`

**Created**: 2026-09-03

**Status**: Draft

**Input**: [jantman/robot-army#117](https://github.com/jantman/robot-army/issues/117) — "I have 12 items in my dispatch queue. By happenstance, 4 of them are at the top of the queue but are from a repository that I consider low-priority and 1 more of them is an issue that is extremely low priority. I would like one or both of the following; perform some analysis on how difficult each would be, and then let me decide whether we should implement one or the other or both. 1. The ability to temporarily hold dispatch of specific items and/or all items from specific repositories. These items (or items from these repositories) would not be dispatched until un-held. This should persist across daemon restarts (i.e. be stored in persistent storage). 2. The ability to manually reorder the dispatch queue via the web UI (i.e. via drag-and-drop reordering, or at least via up/down arrow buttons on items) to manually determine the dispatch order. Again, this should persist across daemon restarts (i.e. be stored in persistent storage)."

## Scope decision

The issue asked for an analysis of both options and a decision. The analysis was done and the
decision is recorded here so it is not relitigated during planning:

**This feature is option 1 only — holds. Manual reordering (option 2) is out of scope.**

Why, briefly. Holds land on machinery that already exists: the queue already has a
first-class vocabulary for *eligible but not moving*, every surface already renders the same
plan, and pause already demonstrates a dispatch decision persisted in the database and
surviving restart. Manual reordering, by contrast, introduces a stored order into a system
whose ordering is deliberately computed and stored nowhere, and it would have to be given a
defined composition with two orderings that already exist — the configured mode
(`oldest-first` / `repo-priority`) and, for governed repositories, the GitHub project board
that is re-read on every poll. Most of that cost is semantic rather than mechanical, and the
risk is an order the author can no longer explain to themselves. Holds solve the problem the
issue actually describes — four items and one issue, temporarily, until the author says
otherwise — without disturbing any of it.

If holds turn out to be insufficient, reordering is a better conversation to have once they
exist. Nothing in this feature forecloses it.

## Context: what already exists

Recorded here rather than rediscovered during planning:

- The queue reports *why* an eligible item is not running, choosing one reason from a fixed
  precedence: paused, capacity unobservable, global cap, repository cap, awaiting merge, not
  onboarded, off column, preparation failed. The queue view, `robot-army status`, and the
  dispatcher all read the same computed plan, so a reason added once appears in all three.
- Dispatch can already be paused globally, and that pause is persisted so it survives a
  daemon restart and a reboot, recording when it was set and which front end set it.
- `[repos."owner/name"].priority`, combined with `[dispatch] order = "repo-priority"`,
  already lets the author permanently de-prioritise a whole repository by editing
  configuration. That is a *standing* preference expressed in a file. It is not what this
  issue asks for: the issue asks for a *temporary* statement, made from whichever surface is
  to hand, and taken back the same way.
- Every control in the web interface has a terminal equivalent, and that correspondence is
  verified rather than asserted.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Hold one item that is not worth doing yet (Priority: P1)

The author is looking at the queue and sees one issue that is real but extremely
low-priority, sitting ahead of work they care about. They hold it. It stays in the queue,
visibly held, and is skipped on every dispatch pass until they release it. Everything behind
it moves up and runs.

**Why this priority**: This is the smallest complete slice of the issue's request, and it
delivers value on its own — a single held item already unblocks the queue behind it. Every
later story is a widening of this one.

**Independent Test**: Queue two eligible items, hold the first, confirm the second dispatches
and the first does not, then release the first and confirm it dispatches.

**Acceptance Scenarios**:

1. **Given** two eligible items and capacity for one session, **When** the author holds the
   first and a dispatch pass runs, **Then** the second item is dispatched and the first is
   not.
2. **Given** a held item, **When** the queue is viewed from the terminal or the web
   interface, **Then** it is listed in its normal position, marked as held, and shows when
   the hold was placed.
3. **Given** a held item, **When** the author releases the hold and a dispatch pass runs,
   **Then** the item is dispatched exactly as it would have been had it never been held.
4. **Given** a held item, **When** the author holds it again, **Then** the request is a
   no-op that reports the hold already in place, and the original hold time is unchanged.

---

### User Story 2 - Hold a whole repository's work (Priority: P1)

Four items at the top of the queue all come from a repository the author considers
low-priority this week. Rather than holding four items one at a time — and rather than
holding a fifth tomorrow when polling discovers one — the author holds the repository. Every
item from it, present and future, is skipped until the repository is released.

**Why this priority**: This is the half of the issue that the per-item hold cannot express.
Holding items one by one does not cover items that have not been discovered yet, which is
precisely the situation the issue describes. It is P1 rather than P2 because the reported
problem is four items from one repository, not one item.

**Independent Test**: Queue items from two repositories, hold one repository, confirm only
the other repository's items dispatch, then introduce a *new* item in the held repository and
confirm it too is held without any further action.

**Acceptance Scenarios**:

1. **Given** eligible items in two repositories, **When** the author holds one repository and
   a dispatch pass runs, **Then** no item from the held repository dispatches and the other
   repository's items dispatch normally.
2. **Given** a held repository, **When** a new eligible item in that repository is discovered,
   **Then** it enters the queue already held, with no further action by the author.
3. **Given** a held repository, **When** the author releases it and a dispatch pass runs,
   **Then** its items dispatch in their normal order.
4. **Given** an item that is individually held **and** whose repository is also held, **When**
   the queue is viewed, **Then** a single hold reason is reported that makes clear both holds
   are in force, so releasing only one is not mistaken for a release.

---

### User Story 3 - See every hold in force, including ones holding nothing (Priority: P2)

Days later the author wonders why a repository has been quiet. They ask what is held and get
a complete answer — every item hold and every repository hold, with the time each was placed
— including repository holds that currently match no queued item at all.

**Why this priority**: A hold is a thing the author sets and forgets. A hold that is
invisible because nothing it holds is currently queued is the exact failure mode this
feature would otherwise introduce, and it would be diagnosed as "polling is broken". It is
P2 rather than P1 because the P1 stories are demonstrable without it, not because it is
optional.

**Independent Test**: Hold a repository that has no queued items, confirm the hold is listed
and attributed, then confirm it still takes effect when an item in that repository appears.

**Acceptance Scenarios**:

1. **Given** holds on one item and one repository, **When** the author asks what is held from
   the terminal, **Then** both are listed with what they hold, when they were placed, and
   which surface placed them.
2. **Given** a repository hold matching no currently queued item, **When** the queue is viewed
   in the web interface, **Then** the hold is still shown, so it cannot silently suppress
   work that arrives later.
3. **Given** no holds at all, **When** the author asks what is held, **Then** the answer says
   plainly that nothing is held rather than printing an empty result.

---

### User Story 4 - Holds outlive the daemon (Priority: P1)

The author holds a repository, restarts the daemon, and reboots the machine. The hold is
still in force, still attributed, still showing when it was placed.

**Why this priority**: The issue states this requirement explicitly, twice. A hold that
evaporates on restart is worse than no hold, because the author would believe work was held
when it was already running.

**Independent Test**: Place holds, stop and restart the daemon, confirm the same holds are in
force with unchanged placement times, and confirm nothing dispatched in between.

**Acceptance Scenarios**:

1. **Given** an item hold and a repository hold, **When** the daemon is stopped and started
   again, **Then** both holds are still in force and their recorded placement times are
   unchanged.
2. **Given** a hold placed while the daemon is not running at all, **When** the daemon next
   starts, **Then** the hold is honoured on the first dispatch pass, with nothing from the
   held set dispatched first.

---

### Edge Cases

- **Holding something that is already running.** A hold governs entry into dispatch. It does
  not interrupt a session that is already running, and it does not retract an item whose
  dispatch is already in flight. The surfaces must say so rather than let the author infer
  that a hold stopped a session; the existing cancel path is what stops a session.
- **Holding an item that is not currently eligible.** Failed, interrupted, and blocked items
  can be held. The hold has no visible effect until the item would otherwise become eligible,
  which is the point: holding an item before retrying it is a legitimate sequence.
- **Holding a work item that does not exist**, or **a repository that has never been
  onboarded.** Both are refused with a message naming what was not found, and the terminal
  command exits non-zero. Accepting a mistyped repository name would create a hold that holds
  nothing and reports nothing wrong.
- **Releasing something that was not held.** Not an error. Reported as a no-op, exits zero,
  and is recorded — because "I already released that" and "that was never held" are the same
  outcome to the author and neither deserves a failure.
- **Every eligible item held.** The queue renders every item as held, the dispatcher
  dispatches nothing, and no error is raised. An empty dispatch pass for this reason is a
  correct outcome, not a fault.
- **A held item reaching a terminal state.** When a work item is removed from the database —
  simulated rows being purged, for instance — its hold goes with it. A hold must never
  outlive the thing it holds and become an unattributable row.
- **A held repository being un-onboarded.** Its holds go with it, for the same reason.
- **Interruption mid-change.** A process killed while placing or releasing a hold leaves the
  hold either fully in place or fully absent, never partially recorded.
- **Both surfaces at once.** A hold placed in the web interface is in force for the next
  terminal command and vice versa, with no restart and no cache to invalidate.

## Requirements *(mandatory)*

### Functional Requirements

**Placing and releasing**

- **FR-001**: The author MUST be able to hold a single work item, identified the same way
  every other per-item command identifies one.
- **FR-002**: The author MUST be able to hold all work from a single repository, identified
  by the repository key already used throughout configuration and the surfaces.
- **FR-003**: The author MUST be able to release either kind of hold individually.
- **FR-004**: Placing a hold that is already in place MUST be a no-op that reports the
  existing hold and leaves its recorded placement time unchanged.
- **FR-005**: Releasing a hold that is not in place MUST be reported as a no-op and MUST NOT
  be treated as a failure.
- **FR-006**: Holding an unknown work item, or a repository that has not been onboarded, MUST
  be refused with a message naming what was not found, and MUST fail rather than succeed
  silently.
- **FR-007**: Every hold and release MUST be available from the terminal and from the web
  interface, with the same effect from either.
- **FR-008**: Each hold MUST record when it was placed and which surface placed it.

**Effect on dispatch**

- **FR-009**: A held item MUST NOT be dispatched, and a held repository's items MUST NOT be
  dispatched, until the corresponding hold is released.
- **FR-010**: A hold MUST NOT stop, cancel, or otherwise disturb a session that is already
  running, and MUST NOT retract an item whose dispatch is already under way.
- **FR-011**: A hold on one repository MUST NOT delay any other repository's items in the
  same dispatch pass. Holding a repository holds that repository's work, never the queue.
- **FR-012**: A hold MUST apply to items discovered after the hold was placed, not only to
  items that existed when it was placed.
- **FR-013**: Releasing a hold MUST restore the exact behaviour the item or repository would
  have had if it had never been held — same order, same position, same gates.

**Reporting**

- **FR-014**: A held item MUST remain visible in the queue, in the position it would occupy
  anyway, marked as held. Holds MUST NOT remove items from view or renumber the items around
  them.
- **FR-015**: The queue MUST report *held* as a reason in the same single-reason form it
  already uses for every other reason an eligible item is not running, so no item ever
  displays two competing explanations.
- **FR-016**: *Held* MUST rank in that precedence directly below *paused* and above every
  capacity, gate, and item-condition reason. Both are statements the author made
  deliberately, and neither is changed by freeing capacity, merging a pull request, or
  fixing the item — so pointing the author at any of those instead would point at a fix that
  cannot work.
- **FR-017**: When an item is held individually **and** its repository is held, the single
  reason reported MUST make both holds evident, so that releasing one is not mistaken for
  releasing the item.
- **FR-018**: The author MUST be able to list every hold in force, from the terminal,
  including repository holds that currently match no queued item.
- **FR-019**: A repository hold that currently matches no queued item MUST still be visible in
  the web interface's queue surface, so a hold can never suppress future work invisibly.
- **FR-020**: Reports MUST show how long each hold has been in place, so a hold set and
  forgotten is recognisable as such.

**Persistence, accountability, and interruption**

- **FR-021**: Holds MUST be stored durably and MUST survive a daemon restart and a machine
  reboot with their recorded placement times unchanged.
- **FR-022**: A hold placed while the daemon is not running MUST be honoured on the daemon's
  first dispatch pass after it starts.
- **FR-023**: Placing and releasing a hold MUST each be written to the audit log at the time
  it occurs, naming what was held or released, when, and from which surface.
- **FR-024**: A change to a hold MUST be atomic: interrupted at any point, the hold is either
  wholly in place or wholly absent, never partially recorded.
- **FR-025**: A hold MUST NOT outlive the thing it holds. When a work item or a repository is
  removed from the system, its holds are removed with it.

**Bounds**

- **FR-026**: Holds MUST NOT expire on their own. A hold is released when the author releases
  it and at no other time; automatic expiry would silently start work the author stopped.
- **FR-027**: Holds MUST NOT alter dispatch order. A released item returns to exactly the
  position the existing ordering gives it. Manual reordering is out of scope for this
  feature.

### Key Entities

- **Hold**: One deliberate statement by the author that some work must not be dispatched.
  It names its scope — a single work item, or a repository — the thing it applies to, when
  it was placed, and which surface placed it. A hold is either present or absent; there are
  no levels, no expiry, and no note. At most one hold exists per scope-and-target, so
  "holding it again" is a no-op rather than a second hold.
- **Work item** (existing): gains no new state. Holds are recorded beside items, not inside
  them, so an item's lifecycle is untouched and a hold can be placed and released without
  the item transitioning at all.
- **Repository** (existing): gains no new configuration. A repository hold is a temporary
  runtime statement, deliberately distinct from the standing `priority` preference expressed
  in the configuration file.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From the queue, the author can take a single action per item, or a single
  action per repository, that stops it dispatching — no file editing, no restart, no
  configuration reload.
- **SC-002**: The exact situation in the issue — four items from one repository plus one
  individually low-priority item — is resolved by two actions, not five.
- **SC-003**: With any set of holds in force, the item the queue names as next to dispatch is
  the item the next dispatch pass actually selects, over a hundred consecutive checks.
- **SC-004**: Holds placed before a daemon restart and a reboot are still in force afterwards,
  with unchanged placement times, and nothing from the held set dispatched in between.
- **SC-005**: Every held item in the queue displays exactly one reason for not running, and
  that reason identifies the hold as the cause.
- **SC-006**: Every hold in force is discoverable from the terminal in one command, including
  repository holds matching no queued item.
- **SC-007**: Placing a hold on a repository, then discovering new work in it, results in that
  work being held with no further author action.
- **SC-008**: Every hold placed and released is reconstructable from the audit log alone —
  what was held, when, and from which surface — without re-running anything.
- **SC-009**: Releasing every hold returns the queue to a state indistinguishable from one in
  which no hold was ever placed.
- **SC-010**: No hold, at any point, ends a running session or changes an item's state.

## Assumptions

- **Single user, one machine.** There is one author and no concurrent second operator, so a
  hold needs no ownership, no locking beyond what the store already provides, and no
  conflict resolution. Recording which *surface* placed it is enough.
- **No note or expiry on a hold.** The issue asks for hold and un-hold. A free-text reason and
  an expiry time are both plausible next asks, and neither has a present need; the audit log
  records the placement and the queue shows the age, which together answer "what is this and
  how long has it been there". Adding either now would be a knob with one caller.
- **Held items keep their queue positions.** Reporting a held item in place, rather than
  hiding it or moving it to the bottom, follows what the queue already does for every other
  hold reason. Moving it would be reordering, which this feature explicitly excludes.
- **Repository holds are runtime state, not configuration.** They live with the other
  persisted dispatch state rather than in the configuration file, because they are temporary
  by nature and the issue asks for them to be settable from the web interface — which does
  not edit configuration.
- **Only two scopes.** Item and repository are what the issue asks for. Holding by label, by
  age, by source, or by pattern is not in scope, and a general predicate would be machinery
  with one caller.
- **Simulated items behave like real ones.** A dry-run item occupies a queue slot today, so it
  can be held today; a hold that ignored simulated work would rehearse the wrong behaviour.
  No outward request is made either way.
- **The existing ordering, gating, and capacity behaviour is unchanged.** This feature adds a
  reason an item may not run. It does not touch how the queue is ordered, how capacity is
  counted, or how any existing gate is decided.
