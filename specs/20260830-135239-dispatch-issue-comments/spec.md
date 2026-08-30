# Feature Specification: Say on the issue which machine and which session picked it up

**Feature Branch**: `issues/38`

**Created**: 2026-08-30

**Status**: Draft

**Input**: User description: "issue #38 on this repo"

Source: [#38 — Issue comments on dispatch](https://github.com/jantman/robot-army/issues/38)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The issue names the machine and the session working on it (Priority: P1)

The maintainer looks at a GitHub issue that robot-army picked up, days or weeks after the fact,
and wants to read the session's logs and transcript. The logs live on whichever machine hosted
the session, under the session's own identifier.

Today the issue gets a comment when a session is confirmed, and it names the branch, the worktree
path and the session identifier — but it never says *which machine* any of that is on. With more
than one machine running robot-army, a worktree path alone does not say where to look, and the
maintainer has to guess or check each machine in turn. The comment also omits the session's
human-readable name, which is the string that identifies the session in the terminal tab title
and in the session picker — the two places the maintainer actually looks first.

After this feature, the dispatch comment carries the host, the session name and the session
identifier alongside what it already carries, so the issue alone is enough to find the session.

**Why this priority**: It is the reported request and the whole point of the issue: correlating an
issue with the session that worked on it. Everything else in this feature is a variation on the
same comment.

**Independent Test**: Dispatch an item, then read the resulting comment on the issue and confirm it
names the host, the session name and the session identifier, and that those match what the system
reports for that session locally. Delivers the correlation on its own.

**Acceptance Scenarios**:

1. **Given** an item that reaches a confirmed live session, **When** the maintainer reads the
   issue, **Then** a comment is present naming the host the session runs on, the session's name,
   the session identifier, the branch and the worktree path.
2. **Given** that comment, **When** the maintainer compares it against what the system reports for
   that item locally, **Then** the host, session name and session identifier are identical to the
   recorded ones — no rounding, shortening or re-derivation.
3. **Given** a dispatch that fails before a session is confirmed, **When** the maintainer reads the
   issue, **Then** the failure comment says which host the attempt was made on, so a failure that
   only occurs on one machine is attributable.
4. **Given** the comment posted for a confirmed session, **When** a pull request is later opened
   from that work, **Then** the branch named in the comment is the branch the pull request is
   opened from, which is what ties the pull request back to the session.

---

### User Story 2 - A second attempt says it is a second attempt (Priority: P1)

An item is interrupted or comes back for review, and the maintainer resumes or restarts it. That
is a new session, on a machine that may not be the one that ran the first attempt.

Today the issue does get a comment for the new session, but it is worded exactly like the first
one and it makes no reference to the session it replaced. Reading the issue afterwards, the
maintainer sees two identical-looking announcements with different identifiers and cannot tell
which came first, whether the second continued the first's context, or whether the first was
abandoned.

After this feature, the comment for a subsequent attempt says that this issue has been reassigned
to a new session, names the session it supersedes, and says whether the new session continues the
previous one's context or starts fresh.

**Why this priority**: The issue asks for it explicitly ("Also comment if, for some reason, an
issue is reassigned to a new session"), and a reassignment is precisely the case where a single
identifier is not enough to reconstruct what happened.

**Independent Test**: Dispatch an item, interrupt it, then resume it and separately restart
another one; confirm each subsequent comment names the superseded session and distinguishes
resumption from a fresh start. Testable without touching the first-dispatch wording.

**Acceptance Scenarios**:

1. **Given** an item that has already had a confirmed session, **When** it is dispatched again,
   **Then** the new comment identifies itself as a reassignment rather than a first dispatch.
2. **Given** a resumed item, **When** the reassignment comment is read, **Then** it names the
   session whose context was restored.
3. **Given** a restarted item with no restored context, **When** the reassignment comment is read,
   **Then** it names the previous session it supersedes and says the new session starts without
   that session's context.
4. **Given** an item dispatched three times, **When** the maintainer reads the issue top to bottom,
   **Then** the comments form an ordered record of every session that has held the issue, each with
   its own host and identifier and none overwritten or removed.

---

### User Story 3 - The record survives GitHub being unavailable, and never lies (Priority: P2)

GitHub is unreachable, rate-limited, or the token has lost permission to comment at the moment a
session is confirmed. The session itself is fine.

The comment is a convenience for the maintainer, not a precondition for the work. A failed comment
must not fail a healthy session, must not be silently dropped, and must never claim something that
did not happen — in particular, a rehearsal run at a simulated effect level must not write anything
to a real issue.

**Why this priority**: It is a constraint on the first two stories rather than new capability, but
it is the difference between a trustworthy record and a misleading one. The existing dispatch
comment already behaves this way; this story keeps that true as the comment grows.

**Independent Test**: Force the comment write to fail, and confirm the session still reaches a
running state and the failure is visible in the activity log. Separately, dispatch at a simulated
effect level and confirm nothing is written to the real issue.

**Acceptance Scenarios**:

1. **Given** a confirmed session, **When** the comment cannot be posted, **Then** the item stays
   active, and the failed attempt with its reason is recorded in the durable activity log.
2. **Given** a simulated dispatch, **When** the session is confirmed, **Then** no comment is
   written to the real issue and the log shows the write was simulated.
3. **Given** a dispatch that never confirmed a session, **When** the issue is read, **Then** no
   comment claims a session is running.

---

### Edge Cases

- The host has no resolvable name, or reports an empty one. The comment must still post, saying
  plainly that the host could not be determined rather than omitting the line or printing an empty
  value.
- The item's previous session record is missing — the database was rebuilt, or the item was
  dispatched again after its history was pruned. The comment must post as a dispatch it cannot
  place in a sequence, rather than failing or inventing a predecessor.
- The same item is dispatched twice in quick succession. Each confirmed session posts its own
  comment; no comment is edited or replaced in place.
- The issue has been closed, locked, transferred, or deleted between dispatch and confirmation.
  This is a comment failure like any other: logged, non-fatal, session unaffected.
- The comment body would be unusually long — a long branch name, a deep worktree path. It must
  remain a short, scannable set of labelled facts, not a wall of text.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: On every confirmed session, the system MUST post a comment on the originating issue
  stating the host the session runs on, the session's name, the session's identifier, the branch,
  and the worktree path.
- **FR-002**: The host, session name and session identifier in the comment MUST be the same values
  the system records for that session, so that a value copied from the issue can be used directly
  to find the session's logs and transcript.
- **FR-003**: When a work item is dispatched again after a previous session — resumed or restarted —
  the comment MUST identify the dispatch as a reassignment, name the session it supersedes, and
  state whether the new session continues that session's context.
- **FR-004**: Every dispatch attempt that confirms a session MUST produce its own comment. The
  system MUST NOT edit, replace, or delete any comment it previously posted, so the issue holds the
  full ordered history of sessions that worked on it.
- **FR-005**: When a dispatch attempt fails before a session is confirmed, the comment reporting
  that failure MUST name the host the attempt was made on, in addition to the reason it already
  reports.
- **FR-006**: The system MUST NOT post any comment claiming a session is running before that
  session has been confirmed running.
- **FR-007**: A failure to post a comment MUST NOT change the work item's state or the session's
  fate, and MUST be recorded in the durable activity log with its reason.
- **FR-008**: At effect levels where writing to the issue source is simulated, the system MUST NOT
  write the comment to the real issue, and the log MUST mark the write as simulated.
- **FR-009**: When the host name cannot be determined, the system MUST still post the comment and
  MUST say the host is unknown rather than omitting it or showing an empty value.
- **FR-010**: When no previous session can be found for an item being dispatched again, the system
  MUST post the comment without naming a predecessor rather than failing or asserting one.
- **FR-011**: Comments MUST be identifiable as written by robot-army, and their facts MUST be
  presented as short labelled lines that stay readable in GitHub's rendering.
- **FR-012**: The comment's content and the conditions under which each variant is posted MUST be
  documented for the maintainer alongside the project's existing behaviour documentation.

### Key Entities

- **Work item**: The issue robot-army has taken on. Carries the repository, issue number, branch
  and worktree path that the comment reports, and is the thing a reassignment reassigns.
- **Session**: One attempt at a work item. Carries the identifier the comment publishes, the name
  it is known by in the terminal, the host it runs on, and — for a resumed attempt — the session
  whose context it restored.
- **Dispatch comment**: The message written to the issue. Exists in three variants — first
  dispatch, reassignment, and failed attempt — and is never modified after it is written.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Given only an issue that robot-army worked on, the maintainer can name the machine
  and the session identifier for every session that held it, without consulting any other system.
- **SC-002**: 100% of confirmed sessions leave a comment on their issue naming host, session name
  and session identifier, or leave a logged reason why the comment could not be written.
- **SC-003**: For an item dispatched more than once, the issue's comments alone are enough to put
  its sessions in order and to say, for each one, whether it continued the previous session's work.
- **SC-004**: No comment written by the system asserts a session that was never confirmed, and no
  simulated run writes to a real issue — verified by exercising both paths.
- **SC-005**: A comment that cannot be posted never changes the outcome of the session it describes.

## Assumptions

- The existing dispatch and failure comments are the place this work happens; this feature extends
  their content and adds the reassignment variant rather than introducing a new notification
  channel.
- "Session name" means the identifiable name the session is launched under and shown by in the
  terminal and session picker; "session UUID" means the identifier the system generates and
  correlates logs, transcripts and exit status by. The issue asks for the name *or* the UUID; both
  are published because they are the two different handles the maintainer searches with.
- The host is the machine's own name as the operating system reports it, matching what the system
  already reports elsewhere for health and notifications. No network resolution or fully-qualified
  name is required.
- Pull request correlation is achieved through the branch, which the comment already names and
  which a pull request for the work is opened from. No separate pull-request-to-session link is
  built here.
- Comments are posted after a session is confirmed, which is where the existing dispatch comment
  already sits; a launch call returning success is not treated as a session.
- The repository is single-user and its issues are the maintainer's own, so the comment's content
  is not sensitive. Host name and worktree path are already published in this repository's public
  issues by the existing comment.
- Nothing is posted when a session ends, when a pull request opens, or when an item is abandoned;
  those are separate behaviours and are out of scope.
