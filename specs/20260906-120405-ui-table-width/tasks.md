---

description: "Task list for UI Table Width"
---

# Tasks: UI Table Width

**Input**: Design documents from `specs/20260906-120405-ui-table-width/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/layout.md](./contracts/layout.md),
[quickstart.md](./quickstart.md)

**Tests**: included and not optional. The constitution requires unit tests for every new or changed
unit of behaviour, and the whole suite must pass before the feature is complete.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — a different file, no dependency on an incomplete task
- **[Story]**: the user story the task serves (US1, US2, US3)

## Path Conventions

Single project. `src/robot_army/`, `tests/unit/`, `docs/guide/` at the repository root.

## A note on story independence

The three stories in the spec are all P1 and are delivered by the same four declarations in one
stylesheet constant. They are not three separable slices of code, and pretending otherwise would produce
a task list that lies about its own shape. What each story does own is **its own verification**: US1 is
the desktop width on `/active`, US2 is that the same thing happened on the other eight tables, and US3 is
that nothing moved at 390 pixels. The phases below are therefore organised by what is being proven, and
each ends at a checkpoint that can be run and judged on its own.

---

## Phase 1: Setup

**Purpose**: be able to see the problem before changing anything, so the change can be judged against
something real rather than against a description of it.

- [ ] T001 Confirm the baseline: run `uv run pytest` and record that the suite passes before any edit
- [ ] T002 Read `APP_CSS` in `src/robot_army/web/html.py` end to end, noting the four declarations the
      plan touches — `main`, `.scroll`, `table`, and the `:root` custom properties — and the comments
      that explain why `.scroll` and `th { white-space: nowrap }` exist
- [ ] T003 Render the current pages to files for a before/after comparison: seed three active items with
      realistic titles, worktree paths and branch names through the `web` and `conn` fixtures, and write
      `/active`, `/queue`, `/cards` and an item page to a scratch directory with the stylesheet inlined

**Checkpoint**: the problem in issue #148 is reproduced locally at a 1920-pixel window — the `/active`
table about half the width of the screen, titles wrapping over five or six lines.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the two width values everything else refers to. Nothing in Phase 3 or later can be written
without them.

**⚠️ CRITICAL**: no story phase can begin until this phase is complete.

- [ ] T004 Add `--measure: 60rem` and `--page: 120rem` to the `:root` block in `APP_CSS` in
      `src/robot_army/web/html.py`, each with a comment saying what it is for: the measure is the line
      length prose is read at and is today's whole-page width; the page bound is the widest the content
      area grows to, chosen as exactly a full-size monitor so nothing on the machine in the issue is
      narrowed by it
- [ ] T005 Add the two values to the dark and light palettes correctly — they are lengths, not colours,
      so they belong in the base `:root` block only and must NOT be repeated in the
      `prefers-color-scheme: light` override; confirm the light-scheme block is untouched

**Checkpoint**: both custom properties exist, differ, and are documented in place. Nothing renders
differently yet.

---

## Phase 3: User Story 1 — Reading the active table on a desktop monitor (Priority: P1) 🎯 MVP

**Goal**: `/active` uses the window. Every title on one line, every timestamp on one line, prose still at
a readable measure.

**Independent Test**: open the rendered `/active` page at a 1920-pixel window; the table renders at the
width its content needs — about 1700 pixels against 928 before — and no title wraps.

### Tests for User Story 1

> Write these first and confirm each one FAILS against the current stylesheet. A test that passes before
> the change is testing nothing.

- [ ] T006 [US1] Create `tests/unit/test_web_layout.py` with a module docstring explaining why the
      stylesheet is asserted as a string: it is a module constant with no other seam, and the rules in
      it are the entire feature
- [ ] T007 [US1] In `tests/unit/test_web_layout.py`, assert against `html.APP_CSS` that the prose measure
      and the page bound both exist as `:root` custom properties and are different values — one value
      wearing two names is the bug this feature fixes
- [ ] T008 [US1] In `tests/unit/test_web_layout.py`, assert that `main` is bounded by the page value and
      not by the measure, naming in the failure message what a regression to `max-width: 60rem` would
      cost: every table back to half a window
- [ ] T009 [US1] In `tests/unit/test_web_layout.py`, assert that the prose selector list caps paragraphs,
      lists, definition lists, banners, cards, audit records and filter rows at the measure, and that it
      is written as a descendant selector so it holds at any nesting depth
- [ ] T010 [US1] Run the three new tests and confirm all three fail against the unmodified stylesheet

### Implementation for User Story 1

- [ ] T011 [US1] Change `main` in `APP_CSS` in `src/robot_army/web/html.py` from `max-width: 60rem` to
      `max-width: var(--page)`, keeping its padding and centring, and replace the existing comment with
      one that says why the page bound and the prose measure are two different things
- [ ] T012 [US1] Add the prose rule to `APP_CSS` — `main p, main ul, main dl, main .banner, main .card,
      main .record, main .filters { max-width: var(--measure); }` — with a comment naming what is
      deliberately absent from the list and why: headings, because they are short, and the chrome pill
      row, because it wraps and reads better in fewer lines
- [ ] T013 [US1] Run `uv run pytest tests/unit/test_web_layout.py` and confirm the three tests now pass
- [ ] T014 [US1] Re-render `/active` and compare against the Phase 1 capture at a 1920-pixel window:
      confirm the table spans most of the window, every title is on one line, every timestamp is on one
      line, and the banner and any explanatory paragraphs have *not* stretched to the window

**Checkpoint**: the reported problem is fixed on the page that reported it, and prose is still readable.

---

## Phase 4: User Story 2 — The same on every other table (Priority: P1)

**Goal**: the other eight tables grew the same way, and a two-column table is not stretched across the
window to achieve it.

**Independent Test**: open `/queue`, `/cards` and an item page at a 1920-pixel window; every table uses
the width its content needs, and the item page's two-column history table stays narrow.

### Tests for User Story 2

- [ ] T015 [P] [US2] In `tests/unit/test_web_layout.py`, assert that `.scroll` is shrink-to-fit —
      `width: fit-content` and `max-width: 100%` — so a table takes the width its content needs rather
      than the width it is given
- [ ] T016 [P] [US2] In `tests/unit/test_web_layout.py`, assert that `.scroll` still carries
      `overflow-x: auto` and that `table` still carries `width: 100%`, with a comment in the test saying
      why these two are asserted *here*, alongside the change that could tempt someone to remove them:
      together they are what makes a wide table scroll inside itself instead of compressing
- [ ] T017 [US2] In `tests/unit/test_web_layout.py`, assert against rendered markup — through the `web`
      fixture, with rows seeded so the tables are not empty — that every `<table>` on `/active`,
      `/queue`, `/cards` and an item page is inside a `div.scroll`. This is the assertion that catches a
      future page building a table by hand and escaping every rule above
- [ ] T018 [US2] Run the new tests and confirm the two stylesheet assertions fail against the
      unmodified `.scroll` rule; note that T017 is expected to pass already, because `html.table()`
      already wraps every table, and record in the test docstring that it is a guard against future
      drift rather than a test of this change

### Implementation for User Story 2

- [ ] T019 [US2] Add `width: fit-content; max-width: 100%;` to the `.scroll` rule in `APP_CSS` in
      `src/robot_army/web/html.py`, keeping `overflow-x: auto` and `-webkit-overflow-scrolling: touch`
      exactly as they are, and extend the comment to say what shrink-to-fit buys: a two-column table
      that is not stretched across a metre of glass, and what it must not cost — the phone's scroll
- [ ] T020 [US2] Run `uv run pytest tests/unit/test_web_layout.py` and confirm every test passes
- [ ] T021 [US2] Re-render `/queue`, `/cards` and an item page and compare against the Phase 1 capture:
      confirm each table grew, and specifically that the item page's two-column state-history table did
      *not* grow to fill the window
- [ ] T022 [US2] Check the `/queue` repositories table in particular — it is the one nested a level
      deeper than the others, inside a wrapping `div`, and it is the table that would have been missed
      by the grid design research rejected

**Checkpoint**: all nine tables use the space they need; none is stretched into space it does not.

---

## Phase 5: User Story 3 — Nothing regresses on a phone (Priority: P1)

**Goal**: the 390-pixel guarantee is exactly what it was — proven, not assumed.

**Independent Test**: at a 390-pixel viewport, the page does not scroll horizontally, a wide table
scrolls inside its own container, and every measured width matches the pre-change value.

### Tests for User Story 3

- [ ] T023 [US3] In `tests/unit/test_web_layout.py`, assert that no rule introduced by this feature can
      bind below the prose measure: `main`'s bound and the prose cap are both maxima, and `.scroll`'s
      `max-width: 100%` cannot exceed the container. State the reasoning in the test docstring, because
      this is the assertion standing in for a pixel measurement a unit test cannot take
- [ ] T024 [US3] In `tests/unit/test_web_layout.py`, assert that `th { white-space: nowrap }` survives —
      it is what forces a wide table past its container so the container scrolls, and removing it would
      break the phone silently while every other test still passed
- [ ] T025 [US3] Run the whole layout module and confirm the results are as expected

### Verification for User Story 3

- [ ] T026 [US3] Verify in a real browser at a genuine 390-pixel viewport, not a resized desktop window —
      desktop Chrome will not size below about 500 pixels, so use device emulation or load the rendered
      page into a 390-pixel iframe
- [ ] T027 [US3] Measure with and without the change and confirm every number is identical: page
      horizontal scroll, content width, table container width, banner width, and whether the table
      scrolls inside its container. Identity is the pass condition — below the measure nothing may move
- [ ] T028 [US3] Check a wide table by dragging it at 390 pixels: it scrolls inside its own box while the
      header, nav and footer stay put

**Checkpoint**: SC-013 from milestone 002 holds, verified rather than asserted.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T029 Update `docs/guide/operating.md` — the guide page for the web interface, per CLAUDE.md §2 —
      with a short note on how the views use the window: tables take the width their content needs up to
      the width of a full-size monitor, prose stays at a readable measure, and a table too wide for the
      viewport scrolls inside itself rather than scrolling the page
- [ ] T030 [P] Confirm no configuration key changed, so `exampleconfig.py` and
      `share/config.example.toml` need no regeneration — and confirm this by running
      `uv run pytest tests/unit/test_example_config_drift.py` rather than by reasoning about it
- [ ] T031 [P] Confirm `README.md` is untouched and still under its 150-line limit
- [ ] T032 Delete the scratch render helper from Phase 1 if it was added under `tests/`; it is a
      development aid, not a test, and must not be committed
- [ ] T033 Run the full suite: `uv run pytest`. It must pass
- [ ] T034 Run `uv run ruff check` and `uv run ruff format --check` if configured, and fix anything raised
- [ ] T035 Walk [quickstart.md](./quickstart.md) end to end as written, on both viewports, and correct it
      if any step or number no longer matches what the code does
- [ ] T036 Re-read the [layout contract](./contracts/layout.md) against the final stylesheet and confirm
      all seven of its statements hold

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no dependencies. Its output — the before capture — is what Phases 3, 4 and 5
  compare against, so skipping it costs the ability to judge the result.
- **Phase 2 (Foundational)**: depends on Phase 1. **Blocks every story phase**: the two custom
  properties are referenced by every rule that follows.
- **Phase 3 (US1)**: depends on Phase 2.
- **Phase 4 (US2)**: depends on Phase 2. Independent of Phase 3 in code — it touches `.scroll` while US1
  touches `main` and the prose rule — but its visual checkpoint is only meaningful once US1 has widened
  the content area, so run it after.
- **Phase 5 (US3)**: depends on Phases 3 and 4, because it verifies that neither of them moved anything
  at 390 pixels. Running it earlier would prove nothing.
- **Phase 6 (Polish)**: depends on all three story phases.

### Within Each Story

- Tests before implementation, and each test confirmed failing first. T018 is the stated exception and
  says so.
- Stylesheet edit → unit tests pass → browser check. The browser check is last because it is the slowest
  and the one that catches what a string assertion cannot.

### Parallel Opportunities

- T015 and T016 are both new assertions in the same new file but independent of each other's subject.
- T030 and T031 touch nothing and can run alongside anything in Phase 6.
- Everything else is sequential: this feature is four declarations in one constant, and two people
  editing `APP_CSS` at once would conflict on every task.

---

## Implementation Strategy

### MVP

Phases 1, 2 and 3. That is the reported problem fixed on the page that reported it, with tests. It is a
coherent stopping point.

### Incremental Delivery

1. Phase 1 + 2 → the values exist, nothing renders differently
2. Phase 3 → `/active` fixed (MVP) — stop and look at it
3. Phase 4 → the other eight tables, and the shrink-to-fit rule that keeps narrow tables narrow
4. Phase 5 → the phone proven unchanged
5. Phase 6 → documentation, full suite, contract re-read

### Not a parallel-team feature

One stylesheet constant, four declarations. Splitting it across people would cost more in conflicts than
it saved in time.

---

## Notes

- Commit after each phase, with a message saying why — atomic commits, per the constitution.
- The single highest-value habit in this feature is confirming each test fails first. Every assertion
  here is about a string; a typo in a selector produces a test that passes against everything and
  protects nothing.
- Do not "improve" `table { width: 100% }`, `th { white-space: nowrap }`, or `.scroll { overflow-x: auto }`
  while in the neighbourhood. All three are load-bearing for the phone, and the tests in Phase 4 and 5
  exist to catch exactly that temptation.
