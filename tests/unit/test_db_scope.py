"""The FR-056 default query scope (T027).

Simulated rows are absent unless explicitly requested. This is enforced by the accessor
*signatures* rather than by convention — a convention would drift, a default argument
does not — so these tests exercise the accessors rather than the SQL.
"""

from __future__ import annotations

import inspect

import pytest
from tests.conftest import seed_item

from robot_army import db
from robot_army.states import SessionState, WorkItemState

LISTING_ACCESSORS = [
    db.list_work_items,
    db.list_sessions,
    db.count_work_items_by_state,
]


@pytest.mark.parametrize("accessor", LISTING_ACCESSORS, ids=lambda f: f.__name__)
def test_every_listing_accessor_defaults_to_excluding_simulated(accessor):
    parameter = inspect.signature(accessor).parameters.get("include_simulated")
    assert parameter is not None, f"{accessor.__name__} must carry include_simulated"
    assert parameter.default is False
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
        "it must be keyword-only, so including simulated rows is always an explicit act "
        "written out at the call site"
    )


def _seed_both(conn) -> tuple[int, int]:
    live = seed_item(conn, issue_number=1, dry_run=False)
    simulated = seed_item(conn, issue_number=2, dry_run=True)
    return live, simulated


def test_work_item_listings_exclude_simulated_by_default(conn):
    live, simulated = _seed_both(conn)
    assert [i.id for i in db.list_work_items(conn)] == [live]
    assert [i.id for i in db.list_work_items(conn, include_simulated=True)] == [live, simulated]


def test_counts_exclude_simulated_by_default(conn):
    _seed_both(conn)
    assert db.count_work_items_by_state(conn) == {"discovered": 1}
    assert db.count_work_items_by_state(conn, include_simulated=True) == {"discovered": 2}


def test_session_listings_exclude_simulated_by_default(conn):
    live, simulated = _seed_both(conn)
    with db.transaction(conn):
        db.insert_session(conn, work_item_id=live, session_id="s-live", attempt=1, dry_run=False)
        db.insert_session(conn, work_item_id=simulated, session_id="s-sim", attempt=1, dry_run=True)
    assert [s.session_id for s in db.list_sessions(conn)] == ["s-live"]
    assert [s.session_id for s in db.list_sessions(conn, include_simulated=True)] == [
        "s-live",
        "s-sim",
    ]


def test_fetching_by_id_needs_no_flag(conn):
    """An explicit id is already an explicit act; requiring a second one would be noise."""
    _, simulated = _seed_both(conn)
    item = db.get_work_item(conn, simulated)
    assert item is not None and item.dry_run is True


def test_the_concurrency_cap_counts_simulated_sessions(conn):
    """The one place where including simulated rows is the default, and it is FR-055
    rather than an oversight: a simulated session burns the same subscription quota."""
    live, simulated = _seed_both(conn)
    with db.transaction(conn):
        db.insert_session(conn, work_item_id=live, session_id="s-live", attempt=1, dry_run=False)
        db.insert_session(conn, work_item_id=simulated, session_id="s-sim", attempt=1, dry_run=True)
    assert db.count_live_sessions(conn) == 2


def test_ended_sessions_do_not_occupy_a_slot(conn):
    live, _ = _seed_both(conn)
    with db.transaction(conn):
        row_id = db.insert_session(
            conn, work_item_id=live, session_id="s-1", attempt=1, dry_run=False
        )
    conn.execute(
        "UPDATE sessions SET state = ? WHERE id = ?", (str(SessionState.EXITED_CLEAN), row_id)
    )
    assert db.count_live_sessions(conn) == 0


def test_purge_removes_only_simulated_rows(conn):
    """FR-058, and it covers ``cards`` too since milestone 003: a simulated card row is a
    dry-run row like any other, and leaving it behind would make ``purge-simulated`` a
    verb that only mostly does what its name says."""
    live, simulated = _seed_both(conn)
    with db.transaction(conn):
        db.insert_session(conn, work_item_id=live, session_id="s-live", attempt=1, dry_run=False)
        db.insert_session(conn, work_item_id=simulated, session_id="s-sim", attempt=1, dry_run=True)
        for dry_run in (False, True):
            db.insert_card(
                conn,
                board_id="b1",
                card_id="c1",
                card_url="https://trello.com/c/c1",
                title="a card",
                body="",
                dry_run=dry_run,
            )

    with db.transaction(conn):
        purged = db.purge_simulated(conn)
    assert purged == {"work_items": 1, "sessions": 1, "cards": 1}
    assert [i.id for i in db.list_work_items(conn, include_simulated=True)] == [live]
    assert [s.session_id for s in db.list_sessions(conn, include_simulated=True)] == ["s-live"]
    remaining = db.list_cards(conn, include_simulated=True)
    assert [c.dry_run for c in remaining] == [False]


def test_state_filtering_composes_with_the_scope(conn):
    live, simulated = _seed_both(conn)
    conn.execute(
        "UPDATE work_items SET state = ? WHERE id IN (?, ?)",
        (str(WorkItemState.READY), live, simulated),
    )
    assert [i.id for i in db.list_work_items(conn, states=[WorkItemState.READY])] == [live]
    assert [
        i.id
        for i in db.list_work_items(
            conn, states=[WorkItemState.READY], include_simulated=True
        )
    ] == [live, simulated]


def test_state_changes_cannot_bypass_the_transition_gate(conn):
    """``update_*_columns`` refuses ``state`` outright, so there is exactly one way in."""
    live, _ = _seed_both(conn)
    with pytest.raises(ValueError, match="transition_work_item"):
        db.update_work_item_columns(conn, live, state="active")
    with db.transaction(conn):
        row_id = db.insert_session(
            conn, work_item_id=live, session_id="s-x", attempt=1, dry_run=False
        )
    with pytest.raises(ValueError, match="transition_session"):
        db.update_session_columns(conn, row_id, state="running")
