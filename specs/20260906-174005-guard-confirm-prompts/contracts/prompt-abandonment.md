# Contract: giving up at a confirmation prompt

Applies to every `robot-army` command that stops and asks the maintainer a question before
acting. Today that is four commands; this contract is written so that it applies to the
fifth without amendment.

## The two ways a question goes unanswered

| Cause | How it arrives | Cause label written to the log |
|---|---|---|
| The maintainer gave up | Ctrl-C at the prompt (`KeyboardInterrupt`) | `interrupted_at_prompt` |
| There was nobody to ask | stdin at end-of-file (`EOFError`) — `< /dev/null`, a pipeline, a cron entry | `no_answer_available` |

Nothing else is caught. An error raised *inside* the guarded work, after the answer, is not
an abandonment and keeps whatever handling it has now.

## What the command does

1. **Nothing it was about to do happens.** No worktree or branch removed, no signal sent to
   a session, no rows deleted, no repository approved, no state transition, no notification.
   This holds for the typed-id prompt exactly as for the `[y/N]` ones: an absent answer is
   not the item id, and an absent answer is not `y`.
2. **One record is written** — see the table below.
3. **One line is printed** to the human-readable stream:

   | Cause | Line |
   |---|---|
   | `interrupted_at_prompt` | `interrupted` |
   | `no_answer_available` | `no answer available: input ended before the prompt was answered` |

4. **The process exits non-zero**, with the code for the cause:

   | Cause | Exit |
   |---|---|
   | `interrupted_at_prompt` | `1` (`EXIT_FAILED`) |
   | `no_answer_available` | `4` (`EXIT_CHECK_FAILED`) |

5. **No traceback is printed.**

## Streams

Every prompt is written to **stderr**. On a `--json` run, stdout therefore carries one
parseable document and nothing else — including when the run was given up on, which still
renders the command's accumulated `data`. On a run without `--json`, the line from step 3
goes to stderr, because the exit code is non-zero.

## The record

One record per abandonment, under the action name the command already uses, with
`outcome: "error"` and a `detail` carrying at least `cause`.

| Command | Action | Shape |
|---|---|---|
| `onboard` | `repo.onboard` | `refused: true`, `cause`, plus the clone path and verified origin. **Unchanged** from what milestone 011 already wrote. |
| `worktree remove --force` | `worktree.remove` | The intent record — flushed *before* the prompt, carrying `force: true`, `entity_id` the work item and `target` the worktree path — already exists. Its outcome becomes `error` with `abandoned: true` and `cause`. From the pair alone: a forced removal of that path was attempted and abandoned, and nothing was removed. |
| `cancel` | `session.cancel` | `abandoned: true`, `cause`, `entity_type: "session"`, `entity_id` the session id, and the work item in the detail. |
| `purge-simulated` | `purge.simulated` | `abandoned: true`, `cause`, and the counts the prompt quoted, so the log says what was nearly deleted. |

`session.cancel` appears **only** for an abandoned cancel. A cancel that goes through is
already fully reconstructable from `session.terminate` and the session and work-item
transitions; adding a second pair around it is not this feature's business.

## What does not change

- Any answered prompt. `y` / `yes` proceeds, anything else declines with `aborted` and
  exit `1`, and a mistyped item id at the force prompt is a decline, not an abandonment.
- The wording of the four prompts.
- `--force` and `--yes` runs, which never reach a prompt.
- The web interface, which cancels with `force=True` and so never prompts.
- An interrupt during the work itself, which `cli.main` already turns into `interrupted`
  and exit `1`.
