# Implementation Plan: A Simulated Removal Says It Was Simulated

**Branch**: `robot-army/issue-70-worktree-remove-reports-removed` | **Date**: 2026-09-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260913-101008-honest-simulated-removal/spec.md`

## Summary

`_remove_checkout` (the removal step shared by `worktree remove <id>`, `<path>` and
`purge-simulated`), `worktree_prune` and `cleanup.clean_item` all word a simulated git operation
exactly as they word a real one, because none of them knows which it was. The version-control
boundary does — it is what stamps `[simulated]` on its audit records — so it now says so, as a
`simulated` attribute on both implementations and on the `VersionControl` protocol (R1).

With that in hand each verb words its simulated case the way `cancel` already does: "would remove
worktree …", "would delete branch …", then one line naming the effect level and the level at which
version control becomes real, derived from `effects.REAL_AT` (R2, R3). `cleanup` puts the same
fact in the reason it prints and records, and gains the `directory_survived` guard
`_remove_checkout` got in #59, so a real directory on disk is retained rather than recorded `done`
(R4). A new test holds `cancel`, `worktree remove`, `worktree prune` and `cleanup` to one rule (R5).

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none added

**Storage**: no schema change. `worktree.remove`'s outcome gains `simulated`; cleanup's recorded
`cleanup_reason` strings change wording when version control is simulated.

**Testing**: `pytest` via `uv run pytest`. Existing fakes: `SimulatedVersionControl` subclasses
`ListingVcs` and `DeletingVcs` really delete the directory and so declare `simulated = False`;
`test_cleanup.FakeVcs` gains the attribute.

**Target Platform**: one Linux machine

**Project Type**: single Python package (CLI daemon plus local web interface)

**Performance Goals**: none; one attribute read and, in cleanup, one `is_dir()` per removal.

**Constraints**:
- At a real level every line printed and every reason recorded is unchanged (FR-009).
- The source of "was it simulated" is the boundary, never `ctx.effect_level` (FR-003, R1).
- `cancel` is unchanged (FR-010).

**Scale/Scope**:
- Source: `boundaries/__init__.py` (protocol attribute), `boundaries/git.py` (both
  implementations), `effects.py` (`real_from`), `operations.py` (`_remove_checkout`,
  `worktree_prune`, `cleanup_now`, the note helper), `cleanup.py` (`clean_item`, `Decision`,
  the shared survivor reason)
- Tests: new `tests/unit/test_simulated_wording.py` (contract cases and the cross-verb rule);
  additions to `tests/unit/test_cleanup.py` (survivor guard, simulated reasons) and
  `tests/unit/test_effects.py` (`real_from`); fakes declare `simulated`
- Docs: `docs/guide/5-outcome.md`, `docs/guide/audit-log.md`

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design. See the second table.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | One attribute on an existing protocol, one derivation helper beside `is_real`, one note helper in `operations`. No new module, dependency, config key or flag. The alternatives — reading `ctx.effect_level`, or a `simulated` field on `RemovalResult` — were rejected in R1 as a second source of truth and as not covering `delete_branch`/`prune_worktrees`. |
| **II. Single-User, Local-First** | Unchanged. |
| **III. Total Accountability** | **What this logs:** nothing new is done, so no new action. `worktree.remove`'s outcome gains `simulated`, so the removal record agrees with the `[simulated]` git records beneath it. A cleanup retained for a surviving directory is recorded through the existing `cleanup.retained`, as every retention is. **Deliberately unlogged:** nothing. |
| **IV. Interruption Tolerance** | **If it is killed halfway:** nothing new is persisted. The survivor guard *removes* a write (a `done` that would have been wrong) in favour of the existing `retained` path, which is one transaction as before. No network call is added. |
| **V. Public Code, Unsupported Project** | `Decision` and `--json` gain a field; no outside consumers. No credential or personal data. |
| **Operating Constraints** | Observable at the terminal, in `--json`, and in `robot-army log`. Exit status of a simulated removal stays 0 (spec Assumptions). Nothing outward-facing is added. |
| **Development Workflow** | Spec → plan → tasks → implement. Unit tests for every changed unit, including the cleanup guard's refusal path. |

**Verdict: PASS.** No violation, so Complexity Tracking is omitted.

## Project Structure

### Documentation (this feature)

```text
specs/20260913-101008-honest-simulated-removal/
├── plan.md               # this file
├── spec.md
├── research.md           # R1–R6
├── data-model.md         # the boundary attribute; Decision; the outcome record
├── quickstart.md
├── contracts/
│   └── simulated-wording.md   # the exact lines, cases W1–W9
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks output
```

### Source Code (repository root)

```text
src/robot_army/
├── boundaries/__init__.py   # VersionControl.simulated
├── boundaries/git.py        # GitVersionControl.simulated = False; SimulatedVersionControl = True
├── effects.py               # real_from(boundary)
├── cleanup.py               # SURVIVED_REASON; survivor guard; simulated reasons; Decision.simulated
└── operations.py            # _simulated_note(); _remove_checkout, worktree_prune, cleanup_now

tests/unit/test_simulated_wording.py   # new: contract cases W1–W9, cross-verb rule
tests/unit/test_cleanup.py             # survivor guard; simulated reasons
tests/unit/test_effects.py             # real_from
docs/guide/5-outcome.md                # simulated wording; cleanup's survivor guard
docs/guide/audit-log.md                # worktree.remove gains `simulated`
```

**Structure Decision**: no new module. The survivor reason moves into `cleanup.py` as a constant
that `_remove_checkout` imports, so the two guards cannot word the same refusal differently.

## Phase 1 re-check (post-design)

| Question | Answer |
|---|---|
| Did the design add a dependency? | No. |
| A module, a table, a state file, a config key? | None. No config key, so `share/config.example.toml` needs no regeneration. |
| An abstraction with one implementation? | No. `simulated` is an attribute both implementations already imply; `real_from` sits beside `is_real` over the same table. |
| Anything on by default that was not? | No. |
| A new audit action or record shape? | A new field, `simulated`, on `worktree.remove`'s outcome. `audit-log.md` documents it. |
| Which other guide pages? | `5-outcome.md` (worktree removal and cleanup). `state.md`'s cleanup-state table is unchanged: a survivor is an ordinary `retained`. |

**Verdict: PASS, unchanged.**
