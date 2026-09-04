# Contract: `robot-army-session-wrapper`

**Feature**: `specs/20260904-180332-trust-env-session-id` | **Date**: 2026-09-04

The wrapper is a process boundary: the daemon invokes it, and it invokes the worker. This is
its invocation contract after this change. It is the only external interface this feature
touches.

---

## Invocation

```text
robot-army-session-wrapper <item-id> -- CMD [ARGS...]
```

`--` is optional but conventional; the daemon always passes it. Everything after it is the
command to run, executed unmodified.

## Environment

| Variable | Required | Meaning |
|---|---|---|
| `ROBOT_ARMY_SESSION_ID` | **yes** | The session id. The sole source; arguments are never consulted for it |
| `ROBOT_ARMY_SPOOL_DIR` | no | Where records are written. Defaults under `$XDG_STATE_HOME/robot-army` |
| `ROBOT_ARMY_LOG_DIR` | no | Where the session log is written. Same default root |

**Breaking change.** `ROBOT_ARMY_SESSION_ID` was previously optional, because the wrapper
would recover the id from argv when it was absent. It is now required, and a `--session-id`
argument has no effect on the wrapper whatsoever — it is passed through to the worker like
any other argument and is otherwise ignored. Callers that relied on the argv recovery must
set the variable. The repository has no such callers outside its own tests, which are
updated with this change; per Principle V, no compatibility shim is provided.

## Preconditions, checked in this order

The order is part of the contract, not an implementation detail: each check runs before
anything builds a path from the value it guards, so a refusal creates nothing.

1. An item id is present as the first argument, and matches `^[0-9]+$`.
2. A command is present after the arguments are consumed.
3. `ROBOT_ARMY_SESSION_ID` is set and matches
   `^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`.
4. Only then: the spool and log directories are created, and paths are composed.

## Exit statuses

| Status | Meaning |
|---|---|
| The worker's own status | The worker ran. Propagated unchanged, including 128+N for a signal death |
| `2` | The wrapper refused before running anything: a missing or malformed identifier, a missing command, or a spool directory that cannot be created |

Status `2` is the wrapper's pre-existing status for its own usage errors and is reused
rather than extended. A worker can also exit 2, and that ambiguity is harmless: a refusal
writes **no record**, so the daemon never attributes an exit status to a worker that never
started. The distinguishing evidence is the record's existence, not the number.

## Effects

| Condition | Records | Log file | Worker |
|---|---|---|---|
| All preconditions met | `<session-id>.start.json` and `<session-id>.exit.json` in the spool directory | appended | runs |
| Any precondition fails | none | none | not started |

On refusal the wrapper writes one line to standard error naming which identifier was refused
and why. It writes nothing to the audit log — it has no access to one by design; see the
Constitution Check in `plan.md` for why that gap is acceptable and where the action remains
observable.

## Guarantees

- **Containment.** Every file the wrapper creates is inside `ROBOT_ARMY_SPOOL_DIR` or
  `ROBOT_ARMY_LOG_DIR`. No argument, and no content of any argument, can move a write
  outside them.
- **Parseability.** Every record is valid JSON under a strict reader regardless of what the
  arguments contain, and every string it carries round trips exactly.
- **Atomicity.** Records are written to a `.tmp` sibling and renamed; a reader never
  observes a partial record, and no `.tmp` file is left behind on a clean run.
- **Non-interference.** The wrapper never modifies, reorders, or inspects the command it
  runs. It does not `exec` it, because it must survive to observe the exit status.
- **Format stability.** The record's fields and `schema` value are unchanged by this
  feature.
