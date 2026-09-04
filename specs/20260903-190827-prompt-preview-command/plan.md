# Implementation Plan: Prompt Preview Command

**Branch**: `speckit/20260903-190827-prompt-preview-command` | **Date**: 2026-09-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260903-190827-prompt-preview-command/spec.md`

## Summary

One new function in `operations`, one parser and one table entry in `cli`, and a single
`int` widened to `int | None` in `dispatch`. No new module, no new dependency, no migration,
no configuration key, no schema change.

It is this small because the prompt is already a pure function of four arguments.
`prompt.compose(issue, repo_key=…, branch=…, instructions=…, speckit_block=…)` takes no
worktree, no database handle and no boundary, and `dispatch.build_launch_plan` does nothing
to the result but append it to an argv. The preview therefore does not need to reproduce
composition — it needs to resolve the same four arguments and call the same function, which
is the only design here that can keep FR-002 true as the prompt changes underneath it.

Three decisions carry the feature, each displacing a plausible alternative
([R1](research.md), [R3](research.md), [R6](research.md)):

- **The contextual sections are read from the item's worktree when one exists, and from the
  onboarded clone otherwise** — with the source named on stderr every time. Always using the
  clone would quietly disagree with what a dispatched session actually got; creating a
  throwaway worktree to be exact would make a print command write to disk and need cleanup
  guards.
- **`dispatch.speckit_block` is reused, not reimplemented**, at the cost of one widened
  parameter. Its detection, its per-repository suppression, and its configured command list
  are the behaviour the preview must agree with, so sharing the code is what makes agreement
  structural rather than a claim maintained by hand.
- **The prompt is the `Result`'s only line.** `main` already routes lines to stdout on
  `EXIT_OK` and stderr otherwise, so FR-003 and "stdout is empty on every failure" (FR-014)
  come out of routing that already exists rather than from five exit paths remembering.

No `--json` ([R7](research.md)). The payload is one opaque string; redirection covers every
machine use.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added, and none newly used. `httpx` reaches GitHub through
the existing `GitHubReader`; nothing else is imported that the package does not already use.

**Storage**: SQLite, read-only for this feature. No migration; `SCHEMA_VERSION` unchanged.

**Testing**: pytest. Unit tests in `tests/unit/`, one integration test in `tests/integration/`.
`tests/conftest.py` already supplies `FakeIssueReader`, `make_boundaries`, `onboard_repo` and
`repo_clone`; the only fixture change needed is a way to make `FakeIssueReader.get_issue`
raise, for the transport-failure case.

**Target Platform**: one Linux machine with a shell, as everything else here.

**Project Type**: single Python package with a CLI entry point (`robot_army.cli:main`).

**Performance Goals**: one HTTP GET and two `stat`-shaped filesystem reads. Wall clock is the
GitHub round trip; nothing is cached and nothing needs to be (SC-006).

**Constraints**: stdout must carry the prompt and nothing else (FR-003); the four exit codes
must stay distinguishable (SC-007); the printed text must equal what dispatch composes
(FR-002).

**Scale/Scope**: one command, one operation, roughly 60 lines of implementation plus tests
and two documentation edits.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design. Result unchanged: passes.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | No new module, dependency, table, or configuration key. The one abstraction touched is a parameter widened from `int` to `int \| None`, which has two real callers the moment it lands. `--json` was considered and rejected as a mode with no caller ([R7](research.md)). |
| **II. Single-User, Local-First** | No account, no authorization, no service. Reads the local database, the local clone or worktree, and the existing GitHub token. Adds no deployment surface. |
| **III. Total Accountability** | One `prompt.preview` record per invocation on **every** path, plus `speckit.detect` where it runs. Two gaps, both inherited, both enumerated and justified below and in [R4](research.md). |
| **IV. Interruption Tolerance** | Nothing persistent is written, so there is nothing to make atomic and nothing to resume. Answered in full below. |
| **V. Public Code, Unsupported Project** | No credential, hostname or personal datum enters the repository. No compatibility obligation is created: the command is new, so nothing outside depends on it. |
| **Operating Constraints** | Terminal-only, exits non-zero on failure, no GUI. The command performs no irreversible or outward-facing action, so it needs neither confirmation nor an effect-level gate — it is a read, like `robot-army show`. |
| **Development Workflow** | Unit tests for each behaviour, plus an integration test proving preview and dispatch agree. Both constitutional questions answered below. Full suite must pass. |

No violation to justify, so [Complexity Tracking](#complexity-tracking) stays empty.

### What does this log?

One `prompt.preview` record for every invocation — success, malformed arguments, repository
not onboarded, issue unavailable — naming the repository, the issue, the outcome, the branch
and its source, which directory the contextual sections came from, and whether each optional
section was included. Plus the `speckit.detect` record `dispatch.speckit_block` already
writes, keyed to the repository rather than a work item when the issue has no row. Shapes in
[contracts/audit-records.md](contracts/audit-records.md).

**Two gaps, enumerated as Governance requires** (detail in [R4](research.md)):

1. **The composed prompt text is not recorded.** Dispatch does not record it either — the
   issue body, the repository's own instructions and the delivery block are all absent from
   the log today, and `dispatch.speckit_block`'s docstring already carries that justification.
   Recording it here alone would leave the log describing the rehearsal in more detail than
   the performance.
2. **A successful issue read is not individually recorded.** `GitHubReader` logs every retry
   and every failure but takes the aggregate-read exception for successes. The
   `prompt.preview` record names what was asked for and whether it was answered, which is
   what the reconstruction standard needs.

One deliberate non-record: argparse's own rejection of a non-numeric issue number exits `2`
before the audit log is open ([R11](research.md)). Nothing was read and nothing was reached,
so there is no action to reconstruct — this is a malformed invocation, not an event.

### What happens if it is killed halfway?

Nothing to recover, and nothing to clean up. The command creates no work item, branch,
worktree, session, socket or file; it takes no lock, so a kill cannot block the daemon; and
it makes one bounded HTTP read whose timeout and retry policy are `GitHubReader`'s existing
ones. A re-run is identical to the run that was killed.

The only residue a kill can leave is a truncated final line in the audit log, which is that
log's existing and already-documented property (`audit.py` R14, which flushes per line and
tolerates a partial last record on read) rather than anything this feature introduces.

## Project Structure

### Documentation (this feature)

```text
specs/20260903-190827-prompt-preview-command/
├── plan.md                      # This file
├── spec.md                      # Phase -1 output (/speckit-specify)
├── research.md                  # Phase 0 output
├── data-model.md                # Phase 1 output
├── quickstart.md                # Phase 1 output
├── contracts/
│   ├── cli.md                   # The command's surface, exit codes, guarantees
│   └── audit-records.md         # What a run writes to the log
├── checklists/
│   └── requirements.md          # Spec quality gate
└── tasks.md                     # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/robot_army/
├── operations.py     # + prompt_preview(): the whole feature
├── cli.py            # + the `prompt` subparser and one dispatch-table entry
├── dispatch.py       # ~ speckit_block(item_id: int) -> (item_id: int | None)
└── prompt.py         # unchanged — compose(), branch_name(), read_instructions()

tests/
├── conftest.py                          # ~ FakeIssueReader gains a raise-on-get_issue hook
├── unit/test_prompt_preview.py          # + resolution, streams, exit codes, audit records
└── integration/test_prompt_preview_matches_dispatch.py
                                         # + the fidelity proof (FR-002, SC-002)

README.md             # ~ "What every session is told" gains the way to read one
```

**Structure Decision**: no new module. `operations.py` is where every CLI verb's logic
already lives, and that placement is what lets milestone 002's web surface call a verb
without reimplementing it. A `preview.py` holding one function with one caller would be
speculative structure, which Principle I rules out; `prompt.py` is the wrong home because it
is pure and this function reads a database, a filesystem and a network boundary.

## Implementation outline

Ordered so that each step is independently testable and the P1 story lands first.

1. **`dispatch.speckit_block`**: widen `item_id` to `int | None` and key the audit record on
   the repository when it is `None`. Dispatch's call site is unchanged; its record shape is
   unchanged. Covered by an assertion in the existing Spec Kit dispatch tests plus one new
   case ([R3](research.md)).
2. **`operations.prompt_preview(ctx, repo_key, issue_number, *, notes=None)`**: validate the
   slug and number, resolve the repository, fetch the issue, find the row, choose the context
   root, compose, write the audit record, and return a `Result` whose only line is the prompt
   ([data-model.md](data-model.md)).
3. **`cli.py`**: a `prompt` subparser with two positionals (`repo_key`, `issue_number` with
   `type=int`), and a table entry passing `notes=sys.stderr`. **Not** added to the `--json`
   set and **not** added to `READ_COMMANDS`, since that set is what grants `--json`.
4. **Tests**: unit coverage of each resolution rule, each exit code, each stream, and the
   audit record; an integration test that composes the same fixture through
   `prompt_preview` and `dispatch.build_launch_plan` and asserts the strings are equal.
5. **Documentation**: a short paragraph in README's "What every session is told" — the
   section that describes the prompt's contents is the section that should say how to read
   one — and the command's exit codes wherever the others are listed.

## Complexity Tracking

No Constitution Check violation, so nothing to justify here.
