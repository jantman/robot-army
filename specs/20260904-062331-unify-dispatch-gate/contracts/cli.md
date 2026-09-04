# Contract: `robot-army resume` and `robot-army restart`

## Synopsis

```
robot-army resume  <item-id> [--force]
robot-army restart <item-id> [--force]
```

Both verbs keep every argument and behaviour they have today. `--force` is new.

## `--force`

```
--force   start the session even though dispatch is paused, the item or its
          repository is held, or the machine is at its session limit. Does not
          bypass the issue author check, workspace trust, the committed settings
          fingerprint, onboarding, or the state machine.
```

Two sentences, and the second is load-bearing: `robot-army cancel --force` already means
"skip the confirmation prompt", so each flag's help text has to say which thing it is.

The flag is off by default and has no configuration equivalent (FR-022). There is no way to
turn the gate off standing.

## Exit codes

| Code | Name | When |
|---|---|---|
| `0` | `EXIT_OK` | the session started |
| `1` | `EXIT_FAILED` | the launch was attempted and failed — the item is `failed` and carries a reason |
| `3` | `EXIT_PRECONDITION` | refused before anything was attempted — the item is untouched |

`3` versus `1` is the whole of FR-014, and the difference the author acts on: `1` means go
read `robot-army show <id>`; `3` means the reason is already on the screen and the item needs
nothing.

Every pre-existing refusal in these two verbs — no such item, wrong state, no previous session
to resume — already exits `3`. The gate's refusals join them.

## Output

### Refused by the gate

Standard **error**, exit `3` — the CLI already puts any outcome that did not succeed on
stderr, and a refusal is one. One line naming the action and the item, one line carrying
the reason exactly as the queue view renders it:

```
$ robot-army resume 7
refusing to resume item 7: dispatch is paused
  dispatch is paused; lift it with `robot-army unpause`
```

```
$ robot-army resume 7
refusing to resume item 7: the machine is at its session limit
  2 of 2 sessions running (2 ours, 0 other)
```

```
$ robot-army restart 7
refusing to restart item 7: held
  held since 2026-09-04 06:12 by web; repository jantman/robot-army is held
  since 2026-09-04 05:40 by cli — releasing one leaves the other in force
```

```
$ robot-army resume 7
refusing to resume item 7: repository jantman/robot-army is at its session limit
  repository jantman/robot-army: 1 of 1 sessions (configured)
```

```
$ robot-army resume 7
refusing to resume item 7: another dispatcher claimed it
  item 7 is dispatching; it was claimed by another dispatcher
```

The second line is `HoldReason`'s own detail string, unmodified. The first is this surface's
summary of which reason it was. Nothing is invented for the terminal that the web does not
also say.

### Overridden

Exit `0`, and the override is announced rather than silent:

```
$ robot-army resume 7 --force
resumed item 7 from session 019831f2-... (--force: the dispatch gate was overridden;
see dispatch.forced in the log for what it went past)
```

**Every** condition is named in the `dispatch.forced` record, not just the first (FR-023) —
the author who forces past a pause needs to know they also forced past a hold.

The terminal line points at that record rather than repeating it, and the reason is worth
stating because the first draft of this contract had it the other way round. Only the gate
inside `dispatch_item` knows which conditions applied, and `dispatch_item` returns a
`bool`; carrying the list back out would mean a return channel or a callback threaded
through two functions for one caller and one cosmetic line. FR-023 asks for a durable
record, which the log has, and Principle I asks that machinery earn its place, which this
would not.

### `--json`

Where the CLI already offers `--json`, a refusal carries the same fields the human output
does:

```json
{
  "item_id": 7,
  "ok": false,
  "refused": true,
  "hold": "global_cap",
  "detail": "2 of 2 sessions running (2 ours, 0 other)"
}
```

and an override carries `"forced": true`; which conditions it went past is in the
`dispatch.forced` record, for the reason given above.

## What a refusal leaves behind

Nothing. The item's state, `failure_reason`, `blocked_reason`, timestamps and worktree are all
exactly as they were (FR-010, FR-011). Running the same command again after lifting the
condition succeeds on the first attempt, with no `retry`, no `unblock`, and no repair
(FR-012).

## Unchanged

- `robot-army retry` — it moves a `failed` item to `ready` and does not launch anything, so
  the gate does not apply. The queue applies it on the next pass, as it always has.
- Every other verb.
