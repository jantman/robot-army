---
description: "Task list for the chrome waiting count and the two pills that stop stating the default"
---

# Tasks: A chrome pill for work waiting on the operator

**Input**: Design documents from `specs/20260920-093322-chrome-waiting-count/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/chrome-bar.md](contracts/chrome-bar.md)

**Tests**: **Required, not optional.** The constitution's Development Workflow states that every
new or changed unit of behaviour ships with unit tests and that the full suite must pass before
the feature is complete. The issue names three existing tests that must be rewritten rather
than deleted, which is a stronger obligation still: each carries an assertion of the *old*
behaviour, and a deleted test is an unrecorded reversal.

**Organization**: by user story. Note the deliberate ordering below.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: US1–US4 from [spec.md](spec.md)

## Story ordering, and why it is not spec order

Spec order is US1 (the pill) then US2 (the page). **Implementation order is the reverse**, and
the reason is C12: the pill's number and its destination's listing must agree. Build the pill
first and the only increment that exists is one where a pill promising three states points at a
page listing two — the exact disagreement the spec forbids. So the destination is built first
and the pill is pointed at a page that can already honour it.

US3 and US4 are independent of both and of each other, except that US4 is gated on T018.

---

## Phase 1: Setup

**Purpose**: nothing to set up. No dependency, no scaffold, no migration.

- [ ] T001 Confirm the suite is green before any change: run `uv run pytest` and record the baseline, so a failure later is attributable to this work rather than inherited

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the one thing that must be settled before any pill is removed.

**⚠️ CRITICAL**: T002 is a gate on Phase 6 (US4). If it fails, FR-012 cannot be implemented as
written and the plan must be revised rather than the pill removed.

- [ ] T002 Verify [research.md](research.md) R6 against the current code: for every route in `ROUTES` (`src/robot_army/web/server.py`), confirm that a view which filters rows by `include_simulated` renders either `_nothing` or `withheld_note` (or `/log`'s own scoped note) when it withholds. Record any view that can withhold without disclosing. R6's table is the expected answer; this task is the re-check against the tree as it actually stands

**Checkpoint**: the disclosure precondition is settled; story work can begin.

---

## Phase 3: User Story 2 — the destination lists all three states (Priority: P1)

**Goal**: `/interrupted` lists `failed` beside `interrupted` and `awaiting_review`, each section
honest about what it withholds, and the page is named for what it holds.

**Independent test**: seed one `failed` item, open `/interrupted`, and see it listed with
`retry` and `reset` offered. Delivers value alone — `failed` is listed nowhere today.

**Contract cases**: C22–C28.

### Implementation

- [ ] T003 [US2] In `interrupted_view` (`src/robot_army/web/pages.py`), fetch failed items with a third `_items` call for `WorkItemState.FAILED`, pass it through `_visible` for its own withheld count, and map it through `_signal_row` — the same three steps the two existing sections take, in the same order, so the third section reads as obviously parallel to them
- [ ] T004 [US2] Render the failed section in the same body: an `h(2, f"failed ({len(failed)})")`, a one-line `meta` saying what the state means and that `retry` and `reset` are the routes out, and either `_nothing("Nothing has failed.", withheld_failed, …)` or the cards, exactly as the awaiting-review section does (C22, C23, C25)
- [ ] T005 [US2] Extend the page's withheld arithmetic: add `withheld_failed` to the `withheld` total and add `(withheld_failed if failed else 0)` to the `withheld_note` term, preserving the invariant that each withheld row is disclosed exactly once — the empty sections' counts and the note's count disjoint, together the whole ([research.md](research.md) R7, C26)
- [ ] T006 [US2] Extend the `View.data` payload: a `failed` key holding the rows, a `failed` entry under `counts`, and `withheld_simulated` as the sum of all three sections, so the JSON body states what the page states (C28)
- [ ] T007 [US2] Rename the page: the `h(1, …)` becomes `needs me`, with a `meta` line beneath it naming the three states it lists and why they share a page — the work is parked and the machine will not move it without a decision ([research.md](research.md) R4)
- [ ] T008 [US2] Update `interrupted_view`'s docstring to carry the reasoning for the third section: `failed` was in exactly the position awaiting-review was in when this page took it on — navigable from nowhere — and `retry`/`reset` are controls that exist but could not be reached
- [ ] T009 [P] [US2] Rename the `/interrupted` nav entry to `needs me` in `NAV` (`src/robot_army/web/html.py`), leaving the route unchanged, with a comment recording that the label is renamed and the path is not, and why ([research.md](research.md) R3)

### Tests

- [ ] T010 [US2] In `tests/unit/test_web_views.py`, assert a seeded `failed` item is listed on `/interrupted` with the controls `legal_actions` permits, that the JSON body carries it under `failed` and `counts.failed`, and that an empty failed section says so in its own words (C22–C24, C28)
- [ ] T011 [US2] Add `("/interrupted", "failed", "Nothing has failed.")` to `WITHHELD_VIEWS` in `tests/unit/test_web_views.py`, so the new section is held to the four existing parametrised disclosure rules — withholding everything, withholding some, nothing withheld and nothing present, nothing withheld with rows present (C25, C26)
- [ ] T012 [P] [US2] Assert the nav entry reads `needs me` and still points at `/interrupted` (C27)

**Checkpoint**: the destination can honour any count a pill states.

---

## Phase 4: User Story 1 — the bar says how much work is parked (Priority: P1) 🎯 MVP

**Goal**: every view carries a count of everything waiting on the operator, linking to the page
built in Phase 3.

**Independent test**: put one item in `awaiting_review`, request any view, see `1 needs me` in
the bar, follow it, find the item.

**Contract cases**: C1–C12.

### Implementation

- [ ] T013 [US1] In `pages.chrome` (`src/robot_army/web/pages.py`), add `waiting_count`: `db.count_work_items_by_state(ctx.conn, include_simulated=include_simulated)` summed over `awaiting_review`, `interrupted` and `failed`, named as a module-level constant beside a comment giving the rule the three share and why one number beats three ([research.md](research.md) R1, [data-model.md](data-model.md); C7–C10). The comment must also record that the scoping is not optional, for the same reason recorded for the anomaly count: an unscoped count disagrees with the page it links to the moment the toggle is off
- [ ] T014 [US1] In `_chrome_bar` (`src/robot_army/web/html.py`), render the pill immediately before the anomaly pill: a link to `/interrupted` carrying `_visibility_suffix`, reading `{n} need{'s' if n == 1 else ''} me`, class `pill warn` above zero and `pill quiet` at zero (C2–C6)
- [ ] T015 [US1] Guard the pill on the key's *presence*, not its truthiness, so the chrome from `server._bare` — which counted nothing — renders no pill at all. Comment it against `_visibility_suffix`'s recorded reasoning for the same guard, and note explicitly that this does not follow `anomaly_count`, which `_bare` sets to zero (C1, [research.md](research.md) R2)

### Tests

- [ ] T016 [US1] New file `tests/unit/test_web_chrome_waiting.py`: the count across all three states and none of the others (C7, C8); zero rendering quiet and present (C2); one and many rendering warn with correct inflection (C3, C4); the count in the JSON body of every view (C11); the pill absent on a 404 (C1); and the quiet bar at `live` reduced to exactly the daemon, capacity, order, waiting and anomaly pills (C21)
- [ ] T017 [US1] In the same file, assert the agreement end to end (**C12**): with a simulated item parked, request a view at `include_simulated=0` and at `=1`, and assert in each case that the pill's number equals the number of items its destination lists under the same setting. This is the property the whole feature rests on and must be asserted as one fact, not as two halves (C9, C10, C12)

**Checkpoint**: the reported defect is fixed. This and Phase 3 together are the MVP.

---

## Phase 5: User Story 3 — the level pill stops stating the default (Priority: P2)

**Goal**: no effect-level pill at exactly `live`; the pill everywhere else, including `unknown`.

**Independent test**: render at `live` (no pill), at `plan` (pill, alarming), at `unknown` (pill).

**Contract cases**: C13–C16.

### Implementation

- [ ] T018 [P] [US3] In `_chrome_bar` (`src/robot_army/web/html.py`), render the level pill only when the resolved level is not exactly `live`. The condition is inequality with `"live"`, not membership of the below-live set, because `unknown` must keep its pill (C13–C16)
- [ ] T019 [US3] Replace the "deliberate and settled" comment with the reasoning that overrules it ([research.md](research.md) R10): the old argument was against *alarming* at `live` and remains right; it does not reach whether a calm pill belongs there, which the pause pill, the effect-mismatch banner, the cap-disagreement note and the simulated-consequences banner — all absent when silent — already answer the other way. An absent pill reads as `live` by the convention the bar teaches. And `unknown` keeps its pill, in both places it arises, because "we could not tell" is news, not the default. **Rewritten, not deleted**

### Tests

- [ ] T020 [US3] Rewrite `test_the_pill_is_calm_at_live` in `tests/unit/test_web_non_live_banner.py` to assert the pill's *absence* at `live`, keeping its existing assertion that the word "simulated" appears nowhere on a live bar, and renaming it for what it now asserts (C13)
- [ ] T021 [P] [US3] Add coverage that the pill survives `unknown` in both places it arises: a running daemon whose effect level cannot be read, and the bare chrome of a 404 (C15, C16). `test_the_pill_alarms_below_live` is unchanged and stands (C14)

---

## Phase 6: User Story 4 — the visibility pill stops stating the default (Priority: P2)

**⚠️ Gated on T002.** If any view can withhold a simulated row without disclosing it, this phase
does not proceed as written.

**Goal**: the visibility pill only when simulated rows are being included.

**Independent test**: render with rows hidden (no pill), with rows included (pill, linking to
the flipped setting).

**Contract cases**: C17–C20.

### Implementation

- [ ] T022 [US4] In `_chrome_bar` (`src/robot_army/web/html.py`), render the visibility pill only when `include_simulated` is true, keeping the existing absence when the key is missing entirely (C17–C19)
- [ ] T023 [US4] Replace the 009 R9 comment with the reasoning that supersedes it ([research.md](research.md) R10): R9's complaint was that nothing on the page suggested the parameter existed; `withheld_note` did not exist then and now answers it exactly, beneath the table that withheld the rows, at the moment there is something to reveal. Record that R9 is satisfied elsewhere rather than abandoned, that T002 verified it holds on every withholding view, and the asymmetry with the level pill — below `live` the default is to *include*, so this pill is normally visible on a testing instance, which is the same polarity in both cases: the pill marks the surprising state. **Rewritten, not deleted**

### Tests

- [ ] T024 [US4] Rewrite the first half of `test_the_toggle_pill_offers_the_other_direction` in `tests/unit/test_web_simulated_default.py`: at `plan` with rows included the pill is present and links to `include_simulated=0` (C17), and rename the test for what it now asserts
- [ ] T025 [US4] Rewrite its second half — rows explicitly hidden on a `plan` instance — into an assertion that the *route back* is carried by `withheld_note` beneath the withholding table, and that no visibility pill is rendered. This is precisely the case R9 was protecting, and the assertion is what proves R9 is still honoured by something (C18, C20)
- [ ] T026 [US4] Confirm `test_every_generated_link_restates_the_preference` still finds a link offering the other direction in both directions at `plan`. With the pill gone at `include_simulated=0`, the reveal link in the withheld disclosure is the one that supplies it — so this existing test now depends on the disclosure rather than on the pill, and that dependency should be stated in its docstring

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T027 [P] Update `docs/guide/operating.md`'s web interface section: the new pill and what it counts; the nav entry renamed and the page now listing three states; the two pills that are now silent by default and what their silence means. Correct "Six views — active, queue, interrupted, one item, anomalies, and the log" to match the renamed page
- [ ] T028 Run `uv run pytest`. The full suite must pass — the constitution's completion criterion (SC-006)
- [ ] T029 Walk [quickstart.md](quickstart.md) by hand against a scratch state directory: the quiet bar at `live`, the count changing with the visibility setting, and the page agreeing with it under both. The end-to-end check the unit tests approximate
- [ ] T030 Re-read the three rewritten comments as a stranger would. Each must answer "why is this the way it is" and "what was the earlier position", because each reverses a decision the code previously argued for and the next reader's first instinct will be to change it back

---

## Dependencies

```text
T001 ──► T002 ──┬──► Phase 3 (US2) ──► Phase 4 (US1) ──┐
                │                                       │
                ├──► Phase 5 (US3) ─────────────────────┤──► Phase 7
                │                                       │
                └──► Phase 6 (US4) ─────────────────────┘
                     (gated on T002's result)
```

- **Phase 4 depends on Phase 3**, for C12: a pill counting three states needs a destination
  listing three states, or the increment ships the disagreement the spec forbids.
- **Phase 5 and Phase 6 are independent** of Phases 3–4 and of each other. Both touch
  `_chrome_bar`, as does T014, so they are not parallel *with* T014 at the file level even
  though they are logically independent.
- **T002 gates Phase 6 alone.** Phases 3–5 proceed regardless of its result.

## Parallel opportunities

Within Phase 3: T009 (a `NAV` constant in `html.py`) is parallel with T003–T008 (`pages.py`),
and T012 with T010–T011.

Across phases: Phase 5's T018/T021 could run alongside Phase 3, since Phase 3 does not touch
the level pill. In a single-worker implementation this buys nothing, and the sequential order
is easier to bisect if something breaks.

T027 (docs) is parallel with everything once the behaviour is settled.

## Implementation strategy

**MVP = Phase 3 + Phase 4.** That is the reported defect, complete: work waiting on the
operator is counted on every view and reachable in one click, and all three parked states are
listed. Phases 5 and 6 are the second half of the issue — noise removal — and are genuinely
optional to the MVP, which is why they are P2 and why they are last.

Ship order: green baseline → verify the precondition → build the destination → point the count
at it → remove the two silent pills → document → full suite → walk it by hand.
