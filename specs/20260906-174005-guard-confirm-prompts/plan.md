# Implementation Plan: Every confirmation prompt survives being given up on

**Branch**: `robot-army/issue-23-eof-ctrl-c-at-a-confirm-prompt` | **Date**: 2026-09-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260906-174005-guard-confirm-prompts/spec.md`

## Summary

Three of the four commands that stop and ask the maintainer a question crash with a Python
traceback when the maintainer presses Ctrl-C or when there is no input to read, and two of
those three are the destructive ones. `cancel` and `purge-simulated` leave no record that
the command was even attempted; `worktree remove --force` leaves one whose only content is
`EOFError`.

The fix moves the handling off the call sites entirely. One helper wraps the injected
`confirm` callable, records the abandonment under the command's own audit action, and
raises a single exception carrying a finished `Result`; `cli.main`, which already wraps the
dispatch in a `try`, turns that back into an exit code and a line. Nothing is treated as
consent, the two causes stay distinguishable, and the fifth prompt someone adds later is
guarded without its author writing anything.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: standard library only; no new dependency

**Storage**: the existing append-only JSONL audit log; SQLite is read but not written on
any path this feature touches

**Testing**: `pytest`, via `uv run pytest`

**Target Platform**: one Linux machine, one shell

**Project Type**: single-user CLI plus daemon

**Performance Goals**: none — this is on a path that is blocked on a human typing

**Constraints**: no configuration key; no change to any answered prompt; no change to the
web interface, which never prompts

**Scale/Scope**: four call sites in `src/robot_army/operations.py`, one `except` clause in
`src/robot_army/cli.py`, and their tests and guide pages

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design — see the re-check at the
bottom of this section.*

| Principle | Verdict | Reasoning |
|---|---|---|
| **I. Simplicity First** | Pass | One helper function, one exception class, one `except` clause. No new dependency, no configuration knob, no abstraction with one implementation. The design was chosen *because* it removes code: four call sites' worth of prospective `try/except` becomes none. The one thing that could be called generality — the helper taking a `record` callback — has four callers today, which is the opposite of speculative. |
| **II. Single-User, Local-First** | Pass | Nothing added is networked, hosted, or multi-user. The change is entirely about what one operator at one terminal sees and what the local log holds. |
| **III. Total Accountability** | Pass, and this is the point of the feature | It *closes* three gaps in the record rather than opening any. Every abandonment writes a record naming the command, the entity, and the cause, at the moment it happens. Nothing is swallowed: the exception is caught in exactly one place, where it becomes a non-zero exit and a printed line. **No action is left unlogged by this feature.** |
| **IV. Interruption Tolerance** | Pass | The feature *is* interruption tolerance, for the narrow window in which a question is unanswered. No persistent write is involved beyond one appended, flushed log line; a kill before that line lands leaves nothing done and nothing claimed. No network call, no state file, no transaction is open at any of the four prompts — verified by reading each: `purge_simulated` opens its transaction after the answer, `cancel` after it, and `worktree_remove` holds only an audit action, whose own contract is that an intent without an outcome is the detectable crash signature. |
| **V. Public Code, Unsupported Project** | Pass | No credential, no personal data. This is a breaking change to milestone 011's FR-014 (the three prompts move from stdout to stderr) and the constitution says breaking changes are acceptable whenever they serve the single user; the superseded decision is named in [research.md](research.md) R5 rather than silently reversed. |

**What does this log?** Four things, one per command: the abandonment of `repo.onboard`
(unchanged), of `worktree.remove` (on the already-open action), of `session.cancel` (a new
action name, written only for the abandonment — justified in research.md R4) and of
`purge.simulated`. Each carries the cause label that says which of the two ways the
maintainer gave up.

**What happens if it is killed halfway through?** The window this feature covers is one in
which nothing has been done yet — that is what makes the prompt a prompt. Killed before the
record is written, the log holds `worktree.remove`'s intent with no outcome (the documented
crash signature) or, for the other three, nothing at all; and in every case the world is
untouched, which is what the absent record would truthfully imply.

**Post-Phase-1 re-check**: unchanged. The design artifacts added no dependency, no
persistent structure and no configuration; the contract in `contracts/prompt-abandonment.md`
is a description of behaviour the plan already committed to, not new surface.

## Project Structure

### Documentation (this feature)

```text
specs/20260906-174005-guard-confirm-prompts/
├── plan.md                            # This file
├── spec.md
├── research.md                        # Phase 0
├── data-model.md                      # Phase 1
├── quickstart.md                      # Phase 1
├── contracts/
│   └── prompt-abandonment.md          # Phase 1
├── checklists/
│   └── requirements.md
└── tasks.md                           # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
src/robot_army/
├── operations.py     # the helper, the exception, and the four call sites
└── cli.py            # one `except` clause in `main`

tests/unit/
├── test_operations_onboard.py     # onboard's existing guard, now via the helper
├── test_operations_worktree.py    # worktree remove --force
├── test_operations_cancel.py      # cancel
├── test_operations_purge.py       # purge-simulated
└── test_cli.py                    # the exit codes and the streams

docs/guide/
├── audit-log.md      # the four abandonment records and the new action name
├── operating.md      # what giving up at a prompt does
└── 5-outcome.md      # if cancel's page is where cancel's prompt is described
```

Exact test-file names are settled against the tree in `/speckit-tasks`; the point here is
that each of the four commands gets failure-path tests for both causes, as the constitution
requires of interruption paths.

**Structure Decision**: no new module. The helper belongs beside `_ask` in `operations.py`
because that is where the four prompts already are and where `Result` and the exit codes
already are; a `prompts.py` holding twenty lines used by one module would be a file to
find rather than a file to read.

## Implementation Approach

1. **`PromptAbandoned`** — a public exception in `operations.py` carrying one attribute, the
   `Result` the command would have returned. Public because `cli.main` catches it.
2. **The helper** — takes the prompt, the injected `confirm`, a `record(cause)` callback,
   and the caller's `lines` and `data`. Returns the answer, or records and raises. It holds
   the only copy of the two cause labels, the two exit codes and the two messages.
3. **Four call sites** — each replaces `confirm(...)` with a call to the helper and supplies
   its `record` callback. `worktree_remove`'s callback mutates its open audit outcome;
   `cancel`'s and `purge_simulated`'s write a standalone record; `onboard`'s calls the
   `_record_onboard_outcome` that already exists, and its two `except` blocks are deleted.
4. **`cli.main`** — one `except operations.PromptAbandoned as gone: result = gone.result`,
   placed so that the existing `KeyboardInterrupt` clause still catches interrupts that
   happen anywhere else. Because it is assigned rather than returned, `--json` rendering and
   the stdout/stderr split apply unchanged.
5. **Streams** — the three call sites' `confirm` default moves from builtin `input` to
   `_ask`, and `_ask`'s docstring stops claiming only `onboard` uses it.
6. **Tests** — for each of the four commands, both causes: nothing happened, the exit code,
   the line, and the audit record. Plus a test that the helper is the only place handling
   this, expressed as a test of a prompt driven through it rather than as a grep.
7. **Documentation** — `docs/guide/audit-log.md` gains the four records and the new
   `session.cancel` name; `docs/guide/operating.md` gains what giving up at a prompt does.

No configuration key is added or renamed, so `exampleconfig.py` and
`share/config.example.toml` are untouched and the drift test stays green.
