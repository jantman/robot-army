# Phase 1 Data Model: Refuse to Signal an Unverified PID

**Feature**: `specs/20260831-184927-guard-terminate-pid` | **Date**: 2026-08-31

## Schema changes

**None.** No migration, no new column, no new table.

Every field this feature reasons about already exists on `sessions` (`migrations.py:60-78`) and is
already populated by ordinary operation:

| Column | Type | Written where | Role here |
|---|---|---|---|
| `pid` | `INTEGER` (nullable) | `dispatch.py:925`, at confirmation | The value that must be validated before anything is signalled. |
| `proc_start` | `TEXT` (nullable) | `dispatch.py:926`, same transaction | The identity that makes a pid **signallable**. Absent ⇒ the signal rung is refused; the scope rung still runs. Genuinely nullable: the registration treats it as optional. |
| `scope` | `TEXT` (nullable) | `dispatch.py:927` | Unchanged. The scope rung is #67's territory. |
| `dry_run` | `INTEGER NOT NULL` | `db.insert_session`, at row creation | One of **three** fields that together select the session host at termination time (FR-011). Alone it is not sufficient: it means "not created at `live`", and `no-remote` rows are dry-run records with real processes. |
| `state` | `TEXT NOT NULL` | `states.transition_session` | Unchanged by a refusal (FR-005). |

The feature is a set of guards over data the system already records. That is the point: the
incident happened because the code held no opinion about values it already had in hand.

## In-memory model changes

### `TerminationOutcome` (`boundaries/__init__.py`) — one new field

```
confirmed: bool
method: str
escalated: bool = False
refused_reason: str | None = None      # NEW
detail: dict[str, Any] = field(default_factory=dict)
```

**Validation rules** (enforced by the contract, asserted by tests):

- `refused_reason is not None` ⟺ `method == "refused"`.
- `method == "refused"` ⇒ `confirmed is False`. A refusal can never authorise a state change.
- `method == "refused"` ⇒ `escalated is False`. Nothing was attempted, so nothing over-reported.
- `method == "refused"` ⇒ zero signals delivered and zero rungs attempted.

`refused_reason` is a short, human-readable sentence naming the field and value — it is what the
maintainer reads and what the record carries. It is not an enum: there are three producers, they
are all in one function, and a code would have to be translated back into that sentence at both
ends (Principle I).

### `Boundaries` (`effects.py`) — one new field

```
session_host: SessionHost               # unchanged: chosen by effect level
simulated_session_host: SessionHost     # NEW: always a SimulatedSessionHost
```

Wired so that at a simulated effect level the two names refer to the **same object**, because
`SimulatedSessionHost` holds an `_alive` set and two instances would answer `is_alive` differently
(research R7):

```
simulated_host = SimulatedSessionHost(audit)
host = DtachHost(audit) if is_real("session_host", level) else simulated_host
```

`Boundaries.describe()` gains the field, so the startup record continues to name every wired
implementation (Principle III).

### `HostHandle` — unchanged

`simulated: bool` already exists and is already set by `SimulatedSessionHost.spawn`. `cancel` will
set it from the session record when it builds the handle, so the handle no longer lies about what
it describes. Nothing branches on it — the routing decision is made from the session record, not
from the handle (FR-012) — but a handle that says `simulated=False` for a simulated session is a
falsehood in the record, and this is a one-word fix.

## State transitions

**None added, none changed.**

A refusal performs no transition at all. The work item stays in whatever state it held — `ACTIVE`
in every case that matters — which keeps it in front of reconciliation's active sweep rather than
parking it somewhere nothing revisits (FR-005, SC-006).

The existing `SessionState.RUNNING → LOST` and `WorkItemState.ACTIVE → INTERRUPTED` pair still
happens only on `outcome.confirmed`, guarded by 014's K1. This feature adds one more way for
`confirmed` to be `False`; it does not touch the settle path.

## Entities from the spec, mapped

| Spec entity | Implementation |
|---|---|
| **Session record** | `models.Session` — `pid`, `proc_start`, `scope`, `state`, `dry_run`. No change. |
| **Termination outcome** | `boundaries.TerminationOutcome` — one new field, `refused_reason`. |
| **Action record** | `audit.AuditLog` — the existing `session.terminate` intent/outcome pair, whose outcome detail gains `refused`, `refused_reason`, `signals_sent`. No new action name. |
