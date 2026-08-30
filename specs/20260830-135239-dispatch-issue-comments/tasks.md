---

description: "Task list for: Say on the issue which machine and which session picked it up"
---

# Tasks: Say on the issue which machine and which session picked it up

**Input**: Design documents from `/specs/20260830-135239-dispatch-issue-comments/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/issue-comment.md](./contracts/issue-comment.md)

**Tests**: Included, and **not optional here**. The constitution's Development Workflow requires
unit tests for every new or changed unit of behaviour, and requires the full suite to pass before
the feature is complete. Test tasks are therefore ordinary tasks, not a bonus phase.

**Organization**: grouped by user story, so each can be implemented, tested and stopped at
independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: US1, US2, US3 from [spec.md](./spec.md)
- Paths are repository-relative from `/home/jantman/worktrees/robot-army/issue-38`

## Path Conventions

Single Python package: `src/robot_army/`, tests in `tests/unit/` and `tests/integration/`.

**The whole feature touches five files**: `src/robot_army/dispatch.py`, `src/robot_army/db.py`,
`tests/unit/test_issue_comments.py` (new), `tests/integration/test_dispatch.py`, and the two
documents in the polish phase. Tasks within a story that name the same file are deliberately
**not** marked `[P]`.

---

## Phase 1: Setup

**Purpose**: know the starting state, so a later failure is attributable to this work.

- [X] T001 Run `uv run pytest` and record that the suite passes on `issues/38` before any edit, so any later failure is this feature's; the suite lives in `tests/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the one fact every comment variant needs. Nothing else is shared.

**⚠️ Both user stories consume `host_name()`. Neither can be finished before it exists.**

- [X] T002 Add module-level `host_name() -> str` to `src/robot_army/dispatch.py` returning `os.uname().nodename`, or the literal `"unknown"` when that is empty or `os.uname()` raises (FR-009, research R2); add the `os` import if absent
- [X] T003 Create `tests/unit/test_issue_comments.py` with the `host_name()` cases: a normal nodename is returned verbatim, an empty nodename becomes `"unknown"`, and an `os.uname()` that raises becomes `"unknown"` — drive all three by `monkeypatch.setattr(os, "uname", …)`, no git and no `requires_git` marker

**Checkpoint**: `uv run pytest tests/unit/test_issue_comments.py` passes; user story work can begin.

---

## Phase 3: User Story 1 - The issue names the machine and the session working on it (Priority: P1) 🎯 MVP

**Goal**: every dispatch comment carries the host, the session name and the session id; every
failure comment carries the host.

**Independent Test**: dispatch an item at `--effect-level local`, read the simulated
`github.comment` record's `detail.body` from the audit log, and confirm it names this machine,
`ra-<repo>-<number>`, and the session UUID — then confirm `dispatch.confirmed` carries the same
three values. Delivers the issue's core correlation on its own; a reassignment simply gets the
first-dispatch wording until US2 lands.

### Implementation for User Story 1

- [X] T004 [US1] Add pure `dispatch_comment_body(*, host, session_name, session_id, branch, worktree_path, attempt=1, previous_session_id=None, resumed=False) -> str` to `src/robot_army/dispatch.py`, implementing only the first-dispatch variant of [contracts/issue-comment.md](./contracts/issue-comment.md) §1 (the reassignment branch is T012); one labelled line per fact, values in backticks
- [X] T005 [US1] Add pure `failure_comment_body(*, host, reason) -> str` to `src/robot_army/dispatch.py` per contracts §3 — the existing wording, a `- Host:` line, and the reason in a fenced block
- [X] T006 [US1] Rewrite `_comment_dispatch` and `_comment_failure` in `src/robot_army/dispatch.py` (around lines 1158-1181) to call the two builders and pass the result to `_safe_comment`; `_comment_dispatch` gains `session_name`, `attempt` and `previous_session_id` parameters, `_comment_failure` calls `host_name()` itself, and both drop the unused `config` parameter they carry today
- [X] T007 [US1] At the call site in `_dispatch_item` (`src/robot_army/dispatch.py:1032`) pass `host_name()`, `plan.title` as the session name, and the local `attempt`; leave the call in its current position — after confirmation, the notification, the board update and `dispatch.confirmed` — because that placement is what makes FR-006 structural
- [X] T008 [US1] Extend the `dispatch.confirmed` record's `detail` in `src/robot_army/dispatch.py` (around line 987) with `host`, `session_name` and `attempt`, taken from the same variables T007 passes to the comment so the log and the issue cannot disagree (FR-002, contracts §5)

### Tests for User Story 1

- [X] T009 [P] [US1] Add body-rule cases to `tests/unit/test_issue_comments.py`: the first-dispatch body names host, session name, session id, branch and worktree; an unknown host still renders a `Host:` line rather than an empty or absent one; the failure body carries the host line and fences the reason
- [X] T010 [P] [US1] Add integration cases to `tests/integration/test_dispatch.py` beside `test_a_confirmed_dispatch_reaches_active`: the single body captured by `RecordingWriter` contains this machine's `host_name()`, `ra-demo-<n>` and the session id from `db.latest_session_for_item`, and the emitted `dispatch.confirmed` record carries the identical three values
- [X] T011 [P] [US1] Add an integration case to `tests/integration/test_dispatch.py` asserting a blocked dispatch (reuse the untrusted-repository or hook-failure setup already there) posts a failure comment naming the host, and that the item is `failed` with the same reason

**Checkpoint**: US1 is complete and shippable. A dispatch comment now answers "which machine, which session".

---

## Phase 4: User Story 2 - A second attempt says it is a second attempt (Priority: P1)

**Goal**: a dispatch that is not the item's first says so, names the session it supersedes, and
says whether it continues that session's context.

**Independent Test**: dispatch an item, cancel it, resume it, cancel again, restart it — the issue
holds three comments; the second says `Continues:` with the first session's id, the third says
`Supersedes:` with the second's and states it starts without that context.

**Depends on**: Phase 3, because it extends `dispatch_comment_body` and the same call site.

### Implementation for User Story 2

- [X] T012 [P] [US2] Add `previous_session_for_item(conn, item_id, attempt)` to `src/robot_army/db.py` beside `latest_session_for_item` (line 381), selecting `WHERE work_item_id = ? AND attempt < ? ORDER BY attempt DESC LIMIT 1`; document in its docstring that the `attempt <` bound exists because our own session row already exists by the time the comment is written, so `latest_session_for_item` would return it (research R3, data-model.md)
- [X] T013 [US2] Extend `dispatch_comment_body` in `src/robot_army/dispatch.py` with the reassignment variant per contracts §2: the opening line states the attempt number, and exactly one of `Continues:` (a resume, naming the restored session), `Supersedes:` (naming the previous attempt, stating the new session starts without its context), or `Supersedes: no earlier session is on record` (FR-010) appears
- [X] T014 [US2] At the call site in `src/robot_army/dispatch.py` resolve the predecessor in the order research R3 fixes — `resume_session_id` first, then `db.previous_session_for_item(conn, item_id, attempt)`, then none — and pass it with `resumed=` to `_comment_dispatch`; do the lookup here, where `conn` already is, so `_comment_dispatch` stays free of database access
- [X] T015 [US2] Add `supersedes` to the `dispatch.confirmed` detail in `src/robot_army/dispatch.py`, present only when a predecessor was identified without a resume; leave `resumed_from` exactly as it is (contracts §5)

### Tests for User Story 2

- [X] T016 [P] [US2] Add cases to `tests/unit/test_issue_comments.py` for the three predecessor rules and the attempt number in the opening line, plus a case pinning that a resume's `Continues:` wins over a looked-up predecessor when both are supplied
- [X] T017 [P] [US2] Add a `previous_session_for_item` case to `tests/unit/test_issue_comments.py` using `seed_item`/`seed_session`: with rows at attempts 1, 2 and 3, asking for the predecessor of 3 returns attempt 2 and never attempt 3 itself, and asking for the predecessor of 1 returns `None`
- [X] T018 [P] [US2] Extend `test_a_resume_is_a_new_attempt_naming_what_it_restored` in `tests/integration/test_dispatch.py` to assert the second comment says "reassigned", names attempt 2, and names the restored session id
- [X] T019 [P] [US2] Add an integration case to `tests/integration/test_dispatch.py` for a restart (no `resume_session_id`): the comment names the previous attempt's session id under `Supersedes:` and states the new session starts without its context — the case that would have caught a session claiming to supersede itself

**Checkpoint**: the issue now reads as an ordered history of every session that held it.

---

## Phase 5: User Story 3 - The record survives GitHub being unavailable, and never lies (Priority: P2)

**Goal**: pin the three properties this feature must not break. These are existing behaviours;
this phase is tests, and code only if a test finds otherwise.

**Independent Test**: force a comment failure and confirm the session still reaches `active`;
dispatch below `live` and confirm nothing reaches the real writer.

**Depends on**: Phases 3 and 4, since the bodies these assert on are the ones those build.

### Tests for User Story 3

- [X] T020 [P] [US3] Extend `test_a_comment_failure_does_not_change_the_items_state` in `tests/integration/test_dispatch.py` to also assert the `github.comment` error record was written with the failure's reason — the failure is non-fatal *and* not silent (FR-007, and the Principle III exception the plan documents)
- [X] T021 [P] [US3] Add a case to `tests/unit/test_simulated_writers.py` asserting `SimulatedIssueWriter.comment` records the full body with `simulated: true` and posts nothing, so quickstart step 2's offline verification is guaranteed to have something to read (FR-008)
- [X] T022 [P] [US3] Add an integration case to `tests/integration/test_dispatch.py` asserting an unconfirmed launch leaves no comment claiming a running session — only the failure comment — which is FR-006 pinned at the call site rather than trusted to stay put

**Checkpoint**: all three stories done; every FR has a test.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T023 [P] Add a `## What it writes on the issue` section to `README.md`, immediately before `## Being told when something happens` (line 465): the three variants, what each names, that nothing is ever edited, and that below `live` nothing is posted (FR-012, research R8)
- [X] T024 [P] Add a paragraph to `docs/logging.md` near the `github.comment` intent/outcome example (line 139) saying `dispatch.confirmed` now carries `host`, `session_name`, `attempt` and `supersedes`, so a comment on an issue and a record in a log on some other machine can be matched
- [X] T025 Run `uv run ruff check src tests` and `uv run ruff format --check src tests`, fixing anything this feature introduced
- [X] T026 Run the full `uv run pytest` over `tests/` and confirm it passes — the constitution's completion gate, not a formality
- [X] T027 Walk [quickstart.md](./quickstart.md) steps 2 and 3 for real: dispatch at `--effect-level local --once` and confirm the logged body and the `dispatch.confirmed` values agree verbatim, with nothing posted to GitHub
- [X] T028 Self-review against `.specify/memory/constitution.md`: confirm `SCHEMA_VERSION` in `src/robot_army/migrations.py` is unmoved, `pyproject.toml` gained no dependency, `src/robot_army/config.py` gained no knob, and that the accepted gap — a comment lost to a crash is never retried — still matches what `plan.md` documents

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001)**: none.
- **Foundational (T002-T003)**: after Setup. **Blocks both user stories** — every comment variant renders a host line.
- **US1 (T004-T011)**: after Foundational. No dependency on US2 or US3.
- **US2 (T012-T019)**: after US1, because T013 extends the function T004 creates and T014 extends the call site T007 edits. This is a file-level dependency, not a conceptual one.
- **US3 (T020-T022)**: after US2, since it asserts on the finished bodies. T021 alone could run any time — it touches only the simulated writer.
- **Polish (T023-T028)**: after every story that is being shipped.

### Within each story

- Builders before wiring: T004/T005 → T006 → T007 → T008; T012/T013 → T014 → T015.
- Tests after the behaviour they describe. The constitution does not mandate test-first, and these
  bodies are easier to pin once their exact wording exists.

### Parallel Opportunities

- **T009, T010, T011** — three different test concerns, two different files, once T004-T008 are in.
- **T012** is independent of T013-T015 (different file: `db.py`), so it can be written first or alongside.
- **T016, T017, T018, T019** — once T013-T015 are in.
- **T020, T021, T022** — all independent of each other.
- **T023, T024** — two different documents.

Nothing in `src/robot_army/dispatch.py` is parallel with anything else in `src/robot_army/dispatch.py`. That is most of the feature, so the honest answer is that this is a mostly-sequential piece of work with parallel test writing at the end of each story.

---

## Parallel Example: User Story 1

```bash
# After T004-T008 are in place, these three are independent:
Task: "T009 body-rule cases in tests/unit/test_issue_comments.py"
Task: "T010 confirmed-dispatch comment cases in tests/integration/test_dispatch.py"
Task: "T011 failure-comment host case in tests/integration/test_dispatch.py"
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. T001 → T003 (foundation)
2. T004 → T011 (US1)
3. **Stop and validate**: quickstart step 2 shows a body naming host, session name and session id; step 3 shows the log agreeing. The issue's headline complaint — "which machine, which session" — is answered.
4. A reassignment at this point still posts the first-dispatch wording. That is a smaller lie than today's (which omits the host entirely), and it is fixed by US2.

### Incremental delivery

1. Foundation → US1 → **ship**; the correlation works.
2. US2 → **ship**; reassignments become readable.
3. US3 → the guarantees are pinned by tests rather than by habit.
4. Polish → the behaviour is documented where a reader will find it.

### Notes

- Commit after each story, with a message saying *why* — the constitution asks for atomic commits explaining the reason, not the diff.
- `[P]` means different files. Two tasks naming `src/robot_army/dispatch.py` are never `[P]`, whatever their numbers suggest.
- Do not move the comment call site. Its position is load-bearing for FR-006 and is the one thing a refactor here could quietly break.

---

## What actually happened

Three places where the work differed from the task as written. Recorded because a plan that
is quietly edited to match the outcome stops being evidence of anything.

- **T004** — the parameter is `worktree_path`, not `worktree`. `dispatch.py` already imports
  a module called `worktree`, and a keyword argument of that name shadows it inside the
  function. Legal, and a trap for the next person to add a line there.
- **T025** — `uv run ruff check src/ tests/` only. The repository is not `ruff format`
  managed: `ruff format --check` wants to reflow dozens of untouched functions, and CI
  (`.github/workflows/tests.yml`) runs the linter alone. Reformatting the tree would have
  buried this feature's diff in unrelated churn.
- **T027** — **not run against the live system.** `robot-army run --effect-level local
  --once` polls real GitHub, creates real worktrees and runs real preparation hooks against
  the maintainer's own state database; doing that unasked to verify a comment's wording is
  not a trade worth making. What it would have proved is proved mechanically instead:
  `test_a_simulated_comment_records_the_whole_body_and_posts_nothing` pins that the full
  body reaches the log below `live`, and
  `test_the_dispatch_comment_names_the_machine_and_both_session_handles` pins that the
  comment and `dispatch.confirmed` carry identical values. Quickstart steps 2 and 3 remain
  worth walking by hand once, on the real machine, and are still written for that.

**Result**: 1805 passed, 1 skipped (was 1780 passed, 1 skipped) — 25 new tests. Ruff clean.
