# Quickstart: proving a stop is confirmed, not assumed

**Feature**: `specs/014-confirm-session-termination` | **Date**: 2026-08-30

Five scenarios. The first three need nothing but the test suite; the last two need a real
session and are the ones that actually settle the issue, because the defect was invisible to
every existing test.

Prerequisites: `uv sync`, and for scenarios 4 and 5 a machine with kitty, dtach, a user systemd
session, and a repository already onboarded.

---

## Scenario 1 — The unit suite

**Validates**: FR-001 – FR-011, FR-013, FR-014, FR-016; contract cases C1–C10, K1–K5.

```bash
uv run pytest tests/unit/test_terminate_confirmation.py tests/unit/test_cancel.py -v
uv run pytest        # the whole suite must pass (Development Workflow)
uv run ruff check .
```

**Expected**: every case in
[contracts/termination-outcome.md](./contracts/termination-outcome.md) is named by a test. C2 —
the scope stop returns 0 while the pid is still present — is the regression test for this
issue; delete the confirmation step and it must fail.

## Scenario 2 — The regression is real

**Validates**: FR-016.

```bash
# Temporarily restore the defect, then watch the suite catch it.
uv run pytest tests/unit/test_terminate_confirmation.py -k escalat
```

Re-introduce `if result.ok: return` in `DtachHost.terminate` and re-run.

**Expected**: the escalation test fails, naming the surviving pid. Restore the code; it passes.
A check that cannot fail is not a check.

## Scenario 3 — Simulated levels do not diverge

**Validates**: FR-014.

```bash
uv run pytest tests/integration/test_effect_levels.py -v
uv run robot-army --effect-level local cancel <id> --force
```

**Expected**: a simulated cancel reports a confirmed stop and settles the item, exactly as a
successful real one does. It must not take the failure branch, and it must not read `/proc`.

## Scenario 4 — The issue's own reproduction, against a real session

**Validates**: SC-001, SC-003; 001 quickstart scenario 5's cancel row.

Dispatch a real session at `no-remote` or `live`, then engineer the exact conditions from the
issue — a recorded scope that systemd already considers inactive while the worker survives:

```bash
uv run robot-army show <id>                       # note the pid and the scope
systemctl --user is-active <scope>                # inactive, while the pid still runs
ps -o pid,etime,comm -p <pid>                     # alive

uv run robot-army cancel <id> --force
echo "exit: $?"
ps -o pid,etime,comm -p <pid>                     # must print no such process
uv run robot-army show <id>
```

**Expected**:

- The command says the scope reported success but the session was still running, and that it
  was stopped by signalling the process group, and that it is confirmed gone.
- Exit status 0.
- The pid no longer exists.
- `show` reports the item `interrupted` and the session no longer `running`.
- `robot-army log` (or the audit file) contains one `session.terminate` record naming both
  rungs, the `alive_after` observation that contradicted the first, and `confirmed: true`.

The pre-fix behaviour for the same steps is the issue verbatim: success reported, pid still
alive minutes later.

## Scenario 5 — A stop that cannot succeed says so

**Validates**: FR-006, FR-007, FR-010, FR-012, SC-004.

Make the session unkillable by the paths available — for example, cancel a session whose
recorded pid belongs to a process this user cannot signal, or run the cancel with the recorded
scope removed and the pid held by a stopped (`SIGSTOP`-ed) process that ignores termination
within the bound.

```bash
uv run robot-army cancel <id> --force
echo "exit: $?"
uv run robot-army show <id>
```

**Expected**:

- Non-zero exit.
- The output does **not** claim the session was stopped; it names the surviving pid and prints
  the `dtach -a <socket>` command to reach it.
- `show` reports the item in **the same state it was in before** — still `active`, still
  visited by reconciliation's session sweep.
- The same cancel from the web interface renders as a failure, not as "cancelled".

Then release the process (`kill -CONT`, or restore permissions) and cancel again.

**Expected**: the second cancel confirms and settles, and a third reports that there was
nothing left to stop rather than claiming a fresh stop.

---

## What "done" looks like

| Question | Where it is answered |
|---|---|
| Does a reported stop mean the worker is gone? | Scenario 4 |
| Does a stop that fails say so, and change nothing? | Scenario 5 |
| Can the defect ship again unnoticed? | Scenario 2 |
| Can the log alone reconstruct what happened? | Scenario 4's `session.terminate` record |
| Do simulated levels still behave? | Scenario 3 |
