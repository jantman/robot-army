# Feature Specification: GitHub Project Board Ordering

**Feature Branch**: `robot-army/issue-48-github-project-ordering`

**Created**: 2026-09-02

**Status**: Draft

**Input**: [jantman/robot-army#48](https://github.com/jantman/robot-army/issues/48) — "Add support for GitHub Projects, where issues are dispatched in order matching that of the project. Discovery of projects should be automatic for a given repo, with a manual configuration fallback if there are multiple projects or if discovery returned an indeterminate result. We should support, at a minimum, the following paradigms: (1) GitHub's default 'Kanban' model, as seen in https://github.com/users/jantman/projects/3/views/1 , where the 'Ready' column is where we pull issues to work from. (2) A 'Todo' / 'In Progress' / 'Done' model, such as https://github.com/users/jantman/projects/2/views/1 , where we pull issues from the 'Todo' column. (3) A manually-configured model where we define via repo-specific configuration the name of the column to pull issues from. We must still only look at issues with the configured robot-army label. This ordering only matters within one repo/project. We don't care if an issue from repo A gets dispatched before or after an issue from repo B, just that within a repo they're dispatched in priority order from the project board."

## Context: what already exists

Recording the current shape so planning does not rediscover it.

- Dispatch order has exactly one producer. It sorts every ready work item into a single
  list, and the dispatcher, the web queue page, and the terminal status command all render
  that same list. Keeping those three identical is an existing, deliberate invariant.
- Two order modes exist today. `oldest-first` (the default) orders by when an item was
  discovered. `repo-priority` groups by an integer priority set per repository, then falls
  back to discovery time. Neither has any notion of a per-issue rank; there is no place in
  the stored work item where a rank could live.
- Ordering is computed on every read, including every web page render, and does no network
  I/O. Anything the order depends on must already be on disk by the time the queue is drawn.
- Eligibility is decided at poll time, not at ordering time. An issue becomes a ready work
  item only if it carries the configured label, belongs to an onboarded repository, was
  opened by the configured author, and is still open. The label is the human "go" gate and
  the author check is a security boundary that cannot be switched off.
- The queue can hold an item for a named reason, and those reasons have a fixed precedence.
  Adding a new reason means slotting it into that order deliberately.
- Nothing in the system knows GitHub Projects exist. All GitHub access is REST, through one
  module that is the only place allowed to know the GitHub API exists at all. There is no
  GraphQL plumbing, and GitHub Projects of the current generation are only reachable over
  GraphQL.
- Trello is not a second source of dispatchable work; it files GitHub issues and stops. So
  Trello-originated issues inherit whatever this feature does, with no special handling.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Work arrives in the order the board says (Priority: P1)

The author keeps a project board for a repository and drags the issues they care about most
to the top of its ready column. When robot-army dispatches from that repository, it takes
them in that order — the top card first — rather than in the order the issues happened to be
filed. When the author reorders the board, the next pass reflects the new order without
restarting anything.

**Why this priority**: This is the whole point of the issue. Discovery, configuration, and
reporting all exist to serve this behavior, and none of them is worth building without it.

**Independent Test**: Label three issues in one repository, place them on a project board in
an order that differs from their creation order, and confirm the queue and the dispatcher
both present them in board order. Reorder the board and confirm the queue follows within one
poll interval.

**Acceptance Scenarios**:

1. **Given** a repository whose project board and dispatch column are resolved, and three
   ready items whose board order is the reverse of their discovery order, **When** the queue
   is computed, **Then** those three items appear in board order relative to one another.
2. **Given** that same state, **When** the board is reordered, **Then** after the next poll
   of that repository the queue reflects the new board order without any restart.
3. **Given** ready items in two repositories, one of them project-ordered and one not,
   **When** the queue is computed, **Then** the project-ordered repository's items appear in
   board order relative to one another, and the other repository's items are ordered exactly
   as they are today.
4. **Given** a repository with no resolved project, **When** the queue is computed, **Then**
   its items are ordered exactly as they are today and nothing about its behavior changes.
5. **Given** a project-ordered repository under the `repo-priority` order mode, **When** the
   queue is computed, **Then** the repository still occupies the same positions in the
   overall queue that its priority earns it, and only the assignment of its own items to
   those positions changes.
6. **Given** a labelled, open, eligible issue in a project-ordered repository that is not on
   the board at all, **When** the queue is computed, **Then** it is still dispatchable, and it
   is ordered after every item of that repository that holds a position in the dispatch
   column.
7. **Given** a labelled, open, eligible issue in a project-ordered repository that sits on the
   board in a column other than the dispatch column, **When** the queue is computed, **Then**
   it is held rather than dispatched, and the hold names the column it is actually sitting in.
8. **Given** that held issue, **When** it is moved into the dispatch column, **Then** no later
   than the next successful poll it stops being held and takes its board position in the
   queue.
9. **Given** a project-ordered repository whose board could not be read on this pass but was
   read successfully before, **When** the queue is computed, **Then** the last board read
   successfully decides both the order and the holds, and the queue shows that it is stale.
10. **Given** a project-ordered repository whose board has never been read successfully,
    **When** the queue is computed, **Then** nothing is held for its column and its items are
    ordered exactly as they are today.

---

### User Story 2 - The board is found without being described (Priority: P2)

The author connects a project to a repository and expects robot-army to work it out. When
exactly one project is associated with the repository and it has an obvious ready column —
`Ready` on GitHub's Kanban template, `Todo` on the simpler board — nothing needs to be
configured. When the answer is not obvious, robot-army says so plainly and the author names
the project, the column, or both in that repository's configuration.

**Why this priority**: Without discovery this is just another pair of settings to maintain by
hand, which the issue explicitly asks to avoid. It is P2 rather than P1 because a manually
configured board already delivers Story 1's value.

**Independent Test**: Point the system at a repository with one linked Kanban-template
project and confirm the board is used with no configuration. Then point it at a repository
with two linked projects and confirm it declines to guess, says why, and honours an explicit
setting.

**Acceptance Scenarios**:

1. **Given** a repository with exactly one associated project whose columns include `Ready`,
   **When** its ordering is resolved with no repository configuration, **Then** that project
   and that column are used, and both are reported as having been discovered.
2. **Given** a repository with exactly one associated project whose columns include `Todo`
   but not `Ready`, **When** its ordering is resolved, **Then** `Todo` is used.
3. **Given** a repository with more than one associated project, **When** its ordering is
   resolved with no configuration, **Then** no project is chosen, the ambiguity and the
   candidate projects are recorded and surfaced, and the repository is ordered as it is
   today.
4. **Given** a repository with one associated project whose columns match none of the
   recognised names, **When** its ordering is resolved with no configuration, **Then** no
   column is chosen, the reason is recorded and surfaced, and the repository is ordered as it
   is today.
5. **Given** a repository configured with an explicit project and an explicit column,
   **When** its ordering is resolved, **Then** those values are used without discovery, and
   both are reported as having been configured.
6. **Given** a repository configured with an explicit column only, **When** its ordering is
   resolved, **Then** the project is discovered and the configured column is used.
7. **Given** a repository configured with a project or column that does not exist on the
   board, **When** its ordering is resolved, **Then** the mismatch is reported naming what
   was asked for and what the board actually offers, and the repository is ordered as it is
   today.
8. **Given** a repository with a resolvable project and no configuration of any kind,
   **When** its ordering is resolved, **Then** board ordering and its holds take effect for
   that repository without the author having enabled anything.
9. **Given** that same repository with project ordering switched off in its configuration,
   **When** its ordering is resolved, **Then** no project is consulted for it, nothing is held
   for a column, and it is ordered exactly as it is today.

---

### User Story 3 - Knowing why the queue is in the order it is in (Priority: P3)

Before trusting the order, the author wants to see, per repository, whether a board is
driving it, which project and column, whether those were discovered or configured, and when
the board was last read. When a board should be driving the order and is not, they want the
reason on screen rather than in the log.

**Why this priority**: An ordering the author cannot explain is an ordering they will not
rely on, and a board that silently stopped being read is worse than no board. It is P3
because Stories 1 and 2 are useful the moment they work, and this makes them trustworthy.

**Independent Test**: With one repository ordered by a board, one ambiguous, and one with no
board at all, confirm each reports its own state from the terminal and from the web queue
page without consulting the log.

**Acceptance Scenarios**:

1. **Given** a repository ordered by a board, **When** the author asks for status, **Then**
   the project, the column, whether each was discovered or configured, and the time the board
   was last read are reported.
2. **Given** a repository whose board could not be resolved, **When** the author asks for
   status, **Then** the specific reason is reported — ambiguous project, unrecognised column,
   configured value not found, or credentials that cannot read projects.
3. **Given** a repository whose board could not be read on the most recent pass, **When** the
   queue is shown, **Then** it is visible that the order shown is stale, and how stale.
4. **Given** any of the above states, **When** the queue is rendered in the web UI and
   printed by the status command, **Then** both show the same order and the same explanation.

---

### Edge Cases

- A project spans several repositories. Only the items whose issue belongs to the repository
  being ordered take part in that repository's order.
- The same issue appears on more than one project. Only the project resolved for that
  repository is consulted.
- The dispatch column contains draft items that are not issues, or issues that are closed,
  not labelled, or by another author. They occupy no position and are ignored; they must not
  create work items, and must not become gaps or holes in the order.
- The board is renamed, the column is renamed or deleted, or the project is closed or
  deleted between two passes.
- The board is reordered while one of its items is already dispatching or running. The item
  in flight is unaffected; only the remaining ready items are reordered.
- Two ready items in the same repository end up with no distinguishable board position, or
  the board read returns them without an order.
- The project has more items than one response can carry and must be read in pages; the order
  must survive being assembled from several pages.
- The GitHub credentials are valid for issues but lack the scope needed to read projects.
- The project read fails, times out, or is rate-limited on a given pass.
- Project ordering is turned on for a repository that has never had a successful board read.
- A repository is onboarded but has no configuration section at all.
- Dry-run and simulated items belong to a project-ordered repository.
- The board's column is empty while the repository has ready items — every eligible item of
  that repository is held, and the queue must not read as though the repository is idle.
- An item held for sitting in a non-dispatch column is then removed from the board entirely,
  or the column it was sitting in is deleted.
- Project ordering resolves for a repository for the first time — on upgrade, or when a board
  is attached — and items that dispatched freely yesterday are held today.

## Requirements *(mandatory)*

### Functional Requirements

**Ordering**

- **FR-001**: The system MUST be able to order the ready work items of a repository by the
  top-to-bottom position of their issues in a designated column of a GitHub project board.
- **FR-002**: Applying board order to a repository MUST NOT change which positions in the
  overall queue that repository's items occupy under the configured global order mode; it
  MUST change only which of that repository's items occupies each of those positions.
- **FR-003**: A repository with no resolved board MUST be ordered exactly as it is today, and
  the existing order modes MUST continue to behave unchanged for it.
- **FR-004**: Board order MUST be reflected across the dispatcher, the web queue page, and
  the status command identically; these three MUST continue to present one and the same
  ordered list.
- **FR-005**: Rendering the queue MUST NOT require contacting GitHub. The board order used
  for a pass MUST already be stored locally when the queue is computed.
- **FR-006**: A change to the board's order MUST take effect no later than the next
  successful poll of that repository, without restarting the daemon.
- **FR-007**: Items whose board position cannot be distinguished from one another MUST fall
  back to the existing order between themselves, deterministically, so that the queue does
  not shuffle between two renders of unchanged state.
- **FR-008**: An eligible item of a project-ordered repository that is absent from the board
  entirely MUST remain dispatchable and MUST be ordered after every item of that repository
  holding a position in the dispatch column; such items MUST be ordered among themselves by
  the existing order.

**Eligibility and holds**

- **FR-009**: The configured label MUST remain required for an issue to become dispatchable.
  Presence on a board MUST NOT substitute for it.
- **FR-010**: The author check and every other existing eligibility rule MUST remain in force
  unchanged.
- **FR-011**: Board entries that are not open, labelled, eligible issues of the repository
  being ordered MUST be ignored for ordering purposes.
- **FR-012**: An eligible item of a project-ordered repository that appears on that board in a
  column other than the dispatch column MUST be held rather than dispatched, and the hold MUST
  name the column the item is actually sitting in.
- **FR-013**: That hold MUST be given an explicit, documented place in the existing hold-reason
  precedence, and MUST be reported through the same surfaces as every other hold.
- **FR-014**: An item MUST NOT be held for its column unless a board has been read
  successfully at least once for its repository. With no board knowledge, nothing is gated.
- **FR-015**: Moving a held item into the dispatch column MUST release that hold no later than
  the next successful poll of its repository, with no manual step.

**Discovery and configuration**

- **FR-016**: The system MUST attempt to discover the project associated with a repository
  without configuration, and MUST use it when exactly one candidate exists.
- **FR-017**: The system MUST select the dispatch column automatically when the board offers
  exactly one recognised candidate, recognising at minimum `Ready` and `Todo`.
- **FR-018**: When more than one project is associated with a repository, or more than one or
  none of the recognised column names is present, the system MUST NOT guess. It MUST record
  and surface the ambiguity, naming the candidates it saw.
- **FR-019**: Where a project and a dispatch column resolve unambiguously for a repository,
  board ordering and its holds MUST take effect for that repository without the author
  configuring or enabling anything.
- **FR-020**: The author MUST be able to switch project ordering off for a single repository,
  restoring that repository's present-day behavior exactly, including holding nothing for a
  column.
- **FR-021**: The author MUST be able to configure, per repository, the project to use, the
  column to pull from, or both, and configured values MUST take precedence over discovery.
- **FR-022**: A configured project or column that does not exist on the board MUST be
  reported as such, naming both the configured value and what the board offers, and MUST NOT
  silently fall back to a guess.
- **FR-023**: Whenever a board cannot be resolved for a repository, that repository MUST
  continue to dispatch under the configured global order mode rather than stalling.

**Accountability and interruption**

- **FR-024**: Every project discovery attempt, board read, resolution outcome, and fallback
  MUST be recorded with the repository, the project and column involved, and the result.
- **FR-025**: A failed or unreadable board MUST NOT silently produce a different order or a
  different set of holds. The system MUST either use the last board it read successfully —
  for both order and holds — and record that it is doing so, or fall back to the configured
  global order with nothing held, and record that.
- **FR-026**: The stored board order MUST survive the process being killed mid-update; a
  partially written order MUST NOT be observable to a later run.
- **FR-027**: The system MUST report, from the terminal, when the configured GitHub
  credentials cannot read projects, before that failure has a chance to change any order.

**Reporting**

- **FR-028**: For each repository, the system MUST report whether a board is driving its
  order, which project and column, whether each was discovered or configured, and when the
  board was last read successfully.
- **FR-029**: When the order being shown was not refreshed on the most recent pass, the queue
  MUST make that visible along with its age.
- **FR-030**: When every eligible item of a repository is held for sitting outside the
  dispatch column, that MUST be distinguishable from the repository simply having no work.

### Key Entities

- **Project board**: A GitHub project associated with a repository. Identified by owner and
  number, and carrying a name and a set of columns. At most one board is in effect for a
  repository at a time.
- **Dispatch column**: The single named column of a board whose contents robot-army pulls
  from. Chosen by discovery from a set of recognised names, or named explicitly in
  configuration.
- **Board position**: An issue's place in the dispatch column, top first. Meaningful only
  relative to the other issues of the same repository in the same column.
- **Ordering resolution**: What the system decided for one repository — the board and column
  in effect, how each was decided, when the board was last read, and, when nothing is in
  effect, why.
- **Off-column hold**: The state of an eligible item that its repository's board places
  somewhere other than the dispatch column. Carries the column the item is actually in, and
  clears when the item is moved or the board stops governing the repository.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a repository with a resolved board, the ready items holding a position in
  the dispatch column appear in the queue in that column's top-to-bottom order, ahead of that
  repository's off-board items, in every pass where the board was read successfully.
- **SC-002**: Reordering a board changes the dispatch order within one poll interval — one
  minute at the default setting — with no restart and no manual step.
- **SC-003**: Repositories with no board behave identically to today; every existing ordering
  test passes unchanged.
- **SC-004**: The queue page and the status command make no network request while rendering,
  and the queue page's render time is indistinguishable from today's.
- **SC-005**: For every onboarded repository, the author can determine from a single terminal
  command whether a board governs its order and, if not, exactly why — without reading the
  log.
- **SC-006**: The dispatcher, the web queue page, and the status command present the same
  order for the same state, in all board and non-board configurations.
- **SC-007**: Every discovery attempt, board read, and fallback is reconstructable from the
  log alone: which repository, which project and column, and what the outcome was.
- **SC-008**: A board that has become unreadable never causes a repository to stop
  dispatching, and never changes its order or its holds without that change being visible.
- **SC-009**: An item held for sitting outside the dispatch column dispatches on the first
  pass after it is moved into that column, with no restart and no manual step.
- **SC-010**: Switching project ordering off for a repository restores its present-day
  behavior exactly — same order, no holds — verifiable by the existing ordering tests.

## Assumptions

- "GitHub Projects" means the current generation of GitHub projects — the boards behind
  `github.com/users/<user>/projects/<n>` and the organisation equivalent. Classic projects
  are out of scope.
- Discovery works from the projects associated with a repository. A user- or organisation-
  owned project that is not associated with the repository cannot be discovered and must be
  configured explicitly. **Verified during planning**: project #3 is a user-owned board and
  *is* discoverable through `jantman/robot-army`, because linkage is by repository rather
  than by owner.
- Recognised column names are matched case- and space-insensitively, so `Ready`, `Todo`, and
  `To do` are all recognised. Anything else requires configuration.
- Cross-repository ordering is out of scope by the issue's own terms. Whichever global order
  mode is configured continues to decide how repositories interleave.
- Board order applies to dry-run and simulated items on the same terms as live ones, since
  they already occupy queue positions.
- Reading projects needs a token permission that reading issues does not. **Verified during
  planning**: it must be a *classic* personal access token with `read:project`. GitHub has no
  account-level Projects permission for fine-grained tokens, so a fine-grained token cannot
  read a user-owned board at all. The system's job is to say which kind of token is in use and
  what is missing, not to work around it.
- A single board is enough per repository. Multiple boards feeding one repository, per-column
  weighting, and ordering across boards are not needed.
- The board carries two distinct signals and this feature honours both. A card the author
  placed in another column is a deliberate "not yet" and is held. An issue that is not on the
  board at all is no signal either way, so it still dispatches — after everything the board
  has actually ranked.
- Board ordering applies wherever a board resolves cleanly, without being switched on. That
  means a repository that already has a linked project changes behavior the first time this
  ships, including gaining holds for cards parked in other columns; the per-repository
  off switch exists for exactly that case.
- One new hold reason is expected. Where it sits in the existing precedence is a design
  decision for planning, not something this spec fixes.

## Out of Scope

- Writing to project boards: moving cards, setting status fields, or reflecting session state
  back onto the board. This feature reads.
- Adding issues to a board, or creating boards.
- Using project fields other than the column an item sits in — no iteration, size, estimate,
  or custom-field-driven priority.
- Ordering repositories against one another by anything derived from a board.
- Replacing or retiring the existing `oldest-first` and `repo-priority` modes.
- Any change to Trello intake beyond what it inherits by filing ordinary GitHub issues.
