"""The card lifecycle, and the single gate every card transition passes through.

Separate from :mod:`robot_army.states` deliberately. That module's two machines are about
*dispatchable work*; this one is not, and merging them would put a state that can never be
dispatched into the same enumeration the dispatcher reads.

The shape is the same as its neighbour's, for the same reasons: the legal transitions are
a frozen set rather than scattered ``if`` statements, so "is this legal?" has exactly one
answer in one place and the illegal cases can be enumerated in a test; and
:func:`transition_card` writes its audit record *inside the same transaction as the state
change*, so a crash can never produce one without the other.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from robot_army.audit import AuditLog


class CardState(StrEnum):
    """FR-020, and data-model.md's table.

    ``linked`` is terminal — even for a card that is later archived or untagged. That is
    not tidiness: dropping a linked card's mapping would let a re-tagged card create a
    second issue, which is the exact failure §11 exists to prevent. An archived linked
    card records ``archived_at`` and keeps its mapping.
    """

    #: Row exists, evaluation not yet settled. Observable only after an interrupted pass.
    DISCOVERED = "discovered"
    #: Tagged, but no single known repository could be identified. ``reason`` says which.
    NEEDS_INFO = "needs_info"
    #: Intent to create an issue is recorded; the issue may or may not exist yet (R6).
    CREATING = "creating"
    #: The issue exists and the mapping is recorded. Terminal.
    LINKED = "linked"
    #: The card lost its tag, was archived, or was deleted **before** it was linked.
    DROPPED = "dropped"


#: Legal card transitions, straight from data-model.md's table. Anything absent here is
#: rejected — that is the whole point of a single gate.
#:
#: ``creating`` has **no** exit to ``needs_info`` or ``dropped``, and that is deliberate in
#: both directions. A failed creation stays in ``creating`` with its reason and an
#: incremented ``create_failures``, because the intent stands and R6's recovery must still
#: run against it; retreating to ``needs_info`` would discard the ``intent_at`` timestamp
#: that recovery depends on. And a card archived while in ``creating`` cannot be dropped,
#: because an issue may already exist for it.
CARD_TRANSITIONS: frozenset[tuple[CardState, CardState]] = frozenset(
    {
        (CardState.DISCOVERED, CardState.NEEDS_INFO),
        (CardState.DISCOVERED, CardState.CREATING),
        (CardState.DISCOVERED, CardState.DROPPED),
        (CardState.NEEDS_INFO, CardState.CREATING),
        (CardState.NEEDS_INFO, CardState.DROPPED),
        (CardState.CREATING, CardState.LINKED),
    }
)

TERMINAL_CARD_STATES: frozenset[CardState] = frozenset({CardState.LINKED, CardState.DROPPED})

#: States for which milestone 006's ignore list is not consulted at all.
#:
#: ``linked`` is past intake (FR-013) — and it is what makes listing the in-progress or
#: done column harmless rather than contradictory, since by the time the daemon puts a card
#: in either it is already linked. ``creating`` has a recorded intent that R6's recovery
#: must still run against, so parking must not cancel it. ``dropped`` is terminal and the
#: ignore list is not a route back from it (FR-012).
#:
#: It lives **here** rather than beside either of its two callers because both of them need
#: it and they cannot share it any other way: ``operations`` imports ``intake``, so the set
#: cannot live in ``operations``, and a second copy in ``intake`` is exactly how the two
#: drift — which they did, and a review caught it. ``intake`` decides whether to *record* a
#: park; ``operations`` decides whether to *show* one; disagreeing produced a record for a
#: card that was never shown as parked, and a park with no matching release.
NEVER_PARKED: frozenset[CardState] = frozenset(
    {CardState.LINKED, CardState.CREATING, CardState.DROPPED}
)

#: Card state → the timestamp column that state stamps, where one exists. ``creating``
#: stamps ``intent_at`` because that timestamp *is* the intent: R6's recovery bounds its
#: issue listing by it, so it must be written by the same statement that records the state.
_CARD_STAMP_COLUMN: dict[CardState, str] = {
    CardState.CREATING: "intent_at",
}


class IllegalCardTransition(Exception):
    """Raised when a card transition is not in the legal table.

    A programming error rather than an operational condition, and deliberately loud for
    the same reason its ``states.py`` counterpart is: silently ignoring it would let state
    drift, which is exactly what a single gate exists to prevent.
    """

    def __init__(self, card_row_id: object, source: str, target: str) -> None:
        super().__init__(f"illegal card transition for {card_row_id}: {source!r} -> {target!r}")
        self.card_row_id = card_row_id
        self.source = source
        self.target = target


def is_legal_card_transition(source: CardState, target: CardState) -> bool:
    return (source, target) in CARD_TRANSITIONS


def utcnow() -> str:
    """UTC ISO 8601 with a ``Z`` suffix — the only timestamp format in the database."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def transition_card(
    conn: sqlite3.Connection,
    audit: AuditLog,
    *,
    card_row_id: int,
    target: CardState,
    reason: str,
    extra_columns: dict[str, object] | None = None,
) -> CardState:
    """Move one card row to ``target``, or raise. Returns the state it came from.

    The audit record is written before the transaction commits. The caller is expected to
    already be inside one (``db.transaction``); this function does not open its own,
    precisely so the state change and everything else the caller is doing commit or roll
    back together — which is what makes a card's state and its mapping impossible to
    observe out of step with each other.
    """
    row = conn.execute(
        "SELECT state, dry_run, board_id, card_id FROM cards WHERE id = ?", (card_row_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"no card with row id {card_row_id}")
    source = CardState(row["state"])
    if source == target:
        # A no-op re-assertion is not an error: the recovery sweep legitimately re-derives
        # a state a card already holds.
        return source
    if not is_legal_card_transition(source, target):
        raise IllegalCardTransition(card_row_id, source, target)

    now = utcnow()
    columns: dict[str, object] = {"state": str(target), "updated_at": now}
    stamp = _CARD_STAMP_COLUMN.get(target)
    if stamp is not None:
        columns[stamp] = now
    if extra_columns:
        columns.update(extra_columns)

    assignments = ", ".join(f"{name} = ?" for name in columns)
    conn.execute(
        f"UPDATE cards SET {assignments} WHERE id = ?",  # noqa: S608 - names are ours
        (*columns.values(), card_row_id),
    )
    audit.record(
        "state.card",
        entity_type="card",
        entity_id=row["card_id"],
        target=row["card_id"],
        outcome="ok",
        detail={
            "from": str(source),
            "to": str(target),
            "reason": reason,
            "board_id": row["board_id"],
            "columns": {k: v for k, v in columns.items() if k not in ("state", "updated_at")},
        },
        dry_run=bool(row["dry_run"]),
    )
    return source
