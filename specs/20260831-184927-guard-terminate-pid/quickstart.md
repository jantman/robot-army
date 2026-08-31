# Quickstart: proving the refusal

**Feature**: `specs/20260831-184927-guard-terminate-pid` | **Date**: 2026-08-31

## Read this first

**Do not stage this feature's failure cases against a live desktop session.** Writing `pid = 1`
into a session row and running `robot-army cancel` is exactly what produced the 2026-08-31
incident: it sends `SIGTERM` to every process the user owns, waits ten seconds, and sends
`SIGKILL`. Staging that scenario by hand is what caused the incident in the first place, and the
`--force` flag removes the only prompt in the way.

Every scenario below is therefore driven through the unit suite against a fixture `/proc` tree and
spied signal primitives, in the style of `tests/unit/test_procinfo.py`. **No scenario in this
feature may deliver a real signal to a real process.** The one scenario that touches a real machine
(Scenario 5) is a positive control that only proves ordinary cancels still work.

## Prerequisites

```bash
cd /home/jantman/GIT/robot-army
uv sync            # or: pip install -e '.[dev]'
```

## Scenario 1 — The catastrophic value is unreachable (P1, SC-001, SC-003)

The load-bearing test. It targets `_signal_group` **directly**, because that is the function the
existing suite has always stubbed out — which is why a `kill(-1)` survived C1–C10 coverage
(research R8).

```bash
uv run pytest tests/unit/test_signal_refusal.py -v
```

**Expected**: for recorded pids `0` and `1`, and for a live pid whose group resolves to `1`,
`_signal_group` raises `BoundaryError` and the `killpg` spy records **zero calls**. The assertion is
on the empty call list, not on the exception: proving the refusal branch is reachable is not the
same as proving the signal is unreachable.

The spy replaces the module reference in dtach's own namespace
(`monkeypatch.setattr("robot_army.boundaries.dtach.os", FakeOs())`), never an attribute on the real
`os` module — patching `os.killpg` globally would leak into every other test in the session.

## Scenario 2 — `terminate` refuses before any rung (P1/P2, S-C1..S-C8)

```bash
uv run pytest tests/unit/test_terminate_confirmation.py -v -k refus
```

**Expected**, one case per row of `contracts/signal-refusal.md`:

- `pid=1` with no recorded start time → `confirmed=False`, `method="refused"`, `refused_reason`
  names pid 1, `systemctl` never invoked, `_signal_group` never entered.
- `pid=1` with a *matching* start time → same. (This is the case an identity check alone would let
  through — `/proc/1` has a real start time, measured as `17`.)
- `pid=0`, not simulated → same, naming pid 0.
- live pid with `proc_start` absent → same, naming the missing start time.
- live pid with `proc_start` **mismatching** → **unchanged**: `already_gone`, `confirmed=True`.
  A refusal and a recycled pid are different facts and must not collapse into one.
- ordinary live pid with matching start time → **unchanged**: the full ladder, exactly as today.

## Scenario 3 — A refusal settles nothing and says so (P3, SC-002, SC-004, SC-006)

```bash
uv run pytest tests/unit/test_cancel.py -v -k refus
```

**Expected**:

- `cancel` exits non-zero; the work item is still `ACTIVE` and the session row still `RUNNING`.
- The message names the session id, the rejected field and its value, and points at the session
  row — and is **not** the unconfirmed-stop wording, which would claim a signal was sent (S-K3).
- The `session.terminate` outcome record carries `refused: true`, the reason, and
  `signals_sent: 0`, and there is no `state.session` or `state.work_item` record beside it.
- The refusal returns immediately — no ten-second escalation wait (SC-002).

## Scenario 4 — A simulated session survives a go-live (P3, FR-011..013, SC-007)

The route that reaches this bug through ordinary operation, with no hand-edited database:

```bash
uv run pytest tests/unit/test_effects.py tests/unit/test_cancel.py -v -k simulated
```

**Expected**: a session record marked `dry_run` is terminated by the simulated host even when the
configured effect level would select `DtachHost`; the outcome is `confirmed=True`,
`method="simulated"`; the real termination path is never entered; and
`Boundaries.describe()` names `simulated_session_host` so the startup record still accounts for
every wired implementation.

Also assert the wiring identity from research R7: at a simulated effect level,
`boundaries.session_host is boundaries.simulated_session_host` — one object, two names.

## Scenario 5 — Ordinary cancels are untouched (SC-005) — *the only live check*

Safe to run on the real machine: it cancels a session this orchestrator actually started, which is
the operation that has always worked.

```bash
robot-army status                     # find an item with a running session
robot-army cancel <item-id>           # answer the prompt; do not use --force here
robot-army show <item-id>
```

**Expected**: unchanged from today — the session stops, the item becomes `interrupted`, the
worktree is untouched, and the `session.terminate` record shows the same rungs and the same
`confirmed: true` it showed before this feature. `refused` is absent from the record.

Then confirm the guard did not fire on a legitimate row:

```bash
robot-army log --item <item-id> --limit 20
```

## Full suite and lint

```bash
uv run pytest
uv run ruff check src tests
```

**Expected**: all green. The constitution's Development Workflow makes a passing suite the
completion gate, and this feature is a safety guard — a red suite here is not a partial success.
