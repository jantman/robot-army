# Implementation Plan: The onboarding security review reads real committed settings at every effect level

**Branch**: `robot-army/issue-20-onboard-cannot-see-committed-claude` | **Date**: 2026-09-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260906-070620-real-settings-read-in-simulation/spec.md`

## Summary

`SimulatedVersionControl.show_file_at_ref` returns `None` unconditionally, so below `local` the
onboarding review screen is blank for every repository and an empty fingerprint is recorded as
approved. Delegate that read to the real implementation, as `list_remotes` and `remote_url` already
do; do the same for `default_remote`, which is the identical defect in the same class; write the
rule that decides the question into the class and enforce its coverage with a test.

The whole production change is four lines of delegation plus docstrings. The weight of the work is
in the tests that pin the property — *simulated answers the same as real* — and in the guide, which
currently lets a reader believe the review is reduced below `live`.

## Technical Context

**Language/Version**: Python 3.11+ (running 3.14 locally)

**Primary Dependencies**: standard library; `git` invoked as a subprocess through `robot_army.subproc.run`. Nothing new.

**Storage**: SQLite (`repos` table holds the approval and its fingerprint); the git object store is the read's source.

**Testing**: `pytest`, via `uv run pytest`. New unit tests in `tests/unit/test_git_boundary.py`, `tests/unit/test_trust.py`, `tests/unit/test_onboard*.py`.

**Target Platform**: single Linux machine, single user.

**Project Type**: single-project CLI + daemon (`src/robot_army/`).

**Performance Goals**: none new. The change adds, at `plan` and below, two `git show` invocations per onboarding and per dispatch gate, plus one `git remote` per dispatch preparation — each already timeout-bounded by `QUICK_TIMEOUT` (30 s) and each local.

**Constraints**: the simulated boundary must still create nothing. No fetch, no worktree, no ref write at any level below the table's.

**Scale/Scope**: two methods in one class, their docstrings, one test table, one guide page, one spec-quickstart note.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design — see the re-check at the end.*

### I. Simplicity First (YAGNI & KISS)

**PASS.** The change removes code rather than adding it: two method bodies become one-line
delegations to an object the class already holds. No new dependency, no new abstraction, no new
configuration key. The one thing that could have been speculative — a `_REAL_READS` declaration in
`src/` — was rejected in research R4 for having exactly one consumer; the coverage table lives in
the test that consumes it.

### II. Single-User, Local-First

**PASS.** No multi-user surface is touched. The reads are local `git` invocations against a clone on
this machine; nothing gains a network dependency. `default_remote` becoming real reads local git
config only — it does not contact the remote (that is `remote_branch_head`, unchanged).

### III. Total Accountability

**PASS, and the record improves.** Nothing that changes state outside the process is added, so
there is **no unlogged action to declare and no Principle III exception is claimed**. What changes
is the shape of two existing records: today a simulated `git.show_file_at_ref` /
`git.default_remote` record with `simulated=True`; afterwards the real implementation's
`git.subprocess` records. That is the correct direction — the reads genuinely happen, and recording
a real read as simulated is the lie the class docstring already names. Reconstruction is preserved:
the log still answers what was read, when, from which clone and at which ref.

The security-relevant consequence is itself accountability: the approval record stops asserting
"this repository commits no settings" for repositories that do.

### IV. Interruption Tolerance

**PASS.** No persistent-state write is added or changed. Both reads are idempotent, side-effect-free
and already timeout-bounded (`QUICK_TIMEOUT`). Killed halfway through, the onboarding writes
nothing — the approval row is written only after the human answers, which is existing behaviour and
is unchanged. A read that cannot answer returns `None`/`[]` rather than raising, which the existing
tests already require of the real implementation.

### V. Public Code, Unsupported Project

**PASS.** No credential enters the repository, the config or the log: the reads return committed
file text and remote *names*. Remote URLs, which may embed credentials, are read by `remote_url`,
which is unchanged and whose callers already normalise through `repos.normalise_remote` (FR-032).
No backward-compatibility shim is added for the changed approval behaviour — existing blank
approvals are blocked and re-approved, which is a breaking change made deliberately in the single
user's favour.

**Documentation obligation (CLAUDE.md §1)**: this changes onboarding and effect-level behaviour, so
[`docs/guide/1-setup.md`](../../docs/guide/1-setup.md) is the page to update. No configuration key
is added, removed or renamed, so §2 (the example-config regeneration) does not apply and
`share/config.example.toml` is untouched.

### Development Workflow

**PASS.** Unit tests ship for every changed unit of behaviour. The boundary is code parsing external
input and reaching a security decision, so it additionally carries failure-path tests: a path that
is not a repository, a ref that does not exist, a settings file that is not valid UTF-8, and a
settings file that is empty. The full suite must pass before this is complete.

## Project Structure

### Documentation (this feature)

```text
specs/20260906-070620-real-settings-read-in-simulation/
├── plan.md              # This file
├── spec.md
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── simulated-reads.md   # Phase 1 — the subject rule, per protocol member
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
src/robot_army/
├── boundaries/
│   ├── __init__.py          # VersionControl protocol — docstring for show_file_at_ref
│   └── git.py               # THE CHANGE: SimulatedVersionControl.show_file_at_ref,
│                            #   .default_remote, and the class docstring's rule
├── dispatch.py              # unchanged — read_committed_settings, compute_fingerprint,
│                            #   check_launch_gate all become correct by the boundary fix
├── operations.py            # unchanged — onboard() renders whatever it is handed
└── effects.py               # unchanged — the wiring table is right as it stands

tests/unit/
├── test_git_boundary.py     # simulated == real for each real-answering read;
│                            #   the protocol-coverage table (FR-007)
├── test_trust.py            # fingerprint and review text under a simulated boundary
└── test_onboard_*.py        # the review screen at plan; the stale-blank-approval block

docs/guide/
└── 1-setup.md               # the review is real at every effect level (FR-008)
```

**Structure Decision**: single project, existing layout. This feature adds no module and no
directory; it changes two methods in `src/robot_army/boundaries/git.py` and the tests and guide
that describe them.

## Complexity Tracking

No Constitution Check violations. Nothing to justify.

## Post-Design Constitution Re-Check

Re-evaluated after the Phase 1 artifacts (`data-model.md`, `contracts/simulated-reads.md`,
`quickstart.md`) were written. **No gate changed verdict.**

The design added one artifact that did not exist before Phase 0 — the subject rule, written as a
contract and enforced by a test table. It was checked against Principle I specifically, since a
written rule is the kind of thing that becomes a framework: it introduces no runtime code, no
indirection and no extension point, and its enforcement is one assertion in an existing test module.
It documents a decision the codebase was already making case by case, which is the opposite of
speculative generality.

Phase 1 confirmed there is no data-model change: the `repos` table's shape, the fingerprint's
format and the approval's meaning are all unchanged. Only the *values* written into an existing
column become correct.
