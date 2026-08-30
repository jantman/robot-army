# Data Model: A Stop That Is Confirmed, Not Assumed

**Feature**: `specs/014-confirm-session-termination` | **Date**: 2026-08-30

**No schema migration.** Every column this feature reads already exists. The only new
structure is an in-memory return value.

---

## Existing entities read (unchanged)

### `sessions` row (`migrations.py:60-78`, `models.py:Session`)

| Column | Role in this feature |
|---|---|
| `session_id` | Identity in the report and in every audit record |
| `state` | Re-read immediately before settling (R5); `STARTING`/`RUNNING` are cancellable |
| `pid` | **The thing confirmed against.** The worker process recorded at confirmation |
| `proc_start` | **The PID-reuse guard.** Paired with `pid` in every liveness check (FR-004) |
| `scope` | The opaque systemd handle the first stop rung uses (M0 F18 — never recomputed) |
| `host_socket` | Builds the `HostHandle`; supplies the attach command in a failure message |
| `dry_run` | Distinguishes a simulated session, whose termination is confirmed by construction |
| `ended_at` | Stamped by the `LOST` transition (`states.py:_SESSION_STAMP_COLUMN`) |

A session with `pid IS NULL` cannot be confirmed against anything. That is FR-005's case: the
outcome is `confirmed=False`, reported as unconfirmed, not as success.

### `work_items` row

Only `state` matters here, and only as a precondition and a re-read. `ACTIVE → INTERRUPTED`
happens **after** confirmation, never before (FR-007).

---

## New in-memory entity

### `TerminationOutcome` (`boundaries/__init__.py`, frozen dataclass)

What `SessionHost.terminate` returns in place of `None`.

| Field | Type | Meaning |
|---|---|---|
| `confirmed` | `bool` | The tracked process was **observed** gone. The only field that may drive a state change |
| `method` | `str` | Which rung settled it: `systemd_scope`, `process_group_signal`, `already_gone`, `simulated`, or `none` |
| `escalated` | `bool` | The first rung reported success and the process was still alive afterwards — the issue's exact shape |
| `detail` | `dict[str, Any]` | What each rung returned, what was observed, how long confirmation took. Flows into the audit record verbatim |

Invariants:

- `confirmed is False` ⇒ the caller changes **no** state and reports failure (FR-006).
- `escalated is True` ⇒ `detail` records both the first rung's reported success and the
  observation that contradicted it (FR-002, FR-011).
- `method == "none"` only accompanies `confirmed=False`, and only when there was nothing to
  try or nothing to confirm against.

---

## State transitions

### On a confirmed cancel

```
session:    STARTING | RUNNING  ──►  LOST
            reason: "stopped by cancel (<method>); process confirmed gone"

work item:  ACTIVE              ──►  INTERRUPTED
            reason: "cancelled by the maintainer (session <id>)"   [unchanged wording]
```

Both inside one `db.transaction`, as `cancel` already does.

### On an unconfirmed cancel

```
session:    unchanged
work item:  unchanged
```

This is load-bearing, not conservative housekeeping. An item left `ACTIVE` is still visited by
reconciliation's session sweep, which only walks `ACTIVE` items (`reconcile.py:140`); an item
moved to `INTERRUPTED` while its worker lives is invisible to every sweep the system has —
which is how the issue's session ran unsupervised.

### On a cancel that races the exit spool (R5)

If the re-read finds the session already in `TERMINAL_SESSION_STATES`, or the item already out
of `ACTIVE`, no transition is attempted. The cancel still reports success — the session is
gone, which is what was asked for — and the record says the session had already recorded its
own ending.

---

## What is written to the durable record (FR-011)

One `session.terminate` action record per cancel, carrying `detail`:

```
scope, pid, proc_start,
rungs: [ {method, exit|signal, ok, alive_after, waited_s}, ... ],
escalated, confirmed, outcome
```

plus, unchanged, the `systemctl.stop` subprocess record `subproc.run` already writes, and the
`state.session` / `state.work_item` transition records the gate already writes. Read together
these answer what was attempted, what each rung returned, whether the process was observed
gone, how long confirmation took, and what the maintainer was told — with no re-running, which
is the reconstruction standard of Principle III.
