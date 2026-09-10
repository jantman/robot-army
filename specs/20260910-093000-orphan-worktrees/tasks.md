---

description: "Task list for issue #59 — no worktree left where robot-army cannot reach it"
---

# Tasks: No Worktree Is Left Where robot-army Cannot Reach It

**Input**: Design documents from `specs/20260910-093000-orphan-worktrees/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli.md, quickstart.md

**Tests**: Required. The constitution requires unit tests for every new or changed unit of
behaviour, and failure/interruption-path tests for state-changing code.

**Organization**: Grouped by user story. The shared removal core (Phase 2) is foundational
because all three stories use it — US1 and US3 to remove, US2's anomaly note to name the command
that works.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1, US2, US3 — the spec's stories

---

## Phase 1: Setup

No setup: no dependency, config key, migration or module is added (plan.md, Phase 1 re-check).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the two shared pieces every story builds on — the simulated-host signature and the
removal core that follows the disk.

- [X] T001 [P] Add `Session.hosted_by_simulation` (`dry_run and pid == 0 and proc_start is None`) with a docstring carrying the reasoning now in `cancel`'s comment, in `src/robot_army/models.py`; make `operations.cancel` read it instead of its local expression in `src/robot_army/operations.py` (research R2)
- [X] T002 [P] Unit tests for `Session.hosted_by_simulation`: true only for the full signature; a `no-remote` row (real pid) and a live row with `pid == 0` but `dry_run` false are both false, in `tests/unit/test_purge_worktrees.py`
- [X] T003 Extract `_remove_checkout(ctx, *, path, branch, clone, force, outcome, result) -> bool` from `worktree_remove` in `src/robot_army/operations.py`: git removal; then, if git reported success but `Path(path).is_dir()`, a refusal with `refused_by: "directory_survived"` and no branch half; otherwise branch deletion and the existing messages/`WARNING`. `worktree_remove` (item-id form) calls it and clears `worktree_path` only when it returns true (research R1, R3)
- [X] T004 Tests in `tests/unit/test_worktree_remove_guard.py`: the id form over a real `tmp_path` directory with plain `SimulatedVersionControl` reports `directory_survived`, exit 1, does not delete the branch, and leaves `worktree_path` set; every existing test there still passes unchanged

**Checkpoint**: `uv run pytest tests/unit/test_worktree_remove_guard.py tests/unit/test_cancel.py` green.

---

## Phase 3: User Story 1 — Purge can take its worktrees with it (Priority: P1) 🎯 MVP

**Goal**: `purge-simulated` names the worktrees its rows own, asks separately, removes accepted
ones before deleting rows, and names every survivor with a command that works.

**Independent Test**: simulated item with a real `tmp_path` worktree; purge answering `y`,`y`;
directory and branch gone, rows gone, `worktree.remove` pair precedes the `purge.simulated`
outcome.

### Tests for User Story 1

- [X] T005 [P] [US1] In `tests/unit/test_purge_worktrees.py`, add a `DeletingVcs(SimulatedVersionControl)` whose `remove_worktree` really removes the `tmp_path` directory (and can be told to refuse one path), then test: both questions asked with paths/branches listed; `y`,`y` removes and purges, with each `worktree.remove` record (`detail.by == "purge-simulated"`) before the purge's delete; `y`,`n` purges and leaves directories, output naming `robot-army worktree remove <path>` per survivor; `n` asks nothing more and changes nothing
- [X] T006 [P] [US1] In `tests/unit/test_purge_worktrees.py`, test the flag matrix (research R10): `--yes` alone never asks and never removes; `--yes --remove-worktrees` removes without asking; `--remove-worktrees` alone asks only the row question; a row whose directory does not exist is not offered and nothing is reported as removed
- [X] T007 [P] [US1] In `tests/unit/test_purge_worktrees.py`, test the refusal and interruption paths: git refuses one (dirty) → others removed, rows purged, refused path named with reason, exit 1; an open session with a real pid refuses that one (`live_session`) while a simulation-hosted open session does not; plain `SimulatedVersionControl` over a real directory reports it left (`directory_survived`), never removed; a purge "killed" after the first removal (simulate by removing one via the core and not deleting rows) re-runs offering only the survivor; abandoning the second question (EOF / Ctrl-C) removes and deletes nothing and records the abandonment on `purge.simulated`
- [ ] T008 [P] [US1] Update `tests/unit/test_prompt_abandonment.py` and, if it enumerates the same set, `tests/unit/test_cli_exit_codes.py`, so the prompting-operation set includes the new `worktree_remove_path` (added in US3; do this with T021 if US3 lands separately)

### Implementation for User Story 1

- [X] T009 [US1] Add `db.simulated_worktrees(conn)` or equivalent query in `src/robot_army/db.py` returning simulated work items with a `worktree_path` (a list-of-items filter over `list_work_items(include_simulated=True)` in `operations.py` is acceptable if no SQL is needed — pick the one that keeps the rule in one place)
- [X] T010 [US1] Rewrite `operations.purge_simulated(ctx, *, assume_yes, remove_worktrees=False, confirm)` in `src/robot_army/operations.py` per contracts/cli.md: offered = simulated items whose `worktree_path` is a directory (with branch and `worktree.human_size(directory_size)`); row question lists them; second question per R10; `purge.simulated` intent detail gains `worktrees` and `remove_worktrees`; for each accepted: session guard via `cleanup_mod.live_sessions` filtered by `not s.hosted_by_simulation`, else `worktree.remove` action (`entity_type="work_item"`, `detail={"force": False, "by": "purge-simulated"}`) around `_remove_checkout`, clearing `worktree_path` on success; then the row transaction; output per contract, dropping the old "NOT removed — use `worktree remove`" sentence; `data` gains `worktrees_offered`, `worktrees_removed`, `worktrees_left`, `remove_worktrees`; exit 1 when an accepted removal was refused. Update the docstring (and `db.purge_simulated`'s) to say why removal happens here now
- [X] T011 [US1] Add `--remove-worktrees` to the `purge-simulated` parser and pass it through in `src/robot_army/cli.py`

**Checkpoint**: `uv run pytest tests/unit/test_purge_worktrees.py tests/unit/test_prompt_abandonment.py` green; US1 is shippable alone.

---

## Phase 4: User Story 2 — Reconciliation reports an unclaimed worktree (Priority: P2)

**Goal**: `orphan_worktree` raised for `<root>/<short>/issue-<n>` under an onboarded repository
that no work item claims; resolves itself; nothing removed.

**Independent Test**: `tmp_path` as `worktree_root`, an onboarded repo, `issue-99` directory
with no row → one anomaly; remove directory → resolved with `anomaly.resolved`.

### Tests for User Story 2

- [ ] T012 [P] [US2] In `tests/unit/test_orphan_worktrees.py`, test `worktree.orphans(conn, config)`: finds unclaimed `issue-<n>`; ignores a claimed one (real, simulated, and a `done` item); ignores `my-checkout`, `issue-x`, files named `issue-3`, and folders for repositories not onboarded; compares resolved paths (a stored path with a trailing slash or via a symlinked root still claims); a missing root yields nothing
- [ ] T013 [P] [US2] In `tests/unit/test_orphan_worktrees.py`, test the sweep through `reconcile.reconcile(...)` or `_sweep_orphan_worktrees` directly: one anomaly with `entity_type == "worktree"`, `dry_run` false, detail `path`/`listed_by`/`branch`/`note`; a second pass raises no duplicate and the summary's `orphan_worktrees` counts only new ones; a clone listing that raises `BoundaryError` still raises the anomaly with `listing_failed` and writes `reconcile.list_worktrees`; a clone that lists it fills `listed_by` and `branch`
- [ ] T014 [P] [US2] In `tests/unit/test_orphan_worktrees.py`, test resolution: directory removed → resolved, one `anomaly.resolved` with `reason: "directory_gone"`; a work item later claims it → resolved with `claimed_by_item`; still present and unclaimed → stays open; repeated passes write one resolution only
- [ ] T015 [P] [US2] Add `orphan_worktree` to the kind lists asserted in `tests/unit/test_anomalies_since.py` / `tests/unit/test_anomaly_resolution.py` where they enumerate the self-resolving or known kinds (only if they enumerate; do not add assertions they do not already make)

### Implementation for User Story 2

- [ ] T016 [P] [US2] Add `"orphan_worktree"` to `ANOMALY_KINDS` with a comment in `src/robot_army/models.py`; add `db.open_orphan_worktree_anomalies(conn)` shaped like `open_orphan_session_anomalies` in `src/robot_army/db.py`
- [ ] T017 [US2] Add `orphans(conn, config) -> list[Path]` and `locate(vcs, conn, config, path, *, cache=None) -> Located | None` (repo key, clone, branch) to `src/robot_army/worktree.py` (research R4–R6); keep imports free of cycles (import `db` inside the function if needed, as `repos.resolve` does)
- [ ] T018 [US2] Add `_sweep_orphan_worktrees` and `_resolve_orphan_worktree_anomalies` to `src/robot_army/reconcile.py`, called right after `_sweep_worktrees`; add `ReconcileResult.orphan_worktrees` and the `orphan_worktrees` summary key. Do not name the effect level or its type anywhere in `reconcile.py`, comments included (a test greps for it)

**Checkpoint**: `uv run pytest tests/unit/test_orphan_worktrees.py tests/unit/test_reconcile.py` green.

---

## Phase 5: User Story 3 — Removal by path, and orphans in `worktree list` (Priority: P3)

**Goal**: `worktree remove <path>` removes an unclaimed worktree and its branch under its own
guards; `worktree list` shows orphans.

**Independent Test**: an unclaimed worktree a (test) clone lists → `worktree remove <path>`
removes it and its branch, audited by path.

### Tests for User Story 3

- [ ] T019 [P] [US3] In `tests/unit/test_orphan_worktrees.py`, with a `ListingVcs` that lists the orphan and really deletes it, test the path form: success removes worktree and branch, `worktree.remove` pair with `entity_type == "worktree"` and `detail.by == "path"`; each refusal in contract order with its `refused_by` and exit code (`outside_root`, `claimed` naming the id, `not_a_directory`, `not_a_worktree`, `live_worker` via a stubbed scan/`proc_root`); git's dirty refusal; `--force` asks for the directory name and a wrong answer aborts; a detached-HEAD worktree removes only the directory; `directory_survived` when plain `SimulatedVersionControl` claims success
- [ ] T020 [P] [US3] In `tests/unit/test_orphan_worktrees.py`, test `worktree_list`: orphans appear after claimed rows with `—` item and `unclaimed` condition; JSON has `claimed` on every entry and `item_id: null` for orphans; with only orphans it does not print "no worktrees recorded"; orphans are shown with and without `--include-simulated`
- [ ] T021 [P] [US3] CLI tests in `tests/unit/test_orphan_worktrees.py` (or `tests/unit/test_cli_exit_codes.py` if that is where parser tests live): `worktree remove 42` still dispatches to the id form; `worktree remove /some/path` and `worktree remove ./42` dispatch to the path form

### Implementation for User Story 3

- [ ] T022 [US3] Add `@_guards_its_prompt worktree_remove_path(ctx, path, *, force=False, confirm=_ask, registry_dir=None, proc_root=None)` in `src/robot_army/operations.py` per contracts/cli.md and research R9, using `worktree.locate`, `sessions.scan` + `RegistryEntry.alive` + `sessions.under_root` for the live-worker guard, and `_remove_checkout`
- [ ] T023 [US3] Extend `worktree_list` in `src/robot_army/operations.py` with orphan rows (`worktree.orphans`, `worktree.locate` for branch, `directory_size`) and the `claimed` key
- [ ] T024 [US3] Change `worktree remove`'s positional to `target` (metavar `ITEM_ID|PATH`) and dispatch digits to `worktree_remove`, anything else to `worktree_remove_path`, in `src/robot_army/cli.py`

**Checkpoint**: `uv run pytest tests/unit/test_orphan_worktrees.py` green.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T025 [P] Update `docs/guide/5-outcome.md` "Cleaning up": purge offers to remove its worktrees (and `--remove-worktrees`), `worktree remove <path>`, orphans in `worktree list`, and the `directory_survived` rule
- [ ] T026 [P] Update `docs/guide/operating.md`: `orphan_worktree` in the anomalies worth understanding, and "four kinds now clear themselves"
- [ ] T027 [P] Update `docs/guide/audit-log.md`: `worktree.remove` by path and from a purge, the new `refused_by` values, `purge.simulated`'s new detail/outcome keys, and `anomaly.resolved` for `orphan_worktree`
- [ ] T028 [P] Update `docs/guide/state.md` anomalies section: the fourth self-resolving kind and why its retraction is a positive observation
- [ ] T029 Check `docs/guide/1-setup.md` and `README.md` for wording that promises purge never touches disk; correct if so (README stays under 150 lines)
- [ ] T030 Run `uv run pytest` (whole suite) and `uv run ruff check` if configured; fix anything red
- [ ] T031 Mark every task above `[X]` as it completes

---

## Dependencies & Execution Order

- **Phase 2** (T001–T004) blocks everything: US1 and US3 call `_remove_checkout`; US1 needs `hosted_by_simulation`.
- **US1** (T005–T011) depends only on Phase 2.
- **US2** (T012–T018) depends on nothing in US1; T017 (`worktree.orphans`/`locate`) is also needed by US3.
- **US3** (T019–T024) depends on Phase 2 and T017.
- **Polish** after the stories it documents.

### Parallel Opportunities

- T001 ∥ T002; T003 then T004.
- Within US1: T005, T006, T007 are one new file — write together; T008 is separate.
- US2's tests T012–T015 in parallel with T016; T017 before T018.
- Docs T025–T028 all in parallel.

## Implementation Strategy

MVP is Phase 2 + US1: it closes the window the issue reports. US2 finds the three orphans that
already exist on the reporting machine; US3 makes them removable. All three ship in one PR, in
that order, committed per phase.
