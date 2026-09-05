"""Exit-record parsing, quarantine, and idempotency (T076, T077).

This code parses external input written by a bash script in a bare environment, so the
constitution requires failure-path tests. The behaviours asserted here are
contracts/exit-record.md's malformed-record table, verbatim.
"""

from __future__ import annotations

import json

import pytest
from tests.conftest import seed_item, write_exit_record

from robot_army import db, spool
from robot_army.spool import MalformedRecord, parse_record
from robot_army.states import SessionState, WorkItemState


def running_session(conn, session_id: str = "s-1", **kwargs) -> tuple[int, int]:
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE), **kwargs)
    with db.transaction(conn):
        row_id = db.insert_session(
            conn,
            work_item_id=item_id,
            session_id=session_id,
            attempt=1,
            dry_run=kwargs.get("dry_run", False),
        )
    conn.execute("UPDATE sessions SET state = 'running' WHERE id = ?", (row_id,))
    return item_id, row_id


# -- parsing ---------------------------------------------------------------


def test_a_valid_exit_record_parses():
    payload = parse_record(
        json.dumps(
            {
                "schema": 1,
                "event": "exit",
                "item": "42",
                "session_id": "abc",
                "ts": "2026-08-23T16:31:02Z",
                "exit": 0,
                "signal": None,
            }
        )
    )
    assert payload["session_id"] == "abc"


def test_unparseable_json_is_rejected_with_a_reason():
    with pytest.raises(MalformedRecord, match="unparseable JSON"):
        parse_record('{"schema": 1, "event": "exi')


def test_an_unknown_schema_is_rejected_rather_than_guessed_at():
    """Guessing is how a field that changed meaning corrupts state silently."""
    with pytest.raises(MalformedRecord, match="unknown schema"):
        parse_record(json.dumps({"schema": 99, "event": "exit", "session_id": "a", "exit": 0}))


def test_a_missing_schema_is_rejected():
    with pytest.raises(MalformedRecord, match="unknown schema"):
        parse_record(json.dumps({"event": "exit", "session_id": "a", "exit": 0}))


def test_an_unknown_event_is_rejected():
    with pytest.raises(MalformedRecord, match="unknown event"):
        parse_record(json.dumps({"schema": 1, "event": "wat", "session_id": "a"}))


def test_a_missing_session_id_is_rejected_because_it_is_the_join_key():
    with pytest.raises(MalformedRecord, match="session_id"):
        parse_record(json.dumps({"schema": 1, "event": "exit", "exit": 0}))


def test_a_non_integer_exit_is_rejected():
    with pytest.raises(MalformedRecord, match="exit must be an integer"):
        parse_record(json.dumps({"schema": 1, "event": "exit", "session_id": "a", "exit": "0"}))


def test_a_boolean_exit_is_rejected_despite_being_an_int_in_python():
    with pytest.raises(MalformedRecord, match="exit must be an integer"):
        parse_record(json.dumps({"schema": 1, "event": "exit", "session_id": "a", "exit": True}))


def test_a_json_array_is_rejected():
    with pytest.raises(MalformedRecord, match="expected a JSON object"):
        parse_record("[]")


def test_a_start_record_needs_no_exit_field():
    payload = parse_record(json.dumps({"schema": 1, "event": "start", "session_id": "a"}))
    assert payload["event"] == "start"


# -- draining --------------------------------------------------------------


def test_a_valid_record_is_applied_and_the_file_is_removed(conn, audit, layout):
    item_id, _ = running_session(conn)
    path = write_exit_record(layout.spool_dir, session_id="s-1", exit_code=0)

    result = spool.drain(conn, audit=audit, layout=layout)
    assert result.applied == 1
    assert not path.exists(), "the file is unlinked only after the transaction committed"
    assert db.get_session(conn, "s-1").state is SessionState.EXITED_CLEAN
    assert db.get_work_item(conn, item_id).state is WorkItemState.AWAITING_REVIEW


def test_applying_the_same_record_twice_is_a_no_op(conn, audit, layout):
    """T077. A crash between apply and unlink causes reapplication, so this is not a
    theoretical case — it is the expected consequence of the ordering that makes the
    record safe in the first place."""
    item_id, _ = running_session(conn)
    write_exit_record(layout.spool_dir, session_id="s-1", exit_code=0)
    first = spool.drain(conn, audit=audit, layout=layout)
    assert first.applied == 1

    # Simulate the crash: the record is back, because it was never unlinked.
    write_exit_record(layout.spool_dir, session_id="s-1", exit_code=0)
    second = spool.drain(conn, audit=audit, layout=layout)

    assert second.applied == 0
    assert second.duplicates == 1
    assert db.get_work_item(conn, item_id).state is WorkItemState.AWAITING_REVIEW
    assert len(db.list_sessions_for_item(conn, item_id)) == 1


def test_a_conflicting_second_record_does_not_overwrite_the_first(conn, audit, layout):
    """Idempotency is on ``(session_id, event)``, so a *different* exit code arriving for
    an already-settled session must not silently rewrite history."""
    item_id, _ = running_session(conn)
    write_exit_record(layout.spool_dir, session_id="s-1", exit_code=0)
    spool.drain(conn, audit=audit, layout=layout)

    write_exit_record(layout.spool_dir, session_id="s-1", exit_code=137, signal=9)
    spool.drain(conn, audit=audit, layout=layout)

    session = db.get_session(conn, "s-1")
    assert session.exit_code == 0
    assert session.state is SessionState.EXITED_CLEAN
    assert db.get_work_item(conn, item_id).state is WorkItemState.AWAITING_REVIEW


def test_a_truncated_record_is_quarantined_never_deleted(conn, audit, layout):
    running_session(conn)
    path = write_exit_record(layout.spool_dir, session_id="s-1", truncate=True)

    result = spool.drain(conn, audit=audit, layout=layout)
    assert result.quarantined == 1
    assert not path.exists()
    quarantined = list(layout.spool_rejected_dir.glob("*.json"))
    assert len(quarantined) == 1, "the evidence must survive"

    anomalies = db.list_anomalies(conn)
    assert [a.kind for a in anomalies] == ["malformed_exit_record"]
    assert "unparseable JSON" in anomalies[0].detail_obj["reason"]


def test_an_unknown_schema_is_quarantined(conn, audit, layout):
    running_session(conn)
    write_exit_record(layout.spool_dir, session_id="s-1", schema=7)
    result = spool.drain(conn, audit=audit, layout=layout)
    assert result.quarantined == 1
    assert db.list_anomalies(conn)[0].kind == "malformed_exit_record"


def test_quarantining_twice_does_not_clobber_the_first_file(conn, audit, layout):
    running_session(conn)
    write_exit_record(layout.spool_dir, session_id="s-1", truncate=True)
    spool.drain(conn, audit=audit, layout=layout)
    write_exit_record(layout.spool_dir, session_id="s-1", truncate=True)
    spool.drain(conn, audit=audit, layout=layout)
    assert len(list(layout.spool_rejected_dir.glob("*.json"))) == 2


def test_a_record_for_an_unknown_session_becomes_an_orphan_and_is_kept(conn, audit, layout):
    """This is evidence of a session the daemon lost track of; discarding it would
    destroy the evidence."""
    running_session(conn)
    path = write_exit_record(layout.spool_dir, session_id="never-seen", exit_code=0)

    result = spool.drain(conn, audit=audit, layout=layout)
    assert result.orphaned == 1
    assert path.exists(), "an orphan record is kept, not unlinked"
    assert db.list_anomalies(conn)[0].kind == "orphan_exit_record"


def test_a_repeated_orphan_does_not_multiply_anomalies(conn, audit, layout):
    """The record stays on disk and is re-read every tick; the partial unique index is
    what keeps that from becoming a row per tick."""
    write_exit_record(layout.spool_dir, session_id="never-seen", exit_code=0)
    for _ in range(4):
        spool.drain(conn, audit=audit, layout=layout)
    assert len(db.list_anomalies(conn)) == 1


def test_an_exit_with_no_prior_start_is_applied_anyway(conn, audit, layout):
    """A missing start is worth an audit line, but the outcome is the valuable part."""
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE))
    with db.transaction(conn):
        db.insert_session(
            conn, work_item_id=item_id, session_id="s-2", attempt=1, dry_run=False
        )
    # State is still `starting` — no start record ever arrived.
    write_exit_record(layout.spool_dir, session_id="s-2", exit_code=0)
    result = spool.drain(conn, audit=audit, layout=layout)

    assert result.applied == 1
    assert db.get_session(conn, "s-2").state is SessionState.EXITED_CLEAN
    assert db.get_work_item(conn, item_id).state is WorkItemState.AWAITING_REVIEW
    audit.close()
    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    assert "spool.exit_without_start" in text


def test_a_start_record_moves_a_session_to_running(conn, audit, layout):
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE))
    with db.transaction(conn):
        db.insert_session(
            conn, work_item_id=item_id, session_id="s-3", attempt=1, dry_run=False
        )
    write_exit_record(layout.spool_dir, session_id="s-3", event="start")
    assert spool.drain(conn, audit=audit, layout=layout).applied == 1
    assert db.get_session(conn, "s-3").state is SessionState.RUNNING


def test_a_start_record_applied_twice_is_a_no_op(conn, audit, layout):
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE))
    with db.transaction(conn):
        db.insert_session(
            conn, work_item_id=item_id, session_id="s-4", attempt=1, dry_run=False
        )
    write_exit_record(layout.spool_dir, session_id="s-4", event="start")
    spool.drain(conn, audit=audit, layout=layout)
    write_exit_record(layout.spool_dir, session_id="s-4", event="start")
    assert spool.drain(conn, audit=audit, layout=layout).duplicates == 1


def test_draining_an_empty_spool_is_free_and_silent(conn, audit, layout):
    result = spool.drain(conn, audit=audit, layout=layout)
    assert result.total == 0
    audit.close()
    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    assert "spool.drain" not in text, "an empty drain must not add a record every 5 seconds"


def test_draining_a_missing_spool_directory_is_not_an_error(conn, audit, tmp_path):
    from robot_army.paths import Layout

    layout = Layout(state_dir=tmp_path / "absent", socket_dir=tmp_path / "run")
    assert spool.drain(conn, audit=audit, layout=layout).total == 0


def test_one_bad_record_does_not_stop_the_drain(conn, audit, layout):
    item_id, _ = running_session(conn, session_id="s-good")
    write_exit_record(layout.spool_dir, session_id="s-bad", truncate=True)
    write_exit_record(layout.spool_dir, session_id="s-good", exit_code=0)

    result = spool.drain(conn, audit=audit, layout=layout)
    assert result.quarantined == 1
    assert result.applied == 1
    assert db.get_work_item(conn, item_id).state is WorkItemState.AWAITING_REVIEW


# -- a record that arrives after the session was retired (issue #138) ---------


def test_an_exit_record_arriving_after_the_row_was_closed_settles_and_is_unlinked(
    conn, audit, layout
):
    """The interruption path retirement makes routine, pinned rather than assumed.

    Retirement terminates a worker and closes its row as ``lost``. The wrapper traps
    nothing and SIGTERM ends bash outright, so it almost certainly writes no exit record —
    but "almost certainly" is a race, not a guarantee, and if one does arrive it must not
    attempt a transition out of a state the machine calls terminal.

    The property this asserts already held before retirement existed: ``_already_applied``
    treats every terminal session state, ``lost`` included, as "this exit is already
    accounted for". Planning for this feature predicted a bug here and was wrong; the test
    stays because retirement turns a rare race into a routine one, and the cost of that
    prediction being right later is a spool file retried on every tick forever.
    """
    running_session(conn)
    with db.transaction(conn):
        conn.execute(
            "UPDATE sessions SET state = 'lost', ended_at = '2026-09-05T00:00:00Z' "
            "WHERE session_id = ?",
            ("s-1",),
        )
    path = write_exit_record(layout.spool_dir, session_id="s-1", exit_code=0)

    result = spool.drain(conn, audit=audit, layout=layout)

    assert result.duplicates == 1
    assert result.applied == 0
    assert not path.exists(), (
        "a late record must be unlinked; leaving it makes the drain retry it every tick "
        "forever and log an error each time"
    )
    assert db.get_session(conn, "s-1").state is SessionState.LOST, (
        "the retirement's own settlement stands; a late record does not rewrite it"
    )


def test_a_late_record_does_not_move_the_work_item(conn, audit, layout):
    """A retired session's item is ``done``. A late exit record must not walk it anywhere
    else — and cannot, because the item transition is guarded on ``active``."""
    item_id, _ = running_session(conn)
    with db.transaction(conn):
        conn.execute(
            "UPDATE sessions SET state = 'lost' WHERE session_id = ?", ("s-1",)
        )
        conn.execute("UPDATE work_items SET state = 'done' WHERE id = ?", (item_id,))
    write_exit_record(layout.spool_dir, session_id="s-1", exit_code=1)

    spool.drain(conn, audit=audit, layout=layout)

    assert db.get_work_item(conn, item_id).state is WorkItemState.DONE


def test_a_start_record_arriving_after_the_row_was_closed_is_also_absorbed(
    conn, audit, layout
):
    """The other half of the same race, and the one an ``exit``-only guard would miss."""
    running_session(conn)
    with db.transaction(conn):
        conn.execute("UPDATE sessions SET state = 'lost' WHERE session_id = ?", ("s-1",))
    path = write_exit_record(layout.spool_dir, session_id="s-1", event="start")

    result = spool.drain(conn, audit=audit, layout=layout)

    assert result.duplicates == 1
    assert not path.exists()
    assert db.get_session(conn, "s-1").state is SessionState.LOST
