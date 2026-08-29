# Contract: Onboarding's output, prompt, and exits

What `robot-army onboard` writes, in what order, on which stream, and what each way out leaves
behind. Supersedes the ordering described in
[`specs/005-onboard-is-enough/contracts/onboarding.md`](../../005-onboard-is-enough/contracts/onboarding.md)
§"The approval screen" — that section described this order and the implementation never
delivered it. Resolution, verification, the refusal taxonomy and what is recorded on approval
are unchanged by this feature and remain governed by 005's contract.

## Order

```
$ robot-army onboard jantman/some-repo
repository   : jantman/some-repo
clone path   : /home/jantman/GIT/some-repo   (derived from [paths] repo_root)
verified     : github.com/jantman/some-repo via origin
base ref     : main
trust        : accepted — ...

committed tool-permission settings at the base ref:
  These are applied to a dispatched session WITHOUT asking. Read them.

  --- .claude/settings.json ---
  ...

                    ← everything above has been written and flushed by this point
Approve jantman/some-repo for dispatch, recording this fingerprint? [y/N]
```

The rule, stated so it can be tested: **at the moment the process blocks for input, the entire
approval screen has already been written to its destination and flushed.** Not composed, not
buffered — written. This holds whether the destination is a terminal, a file, or a pipe.

With `--reapprove` the screen additionally carries the `recorded path:` line, its
`** CHANGED **` marker when the location moved, and the fingerprint diff — all of it before the
prompt, on the same rule.

## Streams

| Written | Stream | Note |
|---|---|---|
| the approval screen | stdout | information the maintainer asked for, whichever way the run later ends |
| the prompt | **stderr** | so a machine-readable run's document stays parseable. Changed by this feature |
| the outcome line, exit code 0 | stdout | unchanged |
| the outcome line, exit code non-zero | stderr | unchanged |
| a machine-readable document (`--json`) | **stdout, always** | previously stderr on a non-zero exit, contradicting the flag's own help. Changed by this feature |

In `--json` mode the approval screen is not written at all, and the prompt — on stderr — is
still visible and answerable.

`--json` output is a single document on stdout on every path. That is the testable form of
FR-012.

`onboard` gains the `--json` flag in this milestone. It did not have one before, so the two
rules above described a mode no invocation could reach — caught in review on #19, where the
suppression branch in `cli._dispatch` was correctly written and permanently unreachable.

## Exits

| Exit | Screen | Outcome | Code | Audit `cause` |
|---|---|---|---|---|
| approved | once | `onboarded <key>` | 0 | — (approval outcome) |
| already onboarded, fingerprint unchanged | once | `already onboarded and the fingerprint is unchanged; nothing to do` | 0 | — |
| declined at the prompt | once | `aborted` | 4 | `aborted_at_prompt` |
| interrupted at the prompt | once | `interrupted` | 1 | `interrupted_at_prompt` |
| input ended with no answer | once | names the missing answer | 4 | `no_answer_available` |
| `--yes` over unapproved committed settings | once | `refusing --yes: …` | 3 | `unapproved_committed_settings` |
| refused during resolution or verification | never composed | the refusal, naming cause and fix | 3 | the eleven causes from 005 |

**Once means once.** No line of the approval screen appears twice in a run's combined output,
on any row of that table.

**Every row leaves exactly one `repo.onboard` outcome record.** Rows four and five leave none
today; that is the change.

Codes are unchanged from 005 in every row, including the interruption's `1`. Interruption and
an explicit decline are distinguished by the recorded cause, not by the exit code — the same
decision reached two ways.

## What does not change

- The prompt's wording, including that it names the repository, which is what keeps it legible
  after a long settings block has scrolled the identity line off the screen.
- The content of the approval screen. This feature changes when it is shown, not what is in it.
- The five refusal messages, and that each names the path, how it was arrived at, and the edit
  that fixes it.
- What is written to the `repos` row on approval, and that it is written only after approval,
  in one transaction inside one audit action.
- Every other command that prompts — `cancel`, `purge-simulated`, `worktree remove --force`.
  Each asks a self-contained question with nothing composed ahead of it, keeps its prompt on
  stdout, and is untouched.
