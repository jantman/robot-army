# Quickstart: validating dispatch holds

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Contracts**: [dispatch-policy](contracts/dispatch-policy.md), [cli](contracts/cli.md), [web](contracts/web.md)

A validation walk, not a tutorial. Each step names the requirement it proves. Run it against a
real installation after `uv run pytest` passes.

## Prerequisites

```bash
cd ~/GIT/robot-army
uv sync
uv run pytest                  # the suite must pass first
uv run robot-army doctor       # as always, before anything else
```

You need at least two onboarded repositories with queued work — the interesting behaviour is
what a hold on one leaves free in the other. `robot-army status` should show several items in
`ready`.

The schema upgrade happens on the first connection. Confirm it landed:

```bash
uv run robot-army status >/dev/null
sqlite3 ~/.local/state/robot-army/state.db 'PRAGMA user_version'   # expect 10
sqlite3 ~/.local/state/robot-army/state.db '.schema item_holds'
sqlite3 ~/.local/state/robot-army/state.db '.schema repo_holds'
```

## 1. Hold one item — US1, FR-001, FR-009, FR-014

Note the id at the head of the queue and the one behind it:

```bash
uv run robot-army status
uv run robot-army hold <head_id>
uv run robot-army status
```

**Expect**: the held item still listed, **in the same position**, with hold reason `held` and a
detail naming when it was held and by what. The item behind it is now the first with no hold.
Nothing was renumbered.

Let a dispatch pass run (or `uv run robot-army poll`). **Expect**: the second item dispatches;
the held one does not.

## 2. Holding twice is a reported no-op — FR-004

```bash
uv run robot-army hold <head_id>; echo "exit=$?"
```

**Expect**: exit `0`, and the message reports the hold **already in force with its original
timestamp** — not a fresh one. Compare against step 1's output; the time must be identical.

## 3. Release it — FR-003, FR-013

```bash
uv run robot-army unhold <head_id>
uv run robot-army status
```

**Expect**: the item is back at the head of the queue, with no hold, in exactly the position it
had before step 1. Releasing restores; it does not reorder.

```bash
uv run robot-army unhold <head_id>; echo "exit=$?"
```

**Expect**: exit `0` and a no-op message. Releasing what is not held is not a failure (FR-005).

## 4. Hold a whole repository — US2, FR-002, FR-011

```bash
uv run robot-army hold --repo <owner/name>
uv run robot-army status
```

**Expect**: **every** item from that repository shows `held`. Items from the *other* repository
show no hold and dispatch normally on the next pass — a repository hold holds that repository's
work, never the queue.

## 5. A new item arrives already held — FR-012

Label a fresh issue in the held repository, then:

```bash
uv run robot-army poll
uv run robot-army status
```

**Expect**: the newly discovered item enters the queue **already held**, with no action from
you. This is the half a per-item hold cannot express, and the reason the issue asked for
repository scope.

## 6. Both holds at once — FR-017

With the repository still held, also hold one of its items:

```bash
uv run robot-army hold <an_item_in_that_repo>
uv run robot-army status
```

**Expect**: **one** reason (`held`), whose detail names **both** holds and says that releasing
one leaves the other in force. Now release only the item hold:

```bash
uv run robot-army unhold <an_item_in_that_repo>
uv run robot-army status
```

**Expect**: still held, now reporting only the repository hold. The surface must never look like
it ignored the release.

## 7. List everything held — US3, FR-018, FR-020

```bash
uv run robot-army holds
```

**Expect**: every item hold and every repository hold, each with its target, when it was placed,
by which surface, and how long ago. Held items show their current state.

Now the case that matters most — a hold with nothing to attach to:

```bash
uv run robot-army hold --repo <a_repo_with_no_queued_work>
uv run robot-army holds
```

**Expect**: it is listed, with a count of zero queued items. A hold that suppresses future work
invisibly is the failure this step exists to catch.

```bash
uv run robot-army unhold --repo <that_repo>
uv run robot-army holds
```

**Expect**: with nothing held at all, a plain *nothing is held* — not an empty table.

## 8. Refusals — FR-006

```bash
uv run robot-army hold 999999;                echo "exit=$?"   # expect 1
uv run robot-army hold --repo owner/typo;     echo "exit=$?"   # expect 1
uv run robot-army hold 5 --repo owner/name;   echo "exit=$?"   # expect 2
uv run robot-army hold;                       echo "exit=$?"   # expect 2
```

**Expect**: each names what was wrong. The last two are argparse usage errors — the target is
stated, never guessed from its shape.

## 9. Holds survive a restart — US4, FR-021, FR-022

With at least one item hold and one repository hold in force:

```bash
uv run robot-army holds > /tmp/holds-before.txt
# stop the daemon, then start it again
uv run robot-army holds > /tmp/holds-after.txt
diff /tmp/holds-before.txt /tmp/holds-after.txt
```

**Expect**: identical, **placement times included**. Nothing from the held set dispatched across
the restart.

Then the stronger case — hold with the daemon **stopped**, start it, and confirm the hold is
honoured on the first pass (FR-022). Nothing from the held set may dispatch before the daemon
notices it.

## 10. The web surface — FR-007, FR-019

```bash
uv run robot-army serve
```

On the queue page:

- each row carries hold or release for that item; one tap, no confirmation page;
- a held item shows the same reason and detail the terminal shows;
- **held repositories appear as their own notice**, including one matching no queued item;
- a hold placed in the browser is in force for the very next `robot-army holds` — no restart, no
  cache. Reverse it: hold from the terminal, reload the page, and the browser agrees.

Reload after acting. **Expect**: no re-post — every action redirects `303` and carries
`include_simulated` forward.

## 11. A hold does not touch a running session — FR-010, SC-010

With a session running for item `N`:

```bash
uv run robot-army hold <N>
uv run robot-army status
```

**Expect**: the session is still running and the item's state is unchanged. A hold governs entry
into dispatch; `cancel` is what stops a session, and the surfaces must not let one be mistaken
for the other.

## 12. The record — FR-023, SC-008

```bash
uv run robot-army log --since 1h | grep -E '"action": *"(web\.)?(un)?hold\.'
```

**Expect**: one record per hold and release performed above — target, whether a hold was already
in force, the resulting time and placer, and for the web actions the `web.` prefix. Everything
this walk did should be reconstructable from these lines alone, without re-running any of it.

## 13. Cleaning up

```bash
uv run robot-army holds          # see what this walk left behind
uv run robot-army unhold ...     # release each
uv run robot-army holds          # expect: nothing is held
```

A left-behind hold silently stops work. The last line of this walk should be *nothing is held*.
