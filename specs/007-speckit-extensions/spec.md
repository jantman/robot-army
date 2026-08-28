# Feature Specification: Spec Kit Awareness

**Feature Branch**: `007-speckit-extensions`

**Created**: 2026-08-27

**Status**: Draft

**Input**: User description: "issue #9 on this repo" — *"[spec-kit](https://github.com/github/spec-kit)
provides support for extensions files that define hooks to be run at various stages in the
(specify/plan/tasks/implement) lifecycle. Given that >50% of my development work uses spec-kit,
perhaps we should evaluate whether using extensions could improve robot-army (as one possible
example, say, detecting if a repo has spec-kit in it, and if so, automatically populating extensions
so that robot-army can monitor and/or drive the spec-kit process)."*

**Scope note**: This is the resolution of
[issue #9](https://github.com/jantman/robot-army/issues/9). It claims the slot
[`docs/roadmap.md`](../../docs/roadmap.md) currently reserves for 007, "whatever survives contact
with reality", which moves to 008 — the third time that parking lot has been displaced, and for the
third time by the same argument: a milestone with a shape displaces one without.

**The issue asks a question rather than describing a feature**, so this specification answers it
before it requires anything. The answer is that extensions are the *smallest* of the three things
the issue is reaching for, and the least trustworthy — and that the value it wants is available
without them. This milestone therefore **does not use extensions**. The evaluation the issue asked
for is recorded in [Out of Scope](#out-of-scope) rather than acted on, including what would make it
worth revisiting.

**Nothing in this milestone writes anything into a worktree.** Every capability below is a read, a
prompt, or a listing.

### What the daemon does not know today

A dispatched session starts with the issue's title, body, URL, labels, its branch and its worktree —
and nothing else. What it then does with a task is entirely its own decision: write a spec first,
or start editing files. In a repository that uses Spec Kit the author almost always wants the
former, and today the only way to say so is per repository, by hand, in that repository's
`.claude/robot-army.md`. That is milestone 005's lesson repeating verbatim: a file edit, repeated
per repository, for something the repository's own contents already state.

The second gap is on the other side of dispatch. The daemon knows a session is `active` and knows
nothing further about it. For free-form work that is honest, because free-form work has no named
stages. Spec Kit work does: specify, plan, tasks, implement are hours apart, each one writes a file
at a documented path, and on `/active` a session five minutes into `/speckit-specify` is
indistinguishable from one three hours into `/speckit-implement`. The author is looking at that page
from a phone precisely when the difference matters.

Two things close those gaps, and they are the two user stories that follow: **tell the session**
(prompt) and **watch the files** (observation). A third exists in Spec Kit and is not used here; the
next section is why.

### What extensions actually are, and what they are not

`.specify/extensions.yml` registers hooks under keys named for lifecycle points —
`before_specify`, `after_plan`, `after_implement`, and so on for every command. A hook names another
**command** to invoke, plus a description, a prompt, and whether it is optional.

The mechanism is worth stating precisely, because the issue's phrasing ("so that robot-army can
monitor") reads as though it were a callback:

- A hook is **read and executed by the agent**, as part of following its own command instructions.
  Nothing in Spec Kit calls out to anything. There is no daemon-side event.
- A hook can only name a command that **exists in that repository's integration**. Registering a
  hook whose command is absent registers a failure.
- A session that does not follow a hook is not observably different from one that has not reached
  the hook point yet.

So a hook is a **report the session chose to make**. The filesystem is not: Spec Kit writes
`spec.md`, `plan.md` and `tasks.md` into a feature directory it names in `.specify/feature.json`,
and reading those requires no cooperation, no injection, and no trust in the session at all. The two
mechanisms answer nearly the same question, and only one of them can be wrong about it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A Spec Kit repository gets a Spec Kit session (Priority: P1)

The author labels an issue in a repository that has Spec Kit installed. The daemon prepares the
worktree as it always does, notices from the worktree's own contents that this is a Spec Kit
project, and composes a prompt that says so: the repository works this way, here is the lifecycle,
the issue's text is the feature input, and here is when the lifecycle is worth using and when it is
not. The author did not edit anything in that repository to get this, and gets it in every Spec Kit
repository they own from the moment it is installed there.

**Why this priority**: It is the half of the issue that pays for itself immediately, across more
than half of the author's repositories. Shipped alone it is a complete improvement: sessions start
the way the author would have started them.

**Independent Test**: Dispatch one issue in a Spec Kit repository with no `.claude/robot-army.md`
present, and confirm the launched session's prompt names the lifecycle and the convention for using
it; dispatch one issue in a repository without Spec Kit and confirm the prompt is byte-identical to
what it is today.

**Acceptance Scenarios**:

1. **Given** a worktree containing Spec Kit scaffolding and the matching agent commands, **When** a
   session is dispatched, **Then** the prompt contains the Spec Kit guidance, the detection decision
   is in the audit log with the evidence that produced it, and the session starts normally.
2. **Given** a worktree with no Spec Kit scaffolding, **When** a session is dispatched, **Then** the
   prompt is exactly what it would have been before this milestone and one record states that Spec
   Kit was not detected.
3. **Given** a repository that has both Spec Kit scaffolding *and* its own
   `.claude/robot-army.md`, **When** a session is dispatched, **Then** both are present in the
   prompt and the repository's own instructions take precedence over the generic guidance.
4. **Given** the same issue dispatched twice into the same repository, **When** the prompts are
   compared, **Then** they are identical — the guidance is fixed text, even though what the session
   does with it is the session's judgement.
5. **Given** a repository where the author has turned this off, **When** a session is dispatched,
   **Then** no Spec Kit guidance appears and the suppression is recorded.

---

### User Story 2 - I can see which phase an active session reached (Priority: P2)

The author opens `/active` on their phone and sees, for each Spec Kit item, which stage it is in —
specifying, planning, breaking down tasks, or implementing — and when it got there. Nothing was
installed into the worktree to make this true, and it is equally true of a session that ignored
every instruction it was given, because it is read from the files on disk rather than reported by
anyone.

**Why this priority**: It is the "monitor" half of the issue, delivered by the mechanism that cannot
lie. It depends on Story 1 only for knowing which items are worth looking at, and could ship without
it.

**Independent Test**: Dispatch an item into a Spec Kit repository, let a session produce a spec and
then a plan, and confirm the item view moves from *specify* to *plan* within one reconciliation
interval without the session having reported anything.

**Acceptance Scenarios**:

1. **Given** an active item whose worktree has gained a feature directory containing `spec.md`,
   **When** the daemon next observes it, **Then** the item shows the specify phase and one record
   marks the transition.
2. **Given** an item already showing the plan phase, **When** subsequent observations find no
   change, **Then** no further records are written — one line per transition, not one per cycle.
3. **Given** a worktree that already contained a *previous* feature's completed `spec.md`,
   `plan.md` and `tasks.md` at the moment it was created, **When** the item is observed, **Then**
   the item shows no phase rather than showing the previous feature's implement phase.
4. **Given** the daemon is stopped and restarted mid-flow, **When** it reconciles, **Then** the
   phase it reports is the same one it reported before, derived again rather than remembered.
5. **Given** a Spec Kit item whose session chose *not* to use the lifecycle for a small change,
   **When** it is viewed, **Then** it shows no phase and this is not treated as an error, a stall,
   or an anomaly.
6. **Given** a non-Spec-Kit item, **When** it is viewed, **Then** it shows no phase at all rather
   than an empty or unknown one.

---

### User Story 3 - I know in advance which repositories this changes (Priority: P3)

Before a single issue is labelled, the author can ask which of their onboarded repositories this
milestone will behave differently in, and see the answer in the terminal alongside everything else
known about a repository.

**Why this priority**: It is one column on an existing listing. It exists because a behaviour that
switches on by itself, based on a directory the author did not think about, is exactly the kind of
change that is otherwise discovered from its effects — and this project's habit is to make the
derived thing inspectable before it is relied upon. It is the price of having chosen automatic
detection over per-repository opt-in.

**Independent Test**: Run the repositories listing against a mix of Spec Kit and non-Spec Kit clones
and confirm each is reported correctly, with no network requests made.

**Acceptance Scenarios**:

1. **Given** onboarded repositories, some with Spec Kit installed, **When** the listing is run,
   **Then** each row states whether Spec Kit was found in that clone.
2. **Given** a clone that is missing or moved, **When** the listing is run, **Then** the row says
   the clone could not be read rather than asserting Spec Kit is absent.
3. **Given** a repository the author has excluded, **When** the listing is run, **Then** the row
   says Spec Kit is present *and* that the behaviour is turned off for it.

---

### Edge Cases

- **Scaffolding without commands.** A repository has `.specify/` but no Spec Kit commands for the
  agent that will actually run — the flow the prompt names would not exist. Detection must require
  both halves, and must record which half was missing when it declines.
- **A stale feature pointer.** A fresh worktree carries whatever `.specify/feature.json` was
  committed, which points at the *previous* feature and its finished artifacts. Progress must be
  attributed to work done after the worktree was created, or the item reports "implement" the second
  it starts. (This repository is in exactly that state right now: its committed pointer named 006
  until this specification was written.)
- **An issue that is not a feature.** A one-line typo fix in a Spec Kit repository does not want a
  four-phase lifecycle. The prompt states the convention and the session judges; a session that
  judges "no" produces an item with no phase, which is a correct outcome and not a stall.
- **A layout robot-army does not recognise.** Spec Kit changes its own directory or manifest layout
  in a later version. The failure must be a detection miss — behave exactly as today — and never an
  exception during preparation.
- **Feature directories that are not sequential.** Spec Kit supports timestamped feature directory
  names as well as numbered ones; observation must not assume either.
- **Resume after interruption.** An item resumed hours later must pick up its phase where it was,
  because the phase is derived from files that are still there.
- **Two features in one worktree.** A long session that finishes one feature and starts another must
  not appear to move backwards without explanation.
- **A repository that adopts Spec Kit after onboarding.** Nothing may need re-onboarding for that to
  take effect.
- **A repository that tracks its own `.specify/extensions.yml`.** It is read by the session's own
  commands and is none of robot-army's business. Nothing here reads it, writes it, or reacts to it.

## Requirements *(mandatory)*

### Functional Requirements

**Detection**

- **FR-001**: The system MUST determine, for each dispatch, whether the prepared worktree is a Spec
  Kit project, using only local reads of that worktree.
- **FR-002**: Detection MUST require both the Spec Kit scaffolding and the lifecycle commands the
  session would actually invoke. A repository with one and not the other MUST NOT be treated as
  Spec Kit capable, and the record MUST name which half was missing.
- **FR-003**: Detection MUST NOT make network requests, MUST NOT execute anything from the
  repository, and MUST NOT write to the worktree.
- **FR-004**: Every detection decision MUST be recorded with the work item, the worktree, the
  outcome, and the evidence that produced it.
- **FR-005**: A worktree whose layout the system does not recognise MUST be treated as not detected.
  Detection MUST NOT fail a dispatch under any circumstances.
- **FR-006**: A repository that adopts Spec Kit after being onboarded MUST get this behaviour with
  no re-onboarding and no configuration change.

**Driving the flow**

- **FR-007**: When Spec Kit is detected and enabled, the composed prompt MUST tell the session that
  the repository follows the Spec Kit lifecycle, MUST name the lifecycle stages in order, and MUST
  state that the issue's text is the feature input.
- **FR-008**: The prompt MUST state the convention for *when* the lifecycle applies — the kind of
  change that warrants it and the kind that does not — and MUST leave that judgement to the session
  rather than encoding it as a rule the daemon evaluates. The daemon MUST NOT require, verify, or
  enforce that the lifecycle was followed.
- **FR-009**: The guidance MUST compose with a repository's own `.claude/robot-army.md` such that
  the repository's instructions take precedence, and the composed prompt MUST remain deterministic:
  the same issue and worktree MUST produce the same prompt text.
- **FR-010**: When Spec Kit is not detected, the prompt MUST be byte-identical to what the current
  system produces.
- **FR-011**: The behaviour MUST be automatic on detection. The author MUST be able to turn it off
  globally and for one repository, and a dispatch where it was suppressed MUST record that it was
  suppressed and by which setting.

**Observing the phase**

- **FR-012**: The system MUST derive the lifecycle phase of an active Spec Kit item from files in
  its worktree, without requiring any cooperation from the session.
- **FR-013**: Phase MUST be attributed to the work item's own progress. Artifacts present in the
  worktree at the moment it was created MUST NOT be reported as this item's phase.
- **FR-014**: A phase change MUST be recorded once, at the transition, and MUST NOT produce a record
  per observation cycle.
- **FR-015**: The current phase MUST be visible from the terminal and from the web item and active
  views. An item with no phase MUST show nothing rather than an unknown or empty phase.
- **FR-016**: Phase MUST be advisory. It MUST NOT gate dispatch, resume, cleanup, capacity, or any
  work item or session state transition, and the absence of a phase MUST NOT raise an anomaly.
- **FR-017**: Phase MUST survive a daemon restart by being derivable again from the worktree, and
  MUST NOT depend on the daemon having observed every intermediate step.

**Boundaries**

- **FR-018**: The system MUST NOT create, modify, or delete any file inside a worktree as part of
  this milestone — tracked or untracked. Detection and observation are reads.
- **FR-019**: The system MUST NOT install, initialise, upgrade, or repair Spec Kit in any
  repository.
- **FR-020**: The system MUST NOT read, write, or act on a repository's `.specify/extensions.yml`.

**Inspection**

- **FR-021**: The repositories listing MUST state, per onboarded repository, whether Spec Kit was
  found in its clone, and MUST distinguish "not found" from "clone unreadable".
- **FR-022**: The listing MUST show, for a repository where Spec Kit was found, whether the
  behaviour is enabled or suppressed for it.

### Key Entities

- **Spec Kit detection result**: the per-dispatch decision that a worktree is or is not a Spec Kit
  project, the evidence for it, and the time it was made.
- **Lifecycle phase**: which of the named stages a work item's Spec Kit run has reached, and when it
  reached it. Derived from the worktree, never reported by the session.
- **Feature directory**: the location within the worktree where a Spec Kit run's artifacts are
  written, as named by the project itself rather than guessed.

## Out of Scope

**Extension hooks are evaluated and not used.** This is the direct answer to issue #9, and it is
recorded here rather than left as an omission.

What they would add over reading the worktree is real but narrow: a hook fires when a phase
*finishes*, which no file appearing announces, and it fires at the moment it happens rather than at
the next observation. What they cost is the whole of the mechanism — writing a registration and a
command into every dispatched worktree, keeping those files out of the author's commits, respecting
the effect levels on those writes, standing down when a repository tracks its own registration, and
holding a second, weaker class of evidence in the record alongside the first. Principle I settles a
tie by moving parts, and this is not a tie: the files answer nearly the same question with none of
that.

The reliability argument is the stronger one. A hook is an instruction the agent chooses to follow,
so an absent report means either "not there yet" or "did not bother", and nothing distinguishes
them. A design whose failure mode is silence is the one this project has twice gone out of its way
to avoid.

**What would make this worth revisiting**, concretely — any one of these, observed rather than
anticipated:

- Phase-from-files proves too coarse in practice: the author repeatedly wants to know that implement
  *finished* rather than that it started, and the session's own exit does not answer it.
- Observation latency matters — a phase change that took hours is noticed a poll interval late and
  that lateness costs something real.
- Spec Kit gains a hook mechanism the daemon can observe directly, rather than one mediated by the
  agent's willingness to follow it.

Also out of scope, for the avoidance of doubt: installing Spec Kit into repositories that lack it,
running any Spec Kit command from the daemon, gating any state transition on lifecycle progress, and
reacting to review gates inside a running session.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a Spec Kit repository with no repository-specific instruction file, a dispatched
  session that receives a feature-shaped issue begins with the Spec Kit lifecycle rather than
  free-form editing, in at least 8 of 10 dispatches. This is a measurement, not a guarantee: FR-008
  leaves the judgement to the session, so the number is tracked over the live round rather than
  asserted by a test.
- **SC-002**: The prompt for a repository without Spec Kit is byte-identical to the pre-milestone
  prompt, in 100% of cases.
- **SC-003**: For an active Spec Kit item, the author can tell which lifecycle stage it is in from
  their phone within one observation interval of the stage changing, without opening a terminal.
- **SC-004**: The number of files robot-army creates, modifies, or deletes inside a worktree as part
  of this milestone is zero, verified by an unchanged worktree across a full dispatch.
- **SC-005**: The number of items reporting a phase that belongs to a previously completed feature
  is zero.
- **SC-006**: Enabling this milestone requires no edit to any of the author's repositories, and the
  number of `.claude/robot-army.md` files that exist solely to say "this repository uses Spec Kit"
  drops to zero.
- **SC-007**: A dispatch into a repository whose Spec Kit layout is unrecognised succeeds at the
  same rate as it does today — detection never converts a working dispatch into a failure.
- **SC-008**: Before labelling anything, the author can list which onboarded repositories this
  changes and get an answer for every one of them with no network access.

## Assumptions

- **The issue text is the feature description.** This is already how the author works: this very
  specification was produced from `issue #9 on this repo` and nothing else, so a prompt that hands
  the issue to the lifecycle as its input matches observed practice rather than a guess.
- **Only the agent the daemon actually launches matters.** Spec Kit supports several integrations;
  robot-army launches exactly one kind of session, so detection cares only about whether that
  agent's Spec Kit commands are present.
- **The session is trusted to judge scope, and the daemon is not.** FR-008 is a deliberate choice of
  a non-deterministic outcome over a new rule: a second label would put the decision at the moment
  the author is already labelling, and a size heuristic would be the daemon guessing at something it
  has strictly less information about than the session reading the issue. The cost is accepted and
  named in SC-001.
- **Automatic beats opt-in here, and Story 3 is the compensation.** Per-repository opt-in would
  reintroduce exactly the step milestone 005 spent a milestone removing. The risk of a behaviour
  switching on by itself is answered by making it listable before it fires and suppressible after.
- **Phase is derived, not stored as truth.** Storing an observation is a cache of something the
  worktree still says; the worktree is the source. This is what makes interruption tolerance cheap
  here — a restart re-reads rather than recovers.
- **Spec Kit's on-disk layout is a dependency and will move.** The directories and manifests this
  reads are Spec Kit's, not robot-army's, and a future version may rename them. That is accepted
  with the mitigation stated in FR-005: an unrecognised layout is a detection miss and behaves
  exactly as the system does today.
- **Nothing here changes the human gate.** Labelling an issue remains the only thing that starts
  work. This milestone changes what a session is told and what the author can see, not what causes a
  session to exist.
