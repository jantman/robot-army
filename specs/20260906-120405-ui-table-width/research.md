# Phase 0 Research: UI Table Width

Everything below was measured in Chrome against pages produced by the real renderer, seeded with
three active items whose titles, worktree paths and branch names are the length the daemon actually
produces. The numbers are from those measurements, not from reasoning about CSS.

## The cause

One declaration:

```css
main { padding: 1rem; max-width: 60rem; margin: 0 auto; }
```

`60rem` is 960 pixels. On a 1920-pixel window `main` is 960 wide and centred, so the table inside it
is 928 pixels — 49% of the window, with 480 pixels of nothing on each side. Measured on the seeded
`/active` page, that forces every title to wrap over five or six lines and every timestamp over four,
which is exactly the screenshot attached to issue #148.

The same declaration governs `/queue`, `/cards`, the item detail page, the audit log, and the
anomalies and interrupted views, because there is one `main` and one stylesheet.

## Decision 1 — separate the prose measure from the page width

**Decision**: keep the 60rem measure, but apply it to prose rather than to the page. `main` gets a
much larger bound (`120rem`), and a single selector list caps the text-shaped elements inside it.

```css
:root { --measure: 60rem; --page: 120rem; }
main { max-width: var(--page); }
main p, main ul, main dl, main .banner, main .card, main .record, main .filters {
  max-width: var(--measure);
}
```

**Rationale**: 60rem was chosen for reading, and it is still right for reading. What was wrong was
applying a paragraph's limit to a grid. Splitting the two keeps every banner, note, audit record and
field list at the line length they have today while freeing the page around them.

`120rem` (1920 pixels) is the bound rather than `none`: it is exactly a full-size monitor, so on the
machine in the issue nothing is narrowed by it at all, and on a wider display a row's first and last
cells stay close enough to associate by eye.

**Alternatives considered**:

- *Widen `main` and cap nothing.* One line, and it puts banner prose on a 1900-pixel line. Rejected:
  it trades a table problem for a text problem, on the same pages.
- *A CSS grid on `main` with a full-bleed column, tables opting into it.* The idiomatic answer, and it
  fails here: the tables are not all direct children of the grid. The repositories table on `/queue`
  is nested one level deeper inside a wrapping `div`, so a rule keyed on child position would widen
  eight tables and miss the ninth.
- *Breaking the table out of the prose column with `width: 100vw` and negative margins.* Depth-
  independent, and wrong on a desktop: `100vw` includes the vertical scrollbar, so every table would
  be about 15 pixels wider than the space available and clipped by `body { overflow-x: hidden }`.

## Decision 2 — a table takes the width its content needs, not the width available

**Decision**: the existing per-table scroll container becomes shrink-to-fit.

```css
.scroll { width: fit-content; max-width: 100%; }
```

**Rationale**: `table { width: 100% }` means "fill the container", so widening the container alone
would stretch every table to the page width — including the two-column state-history table on the item
page, which would put six characters at the left edge and eleven at the right with 1600 pixels of
nothing between them. `fit-content` makes the container the width of the table's content, capped at
the space available, and `width: 100%` then resolves against that.

Measured on the item page at a 1920-pixel window: the two-column history table renders at 410 pixels
and the eight-column session-attempts table at 743, instead of both at 1888. On `/active` the
ten-column table renders at 1702 — the width its content actually needs, 90% of the usable page — with
every title on one line.

The container keeps `overflow-x: auto`, so the mobile guarantee is untouched: when the content needs
more than the space available, `max-width: 100%` caps the container and the table scrolls inside it
exactly as before.

**Alternatives considered**:

- *Leave `.scroll` full-width and stretch every table.* Simpler by one declaration, and it makes a
  two-column table worse than it is today. Rejected against FR-004.
- *Change `table { width: 100% }` to `width: auto`.* Achieves the same shrink-to-fit, but through the
  element that also has to grow past its container for the mobile scroll to work. Doing it on the
  container leaves the table's own rule — and therefore the overflow behaviour — untouched.

## Verification of the mobile guarantee

The concern with any width change is SC-013 from milestone 002: at a 390-pixel viewport, no horizontal
page scrolling and no text needing zoom. Desktop Chrome will not size a window below 500 pixels, so
the check was done by loading the rendered page into a 390-pixel iframe, twice, with and without the
change:

| Measurement at a 390px viewport | Today | With the change |
|---|---|---|
| Page scrolls horizontally | no | no |
| `main` width | 375 | 375 |
| Table container width | 343 | 343 |
| Table scrolls inside its container | yes | yes |
| Banner width | 343 | 343 |

Identical. Below the prose measure neither new bound can bind, so the change is an expansion into space
that was previously unused and nothing else.

## What is not changed

- `table { width: 100% }`, `th { white-space: nowrap }`, and `.scroll { overflow-x: auto }` stay as they
  are. They are what makes the mobile behaviour work.
- The header, nav and footer already span the window rather than the content column, and continue to.
- No markup changes. Every table in the interface is already wrapped in `.scroll` by `html.table()`, so
  the stylesheet reaches all nine of them and every table added later.
- No new asset, font, dependency, or script. The stylesheet stays a module constant.
