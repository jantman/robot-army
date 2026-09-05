# Implementation Plan: Naming the repository outright on a card

**Branch**: `robot-army/issue-116-method-of-handling-cards-with-multiple` | **Date**: 2026-09-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/20260905-111520-card-repo-directive/spec.md`

## Summary

`resolve_repository` scans a card's title and description for three kinds of repository
reference, filters every candidate against the onboarded set, and resolves only when exactly
one distinct repository survives. A card that legitimately mentions two or three onboarded
repositories therefore cannot be filed at all, and the only workaround is to edit the card's
text until one reference is left — which throws away the context that made the card worth
writing.

This adds one recognised line to the card grammar:

```
robot-army: <repo URL / path / slug>
```

When present, it decides. The implementation is a fourth recogniser at the top of
`resolve_repository` that finds every such line, resolves each line's reference through the
*same* three recognisers and the *same* onboarding filter the text scan already uses, and
short-circuits: one distinct onboarded repository across all the lines resolves the card;
anything else — two lines disagreeing, or any line naming something not onboarded — holds it
with a reason that quotes the line's own text back. The ordinary text scan runs only when no
such line is on the card, so no card that resolves today changes behaviour.

Two things deliberately do **not** change. The onboarding filter still gates everything, so a
declaration cannot file an issue in a repository nobody approved (research R3). And the card's
description still reaches the issue verbatim, declaration included, because the line is
information about the card and not scaffolding to be cleaned up (FR-015).

Around that: `Resolution` gains a `source` field so the existing `trello.evaluated` record can
say *how* the repository was chosen (research R5), the held-card comment learns to describe
the line (research R6), and two guide pages are updated.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: standard library only — `re`, `pathlib`. Both are already imported
by `intake.py`. No new dependency, and none is needed.

**Storage**: unchanged. No schema change, no migration, no new state file, no new
configuration key. The only persistence touched is one added key inside the `detail` object of
the existing `trello.evaluated` audit record.

**Testing**: `pytest`, run as `uv run pytest`. The new cases extend
`tests/unit/test_repo_resolution.py`, which already carries the adversarial fixture (two
onboarded repositories, one of them deliberately without a `[repos.*]` section) that every
case here needs. A smaller number of cases land in `tests/unit/test_intake_poll.py` for the
held-card reason and the audit record's shape.

**Target Platform**: one Linux machine.

**Project Type**: single Python package (`src/robot_army`) with a CLI, a daemon, and a small
web interface. No frontend build.

**Performance Goals**: none. `resolve_repository` runs once per unlinked card per board poll —
a poll every 300 seconds over a board of hundreds of cards — and this adds one `str.replace`
and one anchored regex pass over text that three regexes already traverse.

**Constraints**: recognition must never *widen* what can be selected. A card full of pasted
log output must still be incapable of filing an issue in a repository that is not onboarded,
which is the property `tests/unit/test_repo_resolution.py` exists to defend.

**Scope/Scale**: roughly 70 lines in one module (`src/robot_army/intake.py`), test cases in
two existing test modules, and edits to two guide pages.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design — see the re-check below.*

### I. Simplicity First (YAGNI & KISS)

**Pass.** One module-level regex, one function that returns a list of strings, and a
short-circuit block at the top of an existing function. No new module, no new class hierarchy,
no new dependency. Three simplifications were chosen deliberately and recorded in
`research.md`:

- The prefix is the literal `robot-army` (R8). A configuration key selecting a different
  prefix would have exactly one caller and no second use in hand — the specific shape
  Principle I forbids.
- The declaration's reference is resolved by the three recognisers that already exist rather
  than by a grammar of its own (R3). "The same three spellings" is only true if it is the same
  code.
- `Resolution` gains one field rather than a parallel result type (R5).

### II. Single-User, Local-First

**Pass.** Nothing here touches multi-tenancy, authentication, or a hosted service. No secret
is read, written, or logged: the declaration's reference is a repository name, and it is
already stored in `cards.body` and already carried into filed issues. The onboarded set the
reference is checked against is local state.

### III. Total Accountability

**Pass, with no exception claimed.** This feature performs **no** action that changes state
outside the process — it is a pure function over text the system already holds. The decision
it makes is already recorded once per card evaluation by `trello.evaluated`, and this plan
adds `source` to that record so the log can distinguish a repository the author named from one
the system inferred (FR-014, SC-005). Every outward-facing consequence downstream — the issue
creation, the card comment, the state transition — is logged today by machinery this feature
does not modify.

Silent failure is specifically guarded against rather than merely avoided: research R4's rule
that a declaration which selects nothing **holds** the card, instead of falling back to the
text scan, exists so that a line the author wrote can never be quietly discarded.

### IV. Interruption Tolerance

**Pass, vacuously and deliberately so.** No new persistent write, no new network call, and no
new checkpoint. Resolution is recomputed from the card's stored text on every evaluation, so a
process killed mid-evaluation loses a computation and repeats it on the next poll. The
resumable four-step creation that follows a successful resolution is untouched.

### V. Public Code, Unsupported Project

**Pass.** No credential, hostname, or personal path enters the repository. No public API is
frozen: `Resolution` is internal and gaining a defaulted field breaks nothing outside this
process. Documentation goes on the guide pages for the pipeline stages affected, written for
the author's future self, and `README.md` is not grown.

### Operating Constraints and Development Workflow

**Pass.** The behaviour is reachable and observable from the terminal — `robot-army cards
--state needs_info` shows the new reasons, `robot-army rescan <card-id>` re-evaluates a card
after the line is added, and the audit log records the decision. Unit tests cover every new
branch, and because this is code parsing external input, the adversarial cases are the point
rather than an afterthought: the existing "a pasted log resolves to nothing" family is
extended with declaration-shaped text inside pasted output.

## Project Structure

### Documentation (this feature)

```text
specs/20260905-111520-card-repo-directive/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── repo-declaration.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
src/robot_army/
└── intake.py                       # the only module changed
    ├── _DECLARATION                #   new: the anchored line pattern
    ├── _declared_references()      #   new: card text → the references its lines give
    ├── _resolve_declarations()     #   new: those references → a Resolution
    ├── Resolution                  #   changed: gains `source`
    ├── resolve_repository()        #   changed: consults declarations first
    ├── evaluate_card()             #   changed: `source` into the audit detail
    └── _needs_info_comment()       #   changed: tells the author about the line

tests/unit/
├── test_repo_resolution.py         # extended: grammar, precedence, adversarial cases
└── test_intake_poll.py             # extended: held reason, audit record shape

docs/guide/
├── 2-intake.md                     # the declaration, in "When a card doesn't say enough"
└── audit-log.md                    # `trello.evaluated` now records how it decided
```

**Structure Decision**: no new module. `intake.py` is documented as "the only module that
knows what a card means", and a declaration on a card is a statement about what the card
means (research R1). Splitting it out would put half that knowledge outside the module that
claims all of it, for one function with one caller.

## Complexity Tracking

No Constitution Check violations. Nothing to justify.

## Post-Design Constitution Re-check

Re-read after `data-model.md` and `contracts/repo-declaration.md` were written.

**Still passing, on every principle.** The design as specified adds one dataclass field, one
compiled pattern, and two private functions to an existing module, and it removes nothing.
The contract confirms the property that mattered most — the onboarding filter is applied to a
declaration's reference by the *same* `_offer` and `_key_for_path` the text scan uses, so the
feature cannot broaden what a card is able to select. The one design decision that could have
gone the other way, what a failing declaration does (research R4), was settled toward holding
the card, which is the direction Principle III's prohibition on silent failure points.
