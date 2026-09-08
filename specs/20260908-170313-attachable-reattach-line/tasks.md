---

description: "Task list for issue #51 — a reattach line only where reattaching would reach that session"
---

# Tasks: A Reattach Line Only Where Reattaching Would Reach That Session

**Input**: Design documents from `specs/20260908-170313-attachable-reattach-line/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/show-reattach-line.md](./contracts/show-reattach-line.md),
[quickstart.md](./quickstart.md)

**Tests**: required. The constitution's Development Workflow makes unit tests mandatory for
every new or changed unit of behaviour. Nothing in the suite asserts on this line today
(research.md R6), so there is no existing assertion to lean on and every outcome needs one
written for it.

**Organization**: by user story, in priority order. There is no setup phase — the repository is
set up, and this feature adds no dependency, module, migration, or configuration key.

**Note on size**: this is a small feature and the task list says so rather than padding. The
weight is in the tests, because the rule being fixed is a rule about output that nothing
currently checks.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: which user story the task serves
- Every task names the file it changes

---

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: the helper that decides what an attempt prints, and the call site that stops
deciding it. Every user story is a case of this one function.

**⚠️ CRITICAL**: T001 and T002 block all three stories.

- [X] T001 Add `_reattach_lines(ctx: Context, session: Any) -> list[str]` to `src/robot_army/operations.py`, immediately above `show`, beside `_speckit_lines` and `_pull_request_line`. Implement the four outcomes of [contracts/show-reattach-line.md](./contracts/show-reattach-line.md) in the contract's order: no recorded socket → `[]`; `session.state in states.TERMINAL_SESSION_STATES` → `[]` **before any probe is attempted**; otherwise build a `HostHandle(socket_path=..., argv=(), pid=session.pid)` as `attach` does and ask `ctx.boundaries.session_host.is_alive`; live → the command line, not live → the refusal line, `BoundaryError` → the command line plus the `unverified:` line. Return `list[str]` rather than `str | None` so the caller keeps none of the decision.
- [X] T002 Replace the `if session.host_socket:` block inside `show` in `src/robot_army/operations.py` with a loop over `_reattach_lines(ctx, session)`, in the shape of the `for line in _speckit_lines(item)` loop already above it. No other line of `show` changes.
- [X] T003 Write `_reattach_lines`'s docstring to carry the reasoning that is invisible from the code (this codebase's convention, and the two facts a later reader will otherwise undo): that a socket is named after the **work item**, so a live socket under a finished attempt belongs to a *newer* session and the state check is an attribution guard rather than an optimisation (research.md R2); and that the `BoundaryError` is caught because `show` is read-only and must not die because a socket hung (research.md R5).

**Checkpoint**: `show` renders through the helper. Behaviour is now the contract's; the tests
that prove it follow.

---

## Phase 2: User Story 1 — A session that is over offers nowhere to go (Priority: P1) 🎯 MVP

**Goal**: no attach instruction under an attempt that has ended, whatever is listening on the
path it recorded.

**Independent Test**: an item whose only attempt is `exited_clean`, `exited_error` or `lost`,
with a socket the stub host reports as **live** — no `dtach` in the output, in all three states.

### Tests for User Story 1

- [X] T004 [P] [US1] Create `tests/unit/test_show_reattach_line.py` with a `ctx` fixture in the shape `tests/unit/test_cli_local_time.py` uses — `monkeypatch` on `operations.wire` returning `make_boundaries(log, level=level)` — plus a helper that renders `operations.show(...)` to text and returns the lines mentioning attaching. The module docstring states what the file is for: this line is the only place `show` tells the maintainer to go and do something, and nothing checked it until this feature.
- [X] T005 [P] [US1] In that module, assert the headline: parametrised over `exited_clean`, `exited_error` and `lost`, an attempt seeded with `seed_session` and a recorded socket **that the stub host reports as live** renders no line containing `dtach`. The live socket is the assertion's point and must not be dropped as redundant — it is what distinguishes this guard from a liveness check (research.md R2).
- [X] T006 [P] [US1] In the same module, assert the two-attempt case from quickstart.md scenario 2: one item, attempt #1 `lost` and attempt #2 `running`, both recording the same path, that path live — exactly one `dtach` command in the output, and it is under attempt #2. Assert on the ordering of the rendered lines, not merely on the count, so a regression that prints the command under the wrong row fails.
- [X] T007 [P] [US1] In the same module, assert FR-005 across all five session states: an attempt with `host_socket` NULL renders no attach line of any kind and no path.

**Checkpoint**: the reported defect is fixed and cannot regress, including the half the issue
did not report.

---

## Phase 3: User Story 2 — A session the record calls live, with nothing at the other end, says so (Priority: P2)

**Goal**: an open attempt whose socket answers still offers the command; one whose socket
answers nothing says that, instead of offering a command that fails at the shell.

**Independent Test**: a `running` attempt with a live socket renders today's command; the same
attempt with a dead socket renders the refusal and no command.

### Tests for User Story 2

- [X] T008 [P] [US2] In `tests/unit/test_show_reattach_line.py`, assert the unchanged case (FR-002): a `running` attempt whose path the stub host reports as live renders `       reattach: dtach -a <path>` byte-for-byte, leading spaces included. This is the regression guard for everything else in this feature.
- [X] T009 [P] [US2] In the same module, assert FR-003 for both open states: a `starting` and a `running` attempt whose path the stub host does not report as live each render `       reattach: not available — nothing is listening on <path>`, and neither renders a `dtach` command.
- [X] T010 [P] [US2] In the same module, assert the rehearsal case from spec.md's edge cases: a `dry_run` session row is judged by the same two questions as any other and nothing inspects the row to choose a host — a terminal rehearsal row renders nothing, an open one renders the refusal.

**Checkpoint**: both directions of the wrong claim are gone — the dead row that offered a
command, and the open row that offered one to nothing.

---

## Phase 4: User Story 3 — A check that could not answer is not read as an answer (Priority: P3)

**Goal**: a probe that raises produces an honest line and does not take the command down with
it.

**Independent Test**: substitute a session host whose `is_alive` raises `BoundaryError`; the
command is still printed, the caveat names the boundary's message, and `show` exits `0`.

### Tests for User Story 3

- [X] T011 [P] [US3] In `tests/unit/test_show_reattach_line.py`, add a host whose `is_alive` raises `BoundaryError("dtach probe on <path> timed out")` — wired through `make_boundaries(host=...)` — and assert FR-004: a `running` attempt renders the `dtach` command **and** a following `unverified:` line carrying the boundary's own message unedited.
- [X] T012 [P] [US3] In the same module, assert FR-009 with the same raising host: `operations.show(...).code` is `EXIT_OK`, the item's other sections (`state history`, `sessions`, `resume-decision signals`) all render, and a second attempt on the same item is unaffected. A raising probe must degrade one line, not the command.

**Checkpoint**: every path through the helper is covered, including the one that only appears
when the machine is already misbehaving.

---

## Phase 5: Documentation & Verification

- [X] T013 [P] Add a paragraph to `docs/guide/4-session.md` under "Attaching to a running session" stating the rule the output now follows: the line appears only under a session that is still open and whose socket answers, so its absence is a statement and not an omission; and why the state is checked as well as the socket — the path is named after the item, so a live socket under a finished attempt is the *next* session's. Keep it to the page's register and do not restate the contract table. `docs/guide/operating.md` is deliberately left alone (research.md R8).
- [X] T014 Assert the payload did not move (FR-008): confirm `show(...).data["sessions"][0]` still carries exactly the thirteen keys listed in research.md R7, in `tests/unit/test_show_reattach_line.py`. This is the guard on the decision *not* to annotate the JSON — a later change that adds a key there changes the web's item view, and this test is where that gets noticed.
- [X] T015 Run `uv run pytest` and confirm the whole suite passes, then walk quickstart.md's scenarios 1–7 against the new module's output. The suite passing is the constitution's completion condition, not a formality.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Foundational)**: T001 → T002 (the call site needs the helper); T003 documents T001
  and may land with it. Blocks every other phase.
- **Phases 2, 3 and 4**: all depend on Phase 1 and on nothing else. They touch one test file, so
  the `[P]` markers mean "independent in content"; landing them in one file means one commit per
  story rather than three at once.
- **Phase 5**: T013 depends on nothing and could land first; T014 depends on Phase 1; T015 is
  last by definition.

### Within Each Story

Tests only — the behaviour is one function, delivered in Phase 1. This is deliberate and is
why the phases are not "implement then test": splitting a fifteen-line function across three
stories would be a fiction, and the stories are genuinely independent as *assertions*, which is
what the checkpoints are for.

### Parallel Opportunities

- T005, T006, T007 are independent assertions and can be written in any order.
- T008, T009, T010 likewise, as are T011 and T012.
- T013 (documentation) is independent of every code task.

---

## Implementation Strategy

### MVP

Phase 1 plus Phase 2 is the reported bug fixed and proven, including the shared-socket case the
issue did not report. It is a coherent stopping point.

### Increments

1. Phase 1 → the helper and the call site. One commit.
2. Phase 2 → the P1 assertions. One commit. **The issue is now fixed.**
3. Phase 3 → the open-row cases, both directions.
4. Phase 4 → the unverifiable probe.
5. Phase 5 → the guide paragraph, the payload guard, the full suite.

### Notes

- Commit per phase, message explaining why rather than what, per the repository's convention.
- The exact strings live in [contracts/show-reattach-line.md](./contracts/show-reattach-line.md).
  Tests quote them; the implementation and the guide must not drift from them independently.
