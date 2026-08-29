# Feature Specification: The Web Interface Shows Its Work and Announces Non-Live Mode

**Feature Branch**: `009-web-non-live-visibility`

**Created**: 2026-08-29

**Status**: Draft

**Input**: User description: "issue #14 on this repo" — the web UI hides every row the daemon created below `live` and renders non-live mode as an unstyled pill

## User Scenarios & Testing *(mandatory)*

<!--
  Two defects, reported together because they compound. Story 1 makes the work visible;
  story 2 makes the mode unmistakable. Either is shippable alone; together they remove the
  reading "a working system that has found no work" — which is also what a broken daemon
  looks like.
-->

### User Story 1 - Seeing the work the daemon has actually done, below `live` (Priority: P1)

The maintainer runs the daemon at an effect level below `live` — `plan` is where a first run
starts — and opens the web interface on their phone. Every work item and every tracked card
created at that level is a simulated row. The interface withholds simulated rows unless asked,
and the only way to ask is an addition to the address that nothing on the page mentions. So
the active view says "Nothing is running.", the queue says "Nothing is ready.", and the cards
view says "Nothing on the board yet." — while the database holds four queued work items and
fifteen tracked cards.

Nothing below the rendering layer is wrong: asking by hand shows everything at once. But the
interface exists to be read from a phone, which is exactly the surface where the maintainer
cannot type an incantation or recall a flag, and its unaided answer is a flat "nothing".

After this change, an interface running below `live` shows those rows by default, because
below `live` they are the only rows there are. Withholding them remains reachable — as a
choice the operator makes, not as the state they are dropped into.

**Why this priority**: This is the half of the issue that destroys the interface's usefulness
rather than merely under-signalling. Delivered alone, the maintainer can read what the daemon
did from a phone, which is the entire stated purpose of the interface.

**Independent Test**: Point the interface at a database whose only work items and cards are
simulated, with the configured effect level below `live`, request each view with no query
parameters, and confirm every row appears and no view claims emptiness.

**Acceptance Scenarios**:

1. **Given** the effect level is below `live` and the database holds four simulated work
   items in `ready` and fifteen simulated tracked cards, **When** the maintainer opens the
   active, queue, interrupted and cards views without stating a preference, **Then** each view
   renders the rows it holds and none of them states that there is nothing to show.
2. **Given** the same state, **When** the maintainer explicitly asks a view to withhold
   simulated rows, **Then** they are withheld — the previous behaviour stays reachable as an
   explicit override in that direction.
3. **Given** the effect level is `live`, **When** the maintainer opens any view without
   stating a preference, **Then** simulated rows are withheld, exactly as today, because at
   `live` they are leftovers from earlier testing rather than the whole contents.
4. **Given** the effect level is `live`, **When** the maintainer explicitly asks a view to
   show simulated rows, **Then** they appear — the existing override in that direction is
   unchanged.
5. **Given** the maintainer has explicitly chosen to withhold or include simulated rows,
   **When** they follow a link to another view, submit an action, or the page refreshes
   itself, **Then** their choice is still in effect and is not silently replaced by the
   default for the current effect level.
6. **Given** any view is withholding rows it matched, **When** it renders, **Then** it states
   how many rows it withheld and how to show them, rather than presenting the visible subset
   as the whole.
7. **Given** the effect level is below `live` and the database holds no rows at all,
   **When** the maintainer opens any view, **Then** it says plainly that there is nothing to
   show and does not report withheld rows.

---

### User Story 2 - Being told, on every page, that none of this is real (Priority: P1)

The same maintainer reads a page full of work items, session states, issue links and card
movements. Below `live` none of it happened: no session was launched, nothing was written to
GitHub or Trello, no card moved, and the issue numbers on screen were invented — an item can
show as `linked` to an issue that does not exist. The only thing on the page that says so is
the text `effect level: plan`, rendered in the same weight as `order: oldest-first` and less
prominently than the capacity indicator beside it, and reading identically in structure to
`effect level: live`.

The interface already knows how to be emphatic when the page does not mean what it appears to
mean: a stopped daemon and an effect-level mismatch both get a full-width error banner. Being
below `live` belongs in that category and is broader than either, because it changes the
meaning of every value on the page rather than one of them.

After this change, every view below `live` carries a persistent banner saying, in the
operator's own terms, that the instance is set up for testing rather than real work and naming
what the displayed values are not. The level indicator itself is styled as an alarm below
`live` and stays calm at `live`.

**Why this priority**: Making the rows visible without saying they are simulated trades one
misreading for a worse one — a page full of convincing but fictional work. The two halves of
the issue have to ship together to be an improvement.

**Independent Test**: Render every view at each effect level and confirm the banner is present
and names the consequences at `plan`, `local` and `no-remote`, is absent at `live`, and that
the level indicator is visually distinguished as an alarm below `live` and not at `live`.

**Acceptance Scenarios**:

1. **Given** the effect level is `plan`, **When** the maintainer opens any view, **Then** a
   persistent banner appears in the same position and weight as the daemon-not-running banner,
   naming the current level and stating that no session is really launched, no issue or
   comment is really written, no card really moves, and the issue numbers shown are invented.
2. **Given** the effect level is `local` or `no-remote`, **When** the maintainer opens any
   view, **Then** the banner appears and describes the consequences that actually apply at
   that level, rather than repeating the `plan` text verbatim.
3. **Given** the effect level is `live`, **When** the maintainer opens any view, **Then** no
   such banner appears anywhere.
4. **Given** the effect level is below `live`, **When** the maintainer glances at the
   indicator strip, **Then** the level indicator is rendered with alarm emphasis, distinct
   from the neutral indicators beside it.
5. **Given** the effect level is `live`, **When** the maintainer glances at the indicator
   strip, **Then** the level indicator is calm — no alarm emphasis — so the emphasis stays
   meaningful when it does appear.
6. **Given** the daemon is not running, or the running daemon's effect level does not match
   the configured one, **When** those existing banners render below `live`, **Then** the
   non-live banner appears alongside them and neither suppresses the other.

---

### User Story 3 - Telling a simulated row from a real one at a glance (Priority: P2)

With simulated rows shown by default below `live`, and with a `live` instance able to show
leftover simulated rows on request, a table can hold both kinds at once. Today a simulated row
is marked with a single-character suffix beside its title — adequate when the operator had to
opt in and knew what they had asked for, thin when the rows arrive unrequested and the reader
did not choose to see them.

After this change, a simulated row is legible as simulated without close reading, wherever
rows are shown.

**Why this priority**: It matters most in the mixed case, which is real but not the reported
one; the banner already establishes the frame on a page where every row is simulated. Worth
doing with the rest, not worth blocking on.

**Independent Test**: Render a table holding both a real and a simulated row and confirm the
simulated one is distinguishable from across the room, not only on inspection.

**Acceptance Scenarios**:

1. **Given** a view holds both real and simulated rows, **When** it renders, **Then** each
   simulated row carries a marker that is visually distinct from the surrounding row text
   rather than a single punctuation character appended to it.
2. **Given** any view that shows rows — including the queue, the per-item detail view and the
   cards views — **When** a simulated row appears in it, **Then** it is marked, with no view
   omitting the marking.
3. **Given** a table of rows that are all simulated, **When** it renders, **Then** the marking
   is present but does not overwhelm the row content it sits beside.

---

### Edge Cases

- **The daemon and the configuration disagree about the level.** The interface already detects
  this and renders a mismatch banner. The banner and the level styling must be driven by a
  single, stated choice of which level is authoritative for this purpose, so the page cannot
  claim to be live while flagging itself as simulated.
- **A `live` instance with leftover simulated rows.** Default withholding is unchanged, but
  the view must disclose that it withheld them; requesting them shows them, marked, with no
  non-live banner, because the instance is live.
- **An explicit override that matches the default.** Asking to include below `live`, or to
  exclude at `live`, must behave identically to not asking, and must not produce a
  contradictory indicator.
- **An unintelligible statement of preference.** The interface must resolve to a defined state
  rather than an ambiguous one, and must not fail on a value a person mistyped.
- **Machine-readable responses.** A caller that requests a view's data rather than its page
  gets the same rows the page would show and can tell which rows are simulated, what was
  withheld, and what level the instance is at.
- **Zero rows below `live`.** A genuinely empty database must read as empty, not as
  "everything was withheld" — the two claims must never both appear.

## Requirements *(mandatory)*

### Functional Requirements

**Default visibility**

- **FR-001**: The web interface MUST determine whether to show simulated rows from the
  instance's effect level when the request expresses no preference: shown when the level is
  below `live`, withheld when the level is `live`.
- **FR-002**: The web interface MUST honour an explicit request-level preference in both
  directions, overriding the level-derived default.
- **FR-003**: An explicit preference MUST survive navigation between views, action submission
  and automatic page refresh, so that the operator's choice is not silently reverted.
- **FR-004**: An unrecognised or malformed preference value MUST resolve to the level-derived
  default without producing an error response.
- **FR-005**: The choice of default MUST NOT change which rows exist, which rows are eligible
  for dispatch, or how capacity is counted — it governs display only.

**Disclosure of what is withheld**

- **FR-006**: Every view that withholds rows it would otherwise have shown MUST state how many
  rows it withheld and how the reader can see them.
- **FR-007**: The withheld count MUST reflect only rows the view's own filters would have
  matched, so the number stated is the number revealed.
- **FR-008**: A view MUST NOT state that there is nothing to show when it is withholding rows;
  it MUST distinguish "nothing exists" from "nothing is being shown to you".
- **FR-009**: When nothing is withheld, no withheld-row disclosure MUST appear.

**Announcing non-live mode**

- **FR-010**: Every view MUST render a persistent notice, at the same prominence as the
  existing daemon-not-running notice, whenever the instance's effect level is below `live`.
- **FR-011**: The notice MUST name the current effect level.
- **FR-012**: The notice MUST state the consequences that apply at that level in operator
  terms — which of: no session is really launched, no issue or comment is really written, no
  card really moves, and the issue numbers displayed are invented rather than real.
- **FR-013**: The consequences stated MUST be those that actually hold at the current level,
  not a single message reused across all levels below `live`.
- **FR-014**: No such notice MUST appear when the effect level is `live`.
- **FR-015**: The notice MUST NOT be dismissible and MUST NOT be suppressed by the presence of
  any other banner, nor suppress any other banner.

**Level emphasis**

- **FR-016**: The effect-level indicator MUST be rendered with alarm emphasis, visually
  distinct from neutral indicators beside it, whenever the level is below `live`.
- **FR-017**: The effect-level indicator MUST be rendered without alarm emphasis when the
  level is `live`.
- **FR-018**: The interface MUST use a single, documented rule for which level — the
  configured one or the running daemon's — drives the notice and the emphasis, and MUST NOT
  let the notice and the emphasis disagree with each other.

**Marking simulated rows**

- **FR-019**: Every simulated row MUST carry a marker wherever it is displayed, in every view
  that displays rows.
- **FR-020**: The marker MUST be visually distinguishable from the row's own text, rather than
  relying on an appended punctuation character alone.

**Machine-readable parity**

- **FR-021**: A machine-readable response for a view MUST contain the same rows the rendered
  page would contain for the same request.
- **FR-022**: A machine-readable response MUST state the effect level, whether simulated rows
  were included, and how many matching rows were withheld.

**Accountability**

- **FR-023**: The change MUST NOT alter which requests are audited or what is recorded for
  them; display defaults are a rendering concern and introduce no new state-changing action.

### Key Entities *(include if feature involves data)*

- **Effect level**: The instance's graduated mode — `plan`, `local`, `no-remote`, `live`.
  Every level but `live` produces simulated rows; the level is what the notice, the emphasis
  and the visibility default are all derived from.
- **Simulated row**: A work item, session, tracked card or worktree record created below
  `live`. It is a genuine database row describing an action that was planned but not
  performed.
- **Display preference**: The operator's optional, per-request statement of whether simulated
  rows should be shown, distinct from the level-derived default it overrides.
- **Withheld count**: For a given view and its filters, the number of matching rows the
  current preference is hiding — the figure that makes a partial view legible as partial.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the daemon at `plan` and work tracked in the database, an operator opening
  the interface on a phone sees every tracked work item and card without typing anything,
  editing a URL, or consulting documentation.
- **SC-002**: Across the four main views at a level below `live`, the number of views that
  claim there is nothing to show while rows exist drops from four to zero.
- **SC-003**: An operator shown a single screenshot of any view, with no other context, can
  state correctly whether the instance is doing real work — in under five seconds and without
  scrolling.
- **SC-004**: An operator reading a page below `live` can name, from the page alone, at least
  three specific things the system did not actually do.
- **SC-005**: Any view that is showing fewer rows than it matched says so on the page; zero
  views present a partial set as complete.
- **SC-006**: In a table mixing real and simulated rows, an operator correctly identifies
  which rows are simulated on first reading, without hovering, clicking or consulting a key.
- **SC-007**: The previous behaviour — withholding simulated rows below `live` — remains
  reachable in a single request by an operator who wants it.
- **SC-008**: No change in which rows the dispatcher considers or which items occupy capacity,
  measured by the daemon's selection being identical before and after the change for the same
  database.

## Assumptions

- The polarity is settled and not reopened: the alarm goes on non-live and `live` stays calm,
  per the issue's own resolution. `live` is the expected operating state, and decorating it
  with a warning would train the operator to ignore the one place the level is shown.
- The terminal command's default remains unchanged. Its exclusion default is deliberate and
  milestone 008 already made it honest by disclosing what it withholds; this feature changes
  only the web interface, where the reader has no other way to ask.
- Below `live` the simulated rows *are* the contents, so showing them is showing the truth.
  The concern that motivated the exclusion default — simulated rows being mistaken for real
  history — is met by marking and by the banner, not by hiding rows from the only surface with
  no other way to request them.
- The withheld counts this feature displays come from the figures milestone 008 already
  computes for the underlying operations; no new counting rule is introduced.
- Existing banner and indicator placement, the auto-refresh behaviour, and the closed set of
  action banners are reused as-is; this feature adds to them rather than restructuring them.
- The audience is the single maintainer on their own machine, reading from a phone or a
  desktop browser. No accessibility target beyond "legible without close reading" is claimed,
  and colour is not the sole carrier of the non-live signal — the banner text carries it.
- "Below `live`" covers `plan`, `local` and `no-remote` uniformly for the purposes of the
  visibility default and the emphasis; only the wording of the stated consequences varies by
  level.
