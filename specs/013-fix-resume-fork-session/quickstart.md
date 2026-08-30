# Quickstart: Validating `resume`

How to prove this feature works, by suite and by hand. The launch shapes live in
[contracts/worker-launch-shapes.md](./contracts/worker-launch-shapes.md); the confirmation rules
live in [contracts/confirmation-outcome.md](./contracts/confirmation-outcome.md).

The bar here is higher than usual, and deliberately so: this defect existed because every
automated check passed on a launch the binary refused. **Step 2 is not optional.** A green suite
is necessary and, for this feature specifically, has already been proven insufficient.

## Prerequisites

- The repository checked out on this feature's branch, with `uv` available and `uv sync` run.
- The worker binary on `PATH` for steps 2 and 4. Steps 1 and 3 do not need it.
- For step 4 only: a daemon (`robot-army run`) and an item that has genuinely been interrupted.

## 1. The automated suite

```bash
uv run pytest tests/unit/test_launch_shapes.py -v          # the composed argv, restoring and not
uv run pytest tests/integration/test_dispatch.py -v        # confirmation outcomes, incl. the race
uv run pytest                                              # the whole suite must pass
uv run ruff check .
```

The full run is load-bearing twice over: the constitution's Development Workflow requires it, and
FR-004 says the non-restoring launch must be unchanged — if it moved, the existing dispatch
expectations will say so without anyone having to look.

## 2. The real binary (the step that would have caught this)

```bash
uv run pytest -m requires_worker -v
```

Every shape `build_launch_plan` can compose is handed to the actual worker and must get past
argument validation. To confirm the check has teeth rather than merely passing, break it on
purpose — drop `--fork-session` from the restoring branch and run it again. It must fail, naming
the shape and the binary's own complaint:

```
Error: --session-id can only be used with --continue or --resume if --fork-session is also specified.
```

Then put it back. A check that cannot be made to fail is proving nothing.

Without the binary installed the run reports **skipped**, never passed. Confirm that too — the
skip is a requirement (FR-015), not an accident:

```bash
PATH=/usr/bin:/bin uv run pytest -m requires_worker -v      # expect: skipped, with a reason
```

## 3. The failure path, by hand, against scratch state

```bash
export XDG_STATE_HOME=$(mktemp -d)     # state lives under $XDG_STATE_HOME/robot-army
```

Drive an item into `dispatching` with a worker that exits immediately and records its exit before
the confirmation window closes — the simulated host's `confirm_result` toggle plus an exit record
applied mid-confirmation is the seam the integration tests use, and the calls worth copying are
in `tests/integration/test_dispatch.py` rather than reproduced here.

What must be true afterwards:

```bash
uv run robot-army show <id>            # state: failed — NOT dispatching
uv run robot-army log --since 5m       # the whole story, without re-running anything
```

Specifically:

- the item is `failed`, and its reason names that the worker exited and with what status;
- the session kept `exited_error` — no `state.session` record moving it to `lost`;
- no `IllegalTransition` appears anywhere in the log;
- the item settled at detection time, seconds in, not after the 15-minute reaper.

Then the other half, which must not have regressed: a launch where the worker records nothing at
all still ends `lost` + `failed` with today's "was not confirmed" wording (FR-006).

## 4. End to end, the way it was found

This is quickstart scenario 3 of milestone 002 and T078's phone round — the path that has never
worked. Nothing below is simulated.

1. With the daemon running, take an item that is genuinely `interrupted` and has a previous
   session.
2. From the web interface, on a phone if that is what is at hand, tap **resume** and confirm.
3. The `303` returns immediately — that part was always correct.

What must now happen, and never has:

- a worker window opens and **stays** open, rather than printing `[EOF - dtach terminating]`;
- the item moves `dispatching` → `active` inside the confirmation window;
- `uv run robot-army show <id>` lists a new session with a **new** attempt number, and the record
  names the session it was restored from;
- the new session's id is the one this system chose — no session-identity mismatch anomaly;
- the worker knows what the previous one was doing. Ask it. A resume that starts an empty
  conversation is a failure even if every state transition looks right.

Finally, the failure that started all this, now visible instead of silent: resume an item whose
stored conversation is gone (name a session the worker no longer holds). The item must land in
`failed` within the confirmation window with a reason naming the exit — not sit in `dispatching`
looking like it is still starting up.

## Done when

| Check | Requirement |
|---|---|
| Suite and lint green | Development Workflow |
| Real-binary check passes, and fails when deliberately broken | FR-013 – FR-014, SC-006 |
| Real-binary check skips without the binary | FR-015 |
| Fast-exit launch ends `failed` inside the confirmation window | FR-005, FR-007, SC-002 |
| No launch failure leaves an item in `dispatching` | FR-008, SC-003 |
| Resume reaches `active` with context restored, under our id | FR-001 – FR-003, SC-001 |
| The log alone explains any failed launch | FR-010, SC-004 |
