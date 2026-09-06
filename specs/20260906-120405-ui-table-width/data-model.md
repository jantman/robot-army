# Phase 1 Data Model: UI Table Width

This feature stores nothing, reads no table, and adds no field. There is no data model in the usual
sense. What it does have is two named regions of the page, which the spec refers to and the tests
assert against; they are recorded here so the words mean one thing in the spec, the contract and the
code.

## Region: the content area

**What it is**: the box a view's body renders into — everything between the header and the footer,
inside the page's side margins.

**Today**: one width for everything it contains, 60rem, centred in the window.

**Afterwards**: bounded by `--page` (120rem) instead. Below that it is the window minus the page
margins; above it, it stops growing and stays centred.

**Contains**: the chrome pills, any banners, and the view's body.

## Region: the prose measure

**What it is**: not a box but a limit — the longest line any text-shaped element is allowed to be, so
that reading it does not require moving the eye across the whole window.

**Value**: `--measure`, 60rem — the width the whole page has today, kept for the elements it was
chosen for.

**Applies to**: paragraphs, lists, definition lists (including the item page's field list), banners,
cards, audit records, and filter rows.

**Does not apply to**: headings, which are short; the chrome pill row, which wraps and reads better in
fewer lines; and anything inside a table container.

## Region: the table container

**What it is**: the `div.scroll` that `html.table()` wraps around every table it builds. It is the
element that scrolls when a table is wider than the space available, and the reason a phone can show an
eight-column table without the page scrolling sideways.

**Today**: a full-width block; the table inside fills it and grows past it when the content demands.

**Afterwards**: shrink-to-fit, capped at the space available. A table narrower than the content area
gets a container the width of its content; a table wider than the content area gets a container the
width of the content area, and scrolls inside it exactly as it does now.

**Invariant**: every table in the interface is inside one of these. Nothing builds a `<table>` outside
`html.table()`, and a test asserts it, because the whole layout rests on that being true.

## Relationships

```text
main  (content area, ≤ --page)
├── .chrome            unbounded — pills wrap
├── .banner            ≤ --measure
└── #content
    ├── h1 / h2        unbounded — short
    ├── p / ul / dl    ≤ --measure
    ├── .card          ≤ --measure
    ├── .record        ≤ --measure
    └── div.scroll     ≤ content area, shrink-to-fit
        └── table      width: 100% of its container, growing past it when content demands
```

The nesting depth of `div.scroll` under `#content` varies — the repositories table on `/queue` sits one
level deeper inside a wrapping `div` — which is why every rule above is written to be depth-independent.
