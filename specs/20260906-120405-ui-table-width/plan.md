# Implementation Plan: UI Table Width

**Branch**: `robot-army/issue-148-ui-table-width` | **Date**: 2026-09-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260906-120405-ui-table-width/spec.md`

## Summary

Every view is laid out inside `main { max-width: 60rem }`, a limit chosen for prose and applied to
tables. Split it in two: `main` gets a page-sized bound, one selector list keeps the prose at the
measure it has today, and the per-table scroll container becomes shrink-to-fit so each table takes the
width its content needs rather than the width it is given. Four declarations in `APP_CSS`, no markup
change, no new file in `src/`.

The whole change lands in `robot_army/web/html.py`'s `APP_CSS` constant, which is the one place that
decides layout for all nine tables and every table added later.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none added. The stylesheet is a module constant in
`src/robot_army/web/html.py`; the interface fetches nothing from a third-party host.

**Storage**: not touched. This feature reads and writes nothing.

**Testing**: pytest (`uv run pytest`). The stylesheet is an importable string, so the rules are
asserted directly; the rendered markup is asserted through the existing `web` harness.

**Target Platform**: the web interface served by the daemon on one Linux machine, read from a desktop
browser and from a phone.

**Project Type**: single project — `src/robot_army/`, `tests/unit/`.

**Performance Goals**: none. The stylesheet grows by about six lines; it is already served with a
content-hashed URL and cached for an hour.

**Constraints**: at a 390-pixel viewport the page must not scroll horizontally and no text may require
zoom (milestone 002 SC-013); no web font, CDN stylesheet, or other external asset (SC-009); every page
must remain correct with scripting disabled (milestone 002 R2).

**Scale/Scope**: nine tables across five routes, one stylesheet constant, one new test module.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | Passes. Four CSS declarations and two custom properties. No new module, no new configuration key, no abstraction. The two designs considered that had more moving parts — a grid layout on `main`, and a viewport-unit breakout on each table — are recorded in [research.md](./research.md) with the measurements that rejected them. |
| **II. Single-User, Local-First** | Passes. No account, no role, no network dependency. Nothing is fetched from outside the machine; the stylesheet remains a module constant served from a fixed route. |
| **III. Total Accountability** | **No action to log.** This feature changes no state outside the running process: it writes no file, runs no command, makes no network call, and sends no notification. Rendering a page is already a read-only operation that the audit log deliberately does not record, and this change adds nothing to it. Enumerated here as the constitution requires rather than left implicit. |
| **IV. Interruption Tolerance** | **Not applicable, and stated rather than assumed.** There is no persistent write to make atomic and no long-running work to checkpoint. Killed halfway through, this feature leaves nothing behind: the stylesheet is a constant compiled into the process, so a restart serves either the old bytes or the new ones and never a half-written file. The content-hashed asset URL means a browser holding the old stylesheet fetches the new one on its next page load rather than after the cache expires. |
| **V. Public Code, Unsupported Project** | Passes. No credential, no personal data, no hostname. No public API is being kept stable — the stylesheet is internal and may change freely. |
| **Development Workflow** | Unit tests are required and are the first tasks: the rules are asserted against `APP_CSS`, and the rendered markup through the existing harness. The full suite must pass. |

**Documentation obligation** (CLAUDE.md §2): this changes the web interface, so
[`docs/guide/operating.md`](../../docs/guide/operating.md) is updated. No configuration key changes, so
`exampleconfig.py` and `share/config.example.toml` are untouched.

**Result**: no violation. The Complexity Tracking table is empty and has been removed.

## Project Structure

### Documentation (this feature)

```text
specs/20260906-120405-ui-table-width/
├── plan.md              # This file
├── spec.md              # Phase -1 output (/speckit-specify)
├── research.md          # Phase 0 output — the measurements that chose the design
├── data-model.md        # Phase 1 output — the layout regions, since there is no data
├── quickstart.md        # Phase 1 output — how to see and verify the change
├── contracts/
│   └── layout.md        # Phase 1 output — the stylesheet contract the tests assert
└── checklists/
    └── requirements.md  # Spec quality checklist
```

### Source Code (repository root)

```text
src/robot_army/web/
├── html.py              # APP_CSS — the only file this feature changes
├── pages.py             # unchanged; every table already goes through html.table()
└── server.py            # unchanged

tests/unit/
└── test_web_layout.py   # new — the width rules and the markup they act on

docs/guide/
└── operating.md         # the web-interface page, per CLAUDE.md §2
```

**Structure Decision**: the existing single-project layout. The feature adds one test module and edits
one constant; nothing else moves.

## Phase 0 — Research

Complete. See [research.md](./research.md). Two decisions, both measured in a browser against pages
from the real renderer:

1. Separate the prose measure from the page width — `--measure: 60rem` for text, `--page: 120rem` for
   the content area — rather than widening everything or introducing a grid the nested tables would
   escape.
2. Make `.scroll` shrink-to-fit, so a table takes the width its content needs and the two-column
   history table stays narrow.

The 390-pixel guarantee was verified by loading the rendered page into a 390-pixel iframe with and
without the change: every measurement identical.

No NEEDS CLARIFICATION markers remain.

## Phase 1 — Design

### The change

In `APP_CSS`:

```css
:root {
  ...
  --measure: 60rem;   /* the line length prose is read at — today's page width */
  --page: 120rem;     /* the widest the content area grows to — a full-size monitor */
}

main { padding: 1rem; max-width: var(--page); margin: 0 auto; }

main p, main ul, main dl, main .banner, main .card, main .record, main .filters {
  max-width: var(--measure);
}

.scroll {
  overflow-x: auto; -webkit-overflow-scrolling: touch;
  width: fit-content; max-width: 100%;
}
```

`table { width: 100% }`, `th { white-space: nowrap }` and the `.scroll` overflow rule are deliberately
left alone: they are what makes a wide table scroll inside itself on a phone, and the mobile guarantee
depends on them.

### Why no markup changes

`html.table()` already wraps every table it builds in `div.scroll`, and every table in the interface is
built by it. The stylesheet therefore reaches all nine — `/active`, four on `/queue`, two on `/cards`,
two on the item page — and any table added later, with nothing to opt into. That is FR-008, and it is
satisfied by code that already exists rather than by code this feature adds.

### Design artifacts

- [data-model.md](./data-model.md) — the two layout regions the spec names, since the feature has no
  data entities.
- [contracts/layout.md](./contracts/layout.md) — the width contract: what the stylesheet must state,
  what it must not stop stating, and what a test may rely on.
- [quickstart.md](./quickstart.md) — how to render the pages, measure them, and check both viewports.

### Constitution re-check after design

Unchanged from the gate above. The design added no module, no dependency, no configuration key, and no
state. The one thing the design surfaced — that `.scroll` must keep `overflow-x: auto` for the mobile
guarantee to survive — is a constraint recorded in the contract and covered by a test, not new
complexity.

## Testing approach

`tests/unit/test_web_layout.py`, asserting in two registers so neither can drift alone:

1. **Against `html.APP_CSS`** — the prose measure and the page bound both exist and differ; `main` is
   bounded by the page value and not by the measure; prose elements are capped at the measure;
   `.scroll` is shrink-to-fit *and* still `overflow-x: auto`; `table { width: 100% }` survives.
2. **Against rendered markup** — every table a view renders is inside a `div.scroll`, so the rules
   above actually reach it. This is the assertion that would fail if a later page built a table by
   hand.

Both must fail against the current stylesheet, which is how the tests are shown to be testing the
change rather than describing it.

## Documentation

[`docs/guide/operating.md`](../../docs/guide/operating.md) is the guide page for the web interface. It
gains a short note on how the views use the window: tables take the width their content needs up to the
width of a full-size monitor, prose stays at a readable measure, and a table too wide for the viewport
scrolls inside itself rather than scrolling the page.
