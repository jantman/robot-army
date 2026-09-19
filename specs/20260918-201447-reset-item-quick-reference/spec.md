# Feature Specification: A first-class "start over", and an operating page you can scan

**Feature Branch**: `robot-army/issue-179-a-first-class-start-over-for-an-item`

**Created**: 2026-09-18

**Status**: Draft

**Input**: GitHub issue [jantman/robot-army#179](https://github.com/jantman/robot-army/issues/179) —
"A first-class 'start over' for an item, and an operating page you can scan"
(labels: documentation, enhancement, robot-army).

## Context

robot-army picked up an issue as item 109 while the maintainer was still writing that issue.
The session was cancelled and dispatch paused, and the wanted outcome was the obvious one:
throw the work away and start again from what the issue says *now*. No command does that.
Getting there took a worktree removal, a hand-edit of the GitHub issue, and a hand-written
`UPDATE` against `state.db` — the last of which is a state change with no record, which the
constitution's Principle III exists to forbid.

Every piece of the operation already exists and is already correct; none of them is reachable
from the state an item is actually in when this is wanted:

- **Re-reading the issue** (refresh `title`, `body`, `labels`, `author` from a live read, then
  re-run the poller's eligibility verdict including the author check) is what `retry` does, and
  it is the only code that does it. It is gated on `failed`.
- **Discarding the checkout** (remove worktree and branch, forget the path so a fresh one can be
  built) is what `worktree remove` does for an unfinished item, under guards that already refuse
  a live session and already respect git's refusal over uncommitted work.
- **Dispatching afterwards** needs nothing new: dispatch rebuilds a missing worktree and composes
  the prompt from the stored issue content.

The gap is a route between them. From `interrupted`, `restart` and `resume` redispatch with the
**stored** body — the stale copy is exactly what is being discarded — and `abandon` is terminal.

Separately, `docs/guide/operating.md` is the page reached for when something is wrong, and it is
593 lines of continuous prose about *why*. It cannot answer "what can I do from `interrupted`?",
"what are the subcommands?", or "what do I type to start over?".

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start an item over from what the issue says now (Priority: P1)

The maintainer realises an item was picked up too early, or went down the wrong road entirely.
They stop the session, then ask robot-army to throw away everything it did and begin again from
the current text of the issue. One command discards the checkout and the branch, re-reads the
issue from GitHub, re-checks that it is still eligible — author included — and puts the item back
in the queue as though it had just been discovered. The next dispatch builds a fresh checkout and
composes the prompt from the freshly read issue.

**Why this priority**: It is the thing the issue was filed about, and the only part whose absence
forces an unrecorded hand-edit of the database. Nothing else in this feature is worth anything if
this is missing.

**Independent Test**: Take an `interrupted` item with a worktree on disk and a branch, edit the
issue's body on GitHub, run the new command, and confirm the worktree and branch are gone, the
stored body matches the edited issue, the item is `ready`, and every step left an audit record.

**Acceptance Scenarios**:

1. **Given** an `interrupted` item with a worktree and branch and no live session, **When** the
   maintainer resets it, **Then** the worktree and branch are removed, the stored path is
   forgotten, the issue content is re-read from GitHub, the item is `ready`, and the previous
   failure and blocked reasons are cleared.
2. **Given** an item reset in scenario 1, **When** dispatch next selects it, **Then** a fresh
   checkout is built and the prompt is composed from the re-read issue content, not from what was
   stored before the reset.
3. **Given** an `awaiting_review` item, **When** the maintainer resets it, **Then** the same
   thing happens.
4. **Given** a `failed` item with a worktree, **When** the maintainer resets it, **Then** the same
   thing happens — reset is `retry` with the discard in front, and it is available wherever
   `retry` is.
5. **Given** an item whose issue has since been closed, unlabelled, or edited to be written by
   somebody other than the configured author, **When** the maintainer resets it, **Then** the
   reset is refused with the reason from the live evaluation, and the item does not become
   `ready`.
6. **Given** an item with a session still running, **When** the maintainer resets it, **Then** the
   reset is refused and says how to stop the session first; nothing on disk is touched.
7. **Given** an item whose checkout holds uncommitted or untracked work, **When** the maintainer
   resets it without overriding, **Then** the reset is refused with git's reason and the item does
   not change state.
8. **Given** an `active` item, or a `done` or `abandoned` one, **When** the maintainer resets it,
   **Then** the reset is refused, naming the state and what to do instead.

---

### User Story 2 - Start an item over from the phone (Priority: P2)

The same operation from the web interface, on the item's own page, behind a confirmation screen
that says in plain words what is about to be destroyed — this checkout, this branch, this much
uncommitted work — and offers a way back.

**Why this priority**: The web interface exists so an interrupted item can be decided without
opening a terminal, and "this one is going nowhere, start it again" is exactly that kind of
decision. It is second only because the command is what makes the operation exist at all.

**Independent Test**: Open `/item/<id>` for an `interrupted` item, confirm the reset control is
offered, follow it to the confirmation page, submit, and confirm the item is `ready` with the
issue re-read; then open `/item/<id>` for an `active` item and confirm the control is absent and
the route refuses if posted to directly.

**Acceptance Scenarios**:

1. **Given** an item in a state reset accepts, **When** its page is viewed, **Then** a reset
   control is offered alongside the existing ones.
2. **Given** the reset control is followed, **When** the confirmation page is shown, **Then** it
   states that the checkout and branch will be deleted, that uncommitted work in them is lost,
   that the issue will be re-read and re-checked, and that the item returns to the queue — and it
   offers a way back that changes nothing.
3. **Given** the confirmation is submitted, **When** the reset succeeds, **Then** the page reports
   it and the item's page shows the new state.
4. **Given** an item in a state reset refuses, **When** its page is viewed, **Then** no reset
   control is offered, and a direct post to the route is refused with the same reason the command
   would give.
5. **Given** a reset refused by a live session or by uncommitted work, **When** the confirmation is
   submitted, **Then** the refusal and its reason are reported on the page and nothing is
   destroyed.

---

### User Story 3 - Answer "what do I type?" from one page at 2am (Priority: P2)

Something is wrong. The maintainer opens `docs/guide/operating.md` and finds, without reading
prose: what state the item is in and what that means, which commands that state permits, what
each command does and refuses, whether it needs a terminal or works from the phone, and a short
recipe for the situation they are actually in.

**Why this priority**: It is half the filed issue, it is independently deliverable, and the new
command is undiscoverable without it. It ranks with the web control rather than above it because
the command has to exist before the page can describe it.

**Independent Test**: Read the rewritten page cold and answer, from it alone: what can I do from
`interrupted`; what does `abandon` refuse; can I start an item over from my phone; what do I type
when the disk is full. Then confirm every command it names exists and every state it names is a
real state.

**Acceptance Scenarios**:

1. **Given** the rewritten page, **When** the maintainer looks up a state, **Then** a table gives
   that state's meaning, the commands legal from it, and what it becomes next.
2. **Given** the rewritten page, **When** the maintainer looks up a command, **Then** a table
   gives what it does, when to reach for it, what it refuses, and whether it is reachable from the
   web interface or needs a local terminal.
3. **Given** the rewritten page, **When** the maintainer is in one of the named situations — an
   item is stuck; I want to start over; something is running that should not be; the daemon looks
   dead; the disk is full — **Then** a recipe of a few lines says what to type, in order.
4. **Given** reasoning removed from the page, **When** the maintainer wants the *why*, **Then** it
   is on the narrative page that already owns that stage, and the quick reference links to it.
5. **Given** the rewritten page, **When** every command and state it names is checked against the
   program, **Then** all of them exist and none is missing.

---

### Edge Cases

- **An item with no worktree on record** (never dispatched, or already removed): the reset does not
  fail for want of something to delete. It re-reads and re-queues.
- **A worktree directory deleted by hand**, with the path still on record: treated the way the
  removal path already treats it — already gone is not a refusal.
- **The issue cannot be read** — network failure, or the issue no longer exists or is invisible to
  the token: the reset stops, says which of the two happened, and never falls back to the stored
  copy. Whether the checkout is discarded before or after the read is decided in the plan; whichever
  order is chosen, the item must not be left `ready` carrying content nobody re-read.
- **Interrupted mid-reset** (process killed between the discard and the re-queue): the item is left
  in a state a second reset completes, with no silent half-done result.
- **The repository no longer resolves to a clone**, or its trust fingerprint has changed: refused
  with the existing reason and remedy, before any network read is spent.
- **Dispatch is paused**: the reset still works. Pausing stops dispatch, not the maintainer, and an
  item reset while paused sits in the queue until dispatch resumes.
- **A simulated (dry-run) item**: the reset behaves as every other destructive verb does below the
  live effect level — it says what it *would* do and touches nothing, naming the level that would
  make it real.
- **A branch git will not delete** because it is unmerged: reported, as the removal path already
  reports it, without claiming the branch is gone.
- **Reset run twice**: the second is not destructive to anything the first did; an already-`ready`
  item is either refused with a clear reason or re-read idempotently, decided in the plan.

## Requirements *(mandatory)*

### Functional Requirements

**The reset operation**

- **FR-001**: The system MUST provide a single named operation that discards a work item's
  checkout and branch, re-reads its issue from the source of record, re-evaluates its
  eligibility, and returns the item to the queue.
- **FR-002**: The operation MUST refresh the stored issue title, body, labels, and author from the
  live read, and MUST NOT fall back to the stored copy when the read fails.
- **FR-003**: The operation MUST put the re-read issue through the same eligibility evaluation the
  poller uses, including the check that the issue's author is the configured author, and MUST
  refuse — leaving the item out of the queue — when that evaluation says the issue is not
  eligible. This check MUST be the poller's own code, not a second implementation of it.
- **FR-004**: The operation MUST be accepted from `interrupted`, `awaiting_review`, and `failed`,
  and MUST be refused from every other state with a message naming the state and what to do
  instead.
- **FR-005**: The state machine MUST permit the transitions this requires, and those transitions
  MUST be declared in the single table that governs every state change rather than worked around.
- **FR-006**: The operation MUST refuse while any session row for the item is open, and MUST say
  how to stop the session first. It MUST NOT decide this on whether a process can be seen.
- **FR-007**: The operation MUST respect the version-control refusal over uncommitted or untracked
  work by default, and MUST offer the same deliberate override the existing removal path offers,
  with at least as much friction.
- **FR-008**: The operation MUST clear the stored checkout path when the checkout is discarded, so
  a fresh one is built on the next dispatch, and MUST clear the stored failure and blocked reasons
  when the item returns to the queue.
- **FR-009**: The operation MUST NOT delete or rewrite the historical record of the item's previous
  sessions; "discard the session history" means the next dispatch carries no prior session's
  context, not that records are destroyed.
- **FR-010**: After a reset, the next dispatch of the item MUST compose its prompt from the re-read
  issue content and MUST NOT restore any previous session's context.
- **FR-011**: Every step that changes state outside the process — the checkout removal, the branch
  deletion, the content refresh, the evaluation, and the state change — MUST be recorded, and the
  destructive steps MUST be recorded before they happen. A refusal MUST be recorded too, and MUST
  distinguish "we never asked GitHub" from "we asked".
- **FR-012**: The operation MUST be reachable from the command line and MUST exit non-zero when it
  refuses.
- **FR-013**: Where the effect level means the operation is rehearsed rather than performed, it
  MUST report what it would have done rather than claiming it was done, and MUST name the level at
  which it would become real.

**The web control**

- **FR-014**: The item view MUST offer the reset control exactly when the item's state permits it,
  from the same single source of truth the other controls use.
- **FR-015**: The control MUST route through a confirmation step before anything is destroyed, and
  that step MUST say what will be deleted, that uncommitted work in it is lost, that the issue will
  be re-read and re-checked, and that the item returns to the queue.
- **FR-016**: The confirmation step MUST offer a way back that changes nothing.
- **FR-017**: The route MUST re-check legality when the confirmation is submitted, and MUST refuse
  with the same reason the command gives if the state changed in between.
- **FR-018**: A refusal — live session, uncommitted work, ineligible issue, unreachable issue —
  MUST be reported on the page, with its reason, rather than silently redirecting as though it
  succeeded.
- **FR-019**: The web control MUST NOT offer an override that the terminal makes deliberate. Where
  the terminal demands a typed confirmation to destroy uncommitted work, the web control MUST
  refuse and say which command to run instead.

**The quick reference**

- **FR-020**: `docs/guide/operating.md` MUST carry a state table giving, for every work item state:
  what it means, which commands are legal from it, and what it becomes next.
- **FR-021**: The page MUST carry a command table giving, for every subcommand: what it does, when
  to reach for it, what it refuses, and whether it is reachable from the web interface or requires
  a local terminal.
- **FR-022**: The page MUST carry short task recipes, of a few lines each, for at least: an item is
  stuck; I want to start over; something is running that should not be; the daemon looks dead; the
  disk is full.
- **FR-023**: Reasoning that explains *why* the system behaves as it does MUST be moved to the
  narrative guide page that owns that stage rather than deleted outright where it is still true,
  and the quick reference MUST link to it.
- **FR-024**: Every command and every state the page names MUST exist in the program, and no state
  may be omitted from the state table. This MUST be checked by a test rather than by reading.
- **FR-025**: The guide pages affected by the new operation — the state page for the new
  transitions and the operating page for the new command and web control — MUST be updated in the
  same change.

### Key Entities

- **Work item**: one GitHub issue robot-army is working. Carries the state, the stored issue
  content (title, body, labels, author), the checkout path, the branch, and the reasons it last
  failed or was blocked. Reset is a transition of this entity that also rewrites the stored issue
  content and clears the checkout path.
- **Checkout**: the isolated working directory and its branch. Reset's destructive half. Owned by
  the item; rebuildable from the repository and the issue number.
- **Session**: one run of an agent against an item. A record, not a resource; reset refuses while
  one is open and preserves all of them.
- **Eligibility verdict**: the poller's answer to "should robot-army work this issue?", including
  the author check. Reset is gated on a fresh one.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Starting an item over is one command, or one confirmed click, where it previously
  took three manual steps including a hand-written database update.
- **SC-002**: No hand-written database edit is needed to start any item over from any state the
  operation accepts.
- **SC-003**: An item reset after its issue was edited is next worked from the edited text, in
  100% of cases — the stored copy is never the source after a reset.
- **SC-004**: An issue that would no longer pass the poller — closed, unlabelled, or written by
  somebody else — cannot be returned to the queue by a reset.
- **SC-005**: Every reset, and every refusal of one, can be reconstructed from the audit log alone:
  what was removed, what was read, what the verdict was, and what the item became.
- **SC-006**: A reader who has never seen the operating page can answer "what can I do from
  `interrupted`?", "what does `abandon` refuse?", "can I do this from my phone?" and "what do I
  type when the disk is full?" from that page alone, without following a link.
- **SC-007**: The operating page is materially shorter than the 593 lines it replaces and is
  majority table and recipe rather than prose.
- **SC-008**: No command or state named on the operating page is absent from the program, and no
  state is missing from its table — enforced by a test that fails when they drift apart.

## Assumptions

- **The verb is `reset`**, a command of its own rather than a flag on `restart`. `restart` is
  non-destructive and reuses the checkout; hiding an irreversible deletion behind a flag on it
  makes the dangerous thing look like a variation of the safe one. The issue offered both shapes
  and left the choice open.
- **Reset is accepted from `failed` as well**, although `retry` already serves that state.
  `retry` re-reads without discarding, which is right when the failure was environmental; reset
  discards as well, which is right when the work itself was wrong. Both remain available and
  `retry`'s behaviour is unchanged.
- **Terminal stays terminal.** Reset is refused from `done` and `abandoned`. The issue observes
  that abandoning an item means its issue can never be worked again, but it raises that as the
  reason `abandon` is not the answer here, not as a request to change it. See Out of Scope.
- **Reset is refused from `active`.** Stopping a running session is `cancel`'s job and stays a
  separate, deliberate act; reset says so rather than doing it.
- **The existing removal guards are reused rather than restated** — live session, git's refusal
  over uncommitted work, unresolved repository, already-removed record — so that reset cannot
  drift away from the command that has been getting those refusals right.
- **The existing confirmation machinery is reused** for the web control, so its confirmation page,
  its legality re-check at submission, and its cross-origin protections are the same ones every
  other mutating route already has.
- **The reasoning removed from the operating page is still wanted**, and moves to `3-selection.md`,
  `5-outcome.md` and `state.md` rather than being dropped, except where it merely restates what a
  table now says.
- **The state table's canonical home stays `state.md`.** The operating page's table is the
  operator's view — meaning, legal commands, next state — and links to the full machine rather
  than duplicating its narrative.

## Out of Scope

- **Reading issue comments into the prompt.** The issue's sub-question — that a comment written by
  the configured author is the same trust class as the issue body, and that there is therefore no
  supported way to add context to an issue after filing it — is real and was the thing that
  actually bit the maintainer. The issue itself says it is "worth deciding on its own merits", and
  it is a change to what an agent is told rather than to how an item is managed: a different blast
  radius, a different set of failure modes, and its own security argument to make. It belongs in
  its own issue, and this specification does not decide it.
- **Rescuing an `abandoned` or `done` item.** Making a terminal state non-terminal is a change to
  the shape of the state machine rather than a new route through it, and nothing in the reported
  incident needed it.
- **Changing what `retry`, `restart`, `resume`, `abandon` or `worktree remove` do.** Reset composes
  them; it does not alter them.
- **Any change to how the daemon selects, orders, or caps work.** A reset item re-enters the queue
  and is selected by the existing rules.
