# Research: Read Before You Approve

**Feature**: 011-onboard-review-before-prompt | **Date**: 2026-08-29

The spec describes an ordering defect. The code makes the ordering look correct — `onboard`
composes the approval screen above the prompt, with a comment saying it does so deliberately.
The defect lives one layer down, in how a command's output reaches the terminal at all. So the
research here is mostly about that layer, and about the three exits from the prompt that the
fix makes reachable in practice.

## R1 — Why the screen arrives late

`operations.Result.say()` appends to a list (`operations.py:74`). Nothing is written anywhere.
`cli.main` renders that list and prints it at `cli.py:296-298`, *after* `_dispatch` has
returned. `onboard` blocks on `confirm(...)` at `operations.py:1031`, inside `_dispatch`.

So every line of the approval screen is still sitting in a Python list when the process blocks
for input. The comment at `operations.py:967-969` ("These three lines come **first** … because
they answer *which repository is about to be trusted*") describes an intent the output
mechanism silently discards. The contract at `specs/005-onboard-is-enough/contracts/onboarding.md`
describes the same intent, and is likewise not what the maintainer sees.

**Decision**: fix the mechanism, not the composition. The order in which `onboard` says things
is already right; what is missing is a point at which the accumulated lines are written out.

**This is the whole bug.** Everything below follows from choosing where that point is.

## R2 — How the screen is written out

**Decision**: give `Result` one explicit method that writes everything said so far to a stream
and forgets it, and call it from `onboard` at exactly one place — the point where the screen
ends and the outcome begins.

```
flush_to(stream) -> writes the accumulated lines, flushed, then clears them
```

Clearing is what satisfies FR-006. `main` later renders whatever is left, which after the
flush is only the outcome line — so no line can be printed twice, on any path, without a
second `flush_to` call that nothing makes.

**Alternatives considered**:

| Option | Rejected because |
|---|---|
| `say()` writes through to a stream as it is called | The outcome lines would stream too, onto whichever stream was attached. Today a failure's outcome goes to stderr and a success's to stdout, decided by the exit code — which is not known while the lines are being said. Streaming everything would either lose that split or require the split to be guessed early. |
| Print the screen directly with `print()` and keep it out of `Result.lines` | Loses the screen from `result.lines` entirely, which four existing tests assert on to check the screen's *composition* (`test_onboard.py:146,166,464`). Those tests are checking the right thing and should not be rewritten to read a stream. |
| Split `onboard` into "compose and return a pending approval" and "record it", with the CLI driving the prompt between them | The honest architecture, and too large for this. It moves the prompt, the abort path, the `--yes` refusal and the audit records across a module boundary to fix an ordering bug. Principle I: fewer moving parts wins. |

## R3 — Who supplies the stream, and what happens when nobody does

**Decision**: `operations.onboard` gains `out: TextIO | None = None`. `None` means "do not
flush" — the existing behaviour, unchanged. `cli` passes `sys.stdout`.

The default matters for two reasons. Every existing onboarding test calls `operations.onboard`
directly and asserts on `result.lines`; with `out=None` all of them keep passing and keep
testing composition. And `None` is also what the machine-readable mode passes (R4), so the
parameter is doing real work in both directions rather than being a test affordance.

The cost is that the default preserves the old ordering for a caller who forgets to pass a
stream. There is exactly one caller — `cli.py:393` — so the exposure is a single line, and the
new ordering tests pass a real stream through that same function.

## R4 — Machine-readable mode

FR-012 requires that a `--json` run's machine-readable stream carry the document and nothing
else. Two things currently violate that, and the second is not about onboarding at all.

**The prompt text.** `input(prompt)` writes its prompt to stdout. In `--json` mode that lands
in the middle of the JSON document.

**Decision**: onboard's prompt is written to **stderr**, always — not only in machine-readable
mode. One rule is easier to hold than two, an interactive terminal shows both streams anyway,
and it means the machine-readable path is not a special case that only gets exercised when
someone remembers to test it. Implemented as a module-level default for `onboard`'s `confirm`
parameter, so injected confirms in tests are untouched.

**The document's own stream.** `cli.main` sends the rendered output to stdout when the exit
code is 0 and stderr otherwise (`cli.py:295`). In `--json` mode that means a failing run's
JSON document goes to stderr — where, after the change above, the prompt also went. A
`--json` run that is declined would put the prompt and the document on the same stream.

**Decision**: in machine-readable mode the document goes to stdout regardless of exit code.
Human-readable output keeps today's split unchanged.

This is a cross-command change, and it is worth being explicit that it is one. The
justification is that `--json`'s own help text at `cli.py:242` already reads "machine-readable
output on stdout", so this makes the flag do what it says, and no test anywhere asserts that a
document arrives on stderr. It is the smallest change that lets a `--json` onboarding run be
both answerable and parseable. FR-014 constrains prompt text, exit codes and recorded outcomes
outside onboarding; the stream a machine-readable document is written on is none of those.

## R5 — Interruption at the prompt

Today `KeyboardInterrupt` at the prompt propagates out of `_dispatch` to `cli.py:287`, which
prints `interrupted` to stderr and returns `EXIT_FAILED`. The `repo.onboard` audit action is
opened *after* the prompt, so nothing is written: the log holds no trace that onboarding was
attempted. `contracts/onboarding.md` says every non-zero exit is written to the audit log, and
Principle III's reconstruction standard says the same. This is a pre-existing gap that story 1
promotes from theoretical to routine — a maintainer who reads a wrong clone path has no reason
to type anything.

**Decision**: catch `KeyboardInterrupt` around the `confirm(...)` call inside `onboard`, write
a refusal outcome with cause `interrupted_at_prompt` through the existing
`_record_onboard_outcome`, and return `Result(code=EXIT_FAILED, lines=["interrupted"])`.

The exit code and the message stay exactly what they are today, which is what SC-005 asks for;
`main`'s own `KeyboardInterrupt` handler stops firing for this command because the exception no
longer reaches it, and produces identical observable output when it does not. The cause value
is what distinguishes this from `aborted_at_prompt` (FR-011) — the distinction lives in the
record, not the exit code, because "I gave up" and "I said no" are the same decision arrived at
two ways.

## R6 — End of input at the prompt

Adjacent and found while reading R5: `robot-army onboard some/repo < /dev/null` raises
`EOFError` from `input()`, which nothing catches — `main` handles `PreconditionFailed` and
`KeyboardInterrupt` and no more. The result is a traceback and no audit record.

**Decision**: handle it in the same `except` clause as R5, with its own cause
`no_answer_available`, exiting `EXIT_CHECK_FAILED` — the decline code, because an absent answer
is not an approval and the contract already gives declining its own code.

This is one clause wider than the spec asks for. It is included because it is the same code
path, the same record, and the alternative is a traceback that Principle III cannot account
for. It is separable: dropping it means keeping today's traceback.

## R7 — Stream discipline, exit by exit

The table the implementation has to satisfy. "Screen" is the block flushed by R2.

| Exit | Screen | Outcome line | Exit code | Audit cause |
|---|---|---|---|---|
| Approved | stdout, once | `onboarded <key>` → stdout | 0 | — (approval, not a refusal) |
| Already onboarded, fingerprint unchanged | stdout, once | `already onboarded …` → stdout | 0 | — |
| Declined at the prompt | stdout, once | `aborted` → stderr | 4 | `aborted_at_prompt` |
| Interrupted at the prompt | stdout, once | `interrupted` → stderr | 1 | `interrupted_at_prompt` |
| No answer available | stdout, once | outcome line → stderr | 4 | `no_answer_available` |
| `--yes` with unapproved committed settings | stdout, once | refusal → stderr | 3 | `unapproved_committed_settings` |
| Refused during resolution or verification | never composed | refusal → stderr | 3 | the eleven existing causes |

The flush point sits after the fingerprint-diff block and before the already-onboarded check,
which is the last line of screen and the first line of outcome. Every row above is downstream
of it.

## R8 — The constitution's two questions

**What does this log?** No new action. `repo.onboard` gains two `cause` values on its existing
refusal outcome (R5, R6). The approval record is untouched in content and still written only
after approval, inside the same single transaction.

Writing the screen to a terminal is not logged, and that is a deliberate, named omission under
Principle III's exception clause: it changes no state outside the process that the audit log
does not already describe, the run's outcome record says what was resolved and what was
decided, and logging "printed some lines" would be noise in a file whose value is that it is
not noisy.

**What happens if it is killed halfway?** Better than today. The flush happens before any
database write and before the audit action is opened; a run killed while the screen is on
screen has written nothing anywhere, which is unchanged. A run killed *at the prompt* now
leaves a record where before it left none — that is R5, and it is the improvement. A run killed
after approval is unchanged: one audit action, one transaction, both already atomic.

## R9 — Proving the ordering

The ordering cannot be proved by inspecting `result.lines`; that is what made the defect
invisible. It has to be proved by observing what had reached the stream at the moment input was
demanded.

**Decision**: tests pass a real writable stream as `out` and an injected `confirm` that
snapshots that stream's contents before returning an answer. The assertion is on the snapshot,
not on the final output — "the screen was already there when I was asked" rather than "the
screen appears somewhere".

The four existing composition tests keep asserting on `result.lines` with `out` unset. They
answer a different question — *is the screen right* — and should keep answering it.

## R10 — The other three prompts

`cancel`, `purge-simulated` and `worktree remove --force` each ask a self-contained question
with nothing said before it. Verified by reading all three (`operations.py:1346`, `1422`,
`1888`): no `say()` precedes any of their `confirm()` calls, so none has a screen to lose.

**Decision**: leave them exactly as they are — same `input` default, same stdout prompt, same
codes. FR-014 requires it, and Principle I has no interest in a consistency none of them needs.
Their prompts stay on stdout while onboarding's moves to stderr; that asymmetry is the price of
not changing three commands to fix one, and it is the cheaper side of the trade.

## R11 — Documentation to update

- `docs/logging.md:286` — the `repo.onboard` cause table gains `interrupted_at_prompt` and
  `no_answer_available`.
- `specs/005-onboard-is-enough/contracts/onboarding.md` — its approval-screen section describes
  an ordering that was never delivered. It is superseded, not contradicted, by this feature's
  contract; a pointer is added rather than a rewrite, because 005's contract is the record of
  what 005 decided.
