# Data Model: Standing Delivery Instructions

**There is none.** No table, no column, no migration, no configuration key, no file written into
a worktree, and no new field on any existing record. This is the short version of this document
and the accurate one; the rest exists so a later reader can confirm the absence was decided
rather than overlooked.

## Why nothing is stored

The feature adds a constant to a pure function. `prompt.compose()` reads no state, writes no
state, and returns a string derived entirely from its arguments — so there is nothing to
persist, nothing to migrate, and nothing that can be stale.

The obvious candidate for storage would be "which prompt did item 42 actually get". That is
already stored, and storing it a second time is what this deliberately does not do:
`db.insert_session()` persists `launch_argv` as JSON on the session row, and the composed prompt
is the final element of the worker argv nested inside it. The record already answers the
question.

## The one entity, and it lives in source

| Name | Where | Shape | Lifetime |
|---|---|---|---|
| The standing delivery block | a module constant in `src/robot_army/prompt.py` | `str`, fixed, no placeholders | the process; changes only by editing the source |

Its exact wording and the requirements it satisfies are the subject of
[contracts/delivery-block.md](contracts/delivery-block.md).

## The one structure that changes

The ordered section list `prompt.compose()` builds. Before and after:

| # | Before | After |
|---|---|---|
| 1 | `.claude/robot-army.md` (optional) | `.claude/robot-army.md` (optional) |
| 2 | Spec Kit block (optional) | Spec Kit block (optional) |
| 3 | — | **the delivery block (always)** |
| 4 | the issue section | the issue section |

Order is the model here: it is how the prompt encodes precedence, and it is the only reason the
new block can be described as subordinate to a repository's own instructions (FR-009) without any
code enforcing that. Sections are joined by a blank line with a `---` rule between guidance
sections, unchanged.

## State transitions

None. No state machine is touched: an item's states, a session's states, and a card's states are
all unaffected by what a prompt says.

## What is *not* recorded, and why that is not a Principle III gap

Nothing new happens, so there is nothing new to record. The feature changes the value of a string
that is already written to durable state in full (`sessions.launch_argv`) and already appears
verbatim in the `dispatch.unconfirmed` audit detail when a launch cannot be confirmed. No
Principle III exception is claimed or needed.
