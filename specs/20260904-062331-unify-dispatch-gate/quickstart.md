# Quickstart: verifying the dispatch gate by hand

Eight checks. Each maps to requirements in [spec.md](spec.md) and can be run on its own. The
automated equivalents live in `tests/unit/test_launch_gate.py`,
`tests/unit/test_claim_work_item.py` and `tests/integration/test_dispatch_capacity.py`; this
guide is for seeing it with your own eyes on a real machine.

## Prerequisites

```bash
uv sync
uv run pytest            # everything green before you start
uv run ruff check
```

You need the daemon running (the web refuses resume and restart without it) and at least one
item in `awaiting_review` or `interrupted`:

```bash
uv run robot-army run &          # or the systemd unit
uv run robot-army status         # find an item id; call it 7 below
```

Watch the log in a second terminal throughout:

```bash
uv run robot-army log --follow
```

---

## 1. The pause stops resume (FR-005, FR-014, SC-003)

```bash
uv run robot-army pause
uv run robot-army resume 7 ; echo "exit=$?"
```

**Expect** exit `3`, and:

```
refusing to resume item 7: dispatch is paused
  dispatch is paused; lift it with `robot-army unpause`
```

**Expect in the log** one `dispatch.refused` record with `outcome=error`, the item id, the
reason `paused`, and the surface.

**Expect not to see** any `state.work_item` record — the item did not move.

```bash
uv run robot-army unpause
uv run robot-army resume 7 ; echo "exit=$?"     # exit=0, it starts
```

That second command is FR-012 and SC-004: no repair step, first attempt.

## 2. A hold stops resume and restart, and names both when both apply (FR-006, SC-003)

```bash
uv run robot-army hold 7
uv run robot-army resume 7 ; echo "exit=$?"     # exit=3, reason: held
uv run robot-army hold --repo jantman/robot-army
uv run robot-army restart 7 ; echo "exit=$?"    # exit=3, names BOTH holds
```

The third refusal must mention the item hold *and* the repository hold, and must say that
releasing one leaves the other in force. Collapsing to one is the failure FR-006 exists to
prevent — you release the item hold, expect it to run, and it does not.

```bash
uv run robot-army unhold 7
uv run robot-army unhold --repo jantman/robot-army
```

## 3. The pause outranks the limit (FR-007, US2 AS5)

Fill the machine (see step 4), then pause it, then resume. The refusal must say **paused**, not
**global_cap** — freeing a slot would change nothing, and naming the cap sends you to fix the
wrong thing.

This is the check that proves the precedence is the queue's own. Compare against
`uv run robot-army status`, which must name the same reason for the same item.

## 4. The session limit stops every launch path (FR-002, SC-001)

Set the limit to what is already running:

```toml
# in your config
[daemon]
max_concurrent_sessions = 2
```

With two sessions live:

```bash
uv run robot-army resume 7  ; echo "exit=$?"    # exit=3
uv run robot-army restart 7 ; echo "exit=$?"    # exit=3
```

Then both buttons in the web interface, on `http://<host>:<port>/item/7`. **Expect** the
refusal on the page you are looking at, immediately — not a redirect that appears to succeed
followed by nothing happening (FR-015). That immediacy is the whole point of the request-thread
guard; if you see a cheerful `303` and an unchanged item, the guard is not wired in.

Four attempts, four refusals, zero new sessions. That is SC-001.

Cancel one session and resume again: it starts.

## 5. A repository limit binds on its own (FR-003, SC-002)

```toml
[repos."jantman/robot-army"]
max_concurrent_sessions = 1
```

With one session running in that repository and the machine otherwise idle, resuming a second
item **in that repository** is refused naming the repository and its two numbers, while
resuming an item in a *different* repository succeeds in the same conditions. One repository's
limit must not stall the others.

## 6. A refusal is not a failure (FR-010, FR-011, SC-004)

Before and after any refusal above:

```bash
uv run robot-army show 7 --json | jq '{state, failure_reason, blocked_reason, worktree_path}'
```

**Expect identical output.** If a refusal wrote a `failure_reason`, the item would need
`retry` before it could run — turning "the machine is busy" into "your work item is broken",
which is worse than the bug being fixed.

## 7. The override works, and says what it went past (FR-021, FR-023, SC-008)

With the machine at its limit, the system paused, and the item held all at once:

```bash
uv run robot-army resume 7 --force ; echo "exit=$?"
```

**Expect** exit `0`, the session starts, and:

```
overriding 3 conditions on item 7: paused, held, global_cap
```

**Expect in the log** one `dispatch.forced` record listing all three — not just the first.

Then confirm what `--force` cannot reach (FR-024, FR-025): point the item at a repository whose
onboarding record you have removed, or one whose recorded author does not match, and
`--force` must still refuse. The override covers your policy, never the checks about who may
run code in your checkout.

While you are here, look at the log volume from steps 1–6: every refusal is a separate record,
deliberately, because each one is a button you pressed. This is the Principle III gap the plan
enumerates — refusals are not de-duplicated the way the queue's own holds are.

## 8. Exactly one dispatcher wins (FR-016, FR-017, SC-005)

The honest version of this needs two processes racing, which is what
`tests/integration/test_dispatch_capacity.py` does 50 times. By hand you can see the
deterministic half:

```bash
# terminal 1 — start a long resume
uv run robot-army resume 7 &
# terminal 2 — immediately, while item 7 is still `dispatching`
uv run robot-army restart 7 ; echo "exit=$?"
```

**Expect** exit `3` from the second, with `another dispatcher claimed it`, and **expect** the
first to complete normally. One worktree, one branch, one agent.

Check afterwards that the loser changed nothing:

```bash
uv run robot-army show 7 --json | jq .state    # whatever the winner made it
```

## Regression: the automatic dispatcher is unchanged (SC-006)

The gate must not alter what the daemon dispatches or in what order.

```bash
uv run pytest tests/integration/test_dispatch.py tests/integration/test_dispatch_capacity.py
uv run pytest tests/unit/test_holds_ordering.py tests/unit/test_states.py
```

`ordering.plan`'s output must be identical for every input, since `launch_holds` is the code it
already ran, moved. And `tests/unit/test_states.py` must still show reconciliation and spool
replay re-asserting a held state without error — that no-op is FR-020, and it is protected by
`transition_work_item` not being edited at all.

## Full gate

```bash
uv run pytest        # SC-009: the whole suite, green
uv run ruff check
```
