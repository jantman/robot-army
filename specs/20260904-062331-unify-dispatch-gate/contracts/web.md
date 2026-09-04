# Contract: the web interface

## Routes

No route is added, removed or renamed. Two gain a guard:

| Route | Change |
|---|---|
| `POST /item/<id>/resume` | new guard `require_dispatchable` |
| `POST /item/<id>/restart` | new guard `require_dispatchable` |

Everything else is untouched.

## `require_dispatchable`

```python
def require_dispatchable(ctx: Context, item_id: int, action: str) -> None
```

Joins the existing guard family — `require_daemon`, `require_effect_agreement`,
`require_legal` — and is called from `_slow_item_action`'s body, after them:

```
require_daemon(ctx, action)
require_effect_agreement(ctx, action)
require_legal(ctx, item_id, action)
require_dispatchable(ctx, item_id, action)      # new
app.submit(action, item_id)
```

Last, because it is the most expensive of the four (it takes a capacity snapshot) and there is
no reason to observe the machine for a request that a cheaper guard will refuse anyway.

**Raises** `Refusal(reason, status=409, code=EXIT_PRECONDITION)` when the gate refuses.
`409 Conflict` matches `require_effect_agreement`, and says what is true: the request is
well-formed and would be valid in a different state of the system.

Never passes `force`. The web has no override (FR-026).

## Why the check happens twice

`_slow_item_action` answers `303` immediately and does the slow work on a single background
worker, because preparing a worktree can take minutes and no phone holds a request that long.
A refusal discovered on the worker therefore reaches the author only through the audit log,
while the page shows an item that simply did not change — indistinguishable from nothing
having happened.

So the gate runs in the request thread, where its refusal becomes a response the author reads,
and again inside `dispatch_item` on the worker, where it is authoritative because minutes can
have passed. The second check is the one that decides; the first is the one that explains.

The server's own docstring already states the discipline this follows: the pre-check inside
`operations.resume` is advisory and the check inside `dispatch_item` is authoritative. This
adds a second advisory check of the same kind, for the same reason.

## What the author sees

The refusal renders through the existing `Refusal` path — the same one `require_daemon` uses —
so the message appears on the page the author is already looking at:

> **Cannot resume item 7.** dispatch is paused; lift it with `robot-army unpause`

> **Cannot resume item 7.** 2 of 2 sessions running (2 ours, 0 other)

> **Cannot restart item 7.** held since 2026-09-04 06:12 by web; repository
> jantman/robot-army is held since 2026-09-04 05:40 by cli — releasing one leaves the other in
> force

The detail is `HoldReason`'s own string — the same sentence `/queue` renders for the same
condition, and the same one the terminal prints (FR-008).

## The escape hatch

The web offers no `--force`. Its answer to a refusal is to lift the condition, and every
control for that already exists:

| Refusal | Control | Route |
|---|---|---|
| dispatch is paused | **Unpause** | `POST /dispatch/unpause` |
| the item is held | **Release hold** | `POST /item/<id>/unhold` |
| the repository is held | **Release repository hold** | `POST /repos/unhold` |
| the machine is at its limit | wait, or **Cancel** a running session | `POST /item/<id>/cancel` |

Lifting the condition is the truer action: it leaves the system in a state that matches what
the author decided, and the queue then agrees with the button. Only the last row has no
one-press answer, and that is the case where being at a keyboard is an advantage anyway.

## Audit

No new record is needed. `_perform` writes the `web.<action>` intent record *before* the
guards run and closes it with `outcome="error"` when a `Refusal` is raised, so a refused
`POST` already leaves the pair that says one arrived and what happened to it.

The worker's authoritative check, when it refuses, writes `dispatch.refused` like any other
launch path, and the existing `web.<action>.result` record carries the exit code and message.

## Unchanged

- The single worker queue, its lifetime, and its lack of supervision.
- The `303` and the redirect target.
- The confirmation pages.
- Every other route, guard and view.
