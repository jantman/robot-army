# Feature Specification: Prompt Preview Command

**Feature Branch**: `speckit/20260903-190827-prompt-preview-command`

**Created**: 2026-09-03

**Status**: Draft

**Input**: User description: "Add a `robot-army` subcommand that takes a repo slug and issue number (of an onboarded repo), generates the prompt that would be passed to claude code for that issue, and prints it to STDOUT. The name of the command could be `prompt` so that the usage would be something like `robot-army prompt <orgName/repoName> <issue number>`"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See what a session will be told, before it runs (Priority: P1)

The maintainer has an issue they are considering labelling for dispatch, or one already
queued and waiting. They want to read the exact text a worker session would be handed —
the repository's own standing instructions, the Spec Kit guidance if it applies, the
delivery rules, and the issue itself — without starting a session, creating a worktree, or
spending a model call. They run one command with the repository slug and the issue number
and the whole prompt appears in their terminal.

**Why this priority**: This is the feature. Every other story here is a refinement of the
output of this one. Today the only way to find out what a session is told is to dispatch
it and read the transcript, which costs a worktree, a branch, and a model session, and
which cannot be done at all for an issue that is not yet eligible.

**Independent Test**: Run the command against an onboarded repository and an issue number
that has never been dispatched; confirm the printed text contains the delivery rules, the
issue's title, URL, labels, and body, and that no worktree, branch, work item, or session
was created.

**Acceptance Scenarios**:

1. **Given** an onboarded repository with an open issue that is not tracked as a work item,
   **When** the maintainer runs the command with that repository slug and issue number,
   **Then** the full composed prompt is printed and the command exits successfully.
2. **Given** a repository whose checkout carries repository-specific standing instructions,
   **When** the command runs against an issue in it, **Then** those instructions appear at
   the top of the printed prompt, ahead of every other section.
3. **Given** a repository detected as a Spec Kit project and not opted out, **When** the
   command runs, **Then** the Spec Kit guidance appears between the repository's own
   instructions and the delivery rules, exactly where a dispatch would put it.
4. **Given** a repository for which the Spec Kit behaviour is suppressed by configuration,
   **When** the command runs, **Then** no Spec Kit guidance appears, matching what that
   repository's sessions actually receive.
5. **Given** any onboarded repository, **When** the command runs, **Then** the delivery
   rules appear immediately above the issue section, unconditionally.

---

### User Story 2 - Reproduce what an already-dispatched session was told (Priority: P2)

An issue has been dispatched and its session behaved unexpectedly — it worked on the wrong
branch, ignored a house rule, or missed a Spec Kit step. The maintainer wants to see the
prompt for that issue as the system composes it, so they can tell whether the session was
misinstructed or merely disobedient.

**Why this priority**: It turns a class of "why did it do that?" question into a two-second
check. It is second because it is diagnosis after the fact, whereas Story 1 prevents the
bad dispatch in the first place.

**Independent Test**: Run the command for an issue that already has a worktree on disk and
confirm the branch named in the printed prompt is the branch the item is actually recorded
against, and that the contextual sections are read from that worktree.

**Acceptance Scenarios**:

1. **Given** a tracked work item with a recorded branch, **When** the command runs for its
   issue, **Then** the prompt names that recorded branch rather than a freshly derived one.
2. **Given** a tracked work item whose worktree still exists on disk, **When** the command
   runs, **Then** the repository instructions and Spec Kit decision come from that worktree,
   and a note on the diagnostic stream says so.
3. **Given** an issue with no worktree, **When** the command runs, **Then** the contextual
   sections come from the repository's onboarded checkout, a note on the diagnostic stream
   says so, and the branch is the one a dispatch would derive.

---

### User Story 3 - Pipe, save, and diff the output (Priority: P3)

The maintainer wants to redirect the prompt to a file, page it, or diff two runs after
editing the repository's standing instructions, to confirm the edit lands where they
expected.

**Why this priority**: It costs almost nothing to get right and is worthless to retrofit —
a single stray header line on the output stream breaks every downstream use. It is last
because it is a property of Story 1's output rather than a separate capability.

**Independent Test**: Redirect the command's output to a file and confirm the file contains
the prompt and nothing else — no banner, no source note, no trailing summary.

**Acceptance Scenarios**:

1. **Given** a successful run, **When** the output stream is redirected to a file, **Then**
   that file holds the prompt text alone.
2. **Given** a run where the contextual sections came from a checkout rather than a
   worktree, **When** the output stream is redirected, **Then** the note explaining that
   appears on the diagnostic stream and not in the file.
3. **Given** a failing run, **When** the output stream is redirected, **Then** the file is
   empty and the reason for the failure appears on the diagnostic stream.

---

### Edge Cases

- **Repository not onboarded**: the command refuses, explains that the repository has not
  been through the onboarding step, and prints nothing as output. Composing a prompt for an
  unapproved repository would read that repository's files, which is precisely the step
  onboarding exists to gate.
- **Repository slug malformed** (no `owner/name` shape) or **issue number not a positive
  integer**: usage error, nothing on the output stream.
- **Issue does not exist, or the issue source rejects the request** (authentication,
  rate limit, network failure): the command fails with the reason on the diagnostic stream
  and nothing on the output stream. It never prints a partial or invented prompt.
- **Issue exists but is closed, or is a pull request**: the prompt is still composed and
  printed. The command reports what would be sent; deciding whether an issue is eligible for
  dispatch is a different question with its own answer elsewhere.
- **Issue has an empty body**: the prompt carries the same placeholder text a dispatch would
  use, not an empty section.
- **Issue body longer than the dispatch limit**: the prompt is truncated exactly as a
  dispatch truncates it, including the pointer back to the issue URL.
- **Repository onboarded but its checkout is missing or unreadable**: the prompt is still
  printed, with the repository-instruction and Spec Kit sections omitted, and a note on the
  diagnostic stream explaining why those sections are absent — an omission the reader must
  not mistake for the repository genuinely having none.
- **Issue title reduces to no usable slug** (emoji-only or non-Latin title): the branch name
  in the prompt omits the slug, matching dispatch.
- **Dispatch is paused, or the item is on hold**: irrelevant. The command reads and prints;
  it is not a dispatch and is not gated by dispatch controls.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The tool MUST accept a subcommand that takes a repository slug and an issue
  number as positional arguments, in that order.
- **FR-002**: The command MUST print the prompt that a dispatch of that issue would compose
  from the same inputs, identical character for character — same sections, same order, same
  separators, same truncation rule.
- **FR-003**: The printed prompt MUST be the only thing on the command's output stream. No
  banner, header, footer, label, or summary may accompany it.
- **FR-004**: Every explanatory note, warning, and error message MUST go to the diagnostic
  stream, never the output stream.
- **FR-005**: The command MUST refuse to run for a repository that has not been onboarded,
  reporting a precondition failure and printing nothing to the output stream.
- **FR-006**: The command MUST work for any issue in an onboarded repository, whether or not
  that issue is tracked as a work item, has ever been dispatched, or carries a dispatch label.
- **FR-007**: The branch named in the prompt MUST be the branch recorded against the work
  item when one exists, and otherwise the branch name a dispatch of that issue would derive.
- **FR-008**: The repository's standing instructions and the Spec Kit detection MUST be read
  from the item's existing worktree when one is present on disk, and otherwise from the
  repository's onboarded checkout.
- **FR-009**: The command MUST name, on the diagnostic stream, which location the contextual
  sections were read from, so a reader can tell a genuinely absent section from one that was
  unreadable.
- **FR-010**: The Spec Kit guidance MUST be included or omitted according to the same
  detection and the same configuration gates a dispatch would apply, including any
  repository-level suppression and any configured command list.
- **FR-011**: The delivery rules MUST appear in every printed prompt, with no way for a
  caller to suppress them, matching their unconditional presence in a dispatch.
- **FR-012**: Running the command MUST NOT change any state outside the process beyond its
  own record of having run: no work item created or modified, no branch, no worktree, no
  session, no comment or label written back to the issue source, no file written into the
  repository.
- **FR-013**: The command MUST record its run in the durable log — the repository and issue
  it was asked about, the outcome, and the Spec Kit decision it reached — so that a prompt
  read out of the terminal can be tied to a moment in the record.
- **FR-014**: A failure to obtain the issue MUST produce a failure exit status with the
  reason on the diagnostic stream and nothing on the output stream.
- **FR-015**: A malformed repository slug or a non-numeric issue number MUST produce a usage
  error, distinguishable by exit status from a repository that is simply not onboarded and
  from an issue that could not be fetched.
- **FR-016**: The command MUST honour the tool's existing global configuration-file option,
  so a preview reflects the configuration a dispatch under the same options would use.
- **FR-017**: The command MUST be documented in the same places the tool's other commands
  are documented, including its exit statuses.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A maintainer can read the complete text a worker session would receive for any
  issue in an onboarded repository by running one command, without dispatching anything.
- **SC-002**: For a given issue and unchanged inputs, the printed text matches the text a
  real dispatch composes in 100% of cases, with no section present, absent, or reordered.
- **SC-003**: The output redirected to a file contains the prompt and nothing else; a second
  run with unchanged inputs produces a byte-identical file.
- **SC-004**: Comparing the full recorded system state before and after a run shows no
  difference other than the log entry the run itself writes — no new work item, branch,
  worktree, session, or change at the issue source.
- **SC-005**: Every run, successful or failed, leaves a log entry naming the repository, the
  issue, and the outcome, so the record answers what was asked and what was answered.
- **SC-006**: A run against a typical issue completes in the time of a single issue lookup —
  seconds, not the minutes a dispatch takes — with no session started and no model call made.
- **SC-007**: Each documented failure case (repository not onboarded, malformed arguments,
  issue unavailable) is distinguishable by exit status alone, without parsing any message.

## Assumptions

- The subcommand is named `prompt`, giving the usage `robot-army prompt <owner/repo> <number>`,
  as the feature description suggests. It reads a prompt; it does not prompt the user for
  anything.
- The output is plain text only. No machine-readable output mode is added: the prompt is a
  single opaque string, so wrapping it adds a mode with no caller and a second thing to keep
  correct, against the project's simplicity principle. Redirection covers the machine case.
- The command prints the prompt only, not the full worker command line. Permission mode,
  model selection, session naming, and environment are outside its scope; a reader who needs
  those has other commands for them.
- The command reads the issue live at invocation time. Its output is a preview of what a
  dispatch *now* would send, not a record of what a past dispatch actually sent — an issue
  edited since dispatch will produce a prompt that differs from the one that ran, and the
  historical prompt is not recoverable from this command.
- Reading contextual sections from the repository's onboarded checkout when no worktree
  exists is an approximation: the checkout may sit on a different branch or carry uncommitted
  changes, so a preview taken from it can differ from what a freshly created worktree would
  hold. Naming the source (FR-009) is what keeps that honest; reproducing a worktree just to
  answer the question would not be worth its cost.
- Onboarding is what makes reading a repository's files acceptable, so the command inherits
  that gate rather than introducing its own trust decision.
- The existing issue-source, prompt-composition, Spec Kit detection, and branch-naming
  behaviour are reused as they stand. This feature adds a way to see their output; it changes
  none of them, and any observed difference between preview and dispatch is a defect in this
  feature rather than a reason to alter dispatch.
