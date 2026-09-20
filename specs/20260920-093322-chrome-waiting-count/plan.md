# Implementation Plan: A chrome pill for work waiting on the operator

**Branch**: `robot-army/issue-182-the-web-chrome-never-says-there-is-work` | **Date**: 2026-09-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260920-093322-chrome-waiting-count/spec.md`

## Summary

The chrome payload (`pages.chrome`) carries no work-item state information at all, so an item
that goes to `awaiting_review` leaves `/active`, was never on `/queue`, and nothing anywhere
notices (issue #182). `failed` is worse: no page lists it.

`chrome` gains one key — `waiting_count`, the number of items in `awaiting_review`,
`interrupted` or `failed`, scoped by the request's simulated-row visibility, from the same
`db.count_work_items_by_state` that `robot-army status` counts with. `_chrome_bar` renders it
as one pill in the anomaly pill's shape, quiet at zero and warning above it, linking to
`/interrupted`. That page grows a third section for `failed`, so the pill and its destination
agree, and the nav entry, the heading and the pill all read `needs me` instead of naming one
state of three.

Two pills stop stating the default. The effect-level pill renders on any level other than
exactly `live` — `unknown` keeps it, because "we could not tell" is news. The visibility pill
renders only when simulated rows are being *included*, which is the default below `live`. Both
reverse an argument recorded in a comment; both comments are rewritten with the reasoning that
overrules them (R10), not deleted. Removing the visibility pill is conditional on the
verification in R6, which is complete: all six views that can withhold a simulated row
disclose it themselves.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none added

**Storage**: SQLite `work_items`, read only. No schema change, no migration, no new audit action.

**Testing**: `pytest` via `uv run pytest`. The existing web harness fixtures (`web`, `web_at`,
`conn`, `seed_item`) drive every case; three named tests are rewritten rather than deleted.

**Target Platform**: one Linux machine

**Project Type**: single Python package (CLI daemon plus local web interface)

**Performance Goals**: none. One extra `SELECT … GROUP BY state` per page render; one extra
listing on one page, sharing the request's existing capacity snapshot (R8).

**Constraints**:
- The count and the page it links to must agree under both visibility settings (FR-002, C12).
  This is the one property that must be tested end to end rather than on its two halves.
- The pill must not appear on bare chrome, which counted nothing (C1, R2).
- The level pill's condition is `!= "live"`, not membership of the below-live set, so `unknown`
  keeps it (C15, C16).
- Removing the visibility pill is gated on R6's verification (C20, FR-013).
- Each withheld simulated row is disclosed exactly once on the grown page (C25, C26, R7).

**Scale/Scope**:
- Source: `web/pages.py` (`chrome`, `interrupted_view`), `web/html.py` (`NAV`, `_chrome_bar`)
- Tests: `tests/unit/test_web_views.py` (the count, the failed section, `WITHHELD_VIEWS`),
  `tests/unit/test_web_non_live_banner.py` (the level pill at `live` and at `unknown`),
  `tests/unit/test_web_simulated_default.py` (the visibility pill in both directions), plus a
  quiet-bar test
- Docs: `docs/guide/operating.md` (the web interface section — the new pill, the renamed nav
  entry, the two pills that now stay silent)

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design — see the second table.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | One key on an existing payload, one pill in an existing renderer, one section on an existing page, two conditions inverted. No new route, handler, database function, config key, dependency or abstraction. The three rejected alternatives are recorded: a `db.count_waiting` helper with one caller (R1), a new `/waiting` page (R3), a bulk multi-state fetch to save an uncounted cost (R8). The route is not renamed, because the reader sees the label and nobody types the URL (R3). |
| **II. Single-User, Local-First** | Unchanged. Local SQLite, local HTTP, no account, no network call added. |
| **III. Total Accountability** | **What this logs: nothing, because nothing outside the process changes.** No file is written, no command run, no request sent, no notification raised. Principle III's obligation attaches to actions that change external state; this feature has none, so this is the absence of an action rather than an unlogged one (R9). The reads it adds are one `SELECT … GROUP BY` per render and one listing on one page. No silent failure is introduced: no exception is caught anywhere in the change. |
| **IV. Interruption Tolerance** | **If it is killed halfway: a truncated HTTP response, and nothing else.** No persistent write, so no atomic-rename discipline applies and nothing is left half-written. The next request re-reads and re-renders. There is no checkpoint because there is no progress. No network call, so no timeout or retry bound is needed. |
| **V. Public Code, Unsupported Project** | The pill and the nav entry are text; no credential, hostname or personal datum is added. No outside consumer exists, so the changed JSON key set and the changed nav label break nothing that must be kept. The guide page is updated for the author's future self — no tutorial, no migration note. |
| **Operating Constraints** | Every fact the new pill carries is already in `robot-army status`, which is where it came from; the terminal remains sufficient, and the web is not a prerequisite for anything. Nothing outward-facing is added or enabled. |
| **Development Workflow** | Spec → plan → tasks → implement, this plan carrying the check. Unit tests cover every contract case C1–C28, including the zero count, both visibility settings, the `unknown` level in both places it arises, the disclosure precondition on all six withholding views, and the quiet bar. The full suite must pass. |

**Verdict: PASS.** No violation, so Complexity Tracking is omitted.

### Re-check after Phase 1 design

The design added nothing the first check did not anticipate. Two points are worth naming
because they were decided *during* design:

- **The count's absence is a third state** (C1, R2). `_bare` omits the key rather than setting
  zero. That is one `in` check in the renderer, and it follows a pattern `_visibility_suffix`
  already established for the same reason in the same function — not a new concept.
- **`interrupted_view` calls `_items` a third time** (R8). Accepted rather than optimised. The
  alternative is a helper with one caller built against an unmeasured cost, which Principle I
  forbids more clearly than it forbids a third call.

**Verdict after design: PASS.**

## Project Structure

### Documentation (this feature)

```text
specs/20260920-093322-chrome-waiting-count/
├── plan.md               # this file
├── spec.md
├── research.md           # R1–R10
├── data-model.md         # the counted set, the derived value, no schema change
├── quickstart.md         # how to see it working, and how to see it fail
├── contracts/
│   └── chrome-bar.md     # cases C1–C28
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks output — not created here
```

### Source Code (repository root)

```text
src/robot_army/
├── web/
│   ├── pages.py          # chrome(): + waiting_count (R1, R2)
│   │                     # interrupted_view(): + failed section, + heading (R3, R7)
│   └── html.py           # NAV: the renamed entry (R4)
│                         # _chrome_bar(): + the waiting pill (C1–C6)
│                         #                level pill condition inverted (C13–C16, R10)
│                         #                visibility pill condition inverted (C17–C19, R10)
└── db.py                 # unchanged — count_work_items_by_state already exists

tests/unit/
├── test_web_views.py             # the count, the failed section, WITHHELD_VIEWS
├── test_web_non_live_banner.py   # the level pill: absent at live, present at unknown
├── test_web_simulated_default.py # the visibility pill, and R9's route via withheld_note
└── test_web_chrome_waiting.py    # the new pill and the quiet bar (C1–C6, C21)

docs/guide/
└── operating.md          # the web interface section
```

**Structure Decision**: no new module. The change lives in the two files the issue names, in
functions that already exist, plus one new test file for the pill itself and edits to the three
test files the issue names. `docs/guide/operating.md` is the guide page for "the web interface,
health, recovery, anomalies, paths" per CLAUDE.md's table, and it is the one that changes.

## Phase 1 artefacts

- [data-model.md](data-model.md) — which states count and why, the derived value's three-state
  presence, the page payload's new keys. No schema change.
- [contracts/chrome-bar.md](contracts/chrome-bar.md) — C1–C28: every pill's render condition,
  the count's scoping, the agreement property, the disclosure precondition, the quiet bar, and
  the grown page.
- [quickstart.md](quickstart.md) — how to see the pill appear, how to see the count and the
  page agree, and how to see the two pills stay silent.
