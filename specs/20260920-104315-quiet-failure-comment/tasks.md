---

description: "Task list for the quiet failure comment and the needs-me card that explains itself"
---

# Tasks: A failure comment that says nothing about my machine, and a needs-me card that says why

**Input**: Design documents from `specs/20260920-104315-quiet-failure-comment/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: required. Not by request — by the constitution's Development Workflow section:
"Every new or changed unit of behavior MUST ship with unit tests." Every behaviour task
below is preceded by the test that pins it.

**Organization**: by user story, so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: US1, US2, US3, mapping to the stories in spec.md

---

## Phase 1: Setup

**Purpose**: establish the baseline this feature is measured against.

- [ ] T001 Confirm the suite is green before any edit: `uv run pytest`

No project initialisation, no dependency, no tooling change. This feature edits four
existing functions in two existing modules.

---

## Phase 2: Foundational

**None.** There is no blocking prerequisite shared by the three stories. US1 touches
`src/robot_army/dispatch.py`; US2 and US3 touch `src/robot_army/web/pages.py` and share one
function, which is the only ordering constraint in the feature and is recorded under
Dependencies below.

**Checkpoint**: after T001, all three stories can begin.

---

## Phase 3: User Story 1 — A failed attempt tells a public issue nothing but where it happened (Priority: P1) 🎯 MVP

**Goal**: the comment posted on a failure carries the host and the work item number, and
nothing else, for every failure path.

**Independent Test**: drive a dispatch failure whose reason contains a home-directory path
and a settings filename; the posted body contains neither, and contains two labelled lines.

**Contract**: [contracts/failure-comment.md](./contracts/failure-comment.md)

### Tests for User Story 1

- [ ] T002 [P] [US1] Rewrite `test_a_failure_comment_names_the_host_and_fences_the_reason` in `tests/unit/test_issue_comments.py` as `test_a_failure_comment_names_the_host_and_the_item_and_nothing_else` — assert `- Host:` and `- Work item:` lines, assert no fence, and assert the body is exactly one opening line plus two `- Label: value` lines
- [ ] T003 [P] [US1] Add `test_a_failure_comment_cannot_be_given_a_reason` to `tests/unit/test_issue_comments.py` — assert via `inspect.signature` that `dispatch.failure_comment_body` takes no `reason` parameter, because the signature is what makes the omission structural rather than a rule review has to remember (research R2)
- [ ] T004 [P] [US1] Add `test_every_failure_path_posts_the_same_body` to `tests/integration/test_dispatch.py` — drive two failures with different causes on the same item and host, assert the two bodies are byte-identical (FR-002)
- [ ] T005 [P] [US1] Add `test_the_reason_leaves_the_comment_but_not_the_record` to `tests/integration/test_dispatch.py` — after a failure, assert the reason is absent from the posted body and present in all three of `work_items.failure_reason`, the `state.work_item` audit record's `detail.reason`, and the emitted notification's detail (FR-004, FR-005)

### Implementation for User Story 1

- [ ] T006 [US1] Change `failure_comment_body` in `src/robot_army/dispatch.py` to `(*, host: str, item_id: int)`, returning the two-line body from the contract; rewrite its docstring — the existing one argues *for* fencing the reason, and that argument is what this change overturns, so it is replaced by the reason it was overturned rather than deleted
- [ ] T007 [US1] Change `_comment_failure` in `src/robot_army/dispatch.py` to take no `reason`, passing `item.id` instead
- [ ] T008 [US1] Update the five `_comment_failure` call sites in `src/robot_army/dispatch.py` (lines 1077, 1125, 1172, 1208, 1293) to stop passing the reason; each keeps its `reason` local, which `_fail` still requires
- [ ] T009 [US1] Update `tests/integration/test_dispatch.py:1216` — `"trust check failed" in body` becomes an assertion that it is **not** in the body; the assertion two lines below that it *is* on `item.blocked_reason` already exists and stays

**Checkpoint**: the leak is closed. Independently shippable — the reason is still reachable
from `robot-army show`, the item page and `/queue`'s blocked table without US2.

---

## Phase 4: User Story 2 — The needs-me page says why an item failed (Priority: P1)

**Goal**: a failed item's card carries the recorded reason, without a click through.

**Independent Test**: seed one failed item with a reason, render `/interrupted`, and find the
reason on its card.

**Contract**: [contracts/needs-me-card.md](./contracts/needs-me-card.md)

### Tests for User Story 2

- [ ] T010 [P] [US2] Add `test_a_failed_card_says_why_it_failed` to `tests/unit/test_web_views.py` — seed a failed item with a `failure_reason`, assert the text appears in the `/interrupted` HTML (FR-006)
- [ ] T011 [P] [US2] Add `test_a_failure_reason_is_rendered_as_text_not_markup` to `tests/unit/test_web_views.py` — seed a reason containing `<b>` and `&`, assert the escaped forms appear and the raw tag does not (FR-008, research R4)
- [ ] T012 [P] [US2] Add `test_a_failed_card_with_no_recorded_reason_says_so` to `tests/unit/test_web_views.py` — assert the stated absence, and that it is distinguishable from a card that simply has no reason element (FR-007)
- [ ] T013 [P] [US2] Add `test_interrupted_and_awaiting_cards_gain_no_empty_reason` to `tests/unit/test_web_views.py` — seed one item in each state and assert neither card carries a reason element (FR-013)
- [ ] T014 [P] [US2] Add `test_the_needs_me_json_carries_the_reason_the_page_shows` to `tests/unit/test_web_views.py` — assert `failure_reason` on the JSON row equals the text rendered on the card (FR-012)

### Implementation for User Story 2

- [ ] T015 [US2] In `_interrupted_card` in `src/robot_army/web/pages.py`, render `failure_reason or blocked_reason` for items in `failed`, falling back to a stated absence; use the same expression `/queue`'s blocked table uses (`pages.py:937`), and say in a comment why it is the same expression rather than a second rule
- [ ] T016 [US2] Add a `.card .reason` rule to `APP_CSS` in `src/robot_army/web/html.py` — the existing `.reason` rule is scoped to `.banner`, so an unstyled reason inside a card would render at body weight with no separation from the signals above it

**Checkpoint**: US1 and US2 together are the whole of the reported defect. The banner is
still wrong for a gate-blocked item, which US3 fixes.

---

## Phase 5: User Story 3 — A card claims the checkout is missing only when there was one (Priority: P1)

**Goal**: an item refused before anything was created is not described as having lost
something.

**Independent Test**: render the page with a failed item that has no recorded checkout path
and find no banner; render with an item whose recorded checkout is gone and find one.

### Tests for User Story 3

- [ ] T017 [P] [US3] Add `test_an_item_that_never_had_a_checkout_is_not_told_it_lost_one` to `tests/unit/test_web_views.py` — seed a failed item with no `worktree_path`, assert no banner text in the HTML **and** `worktree_missing is False` in the JSON (FR-010, FR-012)
- [ ] T018 [P] [US3] Extend `test_a_missing_checkout_is_surfaced_distinctly` in `tests/unit/test_web_views.py` to assert the banner still appears for a recorded-but-absent path in the `failed` state as well as `interrupted` (FR-011); the existing assertion sets a `worktree_path` and needs no change
- [ ] T019 [P] [US3] Add `test_a_failed_card_can_carry_both_a_reason_and_a_missing_checkout` to `tests/unit/test_web_views.py` — assert the reason is not displaced by the banner when both apply (spec US3 scenario 4)

### Implementation for User Story 3

- [ ] T020 [US3] In `_signal_row` in `src/robot_army/web/pages.py`, redefine `worktree_missing` as "a checkout path is recorded **and** it is not present"; document in a comment that the field is what makes the claim, so conditioning only the banner would leave the same false claim in the JSON (research R6, data-model.md)
- [ ] T021 [US3] Update the banner's surrounding comment in `_interrupted_card` in `src/robot_army/web/pages.py` to record the narrowing and the case that motivated it — a gate refusal fails the item before `worktree.prepare` runs, so a blocked item has no path

**Checkpoint**: all three stories complete. The item 126 scenario now renders correctly and
comments quietly.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T022 [P] Update `docs/guide/5-outcome.md` — line 41 ("A failed attempt gets its own comment naming the host and the reason") is now false. State what the comment carries, why it carries so little, and the three places the reason went instead
- [ ] T023 [P] Update `docs/guide/operating.md` — the needs-me section now describes a failed card that explains itself, and a missing-checkout warning that means something narrower
- [ ] T024 Add the end-to-end test for SC-005 in `tests/integration/test_dispatch.py` — onboard a repository, add committed tool-permission settings at its base ref afterwards, dispatch, and assert both halves at once: a comment naming only a host and an item number, and a needs-me card naming the fingerprint reason with no missing-checkout banner
- [ ] T025 Run the full suite: `uv run pytest`
- [ ] T026 Walk [quickstart.md](./quickstart.md) — including the `--effect-level local` recipe that prints the body that would have been posted

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (T001)**: no dependencies
- **Foundational**: none exists
- **US1 (Phase 3)**: after T001. Touches only `dispatch.py` and its tests
- **US2 (Phase 4)**: after T001. Touches `web/pages.py` and `web/html.py`
- **US3 (Phase 5)**: after T001. Touches `web/pages.py`
- **Polish (Phase 6)**: after the stories it documents

### The one ordering constraint inside the feature

US2's T015 and US3's T021 both edit `_interrupted_card`. They are not parallel with each
other. T020 (`_signal_row`) is a different function and is parallel with T015.

US1 is parallel with everything in US2 and US3 — a different module entirely.

### Within each story

Tests before implementation, as written. Not because TDD is mandated — the constitution
explicitly does not mandate it — but because T002, T009 and T018 are edits to assertions
that currently pass by asserting the wrong thing, and changing them first is what makes the
implementation's effect visible.

### Parallel opportunities

- T002–T005 (US1 tests) — four files' worth of edits across two files, independent
- T010–T014 (US2 tests) — all in `test_web_views.py`, independent functions
- T017–T019 (US3 tests) — same
- T022 and T023 — different guide pages
- US1's implementation (T006–T009) runs in parallel with all of US2 and US3

---

## Parallel Example: starting all three stories at once

```bash
# US1, in dispatch.py and its tests
Task: "Rewrite the failure-comment unit test (T002)"
Task: "Add the signature test (T003)"

# US2 and US3, in the web tests — independent of US1 and of each other
Task: "Add the failed-card reason tests (T010–T014)"
Task: "Add the banner-condition tests (T017–T019)"
```

---

## Implementation Strategy

### MVP

**US1 alone.** It closes the leak, which is the half of this that is live on a public
repository right now. The reason stays reachable from `robot-army show`, the item page and
`/queue`'s blocked table, so shipping US1 without US2 leaves the operator informed — just
one page less conveniently than they should be.

### Incremental

1. T001 → baseline
2. US1 → the leak is closed → **shippable**
3. US2 → the destination of the chrome pill explains what it counted
4. US3 → the card stops contradicting the reason directly above it
5. Phase 6 → the guide matches the behaviour, and SC-005 is pinned end to end

### Notes

- Commit per story, atomically, with a message explaining why — the constitution's
  Development Workflow requires the why, and for US1 the why is Principle V.
- The comment already on `DecaturMakers/kiosk-show-replacement#26` is not touched by any
  task here. Nothing in this system edits or deletes a posted comment; it is a manual
  deletion, recorded in quickstart.md.
