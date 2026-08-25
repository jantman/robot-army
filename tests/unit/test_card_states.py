"""The card lifecycle, enumerated (T010).

data-model.md's transition table is asserted here *whole* — every legal move, and every
illegal one — rather than spot-checked. The two refusals that carry design weight get
their own tests, because both are easy to "fix" into a bug by someone who reads the
enumeration as an oversight:

* ``creating`` has no exit to ``needs_info`` or ``dropped``. An issue may already exist.
* ``linked`` is terminal, archived card or not. Dropping the mapping is how a re-tagged
  card creates a second issue.
"""

from __future__ import annotations

import itertools

import pytest

from robot_army import db
from robot_army.cardstates import (
    CARD_TRANSITIONS,
    CardState,
    IllegalCardTransition,
    is_legal_card_transition,
    transition_card,
)

#: data-model.md's table, transcribed by hand rather than derived from the code — the
#: point is to catch the code drifting away from the document.
EXPECTED_LEGAL = {
    (CardState.DISCOVERED, CardState.NEEDS_INFO),
    (CardState.DISCOVERED, CardState.CREATING),
    (CardState.DISCOVERED, CardState.DROPPED),
    (CardState.NEEDS_INFO, CardState.CREATING),
    (CardState.NEEDS_INFO, CardState.DROPPED),
    (CardState.CREATING, CardState.LINKED),
}


def seed_card(conn, *, state: CardState = CardState.DISCOVERED, dry_run: bool = False) -> int:
    with db.transaction(conn):
        row_id = db.insert_card(
            conn,
            board_id="board-1",
            card_id="card-1",
            card_url="https://trello.com/c/card-1",
            title="Fix the thing",
            body="in me/demo",
            dry_run=dry_run,
        )
    assert row_id is not None
    if state is not CardState.DISCOVERED:
        conn.execute("UPDATE cards SET state = ? WHERE id = ?", (str(state), row_id))
    return row_id


def test_the_transition_table_is_exactly_the_documented_one():
    assert CARD_TRANSITIONS == EXPECTED_LEGAL


@pytest.mark.parametrize(("source", "target"), sorted(EXPECTED_LEGAL))
def test_every_legal_transition_is_accepted(conn, audit, source, target):
    row_id = seed_card(conn, state=source)
    with db.transaction(conn):
        assert transition_card(
            conn, audit, card_row_id=row_id, target=target, reason="test"
        ) == source
    assert db.get_card_by_id(conn, row_id).state == target


ILLEGAL = sorted(
    pair
    for pair in itertools.product(CardState, CardState)
    if pair[0] != pair[1] and pair not in EXPECTED_LEGAL
)


@pytest.mark.parametrize(("source", "target"), ILLEGAL)
def test_every_illegal_transition_is_refused(conn, audit, source, target):
    row_id = seed_card(conn, state=source)
    assert not is_legal_card_transition(source, target)
    with pytest.raises(IllegalCardTransition), db.transaction(conn):
        transition_card(conn, audit, card_row_id=row_id, target=target, reason="test")
    assert db.get_card_by_id(conn, row_id).state == source


@pytest.mark.parametrize("target", [CardState.NEEDS_INFO, CardState.DROPPED])
def test_creating_has_no_retreat(conn, audit, target):
    """A failed creation stays in ``creating``: the intent stands, and R6's recovery must
    still run against it. Retreating would discard the ``intent_at`` the recovery bounds
    its issue listing by — and an issue may already exist."""
    row_id = seed_card(conn, state=CardState.CREATING)
    with pytest.raises(IllegalCardTransition), db.transaction(conn):
        transition_card(conn, audit, card_row_id=row_id, target=target, reason="gave up")
    assert db.get_card_by_id(conn, row_id).state == CardState.CREATING


def test_linked_is_terminal_even_for_an_archived_card(conn, audit):
    """FR-025's exception, and the reason for it: dropping a linked card's mapping would
    let a re-tagged card create a second issue. ``archived_at`` is recorded instead."""
    row_id = seed_card(conn, state=CardState.LINKED)
    for target in (CardState.DROPPED, CardState.NEEDS_INFO, CardState.CREATING):
        with pytest.raises(IllegalCardTransition), db.transaction(conn):
            transition_card(conn, audit, card_row_id=row_id, target=target, reason="archived")
    with db.transaction(conn):
        db.update_card_columns(conn, row_id, archived_at="2026-08-24T00:00:00Z")
    card = db.get_card_by_id(conn, row_id)
    assert card.state == CardState.LINKED
    assert card.archived_at == "2026-08-24T00:00:00Z"


def test_re_asserting_the_state_a_card_already_holds_is_a_no_op(conn, audit):
    """The recovery sweep legitimately re-derives a state a card already holds."""
    row_id = seed_card(conn, state=CardState.LINKED)
    with db.transaction(conn):
        assert transition_card(
            conn, audit, card_row_id=row_id, target=CardState.LINKED, reason="recovered"
        ) == CardState.LINKED


def test_the_audit_record_is_written_inside_the_same_transaction(conn, audit, layout):
    """A crash can never produce one without the other, which is what makes the log a
    reconstruction of state rather than a parallel story about it."""
    row_id = seed_card(conn)
    with pytest.raises(RuntimeError), db.transaction(conn):
        transition_card(
            conn, audit, card_row_id=row_id, target=CardState.CREATING, reason="test"
        )
        raise RuntimeError("killed before commit")
    assert db.get_card_by_id(conn, row_id).state == CardState.DISCOVERED


def test_entering_creating_stamps_the_intent_timestamp(conn, audit):
    """``intent_at`` *is* the intent: R6's recovery bounds its issue listing by it, so it
    is written by the same statement that records the state."""
    row_id = seed_card(conn)
    with db.transaction(conn):
        transition_card(
            conn, audit, card_row_id=row_id, target=CardState.CREATING, reason="resolved"
        )
    assert db.get_card_by_id(conn, row_id).intent_at is not None


def test_a_missing_card_raises_rather_than_silently_doing_nothing(conn, audit):
    with pytest.raises(LookupError), db.transaction(conn):
        transition_card(conn, audit, card_row_id=999, target=CardState.DROPPED, reason="x")
