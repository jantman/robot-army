---

description: "Task list for reset, the web control, and the operating quick reference"
---

# Tasks: A first-class "start over", and an operating page you can scan

**Input**: Design documents from `specs/20260918-201447-reset-item-quick-reference/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: **Required, not optional.** The constitution's Development Workflow section makes unit
tests mandatory for every new or changed unit of behaviour, and requires failure- and
interruption-path tests additionally for state machines and persistence. Both apply here.

**Organization**: grouped by user story, in the dependency order plan.md sets out, so each group
is independently committable with a message that explains why.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: US1 (the command), US2 (the web control), US3 (the quick reference)

## Path Conventions

Single package at the repository root: `src/robot_army/`, `tests/unit/`, `tests/integration/`,
`docs/guide/`.

---

## Phase 1: Setup

**Purpose**: confirm the ground before changing it. No project initialisation is needed — this is
an established repository.

- [X] T001 Run `uv sync && uv run pytest` and record the suite as green, so any later failure is
      known to be this feature's and not inherited.
- [X] T002 Verify the three code facts the plan rests on, and correct the plan if any is wrong:
      that `dispatch.dispatch_item` rebuilds a missing worktree for a `ready` item
      (`src/robot_army/dispatch.py`), that it composes the prompt from the stored `title`/`body`
      (`src/robot_army/dispatch.py`), and that `ordering.order_key` ranks on `discovered_at` and
      never on `ready_at` (`src/robot_army/ordering.py`).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the state machine has to admit the route before any surface can offer it. Everything
in Phase 3 and Phase 4 depends on this.

**⚠️ CRITICAL**: no user story work can begin until T003 and T004 are done.

- [X] T003 Add `(WorkItemState.INTERRUPTED, WorkItemState.READY)` and
      `(WorkItemState.AWAITING_REVIEW, WorkItemState.READY)` to `WORK_ITEM_TRANSITIONS` in
      `src/robot_army/states.py`, with a comment saying which verb needs them and why the route
      did not exist before.
- [X] T004 Extend `tests/unit/test_states.py`: both new transitions are legal; `active → ready`,
      `done → ready`, `abandoned → ready` and `dispatching → ready` are still illegal and still
      raise `IllegalTransition`. The illegal cases are the point — the table is enumerated in
      that test so widening it cannot go unnoticed.

**Checkpoint**: the machine permits the route. Commit: why the two transitions exist.

---

## Phase 3: User Story 1 — `robot-army reset <id>` (Priority: P1) 🎯 MVP

**Goal**: one command that discards the checkout and branch, re-reads the issue, re-checks
eligibility through the poller's own evaluation, and returns the item to the queue.

**Independent Test**: take an `interrupted` item with a checkout on disk, edit its issue body on
GitHub, run `robot-army reset <id>`, and confirm the checkout and branch are gone, the stored
body matches the edit, the item is `ready`, and the audit log reconstructs every step. This is
the MVP: it removes the hand-written `UPDATE` against `state.db` entirely.

### The shared read path

- [X] T005 [US1] Extract the middle of `retry` into `_reread_and_refresh(ctx, item, *, verb,
      trust_file)` in `src/robot_army/operations.py`: the `_local_blocker` check, the live read,
      the four-column refresh, and `poll.evaluate`. It returns either a refusal `Result` or the
      verdict. Audit names take the verb — `f"{verb}.blocked"`, `f"{verb}.evaluate"`. Keep
      `_retry_unread`'s two causes (`issue_unreachable`, `issue_absent`) distinguishable and
      parameterise its action name the same way. Carry `retry`'s existing docstring reasoning
      about why the poller's function is *called* and not reimplemented onto the helper — it is
      the reason the helper exists.
- [X] T006 [US1] Rewrite `retry` in `src/robot_army/operations.py` as: gate on `failed` → the
      helper → transition to `ready`. Its observable behaviour, wording and exit codes must be
      unchanged.
- [X] T007 [US1] Run `uv run pytest tests/unit/test_operations_retry.py` and confirm it passes
      untouched. If any assertion needs editing, the refactor changed behaviour and is wrong —
      fix the code, not the test.

### The command

- [X] T008 [US1] Implement `reset(ctx, item_id, *, force=False, assume_yes=False, confirm=_ask,
      trust_file=None)` in `src/robot_army/operations.py`, wearing `@_guards_its_prompt`, in the
      order [contracts/reset.md](contracts/reset.md) fixes: item exists → state accepted
      (`interrupted`, `awaiting_review`, `failed`) → `_reread_and_refresh` → confirmation →
      discard → transition. Open a `reset` intent/outcome pair around the whole operation, with
      the intent flushed before anything is destroyed.
- [X] T009 [US1] Within `reset`, call `operations.worktree_remove(ctx, item_id, force=force)`
      whole for the discard — never `_remove_checkout` directly, which would take the disk half
      without the four guards. Skip it entirely when the item has no `worktree_path`. **Branch on
      `result.data["worktree_removed"]`, not on `result.code`** (research R3): a removal that
      leaves an unmerged branch exits non-zero with the worktree already gone, and stopping there
      would abort with the destruction done. Carry the removal's lines into reset's own output so
      a retained branch is still reported.
- [X] T010 [US1] Add the confirmation (research R4): without `--force` and without `assume_yes`,
      ask `[y/N]` through `_answer_or_give_up`, naming the checkout path and the branch, placed
      immediately before the discard. With `--force`, ask nothing — `worktree_remove`'s
      typed-item-id prompt is the question. Refusal messages name the remedy: `cancel` for an
      `active` item, `--force` for git's refusal, `robot-army cancel <id>` for an open session.
- [X] T011 [US1] Add the `reset` subparser to `src/robot_army/cli.py` with `item_id`, `--force`
      and `--yes`, and its entry in the dispatch dict. The parser's `description` is the same
      single string the web confirmation page shows, as `retry` already does — one string, two
      surfaces, so they cannot disagree.

### Tests for User Story 1

- [X] T012 [P] [US1] `tests/unit/test_operations_reset.py` — success paths: from `interrupted`,
      from `awaiting_review`, and from `failed`. Assert the four content columns come from the
      read, `worktree_path` is cleared, `failure_reason` and `blocked_reason` are cleared, the
      state is `ready`, and `discovered_at` is untouched.
- [X] T013 [P] [US1] `tests/unit/test_operations_reset.py` — **the ordering test (research R1)**:
      on an ineligible verdict the checkout is **still on disk** and `worktree_path` is still
      set, while the content columns *have* been refreshed. This pins the one real design
      decision and is the test that fails if someone later reorders the steps.
- [X] T014 [P] [US1] `tests/unit/test_operations_reset.py` — **the retained-branch test (research
      R3)**: worktree removed, git keeps the unmerged branch, and the item still reaches `ready`
      with the warning reported in the output.
- [X] T015 [P] [US1] `tests/unit/test_operations_reset.py` — refusals, one test each: no such
      item; each unaccepted state (`discovered`, `ready`, `dispatching`, `active`, `done`,
      `abandoned`); unresolved repository; a dispatch gate still blocking; issue unreachable;
      issue absent; issue ineligible **because the author changed** — that case specifically,
      since it is why the read path is shared and not copied. Each asserts a non-zero exit and an
      unchanged state.
- [X] T016 [P] [US1] `tests/unit/test_operations_reset.py` — confirmation paths: declined `[y/N]`
      aborts and destroys nothing; abandoned at EOF returns the recorded refusal rather than a
      traceback; `--force` asks the typed id and a wrong answer aborts; `assume_yes=True` asks
      nothing and still never overrides git.
- [X] T017 [P] [US1] `tests/unit/test_operations_reset.py` — guard passthrough: an open session
      row refuses and names `cancel`; git's dirty-tree refusal refuses and names `--force`; a
      removal already on record refuses. These prove the guards are reached, not re-implemented.
- [X] T018 [P] [US1] `tests/unit/test_operations_reset.py` — **interruption paths**: an item
      whose worktree was already removed and `worktree_path` already cleared (a reset killed
      between the discard and the transition) is completed by a second `reset`; an item refreshed
      but not transitioned is completed by a second `reset`. Assert no state is half-written.
- [X] T019 [P] [US1] `tests/unit/test_operations_reset.py` — audit: a successful reset writes
      `reset` intent, `reset.evaluate`, the `worktree.remove` pair, `state.work_item` and the
      `reset` outcome, in that order; a refusal writes the intent and an outcome carrying
      `refused_by`. Assert the intent precedes the destruction.
- [X] T020 [P] [US1] `tests/unit/test_operations_reset.py` — simulation: below the level at which
      version control is real, the removal reports "would remove", nothing on disk is touched,
      and the level that would make it real is named.
- [X] T021 [P] [US1] Extend `tests/unit/test_cli_exit_codes.py` (or the nearest equivalent) so
      `reset` exits non-zero on refusal, and add it to `tests/unit/test_named_commands.py` if
      that test enumerates the verbs.

**Checkpoint**: `robot-army reset <id>` works end to end from a terminal. Commit: why the verb
exists and why it reads before it destroys.

---

## Phase 4: User Story 2 — the web control (Priority: P2)

**Goal**: the same operation from `/item/<id>`, behind the confirmation machinery every other
mutating action already uses, and unable to override git.

**Independent Test**: `/item/<id>` for an `interrupted` item offers a reset control; following it
reaches a confirmation page that says what will be destroyed and links back; submitting performs
it. `/item/<id>` for an `active` item offers nothing and a direct POST is refused `409`.

- [X] T022 [US2] Add the `reset` entry to `ITEM_ACTIONS` in `src/robot_army/web/pages.py` per
      [contracts/web-reset.md](contracts/web-reset.md): `confirm=True`, `danger=True`,
      `needs_daemon=False`, `effect_guarded=True`, `item_states=(INTERRUPTED, AWAITING_REVIEW,
      FAILED)`, and the description that names all four consequences. No `session_states`: the
      open-session refusal belongs to `worktree_remove`, asked at the moment of the POST.
- [X] T023 [US2] Add `Route(POST, ("item", "<id>", "reset"), _inline_item_action("reset",
      message="reset", run=lambda ctx, item_id: operations.reset(ctx, item_id,
      assume_yes=True)), terminal="reset")` to `ROUTES` in `src/robot_army/web/server.py`.
      `assume_yes=True` and **never** `force=True` — that is FR-019, satisfied by not passing a
      flag rather than by writing a check.
- [X] T024 [P] [US2] Extend `tests/unit/test_web_actions.py`: the control is offered in exactly
      the three accepted states and in no others; a POST from any other state is refused `409`
      naming the state and what is currently legal; the confirmation page renders the description
      and a link back; legality is re-checked at submission against state read then.
- [X] T025 [P] [US2] Extend `tests/unit/test_web_actions.py`: the web never passes `force` —
      assert it directly, by spying on the call — and an item whose checkout holds uncommitted
      work is refused on the page with git's reason and the terminal remedy, not redirected as
      though it succeeded.
- [X] T026 [P] [US2] Extend `tests/unit/test_web_routing.py` if it enumerates routes, so the new
      one is covered by whatever that test asserts about all of them.

**Checkpoint**: an interrupted item can be started over from the phone. Commit: why the control
is offered where it is, and why the web cannot force.

---

## Phase 5: User Story 3 — the operating quick reference (Priority: P2)

**Goal**: a page that answers "what do I type" at 2am, and a test that stops it rotting.

**Independent Test**: answer, from `docs/guide/operating.md` alone and without following a link:
what can I do from `interrupted`; what does `abandon` refuse; can I start an item over from my
phone; what do I type when the disk is full.

- [X] T027 [US3] Move the reasoning that belongs elsewhere out of `docs/guide/operating.md`, per
      the table in [contracts/operating-page.md](contracts/operating-page.md): live blocker
      re-checking → `docs/guide/3-selection.md`; cleanup's guards and what a retained branch
      means → `docs/guide/5-outcome.md`; reboot and interrupted-at-X behaviour →
      `docs/guide/state.md`. Move it, do not delete it, and do not duplicate it.
- [X] T028 [US3] Rewrite `docs/guide/operating.md` in the eight-section order the contract fixes:
      purpose · recipes · state table · command table · where things live · the web interface
      (keeping the no-authentication warning **in full** — a safety statement is not reasoning) ·
      reading the logs · where the why lives. Recipes first, because that is why the page is
      open.
- [X] T029 [US3] Write the state table: one row per `WorkItemState` member — state, what it
      means, which commands are legal from it, what it becomes next. `reset` appears on
      `interrupted`, `awaiting_review` and `failed`.
- [X] T030 [US3] Write the command table: one row per subcommand the parser defines — command,
      what it does, when to reach for it, what it refuses, and `Web` / `Terminal` / `Both`.
      `reset` is `Both`; `reset --force` is `Terminal`.
- [X] T031 [US3] Write the five recipes, a few lines each with the commands in order: an item is
      stuck · I want to start over · something is running that should not be · the daemon looks
      dead · the disk is full.
- [X] T032 [P] [US3] Write `tests/unit/test_operating_reference.py`: parse both tables out of the
      page and assert the state set equals `WorkItemState`'s members, the command set equals the
      parser's subcommands (read from the parser, never a hand-kept list), the `Where` column
      holds only the three permitted values, and the five recipe headings are present.
- [X] T033 [P] [US3] Update `docs/guide/state.md` for the two new transitions — the table, the
      prose about what `ready` is reachable from, and the note that `retry` is no longer the only
      path that refreshes the four columns.
- [X] T034 [P] [US3] Add `reset`, `reset.blocked` and `reset.evaluate` to
      `docs/guide/audit-log.md` with their record shapes, per [data-model.md](data-model.md).
- [X] T035 [P] [US3] Update `docs/guide/3-selection.md` and `docs/guide/5-outcome.md` where they
      name `retry` as the only way back to the queue, so they name `reset` too and say how the
      two differ: `retry` re-reads without discarding, `reset` discards as well.
- [X] T036 [P] [US3] Check `docs/guide/index.md` and `README.md` for anything the rewrite
      falsifies. `README.md` must stay under 150 lines — `tests/unit/test_docs_links.py` enforces
      it.

**Checkpoint**: the page answers the four questions and a test holds it to the program. Commit:
why the page changed shape.

---

## Phase 6: Polish & Cross-Cutting

- [X] T037 Run `uv run pytest` — the whole suite, green. Nothing is complete until it is.
- [X] T038 Walk [quickstart.md](quickstart.md) end to end against a dry-run item, including the
      browser scenarios, and correct anything it gets wrong.
- [X] T039 Confirm research R9 still holds: no key was added to `config.py`'s `_KNOWN_KEYS` or
      `_REPO_KEYS`, so `tests/unit/test_example_config_drift.py` is green and
      `share/config.example.toml` needs no regeneration. If a key did creep in, regenerate it.
- [X] T040 Re-read the Constitution Check in [plan.md](plan.md) against what was actually built,
      and correct the plan if the implementation diverged. The check is a gate, not a formality.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no dependencies.
- **Phase 2 (Foundational)**: blocks everything. The state machine must admit the route first.
- **Phase 3 (US1)**: depends on Phase 2. Delivers the MVP on its own.
- **Phase 4 (US2)**: depends on Phase 3 — the control calls `operations.reset`.
- **Phase 5 (US3)**: depends on Phase 3 for the command table's content and on Phase 4 for its
  `Where` column. T027 (moving displaced prose) has no code dependency and can start any time.
- **Phase 6 (Polish)**: last.

### User Story Dependencies

- **US1 (P1)**: independent once Phase 2 is done. This is the MVP.
- **US2 (P2)**: needs US1's operation to exist. Cannot be built first.
- **US3 (P2)**: needs US1 and US2 to exist before its tables can be truthful, which is the whole
  point of the test that checks them. Its prose-moving task is independent.

### Within Each Story

- T005 → T006 → T007 strictly in order: extract, rewrite the caller, prove nothing moved.
- T008 → T009 → T010 build one function and must land together.
- All of T012–T021 depend on T011 and on each other only through the file they share.

### Parallel Opportunities

- T012–T021 are one file; write them together, then run them together.
- T024–T026 are separate web test files and are genuinely parallel.
- T033, T034, T035 and T036 touch four different guide pages and are genuinely parallel.

---

## Implementation Strategy

### MVP first

1. Phase 1, then Phase 2 — the transitions, with their test.
2. Phase 3 — the command, with its tests.
3. **Stop and validate**: `robot-army reset <id>` against a dry-run item, and read the audit log
   for it. At this point the hand-written `UPDATE` the issue complains about is gone.

### Then, incrementally

4. Phase 4 — the web control. The operation is reachable from the phone.
5. Phase 5 — the page, which can now describe what exists rather than what is planned.
6. Phase 6 — the suite, the quickstart, and the Constitution Check re-read.

Each phase is one commit or a small group of them, and each explains why the change was made.

---

## Notes

- **The two tests that pin decisions are T013 and T014.** Everything else can be re-derived from
  the contracts; those two encode judgements — destroy last, and do not trust the exit code —
  that a later reader would otherwise have to rediscover by breaking them.
- Do not add a config key. Do not add a dependency. Do not make a terminal state resettable —
  that is out of scope in [spec.md](spec.md) and changing it here would be scope creep.
- The issue's sub-question about author-written issue comments reaching the prompt is **not** in
  this feature. If it comes up during implementation, it belongs in its own issue.
