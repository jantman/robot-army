# Feature Specification: Guard cross-origin GETs, and stop the read views being expensive

**Feature Branch**: `speckit/20260904-143822-guard-cross-origin-gets`

**Created**: 2026-09-04

**Status**: Draft

**Input**: RA-14 in `docs/security-analysis.md` — "cross-origin GETs are unchecked and expensive".

The origin check runs only on state-changing requests. Read views are not checked at all, and
several of them are expensive enough that a page the maintainer happens to have open can make
the workstation do real work in a loop: forking `git` once per displayed item, reading every
audit file in the log directory into memory, and enumerating the process table twice per page.
The response is opaque to the attacking page — it needs no read for the attack to land.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A visited page cannot make the interface do work (Priority: P1)

The maintainer has an unrelated site open in the browser while the interface is running on its
shipped default address. That site issues a stream of no-cors fetches at the interface's read
views. The browser labels each one as cross-site, and the interface declines to do the work
before any view is rendered — no `git` is forked, no audit file is read, no process table is
enumerated. The maintainer notices nothing, because their own browsing of the interface, and
their own terminal, are unaffected.

**Why this priority**: This is the finding. Everything else in this feature reduces the cost of
work that should still be refused outright; this is what refuses it.

**Independent Test**: Issue a read request carrying the browser's cross-site label and confirm
it is declined with an explanation, and that the declining happens before any of the expensive
observation runs. Issue the same request with a same-origin label, with no label at all (the
documented terminal path), and with the label a top-level address-bar navigation carries, and
confirm each is served normally.

**Acceptance Scenarios**:

1. **Given** the interface is running, **When** a read request arrives labelled by the browser
   as cross-site, **Then** it is declined with a 403 and a message explaining why, and no item
   signals, audit files, or process enumeration are computed for it.
2. **Given** the interface is running, **When** a read request arrives with no browser origin
   label at all — the documented terminal path — **Then** it is served normally.
3. **Given** the interface is running, **When** the maintainer opens a page from the address
   bar or a bookmark, **Then** it is served normally.
4. **Given** the interface is running, **When** a read request arrives labelled cross-site,
   **Then** the refusal is legible as a refusal in the same way every other refusal on this
   interface is, rather than as a crash or a blank page.

---

### User Story 2 - The interrupted view stops forking git per card (Priority: P2)

The maintainer opens the view listing interrupted and awaiting-review items. Today, each card
shown costs several `git` subprocesses, every render, and the view auto-refreshes. After this
change, a rendering of the same page repeated within a short window reuses what the previous
render observed, so a page that refreshes on a timer does not multiply the cost by the refresh
rate — while a maintainer who has just acted on an item, or who reloads after touching a
worktree, still sees the current state.

**Why this priority**: This is the single largest per-request cost, it scales with the number of
items on the busiest read view, and it is the one that spawns processes rather than merely
using CPU. It is second only because with Story 1 in place, the hostile caller is already gone;
this is what stops the maintainer's own auto-refreshing page being expensive.

**Independent Test**: Render the interrupted view twice in quick succession against several
items and count the version-control observations made; the second render must make far fewer
than the first. Then advance past the reuse window and confirm the observations are made again.

**Acceptance Scenarios**:

1. **Given** several interrupted items, **When** the interrupted view is rendered twice within
   the reuse window, **Then** the second render performs no fresh version-control observation
   for an item whose state has not changed.
2. **Given** an item whose local worktree state has changed, **When** the reuse window has
   passed and the view is rendered, **Then** the newly observed state is what is shown.
3. **Given** an item the maintainer has just acted on, **When** the resulting page renders,
   **Then** the signals shown reflect the action rather than a value observed before it.
4. **Given** a rendered signal that was reused rather than freshly observed, **When** the
   reader looks at it, **Then** they can tell how old it is, exactly as they already can for
   the signals sourced from the issue host.

---

### User Story 3 - A log query that matches nothing stays cheap (Priority: P3)

The maintainer filters the audit view by an item that has no records — or simply pages far back.
Today that walks every daily audit file in the log directory and reads each one whole into
memory. After this change, each file is read from its end in bounded pieces, and a single
request stops after a stated ceiling on how much it will read. When a request stops at that
ceiling with the page unfilled, the view says so rather than implying the history is empty.

**Why this priority**: It is a genuine unbounded read, but it is bounded by the size of the
maintainer's own log directory rather than by anything an attacker supplies, and Story 1 removes
the caller who would abuse it. It matters most as the log grows.

**Independent Test**: Write a log directory containing several large daily files, request a page
with a filter matching nothing, and confirm the request reads no more than the stated ceiling
and reports that it stopped early. Confirm that an unfiltered first page still returns the
newest records, and that paging with a cursor still produces disjoint pages.

**Acceptance Scenarios**:

1. **Given** a log directory of several large daily files, **When** a page is requested with a
   filter matching no record, **Then** the request reads no more than the stated ceiling of
   bytes and returns promptly.
2. **Given** a request that stopped at the ceiling before filling its page, **When** the result
   is read, **Then** it states that the scan was truncated, so an empty page is never mistaken
   for an empty history.
3. **Given** any log directory, **When** the first page is requested, **Then** the records
   returned are the same newest-first records the current behaviour returns.
4. **Given** a page boundary that falls inside a daily file, **When** the next page is requested
   with the returned cursor, **Then** the two pages are disjoint and no record is skipped.

---

### User Story 4 - One machine observation per rendered page (Priority: P3)

Every page carries a capacity line in its chrome, and the queue view shows capacity again in its
own body. Today, rendering the queue observes the machine twice. After this change, each render
observes the machine once and both places read the same observation — which also removes the
possibility of the two disagreeing within one page.

**Why this priority**: The smallest saving of the four, but it also removes a correctness hazard
that has nothing to do with cost: two observations in one render can differ.

**Independent Test**: Render the queue view and count the machine observations performed; it
must be one. Confirm the capacity shown in the chrome and in the body are identical.

**Acceptance Scenarios**:

1. **Given** the queue view is rendered, **When** the render completes, **Then** the machine was
   observed exactly once.
2. **Given** the queue view is rendered, **When** the chrome's capacity line and the body's
   capacity block are compared, **Then** they report the same numbers.
3. **Given** any other view is rendered, **When** the render completes, **Then** the machine was
   observed exactly once.

---

### Edge Cases

- A read request labelled by the browser as *same-site* — a different port or subdomain on the
  same registrable domain. The interface answers on an address rather than a name, so this label
  cannot arise honestly here; it is treated the same as cross-site, matching how the existing
  state-changing check already treats it.
- A read request carrying a browser origin label *and* an origin that matches the host. The
  label decides; the check is about what the browser says the request's provenance is.
- The static assets and the error pages that are produced before routing. These must remain
  reachable and cheap; a refusal must not itself become expensive to render.
- Reused item signals for an item that no longer exists, or whose branch or worktree path has
  changed since they were observed. The reuse must be keyed such that a changed item is a
  different observation, never a stale one attributed to the new state.
- A version-control observation that fails. The failure is what gets shown, and reusing a
  failure would suppress the retry that would have reported it recovering — so a failed
  observation is not reused.
- A simulated item, which must not cause outward-facing effects. Nothing here may introduce one.
- A daily audit file that is exactly at, or just under, the byte ceiling; and one whose final
  line is truncated because the daemon was writing when the read happened.
- A record longer than the block size used to read a file backwards — it must still be returned
  whole, not split.
- Concurrent renders from the auto-refresh loop and a manual reload arriving together; reuse
  must not produce a torn or half-updated set of signals.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The interface MUST decline any request — read or state-changing — that the
  browser labels as coming from another site, before any work is done on that request beyond
  what is needed to decline it.
- **FR-002**: The interface MUST continue to serve requests that carry no browser origin label
  at all, because the documented terminal path sends none and a client that could forge one
  can reach the port directly anyway.
- **FR-003**: The interface MUST continue to serve a request the browser labels as a top-level
  navigation the user initiated — an address-bar entry or a bookmark — because that is how the
  maintainer opens the interface.
- **FR-004**: A declined request MUST be reported to the client with the same refusal shape the
  interface already uses, carrying a message that explains what was refused and why.
- **FR-005**: The refusal of a read request MUST NOT weaken the existing check on
  state-changing requests, which remains at least as strict as it is today.
- **FR-006**: The locally observed item signals MUST be reusable across renders for a bounded,
  documented interval, rather than recomputed on every render.
- **FR-007**: Reused item signals MUST be keyed on the item's identity together with the
  worktree location and branch they describe, so that an item whose location or branch changed
  is observed afresh rather than served a value describing something else.
- **FR-008**: A failed local observation MUST NOT be reused; the next render MUST attempt it
  again.
- **FR-009**: Reused item signals MUST carry their age, so a reader can tell an observation made
  now from one reused from a moment ago — the same guarantee the issue-host signals already
  give.
- **FR-010**: Acting on an item MUST invalidate any reused signals for that item, so that the
  page rendered after an action reflects the action.
- **FR-011**: Reuse of item signals MUST be in-memory and per-process only. Nothing observed
  here may be written to persistent state.
- **FR-012**: The stored set of reused signals MUST be bounded, so that a long-running process
  cannot accumulate them without limit.
- **FR-013**: A daily audit file MUST be read from its end in bounded pieces, rather than read
  whole into memory, when producing a page of the log.
- **FR-014**: A single log-page request MUST stop after a stated ceiling on the total bytes it
  reads across all files, and MUST report that it stopped early when it does.
- **FR-015**: The records a log page returns, their order, and the disjointness of successive
  pages under a cursor MUST be unchanged by FR-013 and FR-014, except that a truncated scan
  returns fewer records and says so.
- **FR-016**: Rendering a page MUST observe the machine's session capacity at most once, and
  every part of that page that shows capacity MUST read that one observation.
- **FR-017**: Every refusal introduced by this feature MUST leave a record in the audit log, on
  the same terms as the refusals already recorded.
- **FR-018**: The reuse interval, the byte ceiling, and the bound on retained signals MUST be
  stated in the operator-facing documentation alongside what they mean for freshness.

### Key Entities

- **Browser origin label**: What a browser states about where a request came from — same-origin,
  same-site, cross-site, or a user-initiated top-level navigation. Absent from non-browser
  clients, which is what makes it usable as a guard without becoming authentication.
- **Local item signals**: The worktree-and-branch facts shown on a resume decision — whether the
  worktree is present, whether it has uncommitted changes, how many commits are on the branch.
  Observed from local version control; today recomputed on every render.
- **Log page**: A bounded, newest-first window over the daily audit files, addressed by a cursor
  naming a file and how much of it a previous page consumed.
- **Capacity observation**: One reading of how many sessions are running on the machine, from the
  session registry and the process table.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A stream of read requests carrying a browser's cross-site label causes no version
  control processes to be started, no audit files to be read, and no process-table enumeration —
  measured as zero of each across the stream.
- **SC-002**: Every documented terminal invocation in the quickstart continues to work unchanged,
  with no new flag, header, or configuration required.
- **SC-003**: Opening any view from the browser's address bar continues to work unchanged.
- **SC-004**: Rendering the interrupted view twice within the reuse window, against ten items,
  performs at most the version-control observations of a single render.
- **SC-005**: A log page request against a log directory of at least 100 MB with a filter
  matching nothing completes in under two seconds and reads no more than the stated ceiling.
- **SC-006**: The first page of an unfiltered log request returns exactly the records it returns
  today, in the same order.
- **SC-007**: Rendering the queue view performs exactly one capacity observation.
- **SC-008**: The full unit test suite passes, and every behaviour above is covered by a test
  that fails against the current code.

## Assumptions

- The interface is reached by IP address or `localhost`, never by a hostname — this is already
  enforced on every request, and it is why a *same-site* label cannot arise honestly and can be
  refused with the cross-site one.
- Browsers reliably send the origin label on every fetch, and non-browser clients such as `curl`
  send none. This is the same assumption the existing state-changing check already rests on; this
  feature extends its reach rather than introducing a new dependency.
- A few seconds of staleness on the local worktree signals is acceptable to the maintainer,
  provided the age is visible and acting on an item clears it. The alternative — computing the
  signals only for an expanded item — was considered and is rejected as a larger change to how
  the view is read.
- The audit log's daily files are line-oriented and append-only, so reading one backwards in
  blocks yields the same records as reading it whole and reversing.
- Existing configuration is not extended. The reuse interval and the byte ceiling are constants
  in the code with stated reasoning, consistent with how the existing signal reuse interval is
  expressed.
- This feature does not alter which routes exist, what any view renders, or any state-changing
  behaviour. It changes what is refused, and what a render costs.
- `docs/security-analysis.md` is a point-in-time review and is not amended by fixes; the
  precedent set by the two preceding findings in the same section is followed.
