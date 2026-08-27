# Feature Specification: Trello Column Ignore List

**Feature Branch**: `006-trello-ignore-lists`

**Created**: 2026-08-27

**Status**: Draft

**Input**: User description: "issue #3 on this repo" — *"We need a configurable way to ignore Trello
cards that are in one of a configured set of columns (lists)."*

**Scope note**: This is the resolution of
[issue #3](https://github.com/jantman/robot-army/issues/3). It claims the slot
[`docs/roadmap.md`](../../docs/roadmap.md) currently reserves for 006, "whatever survives contact
with reality". That parking lot moves to 007, for the same reason it moved once already at 005: this
milestone has a shape and the parking lot does not. It is also, literally, a thing that surfaced from
*using* milestone 003 rather than from the planning document.

**This milestone adds no new capability and no new surface.** It adds one place where the author can
say "not these" and one thing the daemon must not do. It changes **which tagged cards are intake** —
nothing about how an intake card becomes an issue, how an issue becomes a session, or what the board
is told afterwards.

### The problem, stated plainly

Milestone 003's intake rule is: *a card carrying the configured tag is intake*. Position on the board
is not part of it. The tag is a property of the card and the author sets it once; the column is a
property of *where the card currently is* and the author changes it constantly, because moving cards
between columns is what a board is for.

Those two facts collide the first time a tagged card ends up somewhere it is not meant to be acted
on. Today the author has exactly one way to stop a tagged card from being filed as an issue: remove
the tag. That is a destructive, one-way answer to a question that is usually about *timing* —
"not yet", "not this quarter", "blocked on something else" — and it loses the information that the
card was ever meant for the robot at all.

An ignore list closes the gap without touching the tag. Intake becomes: *a card carrying the
configured tag, in a column the author has not excluded.*

### Why the column, and not something else

Two alternatives were considered and rejected before this shape was chosen.

**A second tag ("robot-hold").** Rejected. It puts the state in the same place as the thing it
qualifies, so the author has to remember to keep two tags consistent on one card, and a card with
both tags has no defined meaning. It also adds a second board-side vocabulary word to a spec whose
003 predecessor went to some trouble to keep *tag* and *label* distinct precisely because two words
for adjacent things is how this project confuses itself.

**A per-card marker in the description.** Rejected harder. It means parsing card text for control
instructions, and 003's edge cases already record the opposite rule: card text is carried as quoted
content and *nothing in it is interpreted as an instruction to the system*.

The column is already the author's mental model of "what is happening with this card". The daemon
already reads which column every card is in, already records the column a card came from so it can
put it back, and already refuses to start ingesting if a configured column is missing from the board.
Excluding by column adds a value to a section, not a mechanism.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Park a card without untagging it (Priority: P1)

The author has a column on the board for work that is real but not now — an icebox, a someday pile, a
blocked stack. They configure that column as ignored. They then drag a tagged card into it. No issue
is filed. The card keeps its tag, keeps its description, and keeps its place in the author's head as
something the robot will eventually pick up. Nothing is written to the card, so the board does not
acquire a comment explaining a decision the author made deliberately and already knows about.

**Why this priority**: This is the entire feature. Everything below is either the other half of it
(getting the card back out) or the accountability the constitution requires around it. With only this
story shipped the author has a working parking column, and every existing behaviour is unchanged for
anyone who configures no ignored columns at all.

**Independent Test**: Configure one ignored column, put a tagged card in it, wait one poll interval,
and confirm no issue exists in any repository, no comment was added to the card, and the card was not
moved.

**Acceptance Scenarios**:

1. **Given** a tagged card that names exactly one known repository, sitting in an ignored column,
   **When** the board is polled, **Then** no issue is created, no comment is added to the card, and
   the card is not moved.
2. **Given** a tagged card in an ignored column that names **no** repository, **When** the board is
   polled, **Then** it is not recorded as awaiting clarification and no comment asking for
   clarification is added — being ignored is decided before resolvability is ever asked about.
3. **Given** a tagged card in a column that is **not** ignored, **When** the board is polled, **Then**
   it is treated exactly as milestone 003 treats it today.
4. **Given** no ignored columns are configured, **When** the board is polled, **Then** every
   observable behaviour is identical to milestone 003's, including which cards are tracked and what is
   written to the board.
5. **Given** an ignored column, **When** the board is polled, **Then** the number of tagged cards
   skipped for sitting in an ignored column is recorded in the poll's audit record, distinctly from
   the number of tagged cards found.

---

### User Story 2 - Un-parking works, and works on its own (Priority: P2)

The author drags a card back out of the ignored column into an ordinary one. Within one poll interval
an issue appears, exactly as if the card had been tagged for the first time that moment. The author
does not have to remove and re-add the tag, does not have to run a re-scan, and does not have to
know that the card was ever seen and skipped.

**Why this priority**: A parking space you cannot drive out of is a scrapyard. This story is what
separates "ignored" from "dropped", and getting it wrong would produce the worst kind of bug this
project can ship — one where the author does something reasonable, sees nothing happen, and has no
reason to suspect the system rather than themselves.

**Independent Test**: Put a tagged card in an ignored column, confirm nothing is created, move it to
an ordinary column, wait one poll interval, and confirm an issue appears with no further action.

**Acceptance Scenarios**:

1. **Given** a tagged card that has been polled one or more times while in an ignored column, **When**
   it is moved to a column that is not ignored, **Then** it is evaluated on the next poll and follows
   the ordinary creation path.
2. **Given** a card that was **already tracked and not yet linked** — awaiting clarification, or seen
   but not yet settled — **When** it is moved into an ignored column, **Then** it stops being
   evaluated and stops being surfaced as outstanding, and this is **not** recorded as the card having
   left the board.
3. **Given** such a card, **When** it is moved back out of the ignored column, **Then** it resumes
   from the state it was in, is evaluated again, and is once more surfaced as outstanding if it is
   still awaiting clarification.
4. **Given** a card awaiting clarification that is parked and then un-parked with no edit in between,
   **When** it is re-evaluated and reaches the same unresolved reason as before, **Then** no second
   clarification comment is added to the card.
5. **Given** an ignored column is removed from the configuration, **When** the board is next polled,
   **Then** the tagged cards in that column become intake with no further action, exactly as if they
   had been moved out of it.

---

### User Story 3 - Ignoring a column never abandons work already in flight (Priority: P3)

The author configures a column as ignored, or drags a card into an ignored one, while that card's
issue already exists — possibly with a session running against it. Nothing about the work changes.
The issue is not closed, the session is not stopped, the mapping is not forgotten, and the card's
remaining lifecycle moves still happen. The ignore list decides what becomes work. It does not
decide what stops being work.

**Why this priority**: It ranks below the two stories that deliver the feature because it is mostly a
statement of what must *not* happen, but it is the story with the largest blast radius if it is wrong.
It also settles the question the configuration otherwise raises immediately: whether the author may
list the in-progress or done columns as ignored. They may, and it costs nothing, because by the time
the daemon puts a card in either of those the card is already linked and the ignore list no longer
applies to it.

**Independent Test**: Take a card through to a linked issue with a running session, then add its
current column to the ignore list, and confirm the session, the issue, the mapping, and the
subsequent board moves are all unaffected.

**Acceptance Scenarios**:

1. **Given** a card whose issue already exists and is recorded, **When** the card's column is added to
   the ignore list or the card is moved into an ignored column, **Then** the mapping is retained, the
   issue is untouched, and any session for it is unaffected.
2. **Given** such a card, **When** its work reaches a point that would move the card — running,
   closed, or abandoned — **Then** the move happens as milestone 003 specifies, into or out of an
   ignored column if that is where it leads.
3. **Given** the configured in-progress or done column is also listed as ignored, **When** the
   configuration is loaded, **Then** it is accepted without error, because the two settings act on
   disjoint sets of cards.
4. **Given** a card that has been recorded as having left the board, **When** the ignore list changes,
   **Then** nothing revives it — the ignore list is not a route back from a card that lost its tag or
   was archived.

---

### User Story 4 - The configuration is checkable before the daemon runs (Priority: P4)

The author renames a column on the board, or mistypes one in the configuration file. They find out by
running `robot-army doctor`, which names every configured ignored column, says whether it exists on
the board, and lists the columns that do exist when one does not. They do not find out by noticing,
weeks later, that the icebox has been quietly filing issues.

**Why this priority**: It is the smallest story and it delivers no capability, but it closes the
failure mode that makes the whole feature untrustworthy. Every other configuration failure in this
system is loud. A silent one here does not break anything visibly — it just widens intake back to
where it was, which is indistinguishable from the feature working.

**Independent Test**: Configure an ignored column that does not exist on the board and confirm
`doctor` reports it by name, reports which columns do exist, and that ingestion is refused.

**Acceptance Scenarios**:

1. **Given** a configured ignored column that exists on the board, **When** `doctor` runs, **Then** it
   reports the check as passing and names the column.
2. **Given** a configured ignored column that does **not** exist on the board, **When** `doctor` runs
   or the daemon starts, **Then** the check fails, the failure names the missing column and lists the
   columns the board actually has, and board **ingestion** is refused — dispatch of issues the author
   wrote themselves continues.
3. **Given** a configured column name that differs from the board's only in letter case, **When**
   the checks run, **Then** it fails as missing and the failure lists the board's actual columns,
   rather than silently excluding nothing.
4. **Given** the same column name listed twice, **When** the configuration is loaded, **Then** it is
   accepted and means the same as listing it once.
5. **Given** no ignored columns are configured, **When** `doctor` runs, **Then** it reports no
   additional failing check and the board section behaves as it did in milestone 003.
6. **Given** any change to which cards are intake because of this setting, **When** the daemon runs,
   **Then** the audit record for the poll cycle carries enough to reconstruct the decision — the
   ignored column names in force and how many tagged cards they excluded.

---

### Edge Cases

- **A tagged card sits in an ignored column and the author expects an issue.** There is no
  notification for this and there must not be one — a per-card message every poll cycle for a
  deliberate configuration is noise, and the author's route to the answer is `doctor` and the poll's
  audit record, both of which name the ignored columns in force.
- **The card is moved into an ignored column while its issue is being created.** The intent to create
  is already recorded, and recovery must still run against it. Being parked mid-creation does not
  cancel the creation; the outcome is one issue, not zero and not two.
- **A column is renamed on the board while the daemon is running.** The startup check passed against
  the old name. Ingestion continues against a stale resolution until the next startup check, and this
  is acceptable for the same reason the tag and lifecycle columns already accept it — the alternative
  is re-resolving names on every poll to defend against an event that has a restart as its remedy.
- **Two columns on the board share a name.** Trello permits it. The behaviour must be defined rather
  than accidental, and the safe direction is to ignore cards in *every* column matching the name,
  because the author's intent was expressed as a name.
- **An ignored column is deleted from the board.** Its cards are gone from it by definition, so they
  are ordinary intake again wherever they went. The configuration now names something that does not
  exist, which is the failing check in User Story 4.
- **The card is archived while in an ignored column.** It was never tracked, so there is nothing to
  record as having left. A previously tracked card parked and then archived leaves the board in the
  ordinary way.
- **Every column on the board is ignored.** Legal, and it means the board is inert while remaining
  reachable and privately configured. It is reported as a count of skipped cards, not as an error —
  "nothing is intake because you excluded everything" and "nothing is tagged" are different facts and
  the poll record must not conflate them.
- **The board is unreachable.** Unchanged from 003: "I could not ask" is not "nothing matched", and it
  is now also not "everything was ignored".

## Requirements *(mandatory)*

### Intake Eligibility

- **FR-001**: The system MUST allow the author to configure a set of board columns whose cards are
  excluded from intake, identified by column name.
- **FR-002**: The configured set MUST default to empty, and an installation that does not set it MUST
  behave exactly as milestone 003 does, including which cards are tracked and what is written to the
  board.
- **FR-003**: A card carrying the configured tag MUST NOT be evaluated for issue creation while it
  sits in a configured ignored column.
- **FR-004**: A card excluded under FR-003 MUST NOT have anything written to it — no comment, no move
  — and MUST NOT cause an issue to be created in any repository.
- **FR-005**: Exclusion MUST be decided before resolvability, so an ignored card that names no
  repository or names two MUST NOT be recorded as awaiting clarification.
- **FR-006**: A card that has never been evaluated because it was ignored MUST NOT be surfaced as
  outstanding work in any terminal or web listing.

### Reversibility

- **FR-007**: A card that leaves an ignored column MUST become intake on the next poll with no
  further human action — no re-tag, no re-scan, no restart.
- **FR-008**: A tracked but unlinked card that enters an ignored column MUST be **parked** — not
  recorded as having left the board — and MUST retain the state and reason it had.
- **FR-009**: A parked card that leaves an ignored column MUST resume evaluation from the state it
  was parked in, and MUST be surfaced as outstanding again if it is still awaiting clarification.
- **FR-010**: Being parked MUST NOT cause a repeated clarification comment: the existing rule that a
  card is commented on only when its reason changes MUST hold across a park-and-unpark cycle.
- **FR-011**: Removing a column from the configured set MUST make its tagged cards intake on the next
  poll, with no further human action.
- **FR-012**: The ignore list MUST NOT revive a card that was recorded as having left the board.

### Work Already In Flight

- **FR-013**: A card that already has a recorded issue MUST be unaffected by the ignore list, in
  either direction: its mapping is retained, its issue is untouched, and any session for it continues.
- **FR-014**: Board lifecycle moves and comments for a card that already has a recorded issue MUST
  happen as milestone 003 specifies, regardless of whether the source or destination column is
  ignored.
- **FR-015**: The system MUST accept a configuration in which the in-progress or done column is also
  listed as ignored, because the ignore list applies only to cards with no recorded issue.

### Validation

- **FR-016**: Every configured ignored column MUST be checked for existence on the board at startup
  and by `robot-army doctor`, individually and by name.
- **FR-017**: A configured ignored column that does not exist on the board MUST fail its check, and
  the failure MUST name the missing column and list the columns the board actually has.
- **FR-018**: A failing ignored-column check MUST refuse board ingestion and MUST NOT affect dispatch
  of issues the author wrote themselves.
- **FR-019**: A configured column name MUST match a board column exactly, including letter case —
  the same rule the tag and lifecycle columns already use. A name that does not match MUST fail
  FR-017's check rather than silently excluding nothing.
- **FR-019a**: Listing the same column name more than once MUST be accepted and MUST mean the same as
  listing it once.
- **FR-019b**: Where the board has more than one column of the configured name, cards in **all** of
  them MUST be excluded, because the author's intent was expressed as a name.
- **FR-020**: A malformed value for this setting — the wrong shape, or an entry that is not a usable
  column name — MUST be a configuration error at load, consistent with the existing rule that a typo
  inside the board section is an error rather than a warning.

### Accountability

- **FR-021**: The poll cycle's audit record MUST report the number of tagged cards skipped for
  sitting in an ignored column, distinctly from the number of tagged cards found.
- **FR-022**: The audit record for the board's startup checks MUST name the configured ignored
  columns and the outcome of each one's existence check.
- **FR-023**: Parking a tracked card, and releasing a parked card, MUST each leave a record naming
  the card, the column, and which of the two happened. One record per transition, never one per
  poll cycle.
- **FR-024**: No individual audit record MUST be written per ignored card per poll cycle, beyond the
  aggregate count of FR-021 — this is a deliberate, documented instance of the Principle III
  exception the board poll already takes, on the same grounds: the reads change no state outside the
  process, and a record per card per cycle would bury the records that matter.

### Key Entities

- **Ignored column set**: The columns, named by the author, whose cards are not intake. A property of
  the board configuration, not of any card. Empty by default. Consulted only for cards with no
  recorded issue.
- **Parked card**: A tracked, unlinked card currently sitting in an ignored column. Distinct from a
  card that has left the board: the latter is terminal and the former is not. Carries the state and
  reason it had when it was parked.

  *Parked*, deliberately, and not *held*: the existing web copy already renders the `needs_info` state
  as "held — the card does not say which repository", and the poll cycle already reports a `held`
  count meaning exactly that. A card can be both at once — awaiting clarification **and** sitting in
  an ignored column — so the two conditions need two words.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The author can stop a tagged card from being filed as an issue by moving it, in one
  drag, into a column configured once — with no edit to the card, no edit to any file, and no restart.
- **SC-002**: A card moved out of an ignored column produces its issue within one poll interval, with
  no human action beyond the move.
- **SC-003**: An installation with no ignored columns configured is indistinguishable from milestone
  003 across every observable behaviour: cards tracked, issues created, board writes, and audit
  records.
- **SC-004**: Every configured ignored column that does not exist on the board is reported by name,
  with the board's actual columns listed alongside it, before any card is evaluated.
- **SC-005**: For any poll cycle, the audit record alone answers how many tagged cards were found, how
  many were skipped as ignored, and which columns were being ignored — without re-reading the board.
- **SC-006**: Zero issues are created from cards in ignored columns across a full verification round,
  and zero cards in ignored columns receive a comment or a move.
- **SC-007**: No card that already has an issue changes its behaviour in any way as a result of this
  setting, measured by taking one card through the full lifecycle with its every column ignored.

## Assumptions

These are the choices made where the issue's one sentence did not say, and the reasoning that picked
them. Each is a decision, not a placeholder.

- **Columns are named, not identified.** The setting holds column *names*, matching the existing
  `in_progress_list` and `done_list` settings, which the author already writes by name. Board ids are
  the more robust identifier and are rejected anyway: this file is read and edited by a human, and a
  24-hex string in it would be unreadable and unverifiable at a glance.
- **The default is empty and the feature is inert.** Consistent with every optional thing in this
  project — the board section itself, cleanup, and notifications all default to doing nothing.
- **Parking is reversible; untagging is not.** The two are deliberately different. Removing the tag is
  a statement about the card's *nature* and is terminal today. Moving a card between columns is a
  statement about its *timing*, and a board's columns are things the author drags cards between many
  times a day. A one-way ignore would be a trap, and the trap would be silent.
- **The ignore list gates intake, not the lifecycle.** It is consulted only for cards with no recorded
  issue. This is what makes the in-progress and done columns safe to list, and it is also the only
  reading consistent with the issue's word "ignore" — a card whose issue exists is not being ignored,
  it has already been acted on.
- **A missing column refuses ingestion rather than warning.** Same precedent, same reasoning as
  milestone 003's tag and lifecycle-column checks: a renamed column is otherwise indistinguishable
  from the feature working, and the failure mode is silently filing issues the author excluded.
- **Ignored cards are counted, not enumerated, in the log.** The board poll already takes a documented
  Principle III exception for its per-card reads. A record per ignored card per cycle would, on a
  board with a full icebox, be the majority of the audit log and would say the same thing every five
  minutes.
- **No notification is sent for an ignored card.** The `notifications` events in milestone 004 are
  about work changing state. A card sitting where the author put it is not an event.

## Out of Scope

- **Ignoring by anything other than column.** No second tag, no card-text marker, no per-repository
  or per-author exclusion. The issue asks for columns and columns are sufficient.
- **A per-card override.** There is no way to say "ignore this column except this card". If the card
  should be intake, it should be in a column that is intake — that is what the columns are for.
- **Acting on a card that enters an ignored column mid-flight.** Cancelling an issue, closing it, or
  stopping a session because its card moved is a different feature with a much larger blast radius,
  and FR-013 explicitly forbids it here.
- **A default set of ignored columns.** Nothing is ignored until the author says so. Guessing that
  "Done" should be ignored would change existing installations' behaviour on upgrade.
- **Scheduling.** "Ignore this column until Monday" is a time-based rule, not a column-based one.
- **Any change to how an intake card becomes an issue.** Resolution, creation, the one-card-one-issue
  invariant, and the recovery path are milestone 003's and are untouched.

## Dependencies & Follow-on

- **Depends on milestone 003.** The board configuration section, the column existence checks, the
  card lifecycle, and `origin_list_id` being captured at first sighting all exist and are extended
  rather than rebuilt.
- **Parked and left must become distinguishable.** FR-008 requires that a tracked card can stop being
  evaluated without being recorded as gone. Today a tracked, unlinked card that stops appearing in the
  poll is recorded as having left the board, and that is a terminal outcome nothing recovers from — so
  reusing that path for parking would violate FR-007 and FR-009. This is the one place where the
  change is structural rather than additive, and the plan must say how.
- **[`docs/roadmap.md`](../../docs/roadmap.md) needs an entry.** This milestone claims the 006 slot;
  the "whatever survives contact with reality" parking lot moves to 007.
- **Closes [issue #3](https://github.com/jantman/robot-army/issues/3).**
