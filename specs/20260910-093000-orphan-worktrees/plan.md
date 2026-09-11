# Implementation Plan: No Worktree Is Left Where robot-army Cannot Reach It

**Branch**: `robot-army/issue-59-purge-simulated-orphans-worktrees` | **Date**: 2026-09-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260910-093000-orphan-worktrees/spec.md`

## Summary

`purge-simulated` deletes the rows that are robot-army's only route to a worktree, then points
at `worktree remove <id>`, which needs those rows. Nothing notices the directories afterwards:
reconciliation only checks the other direction (a row with no directory).

Three changes, one per story, sharing one removal core:

1. **Purge** finds, before deleting anything, which simulated rows own a directory still on
   disk, names them in its question, and asks separately whether to remove them. `--yes` keeps
   meaning rows only; `--remove-worktrees` answers the second question. Removals run first,
   through the same core as `worktree remove`, each audited; then the rows go.
2. **Reconciliation** gains the opposite sweep: `<root>/<repo>/issue-<n>` for an onboarded
   repository, claimed by no work item, is an `orphan_worktree` anomaly. It resolves itself when
   the directory goes or a row claims it. Nothing is removed automatically.
3. **`worktree remove`** accepts a path. It refuses anything outside the root, anything claimed
   (naming the id), anything no clone lists as a worktree, and a directory a live worker is in;
   otherwise it removes worktree and branch exactly as the id form does. `worktree list` shows
   the orphans.

The shared core also closes a fourth route to the same defect (research R3): a removal the
version-control boundary *reports* while the directory survives — which is what the simulated
boundary does — is now a refusal, not "removed worktree".

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none added

**Storage**: none changed. SQLite `anomalies` gains a kind value (unconstrained text column); no
migration

**Testing**: `pytest` via `uv run pytest`. Operations tests drive `SimulatedVersionControl`
subclasses (the existing `RefusingVcs` pattern) plus a subclass that really deletes a `tmp_path`
directory and one that lists worktrees; reconcile tests use `tmp_path` as `worktree_root`

**Target Platform**: one Linux machine

**Project Type**: single Python package (CLI daemon plus local web interface)

**Performance Goals**: the orphan sweep is one `iterdir` per onboarded repository folder plus the
per-clone `git worktree list` the prunable sweep already makes (cached per pass). No sizing on
the pass

**Constraints**: nothing is removed without a confirmation or an explicit flag; nothing is
forced without a typed confirmation; only git removes a worktree (never `rmtree`)

**Scale/Scope**: `models.py`, `db.py`, `worktree.py`, `reconcile.py`, `operations.py`,
`cli.py`; tests; four guide pages

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design — see the second table.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | No module, dependency, table, config key or abstraction. One private helper (`_remove_checkout`) with three callers, extracted because FR-003/FR-012 require identical behaviour three times. One property (`Session.hosted_by_simulation`) with two callers, replacing a local expression in `cancel`. One new flag, `--remove-worktrees`, justified in R10: without it the only way to remove worktrees unattended is to change what `--yes` already means to disk. Two helpers in `worktree.py` (`orphans`, `locate`) each have three callers (sweep, list, path removal). |
| **II. Single-User, Local-First** | Unchanged. |
| **III. Total Accountability** | **What this logs:** every removal — from the id form, the path form, and the purge — is a `worktree.remove` intent/outcome pair written *before* git is asked, with the git boundary's own `git.remove_worktree`/`git.delete_branch` records inside it. Every refusal is an outcome with `refused_by`. The purge's intent records which worktrees were offered and whether removal was accepted; its outcome which were removed and which left. The sweep raises through `db.raise_anomaly` (a state change inside the database, the same as every other kind) and a failed clone listing is `reconcile.list_worktrees` as today; each retraction is `anomaly.resolved`. Even the path form's cheapest refusals (`outside_root`, `claimed`) are `worktree.remove` outcomes, so "what did I try to remove, and why did it say no" is answerable from the log. **Nothing is deliberately unlogged:** the only actions without a record of their own are reads (`iterdir`, `is_dir`, `sessions.scan`), which change nothing outside the process. |
| **IV. Interruption Tolerance** | **If it is killed halfway:** in a purge, removals happen one worktree at a time before the row transaction; each successful removal clears that row's `worktree_path` in its own transaction (as `worktree remove` does today). Killed mid-way, the rows remain; re-running offers only directories still on disk, and a row whose path was cleared or whose directory is gone is not offered. Killed between the worktree half and the branch half: the branch remains, which is the existing state `worktree remove` and `state.md` already document; for an orphan removed by path, a surviving branch is the only residue and git lists it. The sweep and resolver are idempotent by the unique index and the `resolved_at IS NULL` guard. No network call is added. |
| **V. Public Code, Unsupported Project** | `worktree remove`'s positional changes from `item_id` to `TARGET`; every existing invocation keeps its meaning (R11). No outside consumers. |
| **Operating Constraints** | Deleting user data is irreversible: it happens only after a `y` to a question that lists the paths, or an explicit flag, and forcing past git needs a typed name. Nothing is on by default. Every capability is a terminal command. |
| **Development Workflow** | Spec → plan → tasks → implement. Unit tests for each story's success path and every refusal, plus the interruption paths: a purge killed after some removals re-runs cleanly; a removal git reports while the directory survives is a refusal; a failed clone listing still raises and never resolves. |

**Verdict: PASS.** No violation, so Complexity Tracking is omitted.

## Project Structure

### Documentation (this feature)

```text
specs/20260910-093000-orphan-worktrees/
├── plan.md               # this file
├── spec.md
├── research.md           # R1–R12
├── data-model.md         # derived orphan, anomaly shape, audit shapes
├── quickstart.md
├── contracts/
│   └── cli.md            # purge-simulated, worktree remove TARGET, worktree list
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks output
```

### Source Code (repository root)

```text
src/robot_army/
├── models.py         # Session.hosted_by_simulation; "orphan_worktree" in ANOMALY_KINDS
├── db.py             # open_orphan_worktree_anomalies
├── worktree.py       # orphans(conn, config) and locate(vcs, conn, config, path)
├── reconcile.py      # _sweep_orphan_worktrees, _resolve_orphan_worktree_anomalies,
│                     # ReconcileResult.orphan_worktrees
├── operations.py     # _remove_checkout (shared core, disk check); worktree_remove uses it;
│                     # worktree_remove_path; purge_simulated offers and removes;
│                     # worktree_list shows orphans; cancel reads hosted_by_simulation
└── cli.py            # worktree remove TARGET; purge-simulated --remove-worktrees

tests/unit/
├── test_purge_worktrees.py        # new: US1
├── test_orphan_worktrees.py       # new: US2 sweep + resolution, US3 path form, list
├── test_worktree_remove_guard.py  # R3 applies to the id form
├── test_prompt_abandonment.py     # the prompting-operation set gains worktree_remove_path
└── test_cli_exit_codes.py         # the same set, if it enumerates it

docs/guide/
├── 5-outcome.md      # cleaning up: purge removes worktrees; remove by path; orphans
├── operating.md      # orphan_worktree: four kinds now clear themselves
├── audit-log.md      # worktree.remove by path / from purge; purge.simulated detail
└── state.md          # anomalies: the fourth self-resolving kind
```

**Structure Decision**: no new source module. The two derivations live in `worktree.py`, which
already owns naming and the derived condition of a worktree; the removal core stays in
`operations.py` beside its only callers.

## Phase 1 re-check (post-design)

| Question | Answer |
|---|---|
| Did the design add a dependency? | No. |
| A module, a table, a state file, a config key? | None. No config key, so `share/config.example.toml` needs no regeneration. |
| An abstraction with one implementation? | No. `_remove_checkout` has three callers, `orphans` and `locate` three each, `hosted_by_simulation` two. |
| Anything on by default that was not? | No. The sweep only reports; removal needs a `y` or `--remove-worktrees`. |
| Which documentation? | `5-outcome.md` (cleanup), `operating.md` (anomalies), `audit-log.md` (record shapes), `state.md` (anomalies table) — per `CLAUDE.md`'s table. `1-setup.md` mentions `purge-simulated` only as a command to run; checked, and updated only if its wording promises rows-only. |

**Verdict: PASS, unchanged.**
