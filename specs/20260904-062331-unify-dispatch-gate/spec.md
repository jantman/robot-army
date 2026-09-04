# Feature Specification: One dispatch gate on every launch path

**Feature Branch**: `speckit/20260904-062331-unify-dispatch-gate`

**Created**: 2026-09-04

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#120 — "RA-05: resume and restart bypass the concurrency cap, dispatch pause, and holds". Severity High; RA-05 in `docs/security-analysis.md`.

## Why this exists

The system has three deliberate brakes on starting agent sessions: a limit on how many may
run at once, a pause the author can throw over the whole system, and a hold the author can
put on one work item or one repository. All three are applied in one place — the planner
that walks the queue — and only the automatic dispatcher goes through it.

`resume` and `restart` do not. They reach the launch directly, from the terminal and from
the web interface alike, and none of the three brakes is consulted on the way. With a limit
of two sessions and two already running, tapping **Resume** starts a third, then a fourth.
A held item resumes; the hold does not stop it. A paused system resumes; the pause does not
stop it.

The limit exists to protect one person's Claude subscription running on one personal
computer. On this path it is not merely inaccurate — it is absent.

The same shape of gap allows a second failure. Two processes can reach the launch for the
same work item at the same moment — the web interface's worker and a terminal command — and
because re-asserting the state an item already holds is treated as a legitimate no-op, both
proceed. Two agent sessions then start in one worktree, on one branch, editing the same
files.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The session limit holds however a session is started (Priority: P1)

The author has set a limit of two concurrent sessions and two are running. They open the
item page for a paused-for-review item and press **Resume**, or type `robot-army resume 7`.
The system refuses, tells them the machine is full and how full, and starts nothing. When a
session finishes, the same action succeeds.

**Why this priority**: This is the finding's core. Every other part of the change is a
narrower case of the same rule, and this one is the one that spends the author's money and
saturates their machine. The limit protects a resource the system cannot replenish.

**Independent Test**: With the limit set to a number already reached, invoke resume and
restart from the terminal and from the web interface. Each returns a refusal naming the
counts, no new session appears, and the item stays exactly as it was.

**Acceptance Scenarios**:

1. **Given** the session limit is 2 and 2 sessions are running, **When** the author resumes
   an item that is paused for review, **Then** the request is refused with a message giving
   the running count and the limit, and no session starts.
2. **Given** the session limit is 2 and 2 sessions are running, **When** the author restarts
   an interrupted item, **Then** the request is refused the same way and no session starts.
3. **Given** a repository is configured to run at most one session at a time and one of its
   sessions is running, **When** the author resumes a second item in that repository,
   **Then** the request is refused naming the repository and its two numbers, even though
   the machine-wide limit has room.
4. **Given** the number of running sessions cannot be determined at all, **When** the author
   resumes an item, **Then** the request is refused rather than allowed, and the message
   says the count could not be established.
5. **Given** a session finishes and frees a slot, **When** the author resumes the same item
   again, **Then** it starts normally.
6. **Given** the automatic dispatcher is running its ordinary pass, **When** it starts an
   item the queue reported as ready to go, **Then** it starts, unchanged in behaviour and
   with no duplicate refusal recorded.

---

### User Story 2 - A pause and a hold stop every launch path (Priority: P2)

The author pauses the system before going out, or puts a hold on one troublesome item, or
on a whole repository they are mid-rebase in. Resume and restart respect it. The refusal
names which of the three it was, in the same words the queue view already uses, so the
author knows exactly what to lift.

**Why this priority**: A brake that some buttons ignore is worse than no brake, because the
author stops checking. The pause and the holds are the author's direct statements of intent
and the interface currently overrides them silently.

**Independent Test**: Pause the system, then hold an item, then hold its repository, each in
turn, and attempt resume and restart from both surfaces. Each attempt is refused with the
reason for that specific condition.

**Acceptance Scenarios**:

1. **Given** dispatch is paused, **When** the author resumes an item, **Then** the request
   is refused, the message says dispatch is paused and names the command that lifts it, and
   no session starts.
2. **Given** an item is held, **When** the author resumes or restarts it, **Then** the
   request is refused, the message says the item is held and since when and by which
   surface, and no session starts.
3. **Given** an item's repository is held, **When** the author resumes an item in it,
   **Then** the request is refused naming the repository hold.
4. **Given** an item is held *and* its repository is held, **When** the author resumes it,
   **Then** the refusal names both, so releasing one does not look like it was ignored.
5. **Given** the system is paused *and* the machine is at its limit, **When** the author
   resumes an item, **Then** the refusal names the pause, because freeing a slot would
   change nothing.
6. **Given** the author lifts the pause or releases the hold, **When** they resume the same
   item, **Then** it starts normally.

---

### User Story 3 - Only one dispatcher can win an item (Priority: P3)

The author taps **Resume** on their phone and, in the same few seconds, runs
`robot-army resume 7` at their desk. Exactly one of the two starts a session. The other is
told the item was claimed by another dispatcher. One worktree, one branch, one agent.

**Why this priority**: Rarer than the first two — it needs two surfaces used at once — but
the damage is worse and is not automatically recoverable: two agents committing to one
branch in one directory. It is separable from the other stories and can be delivered and
tested on its own.

**Independent Test**: Drive two launches of the same item from two connections so that both
pass their checks before either claims the item. Exactly one launch proceeds; the other is
refused, and its refusal is recorded.

**Acceptance Scenarios**:

1. **Given** two processes are launching the same item and both have passed every check,
   **When** each attempts to claim it, **Then** exactly one succeeds and the other is
   refused with a message saying another dispatcher claimed it.
2. **Given** an item is already starting up, **When** a second launch is attempted for it,
   **Then** the second is refused rather than proceeding silently.
3. **Given** a launch is refused for having lost the claim, **When** the author looks at the
   item, **Then** it is in the state the winning launch put it in, and the losing attempt
   has not changed it, failed it, or removed its worktree.
4. **Given** an item is in a state from which starting a session is not permitted at all,
   **When** a launch is attempted, **Then** it is refused with that reason and nothing is
   written.
5. **Given** reconciliation or replay of a recorded session exit re-asserts a state an item
   already holds, **When** it does so, **Then** that remains a legitimate no-op — this
   change must not turn it into an error.

---

### User Story 4 - The author can override deliberately, from the terminal (Priority: P4)

The author knows the machine is full and wants this one item resumed anyway — the running
session is idle, or they are about to stop it, or they are debugging. `robot-army resume 7
--force` starts it, and the log records that the gate was overridden and what it would have
said.

**Why this priority**: An escape hatch is only needed once the gate exists, and the author
already has two better ones for the ordinary cases: lift the pause, or release the hold —
both of which are one press away on both surfaces. This is for the case where the condition
is real and the author means to go past it anyway.

**Independent Test**: With the machine at its limit and the system paused, run resume with
the override flag. The session starts and the log carries a record naming the condition that
was overridden.

**Acceptance Scenarios**:

1. **Given** the machine is at its session limit, **When** the author runs resume with the
   override flag, **Then** the session starts.
2. **Given** an item and its repository are both held and the system is paused, **When** the
   author runs restart with the override flag, **Then** the session starts and the log
   records every condition that was overridden, not just the first.
3. **Given** the override flag is used, **When** the author reads the log afterwards,
   **Then** they can tell that a launch went past the gate, which item it was, and what the
   gate would have refused it for.
4. **Given** the override flag is used, **When** the item is in a state from which a session
   may not start, or another dispatcher already claimed it, **Then** it is still refused —
   the override covers the author's own policy, never the state machine or the claim.

---

### Edge Cases

- **The item's own previous session.** Resume and restart apply to items whose session has
  already ended, so the item does not count against the limit on its own behalf. If a
  session for that item is somehow still live, the count includes it and the launch is
  refused — which is the right answer, because starting a second agent in that worktree is
  precisely the harm.
- **Rehearsal items.** Simulated items and simulated sessions are counted exactly as they
  are counted today. A rehearsal that ignored the limit would rehearse the wrong system.
- **A refusal is not a failure.** Being paused, held, or full says nothing about the item.
  A refused launch must leave the item's state, its stored failure reason, and its worktree
  untouched — it must not be marked failed, and the author must be able to retry it by
  simply pressing the button again later.
- **A refusal on the web must be visible.** The web interface answers resume and restart
  immediately and does the slow work afterwards, so a refusal discovered late would show the
  author nothing but an unchanged item. The refusal must be reported at the moment the
  author asks, while the authoritative check still runs at the launch itself.
- **The queue's own path must not regress.** The automatic dispatcher already checks these
  conditions before selecting an item. Checking again at the launch must not change what it
  dispatches, must not double-record holds in the log, and must not cost a pass its
  progress.
- **Conditions that belong to queueing, not to launching.** Waiting for a previous pull
  request to merge, sitting in the wrong board column, carrying stale preparation failure —
  these decide whether a *new* item enters the queue. They are not conditions on resuming
  work already begun, and are out of scope here (see Assumptions).
- **Overriding what cannot be overridden.** The override flag covers the author's own policy
  — the limit, the pause, the holds. It does not and must not cover the checks that exist
  for safety: onboarding, the clone's recorded location, workspace trust, the committed
  settings fingerprint, and the issue author check.

## Requirements *(mandatory)*

### Functional Requirements

**The gate**

- **FR-001**: Every path that starts an agent session for a work item MUST pass the same
  gate before the session is started — the automatic dispatcher, `resume`, and `restart`,
  from the terminal and from the web interface alike.
- **FR-002**: The gate MUST refuse a launch when the number of sessions running on the
  machine has reached the configured machine-wide limit.
- **FR-003**: The gate MUST refuse a launch when the item's repository has reached its own
  configured limit on concurrent sessions.
- **FR-004**: The gate MUST refuse a launch when the number of running sessions cannot be
  determined, rather than assuming the machine is idle.
- **FR-005**: The gate MUST refuse a launch while dispatch is paused.
- **FR-006**: The gate MUST refuse a launch while a hold is in force on the item or on the
  item's repository, and when both are, MUST name both.
- **FR-007**: When more than one condition applies, the gate MUST report them in the same
  precedence the queue view already uses, so that one surface never names a fix that cannot
  work while another condition stands.
- **FR-008**: The reason the gate gives MUST use the same vocabulary and the same
  human-readable specifics the queue view uses for the same condition, so the author reads
  one language across the queue, the terminal, and the web interface.
- **FR-009**: The gate MUST observe how full the machine is at the moment it is asked, never
  from a remembered or passed-in count.

**What a refusal does and does not do**

- **FR-010**: A refused launch MUST leave the work item's state unchanged.
- **FR-011**: A refused launch MUST NOT mark the item failed or blocked, MUST NOT write a
  failure reason, MUST NOT create or remove a worktree, and MUST NOT post any outward
  message.
- **FR-012**: A refused launch MUST be repeatable: once the condition is lifted, the same
  action MUST succeed with no intervening repair step.
- **FR-013**: Every refusal MUST be written to the durable action log with the item, the
  condition, the specifics, and the surface that asked.
- **FR-014**: The terminal MUST report a refusal as a precondition failure with the reason
  on standard output and a non-zero exit status, distinguishable from a launch that was
  attempted and failed.
- **FR-015**: The web interface MUST evaluate the gate while the author's request is still
  open and report the refusal in that response, so a refused action is visible immediately
  rather than only in the log.

**Exactly one claimant**

- **FR-016**: Claiming a work item for launch MUST be atomic: of any number of concurrent
  attempts on one item, exactly one MUST succeed.
- **FR-017**: A launch that loses the claim MUST be refused with a reason saying the item
  was claimed by another dispatcher, and MUST NOT change the item in any way.
- **FR-018**: The claim MUST accept only those states from which starting a session is
  legal, and MUST refuse any other state, including an item already starting up.
- **FR-019**: The claim MUST write its record of the state change to the action log in the
  same atomic unit as the change itself, exactly as every other state change does.
- **FR-020**: Re-asserting a state an item already holds MUST remain a legitimate no-op for
  reconciliation and for replay of recorded session exits. This change MUST NOT make those
  paths raise.

**The override**

- **FR-021**: The terminal MUST offer an explicit override on `resume` and `restart` that
  proceeds past the limit, the pause, and the holds.
- **FR-022**: The override MUST NOT be the default, and MUST NOT be reachable without the
  author asking for it by name.
- **FR-023**: An overridden launch MUST record, before it proceeds, every condition the gate
  would have refused it for — not only the first.
- **FR-024**: The override MUST NOT bypass onboarding, the clone's recorded location,
  workspace trust, the committed settings fingerprint, or the issue author check.
- **FR-025**: The override MUST NOT bypass the state machine or the atomic claim.
- **FR-026**: The web interface MUST NOT offer the override. Its escape hatch is lifting the
  condition itself — releasing the hold or unpausing — both of which it already offers.

**Documentation**

- **FR-027**: `docs/security-analysis.md` MUST record RA-05 as resolved, naming what now
  enforces the gate.
- **FR-028**: The documented behaviour of `resume` and `restart` MUST state that they are
  subject to the limit, the pause, and holds, and MUST describe the override.

### Key Entities

- **Launch gate decision**: the answer to "may this item start a session right now?" —
  either permission, or one reason with human-readable specifics. Computed on demand from
  the configuration, the database, and a fresh observation of the machine. Never stored.
- **Dispatch claim**: the single, indivisible act of taking an item for launch. Succeeds for
  at most one attempt; its record is written with it.
- **Override**: a per-invocation instruction from the author to proceed past their own
  policy. Not persisted, not a configuration setting, and recorded whenever exercised.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the machine-wide limit set to *N* and *N* sessions running, no sequence
  of resume or restart actions from any surface starts an *N+1*th session. Attempting each
  of the two actions from each of the two surfaces produces four refusals and zero sessions.
- **SC-002**: With a repository limited to one session and one running in it, resuming a
  second item in that repository is refused while resuming an item in a different repository
  succeeds in the same conditions.
- **SC-003**: While dispatch is paused, and separately while an item or its repository is
  held, every resume and restart attempt on the affected item is refused, and the refusal
  names that specific condition rather than any other.
- **SC-004**: For every refusal above, the item's recorded state before and after the
  attempt is identical, and repeating the action after the condition is lifted starts a
  session on the first attempt.
- **SC-005**: In a test that drives two simultaneous launches of one item, exactly one
  session is started, across at least 50 repetitions with no exceptions.
- **SC-006**: The automatic dispatcher's behaviour is unchanged: for the same database,
  configuration, and machine state, it selects and starts the same items in the same order
  as before this change.
- **SC-007**: For every refusal, a reader of the action log alone can name the item, the
  condition, its specifics, and which surface asked — without re-running anything.
- **SC-008**: The override starts a session in conditions where every one of the three
  policy brakes applies, and the log names all three; the same override does not start a
  session when a safety check fails or the claim is lost.
- **SC-009**: The full unit test suite passes.

## Assumptions

- **Which conditions move.** The gate carries the machine-wide limit, the per-repository
  limit, the unobservable-capacity refusal, the pause, and item and repository holds — the
  three brakes the issue names, plus the two that are inseparable from the limit. The
  queue's remaining reasons — waiting for a previous pull request to land, sitting off the
  board's dispatch column, an unresolvable repository, and stale preparation failure — are
  conditions on admitting a *new* item to the queue, not on resuming work already begun, and
  stay where they are. The unresolvable-repository case is already refused at the launch by
  an existing check, so nothing is lost.
- **Precedence is inherited, not reinvented.** The order in which competing reasons are
  reported is the queue view's existing order. Defining a second order would guarantee the
  two surfaces eventually disagree.
- **The gate is one shared decision, not two copies.** The queue view and the launch must
  answer this question with the same code, for the same reason the dispatch order is
  produced in exactly one place today.
- **Naming the override.** The terminal flag is `--force`, following the issue. `robot-army
  cancel --force` already means "skip the confirmation prompt", a different thing; the help
  text for each says which it is. A second vocabulary for one concept was judged worse than
  one word doing two jobs in two commands.
- **Overriding is not configurable.** There is no setting that turns the gate off standing;
  the override is per invocation only. A permanent bypass is the bug this change is fixing.
- **The web keeps its immediate answer.** The web interface's existing shape — answer at
  once, do the slow work on a worker — is unchanged. The gate is evaluated in the request so
  the author sees the refusal, and again at the launch because only that check is
  authoritative.
- **No new storage.** Nothing about the gate or the claim requires a schema change; the
  pause, the holds, the states, and the timestamps all exist already.
- **What this logs** (Constitution Principle III): every refusal, every override with the
  conditions it went past, and the claim itself — the last already logged as the state
  change it is.
- **What happens if it is killed halfway** (Constitution Principle IV): the claim is a
  single atomic write, so an interruption leaves the item either not claimed or claimed and
  recorded, never between. An interruption after a successful claim leaves the item starting
  up, which the existing reaper already resolves. Refusals write nothing, so there is
  nothing to leave half-written.
