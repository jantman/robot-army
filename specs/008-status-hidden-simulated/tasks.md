---

description: "Task list for 008 — status never contradicts itself about hidden simulated work"
---

# Tasks: Status Never Contradicts Itself About Hidden Simulated Work

**Input**: Design documents from `/specs/008-status-hidden-simulated/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/status-output.md](contracts/status-output.md),
[quickstart.md](quickstart.md)

**Tests**: Required, not optional. The constitution's Development Workflow section makes unit
tests mandatory for every new or changed unit of behaviour, and the full suite passing is the
completion gate. Test-first is *not* mandated, so the test task in each phase sits beside its
implementation tasks rather than ahead of them; the ordering within a phase is a reading order,
not a requirement.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths are given in every task

## Path Conventions

Single Python package at the repository root: `src/robot_army/`, `tests/unit/`,
`tests/integration/`. No new source files — this feature repairs two existing functions where
they live.

**A note on parallelism**: this feature is small and concentrated in two files, so genuine `[P]`
opportunities are few. Tasks touching `src/robot_army/operations.py` are sequential with respect
to one another by construction, and pretending otherwise would produce conflicts rather than
speed. The `[P]` markers below are the real ones.

---

## Phase 1: Setup

**Purpose**: Establish the before-picture, so SC-004 ("output unchanged when no simulated rows
exist") can be checked against something rather than asserted.

- [ ] T001 Run `uv run pytest` to confirm a green baseline, then build the scratch environment from [quickstart.md](quickstart.md) ("A throwaway environment" and "Seeding the rows the bug needs") and save two outputs for later comparison: `robot-army status` against the four-simulated-row database (the reported contradiction, reproduced) and `robot-army status` against an empty database (the output SC-004 says must not change)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The one thing both US1 and US2 need before either can state a number.

**⚠️ CRITICAL**: US1 and US2 both report the withheld count; neither can begin until the count
can be obtained.

- [ ] T002 Add `count_simulated_work_items(conn, *, states=None, repo_key=None) -> int` to `src/robot_army/db.py`, placed directly after `list_work_items`, as a single `COUNT(*)` over `work_items WHERE dry_run = 1` that builds its `states` and `repo_key` clauses exactly as `list_work_items` does — the shared construction is what makes the reported number provably equal to the rows `--include-simulated` would reveal ([research.md R1](research.md), [data-model.md](data-model.md) invariant 2). It deliberately takes no `include_simulated` parameter
- [ ] T003 Extend `tests/unit/test_db_scope.py` with cases for `count_simulated_work_items`: it counts simulated rows only and never real ones, it honours `states` and `repo_key` with the same semantics as `list_work_items`, it returns `0` on an empty table, and — the invariant that matters — for any filter combination its result equals `len(list_work_items(include_simulated=True, …)) - len(list_work_items(…))`. Add a comment beside `LISTING_ACCESSORS` recording why the counting accessor is deliberately absent from that list, so a later reader does not "fix" the omission

**Checkpoint**: the withheld count is obtainable and proven equal to what the flag reveals.

---

## Phase 3: User Story 1 — Reading `status` below `live` without being lied to (Priority: P1) 🎯 MVP

**Goal**: A single `robot-army status` invocation never prints two statements that cannot both
be true. Where rows are withheld it says how many and how to see them; where simulated rows are
shown in the queue, they are marked as simulated.

**Independent Test**: Seed only simulated `ready` work items, run `robot-army status` without
`--include-simulated`, and confirm the output contains no claim of absence that its own queue
section contradicts, names the count of withheld rows, and names the flag that reveals them —
[quickstart.md](quickstart.md) scenarios 1–5.

- [ ] T004 [US1] In `status()` in `src/robot_army/operations.py`, compute the two withheld counts beside the existing `counts` and `items` queries: `W_counts` from `count_simulated_work_items(ctx.conn)` with no filters, matching the unfiltered scope of `count_work_items_by_state`, and `W_items` from the same accessor passed the invocation's `states` and `repo` — both fixed at `0` when `include_simulated` is true. The two scopes are different on purpose and must not be collapsed into one number ([research.md R2](research.md))
- [ ] T005 [US1] Render the counts-section disclosure in `status()` in `src/robot_army/operations.py` per [contracts/status-output.md](contracts/status-output.md) §2, all four cases: counts shown with nothing withheld (unchanged), counts shown with rows withheld (one indented line beneath them), no counts and nothing withheld (`no work items yet`, unchanged), and no counts with rows withheld (`no work items (N simulated rows withheld — pass --include-simulated to show them)`, dropping the `yet` that wrongly implies a system which has not started producing work)
- [ ] T006 [US1] Render the item-listing disclosure in `status()` in `src/robot_army/operations.py` per [contracts/status-output.md](contracts/status-output.md) §3, all four cases, keeping the existing `* = simulated (dry-run) row` footnote intact where shown rows are simulated. The disclosure fires whenever `W_items > 0`, not only when the listing is empty — a two-row listing beneath a six-row queue is the same defect, only quieter (FR-003)
- [ ] T007 [US1] Mark simulated rows in the queue table in `status()` in `src/robot_army/operations.py` per [contracts/status-output.md](contracts/status-output.md) §1: suffix the `item` column with `*`, matching `worktree_list`'s convention for the same fact, and print a blank line plus `* = simulated (dry-run) row` beneath the table when any queue row carries it. Leave `ordering.plan`'s `include_simulated=True` alone — the queue must keep naming the item the next dispatch would actually select
- [ ] T008 [US1] Write `tests/unit/test_status_withheld.py` asserting the invariant directly rather than by proxy: no `status` output may both display work item rows and claim there are none. Cover the matrix from [quickstart.md](quickstart.md) — all-simulated, mixed real and simulated, all-real, and wholly empty — each run with and without `--include-simulated`; the filter scoping of `W_items` under `--state` and `--repo`, including a `--repo` that matches no simulated row and must therefore disclose nothing; the empty-database case still reading `no work items yet`; the queue's `*` marking and footnote appearing and, when no queue row is simulated, not appearing; and the exit code staying `0` when rows are withheld
- [ ] T009 [P] [US1] Update the `robot-army status` section of `specs/001-minimum-daemon/contracts/cli.md` to state the invariant that a single invocation may not print two contradictory statements, and link [contracts/status-output.md](contracts/status-output.md) for the exact cases. Amended in place rather than shadowed by a second document, because the milestone that introduced the command still owns its contract

**Checkpoint**: issue #13 is closed. The reported contradiction cannot be reproduced, and
US2 and US3 are optional from here.

---

## Phase 4: User Story 2 — The machine-readable view agrees with the human one (Priority: P2)

**Goal**: A consumer of `status --json` can tell "no work items exist" from "no work items are
being shown", using fields in a single response.

**Independent Test**: With only simulated rows present, request the payload with and without
`--include-simulated` and confirm the withheld counts are `{4, 4}` and `{0, 0}` respectively and
match what the text printed — [quickstart.md](quickstart.md) scenario 6.

- [ ] T010 [US2] Add `withheld_simulated: {"counts": W_counts, "items": W_items}` to the `result.data` dictionary in `status()` in `src/robot_army/operations.py`, per [contracts/status-output.md](contracts/status-output.md) §5. The key is **always present**, with both values `0` when nothing was withheld, so a consumer never has to distinguish "nothing withheld" from "field not reported" — the same absent-versus-zero ambiguity this feature removes from the text. Rename and remove nothing else; `queue[].dry_run` already exists and needs no change
- [ ] T011 [US2] Extend `tests/unit/test_status_withheld.py` with payload cases: the key present in both modes, its two values equal to the numbers the text rendering printed for the same invocation, both `0` under `--include-simulated`, and every key present in the payload before this change still present and carrying its previous meaning

**Checkpoint**: text and payload state the same facts, and `web/pages.py` — which calls
`operations.status` directly — now receives the number issue #14 will need.

---

## Phase 5: User Story 3 — Other listings that hide rows say so too (Priority: P3)

**Goal**: `robot-army cards` and `robot-army worktree list` stop reporting that nothing exists
when rows exist and were withheld.

**Independent Test**: With only simulated cards (respectively simulated items carrying a
worktree path) present, run each command without the include flag and confirm each distinguishes
"nothing exists" from "everything was withheld" — [quickstart.md](quickstart.md), the P3 section.

**Separable**: nothing in US1 or US2 depends on this phase, and dropping it leaves both intact.

- [ ] T012 [P] [US3] Add `count_simulated_cards(conn, *, states=None) -> int` to `src/robot_army/db.py` beside `list_cards`, built the same way as `count_simulated_work_items` and taking the same `states` argument `list_cards` takes. Independent of T014, which needs no accessor
- [ ] T013 [US3] Add the withheld disclosure to `cards()` in `src/robot_army/operations.py` per [contracts/status-output.md](contracts/status-output.md) §7: `no cards visible (N simulated rows withheld — pass --include-simulated to show them)` in place of `no cards tracked yet` when rows were withheld, the standalone line beneath the table when rows are also shown, and the original message verbatim when nothing was withheld. Leave the `[trello]`-not-configured precondition branch untouched
- [ ] T014 [US3] Add the same disclosure to `worktree_list()` in `src/robot_army/operations.py` per §7. Its count comes from the rows it already walks — simulated work items carrying a `worktree_path` — rather than from SQL, because the `worktree_path` predicate lives in Python and duplicating it in a query would put the same rule in two places. Count without computing worktree conditions or sizes for withheld rows
- [ ] T015 [US3] Write `tests/unit/test_listing_withheld.py` covering both commands: everything withheld, some withheld with rows shown, nothing withheld (original messages preserved byte for byte), and `--include-simulated` producing no disclosure. Include a case proving the worktree count ignores simulated items that have no `worktree_path`, since those were never in the listing and were therefore not withheld from it

**Checkpoint**: no listing in the CLI reports absence where there is withholding.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T016 [P] Extend the simulated-rows paragraph in `README.md` (the `--dry-run` / `purge-simulated` section, near "Simulated rows are excluded from every listing unless you ask for them, and are visibly marked when shown") with the new guarantee: every listing that excludes them also says how many it withheld and how to see them
- [ ] T017 [P] Claim the `008` slot in `docs/roadmap.md` for this milestone — status, what it fixes, and the decision that the queue's inclusion of simulated rows was never the thing to change — and move the "Whatever survives contact with reality" parking lot to a `009` heading, following the precedent the 005, 006, and 007 entries set and updating the "Three times now" count accordingly. Add an empty "What running it taught" section
- [ ] T018 Run `uv run ruff check` and `uv run ruff format --check` over the changed files and fix what they report
- [ ] T019 Run the full suite with `uv run pytest` and confirm it passes — the constitution's completion gate
- [ ] T020 Walk [quickstart.md](quickstart.md) scenarios 1 through 7 by hand against a scratch state directory, and note anything the unit fixtures did not predict
- [ ] T021 Verify SC-004 by diffing `robot-army status` against the empty database captured in T001: with no simulated rows present anywhere, the output must be identical to the baseline, with no withheld line, no zero count, and no queue footnote

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: needs Phase 1 only for the baseline capture; T002 could technically start immediately, but T021 has no meaning without T001
- **US1 (Phase 3)**: needs T002. Blocks nothing
- **US2 (Phase 4)**: needs T002. Does not need US1 — see the note below
- **US3 (Phase 5)**: needs nothing from US1 or US2
- **Polish (Phase 6)**: needs whichever stories were taken

### The one subtlety between US1 and US2

Both stories consume the same two computed values, which T004 introduces inside `status()`. If
US2 is delivered before US1 — a legitimate choice, though not the recommended one — T010 must
introduce those two computations itself, and T004 then finds them already present. Neither story
depends on the other's *output*; they share a two-line derivation, and whichever lands first
writes it. This is why the accessor rather than the derivation is the foundational task.

Delivering US2 alone would also leave the text rendering contradictory while the payload is
correct, which is a strange place to stop. US1 first is strongly preferred.

### Within US1

T004 before T005, T006, and T007 — the last three all print numbers T004 computes. T005, T006,
and T007 are three separate edits to `status()` in the same file and must be done in sequence,
though in any order among themselves. T008 can be written at any point and must pass at the end.
T009 touches a different file and is independent of all of them.

### Parallel Opportunities

Genuinely few, and all of them are documentation or a different file:

- **T009** (`specs/001-minimum-daemon/contracts/cli.md`) runs alongside any US1 code task
- **T012** (`src/robot_army/db.py`) runs alongside **T014** (`operations.py`, worktree half)
- **T016** (`README.md`) and **T017** (`docs/roadmap.md`) run alongside each other and alongside anything else

Everything else in `src/robot_army/operations.py` is sequential, and the three US1 rendering
tasks in particular touch the same function.

---

## Parallel Example: User Story 1

```bash
# The only real parallelism in this phase — a contract document and a code edit:
Task: "T009 Update the status section of specs/001-minimum-daemon/contracts/cli.md"
Task: "T005 Render the counts-section disclosure in src/robot_army/operations.py"
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. T001 — capture the baseline and reproduce the bug
2. T002, T003 — the accessor and its proof
3. T004 through T009 — the fix
4. **STOP and VALIDATE**: quickstart scenarios 1–5, then T018–T021
5. Issue #13 closes here. Everything after this is a different, smaller problem

### Incremental Delivery

1. Setup + Foundational → the number exists and is provably right
2. US1 → the terminal stops contradicting itself → **the milestone's reason to exist**
3. US2 → the payload agrees with the terminal, and issue #14 gains the number it will need
4. US3 → the two sibling listings stop reporting absence where there is withholding

### Single maintainer

There is one developer. The "parallel team strategy" the template offers does not apply; the
`[P]` markers above are about which edits can be interleaved without conflicting, not about who
does them.

---

## Notes

- **Nothing here logs, and that is correct.** `status`, `cards`, and `worktree list` are pure reads: they change no state outside the process, so Principle III's obligation is not engaged and no audit record is added. `docs/logging.md` therefore gains nothing, and no Principle III exception is claimed — there is no unlogged *action*, because there is no action. See [plan.md](plan.md), Constitution Check III
- **Nothing here writes, so nothing can be half-written.** No migration, no schema change, no new persistent state, and no change to `docs/state.md`. An interrupted `status` is a truncated line of terminal output and re-running it is the whole recovery procedure
- The default that hides simulated rows (milestone 001 FR-056) and the queue's inclusion of them (FR-055, and milestone 004's R8) are both correct and both unchanged. This feature only reconciles what the command *says* about the gap between them
- The web interface is out of scope. Below `live` it renders as an empty system with a neutral pill, which is worse than a contradiction because there is nothing on screen to notice; that is issue #14, and T010 leaves the number where #14 can reach it
- Commit in atomic pieces with messages explaining why, per the constitution's Development Workflow
