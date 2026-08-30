---

description: "Task list for 012 — standing delivery instructions in the dispatch prompt"
---

# Tasks: Standing Delivery Instructions In The Dispatch Prompt

**Input**: Design documents from `/specs/012-prompt-branch-pr-safety/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/delivery-block.md](contracts/delivery-block.md)

**Tests**: Included, and not optional here. The constitution's Development Workflow section
requires unit tests for every new or changed unit of behaviour, and the full suite must pass
before the feature is complete.

**Organization**: Grouped by user story, with one deliberate departure from the usual shape —
see the note immediately below, which is the thing to read before deciding these tasks are
mis-ordered.

## A note on why the text lands in one task

The template's normal shape gives each story its own slice of implementation. This feature's
entire implementation is a single fixed string, and the stories are three properties of that one
string rather than three parts of it. Splitting its authorship across phases would ship an
intermediate state that contradicts itself: US2's prohibition on changing system state, written
before US1's push-and-pull-request carve-out exists, forbids the very delivery the feature is for
(the FR-005 / FR-006 tension the spec's checklist already records).

So the constant arrives whole, in the foundational phase, byte-for-byte from a contract that was
argued out during planning. What remains genuinely per-story is the *verification*: each story
phase asserts its own requirements against that text and fails loudly if the paragraph it depends
on is missing, weakened, or reworded into something else. Every checkpoint below is still a green
suite and a story that can be demonstrated on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different file, no dependency on an incomplete task
- **[Story]**: US1, US2, US3 per [spec.md](spec.md)

## Path Conventions

Single project: `src/robot_army/`, `tests/unit/` at the repository root.

---

## Phase 1: Setup

**Purpose**: Know the baseline is green before anything is attributed to this change.

- [X] T001 Run `uv sync`, then `uv run pytest` and `uv run ruff check .` from the repository root and confirm both are clean before editing anything

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The constant and its placement. Every user story is a property of what this phase
produces, so nothing else can begin until it is done.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 Add the `DELIVERY` module constant to `src/robot_army/prompt.py`, copying the four paragraphs from `specs/012-prompt-branch-pr-safety/contracts/delivery-block.md` byte-for-byte, written as a `"""\` continuation literal with `—` escapes for em dashes exactly as `speckit.GUIDANCE` in `src/robot_army/speckit.py` does, and a comment naming it as fixed unconditional text whose wording is specified by that contract
- [X] T003 Insert `DELIVERY` as an always-present fourth section in `prompt.compose()` in `src/robot_army/prompt.py`, appended after the optional `speckit_block` section and before the issue section, followed by the same `"---"` rule the other guidance sections use — no new parameter, no caller opt-in, no condition (FR-001, FR-011, research.md D1/D2)
- [X] T004 Update the module docstring of `src/robot_army/prompt.py` to describe the fourth section: that it is unconditional, why it sits below the Spec Kit block rather than above it, and that the issue body overrides it by the block's own last sentence rather than by position
- [X] T005 Re-capture the `GOLDEN` literal in `tests/unit/test_speckit_prompt.py` so it includes the delivery block, and rewrite that module's docstring to record that milestone 012 changed the expected value deliberately and why the test is kept rather than deleted (research.md D5)

**Checkpoint**: `uv run pytest` is green again and a composed prompt contains the block. Every
story below can now be verified independently.

---

## Phase 3: User Story 1 — Work arrives as a pushed branch with a pull request (Priority: P1) 🎯 MVP

**Goal**: Every dispatched session is told to stay on its feature branch and to finish by pushing
to `origin` and opening a pull request — in every repository, with no repository file added.

**Independent Test**: Compose a prompt for a fixture issue with no repository instructions and no
Spec Kit block, and confirm the branch-and-pull-request instruction is present and says both
halves.

### Tests for User Story 1

- [X] T006 [US1] Create `tests/unit/test_delivery_prompt.py` with a fixture issue and a `compose()` helper mirroring `tests/unit/test_speckit_prompt.py`, and tests asserting the block is present when neither `instructions` nor `speckit_block` is passed (FR-001, FR-011), that it tells the session to work on the branch it was started on rather than the repository's default branch (FR-002), and that it names pushing to `origin` and opening a pull request as the conclusion of the work (FR-003)
- [X] T007 [US1] Add a test to `tests/unit/test_delivery_prompt.py` asserting the block contains no direction word pointing at the branch name — the branch is named in the section *below* it — by checking the phrasing is the contract's, not "above" (research.md D3)

**Checkpoint**: US1 is demonstrable on its own — `uv run pytest tests/unit/test_delivery_prompt.py`
passes and quickstart check 2 reads correctly.

---

## Phase 4: User Story 2 — The work product is a diff, not a changed system (Priority: P1)

**Goal**: The same prompt tells the session that the output is repository changes delivered as
commits and pull requests, that satisfying the issue by mutating this or another system is not
the job, and that neither the push, the pull request, nor the ordinary local work of writing the
change is what that prohibits.

**Independent Test**: Read the composed block and confirm the prohibition, its two carve-outs,
and that the carve-outs cannot be missed by a reader who reads only the prohibition sentence.

### Tests for User Story 2

- [X] T008 [US2] Add tests to `tests/unit/test_delivery_prompt.py` asserting the block says the work should be code and file changes in the git repository arriving as commits and pull requests (FR-004), and that it says not to satisfy the issue by changing the state of this machine or any other system (FR-005) — whitespace-normalised, as `test_speckit_prompt.py` does, so an editorial reflow does not read as a change of meaning
- [X] T009 [US2] Add tests to `tests/unit/test_delivery_prompt.py` asserting the push and the pull request are named as exceptions to that prohibition (FR-006), and that running tests, running builds, and installing dependencies inside the worktree are explicitly excluded from it (FR-007)

**Checkpoint**: US1 and US2 both hold against the same constant, and the FR-005 / FR-006
contradiction the spec identified is provably resolved in the shipped text.

---

## Phase 5: User Story 3 — An issue that needs something else can say so (Priority: P2)

**Goal**: The standing instructions state their own override condition, and the prompt's existing
precedence — a repository's `.claude/robot-army.md` first — is unchanged.

**Independent Test**: Compose a prompt with repository instructions and a Spec Kit block present
and confirm the four sections appear in the contracted order; read the block and confirm it says
the issue body wins.

### Tests for User Story 3

- [X] T010 [US3] Add a test to `tests/unit/test_delivery_prompt.py` asserting the block states that an explicit instruction in the issue body overrides it, and that it disclaims enforcement in the same way the Spec Kit block does — nothing here is checked (FR-008)
- [X] T011 [US3] Add a position test to `tests/unit/test_delivery_prompt.py` asserting the composed order is repository instructions → Spec Kit block → delivery block → issue section, by comparing `.index()` of a marker from each, so FR-009's precedence claim rests on an assertion rather than on a comment

**Checkpoint**: all three stories hold. The feature is functionally complete.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T012 Add the determinism and size tests to `tests/unit/test_delivery_prompt.py`: the constant contains no `{` or `}` (FR-010), composing the same fixture issue twice yields identical bytes (FR-010), and `len(prompt.DELIVERY)` is under 1,500 characters (SC-004)
- [X] T013 [P] Add a short section to `README.md` — placed just before "When a repository uses Spec Kit", since that section is its nearest neighbour in subject and in mechanism — saying what every session is now told, that it applies to every repository with nothing to configure, that the issue body overrides it, and that nothing checks compliance
- [X] T014 Run `uv run ruff check .` and `uv run pytest` from the repository root; both must be clean, per the constitution's completion gate
- [X] T015 Run checks 1 through 3 of [quickstart.md](quickstart.md) and confirm the block reads as intended, is under 1,500 characters, and appears in the contracted position with and without the optional sections

Check 4 of the quickstart — a simulated dispatch read back out of `sessions.launch_argv` — is the
FR-014 evidence and needs a running daemon and a labelled issue. Run it when one is convenient;
it verifies that the prompt reaches the durable record without this feature having added any
logging, which is a claim about existing behaviour rather than about anything this milestone
wrote.

---

## Phase 7: Rewording after review

Added after Phases 1–6 were complete and reviewed. The block shipped with its second rule drawn
at side effects rather than at bypassing the repository — see
[research.md D6](research.md) for the reversal and why the first version passed every test
written for it. Nothing about the mechanism changed: same constant, same position, same absence
of a parameter.

- [X] T016 Rewrite the third and fourth paragraphs of `DELIVERY` in `src/robot_army/prompt.py` around the mechanism rule and a single scope sentence, dropping the side-effect prohibition and its exception list, and rewrite the constant's comment to record why the first shape was wrong
- [X] T017 Replace the User Story 2 tests in `tests/unit/test_delivery_prompt.py` — the mechanism rule, the named repository kinds, the stated reason, the scope line, and a regression test asserting neither an unqualified ban nor an exceptions list has reappeared
- [X] T018 Re-capture the `GOLDEN` literal in `tests/unit/test_speckit_prompt.py` for the new text
- [X] T019 Bring the documents to the shipped wording: User Story 2, FR-004 – FR-007 and SC-007 in `spec.md`; D6 in `research.md`; the text, rules table and "what it must not say" in `contracts/delivery-block.md`; the summary in `plan.md`; check 2 in `quickstart.md`; the amendment note in `checklists/requirements.md`; and the section in `README.md`
- [X] T020 Re-run `uv run ruff check .` and `uv run pytest`, and re-run quickstart checks 1 through 3

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001)**: no dependencies
- **Foundational (T002 – T005)**: depends on T001; blocks every story. T003 depends on T002 (the
  constant must exist to be inserted); T005 depends on T003 (the golden cannot be re-captured
  until the output has changed)
- **US1 (T006 – T007)**, **US2 (T008 – T009)**, **US3 (T010 – T011)**: each depends only on the
  foundational phase, not on each other
- **Polish (T012 – T015)**: T012 depends on the foundational phase; T013 and T014 depend on the
  stories being done

### Within Each User Story

Every story's tasks write to the same new file, `tests/unit/test_delivery_prompt.py`, so they run
sequentially within a story. That is why almost nothing here carries `[P]` — marking same-file
tasks parallel would be a lie that costs a merge conflict.

### Parallel Opportunities

- T013 (`README.md`) is the only genuinely parallel task: a different file with no dependency on
  the test file
- The three story phases are independent of one another and could be verified in any order, or by
  three people at once, provided each appends to the test file rather than rewriting it

---

## Implementation Strategy

### MVP

Phase 1 → Phase 2 → Phase 3. That is the smallest thing worth shipping: every dispatched session
told where its work goes, verified. It is also nearly the whole feature — the implementation is
one constant and one appended section, and Phases 4 and 5 add assertions rather than behaviour.

### Incremental Delivery

1. Setup + Foundational → the block is in every prompt, suite green
2. US1 → the delivery rule is proven present and correctly worded
3. US2 → the containment rule and both carve-outs are proven present
4. US3 → the override rule and the precedence ordering are proven
5. Polish → determinism, size, README, full suite

### Notes

- Commit after each phase, with a message that says why — the constitution asks for atomic commits
  whose messages explain the reason, and "the prose" is the substance of this change, so the
  reasoning belongs in the message rather than in a diff of quotation marks
- Do not add a configuration key, a `compose()` parameter, or a call-site condition. All three were
  considered and rejected in [research.md D1](research.md); reintroducing one silently would
  reverse a Constitution Check that was passed on their absence
- Do not add an audit record for prompt composition. The prompt is already persisted in full on the
  session row ([research.md D4](research.md))
