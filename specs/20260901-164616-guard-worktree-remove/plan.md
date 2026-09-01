# Implementation Plan: Refuse to Remove a Worktree While Its Session Is Open

**Branch**: `20260901-164616-guard-worktree-remove` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260901-164616-guard-worktree-remove/spec.md`

## Summary

`operations.worktree_remove` (`operations.py:1449`) checks four things — the item exists, it has a
worktree, the repository resolves, and (with `--force`) a typed confirmation — and then hands the
question to git. Git refuses a dirty or untracked tree, and that refusal is deliberately the guard.
A **read-only session leaves the tree clean**, so git has no objection, and on 2026-08-31 the
worktree of a still-running worker was removed along with its branch. The worker carried on with
its working directory reported as `(deleted)`.

The automatic path already asks the right question — `cleanup.eligible` (`cleanup.py:80-86`) checks
every session row for the item and records `skipped` when one is open. The manual path never asks
it. That is the wrong way round: cleanup is conservative and unattended, while `worktree remove` is
what a person reaches for when `/home` is at 93%, and it is the one that can override git.

The change is small and has four parts:

1. **Lift the existing definition** to `cleanup.LIVE_SESSION_STATES` + `cleanup.live_sessions()`,
   and call it from both paths. One definition, two callers (FR-014).
2. **Refuse in `worktree_remove`** before any git call, any prompt and any write, with
   `EXIT_PRECONDITION` — the code its sibling refusal in the same function already uses, and which
   makes the two refusals distinguishable from the exit status alone.
3. **Keep `--force`**, with the confirmation it already demands, and make its prompt say a live
   worker is in there before it reads anything.
4. **Log it**, which today is impossible: the command writes *no* audit record of its own (R4), so
   a refusal before the git boundary would leave nothing behind. One `worktree.remove`
   intent/outcome pair covers the refusal, the override, and — closing a pre-existing gap at zero
   cost — puts the work item id on a record that currently only names a path.

**The report's own suggested fix would not have fixed the reported case** (R1).
`reconcile.SESSION_BEARING_STATES` is a set of *work item* states, `{DISPATCHING, ACTIVE}`; the item
in the incident was `done`, so a guard written against it would have permitted the removal. And
`db.latest_session_for_item` returns only the highest attempt, missing the live worker of a
superseded one — the case `reconcile`'s own `orphan_session` docstring exists to describe. The
guard therefore reads **every** session row and **never** reads the item's state.

No schema migration, no new dependency, no new module. One new constant, one new function, one new
private dataclass, one new action name, three new payload keys.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`).

**Primary Dependencies**: none added. `httpx` remains the sole runtime dependency; everything here
is existing internal modules (`cleanup`, `db`, `procinfo`, `audit`).

**Storage**: SQLite via `db.py`. **No migration.** `sessions.state`, `.attempt`, `.session_id`,
`.pid`, `.proc_start`, `.host_socket` all exist and are populated by ordinary operation — see
[data-model.md](./data-model.md).

**Testing**: pytest. One new module `tests/unit/test_worktree_remove_guard.py` driving
`operations.worktree_remove` against `SimulatedVersionControl` (`boundaries/git.py:276`), which
logs every intended git operation, so "nothing was removed" is asserted as *no
`git.remove_worktree` record exists* rather than as a surviving directory (R12). Extensions to
`tests/integration/test_worktree_removal.py` (real git, real directory, real branch) and
`tests/unit/test_cleanup.py` (the automatic path is unchanged).

**Target Platform**: single Linux machine.

**Project Type**: single-project CLI/daemon with a small local web interface. This command has **no
web route** — `cli.py:466` is its only caller in the tree.

**Performance Goals**: not a throughput feature. The guard is one indexed query on `sessions` plus,
at most, one `/proc` stat.

**Constraints**: no removal that succeeds today may become a refusal (SC-005); the automatic path's
behaviour, including its `skipped` reason string, must not change (FR-013, R3); the guard must not
depend on being able to see a process (R6).

**Scale/Scope**: one removal at a time, on demand. Two production files carry the change
(`cleanup.py`, `operations.py`), plus `README.md` and `docs/logging.md`.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 — see "Post-design re-check" below.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | Passes, with **no** Complexity Tracking entries. The feature *removes* duplication rather than adding structure: the live-session predicate exists once and gains a second caller. No policy object, no configuration knob, no new module, no injection seam beyond the `confirm` callable the function already takes. The one judgement call — `liveness` as a four-valued word rather than a boolean — is justified in data-model.md by there being four honest answers and no defensible way to fold "cannot tell" into "alive" or "dead". |
| **II. Single-User, Local-First** | Passes. Local SQLite and `/proc`; no network, no new path, no new configuration, no new state. The operating-system user remains the trust boundary. |
| **III. Total Accountability** | Passes, and improves. Today this command logs nothing under its own name; a refusal before the git boundary would be invisible, and a forced override is recorded as `git.remove_worktree {force: true}` — indistinguishable from forcing past a dirty tree. The new `worktree.remove` pair records the decision, the session that caused it, and `forced_over_live_session`. **No Principle III exception is claimed.** |
| **IV. Interruption Tolerance** | Passes; analysis in R11. The refusal path writes nothing but its flushed intent, so there is no half-refused state to reach. An interrupted confirmation prompt is recorded by `audit.action`'s exception branch (`audit.py:243`) and re-raised. The forced path's two-step window is unchanged and already documented at `docs/state.md:422`. |
| **V. Public Code, Unsupported Project** | Passes. `result.data` gains keys with no compatibility shim, which is explicitly permitted; the sole caller is in this repository. Documentation is updated in the same change. |

### What this logs (required by Development Workflow)

- **`worktree.remove`** — **new**, an `intent`/`outcome` pair around the whole operation.
  `entity_type="work_item"`, `entity_id=<item id>`, `target=<worktree path>`, intent detail
  `{"force": …}`. The outcome carries `refused`, `refused_by`, `reason`, `live_session`,
  `forced_over_live_session`, `worktree_removed`, `branch_deleted`. Full shape at
  [contracts/worktree-removal.md](./contracts/worktree-removal.md) W17–W21.
- **`git.remove_worktree` / `git.delete_branch`** — unchanged, and **absent** on a refusal. That
  absence is the evidence that nothing was removed, and it is what the unit tests assert on.
- **`cleanup.considered`** — unchanged. The extraction changes how the live list is computed, never
  what is recorded about it.
- **A refusal is `outcome: "ok"`**, not `"error"` (R5). The vocabulary is fixed at
  `docs/logging.md:63` to `ok` / `error` / `pending`, and the precedent for a guard firing is
  `cleanup.considered`, which records a `skipped` decision as `ok`. Nothing failed: the command was
  asked a question and answered it.

Nothing this feature adds goes unlogged.

### What happens if it is killed halfway (required by Development Workflow)

- **Killed during the guard**: the intent is on disk; nothing else is written and nothing is
  removed. An intent with no outcome is exactly the crash signature Principle IV asks for. The item
  is untouched, so every sweep that visited it still does.
- **Killed at the confirmation prompt**: the same, plus an `error` outcome from `audit.action`'s
  `BaseException` branch. An abandoned prompt is reconstructible.
- **Killed between the worktree removal and the branch deletion** (forced path only): unchanged
  from today, and already in the interruption table at `docs/state.md:422`.

This feature opens **no new interruption window**: the path it adds performs no removal and writes
no state.

## Project Structure

### Documentation (this feature)

```text
specs/20260901-164616-guard-worktree-remove/
├── plan.md                          # This file
├── research.md                      # Phase 0 — R1..R12, all read out of the tree
├── data-model.md                    # Phase 1 — no migration; one constant, one helper, three keys
├── quickstart.md                    # Phase 1 — seven scenarios, safe to stage by hand
├── contracts/
│   └── worktree-removal.md          # Phase 1 — W1..W25
├── checklists/
│   └── requirements.md
├── spec.md
└── tasks.md                         # /speckit-tasks output — not created here
```

### Source Code (repository root)

```text
src/robot_army/
├── cleanup.py            # LIVE_SESSION_STATES + live_sessions(); eligible() calls it.
│                         #   Its reason string is unchanged — clean_item routes on a
│                         #   substring of it (R3)
└── operations.py         # THE CHANGE: worktree_remove() evaluates the guard first,
                          #   refuses with EXIT_PRECONDITION, names the session in the
                          #   forced prompt, and wraps the whole operation in the
                          #   worktree.remove action

tests/
├── unit/
│   ├── test_worktree_remove_guard.py   # NEW — refusals, message, prompt, record, payload
│   └── test_cleanup.py                 # extended — the automatic path still records `skipped`
└── integration/
    └── test_worktree_removal.py        # extended — real git: the directory and branch survive

docs/
└── logging.md            # "## The issue #79 actions" — the new pair, following the
                          #   per-issue section pattern already used for #33 and #106

README.md                 # the worktree/cleanup section: the third guard, and why
```

**Structure Decision**: unchanged single-project layout. The feature touches the module that owns
the reclaim policy (`cleanup.py`) and the module that owns the CLI verbs (`operations.py`). Nothing
new is created outside `tests/` and this spec directory.

## Complexity Tracking

No entries. Nothing in this design adds an abstraction, a dependency, a configuration knob, or a
second way to do something that already has one. The only structural move is the opposite: a
predicate that existed inline in one place now exists once and is called from two.

The one coupling worth naming — `operations` importing a policy from `cleanup` — is not new;
`operations.py:45` already imports `cleanup as cleanup_mod`, and FR-014 requires the two paths to
share the definition rather than restate it. The alternatives (a third module, or a copy of the
two-state tuple) are recorded and rejected in R2.

## Post-design re-check (after Phase 1)

Re-run against the completed artifacts:

- **I. Simplicity** — the design shrank during Phase 0. An early sketch had the guard consult
  process liveness and refuse only on a confirmed live process; R6 killed it, and what replaced it
  is *less* code as well as more correct. A `RefusalKind` enum was replaced by the existing string
  reason plus one discriminator key (R9). **Still passes**, with no Complexity Tracking entries.
- **II. Single-user** — unchanged; no new surface.
- **III. Accountability** — Phase 1 added `forced_over_live_session` to the outcome, because W20
  showed that `force: true` alone cannot distinguish overriding a live worker from overriding a
  dirty tree, and those are not remotely the same act. **Still passes.**
- **IV. Interruption** — unchanged; the added path writes nothing.
- **V. Public code** — the contract, the README and the logging documentation are amended in the
  same change as the code they govern. **Still passes.**

**Gate: PASSED.** No `NEEDS CLARIFICATION` remains; the spec's one debatable decision — whether
liveness gates or merely informs the refusal — was resolved in the spec's Assumptions before
planning and is now grounded in a measured reason (R6, `procinfo.py:120-121`).
