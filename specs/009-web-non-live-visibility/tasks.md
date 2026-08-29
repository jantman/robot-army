---

description: "Task list for 009 — the web interface shows its work and announces non-live mode"
---

# Tasks: The Web Interface Shows Its Work and Announces Non-Live Mode

**Input**: Design documents from `/specs/009-web-non-live-visibility/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/web-visibility.md](contracts/web-visibility.md)

**Tests**: **required, not optional.** The constitution's Development Workflow section states
that every new or changed unit of behaviour MUST ship with unit tests and that the full suite
MUST pass before the feature is complete. Test tasks below are therefore first-class, not a
requested extra.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Every task names the exact file it touches

## Path Conventions

Single Python package at the repository root: `src/robot_army/`, `tests/unit/`,
`tests/integration/`. No new directory is created by this feature.

---

## Phase 1: Setup

**Purpose**: establish the baseline this feature's "nothing changed at `live`" claim rests on.

- [X] T001 Record the green baseline: run `uv run pytest` from the repository root and confirm the suite passes before any edit, so a later failure is attributable to this feature
- [X] T002 [P] Read [contracts/web-visibility.md](contracts/web-visibility.md) end to end and confirm it does not contradict `specs/002-web-ui/contracts/http-api.md` beyond the two rules it explicitly supersedes

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the two derived values every user story reads. US1 needs both to resolve the
default; US2 needs the effective level for the banner and the pill.

**⚠️ CRITICAL**: no user story work can begin until this phase is complete.

- [X] T003 Add a `FALSEY` frozenset (`0`, `false`, `no`, `off`) beside the existing `TRUTHY` in `src/robot_army/web/server.py`
- [X] T004 Replace the `Request.include_simulated` property with `Request.simulated_preference -> bool | None` in `src/robot_army/web/server.py`, returning `True` for `TRUTHY`, `False` for `FALSEY`, and `None` for absent, empty or unrecognised values (FR-004); update its docstring to cite this feature rather than 001's FR-019
- [X] T005 [P] Add `pages.effective_level(ctx, report, *, running) -> EffectLevel | None` in `src/robot_army/web/pages.py`, beside `effect_mismatch` and reusing its three-state reading of the lock and heartbeat: the more simulated of `ctx.effect_level` and the daemon's level by `list(EffectLevel).index`, the configured level alone when no daemon holds the lock, and `None` when a daemon holds the lock but no heartbeat can be read (research [R4](research.md))
- [X] T006 Add `effective_level` (the string, or `"unknown"`) and `simulated_preference` to the payload returned by `pages.chrome` in `src/robot_army/web/pages.py`, computed once per request from the same `report`/`running` values `effect_mismatch` already receives, so the banner and the pill cannot re-derive them differently (FR-018)
- [X] T007 Pass `request.simulated_preference` into `pages.chrome` from `handle` in `src/robot_army/web/server.py` (line ~1081), replacing the current `include_simulated=request.include_simulated` argument with both the resolved value and the stated preference
- [X] T008 [P] Add `tests/unit/test_web_simulated_default.py` with the preference-parsing matrix: each `TRUTHY` spelling → `True`, each `FALSEY` spelling → `False`, and absent/empty/`"treu"` → `None` with a `200` rather than a `400`
- [X] T009 [P] Add to `tests/unit/test_web_effect_guard.py` a test of the effective-level rule across its four states — agreement, disagreement in each direction, no daemon, and daemon-with-unreadable-heartbeat — asserting the more-simulated value wins and that `unknown` emits no second banner
- [X] T010 [P] Add to `tests/unit/test_web_effect_guard.py` a test pinning `list(EffectLevel)` to the order `plan, local, no-remote, live`, so a future reordering of the enum cannot silently invert the comparison in T005

**Checkpoint**: `chrome()` carries one effective level and one stated preference. Nothing yet
reads them.

---

## Phase 3: User Story 1 — Seeing the work the daemon has actually done, below `live` (Priority: P1) 🎯

**Goal**: below `live`, every view renders the rows that exist, without the operator typing
anything; withholding stays reachable and stays sticky; a partial view says it is partial.

**Independent Test**: point the interface at a database whose only work items and cards are
simulated, with the configured level below `live`, request each view with no query parameters,
and confirm every row appears and no view claims emptiness. Then request one view with
`include_simulated=0`, follow a nav link from it, and confirm the rows are still hidden.

### Implementation for User Story 1

- [X] T011 [US1] Add `include_simulated_for(request, ctx) -> bool` to `src/robot_army/web/server.py`: return the stated preference when it is not `None`, otherwise `True` unless the effective level is `live` (the resolution table in [data-model.md](data-model.md))
- [X] T012 [US1] Replace every remaining `request.include_simulated` read in `src/robot_army/web/server.py` with `include_simulated_for(request, ctx)` — the eight `view_*` handlers (lines ~591–652), `view_root`, and the two sites inside `_perform`/`html_query`
- [X] T013 [US1] Change `html_query` in `src/robot_army/web/server.py` to always emit `include_simulated=1` or `include_simulated=0` rather than omitting the parameter when false (research [R3](research.md)), so a stated preference survives the `303` after a `POST`
- [X] T014 [US1] Change `pages._query` in `src/robot_army/web/pages.py` the same way, and change the three `html.hidden("include_simulated", "1") if include_simulated else None` call sites (in `action_control`, `dispatch_controls`, `rescan_control`) to always emit the field with `"1"` or `"0"`
- [X] T015 [US1] Record both values in the audit detail in `_perform` in `src/robot_army/web/server.py`: keep `include_simulated` as the resolved value and add `simulated_preference`, so a record can be read back without knowing which level was in force
- [X] T016 [US1] Change `pages._items` in `src/robot_army/web/pages.py` to return the rows **and** `data["withheld_simulated"]["items"]` from the same `operations.status` call, and update its four call sites (`active_view` line ~528, `queue_view` line ~606, and the two in `interrupted_view` lines ~873/879) to bind both
- [X] T017 [US1] Add `withheld_simulated` to the payload of `operations.cards` in `src/robot_army/operations.py` — the `withheld` count it already computes at line ~2032, which today reaches only the text lines and not `data`
- [X] T018 [US1] Add `pages.withheld_note(count, *, path, include_simulated) -> Markup` in `src/robot_army/web/pages.py`: empty markup when the count is zero, otherwise `N simulated rows hidden` with a link to the same path carrying the flipped preference (FR-006, FR-009)
- [X] T019 [US1] Render the withheld note in `active_view`, `queue_view`, `interrupted_view` and `cards_view` in `src/robot_army/web/pages.py`, once per view beneath its tables; for `interrupted_view` sum the two state-filtered counts, which is exactly the set the link would reveal (FR-007)
- [X] T020 [US1] Change the empty-state text in those four views (`_empty` calls at lines ~545, 671, 694, 714, 897, 903, 1037 in `src/robot_army/web/pages.py`) so that when rows are withheld it says nothing is *shown* rather than that nothing *exists*, and carries the count and the link (FR-008)
- [X] T021 [US1] Turn the `simulated rows included` pill in `_chrome_bar` in `src/robot_army/web/html.py` (line ~271) into a link rendered in **both** states — `simulated rows included` linking to `include_simulated=0`, `simulated rows hidden` linking to `include_simulated=1` — keeping `pill quiet` in both (research [R9](research.md)); it needs the request path, so add it to the chrome payload in `pages.chrome`

### Tests for User Story 1

- [X] T022 [P] [US1] Extend `tests/unit/test_web_simulated_default.py` with the resolution matrix from [data-model.md](data-model.md): (stated `True`/`False`/unstated) × (`plan`, `local`, `no-remote`, `live`, unknown), asserting the resolved value and that `live`-with-no-preference is unchanged from today
- [X] T023 [P] [US1] Add to `tests/unit/test_web_simulated_default.py` a link round-trip test: request a view with `include_simulated=0` below `live`, extract every generated `href` and hidden form field, and assert each carries `include_simulated=0` — the FR-003 regression that omission used to hide
- [X] T024 [P] [US1] Add a level-parameterised web harness to `tests/conftest.py` (a fixture taking an effect level, leaving the existing `web` fixture at `live` untouched per research [R10](research.md))
- [X] T025 [US1] Add to `tests/unit/test_web_views.py` a per-view withheld-disclosure test covering the four cases in the contract's disclosure table: nothing withheld with rows, nothing withheld with no rows, rows withheld with rows visible, rows withheld with none visible — asserting no view ever claims absence while withholding
- [X] T026 [P] [US1] Add to `tests/unit/test_web_views.py` a test that `operations.cards`'s payload carries `withheld_simulated` and that it equals the number `--include-simulated` reveals under the same state filter
- [X] T027 [US1] Fix the existing assertions that break by design: any test in `tests/unit/test_web_actions.py`, `test_web_routing.py` or `test_web_views.py` asserting on a generated URL that previously omitted `include_simulated` now expects `include_simulated=0` (research [R10](research.md)); re-run `uv run pytest tests/unit/` and confirm every remaining failure is one of these
- [X] T028 [P] [US1] Add to `tests/unit/test_ordering.py` or `tests/unit/test_capacity.py` an assertion that the dispatch selection is identical for the same database regardless of the web's resolved visibility (SC-008, FR-005) — or, if the existing tests already establish this by never consulting the web, note that in a comment rather than adding a redundant test

**Checkpoint**: below `live` the four views show their rows unprompted, a hidden view says what
it is hiding and offers the way back, and the choice survives every click. US1 is complete and
demonstrable on its own — but see the Implementation Strategy note about shipping it alone.

---

## Phase 4: User Story 2 — Being told, on every page, that none of this is real (Priority: P1)

**Goal**: every view below `live` carries a persistent banner naming the level and what the
displayed values are not, and the level pill is unmistakable below `live` and calm at `live`.

**Independent Test**: render every view at each of the four effect levels and confirm the
banner is present and level-appropriate at `plan`, `local` and `no-remote`, absent at `live`,
and that the pill takes alarm styling below `live` only.

### Implementation for User Story 2

- [X] T029 [P] [US2] Add `SIMULATED_CONSEQUENCES: dict[str, str]` to `src/robot_army/effects.py` beside `REAL_AT`, one operator-facing phrase per boundary name, using the wording fixed in [data-model.md](data-model.md) — including the `issue_writer` phrase that names both the unwritten comment and the invented issue number
- [X] T030 [US2] Add `consequences(level) -> list[str]` to `src/robot_army/effects.py`, returning the phrases whose boundary is not real at that level, in `SIMULATED_CONSEQUENCES` declaration order; empty at `live` by construction, which is what makes FR-014 fall out of the derivation rather than out of a branch
- [X] T031 [US2] Emit the non-live banner from `_chrome_bar` in `src/robot_army/web/html.py`, in the `notices` list beside the daemon-not-running and mismatch banners and with the same `banner error` class, whenever `chrome["effective_level"]` is below `live`; naming the level and listing `effects.consequences(...)` (FR-010 through FR-013, FR-015)
- [X] T032 [US2] Suppress only the *second* banner when the effective level is `unknown` in `src/robot_army/web/html.py`: the existing `EFFECT LEVEL UNKNOWN` mismatch banner carries the explanation, so the page shows one account of the situation rather than two
- [X] T033 [US2] Style the level pill in `_chrome_bar` in `src/robot_army/web/html.py`: class `pill level simulated` with the text suffixed `— simulated` below `live` and when unknown, class `pill level live` at `live` (FR-016, FR-017), so the signal survives a monochrome screenshot
- [X] T034 [US2] Add the two CSS rules to the stylesheet in `src/robot_army/web/html.py` (beside `.pill.warn` at line ~416): `.pill.level.simulated` taking `var(--error)` for border and text plus a bolder weight, and `.pill.level.live` taking the muted treatment — the error colour rather than warn because warn is already spent on capacity and on a paused dispatch (research [R7](research.md))

### Tests for User Story 2

- [X] T035 [P] [US2] Add `tests/unit/test_web_non_live_banner.py` asserting the banner is present on every view at `plan`, `local` and `no-remote` and absent from every view at `live`, using the level-parameterised harness from T024
- [X] T036 [P] [US2] Add to `tests/unit/test_web_non_live_banner.py` a test that the stated consequences differ per level and that each names only boundaries genuinely simulated at that level, driven from `effects.REAL_AT` rather than from a hardcoded expected string (FR-013)
- [X] T037 [P] [US2] Add to `tests/unit/test_effects.py` the drift guard: `set(SIMULATED_CONSEQUENCES) == set(REAL_AT)`, so a boundary added to one table without the other fails the suite
- [X] T038 [P] [US2] Add to `tests/unit/test_web_render.py` a test that `.pill.level.simulated` and `.pill.level.live` both have rules in the served stylesheet — the defect this story exists to fix was an unstyled class, and nothing today would catch its return
- [X] T039 [US2] Add to `tests/unit/test_web_non_live_banner.py` a test that the non-live banner, the daemon-not-running banner, the mismatch banner and a `?msg=` action banner all render together and none suppresses another (FR-015)

**Checkpoint**: no page below `live` can be mistaken for a live one, at a glance or on close
reading. US1 and US2 together are the shippable unit.

---

## Phase 5: User Story 3 — Telling a simulated row from a real one at a glance (Priority: P2)

**Goal**: a simulated row is legible as simulated wherever rows are shown, including in a table
holding both kinds.

**Independent Test**: render a table holding both a real and a simulated row and confirm the
simulated one is distinguishable without close reading.

> **Scope note**: research [R6](research.md) found this largely already built.
> `html.SIMULATED_MARK` is already a word-badge and `.sim` already has a bold, warn-coloured
> rule; the `*` suffix the issue described is the CLI's convention, not the web's. What is
> missing is coverage — nothing pins that every row-bearing view calls `mark_simulated`. So
> this phase is tests plus one gap fix, not a rendering change.

- [X] T040 [P] [US3] Add to `tests/unit/test_web_render.py` a test that walks each row-bearing view (`/active`, `/queue`, `/interrupted`, `/cards`, `/item/{id}`, the confirm views, `/log`) with one simulated row present and asserts the `sim` badge is rendered — the FR-019 coverage that would catch a table added later without it
- [X] T041 [US3] Fix any view the T040 test finds unmarked, in `src/robot_army/web/pages.py`, by routing its row title or identifier through the existing `mark_simulated`; if every view already passes, record that in the test's docstring rather than changing rendering for its own sake
- [X] T042 [P] [US3] Add to `tests/unit/test_web_render.py` a mixed-table test: one real and one simulated row in the same view, asserting the badge appears exactly once and against the right row (FR-020)

**Checkpoint**: all three stories functional and independently verified.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T043 Run the full suite: `uv run pytest` from the repository root, confirming the constitution's completion gate
- [X] T044 Work through [quickstart.md](quickstart.md) end to end against a throwaway state directory, including reading scenarios 3–6 with eyes rather than `grep` — the feature is about what a page looks like
- [X] T045 [P] Update `docs/roadmap.md`: give this milestone the `009` slot with a short account of the two compounding defects and what running at `plan` taught, and move the "whatever survives contact with reality" parking lot to `010` — the fifth time a milestone with a shape has displaced one without
- [X] T046 [P] Update `specs/002-web-ui/contracts/http-api.md` to point at [contracts/web-visibility.md](contracts/web-visibility.md) for the two universal rules this feature supersedes, so a reader of the older contract is not left with a statement that is no longer true
- [X] T047 [P] Note in `docs/roadmap.md`'s 008 entry that its "the web interface has the loud half and is issue #14 … this milestone puts the count into the payload the web already consumes and stops there" is now discharged by 009
- [X] T048 Self-review against `.specify/memory/constitution.md`, confirming in particular that no new action went unlogged and that nothing this feature added persists state

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks US1 and US2**
- **US1 (Phase 3)**: depends on Phase 2 (needs both `simulated_preference` and `effective_level`)
- **US2 (Phase 4)**: depends on Phase 2 (needs `effective_level`) — **independent of US1**
- **US3 (Phase 5)**: depends on Phase 2 only nominally; its tests need the level-parameterised harness from T024, so in practice it follows US1
- **Polish (Phase 6)**: depends on all stories intended for the release

### Within Phase 2

- T003 → T004 (the parse needs the set)
- T004, T005 → T006 (the payload carries both)
- T006 → T007
- T008, T009, T010 are parallel with each other and can be written against T004/T005 as they land

### Within US1

- T011 depends on T004 and T005
- T012 depends on T011
- T013, T014 are parallel with each other, both independent of T012
- T016 → T019 → T020 (rows and counts, then the note, then the empty states)
- T017 is independent of everything else in the phase and can go first
- T027 must come after T013 and T014, because those are what break the existing assertions
- T022, T023, T026, T028 are parallel; T025 depends on T019 and T020

### Within US2

- T029 → T030 → T031
- T031 → T032
- T033 → T034
- T035–T039 follow their respective implementation tasks; T037 depends only on T029

### Parallel Opportunities

- T005 and T004 touch different files and can be written simultaneously
- **US1 and US2 can be built in parallel once Phase 2 lands** — they share only the chrome
  payload, which Phase 2 already fixed. `server.py` and `pages.py` belong to US1;
  `effects.py` and `html.py` belong to US2. The one overlap is `_chrome_bar` (T021 vs.
  T031/T033), so whoever is second rebases on the first
- Every test task marked [P] within a phase is in a different file or a different test class
- The four Polish documentation tasks are mutually independent

---

## Parallel Example: User Story 2

```bash
# The consequence table and the pill styling touch different concerns in different files:
Task: "Add SIMULATED_CONSEQUENCES to src/robot_army/effects.py"        # T029
Task: "Add .pill.level CSS rules to src/robot_army/web/html.py"        # T034

# Then the tests, all in different files or different classes:
Task: "Banner presence across four levels in tests/unit/test_web_non_live_banner.py"  # T035
Task: "REAL_AT drift guard in tests/unit/test_effects.py"                             # T037
Task: "Stylesheet rule presence in tests/unit/test_web_render.py"                     # T038
```

---

## Implementation Strategy

### The shippable unit is US1 **and** US2 together

The template's default is "MVP = User Story 1", and that is wrong here. Both stories are P1,
and the spec says why: making the rows visible without saying they are simulated trades one
misreading for a worse one — a page full of convincing but fictional work, with invented issue
numbers rendered as links. US1 alone is a regression in honesty even as it is an improvement in
usefulness.

So: **Phase 1 → Phase 2 → Phases 3 and 4 → validate → ship.** Phase 5 is a genuine increment
that can follow, and Phase 6 closes the milestone.

### Suggested order for one person

1. Phases 1 and 2 — small, mechanical, and everything else waits on them
2. Phase 4 (US2) before Phase 3 (US1) — the banner is the smaller change, and having it in
   place means the moment the default flips in US1 the pages are already telling the truth
   about what they are showing
3. Phase 3 (US1) — the larger change, and the one that breaks existing URL assertions (T027)
4. **Stop and validate**: quickstart scenarios 1–5, read on a phone
5. Phase 5 (US3), then Phase 6

### What must stay true throughout

- At `live` with no stated preference, behaviour is byte-identical to today. The existing web
  suite runs at `live` (`tests/conftest.py:132`) and is the check on this — T027 is the only
  place existing tests should need touching, and only for generated URLs
- Dispatch selection does not change. `ordering.plan` and `capacity.snapshot` keep
  `include_simulated=True` unconditionally and are not edited by any task above

---

## Notes

- [P] tasks touch different files and have no incomplete dependencies
- Commit after each task or logical group; messages explain why, per the constitution
- T041 may legitimately be a no-op — research [R6](research.md) expects it to be. Recording
  "already correct, here is the test that pins it" is the honest outcome, not a failure to find
  work

---

## Implementation notes (filled in during `/speckit-implement`)

Five departures from the plan, all recorded rather than quietly absorbed.

1. **T011 moved into Phase 2.** `handle` cannot build the chrome without the resolver, so
   "foundational" and "US1's core" turned out to be the same task. The phase boundary was
   drawn in the wrong place; the work was not.
2. **T005 moved from `pages.py` to `server.py`.** `tests/unit/test_effects.py` enforces that
   only modules which *resolve* an effect level may name `EffectLevel` — the FR-053 guard —
   and `web/server.py` is already exempt while `web/pages.py` is not. The plan put
   `effective_level` in the wrong file and the existing suite said so on the first run.
   `chrome()` now receives the level and the consequence list as plain strings, which also
   keeps `html.py` free of any product import.
3. **Two link gaps the plan did not anticipate**, both found by T023 rather than by reading:
   the nav bar and the brand link carried no query at all, and `item_link` — on every row of
   three views — dropped the preference. Both are now threaded, along with the chrome's
   `/queue` and `/anomalies` pills and the log's "clear" link. This is the second milestone
   running in which the test written to prove a rule found the place the rule was not applied.
4. **T041 was a no-op**, as research [R6](research.md) predicted. The badge coverage test
   passed the moment it was written; no view needed fixing. Recorded here rather than
   presented as work.
5. **Four existing assertions needed updating, not the two files predicted.** All four are
   generated URLs (T027's category): `test_web_routing`, `test_web_end_to_end` (×2),
   `test_web_log`, `test_pause`. Two further existing tests needed a different fix — they
   asserted the *word* "simulated" was absent from a page, which 009 makes meaningless since a
   page withholding rows now says so. They assert on the badge markup instead, which is what
   FR-019 actually requires.

### Four defects found in review, after the first push

All four were real, all four are fixed with a regression test each. Two of them share a root
cause worth naming: **the disclosure rule was written per view, and three of the four views
have more than one section.**

1. **`/queue` counted rows it never shows.** Its withheld number came from an unfiltered
   `operations.status`, so it counted `active`, `done`, `abandoned`, `interrupted` and
   `awaiting_review` rows too. With one ready row and three others it offered to reveal four
   and revealed one — precisely the "merely close" number 008's plan warns against. The count
   is now taken from the sections the page actually renders, by partitioning the rows first
   and hiding them second.
2. **`/interrupted`'s "awaiting review" section could deny rows that existed.** It used the
   count-blind empty text while its sibling used the disclosing one, so a page with real
   interrupted rows and withheld awaiting ones said "Nothing is awaiting review." above a
   footnote saying rows were hidden. Both sections now disclose their own count, and the
   foot of the page carries only what the rendered sections withheld — disjoint sets, each
   row disclosed exactly once.
3. **Error pages pinned "hide everything".** `_visibility_suffix` read a missing chrome key
   as a stated `False`, so every nav link on a `404`, `405` or refusal page carried
   `?include_simulated=0` — and a stated `0` beats the level default. One tap from a `404` on
   a `plan` instance landed on the empty page this milestone exists to remove. A missing key
   now states nothing, and those pages render no toggle, because a toggle reporting a state
   it had to guess is worse than none.
4. **The toggle on a refusal page pointed at a `POST`-only route.** A refused action renders
   the chrome built for its own request, so the toggle offered `/item/5/abandon?...`, which
   answers `405`. The chrome now carries the referring view for any non-`GET` request.

The first two are the more interesting failure: the per-view rule passed every test written
for it, because every test had one section holding rows or none at all. The mixed case — one
section full, its sibling empty and withholding — is the one a rule written at the wrong
granularity gets wrong, and the one nobody thought to write.

### A fifth, from the second review round

**The banner's prose was not derived, only its list.** The `<ul>` came from `REAL_AT` and was
right at every level; the sentences wrapped around it were fixed strings, and they said
"nothing on this page happened", "planned and not performed" and "nothing here reached
GitHub, Trello, or a terminal" at every level below `live`. At `local` branches and commits
are genuinely created; at `no-remote` a real session runs in a real terminal. Two of those
three sentences were false at two of the three levels.

This is FR-013's own failure mode — one message reused below `live` — surviving inside the
change that exists to prevent it, because the derivation stopped at the list. The framing now
turns on whether every simulatable boundary is simulated, which is true only at `plan`, so
`plan` keeps the strongest true statement and the other two levels get an accurate one that
also tells the operator what *was* carried out.

The existing test asserted the fixed sentence was present at all three levels, which is
exactly the assertion that made the bug invisible. Replaced with one asserting the framing
differs by level, plus a mechanical one over the whole rendered banner: no phrase from
`SIMULATED_CONSEQUENCES` may appear at a level where that boundary is real.

**Test count**: 1414 → 1529 (+115), including the five regressions above. Full suite and `ruff check` both green; both quickstart
halves walked against a live server at all four effect levels.
