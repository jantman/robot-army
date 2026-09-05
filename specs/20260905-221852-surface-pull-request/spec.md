# Feature Specification: Surface the pull request in the web UI

**Feature Branch**: `robot-army/issue-143-link-pr-in-web-ui`

**Created**: 2026-09-05

**Status**: Draft

**Input**: jantman/robot-army issue #143 — "Link PR in web UI": *When a work item results in
a PR being opened (or, a work item's issue has a PR linked to it), surface that in the web
UI.*

## Context

The web interface is how the maintainer looks at the system from a phone. It answers "what is
running", "what is queued", "what needs a decision" — and, once a session has done its job, the
next question is always the same one: **where is the pull request?** Today that answer is
nowhere on the site except in one place, for one kind of item: the resume-decision signals
block on `/interrupted` and on the detail page of an interrupted or awaiting-review item shows
a bare `open PR: yes` link, computed live against the item's branch.

Everywhere else — the active list, the queue, a finished item's page — a pull request is
invisible. The maintainer has to go to GitHub, find the repository, and find the branch. That
is the gap this feature closes.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See the pull request on a work item's page (Priority: P1)

The maintainer opens `/item/<id>` for an item whose session opened a pull request. The page
names the pull request, its number, its state (open, merged, or closed unmerged), and links to
it. When no pull request is known the page says so plainly, in a way that distinguishes "there
is no pull request" from "nobody has looked yet".

**Why this priority**: The detail page is where every "what happened to this issue?" question
ends up. One item at a time is also the smallest viable slice: it needs the pull request to be
discovered and stored, which every other story then reuses.

**Independent Test**: Give a work item a branch and a repository with an open pull request from
that branch, run the refresh, and load `/item/<id>`. The pull request appears with a working
link. Delivered value: the maintainer reaches the PR from the item page without visiting
GitHub.

**Acceptance Scenarios**:

1. **Given** a work item whose branch has an open pull request, **When** the maintainer loads
   the item page, **Then** the page shows the pull request's number, its state, and a link to
   it.
2. **Given** a work item whose issue is referenced by a pull request opened from some other
   branch, **When** the maintainer loads the item page, **Then** that pull request is shown
   too.
3. **Given** a work item for which no pull request has ever been found, **When** the maintainer
   loads the item page, **Then** the page says no pull request is known, and says whether that
   is because none exists or because the item has never been looked up.
4. **Given** a work item whose pull request has since been merged, **When** the maintainer
   loads the item page after a refresh, **Then** the pull request is shown as merged.

---

### User Story 2 - Spot pull requests across the lists (Priority: P2)

The maintainer scans `/active`, `/queue` and `/interrupted` from a phone. Any item that has a
pull request carries a compact, clickable marker in its row, so "this one has produced
something" is visible without opening each item.

**Why this priority**: The lists are the landing surfaces and the reason the web interface
exists; a per-row marker turns a several-tap answer into a glance. It is P2 rather than P1
because it is presentation over a fact User Story 1 already establishes.

**Independent Test**: With one active item that has a pull request and one that does not, load
`/active` and confirm exactly one row carries a link and the other carries a plain placeholder.

**Acceptance Scenarios**:

1. **Given** an active item with a known pull request, **When** the maintainer loads `/active`,
   **Then** the row shows the pull request as a link labelled by its number.
2. **Given** an item with no known pull request, **When** the maintainer loads a list
   containing it, **Then** its pull request cell shows a placeholder and no link.
3. **Given** an item with more than one known pull request, **When** the maintainer loads a
   list containing it, **Then** the row shows one of them plus an indication that there are
   more, and the item's own page shows all of them.

---

### User Story 3 - Trust what the page says about the pull request (Priority: P3)

Pull-request knowledge is read from GitHub in the background, so what the page shows is as
recent as the last successful read and no more. The maintainer can tell how old it is, and can
tell a genuinely absent pull request from one the system failed to ask about.

**Why this priority**: Without it the feature is a plausible-looking claim of unknown vintage.
It is P3 because the two stories above are useful the moment the value is right, and this makes
them honest when it is not.

**Independent Test**: Break the GitHub lookup, run a refresh, and confirm the last known pull
request is still shown, marked with when it was last confirmed, and that the failure is
recorded rather than silently swallowed.

**Acceptance Scenarios**:

1. **Given** a stored pull request and a GitHub lookup that fails, **When** the refresh runs,
   **Then** the stored pull request is retained unchanged, the failure is written to the audit
   log, and the page still shows the pull request with its last-checked time.
2. **Given** a pull request whose state changed on GitHub, **When** a refresh succeeds,
   **Then** the page reflects the new state and a new last-checked time.
3. **Given** a work item that has never been looked up, **When** the maintainer loads its page,
   **Then** the pull request field reads as unknown rather than as "none".

---

### Edge Cases

- **The item has no branch.** Nothing has been dispatched, so there is nothing to look up and
  no pull request can exist. The item reads as having none, and no GitHub call is made for it.
- **A simulated (dry-run) item.** No outward-facing call is made for it under any
  circumstance; its pull-request field reads as unknown, exactly as its other GitHub-derived
  signals do.
- **The pull request is closed without merging, and a second one is opened.** Both are known
  and both are shown on the item page; the lists surface the one that best represents the
  item's current outcome.
- **The same pull request is found by both routes** (its head branch is the item's branch *and*
  it references the item's issue). It is one pull request and appears once.
- **A pull request references the issue but was written by someone else.** It is still shown —
  the question the maintainer is asking is "is there a pull request for this issue", not "did
  we write it".
- **The item is done, cleaned up, or its branch has been deleted.** The last known pull request
  is retained and still shown; it is the record of what the work produced.
- **GitHub is unreachable for the whole refresh pass.** No stored knowledge is erased, every
  failure is recorded, and the next pass tries again.
- **The web page is loaded while a refresh is in flight.** The page renders from stored
  knowledge and never blocks on a network call.

## Requirements *(mandatory)*

### Functional Requirements

**Discovering the pull request**

- **FR-001**: The system MUST discover, for a work item, any pull request opened from the
  item's branch in the item's repository.
- **FR-002**: The system MUST discover, for a work item, any pull request GitHub reports as
  linked to the item's issue, whether or not it was opened from the item's branch.
- **FR-003**: The system MUST treat the two routes as one set: a pull request found by both is
  recorded once, and a work item may have zero, one, or several distinct pull requests.
- **FR-004**: For each discovered pull request the system MUST record its number, the address
  that opens it, and whether it is open, merged, or closed without merging.
- **FR-005**: Discovery MUST run in the background on the daemon's existing reconciliation
  cadence, and MUST NOT be triggered by rendering a web page.
- **FR-006**: The system MUST NOT make any GitHub request on behalf of a simulated (dry-run)
  work item.
- **FR-007**: The system MUST NOT make a pull-request lookup for a work item that has no
  branch.
- **FR-008**: The system MUST limit routine re-checking to work items whose pull-request
  status can still change, and MUST retain — never erase — what was last learned about items
  that have finished.

**Storing what was learned**

- **FR-009**: Discovered pull requests MUST be persisted with the work item, so that every
  surface reads the same stored answer rather than each asking GitHub separately.
- **FR-010**: The system MUST record when the item's pull requests were last successfully
  confirmed, and MUST keep "never looked up" distinguishable from "looked up, found none".
- **FR-011**: A failed lookup MUST leave previously stored pull requests unchanged and MUST NOT
  advance the last-confirmed time.
- **FR-012**: Writes of pull-request knowledge MUST be atomic with respect to interruption: a
  process killed mid-refresh MUST leave each work item either with its previous knowledge or
  with the complete new knowledge, never a half-written mixture.

**Showing it**

- **FR-013**: A work item's page MUST show every pull request known for that item — number,
  state, and a link — or state plainly that none is known.
- **FR-014**: The active, queue and interrupted listings MUST show, per row, a link to the
  item's pull request where one is known, and a plain placeholder where none is.
- **FR-015**: Where an item has several pull requests, a listing row MUST show one of them and
  indicate that there are more.
- **FR-016**: Every surface MUST distinguish three states: a known pull request, a confirmed
  absence of one, and an unknown answer.
- **FR-017**: Surfaces that expose their data as JSON MUST include the same pull-request
  information they render, under a stable name.
- **FR-018**: Pull-request links MUST be rendered with the same outbound-link treatment the
  rest of the interface already uses, and MUST only ever point at GitHub.
- **FR-019**: Where a pull request's staleness matters to a decision — the resume-decision
  signals — the surface MUST continue to show how old the answer is.

**Accountability**

- **FR-020**: A failed pull-request lookup MUST be written to the audit log with the work item
  it was for and the error, and MUST NOT be silently swallowed.
- **FR-021**: A change to what is known about an item's pull requests — the first discovery, a
  new pull request, or a state change — MUST be written to the audit log.

### Key Entities

- **Pull request (as known to a work item)**: the number, the address, and the state (open,
  merged, or closed unmerged) of a pull request associated with a work item, by its branch or
  by its issue. Zero or more per work item.
- **Last-confirmed time**: when the set of pull requests for a work item was last successfully
  established. Its absence means the item has never been looked up, which is not the same as
  having no pull request.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From the web interface's landing surfaces, the maintainer can reach the pull
  request for any item that has one in a single tap, without visiting GitHub to find it.
- **SC-002**: For an item with no pull request, the interface never implies there is one, and
  never presents "we could not ask" as "there is none".
- **SC-003**: A pull request opened during a session appears in the web interface within one
  reconciliation cycle of it being opened.
- **SC-004**: Rendering any page of the web interface performs no GitHub request on behalf of
  pull-request display, and page render time is unaffected by GitHub's availability.
- **SC-005**: A finished item's page still names the pull request it produced after the item is
  done and its checkout has been cleaned up.
- **SC-006**: Every unsuccessful pull-request lookup is reconstructable from the audit log
  alone: which item, when, and what went wrong.

## Assumptions

- The maintainer is the only user; there is no permission model to consider, and the GitHub
  token already configured for polling is the one that answers these lookups.
- "Linked to the issue" means the relationship GitHub itself reports between an issue and the
  pull requests that reference or would close it — not a textual scan of issue or pull-request
  bodies performed here.
- One pull request per work item is the overwhelmingly common case; support for several exists
  so the interface is not forced to lie when a first attempt was closed and a second opened,
  not because several is expected.
- The existing resume-decision signals block keeps its live, short-cached behaviour for the
  interrupted/awaiting-review decision, where a value a minute old is worth the call. Stored
  knowledge is what every other surface reads.
- Pull-request state is displayed, not acted upon: nothing dispatches, blocks, closes, or
  cleans up differently because of what is found here. The existing `wait_for_merge` gate and
  the closed-issue resolution are untouched.
- Notifications about pull requests are out of scope.
- The work item's repository is a GitHub repository; no other forge is supported by the
  project.
