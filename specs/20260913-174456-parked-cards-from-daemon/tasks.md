---
description: "Task list for issue #74 — parked is judged by the daemon's ignore list"
---

# Tasks: Parked Is Judged by the Daemon's Ignore List

**Input**: Design documents from `specs/20260913-174456-parked-cards-from-daemon/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/parked-in-force.md](contracts/parked-in-force.md)

**Tests**: required by the constitution — one task per contract case group.

**Organization**: grouped by user story. US1 and US2 share the foundational heartbeat and resolver
work in Phase 2; after that each is independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1, US2, US3 from spec.md

---

## Phase 1: Setup

No setup: no dependency, config key, table or module is added.

---

## Phase 2: Foundational (blocking)

**Purpose**: the daemon publishes its ignore list; a non-daemon reader can believe it; `operations`
resolves the list in force. Every story depends on this.

- [ ] T001 [P] Add `ignore_lists: list[str] | None = None` to `Heartbeat` and an `ignore_lists`
  keyword to `write_heartbeat` in `src/robot_army/health.py` (written as a list, `None` default so an
  older heartbeat parses); docstring in the style of `max_concurrent_sessions`'s, citing issue #74
- [ ] T002 In `src/robot_army/health.py`, factor `published_cap`'s running/pid-match checks into a
  private `_holders_heartbeat(report, *, running, lock_holder) -> dict | None`, have `published_cap`
  call it (behaviour unchanged), and add
  `published_ignore_lists(report, *, running, lock_holder) -> tuple[str, ...] | None` that believes
  only a list of non-empty strings (research R2)
- [ ] T003 [P] Tests H1–H2 and B1–B6 in `tests/unit/test_health.py`, beside the issue #30 block,
  reusing `report_with`/`write_at`; confirm the existing `published_cap` tests pass unchanged
- [ ] T004 Publish `ignore_lists=list(self.config.trello.ignore_lists) if self.config.trello else None`
  from `Daemon._heartbeat` in `src/robot_army/daemon.py`, with a comment naming issue #74
- [ ] T005 [P] Tests H3–H4 (the daemon's own heartbeat carries `["Icebox"]` with a board, `null`
  without) in `tests/unit/test_ignored_lists.py`, constructing a `Daemon` as `tests/unit/test_pause.py`'s
  `make_daemon` does and calling `_heartbeat()`
- [ ] T006 In `src/robot_army/operations.py`: change `card_is_parked(card, config)` to
  `card_is_parked(card, ignore_lists)`; add `resolve_ignore_lists(config, published)` returning
  `(in_force, configured_if_different)` compared as sets (research R3, data-model table); add
  `_published_ignore_lists(ctx)` taking its own reading like `_enforced_cap`; add the mismatch
  sentence builder (contract text, lists sorted)
- [ ] T007 [P] Tests R1–R4 for `resolve_ignore_lists` and the mismatch sentence in
  `tests/unit/test_ignored_lists.py`

**Checkpoint**: heartbeat carries the list; the resolver is tested; nothing renders it yet.

---

## Phase 3: User Story 1 — the web cards page does not count a parked card (P1) 🎯 MVP

**Goal**: `/cards` judges parkedness against the daemon's published list, taken from the request's
single reading.

**Independent Test**: contract L1–L3 and L6 — web config with no ignore list, daemon publishing
`["Icebox"]`, one `needs_info` card in `Icebox` and one in `Inbox`.

- [ ] T008 [US1] `operations.cards(..., published_ignore_lists=_OWN_READING)` in
  `src/robot_army/operations.py`: resolve the list in force once (own reading only when the sentinel
  is passed), use it for every row's `parked`, and add `ignore_lists`, `configured_ignore_lists`,
  `ignore_list_disagreement` to the configured payload (data-model)
- [ ] T009 [US1] In `src/robot_army/web/server.py`, compute
  `health.published_ignore_lists(report, running=running, lock_holder=lock.holder)` from the
  request's existing reading, pass it in the handler `params` beside `capacity`, and have `view_cards`
  hand it to `pages.cards_view`
- [ ] T010 [US1] `pages.cards_view(ctx, *, include_simulated, published_ignore_lists=...)` in
  `src/robot_army/web/pages.py` passes it to `operations.cards` and renders
  `ignore_list_disagreement` as a `banner warn` when present; comment on the held/parked split
  updated to say whose list decides
- [ ] T011 [US1] Tests L1–L3 and L6 in `tests/unit/test_web_views.py`, through the `web` harness with
  `running_daemon` and `beat(layout, ignore_lists=[...])`, the harness config given
  `board_config`'s `[trello]` section with no ignore list; seed the two cards with the
  `tests/unit/test_ignored_lists.py` helpers or equivalent `db` inserts. Assert
  `awaiting clarification (1)`, the reason prefix, one rescan link, the banner, and (L6) that
  `operations._published_ignore_lists` is not called during the request

**Checkpoint**: the issue's reproduction passes on the web.

---

## Phase 4: User Story 2 — the terminal and the web agree (P1)

**Goal**: `robot-army cards` and the card on a work item use the same list in force.

**Independent Test**: contract L4–L5.

- [ ] T012 [US2] `_card_for_item` in `src/robot_army/operations.py` uses
  `resolve_ignore_lists(ctx.config, _published_ignore_lists(ctx))[0]` (research R4), reading only
  when a card was found
- [ ] T013 [US2] `operations.cards` prints `ignore_list_disagreement` after the table when present,
  in `src/robot_army/operations.py`
- [ ] T014 [P] [US2] Tests L4–L5 in `tests/unit/test_ignored_lists.py` using `running_daemon`,
  `beat(layout, ignore_lists=[...])` and `listing_context`: the stale-file direction shows
  `parked in 'Icebox'` and the sentence; agreement leaves output unchanged and the key `None`
- [ ] T015 [P] [US2] Update the existing call sites and tests of `card_is_parked` to the new
  signature (grep `card_is_parked` in `src/` and `tests/`)

**Checkpoint**: both surfaces give one answer.

---

## Phase 5: User Story 3 — a disagreement is visible (P3)

Delivered by T006 (sentence), T010 (web banner) and T013 (terminal line); tested in T011 and T014.

- [ ] T016 [US3] Assert in `tests/unit/test_web_views.py` that no mismatch banner appears on
  `/cards` when the lists agree and when no daemon is running (contract L2)

---

## Phase 6: Polish

- [ ] T017 [P] `docs/guide/state.md`: the heartbeat also carries `ignore_lists`, why, the `jq`
  line, and that an older heartbeat or `null` is *not published*
- [ ] T018 [P] `docs/guide/2-intake.md` "Parking a card": parkedness on both listings is the running
  daemon's decision, and the `IGNORE LIST MISMATCH` line and what to restart
- [ ] T019 Run `uv run pytest` and `uv run ruff check` (if configured in `pyproject.toml`); all pass

---

## Dependencies

- T001 → T002 → T003; T001 → T004 → T005; T006 → T007
- Phase 2 → US1 (T008 → T009 → T010 → T011) and US2 (T012–T015; T013 depends on T008)
- US3 depends on T010 and T013
- Docs (T017, T018) can be written any time after Phase 2; T019 last

## Parallel Opportunities

- T001 alongside T006 (different files); T003, T005, T007 once their subjects exist
- T014 and T015 together; T017 and T018 together

## Implementation Strategy

Phase 2, then US1 — the reported defect — and verify with T011. Then US2 so the terminal cannot
disagree the other way, US3's one negative test, docs, and the full suite. One commit for the
spec/plan/tasks, one for the code and tests, one for the docs.
