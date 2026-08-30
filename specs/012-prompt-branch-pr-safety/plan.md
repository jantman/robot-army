# Implementation Plan: Standing Delivery Instructions In The Dispatch Prompt

**Branch**: `robot-army/issue-29-ensure-that-prompts-include-pr-creation` | **Date**: 2026-08-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/012-prompt-branch-pr-safety/spec.md`

## Summary

Every dispatched session gets told two things it is not told today: that the work stays on the
feature branch it was placed on and ends pushed to `origin` with a pull request open, and that
where this repository is the *mechanism* for changing something — configuration management,
infrastructure as code, deployment definitions — an issue asking for that thing is asking for the
code that produces it rather than for it to be done by hand. An explicit instruction in the issue
body overrides both.

The whole feature is a constant and one line that appends it. `prompt.compose()` already
assembles ordered sections whose position encodes their precedence; this adds a fourth section
between the Spec Kit block and the issue. Unconditional, so no parameter, no configuration key,
and no per-repository state — see [research.md D1](research.md). Nothing is written to a
worktree, nothing new is logged, no schema changes, and the daemon gains no ability to push a
branch or open a pull request itself (FR-013).

The cost is concentrated somewhere unusual: the prose. The text has to satisfy eight functional
requirements in five paragraphs, name the fault precisely enough that a session can reason about
the cases the text does not list, and avoid a direction word that would be subtly false given
where the block sits. That text is specified byte-for-byte in
[contracts/delivery-block.md](contracts/delivery-block.md), and the work of this milestone is
mostly the work of having settled it there.

**Amended after review.** The first implementation drew the second rule at the wrong place —
"do not change the state of this or any other system", with the push, the pull request and the
test suite carved back out as exceptions. That is a faithful reading of the issue text and it is
wrong twice over: too broad, because it forbids the delivery it demands, and too narrow in the
way that matters, because the case it exists for is a session satisfying "set up and run this
service" in a Puppet repository by hand — which is wrong not for touching a machine but for
bypassing the repository that was supposed to. The rule is now drawn at the bypass, needs no
exceptions, and gives its reason. [research.md D6](research.md) records the reversal in full.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: none added. `prompt.py` imports only `re`, `pathlib`, and the `Issue`
boundary type, and continues to.

**Storage**: none. No table, column, migration, or configuration key
([data-model.md](data-model.md)).

**Testing**: pytest. New unit tests in `tests/unit/`, plus an updated golden string in
`tests/unit/test_speckit_prompt.py`.

**Target Platform**: single Linux machine, unchanged.

**Project Type**: single project — `src/robot_army/`, `tests/unit/`.

**Performance Goals**: not applicable. The change is a string concatenation in a function already
called once per dispatch.

**Constraints**: the block must stay under 1,500 characters (SC-004) and must contain no format
placeholders (FR-010). The composed prompt remains a single argv entry well inside `ARG_MAX`;
1,445 characters against a 60,000-character body cap is not a factor (FR-012).

**Scale/Scope**: one constant, one call site, one section inserted, one golden string updated,
one README section, one line each in five prose paragraphs of tests.

## Constitution Check

*GATE: passed before Phase 0 research; re-checked after Phase 1 design below.*

### I. Simplicity First (YAGNI & KISS)

**Pass.** No dependency is added. No abstraction is introduced: the block is a module constant
and the insertion is three lines inside a function that already does exactly this for two other
sections.

The tempting complexity was symmetry — `speckit_block` is a keyword argument, so a
`delivery_block` argument looks like the house style. It was rejected because the two cases
differ in the way that matters: the Spec Kit block is wrong for some repositories and therefore
needs a decision point, and this one is right for all of them and therefore needs none. A
parameter with one caller that always passes the same constant, plus a `None` branch no
production path takes, is precisely the "configuration knob with exactly one caller and no second
use in hand" the principle names. Same reasoning kills a `[dispatch] delivery_guidance` config
key; the argument is in [research.md D1](research.md) and the spec records it as an assumption
open to challenge.

### II. Single-User, Local-First

**Pass, and untouched.** No account, no tenancy, no hosted anything, no secret. The change is a
string in a process that already runs locally.

### III. Total Accountability

**Pass, with no exception claimed.**

*What does this log?* Nothing new, because nothing new happens. `prompt.compose()` is a pure
function: no file write, no subprocess, no network call, no model invocation, no state change
outside the process — so there is no action that Principle III requires a record of. What the
principle *does* care about here is that the prompt a session was given remains reconstructable
from the record, and it already is: `db.insert_session()` persists `launch_argv` as JSON on the
session row, the composed prompt is the last element of the argv chain inside it, and
`dispatch.unconfirmed` records the same argv verbatim in its detail when a launch cannot be
confirmed. The delivery block therefore appears in the durable record from the first dispatch
after this ships, without a line of logging code. Adding a `prompt.compose` audit record would
write a second copy of the same bytes into the same log — longer, not more complete
([research.md D4](research.md)).

*Silent failure?* There is no failure path to swallow. The function has no branches that can
fail and no exception handler is added.

### IV. Interruption Tolerance

**Pass, trivially, and worth stating rather than waving at.**

*What happens if it is killed halfway through?* Nothing is half-done, because nothing is done:
the block is composed in memory as part of building the launch plan, before any process is
spawned or any row is written. A kill before dispatch leaves no partial state — the item is
re-dispatched later and the identical text is recomposed from the identical constant, because
the text depends on nothing that can have changed. A kill after dispatch leaves the session row
holding the full argv it was launched with, which is the existing behaviour. No atomicity
concern arises because there is no write; no idempotency concern arises because recomposition is
deterministic (FR-010).

### V. Public Code, Unsupported Project

**Pass.** The constant is prose about branches and pull requests: no credential, no hostname, no
personal data. It is world-readable and should be. Nothing here is a public API, so no
compatibility obligation attaches to the wording — the golden string it breaks is this
repository's own test, updated deliberately in the same commit
([research.md D5](research.md)).

### Development Workflow

**Pass.** Spec Kit flow followed: specify → plan → tasks → implement, with this Constitution
Check written before implementation. Unit tests are required and are planned per requirement
rather than as a single "it contains some words" assertion. No coverage target is adopted.

One workflow point deserves naming: milestone 007's FR-010 — "with no Spec Kit block, the prompt
is byte-identical to the pre-007 output" — is deliberately superseded here. It was a statement
about *that* change, already satisfied, not a standing promise. The golden test is re-captured
rather than deleted, and its docstring records which milestone changed it and why, so a later
reader sees a superseded expectation rather than a weakened one.

### Post-Design Re-Check

Re-evaluated after Phase 1. No gate moved. The design added no entity, no dependency, no
persisted state, and no configuration surface between the pre-research check and here; the
Complexity Tracking table below is empty because there is nothing to justify.

## Project Structure

### Documentation (this feature)

```text
specs/012-prompt-branch-pr-safety/
├── plan.md                        # this file
├── spec.md
├── research.md                    # Phase 0 — five decisions
├── data-model.md                  # Phase 1 — deliberately empty of data
├── quickstart.md                  # Phase 1 — four validation checks
├── contracts/
│   └── delivery-block.md          # Phase 1 — the text, byte for byte, and its rules
├── checklists/
│   └── requirements.md
└── tasks.md                       # Phase 2 — /speckit-tasks, not created here
```

### Source Code (repository root)

```text
src/robot_army/
└── prompt.py            # + the DELIVERY constant; compose() inserts it as a fourth section

tests/unit/
├── test_delivery_prompt.py    # new — the block's content, position, and determinism
└── test_speckit_prompt.py     # updated — the golden string gains the block

README.md                # + a short section on what every session is told
```

Nothing else is touched. `dispatch.py` is explicitly *not* modified: it calls `prompt.compose()`
already, and the block being unconditional means there is nothing for the call site to decide,
pass, or log.

**Structure Decision**: the existing single-project layout. The feature lives entirely in
`src/robot_army/prompt.py`, the module whose docstring already describes prompt composition as
ordered sections encoding precedence; that docstring gains the fourth section.

## Complexity Tracking

No Constitution Check violations. Nothing to justify.
