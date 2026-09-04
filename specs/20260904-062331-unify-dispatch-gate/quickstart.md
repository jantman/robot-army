# Quickstart: verifying the dispatch gate by hand

**Status**: the statically checkable claims below — command spellings, flag names, exit
codes, and the exact wording of every message — were verified against the implementation.
The live end-to-end walk against a running daemon was **not** performed as part of the
implementing session, because it needs a real terminal host, a real worker binary and a
real GitHub repository. Step 8 in particular is checked by the automated tests rather than
by hand, and says why.

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

**Expect** exit `3`, and on **stderr** (the CLI puts any outcome that did not succeed
there):

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

**Expect** exit `0`, the session starts, and a line pointing at the record:

```
resumed item 7 from session 019831f2-... (--force: the dispatch gate was overridden;
see dispatch.forced in the log for what it went past)
```

**Expect in the log** one `dispatch.forced` record listing **all** the conditions — not
just the first. The terminal points at that record rather than repeating it, because only
the gate inside `dispatch_item` knows which applied and `dispatch_item` returns a `bool`;
see `contracts/cli.md` for why a return channel was not worth building for one line.

Then confirm what `--force` cannot reach (FR-024, FR-025): point the item at a repository whose
onboarding record you have removed, or one whose recorded author does not match, and
`--force` must still refuse. The override covers your policy, never the checks about who may
run code in your checkout.

While you are here, look at the log volume from steps 1–6: every refusal is a separate record,
deliberately, because each one is a button you pressed. This is the Principle III gap the plan
enumerates — refusals are not de-duplicated the way the queue's own holds are.

## 8. Exactly one dispatcher wins (FR-016, FR-017, SC-005)

This one is genuinely hard to see by hand, and it is worth being clear about why rather
than writing a recipe that proves something else.

`resume` and `restart` each check the item's state *before* they reach the launch, so a
second terminal command run while item 7 is `dispatching` is refused by that pre-check —
exit `3`, but saying `restart requires a rested item`, not `another dispatcher claimed
it`. That pre-check is why the sequential double-tap was already safe, and why the
cross-process race was not: two processes that both pass it then both reach the claim.

So the automated tests are the real check here, and they drive it two ways:

```bash
uv run pytest tests/unit/test_claim_work_item.py            # 8 threads, one winner
uv run pytest tests/integration/test_dispatch_capacity.py \
  -k two_concurrent_launches                                # 50 repetitions, one session
```

What you *can* confirm by hand is the state a loser finds:

```bash
uv run robot-army show 7 --json | jq .state    # dispatching, while the winner starts up
```

and that a second launch against it is refused rather than settling it — the winner's item
must come out of the loser's attempt exactly as it went in.

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
