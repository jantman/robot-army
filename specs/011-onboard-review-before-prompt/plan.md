# Implementation Plan: Read Before You Approve — The Onboarding Screen Reaches the Terminal First

**Branch**: `011-onboard-review-before-prompt` | **Date**: 2026-08-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/011-onboard-review-before-prompt/spec.md`

## Summary

`robot-army onboard` asks for approval before showing what is being approved. The composition is
already correct — `onboard` says the repository, clone path, verified origin, base ref, trust
verdict and committed settings above the prompt, and a comment says it does so deliberately. But
`Result.say()` only appends to a list, and `cli.main` prints that list after the command returns,
so the process blocks for input with the whole screen still in memory.

The fix is one flush point: `Result` gains a method that writes what has been said so far to a
caller-supplied stream and clears it, and `onboard` calls it once, at the boundary between the
screen and the outcome. Clearing is what makes "printed exactly once" structural rather than a
thing to remember on five exit paths.

Two consequences follow from the maintainer now actually reaching that prompt informed. Ctrl-C
becomes an ordinary way to leave, and today it leaves no audit record at all — so the
interruption (and the end-of-input case next to it, which is currently an unhandled traceback)
gets recorded through the helper the other prompt-stage refusals already use. And asking a
question writes the question somewhere: onboarding's prompt moves to stderr, and `--json`
documents move to stdout unconditionally, so a machine-readable run can be both answered and
parsed.

No schema change, no migration, no new dependency, no new audit action.

## Technical Context

**Language/Version**: Python 3.14 (existing project)

**Primary Dependencies**: none added. The change uses `print(..., flush=True)`, `sys.stdout`,
`sys.stderr` and `input` — standard library, all already imported in the touched modules.

**Storage**: SQLite at the documented layout path; **unchanged by this feature**. The audit log
is the append-only JSONL already in place; two `cause` values are added to an existing action.

**Testing**: pytest. New ordering tests in `tests/integration/test_onboard.py`; stream and exit
code coverage in `tests/unit/test_cli_exit_codes.py`.

**Target Platform**: single Linux machine with a shell.

**Project Type**: CLI plus daemon plus local web interface. This feature touches the CLI path
only.

**Performance Goals**: none. The change writes the same bytes at a different moment.

**Constraints**: the approval screen must be *flushed*, not merely written, before the prompt
blocks — the difference is only observable when output is redirected, which is exactly where a
naive fix would look correct and be wrong.

**Scale/Scope**: two source files (`operations.py`, `cli.py`), two documentation files, roughly
40 lines of production change. Five exit paths to hold correct.

## Constitution Check

*GATE: passed before Phase 0. Re-checked after Phase 1 — see below.*

| Principle | Verdict | Reasoning |
|---|---|---|
| **I. Simplicity First** | **Pass** | One method on an existing dataclass and one parameter on one function. The alternative designs — auto-streaming `say()`, or splitting `onboard` into compose-and-decide with the CLI driving the prompt — are recorded and rejected in [research.md](research.md) R2. No new abstraction, no new module, no dependency, no configuration knob. |
| **II. Single-User, Local-First** | **Pass** | Terminal output ordering on one machine. No network, no state, no accounts. |
| **III. Total Accountability** | **Pass, and improved** | Two paths that today exit non-zero with no record — interruption at the prompt, and input ending with no answer — gain one. The approval record is unchanged and still written only after approval. One omission is named and justified below. |
| **IV. Interruption Tolerance** | **Pass, and improved** | The flush happens before any write and before the audit action opens, so a run killed mid-screen still writes nothing. A run killed *at the prompt* now leaves a record where it left none. Nothing about the approval transaction changes. |
| **V. Public Code, Unsupported Project** | **Pass** | No credential can reach the screen — `verified_line()` emits a normalised identity and never a raw URL, which milestone 005 established and `test_onboard.py:445` guards. This feature changes when that line is printed, not what it contains. No packaging, no compatibility shim; the `--json` stream change is a deliberate break that serves the single user. |

**Development Workflow gates.**

*What does this log?* No new action. `repo.onboard` gains two `cause` values on the refusal
outcome milestone 005 introduced: `interrupted_at_prompt` and `no_answer_available`. Written by
the existing `_record_onboard_outcome`, carrying the same detail as the other prompt-stage
refusals.

*What is deliberately not logged?* Writing the approval screen to a terminal. Named here as
Principle III requires. It changes no state the audit log does not already describe — the run's
outcome record says what was resolved and what was decided — and a record saying "printed some
lines" would be noise in a file whose worth is that it has none.

*What happens if it is killed halfway?* Covered under Principle IV above and in
[research.md](research.md) R8. Strictly better than today at every point on the timeline.

*Unit tests.* Required and planned: the flush behaviour, each of the five exit paths, the two
new audit causes, and the stream discipline. The ordering assertion is made from inside the
injected prompt, snapshotting the stream at the moment input is demanded — the only form that
would have caught the original defect, since inspecting the final output cannot distinguish
"before" from "after".

### Re-check after Phase 1 design

Design produced no new entity, no new file in `src/`, and no schema change ([data-model.md](data-model.md)).
Two decisions widen slightly past the literal issue text and are recorded rather than absorbed
silently:

1. **Auditing the interrupted and end-of-input exits** (research R5, R6). Justified by
   `contracts/onboarding.md`'s existing rule that every non-zero exit is written to the log, and
   by the fact that this feature is what makes interruption a normal way to leave. Isolated in
   User Story 3 and separable.
2. **`--json` documents move to stdout unconditionally** (research R4). A cross-command change,
   entered in Complexity Tracking below.

No principle moves from pass to fail. The gate stands.

## Project Structure

### Documentation (this feature)

```text
specs/011-onboard-review-before-prompt/
├── plan.md                      # This file
├── spec.md                      # /speckit-specify output
├── research.md                  # Phase 0 — R1–R11
├── data-model.md                # Phase 1 — no schema change; entities and audit causes
├── quickstart.md                # Phase 1 — six manual checks
├── contracts/
│   └── onboard-output.md        # Phase 1 — order, streams, exits
├── checklists/
│   └── requirements.md          # spec quality checklist
└── tasks.md                     # /speckit-tasks output — NOT created here
```

### Source code (repository root)

```text
src/robot_army/
├── operations.py    # Result.flush_to(); onboard() gains `out`; prompt default to stderr;
│                    #   KeyboardInterrupt / EOFError handled at the prompt
└── cli.py           # passes the stream (or None in --json); --json renders to stdout always

tests/
├── integration/
│   └── test_onboard.py        # ordering, the five exits, the two new causes
└── unit/
    └── test_cli_exit_codes.py # stream discipline, --json parses on every path

docs/
└── logging.md       # the repo.onboard cause table gains two rows

specs/005-onboard-is-enough/contracts/onboarding.md   # pointer to this feature's contract
```

**Structure Decision**: the existing single-package layout, unchanged. This feature adds no
module. `operations.py` holds both `Result` and `onboard`, which is where the change belongs;
`cli.py` is the only caller and the only place that knows whether output is for a human.

## Complexity Tracking

| Violation | Why needed | Simpler alternative rejected because |
|---|---|---|
| `--json` output moves to stdout on non-zero exits — a change affecting every command, to fix one | FR-012 requires a machine-readable run to be both answerable and parseable. With the prompt on stderr (research R4) and a failing run's document also on stderr, a declined `--json` onboarding would put both on the same stream and neither would parse. | Keeping the document on stderr for failures would leave FR-012 unmet on exactly the paths a prompt creates. Putting the prompt on stdout instead breaks the successful path rather than the failing one. The flag's own help at `cli.py:242` already reads "machine-readable output on stdout", so this makes it true rather than inventing a rule, and no test anywhere asserts a document on stderr. |
| Handling end-of-input at the prompt, which the spec does not name | It is the same `except` clause, the same helper and the same record as the interruption the spec does name; the alternative is the unhandled `EOFError` traceback that exists today, which Principle III cannot account for. | Deferring it means shipping a feature that makes the prompt reachable while leaving one way of reaching it as a crash. Separable if unwanted — dropping the clause restores today's traceback and nothing else changes. |

Neither entry adds a moving part to the system. Both are single-clause changes whose cost is
that they reach slightly past the issue, which is why they are written down here rather than
folded into the diff.
