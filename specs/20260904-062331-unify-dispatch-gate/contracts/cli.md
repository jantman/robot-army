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
refusing to resume item 7: the item could not be claimed
  item 7 is dispatching; it was claimed by another dispatcher
```

The summary line stays neutral about *why* the claim failed, because the detail beneath it
says which of the two happened. A state a concurrent claimant leaves behind — `dispatching`
or `active` — reads "it was claimed by another dispatcher"; any other non-claimable state
reads "a session cannot be started from that state", because nobody raced for a `done`
item and saying otherwise sends the reader hunting a second process that never existed.

The second line is `HoldReason`'s own detail string, unmodified. The first is this surface's
summary of which reason it was. Nothing is invented for the terminal that the web does not
also say.

### Overridden

Exit `0`, and the output is the ordinary success line:

```
$ robot-army resume 7 --force
resumed item 7 from session 019831f2-...
```

**Every** condition is named in the `dispatch.forced` record, not just the first (FR-023) —
the author who forces past a pause needs to know they also forced past a hold. That record
is written **only when a condition actually applied**, so `--force` on an idle, unpaused,
unheld machine produces no record and overrode nothing.

The terminal says nothing about the override, and the reason is worth stating because two
earlier drafts of this contract got it wrong in opposite directions. The first had the
terminal list the conditions, which only the gate inside `dispatch_item` knows — and
`dispatch_item` returns a `bool`, so carrying the list out would mean a return channel
threaded through two functions for one cosmetic line. The second had it print a fixed
sentence pointing at `dispatch.forced` whenever the flag was given, which was worse: on an
unblocked launch it claimed an override that never happened and sent the reader to a log
record that was never written — exactly the "the interface says something other than what
happened" failure this feature exists to remove. A durable record that is written when and
only when something was overridden is the whole of FR-023, and it needs no help from the
terminal.

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

and `"force"` reports the **flag the author gave** — not a claim that anything was
overridden. Which conditions were actually gone past, if any, is in the `dispatch.forced`
record, for the reason given above.

## What a refusal leaves behind

Nothing. The item's state, `failure_reason`, `blocked_reason`, timestamps and worktree are all
exactly as they were (FR-010, FR-011). Running the same command again after lifting the
condition succeeds on the first attempt, with no `retry`, no `unblock`, and no repair
(FR-012).

## Unchanged

- `robot-army retry` — it moves a `failed` item to `ready` and does not launch anything, so
  the gate does not apply. The queue applies it on the next pass, as it always has.
- Every other verb.
