# Contract: Confirmation Outcome and Item Settlement

**Feature**: [../spec.md](../spec.md) | **Requirements**: FR-005 – FR-010

Governs what `dispatch_item` does when the confirmation window elapses. Applies to every launch
— fresh, restart, or resume — because the race it resolves is not specific to any of them.

## Input

- The confirmation result from the host boundary: a registry entry, or `None` if the window
  elapsed.
- **The session row's current state, read at that moment.** Another process (the daemon draining
  the spool) may have written it since the launch began. Reading it early defeats the purpose.

## Outcomes

Confirmation succeeded → unchanged in every respect.

Confirmation elapsed:

| Session state now | Session | Work item | Reason recorded |
|---|---|---|---|
| `starting` / `running` | → `lost` | → `failed` | "launch was not confirmed within Ns…" (today's wording) |
| `exited_error` | untouched | → `failed` | names that the worker exited and with what status |
| `exited_clean` | untouched | left to the ordinary end-of-session rules | — |
| `lost` | untouched | → `failed` | the reason already recorded |

## Guarantees

- **C1** — A session that has recorded a terminal state is never transitioned again. The recorded
  outcome is the answer (FR-005).
- **C2** — When `dispatch_item` returns, by any path including an exception, the work item is not
  in `dispatching` (FR-008).
- **C3** — The `dispatching` age reaper resolves only launches that genuinely hang, never a
  failure already detected (FR-009).
- **C4** — The session-identity mismatch probe runs only when nothing was recorded. With a
  recorded exit the question is already answered, and probing would search for a rival session
  that cannot exist.
- **C5** — Nothing is swallowed. An unexpected exception is recorded with its detail, the item is
  settled, and the exception is re-raised so the caller still sees it (Principle III).

## Audit records

| Record | When |
|---|---|
| `dispatch.unconfirmed` | Confirmation elapsed. Detail carries the session state at that moment and which outcome was taken. |
| `state.session` | Only when a transition actually occurs — absent, correctly, in the already-terminal case. |
| `state.work_item` | The item's move to `failed`, with the reason. |
| `dispatch.error` | An unexpected exception escaped the launch: the exception detail, before re-raising. |

## Interruption

Killed before the item is settled, it stays in `dispatching` and the existing age reaper resolves
it — the pre-existing backstop, unchanged. Killed after, the state is committed. Each transition
is already inside `db.transaction` with its audit record, so there is no partial outcome.

## Residual, accepted

If the exit record has not yet drained when confirmation elapses, `lost` stands and the later
record is a no-op (`spool._already_applied` treats a terminal session as already applied). The
item still fails; only the reason is the weaker one. See [research.md](../research.md) R4.
