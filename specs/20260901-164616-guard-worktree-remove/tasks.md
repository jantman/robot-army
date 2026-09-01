---

description: "Task list for: Refuse to Remove a Worktree While Its Session Is Open"
---

# Tasks: Refuse to Remove a Worktree While Its Session Is Open

**Input**: Design documents from `specs/20260901-164616-guard-worktree-remove/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/worktree-removal.md](./contracts/worktree-removal.md),
[quickstart.md](./quickstart.md)

**Tests**: **Required, not optional.** The constitution's Development Workflow makes unit tests
mandatory for every new or changed unit of behaviour, and this feature's central success criterion
(SC-001) *is* a test assertion: zero worktrees removed. Test tasks are written before the
implementation they cover in each phase — not because the constitution requires that order (it
explicitly does not), but because the assertions are the specification here.

## The assertion that matters

Everywhere a task says "nothing was removed", assert it as **no `git.remove_worktree` and no
`git.delete_branch` record exists in the audit log**, using `SimulatedVersionControl`
(`src/robot_army/boundaries/git.py:276`), which records every intended git operation and touches no
disk. A surviving directory is the weaker claim: it is also what you get if removal was attempted
and merely failed. See research R12.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US3)

## Path Conventions

Single project: `src/robot_army/`, `tests/` at repository root.

---

## Phase 1: Setup

**Purpose**: Establish the baseline so any regression is attributable to this feature rather than
to pre-existing state.

- [ ] T001 Capture the baseline: run `uv run pytest` and `uv run ruff check src tests` from the repository root and record that both are green before any edit. If either is red, stop and report — this feature must not be built on top of an unexplained failure.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared live-session definition that FR-014 requires, and the action record that
every refusal below must be written into. Without the second, a refusal would leave no trace at all
(research R4), which Principle III forbids.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T002 [P] In `src/robot_army/cleanup.py`, add `LIVE_SESSION_STATES: frozenset[SessionState] = frozenset({SessionState.STARTING, SessionState.RUNNING})` and `live_sessions(conn, item_id) -> list[Session]` returning every row from `db.list_sessions_for_item` whose state is in that set, in the attempt order the query already yields. Rewrite the `live = [...]` comprehension in `eligible` (currently `cleanup.py:80-84`) to call it. **`eligible`'s returned string must stay byte-for-byte `f"session {live[0].session_id} is still live"`** — `clean_item` at `cleanup.py:106` routes on the substring `"still live"`, and changing the wording silently converts every live-session skip into an un-reconsidered non-decision (research R3, contract W5). Document the constant as an allow-list of open states, not a deny-list of closed ones, with the reason `reconcile.SESSION_BEARING_STATES` gives for its own set.
- [ ] T003 [P] In `tests/unit/test_cleanup.py`, add: (a) a regression test that an item with a live session is still recorded `skipped` — not `retained`, not ineligible — and that the reason still contains `"still live"`, pinning the coupling T002 must not disturb; (b) direct tests of `cleanup.live_sessions` covering every row being considered (two attempts, the earlier one open and the later closed), attempt ordering, and each closed state (`exited`, `lost`, `failed`) returning empty.
- [ ] T004 Wrap the body of `operations.worktree_remove` (`src/robot_army/operations.py:1449`) in `with ctx.audit.action("worktree.remove", entity_type="work_item", entity_id=item_id, target=item.worktree_path, detail={"force": force}) as outcome:`, and set `outcome["worktree_removed"]`, `outcome["branch_deleted"]` and `outcome["refused"]` on every path out. **No behaviour change in this task** — same exits, same messages, same payload. The wrapper must open *after* the "no such item" and "no worktree on record" returns, since those have no `worktree_path` to name as a target. Per contract W17–W19 and research R5, a refusal is `outcome="ok"` with `refused: true`; `error` stays for a boundary that broke, which `audit.action`'s own exception branch already writes.
- [ ] T005 In `tests/unit/test_worktree_remove_guard.py` (new file), add the Phase 2 tests: a successful removal and a git-refused removal each produce exactly one `worktree.remove` intent and one `worktree.remove` outcome carrying the same `action_id`, with `entity_id` equal to the work item id and `target` equal to the worktree path. This is the record that does not exist today.

**Checkpoint**: one definition of "live" with two callers, and a command that can be reconstructed
from the log. No user-visible behaviour has changed yet.

---

## Phase 3: User Story 1 — Removal refuses while a session for the item is open (Priority: P1) 🎯 MVP

**Goal**: `robot-army worktree remove <id>` refuses, removes nothing, and tells the operator how to
go and look at the worker — regardless of what state the work item is in.

**Independent Test**: Record an open session for a work item, run the removal with
`SimulatedVersionControl`, and assert the audit log contains no `git.remove_worktree` and no
`git.delete_branch` record, the exit code is `EXIT_PRECONDITION` (3), and `work_items.worktree_path`
and `branch` are unchanged.

- [ ] T006 [US1] In `tests/unit/test_worktree_remove_guard.py`, write the reported case first: a work item in a **terminal** state (`done`) whose session row is `running`. Assert exit `3`, no `git.remove_worktree` record, no `git.delete_branch` record, and `worktree_path`/`branch`/`state`/`cleanup_state` unchanged. Name the test after the report (`test_the_reported_case_...`) and cite issue #79 in its docstring: the item being terminal is what made this reachable through ordinary operation, and it is precisely what the report's own suggested fix would have missed (research R1).
- [ ] T007 [US1] Extend `tests/unit/test_worktree_remove_guard.py` with the remaining guard cases from contract W1–W3 and quickstart Scenario 2: session `starting` with a terminal item; session `running` with an `active` item; **two attempts where only the earlier is open** (the case `db.latest_session_for_item` would miss); and the negative controls — `exited`, `lost` and `failed` rows must not refuse, and removal proceeds exactly as today. Use `tests/conftest.py:1319` `seed_session`, called twice for the multi-attempt case so `db.next_attempt` supplies real attempt numbers.
- [ ] T008 [US1] In `src/robot_army/operations.py`, add a module-private frozen dataclass `_LiveSession` (fields per data-model.md: `session_id`, `attempt`, `state`, `pid`, `liveness`, `socket`) and a helper that builds it from a `Session`. `liveness` is one of the four words in contract W9 — `running`, `gone`, `unidentified`, `unrecorded` — chosen from the record alone. **`procinfo.is_alive` must not be called when `proc_start` is `None`** (contract W10): its documented degradation at `procinfo.py:120-121` returns `True` for any process holding that number, and session rows legitimately carry a pid with no start time (research R6). A four-valued word rather than a boolean, because three of the four answers are not "alive" and folding "cannot tell" into either is the mistake this feature exists to prevent.
- [ ] T009 [US1] In `operations.worktree_remove`, evaluate `cleanup_mod.live_sessions(ctx.conn, item_id)` immediately after the repository resolves and **before** the `vcs` lookup and the `force` prompt. When it is non-empty and `force` is false: return `EXIT_PRECONDITION` with the message of contract W8 — the worktree path; the session id, attempt and state; the liveness sentence; what removing it now would do; the `dtach -a <socket>` line **only when `host_socket` is recorded** (contract W11, matching `operations.py:717` character for character); and the two ways forward (`robot-army cancel <id>`, or `--force`). Render only the first open session, with a trailing count when there is more than one. Set `result.data["refused_by"] = "live_session"`, `result.data["refused_reason"]` to the same sentence, `result.data["live_session"]` to the dataclass as a dict, and the matching `refused`/`refused_by`/`reason`/`live_session` keys on the audit outcome. Nothing is removed, no branch is deleted, and no row is written (contract W12).
- [ ] T010 [US1] Extend `tests/unit/test_worktree_remove_guard.py` with the message and payload assertions of quickstart Scenario 3: each of the four liveness answers renders its own sentence; a row with a pid and no `proc_start` renders `unidentified` and **never** `running`; a row with no `host_socket` prints no reattach line and invents nothing in its place; `data["refused_by"] == "live_session"` while `data["worktree_removed"]` is `False`; and the exit code is `EXIT_PRECONDITION` (3), distinct from the `EXIT_FAILED` (1) that git's dirty-tree refusal returns from the same function (contract W7).
- [ ] T011 [US1] In `tests/integration/test_worktree_removal.py` (already `requires_git`), add a test using the existing `prepared_item` helper: seed a `running` session for the item, run `operations.worktree_remove`, and assert the real directory still exists, the real branch is still listed by `git branch`, and `db.get_work_item(...).worktree_path` is unchanged. This is the end-to-end proof that the guard sits in front of git and not merely in front of a stub.

**Checkpoint**: the reported defect is fixed and cannot be reached without an explicit override.
US1 alone is a shippable increment: it refuses, it explains, and it is on the record.

---

## Phase 4: User Story 2 — The override is available, and it is honest (Priority: P2)

**Goal**: `--force` still clears a worktree whose session row nothing will ever close, and the
prompt says a live worker is in there before the operator types anything.

**Independent Test**: Force the removal of an item with an open session, capture the prompt string
handed to the `confirm` callable, and assert it names the session before any input is accepted;
assert a non-matching answer aborts with nothing removed and a matching answer proceeds.

- [ ] T012 [US2] In `tests/unit/test_worktree_remove_guard.py`, assert that without `--force` the `confirm` callable is **never invoked** — pass a callable that fails the test if called. FR-004 and contract W6: the refusal is not a question, and the prompt must not become a way to talk past the guard.
- [ ] T013 [US2] In `operations.worktree_remove`, extend the `force` branch (currently `operations.py:1471-1477`): when a live session was found, prepend a sentence naming it — the session id, its state and the liveness answer — and say that forcing leaves that worker running in a deleted directory, then keep the existing "type the item id" demand unchanged. When no live session was found, the prompt is **byte-for-byte today's prompt** (contract W15). No second prompt is added, and the flag alone never suffices (contract W14). After a satisfied confirmation, the rest of the function runs exactly as it does today (contract W16).
- [ ] T014 [US2] Extend `tests/unit/test_worktree_remove_guard.py` with quickstart Scenario 4: with a live session, the captured prompt contains the session id; answering anything other than the item id returns `EXIT_FAILED` with no `git.remove_worktree` record; answering the item id removes the worktree and deletes the branch; and with **no** live session the captured prompt equals the pre-feature string exactly, so that the common case did not silently change wording.

**Checkpoint**: no worktree can be made permanently unremovable by this guard, and no operator can
override it without having been told what they are overriding.

---

## Phase 5: User Story 3 — The refusal and the override are both on the record (Priority: P3)

**Goal**: A reader of the action log alone can tell a live-session refusal from a git refusal, and
a forced override of a live worker from a forced override of a dirty tree.

**Independent Test**: Trigger a refusal and a forced override, then read only the audit records and
state which item and which session each concerned and which of the two outcomes occurred.

**Depends on**: US2 — `forced_over_live_session` cannot be produced until the override exists.

- [ ] T015 [US3] In `operations.worktree_remove`, set `result.data["forced_over_live_session"]` and the matching outcome-detail key to `True` only when the override actually proceeded over a live session, and `False` otherwise. Contract W20: `force: true` alone cannot distinguish overriding a live worker from overriding a merely dirty tree, and those are not remotely the same act. Also ensure `result.data["live_session"]` is populated on the forced path, not only on the refusal path — the record must name the session that was overridden.
- [ ] T016 [US3] Extend `tests/unit/test_worktree_remove_guard.py` with the record assertions of quickstart Scenario 6: a refusal's outcome is `ok` (not `error`) with `refused: true`, `refused_by: "live_session"`, the reason sentence, and the `live_session` object; a forced override carries `forced_over_live_session: true`; and a forced removal of a *dirty tree with no session* carries `force: true` but `forced_over_live_session: false`, so the two forced paths are distinguishable from the record alone.
- [ ] T017 [US3] Extend `tests/unit/test_worktree_remove_guard.py` to assert the payload discriminator across all three outcomes: `refused_by` is `"live_session"` on the session refusal, `"git"` on the dirty-tree refusal, and absent when nothing refused; `refused_reason` always carries the sentence belonging to whichever guard refused (research R9). Include the git-refusal case so the pre-existing meaning of `refused_reason` is pinned rather than assumed.

**Checkpoint**: the most destructive thing this command can do is now the thing the log is clearest
about.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T018 [P] Add a `## The issue #79 actions` section to `docs/logging.md`, following the per-issue pattern already used for #33 and #106. Record `worktree.remove` as a new intent/outcome pair, its detail keys, why a refusal is `outcome: "ok"` rather than `"error"` (the vocabulary at `docs/logging.md:63` is fixed to `ok`/`error`/`pending`, and `cleanup.considered` is the precedent for a guard firing), and that `git.remove_worktree`'s **absence** is what evidences a refusal. State explicitly that no Principle III exception is claimed.
- [ ] T019 [P] Update the worktree/cleanup section of `README.md` (around lines 777–800). The line `worktree remove <id>  # refuses if dirty — that refusal is the point` is now incomplete: there are two refusals, and the new one is not git's. Add the third guard to "the two guards are different guards" framing, and say plainly why the manual path needed it when the automatic one already had it — cleanup is conservative and unattended; `worktree remove` is what a person reaches for when the disk is full, and it is the one that can override git.
- [ ] T020 [P] Update the docstring of `operations.worktree_remove` in `src/robot_army/operations.py`. It currently claims git's refusal *is* the guard; it is now one of three, and the docstring should say which question each answers and that only the session guard is ours. Cross-reference `contracts/worktree-removal.md`.
- [ ] T021 Run every scenario in [quickstart.md](./quickstart.md), including Scenario 5 by hand against a scratch installation, and confirm each expected outcome. Scenario 5 is the one that proves the message reads well on a real terminal rather than merely satisfying a substring assertion.
- [ ] T022 Run `uv run pytest` and `uv run ruff check src tests` from the repository root. Both must be green; the constitution's Development Workflow makes a passing suite part of the definition of complete. Compare against the T001 baseline and account for any difference.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup. **Blocks all user stories** — US1's refusal must be
  logged from the moment it exists, or the MVP ships a silent guard.
- **US1 (Phase 3)**: depends on Phase 2 only.
- **US2 (Phase 4)**: depends on US1 — the prompt names what the guard found.
- **US3 (Phase 5)**: depends on US2 — `forced_over_live_session` needs an override to describe.
- **Polish (Phase 6)**: depends on all three stories.

### Within each story

Tests are written before the implementation they cover. T006 and T007 will fail until T008 and T009
land; that failure is the point, and a test that passes before the guard exists is testing nothing.

### Parallel Opportunities

- **T002 ‖ T003** — `src/robot_army/cleanup.py` and `tests/unit/test_cleanup.py`.
- **T018 ‖ T019 ‖ T020** — `docs/logging.md`, `README.md`, `src/robot_army/operations.py`.
- Nothing else. Every remaining task touches either `src/robot_army/operations.py` or
  `tests/unit/test_worktree_remove_guard.py`, and the whole implementation is two files: marking
  same-file tasks `[P]` would invite a conflict for no gain.

```bash
# Phase 2 — the two independent files
Task: "Add LIVE_SESSION_STATES and live_sessions() in src/robot_army/cleanup.py"
Task: "Add the skipped-routing regression and live_sessions tests in tests/unit/test_cleanup.py"

# Phase 6 — three documents, three files
Task: "Add the issue #79 section to docs/logging.md"
Task: "Update the worktree/cleanup section of README.md"
Task: "Update the worktree_remove docstring in src/robot_army/operations.py"
```

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 → Phase 2 → Phase 3.
2. **Stop and validate**: quickstart Scenarios 1–3 and 7.
3. At this point the reported defect is fixed, the refusal is on the record, and the automatic path
   is provably unchanged. `--force` still works exactly as it does today for every case that does
   not involve a live session — it simply has not yet learned to say so.

### Incremental delivery

1. MVP → the harm is removed.
2. US2 → the escape hatch is honest, so a leaked session row cannot strand a worktree.
3. US3 → the record distinguishes the two forced paths.
4. Polish → the documentation matches the code that shipped.

---

## Notes

- Commit after each phase; the message explains why, not what.
- `cli.py:466` is the only caller of `worktree_remove` in the tree — there is no web route — so no
  second call site needs updating.
- No schema migration. Every column this feature reads already exists and is already populated.
- Repairing the `"still live"` substring coupling at `cleanup.py:106` is **out of scope**. It is
  pre-existing, and changing how the automatic path decides things is exactly what FR-013 forbids.
