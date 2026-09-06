# Implementation Plan: Every confirmation prompt survives being given up on

**Branch**: `robot-army/issue-23-eof-ctrl-c-at-a-confirm-prompt` | **Date**: 2026-09-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260906-174005-guard-confirm-prompts/spec.md`

## Summary

Three of the four commands that stop and ask the maintainer a question crash with a Python
traceback when there is no input to read, and two of those three are the destructive ones.
Ctrl-C at the same prompts does not traceback — the top level has caught it all along — but
leaves the same gap in the record that a closed stdin does: `cancel` and `purge-simulated`
leave no record that the command was even attempted, and `worktree remove --force` leaves
one whose only content is the name of an exception.

The fix moves the handling off the call sites entirely. One helper wraps the injected
`confirm` callable, records the abandonment under the command's own audit action, and
raises a single exception carrying a finished `Result`; one decorator, worn by each
prompting operation, turns that back into the result the command returns. Nothing is
treated as consent, the two causes stay distinguishable, and a test fails if the fifth
prompt's author forgets the decorator.

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

**Scale/Scope**: four call sites in `src/robot_army/operations.py`, plus a helper, an
exception and a decorator beside them; no change to `src/robot_army/cli.py`; their tests and
guide pages

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design — see the re-check at the
bottom of this section.*

| Principle | Verdict | Reasoning |
|---|---|---|
| **I. Simplicity First** | Pass | One helper function, one exception class, one decorator. No new dependency, no configuration knob, no abstraction with one implementation. The design was chosen *because* it removes code: four call sites' worth of prospective `try/except` becomes none. The one thing that could be called generality — the helper taking a `record` callback — has four callers today, which is the opposite of speculative. A fourth piece was built and then deleted: a second, defensive `except` in `cli.main` would have caught a forgotten decorator, but it is a branch with no live caller and "when two designs satisfy the requirement, the one with fewer moving parts wins". A test carries that job instead. |
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

**Post-implementation amendment**: the first build put the single `except` in `cli.main` and
was reverted — it made four operations raise where they used to return, which four existing
tests correctly refused. See [research.md](research.md) R1. The verdicts above are unchanged
by the substitution; the reasoning under Principle I is updated to record the piece that was
built and deleted.

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
└── operations.py     # the exception, the decorator, the helper, and the four call sites

tests/unit/
├── test_prompt_abandonment.py     # the guard itself, purge-simulated, and the two
│                                  #   regression guards that keep the handling in one place
├── test_worktree_remove_guard.py  # worktree remove --force
├── test_cancel.py                 # cancel
└── test_cli_exit_codes.py         # all of it through `main`, and the streams

tests/integration/
└── test_onboard.py                # unchanged, and passing unmodified is the point

docs/guide/
├── audit-log.md      # the four abandonment records and the new action name
├── operating.md      # what giving up at a prompt does
└── 5-outcome.md      # cancel's and worktree remove's own pages
```

`src/robot_army/cli.py` is untouched: the decorator keeps the abandonment inside the
operations layer, where a `Result` is what callers already expect.

**Structure Decision**: no new module. The helper belongs beside `_ask` in `operations.py`
because that is where the four prompts already are and where `Result` and the exit codes
already are; a `prompts.py` holding twenty lines used by one module would be a file to
find rather than a file to read.

## Implementation Approach

1. **`PromptAbandoned`** — an exception in `operations.py` carrying one attribute, the
   `Result` the command returns instead. Never seen outside that module.
2. **The helper** — takes the prompt, the injected `confirm`, a `record(cause)` callback,
   and the caller's `lines` and `data`. Returns the answer, or records and raises. It holds
   the only copy of the two cause labels, the two exit codes and the two messages.
3. **Four call sites** — each replaces `confirm(...)` with a call to the helper and supplies
   its `record` callback. `worktree_remove`'s callback mutates its open audit outcome;
   `cancel`'s and `purge_simulated`'s write a standalone record; `onboard`'s calls the
   `_record_onboard_outcome` that already exists, and its two `except` blocks are deleted.
4. **`@_guards_its_prompt`** — one decorator, worn by each of the four operations, that
   catches `PromptAbandoned` and returns the `Result` it carries. `cli.py` needs no change:
   `_dispatch` gets a result as it always did, so `--json` rendering and the stdout/stderr
   split apply unchanged. A test asserts that every operation with a `confirm` parameter
   wears it, so a fifth prompt's author is reminded by the suite rather than by a
   traceback.
5. **Streams** — the three call sites' `confirm` default moves from builtin `input` to
   `_ask`, and `_ask`'s docstring stops claiming only `onboard` uses it.
6. **Tests** — for each of the four commands, both causes: nothing happened, the exit code,
   the line, and the audit record. Plus a test that the helper is the only place handling
   this, expressed as a test of a prompt driven through it rather than as a grep.
7. **Documentation** — `docs/guide/audit-log.md` gains the four records and the new
   `session.cancel` name; `docs/guide/operating.md` gains what giving up at a prompt does.

No configuration key is added or renamed, so `exampleconfig.py` and
`share/config.example.toml` are untouched and the drift test stays green.
