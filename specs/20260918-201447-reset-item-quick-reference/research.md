# Research: reset, and the operating quick reference

**Feature**: [spec.md](spec.md) · **Issue**: jantman/robot-army#179 · **Date**: 2026-09-18

Every question here was settled by reading the code that already does each half of the
operation. Nothing needed a prototype; what needed deciding was the *order* of steps and where
the seams go, because both halves are correct already and the only way to get this wrong is to
compose them badly.

## R1 — Which comes first: the discard, or the read?

**Decision**: guards → **read, refresh, evaluate** → **discard** → transition to `ready`.
The checkout is destroyed only once the item is known to be requeueable.

**Rationale**: the reverse order destroys work for an item that may then be refused. The whole
occasion for a reset is an issue that has changed, and a changed issue is exactly the one that
may now be closed, unlabelled, or — the case `retry` exists to catch — edited by somebody other
than the configured author. Discarding first and refusing second leaves the maintainer with no
checkout *and* no item in the queue, which is strictly worse than the hand-work this feature
replaces.

`retry`'s own ordering note argues the mirror of this and agrees: it refreshes the columns
before consulting the verdict so that an interruption leaves the item `failed` with accurate
content rather than *in the queue* with content nobody re-read. Reset inherits that ordering and
adds the discard after the verdict, not before it.

**Cost accepted**: a reset refused by the live-session guard or by git's dirty-tree refusal will
have spent one GitHub read first, because those guards live inside `worktree_remove` and now run
after the read. `retry` deliberately refuses *before* its read to avoid exactly this. The
alternative — re-asking `cleanup.live_sessions` in `reset` as an early-out — puts a second copy
of a guard's refusal wording in a second place, which is how guards drift; and the cost is one
API call out of five thousand an hour, on a path a person runs by hand. Principle I settles it:
fewer moving parts wins. Recorded here so the trade is visible rather than accidental.

**Alternatives considered**: discard-then-read (rejected: destroys work for an item that may be
refused); splitting `worktree_remove`'s guards out into a shared pre-flight so they could run
before the read (rejected: a large refactor of the one command that has been getting these
refusals right, to save one API call).

## R2 — How the two halves are reused without being reimplemented

**Decision**: extract the middle of `retry` into a helper both verbs call, and call
`worktree_remove` itself for the discard.

FR-003 requires the author check to be the poller's own code. `retry` is the only caller of the
read-refresh-evaluate sequence today, and the sequence is identical for reset; the only
differences are the state gate in front and the discard inserted before the transition. So:

- `_reread_and_refresh(ctx, item, *, verb, trust_file) -> _Reread` carries the local-blocker
  check, the live read, the four-column refresh, and `poll.evaluate`. It returns either a
  refusal `Result` or the verdict, and it takes `verb` so its audit records are named for the
  command that is running — `retry.blocked` / `retry.evaluate`, `reset.blocked` /
  `reset.evaluate`. Two callers, by construction, so it is not speculative generality.
- `retry` becomes: gate on `failed` → helper → transition. Its behaviour is unchanged, which
  the existing `tests/unit/test_operations_retry.py` is there to prove.
- `reset` becomes: gate on the accepted states → helper → confirm → `worktree_remove` → transition.

**The discard calls `worktree_remove` whole**, rather than reaching past it to
`_remove_checkout`. That command holds four guards — an existing cleanup record, an unresolved
repository, open session rows, and git's own refusal — and reaching past it would take the disk
half without them. It also already does the thing FR-008 needs: for an item that is *not*
terminal it clears `worktree_path` so a fresh checkout is built next time, and its comment
already names `retry` as the consumer that motivated it.

**Alternatives considered**: a callback parameter on the shared helper (`before_requeue=`)
(rejected: an inverted control flow to save four lines, in a codebase whose stated default is
obvious top-to-bottom flow); duplicating the read sequence in `reset` (rejected outright — it
would put the author check in two places, which is the defect `retry` was written to close).

## R3 — `worktree_remove`'s exit code is not the question to ask

**Decision**: `reset` branches on `result.data["worktree_removed"]`, not on `result.code`.

**Why this matters**: `_remove_checkout` sets a non-zero exit code in a case where the worktree
*is* gone — when git declined to delete the branch because it is unmerged, which is the ordinary
case for work being thrown away. It reports that as a `WARNING` with a non-zero exit because the
branch half is unfinished. A reset that treated non-zero as "stop" would abort *after* the
checkout was already destroyed and `worktree_path` already cleared, leaving the item neither
reset nor intact — the precise half-done state Principle IV forbids.

So: worktree gone → continue to the requeue, carrying the removal's warning lines into the
reset's own output so the retained branch is still reported. Worktree not gone → stop, return
the removal's refusal unchanged, and leave the item in the state it was in.

## R4 — What reset asks before it destroys anything

**Decision**: one question, always, placed immediately before the destruction; which question
depends on `--force`.

- Without `--force`: `reset` asks its own `[y/N]`, naming the checkout path and the branch.
  Git's refusal still protects uncommitted and untracked work, so what is at risk is committed
  work on a throwaway branch — real loss, worth a question, not worth a typed id.
- With `--force`: `worktree_remove`'s existing typed-item-id prompt is the question, unchanged
  and unweakened. `reset` does not ask a second one; a question asked twice is answered
  reflexively, which is the reasoning already written into `worktree_remove`'s force path.
- `--yes` suppresses `reset`'s own question and nothing else. It exists so the web can reach the
  operation without a terminal prompt while still being unable to override git (FR-019), and it
  follows `onboard --yes`, which already means exactly this. `--yes --force` still faces the
  typed-id prompt: `--force` is `worktree_remove`'s contract and `reset` does not get to weaken
  another command's guard.

Both prompts go through `_answer_or_give_up`, so an abandoned one is a recorded refusal rather
than a traceback, and `reset` wears `@_guards_its_prompt` like every other asking command.

The question is placed after the read rather than before it so that the maintainer is never
asked to consent to a destruction that the evaluation is about to refuse anyway.

## R5 — The web surface

**Decision**: `reset` is an `_inline_item_action`, added to `ITEM_ACTIONS` with
`confirm=True, danger=True`, and run as `operations.reset(ctx, item_id, assume_yes=True)`.

- **Inline, not the slow worker.** `_slow_item_action` exists for `resume` and `restart`, whose
  preparation takes minutes because they dispatch an agent. Reset does one HTTP read, one git
  removal and one transaction; `retry` already does a live GitHub read inline.
- **The confirmation page is free.** `confirm=True` makes the control a link to
  `/item/<id>/confirm/reset`, which renders `ActionSpec.description`, re-validates legality
  against state read *now*, and offers a link back that changes nothing — FR-015, FR-016 and
  FR-017 are all properties of machinery that already exists. The description is the single
  string the CLI's `--help` also prints, as `retry` already does, so the two cannot disagree.
- **`legal_actions` is the single source.** Adding `item_states=(INTERRUPTED, AWAITING_REVIEW,
  FAILED)` to the spec makes the control appear exactly where it is legal and makes
  `require_legal` refuse a direct POST anywhere else, with no second table.
- **No override from the web.** The web passes `assume_yes=True` and never `force=True`, so a
  checkout holding uncommitted work produces `worktree_remove`'s git refusal, which `_report`
  turns into a `Refusal` rendered on the page with its reason. The refusal text already names
  `--force` as the override, which is a terminal flag — FR-019 is satisfied by not passing it.
  This is the one place reset's behaviour differs between surfaces, and it differs in the safe
  direction.
- `needs_daemon=False` (reset does not dispatch; like `retry`), `effect_guarded=True` (it
  touches the disk).

## R6 — State machine

**Decision**: add `(INTERRUPTED, READY)` and `(AWAITING_REVIEW, READY)` to
`WORK_ITEM_TRANSITIONS`. `(FAILED, READY)` is already there.

`transition_work_item` stamps `ready_at` for a `ready` target, so a reset item's `ready_at`
moves. **Queue order does not**: `ordering.order_key` ranks on `discovered_at`, which reset
leaves alone. That is the right answer and not merely the convenient one — `discovered_at` is a
record of when the issue was found, and rewriting it to move an item up or down the queue would
falsify a fact to achieve an ordering effect. The issue's phrase "as if freshly discovered"
describes the *content and checkout*, not the item's place in line.

Nothing else in the codebase assumes `ready` is reachable only from `discovered` and `failed`;
the transition table is consulted, never pattern-matched. `tests/unit/test_states.py` enumerates
the illegal cases and is the test that has to agree.

## R7 — Interruption, step by step

The two persistent effects are the checkout removal (not transactional — it is a directory) and
the database writes (each in its own `db.transaction`). Killed at any point:

| Killed… | State afterwards | What a second `reset` does |
|---|---|---|
| before the read | untouched | everything |
| after the refresh, before the verdict | old state, **fresh** content | re-reads, re-evaluates, proceeds |
| after a refused verdict | old state, fresh content, reasons written | the same refusal, or proceeds if the issue was fixed |
| after the worktree removal, before the branch delete | worktree gone, branch left, path still on record | `worktree_remove` reports the branch, `--force` finishes it |
| after the removal, before the transition | worktree gone, `worktree_path` cleared, old state | finds no worktree, skips the discard, re-reads, requeues |
| after the transition | done | nothing is left to do |

No step leaves the item `ready` with content nobody re-read, and no step leaves a half-written
row: `transition_work_item` writes the state change and its audit record inside one transaction.
The one thing a reset cannot undo is the deleted checkout, which is what it is for.

## R8 — The documentation drift test (FR-024, SC-008)

**Decision**: a new `tests/unit/test_operating_reference.py` that parses the two tables out of
`docs/guide/operating.md` and compares them against the program:

- every `WorkItemState` member appears in the state table, and every state named in that table
  is a real member;
- every subcommand the argument parser defines appears in the command table, and every command
  named there is a real subcommand.

Parsing markdown tables in a test is a small, honest amount of work and it is the only thing
that stops the page rotting — which is the failure this whole half of the issue is about. It
reads the parser's own subcommand list rather than a hand-kept constant, so a new verb fails the
test on the day it is added. `tests/unit/test_docs_links.py` already establishes the precedent
of asserting things about the guide, including the README's line ceiling.

## R9 — What is *not* changing

- **No configuration key.** Reset has no knob; nothing in `config.py`'s `_KNOWN_KEYS` or
  `_REPO_KEYS` moves, so `share/config.example.toml` does not need regenerating and
  `tests/unit/test_example_config_drift.py` will stay green. Confirmed by reading the operation's
  inputs: an item id and two flags.
- **No database migration.** Reset writes only columns that already exist.
- **No new dependency.**
- **`retry`, `restart`, `resume`, `abandon` and `worktree remove` keep their behaviour.** `retry`
  is refactored around an extracted helper and must come out observably identical.
