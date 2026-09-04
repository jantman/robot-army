# Research: Prompt Preview Command

Phase 0 for [plan.md](plan.md). Every decision below displaced at least one plausible
alternative; the alternative is named so a later reader can tell a choice from an accident.

## R1 — Where the contextual sections are read from

**Decision.** The repository's `.claude/robot-army.md` and the Spec Kit detection are read
from the work item's recorded `worktree_path` when a row exists and that directory is
present on disk; otherwise from the onboarded clone path that `repos.resolve` returns.

**Rationale.** `prompt.read_instructions` and `speckit.detect` both take a directory, and
dispatch hands them the worktree. For an issue that has already been dispatched, that
worktree *is* the input dispatch used — reading anywhere else would answer a different
question than the one User Story 2 asks. For an issue that has never been dispatched there
is no worktree, and the clone the worktree would have been cut from is the closest thing
that exists.

**Alternatives considered.**

- *Always the clone.* Simpler by one branch, and wrong for the only story that needs the
  answer: a dispatched item's worktree can hold a `.claude/robot-army.md` the clone does
  not, and the preview would then confidently disagree with what the session was sent.
- *Create a throwaway worktree so the answer is exact.* A preview that costs a `git
  worktree add` and a `rm -rf` is a dispatch in all but name — it writes to disk, it can
  be killed halfway, and it needs the cleanup guards this project already found hard.
  Principle I and Principle IV both point the other way for a command whose entire job is
  to print text.
- *Refuse when there is no worktree.* Kills User Story 1, which is the P1 story and the
  reason the feature exists.

**Cost, stated plainly.** The clone can sit on a different branch or carry uncommitted
changes, so a preview taken from it can differ from what a fresh worktree would hold. R2
is what keeps that honest rather than hidden.

## R2 — Naming the source, always, on the diagnostic stream

**Decision.** Every successful run writes exactly one note to the diagnostic stream naming
the directory the contextual sections came from and which of the two kinds it is. The note
is written whether the source was a worktree, a clone, or unreadable.

**Rationale.** FR-009. Without it, "no repository instructions appear in this prompt" has
three causes a reader cannot tell apart: the file is genuinely absent, the directory was
the wrong one, or the directory could not be read at all. One line removes all three.

**Alternative considered.** *Note only the fallback.* Then the absence of a note means
"read from the worktree", which is a rule the reader has to know rather than a fact on the
screen — and it is silently wrong for a run where the clone was also missing.

## R3 — The audit record for a preview that has no work item

**Decision.** Two changes:

1. `dispatch.speckit_block`'s `item_id: int` becomes `item_id: int | None`. When it is
   `None`, the `speckit.detect` record is keyed `entity_type="repo"`,
   `entity_id=repo_key` instead of `entity_type="work_item"`. Dispatch passes an id as it
   always has, so its record shape is unchanged.
2. The operation writes one `prompt.preview` record per invocation, keyed
   `entity_type="issue"`, `entity_id="<owner>/<repo>#<number>"` — the same key shape
   `poll.rejected` already uses for an issue that has no row.

**Rationale.** The feature's whole claim is that the preview and the dispatch compose the
same text; the only way to keep that true under later edits is for both to run the same
code. `speckit_block` is that code, and its one obstacle is a parameter that assumes a
database row. Widening it costs one union type and has two real callers, so it is not
speculative generality.

**Alternatives considered.**

- *Reimplement detection inside `operations`.* Two copies of a decision that must agree
  exactly, which is the defect this feature exists to make visible.
- *Pass a sentinel id such as `0`.* Writes `entity_id: 0` into an append-only log, where it
  reads as a claim about work item 0. A record that is false is worse than a record that is
  differently shaped.

## R4 — What this feature does not log, enumerated per Governance

Two gaps, both inherited rather than introduced, both justified here as the constitution's
Principle III exception path requires.

- **The composed prompt text is not written to the log.** `dispatch.speckit_block` already
  documents and justifies this for dispatch: the issue body, the repository's own
  instructions and the delivery block are all absent from the record today, and recording
  up to tens of thousands of characters of one section would privilege it for no defensible
  reason. Recording it *here* would be worse than not recording it, because the log would
  then describe the rehearsal in more detail than the performance.
- **A successful `GET /issues/{n}` is not individually logged.** `GitHubReader._request`
  logs every retry and every failure but not a successful read, which is the aggregate-read
  exception milestone 001's plan already took. The `prompt.preview` record names the
  repository, the issue and the outcome, which is what the reconstruction standard needs:
  from the log alone a reader can say what was asked for, when, and whether it was answered.

Everything else is recorded. The command writes one `prompt.preview` record on **every**
path — success, malformed arguments, repository not onboarded, issue unavailable — so
SC-005 is true by construction rather than by five exit paths each remembering.

## R5 — Exit codes

**Decision.** `0` success; `2` malformed repository slug or non-positive issue number; `3`
repository not onboarded; `1` the issue could not be obtained.

**Rationale.** SC-007 requires the three failures to be distinguishable by status alone, so
they cannot share a code. `contracts/cli.md` (milestone 001) defines `3` as "precondition
not met", and an un-onboarded repository is exactly that: `operations._refuse_onboarding`
already returns `3` for the same shape of refusal.

**Noted divergence, deliberately not resolved.** `operations._no_such_repo` returns `1` for
an un-onboarded repository under `hold`/`unhold`. Those two commands are not touched by this
feature, their message is about holding rather than about onboarding, and Principle V
removes any obligation to unify command behaviour for outside consumers. Unifying them
would be a separate change with its own justification.

## R6 — Which stream carries what

**Decision.** The prompt is the `Result`'s only line. Notes and warnings go to a stream the
CLI passes in (`sys.stderr`), and are also placed in `result.data` so a non-CLI caller loses
nothing. Failure messages stay in `lines`.

**Rationale.** `main` already routes a `Result` to stdout on `EXIT_OK` and to stderr on
anything else. Putting the prompt in `lines` therefore satisfies FR-003 with no new code,
and puts stdout-is-empty-on-failure (FR-014) beyond the reach of a forgotten branch: there
is no path that both fails and says anything on stdout, because the routing is one
expression in `main`. The stream parameter mirrors `operations.onboard`'s `out`, inverted —
`onboard` writes the human screen out early and keeps the machine document in `lines`; this
writes the notes out and keeps the payload in `lines`.

**Alternative considered.** *Print the notes from `cli.py`.* Puts a decision in the argument
parser, which `operations`' module docstring exists to prevent, and makes the notes
unavailable to the HTTP surface that calls these functions.

## R7 — No `--json`

**Decision.** `prompt` is not added to the set of commands that get `--json`.

**Rationale.** Principle I. The payload is a single opaque string, so the machine-readable
mode's entire content would be that same string in quotes — a second rendering to keep
correct, with no caller. Redirection already covers every machine use, and `> file` produces
something more useful than JSON for the diffing case in User Story 3.

`result.data` is still populated. That is not a knob: every operation fills it, the unit
tests read it, and it costs one dict literal.

## R8 — Which work item row is consulted

**Decision.** `db.find_work_item(source="github", source_id=f"{repo_key}#{number}",
dry_run=False)`. A simulated row is not consulted.

**Rationale.** The row contributes exactly two facts: the recorded branch and the worktree
path. Below `live` the version control boundary is simulated, so a dry-run row has no
worktree on disk, and its branch is the one `prompt.branch_name` derives — which is
precisely what the no-row fallback already produces. Consulting it would add a branch that
cannot change the answer.

## R9 — What happens if it is killed halfway

Nothing to recover. The command opens the database read-only in effect, makes one network
read, reads two paths, and prints. It creates no worktree, no branch, no row, no file, and
no session, so there is no partial state for a later run to find and no cleanup to perform.
A re-run is identical.

The one thing a kill can leave behind is a truncated final line in the audit log, which is
the log's existing and already-documented property (`audit.py`, R14) rather than anything
this feature introduces. No lock is taken, so a kill cannot block the daemon.

## R10 — The command name

`prompt` is kept, as the spec assumes. The word's other appearance in this codebase is
`onboard`'s confirmation question, which is not a command name, so nothing collides at the
CLI. `robot-army prompt <owner/repo> <number>` reads as "show me the prompt", which is what
it does.

## R11 — Argument validation

**Decision.** `issue_number` is parsed with argparse's `type=int`, so a non-numeric argument
is argparse's own usage error and exits `2` before any of our code runs. The slug's
`owner/name` shape and the issue number's positivity are checked in the operation.

**Rationale.** Checking the slug with an argparse `type=` callable would make the refusal
argparse's message rather than ours, and would happen before the audit log is open — so the
run would exit unrecorded, which is the one thing R4 promises cannot happen. Argparse's own
`type=int` failure is the single exception, and it is a malformed *invocation* rather than
an action: nothing was read, nothing was reached, and there is nothing to reconstruct.

## R12 — Where the code lives

**Decision.** One new function, `operations.prompt_preview`. `prompt.compose` is untouched.
`dispatch.speckit_block` takes the R3 widening. `cli.py` gains a parser and one table entry.

**Rationale.** No new module: the operation is short, and `operations` is where every verb
already lives — that placement is what lets milestone 002's web surface call it without a
second implementation. A `preview.py` would be a module with one function and one caller.
