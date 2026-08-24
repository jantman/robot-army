"""Exit-code classification, every row of the mapping table (T075, T079).

The mapping is data-model.md's, measured in M0 as E3.3. Two rows carry judgement rather
than mechanics and are called out below: the configuration-error codes, and the fact that
a clean exit with the issue still open is a **resting state, not an anomaly**.
"""

from __future__ import annotations

import pytest
from tests.conftest import seed_item

from robot_army import db, spool
from robot_army.states import SessionState, WorkItemState, classify_exit

# Exactly the table in data-model.md and contracts/exit-record.md.
TABLE = [
    (0, SessionState.EXITED_CLEAN, WorkItemState.AWAITING_REVIEW, None),
    (1, SessionState.EXITED_ERROR, WorkItemState.FAILED, None),
    (126, SessionState.EXITED_ERROR, WorkItemState.FAILED, None),
    (127, SessionState.EXITED_ERROR, WorkItemState.FAILED, None),
    (129, SessionState.EXITED_ERROR, WorkItemState.INTERRUPTED, 1),
    (130, SessionState.EXITED_ERROR, WorkItemState.INTERRUPTED, 2),
    (137, SessionState.EXITED_ERROR, WorkItemState.INTERRUPTED, 9),
    (143, SessionState.EXITED_ERROR, WorkItemState.INTERRUPTED, 15),
    (191, SessionState.EXITED_ERROR, WorkItemState.INTERRUPTED, 63),
    (2, SessionState.EXITED_ERROR, WorkItemState.FAILED, None),
    (42, SessionState.EXITED_ERROR, WorkItemState.FAILED, None),
    (192, SessionState.EXITED_ERROR, WorkItemState.FAILED, None),
    (255, SessionState.EXITED_ERROR, WorkItemState.FAILED, None),
]


@pytest.mark.parametrize(
    ("code", "session_state", "item_state", "signal"),
    TABLE,
    ids=[str(row[0]) for row in TABLE],
)
def test_every_row_of_the_mapping_table(code, session_state, item_state, signal):
    assert classify_exit(code) == (session_state, item_state, signal)


def test_configuration_errors_are_distinguished_from_other_failures():
    """1, 126 and 127 mean the worker never ran, so retrying without a config change is
    pointless — which is why they are singled out rather than lumped with "non-zero"."""
    for code in (1, 126, 127):
        assert classify_exit(code)[1] is WorkItemState.FAILED
        assert classify_exit(code)[2] is None, "these are not signal deaths"


def test_signal_deaths_are_interrupted_not_failed():
    """Killed externally is usually resumable and is not a failure *of the work item*."""
    for code in (129, 137, 143):
        assert classify_exit(code)[1] is WorkItemState.INTERRUPTED
        assert classify_exit(code)[2] == code - 128


def test_the_128_boundaries_are_exclusive():
    """``128`` is not ``128+N`` for any signal, and ``192`` is past the signal range."""
    assert classify_exit(128)[2] is None
    assert classify_exit(192)[2] is None
    assert classify_exit(129)[2] == 1
    assert classify_exit(191)[2] == 63


def test_exit_zero_with_the_issue_still_open_produces_no_anomaly(conn, audit, layout):
    """T079. The maintainer may have typed ``/exit`` because they went to lunch. Nothing
    should nag about it — ``awaiting_review`` is a resting state."""
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE))
    with db.transaction(conn):
        row_id = db.insert_session(
            conn, work_item_id=item_id, session_id="s-1", attempt=1, dry_run=False
        )
    conn.execute("UPDATE sessions SET state = 'running' WHERE id = ?", (row_id,))

    from tests.conftest import write_exit_record

    write_exit_record(layout.spool_dir, session_id="s-1", exit_code=0)
    result = spool.drain(conn, audit=audit, layout=layout)

    assert result.applied == 1
    assert db.get_work_item(conn, item_id).state is WorkItemState.AWAITING_REVIEW
    assert db.list_anomalies(conn) == [], "a clean exit is not an anomaly"


def test_a_signal_death_records_the_decoded_signal_on_the_session(conn, audit, layout):
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE))
    with db.transaction(conn):
        row_id = db.insert_session(
            conn, work_item_id=item_id, session_id="s-9", attempt=1, dry_run=False
        )
    conn.execute("UPDATE sessions SET state = 'running' WHERE id = ?", (row_id,))

    from tests.conftest import write_exit_record

    write_exit_record(layout.spool_dir, session_id="s-9", exit_code=137, signal=9)
    spool.drain(conn, audit=audit, layout=layout)

    session = db.get_session(conn, "s-9")
    assert session is not None
    assert (session.exit_code, session.signal) == (137, 9)
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED


def test_an_absent_signal_field_falls_back_to_deriving_it(conn, audit, layout):
    """FR-032 puts the decode in the wrapper, at the point where the information is
    unambiguous. But the field can be absent — an older wrapper, a hand-written record —
    and losing the signal entirely is worse than deriving it, so the daemon derives."""
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE))
    with db.transaction(conn):
        row_id = db.insert_session(
            conn, work_item_id=item_id, session_id="s-x", attempt=1, dry_run=False
        )
    conn.execute("UPDATE sessions SET state = 'running' WHERE id = ?", (row_id,))

    from tests.conftest import write_exit_record

    write_exit_record(layout.spool_dir, session_id="s-x", exit_code=137, signal=None)
    spool.drain(conn, audit=audit, layout=layout)
    session = db.get_session(conn, "s-x")
    assert session is not None
    assert session.signal == 9


def test_the_wrappers_decoded_signal_is_used_when_present(conn, audit, layout):
    """A wrapper that reports a signal outside our derivation is believed: it observed
    the death and we only saw a number."""
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE))
    with db.transaction(conn):
        row_id = db.insert_session(
            conn, work_item_id=item_id, session_id="s-y", attempt=1, dry_run=False
        )
    conn.execute("UPDATE sessions SET state = 'running' WHERE id = ?", (row_id,))

    from tests.conftest import write_exit_record

    write_exit_record(layout.spool_dir, session_id="s-y", exit_code=143, signal=15)
    spool.drain(conn, audit=audit, layout=layout)
    assert db.get_session(conn, "s-y").signal == 15
