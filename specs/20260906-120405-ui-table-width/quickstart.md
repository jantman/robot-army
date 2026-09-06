# Quickstart: verifying the width change

## Prerequisites

```bash
uv sync
```

## The automated check

```bash
uv run pytest tests/unit/test_web_layout.py -v   # the width rules and the markup they act on
uv run pytest                                    # the whole suite must pass
```

`test_web_layout.py` asserts the [layout contract](./contracts/layout.md) in two registers: the rules
against `html.APP_CSS`, and the presence of a `div.scroll` around every table against pages rendered by
the real views. Every assertion in it fails against the pre-change stylesheet.

## Seeing it

Run the interface against your own database:

```bash
uv run robot-army serve          # then open the printed URL
```

Or, with no daemon running and no real data, render the pages to files and open them in a browser. The
seeded pages used while designing this change had three active items with realistic titles, worktree
paths and branch names — a page with one short title proves nothing, because at 60rem it already fits.

## What to look for on a full-size monitor

On `/active`:

- The table spans most of the window rather than a centred column roughly half its width.
- Every item title is on one line. Before the change they wrapped over five or six.
- Every timestamp is on one line. Before the change they wrapped over four.
- The banner text and any explanatory paragraphs are still at a comfortable reading length — they have
  not stretched to the window.

On `/queue`, `/cards` and an item's detail page:

- Every table has grown the same way.
- On the item page specifically, the two-column state-history table has *not* grown to fill the window —
  it stays at the width its two columns need. That is the shrink-to-fit rule working; a stretched
  version of that table would be worse than what is there today.
- The item's own field list is still at reading width.

Measured on a 1920-pixel window with the seeded pages: the `/active` table renders at 1702 pixels
against 928 before; `/queue`'s ready table at 1131 and its repositories table at 351; the item page's
history table at 410 and its session-attempts table at 743 — against 1888 each if the container were
left full-width. Prose sits at 960 against 928 before, a difference of one indent that nothing notices.

## What to look for on a phone

The guarantee at risk is milestone 002's SC-013: at a 390-pixel viewport, no horizontal page scrolling
and no text needing zoom.

- Open any view at 390 pixels wide. The page itself must not scroll sideways.
- Drag a wide table. It scrolls *inside its own box*; the header, nav and footer stay put.

Desktop Chrome will not size a window below about 500 pixels, so a real 390-pixel check needs either a
phone, the browser's device emulation, or the rendered page loaded into a 390-pixel iframe. Measured
that last way across all seven views, every number — content width, table container width, banner width,
and both scrolling behaviours — is identical before and after the change. That identity *is* the check: below the prose
measure neither new bound can bind, so nothing may move.
