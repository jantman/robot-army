# Feature Specification: Read Before You Approve — The Onboarding Screen Reaches the Terminal First

**Feature Branch**: `011-onboard-review-before-prompt`

**Created**: 2026-08-29

**Status**: Draft

**Input**: User description: "issue #17 on this repo" — `robot-army onboard` asks for confirmation
before showing the clone path, verification, and base ref it is asking about

## User Scenarios & Testing *(mandatory)*

<!--
  One defect, with a tail. The defect is that the approval screen is written after the question
  it exists to answer. The tail is that fixing the ordering means the operator will now actually
  use the decline path — including by interrupting — so the exits from that screen have to be
  as sound as the screen itself.

  Story 1 is the issue. Stories 2 and 3 keep the fix from costing something elsewhere; each is
  independently testable and each is shippable on its own.
-->

### User Story 1 - Seeing what is about to be trusted, before answering for it (Priority: P1)

The maintainer runs `robot-army onboard jantman/some-repo`. The command resolves the clone,
reads its origin remote, works out the base ref, checks whether the worker's trust dialog was
accepted, and reads the committed tool-permission settings at the base branch tip — settings
that a dispatched session will honour without asking. It composes all of that into an approval
screen. Then it asks:

```
Approve jantman/some-repo for dispatch, recording this fingerprint? [y/N]
```

That is the only thing on screen. Everything the question is about — which directory was
chosen, whether the path came from `repo_root` or from an explicit section, which remote was
consulted and what identity it resolved to, which base ref will be read, whether trust is
accepted, and the full text of the committed settings — arrives *after* the answer is typed.

So the maintainer is asked to approve a repository slug, which is the one thing they already
knew because they typed it. The screen designed to inform the decision is printed as a receipt
for a decision already made.

The practical cost is the case the issue names: the clone is not where `repo_root` implies, or
the base ref is not the branch this repository actually uses. Both are visible on that screen
and both are correctable by declining and editing configuration. Today the only way to see
them is to answer the question first — either by approving something unexamined, or by
declining, reading the screen, and running the command again.

After this change the screen arrives first. The question is asked last, and it is asked about
something the maintainer has read.

**Why this priority**: This is the issue. Delivered alone, the approval step becomes an
approval step rather than a formality, which is the entire reason a deliberate per-repository
trust command exists.

**Independent Test**: Run onboarding interactively against a resolvable repository with an
input source that records what was already on screen at the moment it was asked for a line.
Confirm the repository identity, clone path with its source, verified origin, base ref, trust
status and committed settings are all present in that recording, before the prompt.

**Acceptance Scenarios**:

1. **Given** a repository that resolves and verifies cleanly, **When** the maintainer runs
   onboarding interactively, **Then** the repository identity, the clone path and whether it
   was derived or configured, the verified origin and the remote it came from, the base ref,
   and the trust verdict are all readable on the terminal before the prompt blocks for input.
2. **Given** the same run, **When** the clone has committed tool-permission settings at the
   base ref, **Then** their full text is readable before the prompt blocks — the prompt is not
   asked about a file the maintainer has not been shown.
3. **Given** the clone has no committed settings at the base ref, **When** the maintainer runs
   onboarding, **Then** the line saying so is readable before the prompt blocks.
4. **Given** `--reapprove` on a repository already onboarded, **When** the maintainer runs it,
   **Then** the previously recorded path, any change marker on it, and the fingerprint diff
   against the approved version are all readable before the prompt blocks.
5. **Given** the maintainer reads the screen and sees a clone path they did not intend,
   **When** they decline at the prompt, **Then** nothing is recorded, the command reports the
   decline, and no second run is needed to have learned the path.
6. **Given** standard output is redirected to a file or a pipe rather than a terminal,
   **When** the run reaches the prompt, **Then** the screen has already been written out — it
   is not still sitting in a buffer waiting for the process to end.

---

### User Story 2 - One screen, printed once, on every way out (Priority: P2)

The approval screen is now emitted before the prompt. Every path out of that prompt previously
carried the same screen with it — the decline path prints it followed by `aborted`, the
refusal of a non-interactive approval prints it followed by the refusal, and the success path
prints it followed by the confirmation. If the early emission is simply added, each of those
paths prints the screen twice, and a maintainer scrolling back cannot tell a duplicate from a
second repository.

The maintainer should see each screen exactly once, whichever way the run ends, and should
still see the outcome line that says which way it ended.

**Why this priority**: A doubled screen is not a functional failure, so this cannot outrank
story 1; but it makes the output of the very command story 1 fixes harder to read, so it ships
with it in practice.

**Independent Test**: Drive every exit path — approved, declined, already-onboarded with no
change, and the refusal of a non-interactive run against unapproved committed settings — and
confirm no line of the approval screen appears more than once in the run's combined output,
while each run's outcome line appears exactly once.

**Acceptance Scenarios**:

1. **Given** an interactive run the maintainer approves, **When** it completes, **Then** the
   approval screen appears exactly once and is followed by the confirmation that the
   repository was onboarded.
2. **Given** an interactive run the maintainer declines, **When** it completes, **Then** the
   approval screen appears exactly once and is followed by the abort notice, and the exit
   status still distinguishes "I decided not to" from "the system refused".
3. **Given** a run that skips the prompt and is refused because committed settings are present
   and unapproved, **When** it completes, **Then** the screen appears exactly once and is
   followed by the refusal explaining why skipping the prompt was not allowed.
4. **Given** a repository already onboarded whose fingerprint has not changed, **When**
   onboarding runs without `--reapprove`, **Then** the screen appears exactly once, the run
   reports there is nothing to do, and no prompt is asked.
5. **Given** a run refused during resolution or verification, before an approval screen exists,
   **When** it completes, **Then** it prints only the refusal naming the cause and the edit
   that fixes it, exactly as it does today.

---

### User Story 3 - The exits from the screen stay accountable and stay machine-readable (Priority: P3)

Two things follow from the maintainer actually reading the screen.

The first is that interrupting becomes an ordinary way to leave. A maintainer who reads a
wrong clone path is at least as likely to press Ctrl-C as to type `n` — the screen has already
told them what they needed, and the question is now beside the point. Typing `n` writes a
record naming the repository and the outcome. Interrupting writes nothing: the run reports
that it was interrupted and exits, and the audit log holds no trace that onboarding was ever
attempted. Onboarding's contract requires every non-zero exit to be recorded, and the
constitution's accountability principle requires the log alone to answer what was attempted
and with what result. Today that gap is unlikely to be hit because nobody reaches the prompt
informed enough to abandon it; after story 1 it is the natural second-most-common ending.

The second is that the machine-readable mode has to stay machine-readable. Asking a question
writes the question somewhere, and if that somewhere is the same stream carrying the
machine-readable document, the document stops parsing. This is already true today and is made
neither better nor worse by story 1 — but the fix for story 1 decides where the screen is
written, and that decision is the same decision.

**Why this priority**: Both are real and both are small, but neither blocks the maintainer from
reading the screen before approving, which is what the issue asks for.

**Independent Test**: Interrupt an interactive run at the prompt and confirm the audit log
holds a record naming the repository and an interrupted outcome. Separately, run in
machine-readable mode and confirm the machine-readable stream parses as a single document with
no human-facing text in it.

**Acceptance Scenarios**:

1. **Given** an interactive run stopped at the prompt, **When** the maintainer interrupts it,
   **Then** the run exits non-zero, says it was interrupted, and the audit log holds a record
   naming the repository, the resolved clone path, and an outcome distinguishable from both
   approval and an explicit decline.
2. **Given** the same interruption, **When** the audit log is read afterwards, **Then** it is
   possible to tell that onboarding was attempted and abandoned without re-running anything.
3. **Given** a run in machine-readable mode, **When** it completes by any path, **Then** the
   machine-readable stream contains the machine-readable document and nothing else — no
   approval screen, no prompt text, no outcome prose.
4. **Given** a run in machine-readable mode that reaches a prompt, **When** the prompt is
   asked, **Then** the maintainer can still see and answer it.

---

### Edge Cases

- **Output redirected or piped.** The screen must be flushed to its destination before the
  prompt blocks, not merely written to a buffer that happens to drain when the process ends.
  A run whose output is being captured while it is answered interactively is the case that
  exposes the difference.
- **Input arriving from a pipe rather than a keyboard.** A run answered by piped input still
  emits the screen in the same order; the ordering is a property of the command, not of
  whether a human is present.
- **A very long committed-settings block.** The screen can be longer than the terminal, so the
  prompt scrolls away from the repository line at the top. The prompt names the repository it
  is asking about, and continues to.
- **Nothing to show.** A run refused during resolution — not permitted, no clone, a linked
  worktree, the wrong repository at that path — never composes an approval screen at all, and
  emits only its refusal.
- **A prompt that is never reached.** An already-onboarded repository with an unchanged
  fingerprint, and a refused non-interactive run, both end without asking. Neither should
  acquire a prompt, and both still show the screen.
- **The screen and the outcome disagree on stream.** The screen is information the maintainer
  asked for; some outcomes are failures. Both are part of one run and must be readable
  together when the two streams are viewed as one, and must not be duplicated when they are
  viewed separately.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Onboarding MUST write its complete approval screen to the maintainer's terminal
  before it blocks for the approval answer.
- **FR-002**: The approval screen MUST include, before the prompt: the repository key; the
  resolved clone path together with whether that path was derived from the configured
  repository root or set explicitly for this repository; the verified origin identity and the
  remote it was read from; the base ref; and the trust verdict with its explanation.
- **FR-003**: The full text of every committed tool-permission file at the base ref, or the
  statement that there are none, MUST appear before the prompt.
- **FR-004**: On a re-approval, the previously recorded clone path, any indication that it has
  changed, and the fingerprint diff against the approved version MUST appear before the prompt.
- **FR-005**: The approval screen MUST be flushed to its destination before the prompt blocks,
  including when that destination is a file or a pipe rather than a terminal.
- **FR-006**: No line of the approval screen may be emitted more than once in a single run, on
  any exit path.
- **FR-007**: Each run MUST still emit exactly one outcome line stating how it ended —
  onboarded, aborted, nothing to do, or refused — and that line MUST follow the screen.
- **FR-008**: The exit code for each path MUST be unchanged: success for approved or
  already-current, the refusal code for every resolution, verification or non-interactive
  refusal, and a distinct code for an explicit decline at the prompt.
- **FR-009**: Runs refused before an approval screen exists MUST emit only their refusal
  message, which MUST continue to name the cause and the edit that resolves it.
- **FR-010**: Declining at the prompt MUST continue to record nothing about the repository
  beyond the audit record of the declined attempt.
- **FR-011**: Interrupting the run at the approval prompt MUST write an audit record naming the
  repository, the resolved clone path, and an outcome distinguishable from both approval and
  an explicit decline.
- **FR-012**: In machine-readable mode, the machine-readable stream MUST carry the
  machine-readable document alone — no approval screen, no prompt text, no outcome prose —
  while any prompt remains visible and answerable by the maintainer.
- **FR-013**: The audit record written on approval MUST be unchanged in content and MUST
  continue to be written only after the maintainer approves.
- **FR-014**: Every other command that asks the maintainer a question MUST behave as it does
  today; no prompt text, exit code, or recorded outcome changes outside onboarding.

### Key Entities

- **Approval screen**: everything onboarding has resolved and read about a repository,
  assembled for the maintainer to judge before answering. Composed of the repository identity,
  the clone location and its provenance, the verified origin, the base ref, the trust verdict,
  the committed settings in full, and on re-approval the recorded path and fingerprint diff.
  It exists only when resolution and verification have both succeeded.
- **Onboarding outcome record**: the durable record of how an onboarding attempt ended. Names
  the repository, the resolved clone path, and one of: approved, declined at the prompt,
  interrupted at the prompt, refused for unapproved committed settings, or refused during
  resolution or verification.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In every interactive onboarding run that reaches the approval prompt, 100% of the
  approval screen is readable before the prompt blocks.
- **SC-002**: A maintainer who needs to check the clone path, path provenance, verified origin,
  base ref, trust verdict or committed settings before approving needs exactly one run of the
  command to do so — down from two, or from one approval given unread.
- **SC-003**: Across all five exit paths — approved, declined, interrupted, already current,
  and refused — no line of the approval screen is emitted twice, and each run emits exactly one
  outcome line.
- **SC-004**: All five exit paths leave an audit record naming the repository and the outcome,
  up from four of five today.
- **SC-005**: Exit codes for all five paths are identical to today's, verified path by path.
- **SC-006**: A machine-readable run's machine-readable stream parses as a single complete
  document on 100% of exit paths, including those that ask a question.

## Assumptions

- **Onboarding is the only command affected.** Every other prompt in the system asks a
  self-contained question — stop this session, delete these simulated rows, type this item id
  to force removal — with nothing composed ahead of it that the maintainer must read. Only
  onboarding builds a screen and then asks about it, so only onboarding changes behaviour.
  FR-014 exists to hold the others still, not to extend the fix to them.
- **The approval screen goes to standard output; failure and abort messages keep the stream
  they use today.** The screen is information the maintainer asked for, so it belongs on the
  output stream even when the run later fails; the existing convention that non-zero outcomes
  are reported on the error stream is not disturbed.
- **Auditing the interrupted exit is in scope.** It is an existing gap — onboarding's contract
  already requires every non-zero exit to be recorded, and the constitution's accountability
  principle already forbids an unrecorded attempt. It is included here rather than deferred
  because this feature is what makes interruption a normal way to leave the prompt: before it,
  nobody reaches that prompt informed enough to walk away. If the maintainer would rather this
  were tracked separately, User Story 3's first half is the piece to strike, and stories 1 and
  2 stand without it.
- **The prompt's wording is unchanged.** It already names the repository it asks about, which
  is what keeps it legible after a long settings block has scrolled the identity line away.
- **No new information is added to the screen.** This feature changes when the screen is shown,
  not what it contains. The content is the one already specified by the onboarding contract.
- **Nothing about resolution, verification, refusal wording, or what is recorded on approval
  changes.** The five refusal messages and the recorded columns are out of scope.
- **A working terminal is assumed for the interactive path**, and a maintainer who redirects
  output while answering interactively is a supported but secondary case, covered by FR-005.
