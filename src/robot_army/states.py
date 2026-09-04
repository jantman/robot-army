"""The two state machines, and the single gate every transition passes through.

data-model.md defines both machines as tables of legal transitions. They are encoded
here as frozen sets rather than as scattered ``if`` statements so that "is this legal?"
has exactly one answer in exactly one place, and so that the illegal cases can be
enumerated in a test (T022).

``transition()`` is deliberately the only way a state column is written. It writes the
audit record *inside the same transaction as the state change* (T014, FR-036), so a
crash can never produce one without the other.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from robot_army.audit import AuditLog


class WorkItemState(StrEnum):
    """FR-030. ``done`` and ``abandoned`` are terminal."""

    DISCOVERED = "discovered"
    READY = "ready"
    DISPATCHING = "dispatching"
    ACTIVE = "active"
    AWAITING_REVIEW = "awaiting_review"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    DONE = "done"
    ABANDONED = "abandoned"


class SessionState(StrEnum):
    """FR-031."""

    STARTING = "starting"
    RUNNING = "running"
    EXITED_CLEAN = "exited_clean"
    EXITED_ERROR = "exited_error"
    LOST = "lost"


#: Legal work item transitions, straight from data-model.md's table. Anything absent
#: here is rejected — that is the whole point of a single gate.
WORK_ITEM_TRANSITIONS: frozenset[tuple[WorkItemState, WorkItemState]] = frozenset(
    {
        (WorkItemState.DISCOVERED, WorkItemState.READY),
        (WorkItemState.DISCOVERED, WorkItemState.FAILED),
        (WorkItemState.READY, WorkItemState.DISPATCHING),
        (WorkItemState.READY, WorkItemState.ABANDONED),
        (WorkItemState.DISPATCHING, WorkItemState.ACTIVE),
        (WorkItemState.DISPATCHING, WorkItemState.FAILED),
        (WorkItemState.ACTIVE, WorkItemState.AWAITING_REVIEW),
        (WorkItemState.ACTIVE, WorkItemState.FAILED),
        (WorkItemState.ACTIVE, WorkItemState.INTERRUPTED),
        (WorkItemState.ACTIVE, WorkItemState.DONE),
        (WorkItemState.AWAITING_REVIEW, WorkItemState.DONE),
        (WorkItemState.AWAITING_REVIEW, WorkItemState.DISPATCHING),
        (WorkItemState.AWAITING_REVIEW, WorkItemState.ABANDONED),
        (WorkItemState.INTERRUPTED, WorkItemState.DISPATCHING),
        (WorkItemState.INTERRUPTED, WorkItemState.DONE),
        (WorkItemState.INTERRUPTED, WorkItemState.ABANDONED),
        (WorkItemState.FAILED, WorkItemState.READY),
        (WorkItemState.FAILED, WorkItemState.ABANDONED),
    }
)

SESSION_TRANSITIONS: frozenset[tuple[SessionState, SessionState]] = frozenset(
    {
        (SessionState.STARTING, SessionState.RUNNING),
        (SessionState.STARTING, SessionState.LOST),
        (SessionState.RUNNING, SessionState.EXITED_CLEAN),
        (SessionState.RUNNING, SessionState.EXITED_ERROR),
        (SessionState.RUNNING, SessionState.LOST),
    }
)

TERMINAL_WORK_ITEM_STATES: frozenset[WorkItemState] = frozenset(
    {WorkItemState.DONE, WorkItemState.ABANDONED}
)

TERMINAL_SESSION_STATES: frozenset[SessionState] = frozenset(
    {SessionState.EXITED_CLEAN, SessionState.EXITED_ERROR, SessionState.LOST}
)

#: Work item state → the timestamp column that state stamps, where one exists.
_WORK_ITEM_STAMP_COLUMN: dict[WorkItemState, str] = {
    WorkItemState.READY: "ready_at",
    WorkItemState.DISPATCHING: "dispatching_at",
    WorkItemState.ACTIVE: "active_at",
    WorkItemState.AWAITING_REVIEW: "ended_at",
    WorkItemState.INTERRUPTED: "ended_at",
    WorkItemState.FAILED: "ended_at",
    WorkItemState.DONE: "done_at",
}

_SESSION_STAMP_COLUMN: dict[SessionState, str] = {
    SessionState.RUNNING: "confirmed_at",
    SessionState.EXITED_CLEAN: "ended_at",
    SessionState.EXITED_ERROR: "ended_at",
    SessionState.LOST: "ended_at",
}


class IllegalTransition(Exception):
    """Raised when a transition is not in the legal table.

    This is a programming error, not an operational condition, and it is deliberately
    loud: silently ignoring it would let state drift, which is exactly what the single
    gate exists to prevent.
    """

    def __init__(self, entity: str, entity_id: object, source: str, target: str) -> None:
        super().__init__(
            f"illegal {entity} transition for {entity_id}: {source!r} -> {target!r}"
        )
        self.entity = entity
        self.entity_id = entity_id
        self.source = source
        self.target = target


class ClaimLost(Exception):
    """Another claimant took this item first, or it is in a state that cannot be claimed.

    Unlike :class:`IllegalTransition` this is an *operational* condition, not a programming
    error: two processes racing for one item is a thing that legitimately happens on a
    machine with a web interface and a terminal, and exactly one of them is supposed to
    lose. The loser has written nothing.
    """

    #: States a *concurrent claimant* leaves behind. Reaching one of these means somebody
    #: else took the item; reaching any other claimable-target failure means the item was
    #: never eligible, which is a different sentence and must not be told as a race.
    _RACED: frozenset[str] = frozenset({"dispatching", "active"})

    def __init__(self, item_id: int, found: WorkItemState | None) -> None:
        if found is None:
            super().__init__(f"work item {item_id} no longer exists")
        elif str(found) in self._RACED:
            super().__init__(
                f"item {item_id} is {found}; it was claimed by another dispatcher"
            )
        else:
            # ``failed``, ``done``, ``abandoned``, ``discovered``. Nobody raced for this —
            # it simply is not in a state a session can start from, and saying otherwise
            # would send the reader hunting a second process that never existed.
            super().__init__(
                f"item {item_id} is {found}; a session cannot be started from that state"
            )
        self.item_id = item_id
        self.found = found


def claimable_sources(target: WorkItemState) -> frozenset[WorkItemState]:
    """The states from which ``target`` may be entered, read off the transition table.

    Derived rather than written out, and that is the point. The obvious implementation of
    an atomic claim hard-codes ``('ready','interrupted','awaiting_review')`` in its
    ``WHERE`` clause — today's correct answer, recorded a second time, in a module whose
    entire purpose is that "is this legal?" has exactly one answer in exactly one place.
    Deriving it also gives FR-018 for free: no state's self-transition is in the table, so
    an item already ``dispatching`` is excluded without anyone remembering to exclude it.
    """
    return frozenset(
        source for source, allowed in WORK_ITEM_TRANSITIONS if allowed is target
    )


def claim_work_item(
    conn: sqlite3.Connection,
    audit: AuditLog,
    *,
    item_id: int,
    target: WorkItemState,
    reason: str,
    extra_columns: dict[str, object] | None = None,
) -> None:
    """Move one work item to ``target``, atomically, or raise :class:`ClaimLost`.

    The other half of RA-05 (issue #120). ``transition_work_item`` reads the state and then
    writes it, and treats "already there" as a legitimate no-op — which is correct for
    reconciliation and for spool replay, both of which re-derive a state an item already
    holds. But it also means ``dispatching -> dispatching`` succeeds silently, so two
    processes racing to launch one item — the web worker and a terminal command — could
    both walk past it and start **two agent sessions in one worktree on one branch**.
    ``web/server.py`` explicitly anticipates an ``IllegalTransition`` from "a concurrent
    terminal command"; for this one pair, none was ever raised.

    This is that function with the read and the write fused into one statement: SQLite
    evaluates the ``WHERE`` and applies the ``SET`` indivisibly, so of any number of
    concurrent attempts exactly one sees a row change. The audit record is written inside
    the caller's transaction exactly as ``transition_work_item`` writes its own, so nothing
    downstream can tell which function moved the item.

    ``transition_work_item`` is deliberately **not** modified. Making *it* conditional
    would take the no-op re-assertion away from reconcile and spool replay, which is a
    behaviour they depend on; leaving it alone makes that guarantee structural rather than
    something a test has to defend against a future tidy-up.
    """
    sources = claimable_sources(target)
    if not sources:
        # No legal way in at all. A programming error rather than a race, and it must not
        # read as one: reporting "claimed by another dispatcher" for a target nothing can
        # reach would send the reader hunting a second process that does not exist.
        raise IllegalTransition("work_item", item_id, "<none>", target)

    now = utcnow()
    columns: dict[str, object] = {"state": str(target), "updated_at": now}
    stamp = _WORK_ITEM_STAMP_COLUMN.get(target)
    if stamp is not None:
        columns[stamp] = now
    if extra_columns:
        columns.update(extra_columns)

    assignments = ", ".join(f"{name} = ?" for name in columns)
    placeholders = ", ".join("?" for _ in sources)
    ordered = sorted(str(source) for source in sources)
    cursor = conn.execute(
        f"UPDATE work_items SET {assignments} "  # noqa: S608 - names are ours
        f"WHERE id = ? AND state IN ({placeholders})",
        (*columns.values(), item_id, *ordered),
    )
    if cursor.rowcount == 0:
        # Read only on the losing path, and only to tell the two failures apart: the item
        # is gone, or somebody else has it. One extra query on the rare branch beats a
        # message that cannot say which happened.
        row = conn.execute(
            "SELECT state FROM work_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no work item with id {item_id}")
        raise ClaimLost(item_id, WorkItemState(row["state"]))

    row = conn.execute(
        "SELECT dry_run FROM work_items WHERE id = ?", (item_id,)
    ).fetchone()
    audit.record(
        "state.work_item",
        entity_type="work_item",
        entity_id=item_id,
        outcome="ok",
        detail={
            # The claim does not read the previous state before writing --- that is what
            # makes it atomic --- so it reports the set it was willing to accept rather
            # than inventing a single value it never observed.
            "from": "|".join(ordered),
            "to": str(target),
            "reason": reason,
            "columns": {k: v for k, v in columns.items() if k not in ("state", "updated_at")},
            "claimed": True,
        },
        dry_run=bool(row["dry_run"]),
    )


def is_legal_work_item_transition(source: WorkItemState, target: WorkItemState) -> bool:
    return (source, target) in WORK_ITEM_TRANSITIONS


def is_legal_session_transition(source: SessionState, target: SessionState) -> bool:
    return (source, target) in SESSION_TRANSITIONS


def utcnow() -> str:
    """UTC ISO 8601 with a ``Z`` suffix — the only timestamp format in the database."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify_exit(exit_code: int) -> tuple[SessionState, WorkItemState, int | None]:
    """Map a raw shell exit status to session state, work item state, and signal.

    The table is data-model.md's, measured in M0 as E3.3:

    ==========  ===============  =================
    exit        session          work item
    ==========  ===============  =================
    0           exited_clean     awaiting_review
    1/126/127   exited_error     failed
    128+N       exited_error     interrupted
    other       exited_error     failed
    ==========  ===============  =================

    ``1``, ``126`` and ``127`` are singled out because they are the configuration
    errors — the worker never ran, so retrying without a config change is pointless.
    ``128+N`` means something killed it externally, which is usually resumable and is
    not a failure of the work item.
    """
    if exit_code == 0:
        return SessionState.EXITED_CLEAN, WorkItemState.AWAITING_REVIEW, None
    if exit_code in (1, 126, 127):
        return SessionState.EXITED_ERROR, WorkItemState.FAILED, None
    if 128 < exit_code < 192:
        return SessionState.EXITED_ERROR, WorkItemState.INTERRUPTED, exit_code - 128
    return SessionState.EXITED_ERROR, WorkItemState.FAILED, None


def transition_work_item(
    conn: sqlite3.Connection,
    audit: AuditLog,
    *,
    item_id: int,
    target: WorkItemState,
    reason: str,
    extra_columns: dict[str, object] | None = None,
) -> WorkItemState:
    """Move one work item to ``target``, or raise.

    The audit record is written to the log before the transaction commits. The caller
    is expected to already be inside a transaction (``db.transaction``); this function
    does not open one, precisely so that the state change and everything else the
    caller is doing commit or roll back together.
    """
    row = conn.execute(
        "SELECT state, dry_run FROM work_items WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"no work item with id {item_id}")
    source = WorkItemState(row["state"])
    if source == target:
        # A no-op re-assertion is not an error; reconciliation and spool replay both
        # legitimately re-derive a state the item already holds.
        return source
    if not is_legal_work_item_transition(source, target):
        raise IllegalTransition("work_item", item_id, source, target)

    now = utcnow()
    columns: dict[str, object] = {"state": str(target), "updated_at": now}
    stamp = _WORK_ITEM_STAMP_COLUMN.get(target)
    if stamp is not None:
        columns[stamp] = now
    if extra_columns:
        columns.update(extra_columns)

    assignments = ", ".join(f"{name} = ?" for name in columns)
    conn.execute(
        f"UPDATE work_items SET {assignments} WHERE id = ?",  # noqa: S608 - names are ours
        (*columns.values(), item_id),
    )
    audit.record(
        "state.work_item",
        entity_type="work_item",
        entity_id=item_id,
        outcome="ok",
        detail={
            "from": str(source),
            "to": str(target),
            "reason": reason,
            "columns": {k: v for k, v in columns.items() if k not in ("state", "updated_at")},
        },
        dry_run=bool(row["dry_run"]),
    )
    return source


def transition_session(
    conn: sqlite3.Connection,
    audit: AuditLog,
    *,
    session_row_id: int,
    target: SessionState,
    reason: str,
    extra_columns: dict[str, object] | None = None,
) -> SessionState:
    """Move one session row to ``target``, or raise. Same transaction contract as above."""
    row = conn.execute(
        "SELECT state, dry_run, session_id FROM sessions WHERE id = ?", (session_row_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"no session with row id {session_row_id}")
    source = SessionState(row["state"])
    if source == target:
        return source
    if not is_legal_session_transition(source, target):
        raise IllegalTransition("session", session_row_id, source, target)

    now = utcnow()
    columns: dict[str, object] = {"state": str(target)}
    stamp = _SESSION_STAMP_COLUMN.get(target)
    if stamp is not None:
        columns[stamp] = now
    if extra_columns:
        columns.update(extra_columns)

    assignments = ", ".join(f"{name} = ?" for name in columns)
    conn.execute(
        f"UPDATE sessions SET {assignments} WHERE id = ?",  # noqa: S608 - names are ours
        (*columns.values(), session_row_id),
    )
    audit.record(
        "state.session",
        entity_type="session",
        entity_id=row["session_id"],
        outcome="ok",
        detail={
            "from": str(source),
            "to": str(target),
            "reason": reason,
            "columns": {k: v for k, v in columns.items() if k != "state"},
        },
        dry_run=bool(row["dry_run"]),
    )
    return source


def dumps_labels(labels: list[str]) -> str:
    """Labels are stored as a JSON array; one helper so the encoding is not repeated."""
    return json.dumps(list(labels), separators=(",", ":"))
