# Implementation Plan: The Web Interface Shows Its Work and Announces Non-Live Mode

**Branch**: `009-web-non-live-visibility` | **Date**: 2026-08-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/009-web-non-live-visibility/spec.md`

## Summary

A rendering change confined to the web front end. Nothing about which rows exist, which rows
the dispatcher considers, or how capacity is counted changes; three things about how a page
reads do.

**One default moves.** `Request.include_simulated` is a bare boolean parsed from the query
string, so it is false everywhere the operator did not type a parameter — and below `live`
every row is a simulated row, so every view renders empty. It becomes a *tri-state
preference* (`True` / `False` / unstated) resolved against the effect level at the one place
that knows both: the request edge, where `ctx` is already in hand. Unstated below `live` means
show; unstated at `live` means withhold, exactly as today.

**One banner appears.** `_chrome_bar` already emits a full-width `banner error` for a stopped
daemon and for an effect-level mismatch — both "what you are reading does not mean what it
appears to mean" conditions. Below `live` is the third and broadest member of that family, and
it gets the same treatment, with the consequences it names derived from `effects.REAL_AT`
rather than written out per level, so the banner cannot drift from what the boundaries
actually do. `.pill.level`, which today has no CSS rule at all and so renders in the same
weight as `order: oldest-first`, gets an alarm treatment below `live` and a quiet one at
`live`.

**Two numbers surface.** Milestone 008 computed how many rows an invocation withheld and
carried it in `operations.status`'s payload; the web consumes that payload and ignores the
field. The views print it, so a page that is showing a subset says so instead of claiming
absence. `operations.cards` computes the same number and does not return it — a one-line
payload addition that closes the same gap for the cards view and for `cards --json`.

Two things the issue asked for turn out not to need building. The simulated row marker is
already a styled badge in the web, not the `*` the issue described — that is the CLI's
convention (research [R6](research.md)); the work there is a test that pins the coverage. And
the existing "simulated rows included" pill becomes a two-way toggle link, which is the direct
answer to the issue's real complaint that nothing on the page suggests the override exists.

No migration, no schema change, no new module, no new dependency, no configuration key.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added. Standard library only for the changed code —
`http.server`, `dataclasses`, `urllib.parse`. `httpx` remains the sole runtime dependency and
is untouched.

**Storage**: the existing SQLite database, read-only for this feature. No migration, no schema
change, no new column, no new accessor. Every count this feature displays already exists:
`db.count_simulated_work_items` and `db.count_simulated_cards`, both added by milestone 008.

**Testing**: pytest. The existing web suite runs at `effect_level = "live"`
(`tests/conftest.py:132`), where this feature changes no default — so the 2,455 existing lines
of web tests are load-bearing regression coverage for "nothing changed at `live`" and are
expected to pass untouched. New coverage needs a `plan`-level harness: a fixture parameterised
by effect level, a resolution matrix over (preference × level), a banner/pill test across all
four levels, a withheld-disclosure test per view, and a test asserting every boundary in
`effects.REAL_AT` has a consequence phrase.

**Target Platform**: the same single Linux machine. The reader is a phone browser on the local
network, which is the constraint that makes this a defect rather than a preference.

**Project Type**: single Python package (`src/robot_army/`) with a CLI and a web front end.
This feature touches the web front end and one line of `operations.py`.

**Performance Goals**: unchanged. Below `live` the views now select rows they previously
filtered out — the same query with a narrower `WHERE`, against tables holding tens to hundreds
of rows. The withheld counts are already computed by `operations.status` on every call and are
currently discarded; displaying them costs nothing new. `operations.cards` gains no query, only
a payload key for a count it already runs.

**Constraints**: the resolved default MUST NOT affect dispatch. `ordering.plan` and
`capacity.snapshot` pass `include_simulated=True` unconditionally and are not touched (FR-005,
SC-008). Behaviour at `live` with no stated preference MUST be byte-identical to today. The
banner and the level emphasis MUST be driven by one rule so they cannot disagree (FR-018).

**Scale/Scope**: five source files. Roughly 40 lines in `web/server.py` (the tri-state
property, one resolver, ~12 call-site substitutions), ~50 in `web/pages.py` (chrome payload,
withheld notes, the toggle pill), ~35 in `web/html.py` (the banner and three CSS rules), ~20
in `effects.py` (one table and one derivation), one line in `operations.py`. One new test file
plus additions to three existing ones.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Simplicity First (YAGNI & KISS) — **PASS**

- **No new module, no new dependency, no configuration knob.** The change lives in the three
  web files that already own the behaviour, plus one table in `effects.py` beside the
  `REAL_AT` table it derives from.
- **No abstraction with one caller.** A `Visibility` value object carrying the resolved
  boolean *and* the stated preference through every `pages.*` signature was designed and
  rejected: it would change roughly fifteen function signatures and every test that calls
  them, to carry a second field that one function needs. Research [R3](research.md) records
  the rejection and the two-line alternative that replaces it — every generated link states
  the value explicitly in both directions, so a single boolean round-trips correctly.
- **The consequence text is one table, not three paragraphs and not a generator.** Research
  [R5](research.md) rejects both hand-writing per-level prose (drifts) and deriving sentences
  mechanically from `REAL_AT` (unreadable). One phrase per boundary, membership computed,
  wording fixed by hand.
- **One rule where two were possible.** Research [R4](research.md) settles which effect level
  drives the banner and the pill rather than leaving the two to be decided independently at
  each site.

### II. Single-User, Local-First — **PASS**

No account, no role, no permission. The change makes the interface more legible to the one
operating-system user who can reach it; the bind, the host check and the same-origin check are
untouched. Nothing new leaves the machine, and no new external URL is constructed — the single
`github_link` chokepoint (`pages.py`) is not modified.

### III. Total Accountability — **PASS, with one enumerated gap already in force**

- **This feature adds no state-changing action**, so it adds nothing that must be logged. Every
  `POST` still passes through `_perform`, whose audit record is written before any check runs.
- **The audit detail gains precision rather than losing it.** `_perform` records
  `include_simulated` today, taken from the raw query string. It will record the *resolved*
  value and the *stated preference* as separate keys, so a record can be read back without
  knowing which effect level was in force — an improvement, not a regression.
- **The enumerated gap**: rendering a page is not logged, and is not being made loggable here.
  That gap was declared in milestone 002's plan and is unchanged; a `GET` mutates nothing, and
  logging every phone refresh at ten-second intervals would bury the action records that
  matter. No *new* gap is introduced by this feature.

### IV. Interruption Tolerance — **PASS**

The feature writes nothing. A request killed mid-render leaves no partial state because there
is no state to leave; the next request re-derives everything from the database and the request
line. The operator's stated preference lives in the URL, which means it survives a daemon
restart, a browser reload and a killed server without any persistence at all — the
interruption-tolerant place to keep it, and one more reason not to store it.

### V. Public Code, Unsupported Project — **PASS**

No credential, hostname or personal data enters the repository. No public API is stabilised:
the query parameter's accepted values widen (falsey values become meaningful), which is a
breaking change for nobody, since the only consumer is the author's own browser.

### Development Workflow — **PASS**

The two required questions are answered above: **what this logs** — nothing new, with the
pre-existing render-not-logged gap re-declared and unchanged; **what happens if it is killed
halfway** — nothing, because nothing is written. Unit tests are required and are enumerated in
Technical Context; the full suite must pass before the feature is complete.

## Project Structure

### Documentation (this feature)

```text
specs/009-web-non-live-visibility/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── web-visibility.md
├── checklists/
│   └── requirements.md  # from /speckit-specify
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/robot_army/
├── effects.py           # + SIMULATED_CONSEQUENCES table, consequences(level)
├── operations.py        # + withheld_simulated in cards() payload (one line)
└── web/
    ├── server.py        # tri-state preference, the resolver, call sites, html_query
    ├── pages.py         # chrome payload, withheld notes, the toggle pill, _items
    └── html.py          # the non-live banner, .pill.level styling, CSS

tests/
├── conftest.py                          # + effect-level-parameterised web harness
├── unit/
│   ├── test_web_simulated_default.py    # NEW: resolution matrix, link round-trip
│   ├── test_web_non_live_banner.py      # NEW: banner + pill across four levels
│   ├── test_web_views.py                # + withheld disclosure per view
│   ├── test_web_render.py               # + .sim coverage pin, CSS presence
│   └── test_web_effect_guard.py         # + effective-level rule vs. mismatch
└── integration/
    └── test_web_end_to_end.py           # + one round-trip at plan
```

**Structure Decision**: the existing single-package layout is kept unchanged. This feature adds
no directory and no module; every edit lands in a file that already owns the behaviour being
changed. `effects.py` is the home for the consequence table because it already owns `REAL_AT`,
the table the consequences are derived from, and keeping them adjacent is what makes the
drift-preventing test trivial to write.

## Post-Design Constitution Re-Check

*Re-evaluated after Phase 1. Result: **PASS**, with one scope reduction and one addition worth
recording.*

- **Principle I held, and design made it stronger.** Phase 1 removed work rather than adding it.
  Research [R6](research.md) found the simulated-row badge already built and already styled, so
  US3 collapses from a rendering change into a coverage test. Research [R3](research.md) replaced
  a fifteen-signature refactor with two changed lines. The design ends smaller than the summary
  first estimated, and no new module, dependency or configuration key survived to Phase 1.
- **One addition beyond the spec's letter, deliberately.** The visibility toggle
  ([R9](research.md)) is not named by any FR — the spec requires only that the override be
  reachable. It is included because the issue's actual complaint is that nothing on the page
  suggests the override exists, and because without it the way back to the hidden view is
  undiscoverable in exactly the new default state. It adds one pill, no route, and no state.
- **Principle III is unchanged and the enumerated gap is unchanged.** Phase 1 confirmed no new
  state-changing action: every key this feature adds is derived per request and discarded with
  the response. The audit detail gains `simulated_preference` beside the existing
  `include_simulated`, which makes an existing record more legible without changing when it is
  written.
- **Principle IV is stronger than at gate time.** The design stores the operator's preference
  nowhere — it lives in the URL — so there is no new persisted state to be interrupted, and no
  cookie or session to recover. [R3](research.md) records why a cookie was rejected on exactly
  these grounds.
- **One contract obligation surfaced during design.** 002's contract states "simulated rows are
  excluded unless `?include_simulated=1`" as a universal rule; this feature supersedes it. That
  is recorded explicitly at the head of
  [contracts/web-visibility.md](contracts/web-visibility.md) rather than left for a reader to
  discover as a contradiction between two contract documents — the same class of defect this
  milestone exists to remove from the interface.

## Complexity Tracking

> No Constitution Check violations. This section is intentionally empty.
