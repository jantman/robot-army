# Feature Specification: UI Table Width

**Feature Branch**: `robot-army/issue-148-ui-table-width`

**Created**: 2026-09-06

**Status**: Draft

**Input**: GitHub issue [#148](https://github.com/jantman/robot-army/issues/148) — "The `/active` page of
the robot-army UI has an active item table that only makes use of about half of the screen width on a
full-size monitor. Please optimize this so it makes best use of available screen space on either a
full-size monitor or mobile. Do the same for any other tables in the UI."

## Context

Every view in the web interface is laid out inside one fixed-width column. On a 1920-pixel monitor that
column occupies roughly the left half of the window, and the ten-column table on `/active` is squeezed
into it: titles wrap to three lines, checkout paths wrap mid-path, and the operator scrolls a table
sideways inside a window with 900 pixels of unused space to its right. The same column governs
`/queue` (four tables), `/cards` (two tables), and the item detail page (two tables).

The column width was chosen for prose. It is still right for prose: the banners, the paragraphs
explaining what a page means, and the audit-record lines are all easier to read at a bounded measure
than at full window width. What is wrong is that a table — a grid whose whole job is to put many
values side by side — is bound by the same limit as a paragraph.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reading the active table on a desktop monitor (Priority: P1)

The operator has the `/active` page open on a full-size monitor while work runs. They want to see, at a
glance, which items are running, how long each has been going, what phase it is in, and which checkout
it lives in — without the rows wrapping into paragraphs and without dragging a scrollbar inside the
table while half the window sits empty.

**Why this priority**: It is the page the issue names, the page the daemon is watched from, and the one
with the widest table in the interface. Fixing it alone resolves the reported problem.

**Independent Test**: Open `/active` with several active items on a 1920-pixel-wide window and confirm
the table spans the usable window width and that no row needs sideways scrolling to be read.

**Acceptance Scenarios**:

1. **Given** a 1920-pixel-wide window and three active items, **When** the operator opens `/active`,
   **Then** the table occupies the full width of the page's content area rather than a fixed column
   roughly half the window wide, and every column is visible without scrolling the table sideways.
2. **Given** the same window, **When** the operator reads the explanatory prose, banners and paragraph
   text on that page, **Then** those still render at a bounded, readable line length rather than
   stretching the whole window width.
3. **Given** a window narrower than the bounded prose measure, **When** any page is opened, **Then**
   tables and prose occupy the same width they do today — the change is only ever an expansion into
   space that was previously unused.

---

### User Story 2 - The same on every other table (Priority: P1)

The operator moves between `/queue`, `/cards`, and an item's detail page. Each carries tables of the
same shape as `/active`'s, and each is squeezed by the same column.

**Why this priority**: The issue asks for it explicitly, and a fix applied to one page only would leave
the interface inconsistent — the same table shape reading two different widths depending on the route.

**Independent Test**: Visit each route carrying a table on a wide window and confirm every table uses
the same full content width as `/active`'s.

**Acceptance Scenarios**:

1. **Given** a wide window, **When** the operator opens `/queue`, **Then** its ready, repositories,
   dispatching, and blocked tables each span the full content width.
2. **Given** a wide window, **When** the operator opens `/cards`, **Then** both the awaiting-information
   and the main card tables span the full content width.
3. **Given** a wide window, **When** the operator opens an item's detail page, **Then** its state
   history and session attempts tables span the full content width, while the item's own field list and
   surrounding prose keep their readable measure.

---

### User Story 3 - Nothing regresses on a phone (Priority: P1)

The operator checks the same pages from a phone. The interface was built phone-first, and the phone is
where the daemon is actually watched from when away from the desk.

**Why this priority**: The existing guarantee — a 390-pixel viewport renders every view with no
horizontal page scrolling and no text needing zoom — is the one thing a width change could plausibly
break, and breaking it would trade the reported annoyance for a worse one.

**Independent Test**: Render every view at a 390-pixel viewport and confirm the page itself does not
scroll horizontally and that each wide table still scrolls within its own container.

**Acceptance Scenarios**:

1. **Given** a 390-pixel-wide viewport, **When** any view is opened, **Then** the page does not scroll
   horizontally and no text requires zooming to read.
2. **Given** a 390-pixel-wide viewport and a table too wide to fit, **When** the operator drags the
   table sideways, **Then** the table scrolls inside its own container as it does today, and the header,
   navigation and footer stay put.

---

### Edge Cases

- **A very wide monitor.** At 3440 pixels an unbounded table would stretch a nine-word title across a
  metre of glass and put a row's first and last cells too far apart to associate by eye. The content
  area therefore has an upper bound generous enough that no table in the interface is squeezed by it,
  not an unlimited one.
- **A table with two columns.** The state-history table has two. Filling a wide window with two columns
  of six characters each is not an improvement; a table narrower than the space available must be free
  to stay narrow rather than being stretched to fill it.
- **A page with both a table and prose.** The item detail page and `/queue` carry both. Prose keeps its
  readable measure on the same page where a table uses the full width.
- **Intermediate widths.** Between the prose measure and the widest table there is a range of window
  sizes — a laptop, a tablet, a half-screen window — where the table is wider than prose but narrower
  than the window. Growth must be continuous across that range, not a jump at one breakpoint.
- **Scripting disabled.** The layout is decided by the stylesheet alone; nothing about the width may
  depend on the refresh script running.
- **The dead-end pages.** The 404, 405 and refusal pages render with no database context. They carry no
  table, and their prose must keep the same measure as everywhere else.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every table in the web interface MUST be able to use the full width of the browser window
  minus the page's normal side margins, rather than being confined to the prose column width.
- **FR-002**: Text that is read as prose — page paragraphs, banners, explanatory notes, audit-record
  detail lines, and key/value field lists — MUST keep a bounded line length that does not grow with the
  window.
- **FR-003**: The content area MUST have an upper bound wide enough that no table currently in the
  interface is narrowed by it on a full-size monitor.
- **FR-004**: A table narrower than the space available MUST NOT be stretched to fill it; it MUST take
  the width its content needs, up to the width available.
- **FR-005**: Every existing table MUST keep its own horizontal scroll container, so that a table wider
  than the viewport scrolls within itself and never causes the page to scroll horizontally.
- **FR-006**: At viewport widths at or below the current prose column width, every view MUST render at
  the width it renders at today — the change MUST only ever add usable width, never remove it.
- **FR-007**: Width MUST grow continuously with the window between the prose measure and the upper
  bound, with no width at which the layout jumps.
- **FR-008**: The change MUST be carried by the stylesheet, so it applies to every table already in the
  interface and every table added later without each one opting in.
- **FR-009**: The page's fixed elements — the header, navigation, and footer — MUST stay aligned with
  the content area at every width.
- **FR-010**: The interface MUST continue to fetch nothing from a third-party host; the layout MUST NOT
  introduce a web font, a CDN stylesheet, or any other external asset.

### Key Entities

- **Content area**: the region between the page margins into which a view's body renders. Today one
  fixed width for everything; afterwards, one width for prose and a wider one for tables.
- **Table container**: the existing per-table wrapper that scrolls horizontally when its table exceeds
  the width available. It is the element that gains the wider bound.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a 1920-pixel-wide window, the `/active` table occupies at least 90% of the usable page
  width, compared with roughly 50% today.
- **SC-002**: On a 1920-pixel-wide window, every table on `/active`, `/queue`, `/cards`, and an item's
  detail page is fully readable with zero horizontal scrolling of the table.
- **SC-003**: On a 1920-pixel-wide window, no line of prose on any view exceeds a readable measure of
  about 100 characters.
- **SC-004**: On a 390-pixel-wide viewport, every view renders with zero horizontal scrolling of the
  page and no text requiring zoom — unchanged from today.
- **SC-005**: At every viewport width at or below the current prose column width, each view's rendered
  width is identical to today's.
- **SC-006**: The whole existing test suite passes, and the new behaviour is covered by tests that fail
  against the current stylesheet.

## Assumptions

- The prose column width in use today is the right measure for prose and is kept as-is; this feature
  widens tables rather than re-deciding typography.
- "Best use of available screen space" means removing an artificial limit that leaves half the window
  empty, not filling every pixel: an upper bound that no current table reaches still satisfies the
  request while keeping a row scannable on an ultrawide monitor.
- The audit log, anomalies, and interrupted views render as record lists and cards rather than tables.
  They are prose-shaped and keep the prose measure; only their enclosing page chrome is touched, and
  only if a shared rule requires it.
- No new dependency, asset, or client-side scripting is needed; the interface's existing
  server-rendered, offline-capable, no-external-asset shape is preserved.
- Verification of rendered pixel widths is by reading the generated markup and stylesheet plus a manual
  look at a real browser; the automated tests assert the rules that produce those widths.
