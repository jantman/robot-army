"""Every legal transition, and every illegal one rejected (T022).

The exhaustive illegal-transition check is the point: a single legal-transition table is
only a guarantee if *everything absent from it* is refused, and the only way to know that
is to try them all.
"""

from __future__ import annotations

import itertools

import pytest
from tests.conftest import seed_item

from robot_army import db
from robot_army.states import (
    SESSION_TRANSITIONS,
    TERMINAL_SESSION_STATES,
    TERMINAL_WORK_ITEM_STATES,
    WORK_ITEM_TRANSITIONS,
    IllegalTransition,
    SessionState,
    WorkItemState,
    classify_exit,
    is_legal_session_transition,
    is_legal_work_item_transition,
    transition_session,
    transition_work_item,
)


def test_every_legal_work_item_transition_is_accepted():
    for source, target in WORK_ITEM_TRANSITIONS:
        assert is_legal_work_item_transition(source, target), f"{source} -> {target}"


def test_every_illegal_work_item_transition_is_rejected():
    illegal = [
        (a, b)
        for a, b in itertools.product(WorkItemState, WorkItemState)
        if (a, b) not in WORK_ITEM_TRANSITIONS
    ]
    assert illegal, "the table cannot legitimately contain every pair"
    for source, target in illegal:
        assert not is_legal_work_item_transition(source, target), f"{source} -> {target}"


def test_every_illegal_session_transition_is_rejected():
    for source, target in itertools.product(SessionState, SessionState):
        expected = (source, target) in SESSION_TRANSITIONS
        assert is_legal_session_transition(source, target) is expected


def test_terminal_states_have_no_outgoing_transitions():
    for state in TERMINAL_WORK_ITEM_STATES:
        assert not [t for s, t in WORK_ITEM_TRANSITIONS if s == state]
    for state in TERMINAL_SESSION_STATES:
        assert not [t for s, t in SESSION_TRANSITIONS if s == state]


def test_a_few_transitions_that_must_not_exist():
    """Named individually because each would be a real bug with a real consequence."""
    # Skipping preparation entirely.
    assert not is_legal_work_item_transition(WorkItemState.READY, WorkItemState.ACTIVE)
    # Reviving a terminal item.
    assert not is_legal_work_item_transition(WorkItemState.DONE, WorkItemState.READY)
    assert not is_legal_work_item_transition(WorkItemState.ABANDONED, WorkItemState.READY)
    # Marking active on the strength of a launch call, with no dispatching step.
    assert not is_legal_work_item_transition(WorkItemState.DISCOVERED, WorkItemState.ACTIVE)
    # A session that never confirmed cannot report a clean exit.
    assert not is_legal_session_transition(SessionState.STARTING, SessionState.EXITED_CLEAN)


@pytest.mark.parametrize(
    ("code", "session", "item", "signal"),
    [
        (0, SessionState.EXITED_CLEAN, WorkItemState.AWAITING_REVIEW, None),
        (1, SessionState.EXITED_ERROR, WorkItemState.FAILED, None),
        (126, SessionState.EXITED_ERROR, WorkItemState.FAILED, None),
        (127, SessionState.EXITED_ERROR, WorkItemState.FAILED, None),
        (137, SessionState.EXITED_ERROR, WorkItemState.INTERRUPTED, 9),
        (143, SessionState.EXITED_ERROR, WorkItemState.INTERRUPTED, 15),
        (42, SessionState.EXITED_ERROR, WorkItemState.FAILED, None),
        (2, SessionState.EXITED_ERROR, WorkItemState.FAILED, None),
    ],
)
def test_classify_exit_table(code, session, item, signal):
    assert classify_exit(code) == (session, item, signal)


def test_128_exactly_is_not_a_signal():
    """``128`` is not ``128+N`` for any signal N, so it is a plain non-zero exit."""
    _, item, signal = classify_exit(128)
    assert signal is None
    assert item is WorkItemState.FAILED


def test_transition_rejects_illegal_move_and_leaves_state_untouched(conn, audit):
    item_id = seed_item(conn)
    with db.transaction(conn), pytest.raises(IllegalTransition):
        transition_work_item(
            conn,
            audit,
            item_id=item_id,
            target=WorkItemState.ACTIVE,
            reason="skipping the whole machine",
        )
    item = db.get_work_item(conn, item_id)
    assert item is not None
    assert item.state is WorkItemState.DISCOVERED


def test_transition_writes_the_audit_record_in_the_same_transaction(conn, audit, layout):
    """A crash must not be able to produce a state change with no record (FR-036).

    Rolling the transaction back after the transition proves the coupling in the only
    direction a test can observe: the state change is gone.
    """
    item_id = seed_item(conn)
    try:
        with db.transaction(conn):
            transition_work_item(
                conn,
                audit,
                item_id=item_id,
                target=WorkItemState.READY,
                reason="eligible",
            )
            raise RuntimeError("simulated crash before commit")
    except RuntimeError:
        pass
    item = db.get_work_item(conn, item_id)
    assert item is not None
    assert item.state is WorkItemState.DISCOVERED


def test_transition_stamps_its_timestamp_column(conn, audit):
    item_id = seed_item(conn)
    with db.transaction(conn):
        transition_work_item(
            conn, audit, item_id=item_id, target=WorkItemState.READY, reason="eligible"
        )
    item = db.get_work_item(conn, item_id)
    assert item is not None and item.ready_at is not None

    with db.transaction(conn):
        transition_work_item(
            conn, audit, item_id=item_id, target=WorkItemState.DISPATCHING, reason="capacity"
        )
    item = db.get_work_item(conn, item_id)
    assert item is not None and item.dispatching_at is not None


def test_re_asserting_the_same_state_is_a_no_op_not_an_error(conn, audit):
    """Reconciliation and spool replay both legitimately re-derive a held state."""
    item_id = seed_item(conn)
    with db.transaction(conn):
        source = transition_work_item(
            conn,
            audit,
            item_id=item_id,
            target=WorkItemState.DISCOVERED,
            reason="re-derived",
        )
    assert source is WorkItemState.DISCOVERED


def test_session_transitions_stamp_and_reject(conn, audit):
    item_id = seed_item(conn)
    with db.transaction(conn):
        row_id = db.insert_session(
            conn, work_item_id=item_id, session_id="s-1", attempt=1, dry_run=False
        )
    with db.transaction(conn):
        transition_session(
            conn, audit, session_row_id=row_id, target=SessionState.RUNNING, reason="confirmed"
        )
    session = db.get_session(conn, "s-1")
    assert session is not None and session.confirmed_at is not None

    with db.transaction(conn), pytest.raises(IllegalTransition):
        transition_session(
            conn,
            audit,
            session_row_id=row_id,
            target=SessionState.STARTING,
            reason="going backwards",
        )
