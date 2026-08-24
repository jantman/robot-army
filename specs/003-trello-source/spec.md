# Feature Specification: Trello Source

**Feature Branch**: `003-trello-source` *(not created — no `before_specify` git hook is configured in this project)*

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "milestone 3 in the roadmap"

**Scope note**: This is milestone 003 of the roadmap in [`docs/roadmap.md`](../../docs/roadmap.md),
corresponding to M3 in the planning document (§4 Trello, §7's `needs_info` state, and §11 loop
prevention). It adds a **second way work arrives** — a card on the author's private board — and the
board-side lifecycle that follows it. It does **not** add a second way work is *dispatched*: a card
becomes a GitHub issue, and that issue reaches a session only when the author labels it by hand,
exactly as in milestone 001. That gap is the safety property of the whole design, not an
inconvenience to be optimised away later.

This is also the first time the source seam built in 001 carries something other than GitHub. Where
Trello does not fit that seam, the seam is wrong and moves; the fix is never a GitHub-shaped special
case with a Trello branch inside it.

Per-repo concurrency caps, priority modes, out-of-band session accounting, worktree cleanup policy,
and notifications remain out of scope and belong to milestone 004.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Capture a task anywhere, find an issue waiting (Priority: P1)

The author is away from the machine and thinks of something that needs doing. They add a card to
their board — a title, a sentence or two, and the repository it concerns — and tag it for the robot.
Without any further action, a GitHub issue appears in that repository containing what the card said,
and the card carries a comment linking to the issue. The author reads the issue later, decides
whether it is worth doing, and labels it for dispatch or does not.

**Why this priority**: This is the entire ergonomic point of the milestone. The board is where the
author already captures work; today that capture goes nowhere. With only this story shipped, the
author gets the capture-to-issue path and nothing else about the system's behaviour has changed —
issues created this way are ordinary unlabelled issues that the existing daemon correctly ignores.

**Independent Test**: Add one card naming a repository, wait one poll interval, and confirm a
matching issue exists in that repository, that it is not labelled for dispatch, that the card links
to it, and that no session was started.

**Acceptance Scenarios**:

1. **Given** a card carrying the configured tag and naming exactly one known repository, **When** the
   board is polled, **Then** an issue is created in that repository whose title and body carry the
   card's title and description and a link back to the card.
2. **Given** an issue created from a card, **When** it is created, **Then** it does **not** carry the
   dispatch label, and no session is started for it by any path other than the author labelling it.
3. **Given** an issue created from a card, **When** creation succeeds, **Then** a comment is added to
   the card containing the issue's URL in a form the system can recognise again later.
4. **Given** a card that the author later labels the created issue for dispatch, **When** the issue is
   next polled, **Then** it is dispatched as ordinary work and is **not** treated as a second,
   separate piece of work from the card it came from.
5. **Given** a card without the configured tag, **When** the board is polled, **Then** nothing is
   created and nothing is written to the board.
6. **Given** the Trello source is not configured, **When** the daemon runs, **Then** it behaves
   exactly as it did in milestone 002 and makes no request to any board.

---

### User Story 2 - A card that doesn't say enough is held, not guessed at (Priority: P2)

The author writes a card that does not say which repository it is about, or names two. The system
does not guess and does not create an issue anywhere. It marks the card as awaiting clarification,
says so on the card once, and surfaces it where the author will see it. When the author edits the
card to say which repository they meant, the system notices the edit on its own and tries again —
without the author remembering to press anything.

**Why this priority**: The planning document is explicit that the author will forget to press the
re-scan button, and an issue filed against the wrong repository is worse than no issue at all. This
story is what makes the automatic path safe to leave running unattended; without it, the first
ambiguous card either creates junk or disappears silently.

**Independent Test**: Add a card with no repository reference, confirm no issue is created anywhere
and the card is surfaced as awaiting clarification; then edit the card to name a repository and
confirm an issue appears within one poll interval with no further human action.

**Acceptance Scenarios**:

1. **Given** a tagged card with no identifiable repository, **When** it is evaluated, **Then** no
   issue is created, the item is recorded as awaiting clarification with the specific reason, and a
   single comment stating what is missing is added to the card.
2. **Given** a tagged card naming more than one distinct repository, **When** it is evaluated,
   **Then** it is treated the same way as one naming none, and the reason names the ambiguity.
3. **Given** a card awaiting clarification, **When** the author edits it, **Then** the system detects
   the change and re-evaluates it without any human action beyond the edit.
4. **Given** a card awaiting clarification, **When** the author asks for a re-scan explicitly from
   either the terminal or the web interface, **Then** it is re-evaluated immediately.
5. **Given** a card that is re-evaluated and still cannot be resolved, **Then** it remains awaiting
   clarification and **no** further comment is added to the card unless the reason itself changed.
6. **Given** a card awaiting clarification, **When** it is re-evaluated successfully, **Then** it
   follows the ordinary creation path exactly as if it had been resolvable when first seen.
7. **Given** an item awaiting clarification, **When** dispatch decisions are made, **Then** it is
   never eligible, and it is visible as awaiting clarification in both the terminal and web listings
   with its reason.
8. **Given** a card that loses its tag, is archived, or is deleted while awaiting clarification,
   **When** it is next polled, **Then** it stops being surfaced as outstanding, the reason is
   recorded, and nothing is created.

---

### User Story 3 - The board tells the truth about what is happening (Priority: P3)

The author looks at their board, not at a terminal or a web page, and can tell what the robot is
doing. A card whose work is running has moved to the in-progress list. A card whose issue has been
closed has moved to the done list. A card whose work was abandoned or failed is back where it was,
with a comment saying what happened, rather than sitting in in-progress claiming to be busy.

**Why this priority**: The board is a status surface the author already reads. A board that lies is
worse than a board that says nothing, which is why this ranks above the invariant work below but
below the two stories that create value on their own. Everything here is a write to a remote system
the author can see, so it is also the part with the most potential to be annoying if it is wrong.

**Independent Test**: Take one card through card → issue → label → dispatch → close, and confirm the
card is in the in-progress list exactly while a session is running and in the done list once the
issue is closed. Then take a second card to abandonment and confirm it returns to its original list
with an explanatory comment.

**Acceptance Scenarios**:

1. **Given** a card-derived work item, **When** a session for it is confirmed running, **Then** the
   card is moved to the configured in-progress list and the list it came from is recorded.
2. **Given** a card-derived work item, **When** its issue is observed closed, **Then** the card is
   moved to the configured done list and a comment records the outcome.
3. **Given** a card-derived work item that is abandoned or has failed terminally, **When** that
   happens, **Then** the card does not remain in the in-progress list: it is returned to the list it
   was moved from, with a comment naming the reason.
4. **Given** a card the author has moved by hand since the system last placed it, **When** the system
   would move it, **Then** it does **not** move it, and instead comments with what it would have
   done, so a human decision is never silently overwritten.
5. **Given** a configured list that does not exist on the board, **When** the system starts, **Then**
   it reports this as a configuration failure rather than discovering it mid-lifecycle.
6. **Given** any card movement or comment, **When** it happens, **Then** it is recorded in the audit
   log before it is attempted and again with its outcome.

---

### User Story 4 - One card, one issue, no matter what happened (Priority: P4)

The author never finds two issues for the same card, two cards for the same issue, or a duplicate
created because the daemon was killed at the wrong moment or because the state database was lost and
rebuilt. Polling the same board a hundred times produces the same single issue it produced the first
time.

**Why this priority**: This is the invariant the planning document singles out, and the failure it
prevents is both silent and cumulative — nothing breaks visibly, the issue tracker just fills up. It
ranks last only because the three stories above must exist before there is anything to duplicate.
The planning document also warns that dry-run cannot demonstrate this property, because a simulated
run writes no mapping; it has to be proven by tests and by a real run against a throwaway board.

**Independent Test**: Poll the same tagged card repeatedly, including across a daemon restart, and
confirm exactly one issue and one linking comment exist. Then kill the daemon between each pair of
steps in the creation sequence in turn, restart, and confirm no step produces a duplicate.

**Acceptance Scenarios**:

1. **Given** a card that already has a recorded issue, **When** the board is polled again, **Then**
   no issue is created, no comment is added, and the existing relationship is used.
2. **Given** an issue created from a card, **When** the GitHub source polls and finds that issue,
   **Then** it is recognised as the same piece of work rather than becoming a second one.
3. **Given** the process is killed after the issue is created but before the relationship is
   recorded, **When** the daemon restarts and polls, **Then** it recovers the existing issue rather
   than creating a second one.
4. **Given** the state database has been lost and recreated, **When** a previously processed card is
   polled, **Then** the linking comment on the card is used to restore the relationship, and no
   second issue is created.
5. **Given** normal operation with the relationship recorded, **When** creation is considered,
   **Then** the recorded relationship is what is consulted — card comments are read only when no
   record exists.
6. **Given** any operation that would create an issue or a card, **When** it runs, **Then** it checks
   the recorded relationships first, so that a card producing an issue that produces a card is
   structurally impossible rather than merely unlikely.

---

### Edge Cases

- **The card names a repository the system has never heard of.** No issue is created anywhere; it is
  held for clarification. Filing an issue against a guess is unrecoverable in the way holding is not.
- **The card names a known repository that has not been onboarded.** The issue is created — creating
  an issue is not dispatching — and the ordinary onboarding block applies later, visibly, at dispatch.
- **The card description contains text pasted from a log, an email, or a web page.** It is carried
  into the issue as quoted content and nothing in it is interpreted as an instruction to the system.
  The human gate is what protects the session prompt, and it is not bypassed for any card.
- **The board turns out not to be private, or has gained a member.** Board access *is* the
  authorisation model for this path. Ingestion must stop and say so rather than continue on an
  assumption that has quietly become false.
- **The board cannot be reached.** "No cards matched" and "I could not ask" are different facts and
  must never be conflated; repeated failure must become visible rather than looking like an idle board.
- **The card is edited while its issue is being created.** The outcome must be one issue reflecting
  one evaluation, not a partial mixture of two.
- **The card is deleted, archived, or untagged after its issue exists.** The issue and any work in
  flight are unaffected; the board simply stops describing it.
- **The issue created from a card is deleted or transferred on GitHub.** The relationship becomes
  unresolvable; this is recorded as an anomaly rather than triggering a fresh creation.
- **Two cards name the same repository, or the same work.** Both produce issues; the invariant is one
  issue per *card*, not one issue per topic, and the system does not attempt to detect duplicates of
  intent.
- **The system is running below the live effect level.** Reads are real, every write is simulated and
  logged with its full arguments, and no card and no issue is touched.
- **A simulated run and a live run see the same card.** A simulated evaluation must never be able to
  suppress a later real creation, and its rows must remain distinguishable as simulated.
- **The board's rate limit is hit.** Polling backs off within bounds rather than retrying freely, and
  the health signal reflects that the source is degraded.
- **The daemon is killed midway through the card lifecycle** — between moving a card and recording
  that it moved. The next pass must be able to tell what actually happened on the board rather than
  assuming.
- **The configured tag is renamed on the board.** Cards stop matching; this looks identical to an
  empty board and so the configuration must be validated against the board at startup.

## Requirements *(mandatory)*

### Source Configuration & Trust Boundary

- **FR-001**: The Trello source MUST be inert unless explicitly configured. An installation that does
  not configure it MUST make no request to any board and MUST behave exactly as it did in the previous
  milestone.
- **FR-002**: Configuration MUST name the board to poll, the card tag that marks work, and the list
  names used for the in-progress and done lifecycle stages, in the same human-inspectable local
  configuration file the rest of the system uses.
- **FR-003**: Board credentials MUST be read from an environment variable or a git-ignored local file,
  MUST NOT be committed, and MUST NOT appear in any log record, terminal output, or served response.
- **FR-004**: The system MUST verify at startup that the configured board is private and that it has
  no members other than the author, and MUST refuse to ingest cards and raise an anomaly if either is
  false. Board access is the authorisation model for this path; there is no per-card author check,
  and a board that has been shared has silently changed the trust boundary.
- **FR-005**: The system MUST verify at startup that the configured board, tag, and lifecycle lists
  exist on the board, and MUST fail loudly rather than treating a renamed tag as an empty board.
- **FR-006**: Creating issues from cards is an outward-facing action that mutates a remote system.
  It MUST be reachable only through the explicit configuration of FR-001 and MUST be logged before
  execution, per the constitution's Operating Constraints.

### Card Discovery & Evaluation

- **FR-007**: The system MUST poll the configured board on a configurable interval for cards carrying
  the configured tag, independently of the GitHub poll interval.
- **FR-008**: Every board request MUST set explicit timeouts and MUST bound its retries with backoff,
  honouring any rate-limit signal the board returns.
- **FR-009**: A failure to reach the board MUST NOT be reported as an empty result. It MUST be
  recorded with its cause, MUST become an anomaly after repeated consecutive failures, and MUST be
  reflected in the daemon's liveness signal.
- **FR-010**: The system MUST determine each tagged card's target repository from the card's own
  content, accepting a repository URL, an owner-and-name reference, or a local checkout path that
  corresponds to a repository the system knows about.
- **FR-011**: A card MUST be considered resolvable only if it identifies exactly one distinct
  repository the system knows about. Zero references and two conflicting references MUST both be
  treated as unresolvable.
- **FR-012**: The system MUST record, for every tagged card it evaluated and did not act on, which
  condition failed, in terms specific enough for the author to fix the card.
- **FR-013**: Text taken from a card MUST be treated as data at every point it is handled. The system
  MUST NOT interpret card content as configuration, as a command, or as a directive to itself.

### Issue Creation

- **FR-014**: For each resolvable card without an existing recorded issue, the system MUST create one
  issue in the identified repository carrying the card's title, the card's description as quoted
  content, and a link back to the card.
- **FR-015**: The system MUST NOT apply the dispatch label to any issue it creates, and MUST NOT
  place a card-derived item into a dispatchable state on its own. The transition from issue to
  dispatch MUST require the author's manual label, exactly as for issues the author writes.
- **FR-016**: On successful creation, the system MUST add a comment to the card containing the issue's
  URL in a stable form the system can recognise on a later pass.
- **FR-017**: The card, the issue it produced, and any work item for that issue MUST be traceable to
  one another in both directions, and a work item whose issue came from a card MUST be identifiable as
  such wherever work items are listed.
- **FR-018**: An issue created from a card, once labelled by the author, MUST produce exactly one work
  item, by the ordinary path any issue takes. Board ingestion MUST NOT create a work item of its own,
  and MUST NOT require the issue path to recognise a special case.
- **FR-019**: Failure to create the issue MUST leave the item in a state that will be retried on a
  later pass, MUST record the cause, and MUST NOT leave a comment on the card claiming an issue
  exists.

### Awaiting Clarification

- **FR-020**: Every tagged card MUST be tracked from first sighting with a lifecycle of its own, one
  state of which is `needs_info`: tagged, but not resolvable, and awaiting human clarification. That
  lifecycle MUST be governed by the same single, enumerable set of legal transitions every other state
  machine in the system uses.
- **FR-020a**: A card MUST NOT become a work item before an issue exists for it. A work item is
  dispatchable work; a card awaiting clarification is not, has no repository and no issue, and MUST
  NOT be representable as one. This deliberately places `needs_info` on the card rather than on the
  work item, which is where the planning document's §7 put it — see the Assumptions section.
- **FR-021**: An item in `needs_info` MUST never be eligible for dispatch and MUST never cause an
  issue to be created.
- **FR-022**: On first entering `needs_info`, the system MUST add exactly one comment to the card
  stating what is missing. Subsequent unsuccessful re-evaluations MUST NOT add further comments unless
  the reason itself has changed.
- **FR-023**: The system MUST detect that a card awaiting clarification has been edited, by observing
  the card's own last-activity indicator, and MUST re-evaluate it automatically without human action.
- **FR-024**: The system MUST provide an explicit re-scan of a card awaiting clarification, available
  both as a terminal command and in the web interface.
- **FR-025**: A card awaiting clarification that loses its tag, is archived, or is deleted MUST stop
  being surfaced as outstanding, and the reason MUST be recorded.
- **FR-026**: Items in `needs_info` MUST be listed, with their reason, in both the terminal and web
  interfaces, alongside the other categories of work that cannot proceed.

### Card Lifecycle

- **FR-027**: When a session for a card-derived work item is confirmed running, the system MUST move
  the card to the configured in-progress list and MUST record the list it was moved from.
- **FR-028**: When a card-derived work item's issue is observed closed, the system MUST move the card
  to the configured done list and comment with the outcome.
- **FR-029**: When a card-derived work item is abandoned or fails after its card was moved to the
  in-progress list, the system MUST return the card to the list it was moved from and comment with the
  reason. A card MUST NOT be left claiming to be in progress when nothing is.
- **FR-030**: The system MUST NOT move a card that is not in the list where the system last placed it.
  In that case it MUST comment with what it would have done instead, so that a manual move by the
  author is never silently overwritten.
- **FR-031**: Every card comment and card movement MUST be recorded in the audit log before it is
  attempted and again with its outcome, naming the card, the lists involved, and the result.

### Loop Prevention & Idempotency

- **FR-032**: The system MUST maintain a durable record of the relationship between cards and issues,
  and that record MUST be the source of truth in normal operation. It MUST admit at most one issue per
  card and at most one card per issue.
- **FR-033**: Every operation that would create an issue or a card MUST consult the recorded
  relationships first and MUST do nothing if a relationship already exists.
- **FR-034**: When no relationship is recorded, the system MUST look for its own linking comment on
  the card before creating anything, and MUST restore the relationship from it if found. This path
  MUST be used only as recovery — it MUST NOT be consulted when a record already exists.
- **FR-035**: Interruption at any point in the creation sequence MUST NOT produce a duplicate issue or
  a duplicate comment. The system MUST record its intent to create before creating, and MUST resolve
  any unfinished intent on the next pass by determining what actually happened rather than by retrying
  blindly.
- **FR-036**: This milestone MUST NOT create Trello cards from GitHub issues. The invariant guard of
  FR-033 MUST nonetheless be implemented and tested for that direction, because the whole point is
  that the loop is structurally impossible rather than absent by omission.
- **FR-037**: An issue that has been deleted, transferred, or otherwise made unresolvable MUST be
  recorded as an anomaly and MUST NOT cause a fresh creation for its card.

### Effect Levels & Dry Run

- **FR-038**: Board reads MUST be real at every effect level, so that a dry run genuinely evaluates
  the cards that exist.
- **FR-039**: Every board write — comments and card movements — and every issue creation MUST be
  simulated at every effect level below the live one, following the same boundary-level enforcement
  the existing sources use rather than checks scattered through the calling code.
- **FR-040**: Each simulated write MUST emit an audit record naming the call and its full arguments
  and MUST return a structurally valid result, so the simulated path cannot diverge from the real one.
- **FR-041**: Work items and relationships produced by a simulated run MUST be marked as simulated,
  MUST be excluded from listings by default exactly as existing simulated rows are, and MUST NOT
  prevent a later live run from performing the real creation.
- **FR-042**: The loop-prevention invariant MUST be verified by tests that exercise the interruption
  and duplicate paths directly, and by one real run against a disposable board. A dry run MUST NOT be
  accepted as evidence for FR-032 through FR-035, because a simulated run writes no relationship
  record and so cannot exercise them.

### Boundary Integrity

- **FR-043**: The board MUST be reached through the same kind of seam the existing source uses, with
  reads and writes separated so that the effect-level rules of FR-038 and FR-039 are structural.
- **FR-044**: Where the existing source seam cannot express what the board needs, the seam MUST be
  changed rather than bypassed. No shared path may branch on which external system it is talking to; a
  reviewer MUST be able to find every source-specific behaviour behind the seam and nowhere else.
- **FR-045**: Adding this source MUST NOT change the observable behaviour of the GitHub path for
  issues the author wrote themselves.

### Accountability & Observability

- **FR-046**: Every state change, every remote write, and every rejection MUST be recorded such that
  the fate of any card can be reconstructed from the log alone, without re-running anything.
- **FR-047**: The author MUST be able to determine, from the terminal alone, why any given tagged card
  did not produce an issue.
- **FR-048**: A card-derived work item MUST show its card link wherever its issue link is shown, in
  both the terminal and web interfaces.
- **FR-049**: Every capability this milestone adds MUST be reachable from the terminal, and the web
  interface MUST NOT be a prerequisite for any of it.

### Key Entities

- **Card**: A tagged item on the author's board, tracked from first sighting with a lifecycle of its
  own. Carries a title, a description, the list it currently sits in, the list it was in before the
  system moved it, and a last-activity indicator used to detect edits. Identified by the board's own
  identifier, which is stable across edits and moves.
- **Card–Issue Relationship**: The durable, authoritative link between one card and one issue. Unique
  in both directions. Consulted before every create; reconstructible from the card's linking comment
  if lost.
- **Creation Intent**: The record that a creation was about to be attempted for a card, written before
  the attempt and resolved after it, so that an interruption mid-sequence is detectable rather than
  ambiguous.
- **Work Item**: As defined in milestone 001 and **unchanged by this milestone**. It is created only
  when an issue the author has labelled is polled, whatever produced that issue. A work item whose
  issue came from a card is traceable to it through the relationship above, not through a field of its
  own.
- **Repository, Session, Audit Record, Anomaly**: As defined in milestone 001 and unchanged here.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A tagged card naming a known repository produces exactly one issue, and one linking
  comment on the card, within one board poll interval of being created.
- **SC-002**: Across one hundred consecutive polls of the same unchanged card, and across at least one
  restart of the system, the number of issues created for it remains exactly one.
- **SC-003**: Interrupting the system at each distinct step of the card-to-issue sequence, once per
  step, produces zero duplicate issues and zero duplicate card comments after restart.
- **SC-004**: With the relationship records discarded entirely, re-polling a previously processed card
  produces zero new issues and restores the relationship from the board.
- **SC-005**: Every tagged card that cannot be resolved to exactly one repository produces zero issues
  in any repository, and is visible as awaiting clarification with a reason the author can act on
  without consulting the code.
- **SC-006**: Editing a card that is awaiting clarification results in it being re-evaluated within
  one board poll interval, with no human action other than the edit.
- **SC-007**: A card awaiting clarification for an extended period accumulates exactly one explanatory
  comment, not one per poll.
- **SC-008**: At any moment, no card sits in the in-progress list whose work item is not running, and
  no card-derived work item whose issue is closed sits outside the done list, except where the author
  has moved the card by hand.
- **SC-009**: Below the live effect level, a full poll-and-evaluate cycle over a board of tagged cards
  results in zero writes observable on the board or in any repository, while the log shows every write
  that would have been made with its full arguments.
- **SC-010**: The fate of any tagged card — created, held for clarification, rejected, or failed — can
  be determined from the audit log alone, without re-running anything and without reading the board.
- **SC-011**: No credential appears in any log record, terminal output, or served page, verified
  across a run that includes at least one board failure and one authentication failure.

## Assumptions

- **The board is private and the author is its only member.** The planning document states this as the
  security boundary for the Trello path: there is no per-card author check, so board access *is*
  authorisation. FR-004 turns the assumption into a checked precondition rather than leaving it as a
  comment, so that sharing the board stops ingestion instead of silently widening who can queue work.
- **One board.** Multiple boards, and per-board configuration, are not needed and are not built.
- **The tag and the lifecycle list names are configuration, not constants**, with the planning
  document's `AI-task`, in-progress and done conventions as defaults.
- **The card is a separate thing from the work item, and `needs_info` belongs to the card.** The
  planning document's §7 lists `needs_info` among the *work item* states, and milestone 001 deferred it
  here on that basis. Design found that it cannot go there: a work item is required to name an
  onboarded repository and an issue number, and a card awaiting clarification has neither — it may name
  a repository nobody has heard of, or none at all. Tracking cards separately also keeps the human gate
  structural rather than conventional, because no board activity can produce a dispatchable row at all.
  One card still maps to at most one issue, and one issue to at most one card; that mapping is what
  carries the invariant, not a shared row.
- **Card content flows into the issue verbatim as quoted text.** Summarising or rewriting it would put
  a model between the author's words and the issue the author later reviews, which defeats the point of
  reviewing it.
- **Only cards awaiting clarification are re-evaluated on edit.** Once a card has produced an issue,
  later edits to the card do not modify the issue. The issue is the artefact under review from that
  point; keeping the two in sync is a bidirectional-sync problem this project has no need for.
- **The lifecycle is one-directional: the issue is authoritative and the card follows it.** Moving a
  card to the done list by hand does not close its issue.
- **Onboarding status is a dispatch-time concern, not an ingestion-time one.** A card naming a known
  but not-yet-onboarded repository still gets an issue; the existing onboarding block stops it later,
  visibly, which is where the author can act on it.
- **Recovery from lost relationship records happens per card, on the next poll, through FR-034.** No
  separate bulk rebuild command is built, because the ordinary path already heals each card the first
  time it is seen again.
- **The board poll shares the daemon process** with the existing poll and reconciliation loops, with
  its own interval and its own failure accounting.

## Out of Scope

- Creating Trello cards from GitHub issues, in either the automatic or the manual direction.
- Editing or closing issues in response to card changes after the issue exists.
- Any additional path from card to dispatch that does not pass through the author's manual label.
- Checklists, attachments, due dates, members, custom fields, and every other board feature beyond
  tag, title, description, list, and comment.
- More than one board, and per-board or per-list configuration overrides.
- Per-repo concurrency caps, priority and ordering modes, out-of-band session accounting, worktree
  cleanup policy, and notifications — all milestone 004.
