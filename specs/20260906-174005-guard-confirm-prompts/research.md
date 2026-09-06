# Research: guarding every confirmation prompt

## R1 — Where the handling goes so that one place holds it

**Decision**: two pieces, both module-private, both in `operations.py`. A helper wraps the
*injected* `confirm` callable, catches `KeyboardInterrupt` and `EOFError`, calls a
caller-supplied recording callback with a cause label, and raises `PromptAbandoned`
carrying a fully-formed `Result`. A decorator, `@_guards_its_prompt`, worn by each
prompting operation, catches that exception and returns the `Result` it carries. No call
site handles anything.

**Rationale**: the issue's diagnosis is that the handling was written at a call site and
therefore got written once out of four times. Anything that leaves each call site with its
own `try/except` re-arms the same trap. Three shapes were weighed:

- Wrapping the *default* `confirm` (`_ask`) with the handling. Rejected: `confirm` is an
  injected parameter — that is how the CLI and every test drive these prompts — so a guard
  that lives in the default callable is bypassed by every caller that supplies its own,
  including the tests that must prove the guard works.
- One `except operations.PromptAbandoned` in `cli.main`, with no decorator. Attractive
  because it is the fewest moving parts and a fifth prompt inherits it with *nothing*
  written at all — and it was built first. **Rejected on contact with the test suite.** It
  makes an operation *raise* where it used to return, and an operation returns a `Result`:
  that is the currency `cli._dispatch` and the web interface both deal in, and
  `specs/005-onboard-is-enough/contracts/onboarding.md` describes onboarding's ways out as
  non-zero *exits with messages*, not as an exception a caller must know to catch. Four
  existing tests said so directly, one of them named
  `test_input_ending_before_an_answer_is_a_result_not_a_traceback`. Fixing three commands
  by making the fourth harder to call is a bad trade, and issue #23's closing note asks for
  the opposite.
- The decorator. Costs the author of the fifth prompt one line they have to remember —
  which is why `test_every_operation_that_prompts_wears_the_decorator` exists: an operation
  with a `confirm` parameter and no decorator fails the suite. The reminder is automatic
  even though the wrapping is not.

**Alternatives considered**: returning a sentinel value the caller must test for (four
`isinstance` checks — the same trap, quieter); letting the exception carry only a cause
(loses the `data` a `--json` run must still emit, and loses `onboard`'s approval screen,
both of which today's `onboard` handling preserves).

## R2 — Why the exception carries a `Result` rather than just a cause

`onboard` today returns `Result(code=…, lines=[*result.lines, msg], data=result.data)` — the
approval screen it already composed rides along so a non-`--json` run still shows what the
maintainer was looking at when they walked away, and `data` is what `--json` renders.
`worktree_remove` likewise has `result.data` populated (`item_id`,
`forced_over_live_session`) before it asks. An exception that carried only a cause would
throw both away. So the helper takes the caller's `lines` and `data` and builds the whole
`Result` at the point of abandonment.

## R3 — Exit codes and messages

Taken verbatim from `onboard`, which issue #23 quotes as the model:

| Cause | Label recorded | Exit | Line printed |
|---|---|---|---|
| Ctrl-C at the prompt | `interrupted_at_prompt` | `EXIT_FAILED` (1) | `interrupted` |
| input ended | `no_answer_available` | `EXIT_CHECK_FAILED` (4) | `no answer available: input ended before the prompt was answered` |

Two codes rather than one because "the maintainer changed their mind" and "this was run
somewhere with no maintainer attached" are different things to find in a shell script's
`$?`. `interrupted` matches what `main`'s existing `except KeyboardInterrupt` prints, so an
interrupt reads the same whether it lands at a prompt or elsewhere.

## R4 — What each command records, and under which action name

| Command | Action name | How the record is written |
|---|---|---|
| `onboard` | `repo.onboard` | unchanged — `_record_onboard_outcome(ctx, repo_key, cause, resolved)` |
| `worktree remove --force` | `worktree.remove` | the action is **already open** when it asks. The callback sets `abandoned` and `cause` on the open outcome dict; the exception then propagates through `ctx.audit.action`, whose own `except BaseException` writes the outcome as `error` with those keys merged in |
| `cancel` | `session.cancel` | **new name.** Nothing is open when it asks, so the callback writes a standalone `error` record naming the session and the item |
| `purge-simulated` | `purge.simulated` | nothing is open when it asks; the callback writes a standalone `error` record carrying the counts the prompt quoted |

`worktree.remove`'s intent record — flushed before the prompt, carrying `force: true` and
the worktree path as `target` — already exists and is exactly what FR-004 asks for. Today
it is followed by an `error` outcome whose only content is `EOFError`; after this change it
is followed by one that says a forced removal was abandoned, and why.

`session.cancel` is a new action name that appears **only** for an abandoned cancel. The
successful path is already recorded as `session.terminate` plus the session and work-item
transitions, and inventing a matching intent/outcome pair around a command that is already
fully reconstructable would be a larger change than the bug warrants. The asymmetry is
deliberate and documented on the audit-log page.

## R5 — The prompt stream, and `--json`

`_ask` writes its prompt to **stderr**; the other three call sites default `confirm` to
builtin `input`, which writes to **stdout**. `worktree remove` and `purge-simulated` both
accept `--json`, whose document goes to stdout whatever the exit code — so today a `--json`
run of either puts the question and the document on the same stream and neither parses.
That is true before this change and true of an *answered* prompt too; it is not what the
issue reports.

**Decision**: point all four at `_ask`. It costs one word per call site, it is required by
FR-008 for the abandonment path, and leaving two of the four on `input` would mean the
"one place all four prompts pass through" is one place for the handling and two for the
stream.

This supersedes milestone 011's FR-014 ("`cancel`, `purge_simulated` and `worktree_remove`
each ask a self-contained question with nothing composed above it, so none of them has a
screen to protect; they keep `input` and their stdout prompts"). The reasoning there was
about protecting a *screen*; it did not consider the machine-readable document, which two
of the three can be asked for. `_ask`'s docstring is updated to say so rather than left
asserting something that stopped being true.

`cancel` has no `--json` flag, so nothing about its stream is load-bearing; it moves for
uniformity.

## R6 — What is deliberately not changed

- Any answered prompt: "y", "no", a mistyped item id. Same wording, same codes, same
  records. The existing tests for those paths are expected to pass unmodified, and that is
  the check that this held.
- An interrupt during the *work*, after the answer. `main`'s existing
  `except KeyboardInterrupt` keeps handling it exactly as it does now.
- `--force` and `--yes` runs, which never reach a prompt.
- The web interface, which calls `cancel(force=True)` and so never prompts.
