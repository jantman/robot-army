# Contract: the web reset control

**Feature**: [../spec.md](../spec.md) · **Date**: 2026-09-18

## Routes

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/item/<id>/confirm/reset` | the confirmation page (existing generic view) |
| `POST` | `/item/<id>/reset` | perform it |

Both are the generic machinery every other mutating item action already uses. Nothing new is
written for confirmation, legality re-checking, same-origin protection, or the redirect back.

## The action specification

Added to `ITEM_ACTIONS`, which is the single source both for which controls are *offered*
(`legal_actions`) and for which are *accepted* (`require_legal`):

```text
name           reset
label          reset
confirm        True      → the control is a link to the confirmation page, not a form
danger         True      → rendered as a destructive action
needs_daemon   False     → reset does not dispatch
effect_guarded True      → it touches the disk
item_states    interrupted, awaiting_review, failed
```

No `session_states` constraint: the open-session refusal is `worktree_remove`'s, asked of the
session rows at the moment of the POST rather than of the state the page was rendered from.

## The description

One string, shown on the confirmation page and printed by `robot-army reset --help`, as `retry`
already does with its own. It must say all four things FR-015 requires:

> Throw the work away and start again. The checkout and its branch are deleted — anything
> committed there is lost, and uncommitted work is refused unless removed from a terminal with
> `--force`. The issue is then re-read from GitHub and its eligibility re-checked, author
> included, and the item goes back in the queue to be worked from scratch.

## Behaviour

- **Offered** exactly when the item's state is one of the three. Absent otherwise, and a direct
  POST is refused `409` naming the state and what is currently legal.
- **Re-checked** at submission against state read then, not state read when the page rendered.
- **A way back**: the confirmation page's existing link to the item, which changes nothing.
- **Run inline** in the request thread as `operations.reset(ctx, item_id, assume_yes=True)` —
  one read, one removal, one transaction. Not the slow worker, which exists for the two actions
  that dispatch an agent.
- **`force` is never passed.** A checkout with uncommitted work therefore produces git's
  refusal, which is reported on the page with its reason and its remedy — a terminal command.
  This is the one behavioural difference between the two surfaces, and it is in the safe
  direction (FR-019).
- **Refusals are reported, not hidden.** Every non-zero `Result` becomes a `Refusal` through
  the existing `_report`, so the page states what was refused and why and nothing is destroyed.
