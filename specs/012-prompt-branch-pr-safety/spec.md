# Feature Specification: Every Session Is Told How Work Is Delivered — A Branch, A Push, A Pull Request, And Nothing Else Touched

**Feature Branch**: `robot-army/issue-29-ensure-that-prompts-include-pr-creation`

**Created**: 2026-08-30

**Status**: Draft

**Input**: User description: "issue #29 on this repo" — the prompts robot-army dispatches with
must say that work happens on a non-default branch which ends pushed with a pull request open,
and that the work product is commits and pull requests rather than changes made directly to
this or any other running system, unless the issue body explicitly says otherwise

## User Scenarios & Testing *(mandatory)*

<!--
  One omission with two halves. The daemon puts a session on a feature branch in an isolated
  worktree and then says nothing about what to do when the work is finished, or about what
  kinds of action are in bounds while it is being done. Both halves are standing instructions
  the maintainer would otherwise repeat in every issue they write.

  Story 1 is the delivery half, Story 2 is the containment half, Story 3 is the escape hatch
  that keeps both from being wrong when an issue genuinely needs something else. Each is
  independently testable, and each is worth shipping on its own.
-->

### User Story 1 - Work arrives as a pushed branch with a pull request (Priority: P1)

The maintainer labels an issue. The daemon prepares a worktree on `robot-army/issue-<n>-<slug>`
and dispatches a session whose prompt already says which branch it is on. Then the prompt stops
talking about git.

So the session is told where its work starts and nothing about where its work goes. What
happens at the end is left to whatever the session infers: commits on a local branch nobody
fetched, a branch pushed with no pull request opened, or — worst and entirely possible — work
committed straight onto the checked-out default branch of a repository where the worktree was
not what the session decided to edit.

Every one of those outcomes is invisible until the maintainer goes looking. `robot-army show
<id>` answers "uncommitted changes? commits on the branch? PR open?" precisely because the
answers are not currently guaranteed, and the cleanup guard exists because an unpushed branch
may hold the only copy of something.

After this change the prompt says it: stay on the non-default branch you were given, and when
the work is done, push it to `origin` and open a pull request. Written once, in the daemon,
applying to every repository it dispatches into.

**Why this priority**: This is the first half of the issue and the half with a durable-loss
failure mode. Work that is committed but never pushed is one `git worktree remove` away from
gone, and the cleanup guards exist to catch exactly that. Delivered alone, dispatched work has
a defined destination for the first time.

**Independent Test**: Compose a dispatch prompt for any issue in any onboarded repository and
read it. Confirm it states that work stays on the non-default branch and ends pushed to origin
with a pull request opened, without any file having been added to that repository.

**Acceptance Scenarios**:

1. **Given** any onboarded repository and any labelled issue, **When** the prompt for that
   dispatch is composed, **Then** it instructs the session to do its work on the non-default
   branch it was placed on rather than on the repository's default branch.
2. **Given** the same prompt, **When** it is read to the end, **Then** it instructs the session
   to push that branch to `origin` and open a pull request when the work concludes.
3. **Given** a repository that does not use Spec Kit, **When** a session is dispatched into it,
   **Then** the branch-and-pull-request instruction is present, because it does not depend on
   Spec Kit detection.
4. **Given** a repository with no `.claude/robot-army.md`, **When** a session is dispatched into
   it, **Then** the instruction is present without any repository file being added or edited.

---

### User Story 2 - The work product is a diff, not a changed system (Priority: P1)

The maintainer's issues describe changes to software. A session reading "the health timer fires
too often" can satisfy that sentence two ways: edit the unit file in the repository and open a
pull request, or run `systemctl --user edit` and be finished in ten seconds. The second is
faster, is not what was wanted, leaves no reviewable record, and changes the maintainer's
machine in a way no pull request describes and no `git revert` undoes.

The same shape scales badly outwards. An issue about a deployment could be satisfied by
deploying. An issue about a remote configuration could be satisfied by changing it. Sessions
run with a permission mode that does not stop them, in a worktree that has the maintainer's
credentials in ambient reach, and the daemon currently offers no standing position on any of
it.

After this change the prompt takes one: the output of this work is code and file changes inside
this git repository, becoming commits and pull requests. Do not change the state of this machine
or any other system as a way of satisfying the issue.

The instruction has to be drawn precisely enough to still permit the work. Running the test
suite, building, installing dependencies into the worktree, and reading whatever is readable are
ordinary parts of writing the change, all of them scoped to the worktree and all of them
reversible by deleting it. Pushing the branch and opening the pull request are outward actions
by definition — Story 1 requires them — so the instruction must name them as the exception
rather than contradict itself.

**Why this priority**: This is the second half of the issue and the half whose failure mode is
irreversible and off-repository. It also stands alone: a session that changes nothing outside
its worktree but forgets to open a pull request has produced recoverable work, while one that
reconfigured the maintainer's machine has not.

**Independent Test**: Compose a dispatch prompt and read it. Confirm it states that the work
product is repository changes reaching the maintainer as commits and a pull request, that
directly mutating this or any other system is not how the issue is to be satisfied, and that
running tests and builds inside the worktree together with the push and the pull request are
not what that prohibits.

**Acceptance Scenarios**:

1. **Given** any dispatch prompt, **When** it is read, **Then** it states that the work should
   be code and file changes in the git repository, delivered as commits and pull requests.
2. **Given** the same prompt, **When** it is read, **Then** it states that the session should
   not directly change the state of the local machine or any other system in order to satisfy
   the issue.
3. **Given** the same prompt, **When** the two instructions are read together, **Then** pushing
   the branch and opening the pull request are identified as permitted outward actions, so the
   containment instruction cannot be read as forbidding the delivery instruction.
4. **Given** the same prompt, **When** it is read, **Then** it does not forbid the ordinary
   local work of producing the change — running tests, builds, and dependency installation
   within the worktree.

---

### User Story 3 - An issue that needs something else can say so (Priority: P2)

Not every issue wants a pull request. "Investigate why the poller stalls and report back" wants
an answer, not a branch. "Delete the stale worktrees under `~/worktrees`" is deliberately an
action on the machine. "Commit this directly to main, it is a typo in the README" is a decision
the maintainer is entitled to make in the issue they wrote.

Both standing instructions are therefore defaults, not laws, and the prompt has to say which
text wins when they disagree with the issue. That is not a detail: the daemon's prompt is
assembled from sections whose order encodes their precedence, the repository's own
`.claude/robot-army.md` sits above everything, and the issue body sits at the bottom — so a
default that is overridable by the issue body is overridable by text that appears *after* it.
Nothing about position communicates that, which means the text has to.

After this change the standing instructions say plainly that an explicit instruction in the
issue body overrides them, and the repository's own instructions continue to outrank them by
position as they already do for every other block.

**Why this priority**: Without it the two P1 instructions are wrong for a real and recurring
class of issue, and the maintainer's only recourse is to argue with a prompt they cannot see.
It is P2 rather than P1 because the failure it prevents is friction rather than loss.

**Independent Test**: Read the standing instructions and confirm they state their own override
condition. Compose a prompt for an issue whose body contradicts them and confirm the override
sentence is present and refers to the issue body specifically.

**Acceptance Scenarios**:

1. **Given** any dispatch prompt, **When** the standing instructions are read, **Then** they
   state that an explicit instruction in the issue body overrides them.
2. **Given** an issue whose body asks for something other than a pull request, **When** the
   prompt is composed, **Then** the standing instructions and the issue body are both present
   and the precedence between them is stated rather than implied by ordering.
3. **Given** a repository whose `.claude/robot-army.md` states a conflicting delivery
   convention, **When** the prompt is composed, **Then** the repository's own instructions
   appear ahead of the standing instructions and continue to take precedence, unchanged from
   today's behaviour.

---

### Edge Cases

- **An issue body that says nothing about delivery.** The overwhelmingly common case. Both
  standing instructions apply as written; no interpretation is required of the session.
- **A session that ignores the instructions.** Nothing enforces them and nothing is recorded as
  failed. This matches the existing stance on the Spec Kit paragraph: the prompt states a
  default, the judgement stays the session's, and the observable outcome is whatever the
  session actually did. `robot-army show` already reports whether commits, a push, and a pull
  request exist, and the cleanup guards already refuse to delete unproven work.
- **A repository whose `origin` is unreachable, unwritable, or absent.** The instruction is
  prose the session acts on, not an operation the daemon performs, so an unpushable branch
  produces a session that says so — exactly as it would today. The daemon's behaviour is
  unchanged and no new failure path is introduced.
- **An issue dispatched into a repository with no pull-request mechanism.** Every repository the
  daemon dispatches into is a GitHub repository, since a GitHub issue is what starts an item.
  No special case is warranted.
- **A dry-run or simulated dispatch.** The prompt is composed identically. The standing
  instructions are fixed text and do not vary with dispatch mode.
- **An issue body long enough to be truncated.** The standing instructions precede the body and
  are unaffected by truncation of it; nothing that must be read can be cut.
- **A repository whose own `.claude/robot-army.md` already says this.** The result is a prompt
  that says it twice, the repository's version first and outranking. Duplication is acceptable
  and the maintainer may delete the now-redundant file content at leisure; nothing detects or
  deduplicates it.
- **A Spec Kit repository.** Receives the Spec Kit paragraph and these standing instructions
  both. The two do not conflict: one is about which process to follow, the other about where the
  output goes.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every prompt the daemon composes for a dispatched session MUST include standing
  delivery instructions, for every onboarded repository, without any file being added to or
  edited in that repository.
- **FR-002**: The standing instructions MUST state that work is to be done on the non-default
  branch the session was placed on, not on the repository's default branch.
- **FR-003**: The standing instructions MUST state that at the conclusion of the work the branch
  is to be pushed to `origin` and a pull request opened.
- **FR-004**: The standing instructions MUST state that the work should take the form of code and
  file changes within the git repository, delivered as commits and pull requests.
- **FR-005**: The standing instructions MUST state that the session should not directly change
  the state of the local machine or of any other system as a means of satisfying the issue.
- **FR-006**: The standing instructions MUST identify the branch push and the opening of the pull
  request as permitted outward actions, so that FR-005 cannot be read as prohibiting FR-003.
- **FR-007**: The standing instructions MUST NOT prohibit local, worktree-scoped work needed to
  produce the change — running tests, running builds, and installing dependencies.
- **FR-008**: The standing instructions MUST state that an explicit instruction in the issue body
  overrides them, because the issue body appears after them in the prompt and position alone
  would imply the opposite.
- **FR-009**: The standing instructions MUST remain subordinate to a repository's own
  `.claude/robot-army.md`, which continues to appear ahead of them, preserving today's
  precedence rule that earlier sections outrank later ones.
- **FR-010**: The standing instructions MUST be fixed text: identical for every repository, every
  issue, and every dispatch mode, so that the same issue composed twice yields byte-identical
  prompts.
- **FR-011**: Inclusion MUST NOT depend on Spec Kit detection, on the repository's configuration
  section, or on any per-repository state.
- **FR-012**: The prompt MUST remain a single argument of a size that dispatch already supports;
  the standing instructions MUST NOT displace or truncate the issue body, the repository's own
  instructions, or the Spec Kit paragraph.
- **FR-013**: The daemon MUST NOT gain any new capability to push branches, open pull requests,
  or otherwise act on a repository's remote. This feature changes what a session is told; it
  changes nothing about what the daemon does.
- **FR-014**: The composed prompt MUST continue to be recorded in the action log exactly as it is
  today, so that what a session was told remains reconstructable from the log alone.

### Key Entities

- **Standing delivery instructions**: A fixed block of prose, authored once in the daemon,
  carrying the branch-and-pull-request default, the repository-changes-only default, the
  permitted-outward-actions carve-out, and its own override rule.
- **The composed prompt**: The existing ordered assembly of a repository's own instructions, any
  Spec Kit paragraph, and the issue itself, in which order encodes precedence. This feature adds
  one section to that assembly.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of dispatches, across every onboarded repository, carry both standing
  instructions — with no repository requiring a `.claude/robot-army.md` to obtain them.
- **SC-002**: The same issue composed twice produces byte-identical prompt text, so what a
  session was told is fully determined by the issue, the repository's own instructions, and the
  Spec Kit detection result.
- **SC-003**: A maintainer reading a composed prompt can determine, in a single read and without
  consulting source code, which text wins when the issue body and the standing instructions
  disagree.
- **SC-004**: The standing instructions add no more than 1,500 characters to the prompt, keeping
  the issue itself the substantial majority of what the session reads.
- **SC-005**: Zero repositories need to be re-onboarded, reconfigured, or otherwise touched for
  the behaviour to take effect; the next dispatch after the change carries it.
- **SC-006**: No new outward-facing action becomes reachable from the daemon as a result of this
  feature — the count of remote-mutating operations the daemon can perform is unchanged.

## Assumptions

- **No configuration switch is added.** The Spec Kit paragraph has one because it is wrong for
  repositories that do not use Spec Kit; these instructions are right for every repository the
  daemon dispatches into. Two override paths already exist and are sufficient: a repository's
  `.claude/robot-army.md`, which outranks by position, and an explicit instruction in the issue
  body, which FR-008 makes explicit. A knob with no second use would be complexity with no
  beneficiary.
- **"Non-default branch" means the branch the daemon already created.** The daemon places every
  session on `robot-army/issue-<n>-<slug>` in its own worktree, and the existing prompt already
  names it. The new instruction reinforces staying there and defines what happens at the end; it
  does not ask the session to create a branch it already has.
- **`origin` is the push target.** Onboarding verifies a clone by reading its `origin` remote, so
  every dispatchable repository has one and it is the remote the maintainer means.
- **Enforcement is out of scope, deliberately.** Nothing in this feature checks that a branch was
  pushed, that a pull request was opened, or that no system was touched. That stance matches the
  Spec Kit paragraph exactly: the prompt states a default, and the session's judgement is the
  session's. Existing surfaces — `robot-army show`, the web item page, and the cleanup guards —
  already report the observable facts.
- **The instructions apply to all sources.** Items originating from a Trello card become GitHub
  issues before they are ever dispatched, so no separate path needs its own treatment.

## Out of Scope

- Verifying, after a session ends, that a branch was pushed or a pull request opened. That is a
  reconciliation feature, not a prompt feature, and the existing `show` output already answers
  the question when it is asked.
- Any daemon-side pushing, pull-request creation, or other remote mutation (FR-013).
- Sandboxing, permission narrowing, or any technical prevention of a session acting outside its
  worktree. This feature adds an instruction, not a boundary; a claim to be a boundary would be
  false.
- Changing the existing precedence model of the composed prompt, or how `.claude/robot-army.md`
  and the Spec Kit paragraph are read and positioned.
- Deduplicating a repository's `.claude/robot-army.md` against the new standing instructions.
