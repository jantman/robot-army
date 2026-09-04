# Feature Specification: Retry Re-Verifies the Author

**Feature Branch**: `speckit/20260903-203351-retry-author-recheck`

**Created**: 2026-09-03

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#119 — "RA-01: retry bypasses the issue-author check, and the web UI says it does not". `retry` returns an author-rejected item to the dispatch queue without re-checking the author, and the web UI tells the operator the opposite. Fix: re-fetch the issue and re-run the eligibility evaluation inside `retry` before returning the item to the queue; correct the interface text; stop the dispatch path fabricating an author it never read. Severity High; RA-01 in `docs/security-analysis.md`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Retry cannot smuggle someone else's issue into the queue (Priority: P1)

Anyone with an account can open an issue on a public repository the maintainer has
onboarded. The label that triggers dispatch is applied by the maintainer, deliberately —
that is the intended workflow. The author check is what stops the second half of that
workflow being available to the first person: an issue written by anybody other than the
configured author is refused, and the refusal is recorded on the item so it can explain
itself.

Today that refusal is one button press deep. The item sits in the queue's blocked section
with a `retry` control beside it, and `retry` returns it to `ready` having re-checked only
the repository's onboarding, clone location, workspace trust, and settings fingerprint —
none of which is about who wrote the issue. The stored, attacker-authored title and body
then dispatch unattended as the prompt of an agent running in the maintainer's checkout.

After this change, `retry` re-reads the issue from its source and re-runs the same
eligibility evaluation the poller runs. An issue that fails any eligibility condition —
the author condition above all — is refused, the item stays blocked, and the refusal names
the condition that is still failing.

**Why this priority**: This is the security defect. Everything else in this specification
is either a consequence of the same re-read or a correction to text that describes it.
Without this story the control the system documents as non-disableable remains disabled by
a single click.

**Independent Test**: Take an item that the poller failed on the author condition, invoke
`retry` on it from the command line and from the web interface, and confirm that both
refuse, that the item is still in the failed state, and that the refusal text names the
author condition.

**Acceptance Scenarios**:

1. **Given** a failed work item whose recorded block is the author condition, **When**
   `retry` is invoked and the issue's author is still not the configured author, **Then**
   the retry is refused, the item remains failed, and the refusal names the author
   condition as the reason.
2. **Given** a failed work item, **When** `retry` is invoked and the live issue has since
   lost the dispatch label, **Then** the retry is refused and the refusal names the missing
   label rather than the reason the item originally failed.
3. **Given** a failed work item, **When** `retry` is invoked and the live issue has since
   been closed, **Then** the retry is refused and the refusal says the issue is closed.
4. **Given** a failed work item whose eligibility conditions all pass on re-evaluation,
   **When** `retry` is invoked and the repository's own preconditions also pass, **Then**
   the item returns to the queue with its recorded failure and block reasons cleared.
5. **Given** a failed work item that failed for a reason unrelated to eligibility — a
   worktree that could not be created, say — **When** `retry` is invoked, **Then**
   eligibility is re-evaluated all the same, and a now-ineligible issue is refused.
6. **Given** a failed work item, **When** `retry` is refused because eligibility no longer
   passes, **Then** every recorded reason the queue may display is updated to the current
   reason, so the queue shows why it is blocked now rather than why it was blocked before —
   verified by reading the rendered page, not only the stored columns.
7. **Given** any invocation of `retry`, **When** it is refused or allowed, **Then** the
   decision, the item it concerned, and the reason are written to the durable action
   record.

---

### User Story 2 - Retry dispatches the issue as it stands, not as it was (Priority: P2)

An issue's title and body are stored when the poller first sees them and are never read
again. An issue that was eligible when it was discovered can be edited afterwards by
anyone with write access to the repository, and a retry weeks later dispatches the text as
it was at discovery — or, if the edit came first, dispatches text nobody re-approved.

Because `retry` now re-reads the issue in order to re-evaluate it, it has the current text
in hand. It records that text on the item, so what dispatches is what the maintainer would
see if they opened the issue in a browser at the moment they pressed the button.

**Why this priority**: It closes the stale-content half of the same family of findings and
costs nothing extra — the read has already happened for Story 1. It is second because an
item with stale-but-honest content is a far smaller problem than an item with someone
else's content.

**Independent Test**: Fail an item, change the issue's title, body and labels at the
source, retry it successfully, and confirm the item now carries the new title, body and
labels.

**Acceptance Scenarios**:

1. **Given** a failed work item whose issue has been retitled and rewritten since
   discovery, **When** `retry` succeeds, **Then** the item's stored title and body are the
   ones just read from the source.
2. **Given** a failed work item whose issue's labels have changed since discovery, **When**
   `retry` succeeds, **Then** the item's stored labels are the ones just read.
3. **Given** a failed work item, **When** `retry` is refused, **Then** the item's stored
   title, body and labels are still refreshed to what was just read, because the queue
   showing a blocked item should describe the issue as it currently is.

---

### User Story 3 - The interface describes the check it actually performs (Priority: P2)

The web interface offers `retry` behind a confirmation reading "Refused, with the reason,
if the condition that blocked it still holds." For the author condition that sentence is
false today, and it is false in the one place where it most matters — the blocked section
of the queue, where an author-rejected item appears with its reason on display next to the
button that promises to honour it.

The confirmation text, and the command-line help for the same operation, must describe
what is really re-verified: the repository's preconditions *and* the issue's eligibility,
re-read from the source.

**Why this priority**: The false promise is what converts the defect into a confused-deputy
attack — the maintainer clicks because the interface tells them clicking is safe. Once
Story 1 lands the sentence becomes true, but leaving it vague would still misdescribe the
new behaviour, in particular the fact that a retry now performs a live read that can fail.

**Independent Test**: Read the confirmation text presented for `retry` in the web interface
and the help text for the command-line equivalent, and confirm both state that the issue is
re-read and its eligibility re-checked.

**Acceptance Scenarios**:

1. **Given** the queue's blocked section, **When** the `retry` control's confirmation is
   shown, **Then** its text says the issue is re-read from its source and its eligibility
   re-checked, in addition to the repository's own preconditions.
2. **Given** the command-line help, **When** the retry operation's description is read,
   **Then** it says the same thing as the web confirmation.

---

### User Story 4 - The dispatch path carries an author it actually read (Priority: P3)

The dispatch path builds the issue it hands to a session by asserting the author is the
configured author rather than by reading one:

```python
issue = Issue(..., author=config.github.author, ...)
```

Nothing downstream consumes that value today, so this is not itself exploitable. It is
worse than harmless: the line makes the code read as though an author check happened
somewhere, which removes the last natural place to notice that one had not. The item
should carry the author its issue actually had, recorded when the issue was read, and the
dispatch path should refuse an item whose recorded author does not match rather than
assert one into existence.

**Why this priority**: Defence in depth, not the vulnerability. It is worth doing because
the security analysis found RA-01 partly by reading that line and believing it, and
because a second, independent refusal point costs one comparison per dispatch.

**Independent Test**: Record an item whose stored author is not the configured author,
drive it into the dispatchable state directly, and confirm the dispatch refuses it, fails
the item, and names the author as the reason.

**Acceptance Scenarios**:

1. **Given** a work item discovered by the poller, **When** its row is written, **Then**
   the issue's author is recorded on it.
2. **Given** a work item returned to the queue by `retry`, **When** its row is updated,
   **Then** the author just read from the source is recorded on it.
3. **Given** a work item in the queue whose recorded author is not the configured author,
   **When** dispatch reaches it, **Then** it is refused, moved to failed with a reason
   naming the author condition, and no worktree, branch or session is created.
4. **Given** a work item that predates this change and therefore has no recorded author,
   **When** dispatch reaches it, **Then** it is refused with a reason saying the author was
   never recorded and that `retry` will re-read and re-verify it, and no worktree, branch
   or session is created.
5. **Given** any refusal under this story, **When** it happens, **Then** it is written to
   the durable action record.

---

### Edge Cases

- **The source cannot be reached.** A retry that cannot read the issue must refuse, and
  must say the read failed. It must never fall back to the stored copy: the stored copy is
  precisely what cannot be trusted, and a retry that silently used it would be the original
  defect with a network hiccup as its trigger.
- **The issue no longer exists, or the token can no longer see it.** Indistinguishable from
  outside, and treated the same: refuse, and say so in those terms.
- **The item's repository is no longer onboarded or no longer resolves to a clone.** The
  existing repository preconditions already refuse this, before any read is attempted;
  nothing here changes that, and no network call is spent on an item that cannot dispatch
  anyway.
- **The configured author changed.** An item blocked on the author condition under an old
  configuration becomes eligible under a new one. Retrying it succeeds, and that is
  correct: the check is against the configuration in force at the moment of the retry.
- **The item is a simulated (dry-run) row.** Reads are real at every effect level, by
  existing design, precisely so that a dry run tells the truth about eligibility. A retry
  of a simulated item therefore performs the same real read and reaches the same verdict.
- **Repeated retries of a permanently ineligible item.** Each one costs one read against
  the source's rate limit. Acceptable: it is an interactive, one-at-a-time operation with a
  confirmation in front of it, not something on a timer.
- **The read succeeds but the item is then blocked by a repository precondition.** Only one
  refusal is reported — whichever condition is evaluated first — because the operator has
  to fix them one at a time regardless, and naming a second condition they have not reached
  yet would be noise.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `retry` MUST re-read the item's issue from its source before returning the
  item to the dispatch queue.
- **FR-002**: `retry` MUST re-run the same eligibility evaluation the poller runs against
  the freshly read issue, and MUST refuse the retry if any eligibility condition fails.
- **FR-003**: The eligibility evaluation performed by `retry` MUST be the same one the
  poller performs, not a reimplementation of it, so that the two can never disagree about
  what makes an issue eligible.
- **FR-004**: A refused `retry` MUST leave the item in the failed state and MUST report the
  condition that caused the refusal.
- **FR-005**: A refused `retry` MUST update **every** recorded reason the interface may
  display — both the block reason and the failure reason — to the reason the refusal just
  established, so the queue describes the item's present state rather than a past one.
  Updating only one of them satisfies the letter of this requirement and defeats it in
  practice, because the queue renders whichever it finds first.
- **FR-006**: `retry` MUST refuse, naming the read failure, when the issue cannot be read —
  whether because the source is unreachable, the issue no longer exists, or it is no longer
  visible. It MUST NOT fall back to the item's stored copy of the issue.
- **FR-007**: `retry` MUST re-evaluate eligibility regardless of why the item originally
  failed, including items that failed for reasons unrelated to eligibility.
- **FR-008**: `retry` MUST continue to verify the repository preconditions it verifies
  today — onboarding, recorded clone location, workspace trust, and settings fingerprint —
  in addition to eligibility.
- **FR-009**: `retry` MUST record the issue's current title, body, labels and author on the
  work item from the read it just performed, whether the retry is then allowed or refused.
- **FR-010**: `retry` MUST write its decision, the item concerned, and the reason to the
  durable action record, for both the allowed and the refused outcome.
- **FR-011**: The web interface's confirmation text for `retry` MUST state that the issue is
  re-read from its source and its eligibility re-checked, alongside the repository's own
  preconditions.
- **FR-012**: The command-line description of the retry operation MUST state the same thing
  as the web confirmation.
- **FR-013**: The work item MUST carry the author of the issue it was created from,
  recorded at discovery and refreshed on every subsequent read of that issue.
- **FR-014**: Dispatch MUST use the item's recorded author rather than asserting the
  configured author, and MUST refuse to dispatch an item whose recorded author is not the
  configured author.
- **FR-015**: Dispatch MUST refuse an item that carries no recorded author, with a reason
  that says the author was never recorded and names `retry` as the way to re-read and
  re-verify it.
- **FR-016**: A dispatch refused under FR-014 or FR-015 MUST create no worktree, no branch
  and no session, MUST move the item to failed with the refusal as its reason, and MUST
  write the refusal to the durable action record.
- **FR-017**: Work items that exist before this change MUST remain readable and operable;
  the absence of a recorded author on them MUST be a recognised state with the defined
  behaviour of FR-015, not an error or a crash.
- **FR-018**: `docs/security-analysis.md` MUST record that RA-01 is resolved and by what,
  and MUST record how much of RA-04 this change closes and how much it does not.

### Key Entities

- **Work item**: the tracked unit of work created from an issue. Gains one recorded fact —
  the author of the issue it was created from — alongside the title, body and labels it
  already stores. That fact is what lets a dispatch check the author without a network
  call, and its absence on older rows is itself meaningful.
- **Eligibility verdict**: the poller's decision about whether an issue may be worked on,
  and the reason when it may not. Already a single, shared decision point; this change adds
  a second caller to it rather than a second copy of it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An item blocked because someone else wrote its issue cannot be returned to
  the dispatch queue by any available operation while that remains true — not from the
  command line, not from the web interface, and not by any sequence of the two.
- **SC-002**: Every path that returns an item to the dispatch queue re-reads the issue and
  re-evaluates it first; the number of such paths that do not is zero.
- **SC-003**: The text shown to the maintainer before they confirm a retry accurately
  describes every check the retry performs.
- **SC-004**: An issue edited after discovery dispatches with the text it has at the moment
  of the retry, not the text it had at discovery.
- **SC-005**: A retry that cannot read its issue never returns the item to the queue, in
  every failure mode of the read.
- **SC-006**: Reconstructing what happened from the action record alone answers, for any
  retry, whether it was allowed, what the eligibility verdict was, and what the item's
  content was refreshed to.
- **SC-007**: The security analysis document no longer describes RA-01 as an open finding.

## Assumptions

- Every work item today originates from a GitHub issue, and the issue source can be read
  one issue at a time by repository and number. Trello-originated work reaches the queue as
  a GitHub issue and is polled like any other, so it needs no separate treatment.
- Reads against the issue source are real at every effect level. This is existing,
  deliberate design — a dry run that faked its reads would say nothing useful about
  eligibility — so a retry of a simulated item performs a real read, and this specification
  does not carve out an exception.
- Refusing to dispatch pre-existing items that carry no recorded author is acceptable
  disruption. Such items are refused into the failed state with an explanation and a named
  recovery — `retry`, which re-reads and re-verifies them — rather than silently trusted.
  This is a single-user system where the realistic number of in-flight items at upgrade is
  small, and trusting a row precisely because its provenance is unknown is the reasoning
  that produced this defect.
- Recording the author on the work item requires a schema change to the local store. The
  project already carries a numbered migration sequence and adding a nullable column to it
  is ordinary maintenance, not new machinery.
- The stale-content problem (RA-04) is only partly addressed here. This change makes the
  retry path re-read, which removes the largest gap; it does not add a re-read or an
  edited-since-discovery confirmation to the ordinary poll-to-dispatch path. That remains
  open and is recorded as such.
- One refusal reason is reported per retry rather than a collected list of every failing
  condition, matching how the repository preconditions already report.
