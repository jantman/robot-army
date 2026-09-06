# Data model

No database table, column, index or state file changes. Nothing persistent is added. The
two structures below live for the duration of one unanswered question, plus one line each
in the existing audit log.

## `PromptAbandoned` (in-process, `operations.py`)

Raised when a confirmation prompt goes unanswered; caught in exactly one place,
`cli.main`.

| Attribute | Type | Meaning |
|---|---|---|
| `result` | `Result` | The result the command would have returned: the exit code for the cause, the command's accumulated `lines` plus the one-line explanation, and the command's accumulated `data` so a `--json` run still renders a document |

It carries a finished `Result` rather than a cause code because two of the four commands
have already composed output by the time they ask — `onboard` its approval screen, and
`worktree_remove` its `data` — and an exception carrying only a cause would discard both.

## Cause labels (constants, `operations.py`)

Two string labels, written into the audit record's `detail.cause`. They are the same two
milestone 011 already writes for `onboard`, so the log needs no new vocabulary:

| Label | Meaning | Exit code |
|---|---|---|
| `interrupted_at_prompt` | Ctrl-C at the prompt | `EXIT_FAILED` (1) |
| `no_answer_available` | stdin ended before the prompt was answered | `EXIT_CHECK_FAILED` (4) |

## Audit records

Existing log, existing format, one added line per abandonment. Three of the four action
names already exist (`repo.onboard`, `worktree.remove`, `purge.simulated`); `session.cancel`
is new and is written only for an abandoned `cancel`. Field shapes are in
[contracts/prompt-abandonment.md](contracts/prompt-abandonment.md).

## State transitions

None. Every one of the four commands asks its question *before* any transition, and an
abandonment leaves the work item and session in exactly the state the command found them.
