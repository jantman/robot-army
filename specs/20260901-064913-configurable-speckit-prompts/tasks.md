---

description: "Task list for configurable Spec Kit lifecycle instructions"
---

# Tasks: What Each Spec Kit Command Is Invoked With Is Configuration, Not Compiled-In Prose

**Input**: Design documents from `specs/20260901-064913-configurable-speckit-prompts/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: **Required, not optional.** The constitution's Development Workflow states "Every new or
changed unit of behavior MUST ship with unit tests", and adds that "code parsing external input MUST
additionally carry tests exercising their failure and interruption paths". Configuration parsing is
exactly that, so the validation failure paths in Phase 5 are a constitutional obligation rather than
a nicety.

**Organization**: Tasks are grouped by user story. Each story is independently completable and
independently verifiable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Exact file paths are in every task

## Path Conventions

Single Python package at the repository root: `src/robot_army/`, `tests/unit/`,
`tests/integration/`. No new module or directory is created by this feature.

---

## Phase 1: Setup

**Purpose**: Establish a clean baseline so any later failure is attributable to this work.

- [ ] T001 Run `uv run pytest` and `uv run ruff check` from the repository root and confirm both pass before any edit, so a later golden-string or lint failure is known to be caused by this milestone rather than inherited

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shapes and constants every story needs. No behaviour change is visible to a user
after this phase — a prompt composed at the end of Phase 2 is byte-identical to one composed before
Phase 1.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T002 In `src/robot_army/speckit.py`, split the `GUIDANCE` constant into `GUIDANCE_BODY` (the first three paragraphs) and `GUIDANCE_CLOSING` (the precedence sentence), and define `GUIDANCE = GUIDANCE_BODY + "\n\n" + GUIDANCE_CLOSING` so its value is unchanged by construction; add a comment recording that the split exists so `guidance()` can insert between them without string-slicing, per `contracts/prompt-block.md` "Absence is byte-identical"
- [ ] T003 In `src/robot_army/speckit.py`, add to the module docstring the rule that this module must never import `robot_army.config`, naming it as the invariant that keeps the `config → speckit` edge acyclic (research R2)
- [ ] T004 [P] In `src/robot_army/config.py`, add `from robot_army.speckit import LIFECYCLE` and `MAX_INSTRUCTION_CHARS = 4000`, with a comment giving the reason for the cap (the composed prompt is one `argv` entry; `prompt.MAX_BODY_CHARS` exists for the same reason)
- [ ] T005 [P] In `src/robot_army/config.py`, add the frozen slots dataclass `CommandInstruction` with fields `command`, `text`, `source`, per `data-model.md` "CommandInstruction"
- [ ] T006 In `src/robot_army/config.py`, add `commands: dict[str, str]` to `SpecKitConfig` (default empty) and `speckit_commands: dict[str, str]` to `RepoConfig` (default empty via `field(default_factory=dict)`, following `env`'s precedent), each with a docstring comment noting that an empty-string value is meaningful only in the repository form
- [ ] T007 In `src/robot_army/config.py`, add `"commands"` to `_KNOWN_KEYS["speckit"]` and `"speckit_commands"` to `_REPO_KEYS`, so a mistyped table name is caught by the existing strict-unknown-key machinery
- [ ] T008 Run `uv run pytest` and confirm the whole suite still passes with no test edited — the shapes exist and nothing reads them yet

**Checkpoint**: Shapes in place, behaviour unchanged, suite green.

---

## Phase 3: User Story 1 - The maintainer decides what `/speckit-implement` is invoked with (Priority: P1) 🎯 MVP

**Goal**: A global instruction configured for one command reaches a dispatched session, verbatim,
inside the guidance block, and the record says which setting supplied it.

**Independent Test**: Set `[speckit.commands] implement` in a configuration file, compose a prompt for
a fixture issue against a detected Spec Kit worktree, and read the result — the text appears attached
to `/speckit-implement`, unaltered. Change the string, compose again, and only that part changes.

### Tests for User Story 1

- [ ] T009 [P] [US1] Create `tests/unit/test_speckit_commands_config.py` with tests for the global happy path: a `[speckit.commands]` table of valid strings parses into `SpecKitConfig.commands`, and `Config.speckit_commands_for("demo")` returns one `CommandInstruction` per configured command with `source` equal to `[speckit.commands] <cmd>`
- [ ] T010 [P] [US1] Create `tests/unit/test_speckit_guidance_render.py` asserting that `speckit.guidance(())` returns `speckit.GUIDANCE` unchanged, and that `speckit.guidance((implement_instruction,))` places the lead-in sentence and the `` `/speckit-implement`: `` block between the constitution paragraph and the closing precedence sentence, with the closing sentence still last
- [ ] T011 [P] [US1] In `tests/unit/test_speckit_guidance_render.py`, assert verbatim carriage: an instruction containing backticks, quotation marks, and two paragraphs separated by a blank line appears in the output exactly as supplied, unwrapped and unindented (FR-009)
- [ ] T012 [P] [US1] In `tests/unit/test_speckit_dispatch_prompt.py`, extend the existing tests to assert that a configured instruction reaches the composed prompt via `dispatch.speckit_block`, and that it does **not** when the repository's block is suppressed by `[speckit] enabled = false` or by the per-repository `speckit = false` (User Story 1 scenario 4, FR-005)
- [ ] T013 [P] [US1] In `tests/integration/test_speckit_dispatch.py`, extend the existing dispatch test to assert the `speckit.detect` audit record carries `instructions` naming the setting that supplied each one, and that the instruction **text** is absent from the record (research R6)

### Implementation for User Story 1

- [ ] T014 [US1] In `src/robot_army/config.py`, parse `[speckit.commands]` in the existing `-- [speckit] --` block: reject a non-table `commands` value as a problem, then for each entry validate the key against `LIFECYCLE` and the value as a non-empty string within `MAX_INSTRUCTION_CHARS`, appending problems in the message shapes given in `contracts/config.md` "Validation"
- [ ] T015 [US1] In `src/robot_army/config.py`, add `Config.speckit_commands_for(repo_key) -> tuple[CommandInstruction, ...]` returning the globally configured instructions in `LIFECYCLE` order with their `source` strings, with a docstring stating — as `speckit_enabled_for`'s does — that the provenance is returned alongside the value because two callers need it and computing it twice is how they come to disagree
- [ ] T016 [US1] In `src/robot_army/speckit.py`, add `guidance(instructions=())` returning `GUIDANCE` when empty, and otherwise `GUIDANCE_BODY`, the lead-in sentence, one `` `/speckit-<command>`: `` block per instruction, and `GUIDANCE_CLOSING` last — exactly the layout in `contracts/prompt-block.md` "The rendered text", with a comment recording why the insertion point is above the closing sentence rather than after it (research R4)
- [ ] T017 [US1] In `src/robot_army/dispatch.py`, have `speckit_block` call `config.speckit_commands_for(repo_key)` alongside its existing `speckit_enabled_for` call, inside the same `try`, and return `speckit.guidance(instructions)` in place of `speckit.GUIDANCE`
- [ ] T018 [US1] In `src/robot_army/dispatch.py`, add the `instructions` field to the `speckit.detect` audit `detail` — a mapping of command name to `source` string, omitted entirely when nothing resolved — per `contracts/config.md` "Audit detail"
- [ ] T019 [US1] Run `uv run pytest tests/unit/test_speckit_commands_config.py tests/unit/test_speckit_guidance_render.py tests/unit/test_speckit_dispatch_prompt.py tests/integration/test_speckit_dispatch.py` and confirm all pass

**Checkpoint**: A single configured instruction reaches a session and is accounted for in the log.
User Story 1 is fully functional and independently testable.

---

## Phase 4: User Story 2 - The same mechanism reaches the other three commands (Priority: P2)

**Goal**: Any subset of the four commands may be configured; configured ones appear in lifecycle
order, unconfigured ones leave no trace, and a configured `specify` instruction reads as an addition
to the issue rather than a replacement for it.

**Independent Test**: Configure instructions for two of the four commands in reversed file order,
compose a prompt, and confirm both appear in lifecycle order and the other two are not mentioned at
all.

### Tests for User Story 2

- [ ] T020 [P] [US2] In `tests/unit/test_speckit_commands_config.py`, assert that a `[speckit.commands]` table written in reverse file order (`implement`, `tasks`, `plan`, `specify`) resolves to a tuple in `LIFECYCLE` order (FR-011)
- [ ] T021 [P] [US2] In `tests/unit/test_speckit_guidance_render.py`, assert that with a subset configured, exactly the configured commands are named and no placeholder, empty heading, or "none" line appears for the others (FR-010)
- [ ] T022 [P] [US2] In `tests/unit/test_speckit_guidance_render.py`, assert that the lead-in sentence appears exactly once regardless of how many instructions are configured, that it contains the "in addition to, not instead of" clause, and that the block's existing sentence about the issue being the input to `/speckit-specify` is still present and unmodified (FR-012)
- [ ] T023 [P] [US2] In `tests/unit/test_speckit_guidance_render.py`, assert that with all four configured the closing precedence sentence is still the last paragraph of the block (FR-015)

### Implementation for User Story 2

- [ ] T024 [US2] In `src/robot_army/config.py`, confirm `speckit_commands_for` iterates `LIFECYCLE` rather than the parsed mapping's insertion order, so the ordering guarantee holds at the single point where it is tested, and add a comment saying so
- [ ] T025 [US2] In `src/robot_army/speckit.py`, confirm `guidance()` emits the lead-in exactly once and omits unconfigured commands entirely; adjust the join so a single instruction and four instructions produce the same spacing between elements
- [ ] T026 [US2] Run `uv run pytest tests/unit/ -k speckit` and confirm all pass

**Checkpoint**: All four commands are configurable, ordered, and independently omissible. User
Stories 1 and 2 both work.

---

## Phase 5: User Story 3 - An unconfigured installation is unchanged, and a broken one says so (Priority: P3)

**Goal**: Configuring nothing produces today's block byte for byte; configuring something malformed
is reported at load with every other problem in the file, never silently dropped.

**Independent Test**: Compose a prompt with no customization and compare against the stored golden
string — byte-identical. Then load configurations with each malformed shape and confirm each is
reported.

### Tests for User Story 3

- [ ] T027 [P] [US3] In `tests/unit/test_speckit_prompt.py`, extend the module docstring to record that this milestone amended 007's FR-009 — the block is fixed per *effective configuration*, not universally — following the precedent that file already set when milestone 012 superseded 007's FR-010, and confirm the existing `GOLDEN` string still passes **unedited** (FR-013)
- [ ] T028 [P] [US3] In `tests/unit/test_speckit_prompt.py`, add a test composing a prompt with a configured instruction and asserting it differs from `GOLDEN` only by the inserted block, so the golden test guards both directions
- [ ] T029 [P] [US3] In `tests/unit/test_speckit_commands_config.py`, add failure-path tests for each shape in `contracts/config.md` "Validation": `commands` not a table; a key that is not a lifecycle command; a non-string value; an empty and a whitespace-only global value; a value exceeding `MAX_INSTRUCTION_CHARS`. Assert the message shape and the offending key are named
- [ ] T030 [P] [US3] In `tests/unit/test_speckit_commands_config.py`, assert that three malformed entries in one file produce three problems in a single `ConfigError` rather than aborting at the first (FR-006, User Story 3 scenario 2)

### Implementation for User Story 3

- [ ] T031 [US3] In `src/robot_army/config.py`, complete the validation added in T014 so every shape in the contract's table produces its documented message, including the length message naming both the limit and the length found
- [ ] T032 [US3] Run `uv run pytest tests/unit/test_speckit_prompt.py tests/unit/test_speckit_commands_config.py` and confirm the golden string passes unedited

**Checkpoint**: The unconfigured path is provably unchanged and every malformed shape is refused out
loud. User Stories 1, 2 and 3 all work.

---

## Phase 6: User Story 4 - One repository needs different instructions (Priority: P4)

**Goal**: A repository may override any command's instruction, may override one to nothing without
suppressing the whole block, and the provenance of each resolved instruction is reported.

**Independent Test**: Configure global instructions plus a repository section overriding one, then
compose prompts for the overridden repository and any other — the first shows the override for that
command and the global text for the rest; the second shows the global text throughout.

### Tests for User Story 4

- [ ] T033 [P] [US4] In `tests/unit/test_speckit_commands_config.py`, add a test covering every row of the resolution matrix in `data-model.md`: absent/absent, global only, override only, override beats global, override-empty clears a global, and override-empty with no global
- [ ] T034 [P] [US4] In `tests/unit/test_speckit_commands_config.py`, assert `source` is `[repos."<key>".speckit_commands] <cmd>` for an overridden command and `[speckit.commands] <cmd>` for an inherited one in the same call, and that a repository with no section inherits everything (FR-023, FR-026)
- [ ] T035 [P] [US4] In `tests/unit/test_speckit_commands_config.py`, add failure-path tests for the repository form: `speckit_commands` not a table, unknown command name, non-string value, over-length value — each naming the repository and the command — and assert that an **empty string is accepted** here rather than reported (R5, FR-025, FR-028)
- [ ] T036 [P] [US4] In `tests/unit/test_speckit_guidance_render.py`, assert that a command overridden to empty produces a block with no mention of that command, while the other commands' global instructions are unaffected
- [ ] T037 [P] [US4] Create or extend a test in `tests/unit/` covering `operations._speckit_column`, asserting the returned detail carries the `instructions` provenance mapping for a detected repository and omits it when nothing resolved

### Implementation for User Story 4

- [ ] T038 [US4] In `src/robot_army/config.py`, parse `speckit_commands` in the `[repos.*]` loop beside the existing `speckit` boolean: reject a non-table value, validate keys against `LIFECYCLE` and values as strings within `MAX_INSTRUCTION_CHARS`, and **accept the empty string**, with a comment giving R5's reason (absent inherits, empty overrides with nothing — two different states, unlike the global form)
- [ ] T039 [US4] In `src/robot_army/config.py`, extend `speckit_commands_for` to apply the repository override per command ahead of the global value, dropping any command whose effective text is empty, and to set `source` accordingly
- [ ] T040 [US4] In `src/robot_army/operations.py`, extend `_speckit_column` to include the `instructions` provenance mapping in the returned detail from the same `speckit_commands_for` call, so `robot-army repos --json` and the audit record cannot disagree (FR-026, FR-027)
- [ ] T041 [US4] Run `uv run pytest` and confirm the full suite passes

**Checkpoint**: All four user stories are functional. The mechanism is global with per-repository
exceptions, and every answer carries its provenance.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: The documentation obligations the spec makes requirements (FR-014, FR-021) and the
final verification.

- [ ] T042 [P] In `specs/007-speckit-extensions/contracts/prompt.md`, add a note under **Rules** recording that FR-009's "identical text on every dispatch, in every repository" is amended by this milestone to "identical per effective configuration", linking to `contracts/prompt-block.md`, and stating that the byte-identity rule still holds for an installation that configures nothing (FR-014)
- [ ] T043 [P] In `specs/007-speckit-extensions/contracts/config.md`, add a note recording that `[speckit]` gained a `commands` sub-table and `[repos.*]` gained `speckit_commands`, linking to this milestone's `contracts/config.md`
- [ ] T044 [P] In `README.md`, extend the "When a repository uses Spec Kit" section with the configuration: the `[speckit.commands]` table, the `[repos.*].speckit_commands` override, the empty-string-clears rule, and the two paragraphs from issue #39 shown explicitly as **examples of use rather than defaults** (FR-021, FR-022)
- [ ] T045 Run `uv run ruff check` and fix any finding introduced by this milestone, respecting the project's line-length of 100
- [ ] T046 Run `uv run pytest` — the whole suite, including `tests/integration/test_speckit_writes_nothing.py` unedited, which is what asserts FR-019 rather than any sentence in the spec
- [ ] T047 Walk `quickstart.md` steps 1 through 4 against a real `config.toml`, confirming in particular that `uv run robot-army doctor` reports three malformed entries together and exits non-zero, and that `robot-army repos --json` shows the resolved provenance

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks every user story**
- **User Story 1 (Phase 3)**: depends on Phase 2
- **User Story 2 (Phase 4)**: depends on Phase 3 — it refines the renderer and resolver US1 creates
- **User Story 3 (Phase 5)**: depends on Phase 2; its validation task T031 completes work begun in T014, so it is cleanest after Phase 3
- **User Story 4 (Phase 6)**: depends on Phase 3 — it extends `speckit_commands_for` and the parser US1 creates
- **Polish (Phase 7)**: depends on every story that is being shipped

### User Story Dependencies

Unlike the template's default, these stories are **not** mutually independent in implementation
order, and pretending otherwise would produce a misleading plan:

- **US1 (P1)** is the mechanism. Everything else extends it.
- **US2 (P2)** refines US1's renderer and resolver rather than adding a parallel one. Its tasks are
  mostly assertions that US1's implementation generalises correctly.
- **US3 (P3)** is independently *valuable* — the byte-identity guard could be written first — but
  T031 finishes validation that T014 starts, so it follows Phase 3 in practice.
- **US4 (P4)** deliberately re-opens `speckit_commands_for` and the parser. That is accepted rather
  than avoided: writing the resolver global-only in T015 and extending it in T039 costs a few lines
  twice and buys a genuinely shippable US1. Building the override up front would make the MVP
  untestable without it.

Each story remains independently **verifiable** at its checkpoint, which is what the checkpoints are
for.

### Within Each User Story

- Tests are listed before implementation in each phase and should be written first, but the
  constitution explicitly does not mandate test-first ordering — "The requirement is that the tests
  exist and are meaningful, not the order they were written in."
- Config shapes before parsing; parsing before resolution; resolution before rendering; rendering
  before dispatch wiring.

### Parallel Opportunities

- **Phase 2**: T004 and T005 touch different regions of `config.py` and are marked [P]; T006 and T007
  edit the same file and follow.
- **Phase 3**: all five test tasks (T009–T013) are [P] — five different files.
- **Phase 4**: all four test tasks (T020–T023) are [P].
- **Phase 5**: all four test tasks (T027–T030) are [P].
- **Phase 6**: all five test tasks (T033–T037) are [P].
- **Phase 7**: T042, T043 and T044 are [P] — three different files.
- Implementation tasks within a phase are **not** parallel: T014, T015, T024, T031, T038 and T039 all
  edit `src/robot_army/config.py`, and T016 and T025 both edit `src/robot_army/speckit.py`.

---

## Parallel Example: User Story 1

```bash
# The five test files for User Story 1 are five different files — write them together:
Task: "Global happy path and resolution in tests/unit/test_speckit_commands_config.py"
Task: "Render position and byte-identity in tests/unit/test_speckit_guidance_render.py"
Task: "Verbatim carriage in tests/unit/test_speckit_guidance_render.py"
Task: "Suppression by the existing gate in tests/unit/test_speckit_dispatch_prompt.py"
Task: "Audit provenance in tests/integration/test_speckit_dispatch.py"

# The implementation that follows is sequential: T014, T015 and T017 all edit config.py
# and dispatch.py in an order that matters.
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 — Setup: confirm the baseline is green
2. Phase 2 — Foundational: shapes and constants, suite still green with no test edited
3. Phase 3 — User Story 1: a global instruction for one command reaches a session
4. **STOP and VALIDATE**: `quickstart.md` step 5 against a real dispatch — this is the only check
   that proves the text reaches a session, and it is worth doing before building three more phases
   on the assumption that it does

At that point issue #39's second item is fully satisfied and its first item is satisfied for any
repository willing to configure `specify`, `plan` and `tasks` globally.

### Incremental Delivery

1. Phase 2 → foundation ready, nothing observable
2. Phase 3 → **MVP**: one command, globally configured, reaching a session
3. Phase 4 → all four commands, ordered, independently omissible — issue #39 fully answered
4. Phase 5 → the unconfigured path provably unchanged, every malformed shape refused
5. Phase 6 → per-repository exceptions with provenance
6. Phase 7 → documentation obligations and the full verification walk

### Single-Maintainer Note

The template's parallel-team strategy does not apply: this project has one maintainer, and the
constitution's Principle II says so. The [P] markers are about which files can be edited without
conflicting, which is useful to one person working in one session, not about staffing.

---

## Notes

- **Nothing in this milestone may write into a worktree, run a subprocess, or make a network
  request** (FR-019). `tests/integration/test_speckit_writes_nothing.py` is what enforces that, and
  T046 requires it to pass unedited.
- The instruction **text** is never written to the audit log — only the name of the setting that
  supplied it. This is the Principle III gap enumerated and justified in
  [plan.md](./plan.md#iii-total-accountability); do not "fix" it during implementation.
- Commits must be atomic with messages explaining *why*, per the constitution's Development Workflow.
- `src/robot_army/speckit.py` must never import `robot_army.config` (T003). That is the invariant
  keeping the new `config → speckit` import acyclic.
- The four sample paragraphs shown in `contracts/config.md` and destined for the README are
  **examples**, never defaults. Nothing ships configured (FR-022).
