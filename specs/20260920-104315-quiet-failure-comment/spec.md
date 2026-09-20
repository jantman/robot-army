# Feature Specification: A failure comment that says nothing about my machine, and a needs-me card that says why

**Feature Branch**: `speckit/20260920-104315-quiet-failure-comment`

**Created**: 2026-09-20

**Status**: Draft

**Input**: Observed on work item 126 — the comment robot-army posted on a public issue
carried the repository's tool-permission diff, and the page that should have explained the
failure asserted instead that a checkout was missing.

## Context

Work item 126 (`DecaturMakers/kiosk-show-replacement#26`) was blocked by the fingerprint
gate: committed tool-permission settings at the base ref had changed since onboarding, so
dispatch refused before anything was created. Two things then went wrong, in opposite
directions.

**The public issue was told too much.** The comment for a failed attempt fences the failure
reason verbatim. For item 126 that published which settings file had appeared in the
repository, what the approved fingerprint had contained, and the exact local command that
would re-approve it. The reason is machine text of unbounded shape — a hook's stderr, a git
error, an exception, a path under the operator's home directory — so this is a general leak
rather than one gate's mistake. Principle V is about what is *committed*, but the argument is
the same one and applies harder here: a comment on somebody else's public repository is
world-readable and is not the operator's to take back.

**The operator was told the wrong thing.** The **needs me** page renders failed items with
the card written for interrupted ones. That card carries a single banner, keyed on the
checkout not being present, and item 126 never had a checkout — the gate stopped dispatch
before one was created. So the page said the isolated checkout was missing, that resuming
would fail until it was restored, and that abandoning was the usual answer. Every clause of
that is false for this item: nothing is missing, resume is not the question, and the answer
is to review the settings and re-approve. The card never renders the stored failure reason at
all, which the item's own page has shown since issue #63.

The two are one change. The reason stops being published, so the operator's own interface
becomes the only place it is legible — and it has to actually be legible there.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A failed attempt tells a public issue nothing but where it happened (Priority: P1)

Dispatch fails for any reason. The issue gets a comment saying that robot-army could not
start a session, which host it happened on, and which work item to go and look at. Nothing
else — no reason, no fenced machine text, no filesystem path, no command to run, and no
category naming which gate refused it.

**Why this priority**: This is the leak. It is live on a public repository now, and every
future failure on any repository repeats it.

**Independent Test**: Drive a dispatch failure whose reason contains a home-directory path
and a settings filename, and confirm the posted body contains neither, contains the host and
the item number, and nothing more.

**Acceptance Scenarios**:

1. **Given** a dispatch that fails with any reason, **When** the comment is composed, **Then**
   its body contains the host and the work item number and does not contain the reason text
   in any form.
2. **Given** a reason containing a filesystem path, a repository key or a command line,
   **When** the comment is composed, **Then** none of those substrings appear in the body.
3. **Given** two failures with different reasons on the same host and item, **When** both
   comments are composed, **Then** the two bodies are identical — the comment carries no
   signal distinguishing one cause from another.
4. **Given** a failure, **When** the audit log is read afterwards, **Then** the full reason is
   still recorded there, unchanged and unredacted.
5. **Given** a failure and a configured notification channel, **When** the notification is
   sent, **Then** it still carries the full reason — the operator's own device is not a
   public issue.

---

### User Story 2 - The needs-me page says why an item failed (Priority: P1)

The operator opens **needs me**. Each failed item's card carries the reason the system
recorded when it failed, in full, without needing a click through to the item's own page.

**Why this priority**: Inseparable from Story 1. Once the comment stops carrying the reason,
the listing is where the operator finds out what happened, and today it is the one place that
shows every other fact about a failed item except that one.

**Independent Test**: Put one failed item with a recorded reason in the database, render the
page, and confirm the reason appears on its card.

**Acceptance Scenarios**:

1. **Given** a failed item with a recorded failure reason, **When** the needs-me page is
   rendered, **Then** the reason appears on that item's card.
2. **Given** a failure reason spanning several lines, or containing characters that are
   meaningful in the page's markup, **When** it is rendered, **Then** it is displayed as
   written and is not interpreted as markup.
3. **Given** a failed item with no recorded reason — a row from a rebuilt database — **When**
   the page is rendered, **Then** the card says that no reason was recorded rather than
   rendering an empty space that reads as "no problem".
4. **Given** an item in `interrupted` or `awaiting_review`, which fail for nothing and carry
   no reason, **When** the page is rendered, **Then** no empty reason element appears on
   those cards.
5. **Given** the JSON form of the page, **When** it is read, **Then** the failure reason is
   present on the same rows, under the same visibility scoping as every other field.

---

### User Story 3 - A card claims the checkout is missing only when there was one (Priority: P1)

An item that was refused before anything was created is not described as having lost
something. The missing-checkout warning, and its advice to abandon, appear only for an item
that actually had a checkout.

**Why this priority**: The false statement is what sent the operator to the wrong remedy. It
is separately testable from Story 2 — one is an addition, the other a condition on an
existing element — but shipping Story 2 without it would leave the correct reason sitting
directly underneath a banner contradicting it.

**Independent Test**: Render the page with a failed item that has no recorded checkout path
and confirm the banner is absent; render it with an item whose recorded checkout is gone from
disk and confirm the banner is present.

**Acceptance Scenarios**:

1. **Given** a failed item with no checkout path recorded, **When** the page is rendered,
   **Then** no missing-checkout banner appears on its card.
2. **Given** an item whose checkout path is recorded but whose directory is gone, **When** the
   page is rendered, **Then** the banner appears exactly as it does today, in whichever of the
   three states the item is in.
3. **Given** an interrupted item with a present checkout, **When** the page is rendered,
   **Then** no banner appears, unchanged from today.
4. **Given** a failed item that both has a recorded reason and has lost its checkout, **When**
   the page is rendered, **Then** both the reason and the banner appear, and the reason is not
   displaced by the banner.

---

### Edge Cases

- **A reason that is enormous.** Hook stderr can run to hundreds of lines. The card must stay
  readable and the page must not be made unusable by one item; the full text must remain
  reachable.
- **A reason that is empty string rather than absent.** Must be treated as "no reason
  recorded", not rendered as a blank.
- **An item that failed, was retried, and failed again for a different reason.** The card
  shows the reason of the failure it is currently in, which is the one that was recorded last.
- **An item whose stored reason no longer describes what blocks it.** The item's own page
  already distinguishes the stored reason from the current blocker; the listing shows the
  stored one and must not imply it was re-checked.
- **A comment that cannot be posted.** Unchanged: logged and otherwise ignored.
- **Comments already posted.** Nothing edits or deletes an existing comment; the ones already
  on public issues stay until the operator removes them by hand.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The comment posted for a dispatch failure MUST contain the host it failed on and
  the work item number, and MUST NOT contain the failure reason or any part of it.
- **FR-002**: That comment MUST NOT vary with the cause of the failure. Two failures on the
  same host and item produce the same body.
- **FR-003**: The comment MUST NOT contain a filesystem path, a command invocation, a
  repository-local filename, or machine-generated output of any kind.
- **FR-004**: The full failure reason MUST continue to be recorded in the audit log and stored
  on the work item, unchanged.
- **FR-005**: Notifications for a failure MUST continue to carry the full reason. They go to
  the operator, not to a public issue.
- **FR-006**: The needs-me listing MUST display the stored failure reason on the card of every
  item in the failed state that has one.
- **FR-007**: A failed item with no recorded reason MUST be shown as having no reason
  recorded, distinctly from an item whose reason is blank on screen for any other cause.
- **FR-008**: The failure reason MUST be rendered as literal text; no part of it may be
  interpreted as markup by the page.
- **FR-009**: A long failure reason MUST NOT be silently truncated to something that reads as
  complete. If the card shows less than the whole, it MUST say so and the whole MUST remain
  reachable.
- **FR-010**: The missing-checkout warning MUST appear only for an item that has a checkout
  path recorded. Absence of a recorded path means nothing was created and nothing is missing.
- **FR-011**: The missing-checkout warning MUST continue to appear, unchanged, for an item
  whose recorded checkout path no longer exists on disk.
- **FR-012**: The JSON form of the needs-me view MUST carry the same failure reason the
  rendered page shows, for the same rows.
- **FR-013**: Items in `interrupted` and `awaiting_review` MUST NOT gain an empty reason
  element on their cards.

### Key Entities

- **Work item**: already carries `failure_reason` (what was recorded at the moment of
  failure), `blocked_reason` (the same sentence when the failure was a refusal) and
  `worktree_path` (empty when nothing was created). No new field is required; this feature
  changes which of them are shown where.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every path that can fail a dispatch, the body posted to the issue is one of
  exactly one shape, and that shape contains no text derived from the failure.
- **SC-002**: An operator reading a failed item on the needs-me listing learns why it failed
  without navigating anywhere else.
- **SC-003**: No item that never had a checkout is described anywhere in the interface as
  having lost one.
- **SC-004**: Everything the comment used to say remains recoverable by the operator from the
  audit log, the item's stored reason, and the notification — three places, none of them
  public.
- **SC-005**: Reproducing the item 126 scenario end to end produces an issue comment naming
  only a host and an item number, and a needs-me card naming the settings-fingerprint reason
  with no missing-checkout banner.

## Assumptions

- **"Work item number" means the local item id**, the number the operator types at
  `robot-army show`. It is meaningless to a stranger reading the public issue, which is the
  point: it is an address on the operator's machine, not a disclosure.
- **The host line stays** for the reason it was added — trust is granted per machine, so which
  machine failed is a real fact and is not sensitive.
- **No categorisation.** The operator asked for host and item number only; naming the class of
  failure ("blocked by onboarding" against "session failed") was considered and declined,
  because the class is itself a fact about the operator's configuration.
- **The comment is still posted.** Silence on the issue is not the alternative being chosen;
  an attempt that happened should leave a trace where the work was requested.
- **Already-posted comments are not touched.** One comment per attempt, never edited, never
  deleted, is an existing rule of this system and this feature does not make an exception to
  it.
- **The dispatch-success comment is out of scope.** It carries a worktree path and a branch
  name, which is a smaller version of the same question, but it was deliberately designed to
  and the operator did not ask for it to change.
- **Notification bodies are out of scope** for the same reason they are exempted in FR-005.
