"""``abandon`` refuses in words, and says what to do (issue #76).

It used to reach the shared gate and print ``illegal work_item transition for 20:
<WorkItemState.ACTIVE: 'active'> -> <WorkItemState.ABANDONED: 'abandoned'>`` — a ``repr``
and no remedy — where ``resume`` already said which state the item was in and which it
needed. The rule is unchanged: an ``active`` item still cannot be abandoned, because
``abandon`` stops nothing. Only what the maintainer is told has changed.
"""

from __future__ import annotations

import pytest
from tests.conftest import seed_item, seed_session

from robot_army import db, operations
from robot_army.states import (
    IllegalTransition,
    SessionState,
    WorkItemState,
    is_legal_work_item_transition,
)


def context(conn, config, audit, boundaries) -> operations.Context:
    return operations.Context(
        conn=conn,
        config=config,
        audit=audit,
        boundaries=boundaries,
        effect_level=boundaries.level,
    )


#: Every state ``abandon`` must refuse, read from the machine so a change to the table is a
#: change to this list rather than a test that silently stops covering a state.
REFUSED = [
    s for s in WorkItemState
    if s is not WorkItemState.ABANDONED
    and not is_legal_work_item_transition(s, WorkItemState.ABANDONED)
]


def test_the_refused_states_are_the_ones_the_issue_is_about():
    assert WorkItemState.ACTIVE in REFUSED
    assert WorkItemState.INTERRUPTED not in REFUSED


def test_abandoning_an_active_item_names_its_state_and_the_verb_that_gets_you_there(
    conn, config, audit, boundaries
):
    item = seed_item(conn, dry_run=True, state="active")
    seed_session(conn, item, state="running", dry_run=True, pid=0)

    result = operations.abandon(context(conn, config, audit, boundaries), item)

    assert result.code == operations.EXIT_PRECONDITION
    text = "\n".join(result.lines)
    assert text.startswith(f"work item {item} is active; abandon requires ")
    assert "'interrupted'" in text
    assert f"robot-army cancel {item}" in text
    assert "<WorkItemState" not in text
    # Refused before anything was touched: the item and its session are as they were.
    assert db.get_work_item(conn, item).state is WorkItemState.ACTIVE
    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING


@pytest.mark.parametrize("state", REFUSED, ids=str)
def test_every_refusal_is_plain_words_and_changes_nothing(
    conn, config, audit, boundaries, state
):
    item = seed_item(conn, dry_run=True, state=str(state))

    result = operations.abandon(context(conn, config, audit, boundaries), item)

    assert result.code == operations.EXIT_PRECONDITION
    text = "\n".join(result.lines)
    assert f"work item {item} is {state}; abandon requires " in text
    assert "<" not in text and "illegal" not in text
    assert result.data == {"item_id": item, "state": str(state)}
    assert db.get_work_item(conn, item).state is state


def test_cancel_then_abandon_is_the_sequence_the_refusal_names(
    conn, config, audit, boundaries
):
    """The remedy the message gives has to work, or it is a second wrong answer."""
    item = seed_item(conn, dry_run=True, state="active")
    seed_session(conn, item, state="running", dry_run=True, pid=0)
    ctx = context(conn, config, audit, boundaries)

    assert operations.abandon(ctx, item).code == operations.EXIT_PRECONDITION
    assert operations.cancel(ctx, item, force=True).code == operations.EXIT_OK
    assert operations.abandon(ctx, item).code == operations.EXIT_OK
    assert db.get_work_item(conn, item).state is WorkItemState.ABANDONED


def test_abandoning_an_abandoned_item_is_still_not_refused(conn, config, audit, boundaries):
    """The gate treats re-asserting a held state as a no-op; the pre-check must not turn
    that into a refusal it never was."""
    item = seed_item(conn, dry_run=True, state="abandoned")
    result = operations.abandon(context(conn, config, audit, boundaries), item)
    assert result.code == operations.EXIT_OK


def test_the_gates_own_message_carries_no_repr():
    """Other surfaces print the gate's message as it stands — the web does when a concurrent
    command wins a race — so it has to be readable too."""
    exc = IllegalTransition("work_item", 20, WorkItemState.ACTIVE, WorkItemState.ABANDONED)
    assert str(exc) == "illegal work_item transition for 20: 'active' -> 'abandoned'"
