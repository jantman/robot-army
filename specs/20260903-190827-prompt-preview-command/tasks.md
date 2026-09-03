---

description: "Task list for the prompt preview command"
---

# Tasks: Prompt Preview Command

**Input**: Design documents from `specs/20260903-190827-prompt-preview-command/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: Included, and not optional here. The constitution's Development Workflow section
requires unit tests for every new or changed unit of behaviour, and the full suite must pass
before the feature is complete. Test-first is *not* required — only that the tests exist and
are meaningful.

**Organization**: Tasks are grouped by user story so each can be implemented and validated on
its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel — a different file, no dependency on an incomplete task
- **[Story]**: Which user story the task serves (US1, US2, US3)

Parallelism is genuinely limited in this feature: almost every change lands in one of three
files (`src/robot_army/operations.py`, `src/robot_army/cli.py`,
`tests/unit/test_prompt_preview.py`), so tasks touching them are sequential by necessity
rather than by preference. `[P]` is marked only where it is true.

## Path Conventions

Single Python package: `src/robot_army/`, `tests/unit/`, `tests/integration/`, all at the
repository root. Run everything from `/home/jantman/GIT/robot-army`.

---

## Phase 1: Setup

**Purpose**: Establish that the starting point is clean, so any later failure is this
feature's.

- [ ] T001 Confirm a green baseline: run `uv run pytest` and `uv run ruff check` at the
      repository root and record that both pass before any file is edited

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The one production change and the one test-fixture change that every user story
below depends on.

**⚠️ CRITICAL**: No user story work can begin until T002 and T003 are complete.

- [ ] T002 Widen `speckit_block`'s `item_id: int` to `int | None` in
      `src/robot_army/dispatch.py`, and when it is `None` key the `speckit.detect` record
      `entity_type="repo"`, `entity_id=repo_key` instead of `entity_type="work_item"`. Leave
      `build_launch_plan`'s call site untouched so dispatch's record shape is unchanged. Per
      [R3](research.md) and [contracts/audit-records.md](contracts/audit-records.md)
- [ ] T003 [P] Add a `raise_on_get_issue: Exception | None = None` attribute to
      `FakeIssueReader` in `tests/conftest.py` and honour it at the top of `get_issue`, so a
      test can exercise the transport-failure path. Mirror the existing `raise_on_remote`
      attribute's shape
- [ ] T004 Add two cases to `tests/unit/test_speckit_dispatch_prompt.py`: one asserting that
      `speckit_block(item_id=None, ...)` writes a record keyed on the repository, and one
      asserting a dispatch's record is still keyed on the work item (depends on T002)

**Checkpoint**: `speckit_block` is callable without a work item, and the fake reader can fail.

---

## Phase 3: User Story 1 - See what a session will be told, before it runs (Priority: P1) 🎯 MVP

**Goal**: `robot-army prompt <owner/repo> <number>` prints the full composed prompt for any
issue in an onboarded repository, whether or not that issue has ever been dispatched.

**Independent Test**: Run the command against an onboarded repository and an issue that has
never been dispatched. The delivery block, the repository's own instructions, the Spec Kit
guidance where it applies, and the issue's title, URL, labels and body all appear; no
worktree, branch, work item or session is created.

### Implementation for User Story 1

- [ ] T005 [US1] Implement `prompt_preview(ctx, repo_key, issue_number, *, notes=None)` in
      `src/robot_army/operations.py`, following [data-model.md](data-model.md): reject a slug
      that is not `owner/name` and a non-positive issue number with `EXIT_USAGE`; resolve the
      repository with `repos_mod.resolve` and return `EXIT_PRECONDITION` when it is `None`;
      fetch the issue with `ctx.boundaries.issue_reader.get_issue` and return `EXIT_FAILED`
      on `None` or `TransportError`; use the onboarded clone as the context root; derive the
      branch with `prompt.branch_name(ctx.config.worker.branch_prefix, ...)`; call
      `dispatch.speckit_block(..., item_id=None, ...)`; call `prompt.compose`; and return a
      `Result` whose single line is the composed prompt. Add `prompt` to the module's bulk
      `from robot_army import ...` import
- [ ] T006 [US1] Write one `prompt.preview` audit record on **every** exit path of
      `prompt_preview` in `src/robot_army/operations.py` — success and all three refusals —
      with the fields and keying in
      [contracts/audit-records.md](contracts/audit-records.md). Never record the prompt text,
      the issue body, or the contents of either optional section ([R4](research.md))
- [ ] T007 [US1] Build the context note in `prompt_preview` naming the directory the
      contextual sections were read from and which of the three cases applies, put it in
      `Result.data["notes"]`, and write it to the `notes` stream when one was passed
      (`src/robot_army/operations.py`, wording in [contracts/cli.md](contracts/cli.md))
- [ ] T008 [US1] Add the `prompt` subparser to `build_parser` in `src/robot_army/cli.py` with
      positionals `repo_key` and `issue_number` (`type=int`), and a `"prompt"` entry in
      `_dispatch`'s table passing `notes=sys.stderr`. Do **not** add it to `READ_COMMANDS` or
      to the `--json` list in the universal-flags loop ([R7](research.md))

### Tests for User Story 1

- [ ] T009 [US1] Create `tests/unit/test_prompt_preview.py` covering the happy path for an
      untracked issue: the delivery block is present unconditionally; the title, URL, labels
      and body appear; the branch is the derived `robot-army/issue-<n>-<slug>`; a clone
      carrying `.claude/robot-army.md` puts those instructions first; the Spec Kit block
      appears when the clone is a Spec Kit project and is absent when the repository is
      suppressed; an empty body yields the placeholder; an over-long body is truncated with
      the URL pointer
- [ ] T010 [US1] Add the audit assertions to `tests/unit/test_prompt_preview.py`: one
      `prompt.preview` record per invocation on each of the four paths, with the keying and
      `detail` fields [contracts/audit-records.md](contracts/audit-records.md) specifies, and
      no prompt or body text anywhere in the record (depends on T006, T009)
- [ ] T011 [P] [US1] Create
      `tests/integration/test_prompt_preview_matches_dispatch.py` asserting that
      `prompt_preview`'s text is exactly the prompt argument `dispatch.build_launch_plan`
      places in the worker argv for the same issue, repository and directory — the proof of
      FR-002 and SC-002
- [ ] T012 [P] [US1] Extend `tests/unit/test_cli_exit_codes.py` with `prompt`'s four codes
      through `cli.main()`: `0` on success, `2` for a malformed slug and for a non-positive
      issue number, `3` for a repository that is not onboarded, and `1` for both an issue
      that returns `None` and one whose read raises (uses T003's hook)

**Checkpoint**: the MVP. Any issue in an onboarded repository can be previewed, and nothing
is created by doing so.

---

## Phase 4: User Story 2 - Reproduce what an already-dispatched session was told (Priority: P2)

**Goal**: For an issue that already has a work item, the preview uses that item's recorded
branch and reads its contextual sections from that item's worktree.

**Independent Test**: Run the command for an issue whose item has a worktree on disk. The
branch in the prompt equals the branch `robot-army show` reports, and the stderr note names
the worktree rather than the clone.

### Implementation for User Story 2

- [ ] T013 [US2] Extend `prompt_preview` in `src/robot_army/operations.py` to look the row up
      with `db.find_work_item(source="github", source_id=f"{repo_key}#{issue_number}",
      dry_run=False)` and, when it exists: prefer `item.branch` over the derived name, prefer
      `Path(item.worktree_path)` as the context root when that directory exists, and pass
      `item.id` to `speckit_block`. A dry-run row is deliberately not consulted
      ([R8](research.md))
- [ ] T014 [US2] Extend the `prompt.preview` record's `detail` in
      `src/robot_army/operations.py` with `item_id`, `branch_source` (`recorded`/`derived`)
      and `context_source` (`worktree`/`clone`/`none`), and extend T007's note so it names
      which of the three the run used (depends on T013)

### Tests for User Story 2

- [ ] T015 [US2] Add cases to `tests/unit/test_prompt_preview.py`: a recorded branch beats the
      derived one; an existing worktree beats the clone; a recorded `worktree_path` whose
      directory is gone falls back to the clone and says so in the note and the record; a
      `dry_run` row is ignored and the derived branch is used
- [ ] T016 [P] [US2] Add the worktree case to
      `tests/integration/test_prompt_preview_matches_dispatch.py`: the same string equality
      when the context root is a real worktree carrying its own `.claude/robot-army.md`

**Checkpoint**: a dispatched item's preview answers the question the maintainer actually
asked — what *that session* was told.

---

## Phase 5: User Story 3 - Pipe, save, and diff the output (Priority: P3)

**Goal**: stdout carries the prompt and nothing else, on every path — so the output can be
redirected, paged and diffed without post-processing.

**Independent Test**: Redirect the command's output to a file. The file holds the prompt
alone; the context note is on the terminal, not in the file; a second run produces a
byte-identical file; a failing run produces an empty one.

**Note**: much of this phase is verification rather than new code, and deliberately so — the
discipline is cheap to establish here and impossible to retrofit once anything consumes the
output.

### Implementation for User Story 3

- [ ] T017 [US3] Verify and, where needed, correct the stream discipline across
      `src/robot_army/operations.py` and `src/robot_army/cli.py`: notes reach only the `notes`
      stream and `Result.data`, never `Result.lines`; every refusal puts its message in
      `Result.lines` with a non-zero code so `main`'s existing routing sends it to stderr; on
      success `Result.lines` holds the prompt and nothing else ([R6](research.md))

### Tests for User Story 3

- [ ] T018 [US3] Add `capsys`-driven cases to `tests/unit/test_prompt_preview.py` running
      through `cli.main()`: stdout equals the prompt plus one trailing newline and nothing
      more; the context note appears on stderr and never on stdout; stdout is empty for each
      of the malformed-argument, not-onboarded and issue-unavailable failures; two successive
      successful runs emit byte-identical stdout

**Checkpoint**: all three stories work, independently and together.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T019 [P] Document the command in `README.md` under "What every session is told" — the
      section that describes what goes into a prompt is where a reader will look for how to
      read one. A short paragraph and the invocation; mention that it creates nothing
- [ ] T020 [P] Add a `prompt.preview` entry to `docs/logging.md` beside the other per-action
      tables, naming the record's fields and the two justified omissions from
      [R4](research.md), so the log's format stays documented as the Operating Constraints
      require
- [ ] T021 Work through `specs/20260903-190827-prompt-preview-command/quickstart.md` against
      a real onboarded repository and a real issue, including the state-unchanged check in
      step 4 and the exit-code checks in step 6
- [ ] T022 Run `uv run pytest` and `uv run ruff check`; both must pass. The feature is not
      complete until they do (constitution, Development Workflow)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: after Setup. **Blocks every user story** — T005 cannot call
  `speckit_block` without a work item until T002 lands, and no failure test can run until T003
  does
- **US1 (Phase 3)**: after Phase 2. Depends on nothing else
- **US2 (Phase 4)**: after Phase 3, because T013 extends the function T005 creates
- **US3 (Phase 5)**: after Phase 3. Independent of US2 — its guarantees hold whether or not
  the row lookup exists
- **Polish (Phase 6)**: after every story that is being shipped

### User Story Dependencies

US2 and US3 both build on US1's function rather than on each other, so after Phase 3 they can
proceed in either order or together. US1 stands alone and is the MVP.

### Within Each Story

T005 → T006 → T007 → T008 is a chain in two files and must run in order. T013 → T014
likewise. Tests follow the behaviour they assert, except T012 and T011, which live in their
own files.

### Parallel Opportunities

- T003 alongside T002 (`tests/conftest.py` vs `src/robot_army/dispatch.py`)
- T011 and T012 alongside T009/T010 — three different files
- T016 alongside T015
- T019 and T020 alongside each other (`README.md` vs `docs/logging.md`)

Everything else is serialised by file, not by choice.

---

## Parallel Example: User Story 1

```bash
# After T005-T008 are in place, these three touch three different files:
Task: "T010 audit assertions in tests/unit/test_prompt_preview.py"
Task: "T011 fidelity proof in tests/integration/test_prompt_preview_matches_dispatch.py"
Task: "T012 exit codes in tests/unit/test_cli_exit_codes.py"
```

---

## Implementation Strategy

### MVP first

1. Phase 1, then Phase 2 — both small, and Phase 2 blocks everything
2. Phase 3 (US1)
3. **Stop and validate**: quickstart steps 1, 4 and 7 against a real repository. At this point
   the feature is genuinely useful: any issue can be previewed and nothing is created by
   previewing it
4. Commit

### Incremental delivery

- US1 → an untracked issue previews correctly (the common case, and the P1 story)
- US2 → a dispatched item previews from its own worktree, answering "what was *that* session
  told"
- US3 → the output is safe to redirect, diff and script against
- Polish → README, the log's documentation, and the full quickstart

Each phase leaves the tree green and the command usable; none of them requires the next.

---

## Notes

- Commit after each phase, with a message explaining why the change was made (constitution,
  Development Workflow)
- The one thing that must not drift: the preview and the dispatch call the same
  `prompt.compose`. If a task tempts you to reimplement any part of composition, the answer is
  in [R3](research.md) — widen the shared code instead
- No migration, no new dependency, no configuration key. If a task seems to need one, stop:
  something has been misread
