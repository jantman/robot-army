# Feature Specification: A chrome pill for work waiting on the operator, and two pills that stop saying the default

**Feature Branch**: `robot-army/issue-182-the-web-chrome-never-says-there-is-work`

**Created**: 2026-09-20

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#182 — "The web chrome never says there is work waiting on me, and spends two pills saying nothing"

## Context

The web interface renders a strip of pills at the top of every view — the chrome bar. It is
the one place a fact is guaranteed to be seen, because it is on every page. Today that strip
carries the effect level, the daemon's state and heartbeat age, the session count, the
dispatch order, the anomaly count, a pause pill when dispatch is paused, and a
simulated-visibility toggle.

Two complaints, from the same strip:

1. **It never says work is parked waiting on the operator.** An item that leaves `active`
   for `awaiting_review` disappears from `/active` and was never on `/queue`, and no count
   anywhere notices. The page that does list it is labelled after a different state
   (`interrupted`) and carries no count, so there is no reason to click it. `failed` is worse
   still: no page lists it at all. The terminal has never had this problem — `robot-army
   status` prints counts by state.

2. **Two pills are on screen permanently stating the default.** `effect level: live` and
   `simulated rows hidden` are both true unless the operator went out of their way to make
   them otherwise, so neither is news. The rest of the bar already follows the opposite
   convention: the pause pill, the effect-mismatch banner, the cap-disagreement note and the
   simulated-consequences banner are all absent when there is nothing to say.

Both of these reverse reasoning that is recorded in comments in the code. The issue's author
states the overruling argument for each, and that argument — not a deletion — is what should
replace the existing comment.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The bar says how much work is parked on me (Priority: P1)

The operator exits a session. The item moves to `awaiting_review`. Whatever page they happen
to be looking at, the chrome bar now carries a count of everything the machine will not move
without them, and that count is a link to the page that lists those items.

**Why this priority**: This is the reported defect. Without it the operator has to remember
to go looking, and the web silently loses a fact the terminal has always shown.

**Independent Test**: Put one item in `awaiting_review`, request any view, and confirm the
bar carries a pill reading `1 needs me` that links to the page listing it. Delivers the whole
of the reported value on its own.

**Acceptance Scenarios**:

1. **Given** one item in `awaiting_review`, one in `interrupted` and one in `failed`, **When**
   any view is rendered, **Then** the chrome bar carries a single pill counting 3, styled as
   a warning, linking to the page that lists all three.
2. **Given** no item in any of those three states, **When** any view is rendered, **Then** the
   pill reads 0 and is styled quiet, exactly as the anomaly pill behaves at zero.
3. **Given** an item in `awaiting_review` that is simulated, **When** the view is rendered
   with simulated rows hidden, **Then** the pill does not count it; **When** rendered with
   simulated rows included, **Then** it does.
4. **Given** the pill reads N, **When** its link is followed with the same visibility setting,
   **Then** the destination page lists exactly N items.
5. **Given** the JSON form of any view, **When** it is read, **Then** the same count is
   present in the chrome payload under the same scoping.

---

### User Story 2 - The page the pill points at lists all three states, and is named for what it lists (Priority: P1)

The operator follows the pill. The page they land on lists everything the pill counted —
interrupted, awaiting review, and failed — each in its own section with its own empty text
and its own legal controls. The navigation entry is named for what the page actually holds.

**Why this priority**: The pill and its destination disagreeing is the same defect in a
different place. A count that promises three items and a page that shows two is worse than no
count. Inseparable from Story 1 in practice; both must ship together.

**Independent Test**: Put one failed item in the database, open the destination page, and
confirm it is listed with its legal controls (retry, reset) reachable.

**Acceptance Scenarios**:

1. **Given** one item in `failed`, **When** the destination page is rendered, **Then** the
   item appears in a failed section, with the controls its state permits.
2. **Given** nothing in `failed`, **When** the page is rendered, **Then** the failed section
   says so in its own words rather than being omitted.
3. **Given** simulated rows are hidden and a section's every row is simulated, **When** the
   page is rendered, **Then** that section's empty text states the number withheld and offers
   the route to reveal them, as the existing sections already do.
4. **Given** any view, **When** the navigation is rendered, **Then** the entry for that page
   is named for all three states it lists rather than for one of them.

---

### User Story 3 - The level pill is present only when the level is not plainly live (Priority: P2)

The operator on a live instance sees no effect-level pill at all, and reads its absence as
`live`. On any instance below live, or where the level could not be determined, the pill is
present and says so.

**Why this priority**: Independent of Stories 1 and 2 and separately testable, but a quieter
win — it removes noise rather than restoring a missing fact.

**Independent Test**: Render a view at `live` and confirm no level pill; render at `plan` and
at `unknown` and confirm the pill is present in both.

**Acceptance Scenarios**:

1. **Given** the effective level is exactly `live`, **When** any view is rendered, **Then** no
   effect-level pill appears.
2. **Given** the effective level is below `live`, **When** any view is rendered, **Then** the
   pill appears and carries both the level and the word marking it as simulated, as today.
3. **Given** a running daemon whose effect level could not be read, so the level resolves to
   `unknown`, **When** any view is rendered, **Then** the pill appears. "We could not tell" is
   news, not the default.
4. **Given** an error response with bare chrome — a 404, a 405, a schema refusal — whose level
   is `unknown`, **When** it is rendered, **Then** the pill appears.

---

### User Story 4 - The visibility toggle is present only when simulated rows are being included (Priority: P2)

The operator on a live instance, with simulated rows hidden as they are by default, sees no
visibility pill. When simulated rows are being included — which is the default below `live` —
the pill is present, because that is the surprising state and the page is full of rows
describing things that did not happen.

**Why this priority**: Same family as Story 3. It carries a verification obligation that the
others do not, which is why it is specified separately.

**Independent Test**: Render a view with simulated rows hidden and confirm no visibility pill;
render with them included and confirm it is present and links to the same view with the
setting flipped.

**Acceptance Scenarios**:

1. **Given** simulated rows are being included, **When** any view is rendered, **Then** the
   pill appears, reads that they are included, and links to the same path with the setting
   flipped.
2. **Given** simulated rows are being hidden, **When** any view is rendered, **Then** no
   visibility pill appears.
3. **Given** simulated rows are being hidden **and** a view withheld at least one row, **When**
   it is rendered, **Then** that view itself carries the note naming the number withheld and
   the link that reveals them — so the reader is never stranded by the pill's absence.
4. **Given** every view that can withhold simulated rows, **When** each is rendered with rows
   withheld, **Then** each one carries that note. This is a precondition on removing the pill,
   not a consequence of it: a view that can withhold without disclosing would lose its only
   route back.

---

### Edge Cases

- **A count of zero.** The pill renders, quiet, reading zero — it does not disappear. This is
  deliberately unlike the pills Stories 3 and 4 hide: an anomaly-shaped count at zero is an
  answer to a question the operator is asking ("is anything waiting on me?"), whereas a level
  pill at `live` is an answer to a question nobody asked.
- **Simulated rows counted but not shown, or shown but not counted.** The count and the page
  it links to must be scoped by the same visibility setting, or one surface prints two
  numbers.
- **The level resolves to `unknown`.** Covered above: the pill stays. `unknown` is not `live`.
- **Bare chrome on error responses.** The chrome for a 404, a 405 or a schema refusal has no
  database context to count from. The pill must not appear there claiming a number it could
  not compute, and must not crash for want of one.
- **A failed item with no checkout.** The destination page's failed section renders items whose
  worktree is gone; the existing signal rendering already distinguishes "missing" from
  "unknown", and must keep doing so.
- **Singular and plural.** One item waiting reads differently from two, as the anomaly pill
  already handles.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The chrome payload carried by every view MUST include a count of work items in
  `awaiting_review`, `interrupted` or `failed`, as a single total rather than a figure per
  state.
- **FR-002**: That count MUST be scoped by the request's simulated-row visibility setting, so
  it agrees with the page it links to under either setting.
- **FR-003**: The chrome bar MUST render that count as one pill on every view, quiet at zero
  and warning above it, matching the anomaly pill's established behaviour.
- **FR-004**: That pill MUST be a link, carrying the current visibility setting, to a page
  that lists every item it counted.
- **FR-005**: The destination page MUST list `failed` items alongside `interrupted` and
  `awaiting_review`, each state in its own section, each section with its own honest empty
  text.
- **FR-006**: Each section on that page MUST offer the controls its items' state permits,
  determined by the existing single source of legal actions rather than by a second list.
- **FR-007**: Each section on that page MUST disclose simulated rows it withheld, in its empty
  text when it rendered nothing and in the page's withheld note when it rendered something —
  the disclosure discipline the page already follows for its two existing sections.
- **FR-008**: The navigation entry for that page MUST be named for all the states it lists.
- **FR-009**: The count MUST be present in the JSON form of every view, under the same
  scoping as the rendered pill.
- **FR-010**: The effect-level pill MUST render when the effective level is any value other
  than exactly `live`, and MUST NOT render when it is exactly `live`.
- **FR-011**: The effect-level pill MUST render when the level is `unknown`, including on the
  bare chrome of error responses.
- **FR-012**: The simulated-visibility pill MUST render when simulated rows are being
  included, and MUST NOT render when they are being hidden.
- **FR-013**: Every view that can withhold simulated rows MUST disclose the withholding on the
  view itself, with a route to reveal them. This MUST be verified before the visibility pill
  is removed.
- **FR-014**: The code comments that argue for the two behaviours being reversed MUST be
  replaced with the reasoning that overrules them, not deleted. The next reader must find out
  why the polarity is what it is, and that the earlier argument was considered.
- **FR-015**: At `live`, unpaused, with no anomalies and nothing waiting, with simulated rows
  hidden, the bar MUST consist of the daemon pill, the capacity pill, the order pill, the
  waiting count at zero and the anomaly count at zero, and nothing else. The two counts stay
  at zero because a count answers a question the operator is asking; the effect-level and
  visibility pills go because they answer one nobody asked.
- **FR-016**: The behaviour change MUST be reflected on the guide page covering the web
  interface.

### Key Entities

- **Waiting count**: a single non-negative integer on the chrome payload, being the number of
  work items in `awaiting_review`, `interrupted` or `failed` that the request's visibility
  setting would show. Machine-readable in the JSON body; rendered as one pill in HTML.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After exiting a session, the operator learns from the page already in front of
  them that an item is waiting on them — no navigation and no second surface required.
- **SC-002**: The number the pill states and the number of items its destination lists are
  equal under both visibility settings, in every case.
- **SC-003**: All three parked states — awaiting review, interrupted and failed — are
  reachable from any view in one click.
- **SC-004**: On a live, healthy, idle instance the chrome bar carries five pills, down from
  six, and every one of them is either a fact about the machine or a count answering a
  question the operator is asking.
- **SC-005**: No view can withhold a simulated row without saying so on the view itself.
- **SC-006**: The full unit test suite passes.

## Assumptions

- The destination for the pill is the existing interrupted page, grown a failed section,
  rather than a new page. Fewer moving parts wins under Principle I, and the page already
  holds two of the three states for exactly the reason a third belongs there: those items must
  be navigable at all.
- The page's URL is unchanged. The issue asks for the *navigation entry* to be renamed, not
  the route; changing the path would break bookmarks and the many generated links that already
  point at it for no gain.
- The pill's wording follows the sketch in the issue — a count and a short phrase, in the
  shape of the anomaly pill, with singular and plural handled.
- The count is taken from the same reading of the database as the rest of the chrome, so the
  pill, the page and the anomaly count all describe one instant.
- `done` and `abandoned` are terminal and are not waiting on anyone; `ready`, `dispatching`
  and `active` are the machine's to move. Neither group is counted.
- The existing per-view disclosure of withheld simulated rows is believed to be complete. It
  is to be verified, not assumed — FR-013 makes that verification a requirement rather than a
  hope.
