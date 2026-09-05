# Implementation Plan: Warn at onboarding when Spec Kit numbers features by scanning

**Branch**: `robot-army/issue-41-spec-numbers-collide-because-they-are` | **Date**: 2026-09-05 |
**Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260905-192844-warn-speckit-numbering/spec.md`

## Summary

One new filesystem read, one new block on the onboarding approval screen, and two new fields on a
record that already exists.

`speckit.py` gains a reader for `.specify/init-options.json` that answers a repository's feature
numbering as one of three things — **timestamp** (collision-free), **scanned** (the default, and
what issue #41 was filed about), or **undetermined** (the file is there and cannot be trusted to
say). `operations.onboard` asks that question when, and only when, Spec Kit detection already says
yes, prints an advisory block for the second and third answers, and carries the answer into both the
`--json` document and the existing `repo.onboard` audit detail.

Nothing blocks, nothing is stored, nothing is written into the onboarded repository. The system's
own numbering is unaffected — this repository already uses `timestamp`.

## Technical Context

**Language/Version**: Python 3.11+, standard library only (`json`, `pathlib`, `re`)

**Primary Dependencies**: none new. The two modules involved are already imported by each other's
neighbours; `operations.py` does not currently import `speckit`, and will.

**Storage**: none. The finding is derived per `onboard` run and never persisted (FR-012). No
migration, no new column, no new file.

**Testing**: pytest. A new unit module for the reader, additions to the existing onboarding
integration module for the screen, the JSON document, and the audit detail.

**Target Platform**: one Linux machine, one user.

**Project Type**: single Python package with a CLI and a daemon.

**Performance Goals**: not applicable. One `stat` and at most one small file read, on an
interactive command a maintainer runs once per repository.

**Constraints**: the reader must never raise (FR-008), and must never let a repository's own
configuration file compose lines on the screen that is being used to decide whether to trust that
repository (FR-007).

**Scale/Scope**: roughly 60 lines of source across two modules, plus tests and two guide pages.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 — see [Post-design re-check](#post-design-re-check).*

### I. Simplicity First

**Pass.** One function, one frozen dataclass with three fields, one screen block. Explicitly
rejected, and recorded in [research.md](research.md): a configuration key to silence the warning
(one hypothetical user, R5), a general "read a repository's tool configuration" abstraction (one
caller, R2), and any attempt to *fix* the numbering (R7). The reader is a plain filesystem read for
the same reason the rest of `speckit.py` is — see that module's docstring.

### II. Single-User, Local-First

**Pass.** A local filesystem read of a clone the maintainer already approved the location of. No
network, no account, no state, no service. Nothing about the feature assumes a second user, and the
warning is addressed to the one person who runs `onboard`.

### III. Total Accountability

**What does this log?** The finding is added to the **existing** `repo.onboard` audit detail as
`speckit` and `speckit_numbering`, so the log answers what the maintainer was looking at when they
approved (FR-013). No new action is introduced, because no new state-changing action occurs.

**What goes unlogged, and why.** The individual file reads — the `stat` and the `read` of
`.specify/init-options.json` — are not logged. This is the *same* documented exception the guide
already carries for Spec Kit detection's own reads
([`audit-log.md`](../../docs/guide/audit-log.md), "what is deliberately not logged"): they change
no state outside the process, and the *decision* they inform is logged on the line that records the
onboarding. Logging each read would bury that line rather than support it. The exception is named
here as Principle III requires, and the guide's table gains this read alongside the detection reads
it already covers.

**Silent failure.** None. Every failure mode of the read has a defined, visible outcome:
undetermined is printed as undetermined, on the screen and in the JSON document. There is no path
where a failure produces silence.

### IV. Interruption Tolerance

**What happens if it is killed halfway through?** Nothing, and that is not a shrug. This feature
performs no writes, holds no lock, opens no transaction, and makes no network call. A kill during
the read leaves the machine exactly as it was; a kill after the screen is printed and before the
answer is the pre-existing `KeyboardInterrupt` path, which is already recorded as
`interrupted_at_prompt` and is untouched here. There is nothing to make atomic because there is
nothing being written, and nothing to restart because a re-run re-derives the same answer from the
same files.

The reader is code parsing external input, so it carries failure-path tests as the Development
Workflow section requires: absent file, unreadable file, invalid JSON, non-object JSON, non-string
value, hostile value, oversized file.

### V. Public Code, Unsupported Project

**Pass.** No credential is read, printed, or recorded — `.specify/init-options.json` is a tool
configuration file with no credential fields, and the only thing this feature ever echoes from it is
a value it has first confirmed is a short identifier (FR-007). No backward-compatibility shim: the
deprecated `branch_numbering` key is deliberately not consulted (R6). Documentation lands on the two
guide pages the runtime guidance names for this kind of change, written for the author's future
self.

### Operating Constraints

**Pass.** Reachable and observable from the terminal, which is the only place it appears. No new
persistent data. Not an irreversible or outward-facing action — it is a read and a sentence.

## Project Structure

### Documentation (this feature)

```text
specs/20260905-192844-warn-speckit-numbering/
├── plan.md                       # This file
├── spec.md
├── research.md                   # Phase 0: the seven decisions and what was rejected
├── data-model.md                 # Phase 1: the Numbering answer and its three outcomes
├── quickstart.md                 # Phase 1: how to see it work
├── contracts/
│   └── numbering-warning.md      # Phase 1: the outcomes table, the exact screen text,
│                                 #          the JSON keys and the audit fields
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
src/robot_army/
├── speckit.py        # + INIT_OPTIONS, SAFE_NUMBERING, Numbering, numbering()
└── operations.py     # onboard(): ask, print, put in `data`, put in the audit detail

tests/
├── conftest.py                      # make_speckit_tree gains `init_options`
├── unit/test_speckit_numbering.py   # new: one test per row of the outcomes table
└── integration/test_onboard.py      # + the screen, the JSON document, the audit detail

docs/guide/
├── 1-setup.md        # the onboarding section: what the warning says and why
└── audit-log.md      # the two new `repo.onboard` detail fields, and the unlogged read
```

**Structure Decision**: the existing layout, unchanged. The reader belongs in `speckit.py` because
that module already owns every question of the form "what does this directory's Spec Kit
installation look like", already reads the filesystem to answer them, and already carries the
argument for why it does it that way. The screen belongs in `operations.onboard` because that
function owns the approval screen and its ordering, and because `speckit.py` deliberately answers
questions rather than composing output — the same split `repos.py` announces in its own docstring.

**One import edge is added**: `operations` → `speckit`. `speckit.py`'s docstring forbids it
importing `config`; it says nothing about being imported, and `operations` already imports `db`,
`dispatch`, and `repos`, of which `dispatch` imports `speckit`. No cycle is created.

## Complexity Tracking

No Constitution Check violations. The table is left empty deliberately rather than removed, so that
a later reader can see the gate was applied and found nothing to justify.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| *(none)* | | |

## Post-design re-check

Re-evaluated after `data-model.md` and `contracts/numbering-warning.md` were written.

The design did not grow. The dataclass stayed at three fields; the outcomes stayed at three; no
configuration key, no database column, no migration, and no new audit action appeared. The one thing
Phase 1 *added* was the identifier guard on the echoed value (contracts, "The value is never
trusted to be text"), which reduces what the feature will print rather than increasing what it does,
and which the constitution's requirement for failure-path tests on code parsing external input was
already asking for. All five principles still pass on the same reasoning given above.
