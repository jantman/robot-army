"""Draining the exit spool — the one contract that crosses a process boundary.

The wrapper writes each session's outcome as a single JSON file, atomically
(``write`` → ``fsync`` → ``rename``). The daemon drains that directory at the top of every
tick, applies each record in a transaction, and unlinks the file **only after the
transaction commits**.

**Why a spool file and not an HTTP POST** (research.md R5, a deliberate departure from
planning §9): a POST to a daemon that is down loses the record permanently, and the daemon
is legitimately down during restarts and upgrades. That lost record would silently
downgrade a clean completion into a phantom that reconciliation could only ever classify
as ``interrupted``. A file survives the daemon being down, survives a reboot, and is
replayed on next startup.

Because unlink follows commit, a crash in between causes reapplication — so application is
**idempotent on ``(session_id, event)``**, checked before anything is written.

Malformed records are quarantined, never deleted. A record we cannot parse is still
evidence, and destroying evidence is the opposite of what an audit trail is for.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from robot_army import db
from robot_army.states import (
    SessionState,
    WorkItemState,
    classify_exit,
    transition_session,
    transition_work_item,
)

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.paths import Layout

#: The only schema this daemon understands. An unknown one is quarantined rather than
#: guessed at — guessing is how a field that changed meaning corrupts state silently.
SUPPORTED_SCHEMA = 1

VALID_EVENTS = frozenset({"start", "exit"})


@dataclass(frozen=True, slots=True)
class DrainResult:
    applied: int = 0
    duplicates: int = 0
    quarantined: int = 0
    orphaned: int = 0

    def __add__(self, other: DrainResult) -> DrainResult:
        return DrainResult(
            applied=self.applied + other.applied,
            duplicates=self.duplicates + other.duplicates,
            quarantined=self.quarantined + other.quarantined,
            orphaned=self.orphaned + other.orphaned,
        )

    @property
    def total(self) -> int:
        return self.applied + self.duplicates + self.quarantined + self.orphaned


class MalformedRecord(Exception):
    """The record cannot be trusted. Carries why, for the anomaly detail."""


def parse_record(raw: str) -> dict[str, Any]:
    """Validate an exit record's shape. Raises ``MalformedRecord`` with the reason."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MalformedRecord(f"unparseable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise MalformedRecord("expected a JSON object")

    schema = payload.get("schema")
    if schema != SUPPORTED_SCHEMA:
        raise MalformedRecord(
            f"unknown schema {schema!r}; this daemon understands schema {SUPPORTED_SCHEMA}"
        )

    event = payload.get("event")
    if event not in VALID_EVENTS:
        raise MalformedRecord(f"unknown event {event!r}")

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise MalformedRecord("missing or empty session_id — the join key")

    if event == "exit":
        exit_code = payload.get("exit")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise MalformedRecord(f"exit must be an integer, got {exit_code!r}")
        signal = payload.get("signal")
        if signal is not None and (not isinstance(signal, int) or isinstance(signal, bool)):
            raise MalformedRecord(f"signal must be an integer or null, got {signal!r}")

    return payload


def _already_applied(
    conn: sqlite3.Connection, session_id: str, event: str
) -> bool:
    """Idempotency on ``(session_id, event)``.

    A ``start`` is applied once the session has moved past ``starting``; an ``exit`` is
    applied once the session's state is terminal. Both are derived from state the
    application itself set, so replay is a genuine no-op rather than a second write that
    happens to produce the same values.
    """
    row = conn.execute(
        "SELECT state, exit_code FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return False
    state = SessionState(row["state"])
    if event == "exit":
        return state in (
            SessionState.EXITED_CLEAN,
            SessionState.EXITED_ERROR,
            SessionState.LOST,
        )
    return state is not SessionState.STARTING


def apply_record(
    conn: sqlite3.Connection,
    audit: AuditLog,
    payload: dict[str, Any],
) -> str:
    """Apply one validated record. Returns ``"applied"``, ``"duplicate"`` or ``"orphan"``.

    Called inside a transaction the caller owns, so the state change and its audit record
    commit together and the file is unlinked only afterwards.
    """
    session_id = str(payload["session_id"])
    event = str(payload["event"])

    row = conn.execute(
        "SELECT id, work_item_id, state, dry_run FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        # Evidence of a session the daemon lost track of. The file is kept: discarding it
        # would destroy exactly the thing that makes this diagnosable.
        #
        # No `dry_run` (issue #21): this branch is reached *because* there is no sessions row,
        # so there is no flag to read. Real is also the right answer — an exit record nothing
        # claims is a gap in the record itself, not a fact about rehearsed work.
        db.raise_anomaly(
            conn,
            kind="orphan_exit_record",
            entity_type="session",
            entity_id=session_id,
            detail={"record": payload, "note": "no sessions row matches this session_id"},
        )
        return "orphan"

    if _already_applied(conn, session_id, event):
        return "duplicate"

    session_row_id = int(row["id"])
    item_id = int(row["work_item_id"])

    if event == "start":
        transition_session(
            conn,
            audit,
            session_row_id=session_row_id,
            target=SessionState.RUNNING,
            reason="wrapper reported the session started",
            extra_columns={"pid": payload.get("pid")} if payload.get("pid") else None,
        )
        return "applied"

    exit_code = int(payload["exit"])
    session_state, item_state, derived_signal = classify_exit(exit_code)
    # The wrapper decodes the signal at the point where the information is unambiguous,
    # so prefer its value; fall back to ours if the field is absent (FR-032).
    reported = payload.get("signal")
    signal = (
        int(reported)
        if isinstance(reported, int) and not isinstance(reported, bool)
        else derived_signal
    )

    current_session_state = SessionState(row["state"])
    if current_session_state is SessionState.STARTING:
        # An exit record with no prior start. Apply it anyway — a missing start is worth
        # an audit line but the outcome is the valuable part (contracts/exit-record.md).
        audit.record(
            "spool.exit_without_start",
            outcome="ok",
            entity_type="session",
            entity_id=session_id,
            detail={"exit": exit_code, "signal": signal},
            dry_run=bool(row["dry_run"]),
        )
        transition_session(
            conn,
            audit,
            session_row_id=session_row_id,
            target=SessionState.RUNNING,
            reason="inferred from an exit record that arrived with no prior start",
        )

    transition_session(
        conn,
        audit,
        session_row_id=session_row_id,
        target=session_state,
        reason=f"wrapper reported exit {exit_code}",
        extra_columns={"exit_code": exit_code, "signal": signal},
    )

    item = db.get_work_item(conn, item_id)
    if item is not None and item.state is WorkItemState.ACTIVE:
        reason = f"session exited {exit_code}"
        if signal is not None:
            reason += f" (signal {signal})"
        transition_work_item(
            conn,
            audit,
            item_id=item_id,
            target=item_state,
            reason=reason,
            extra_columns=(
                {"failure_reason": reason} if item_state is WorkItemState.FAILED else None
            ),
        )
    return "applied"


def _item_for_record(conn: sqlite3.Connection, payload: dict[str, Any]) -> Any:
    """The work item this exit record belongs to, as it stood *before* the record applied."""
    session = db.get_session(conn, str(payload.get("session_id") or ""))
    return db.get_work_item(conn, session.work_item_id) if session else None


def _announce(
    conn: sqlite3.Connection,
    audit: AuditLog,
    boundaries: Any,
    config: Any,
    before: Any,
) -> None:
    """Say that a session finished, if the author asked to be told."""
    if before is None:
        return
    from robot_army import notifications

    after = db.get_work_item(conn, before.id)
    if after is None or after.state is before.state:
        return
    if after.state is WorkItemState.AWAITING_REVIEW:
        kind, title = "completion", f"robot-army: item {after.id} is awaiting review"
    elif after.state is WorkItemState.FAILED:
        kind, title = "failure", f"robot-army: item {after.id} failed"
    else:
        return
    notifications.emit(
        boundaries=boundaries,
        audit=audit,
        config=config,
        kind=kind,
        item_id=after.id,
        repo_key=after.repo_key,
        title=title,
        detail=f"{after.title} — {after.failure_reason or 'the session exited'}",
        url=after.source_url,
    )


def quarantine(
    layout: Layout, path: Path, reason: str, conn: sqlite3.Connection, audit: AuditLog
) -> None:
    """Move a bad record aside and raise an anomaly. **Never silently delete.**"""
    layout.spool_rejected_dir.mkdir(parents=True, exist_ok=True)
    destination = layout.spool_rejected_dir / path.name
    counter = 1
    while destination.exists():
        destination = layout.spool_rejected_dir / f"{path.stem}.{counter}{path.suffix}"
        counter += 1
    try:
        path.replace(destination)
    except OSError as exc:
        audit.error("spool.quarantine", error=exc, detail={"path": str(path)})
        return
    with db.transaction(conn):
        db.raise_anomaly(
            conn,
            kind="malformed_exit_record",
            entity_type="session",
            entity_id=path.name,
            detail={"reason": reason, "quarantined_to": str(destination)},
        )
    audit.record(
        "spool.quarantine",
        outcome="ok",
        target=str(destination),
        detail={"reason": reason, "original": str(path)},
    )


def drain(
    conn: sqlite3.Connection,
    *,
    audit: AuditLog,
    layout: Layout,
    boundaries: Any = None,
    config: Any = None,
) -> DrainResult:
    """Read, apply, and unlink every record in the spool. Safe to call at any time.

    ``boundaries`` and ``config`` are optional and exist only so a completed session can be
    announced (milestone 004). They are optional because a drain must keep working without
    them: this is a recovery path, and a recovery path that needs more plumbing than the
    thing it recovers is not one. When they are absent nothing is sent and nothing else
    changes.

    contracts/notifications.md attributes the ``awaiting_review`` notification to
    ``reconcile.py``. It is here instead, because here is where that transition actually
    happens — a session exiting is what ends the work, and reconciliation never sees the
    moment. Emitting it from reconcile would mean polling for a state change the spool
    already observed exactly.
    """
    result = DrainResult()
    spool = layout.spool_dir
    if not spool.is_dir():
        return result

    for path in sorted(spool.glob("*.json")):
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue  # another drain took it, or the wrapper is mid-rename
        except OSError as exc:
            audit.error("spool.read", error=exc, detail={"path": str(path)})
            continue

        try:
            payload = parse_record(raw)
        except MalformedRecord as exc:
            quarantine(layout, path, str(exc), conn, audit)
            result += DrainResult(quarantined=1)
            continue

        item_before = _item_for_record(conn, payload)
        try:
            with db.transaction(conn):
                verdict = apply_record(conn, audit, payload)
        except Exception as exc:  # noqa: BLE001 - one bad record must not stop the drain
            audit.error(
                "spool.apply",
                error=exc,
                entity_type="session",
                entity_id=payload.get("session_id"),
                detail={"path": str(path), "record": payload},
            )
            continue

        if verdict == "orphan":
            # Keep the file: it is the evidence. It will be re-read every tick, but the
            # anomaly's partial unique index absorbs the repetition, and an acknowledged
            # anomaly plus a manual `rm` is the intended resolution.
            result += DrainResult(orphaned=1)
            continue

        # Unlink only after the transaction committed. A crash here means the record is
        # reapplied next tick, which is a no-op by design.
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            audit.error("spool.unlink", error=exc, detail={"path": str(path)})

        if verdict == "applied" and boundaries is not None and config is not None:
            # Outside the transaction that applied it (R14), and only when the item really
            # changed state — a duplicate record must not produce a second message.
            _announce(conn, audit, boundaries, config, item_before)

        result += (
            DrainResult(applied=1) if verdict == "applied" else DrainResult(duplicates=1)
        )

    if result.total:
        audit.record(
            "spool.drain",
            outcome="ok",
            detail={
                "applied": result.applied,
                "duplicates": result.duplicates,
                "quarantined": result.quarantined,
                "orphaned": result.orphaned,
            },
        )
    return result
