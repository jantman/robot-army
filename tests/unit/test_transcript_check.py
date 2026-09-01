"""The transcript question: when it is asked, and what happens to the answer (issue #58).

The defect this file exists for did not look like a defect. `no_transcript` fired on the
first live dispatch ever performed, against a session whose transcript was healthy and
complete eight seconds later, because the check ran one line after the session was confirmed
running -- before the worker had written anything. It would have fired on every dispatch
forever, and it is the detector for the one failure that hides best: a session that runs,
exits 0, and can never be resumed.

The regression test the issue asks for is
``test_a_transcript_that_appears_shortly_afterwards_is_never_reported``. The rest of this
file is the surrounding contract, because the old inline check had unit coverage that
asserted only the failing case and that is precisely how it shipped.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.conftest import seed_item, write_transcript

from robot_army import db, reconcile, sessions
from robot_army.audit import read_records
from robot_army.states import SessionState, WorkItemState

GRACE = reconcile.TRANSCRIPT_GRACE_SECONDS


def _stamp(seconds_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def session_row(
    conn,
    *,
    session_id: str = "s-1",
    pid: int | None = 4242,
    confirmed_s_ago: float | None = 0,
    started_s_ago: float = 0,
    dry_run: bool = False,
    state: SessionState = SessionState.RUNNING,
    ended: bool = False,
) -> int:
    """One session row with a stated age, returning its row id.

    ``pid`` defaults to a real-looking value because that is what a real session has. A
    caller meaning "simulated" passes ``pid=0``, which is the only shape the simulated host
    can produce: the column is written from whatever ``confirm_session()`` returned.
    """
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE), dry_run=dry_run)
    with db.transaction(conn):
        row_id = db.insert_session(
            conn,
            work_item_id=item_id,
            session_id=session_id,
            attempt=1,
            dry_run=dry_run,
        )
    conn.execute(
        "UPDATE sessions SET state = ?, pid = ?, proc_start = '777', started_at = ?, "
        "confirmed_at = ?, ended_at = ? WHERE id = ?",
        (
            str(state),
            pid,
            _stamp(started_s_ago),
            None if confirmed_s_ago is None else _stamp(confirmed_s_ago),
            _stamp(0) if ended else None,
            row_id,
        ),
    )
    return row_id


def checked_at(conn, row_id: int) -> str | None:
    return conn.execute(
        "SELECT transcript_checked_at FROM sessions WHERE id = ?", (row_id,)
    ).fetchone()["transcript_checked_at"]


def kinds(conn) -> list[str]:
    return [a.kind for a in db.list_anomalies(conn, unacknowledged_only=False)]


def actions(audit) -> list[str]:
    # R14 flushes per line, so nothing is buffered and there is nothing to flush here.
    return [rec["action"] for rec, _ in read_records(audit.log_dir) if rec]


# -- US1: a healthy dispatch raises no anomaly -------------------------------


def test_a_young_session_with_no_transcript_yet_is_left_completely_alone(conn, audit):
    """The reported bug, at the moment it used to fire.

    Nothing is written -- not an anomaly, not the column, not a record. Writing the column
    here would be the same defect with the opposite sign: the question would be closed
    before the worker had a chance to answer it, and a genuinely unresumable session would
    go unreported forever.
    """
    row_id = session_row(conn, confirmed_s_ago=1)

    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (0, 0)
    assert kinds(conn) == []
    assert checked_at(conn, row_id) is None, "the question must still be open"
    assert not [a for a in actions(audit) if a.startswith("session.transcript")]


def test_a_young_session_is_asked_again_on_the_next_pass(conn, audit):
    """The corollary: leaving it alone is not the same as forgetting it."""
    row_id = session_row(conn, confirmed_s_ago=1)

    for _ in range(5):
        reconcile._sweep_transcripts(conn, audit=audit)
    assert checked_at(conn, row_id) is None
    assert kinds(conn) == []

    # Time passes and the transcript still has not appeared.
    conn.execute(
        "UPDATE sessions SET confirmed_at = ? WHERE id = ?", (_stamp(GRACE + 1), row_id)
    )
    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (1, 1)


def test_a_transcript_that_appears_shortly_afterwards_is_never_reported(
    conn, audit, transcripts
):
    """**The regression test issue #58 asks for by name.**

    A dispatch whose transcript appears seconds later must raise no anomaly at any point.
    The old check ran before the worker had written anything, so this sequence -- the
    ordinary, healthy one -- produced an anomaly every single time.
    """
    row_id = session_row(conn, session_id="healthy", confirmed_s_ago=1)

    # Pass one: too early to say anything, and it says nothing.
    reconcile._sweep_transcripts(conn, audit=audit)
    assert kinds(conn) == []

    # The worker gets to its first write, as it always does.
    write_transcript(transcripts, "healthy")

    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (1, 0)
    assert kinds(conn) == []
    assert checked_at(conn, row_id) is not None
    assert "session.transcript_found" in actions(audit)


def test_a_session_with_a_transcript_is_never_examined_again(conn, audit, transcripts):
    """Once found, the question is closed: no repeated globbing, no second answer."""
    session_row(conn, session_id="found", confirmed_s_ago=1)
    write_transcript(transcripts, "found")
    reconcile._sweep_transcripts(conn, audit=audit)

    calls: list[str] = []

    def spy(session_id: str, **kwargs):
        calls.append(session_id)
        return True

    original = sessions.transcript_exists
    reconcile.sessions.transcript_exists = spy
    try:
        checked, reported = reconcile._sweep_transcripts(conn, audit=audit)
    finally:
        reconcile.sessions.transcript_exists = original

    assert (checked, reported) == (0, 0)
    assert calls == [], "a closed question must not reach the filesystem again"


def test_a_long_running_healthy_session_raises_nothing_on_any_pass(
    conn, audit, transcripts
):
    session_row(conn, session_id="long", confirmed_s_ago=GRACE * 10)
    write_transcript(transcripts, "long")

    for _ in range(10):
        reconcile._sweep_transcripts(conn, audit=audit)

    assert kinds(conn) == []


def test_a_session_that_ended_cleanly_with_a_transcript_is_not_reported(
    conn, audit, transcripts
):
    session_row(
        conn,
        session_id="done",
        confirmed_s_ago=GRACE + 60,
        state=SessionState.EXITED_CLEAN,
        ended=True,
    )
    write_transcript(transcripts, "done")

    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (1, 0)
    assert kinds(conn) == []


# -- US2: a genuinely unresumable session is still reported ------------------


def test_a_session_with_no_transcript_after_the_grace_period_is_reported(conn, audit):
    row_id = session_row(conn, session_id="silent", confirmed_s_ago=GRACE + 1)

    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (1, 1)
    anomalies = db.list_anomalies(conn)
    assert [a.kind for a in anomalies] == ["no_transcript"]
    assert anomalies[0].entity_type == "session"
    assert anomalies[0].entity_id == "silent"
    assert checked_at(conn, row_id) is not None
    assert "session.transcript_missing" in actions(audit)


def test_the_report_names_the_item_the_wait_and_the_state(conn, audit):
    """SC-006: the maintainer must be able to judge, from the record alone, whether the
    system waited long enough before concluding the transcript was missing."""
    session_row(conn, session_id="silent", confirmed_s_ago=GRACE + 30)

    reconcile._sweep_transcripts(conn, audit=audit)

    detail = db.list_anomalies(conn)[0].detail_obj
    assert detail["item_id"] is not None
    assert detail["waited_s"] >= GRACE
    assert detail["session_state"] == "running"


def test_the_note_names_both_causes_and_asserts_neither(conn, audit):
    """FR-009. The old note sent the reader hunting ``CLAUDE_CODE_*`` in the terminal
    daemon's environment. On the machine where it fired that environment was verifiably
    clean, so the guidance led away from the answer rather than toward it. The check
    observes an absence and cannot tell the two causes apart; it must say so."""
    session_row(conn, session_id="silent", confirmed_s_ago=GRACE + 1)

    reconcile._sweep_transcripts(conn, audit=audit)

    note = db.list_anomalies(conn)[0].detail_obj["note"]
    assert "doctor" in note, "one cause must name the command that settles it"
    assert "exit" in note, "the other cause must name the record that settles it"
    assert "restart" in note.lower(), "the instruction true either way"
    assert "cannot tell" in note or "cannot distinguish" in note


def test_a_reported_session_is_never_reported_twice(conn, audit):
    session_row(conn, session_id="silent", confirmed_s_ago=GRACE + 1)

    for _ in range(10):
        reconcile._sweep_transcripts(conn, audit=audit)

    assert kinds(conn) == ["no_transcript"]


def test_acknowledging_the_anomaly_does_not_let_it_be_raised_again(conn, audit):
    """The case the anomalies table cannot cover on its own, and the reason the state is a
    column rather than a query.

    The partial unique index dedupes only *unacknowledged* rows -- deliberately, so a
    genuinely new occurrence can be recorded after the maintainer has dealt with the last
    one. But a session's transcript is not a recurring event: it is one question with one
    answer, and acknowledging the answer must not re-open it.
    """
    session_row(conn, session_id="silent", confirmed_s_ago=GRACE + 1)
    reconcile._sweep_transcripts(conn, audit=audit)
    with db.transaction(conn):
        db.acknowledge_anomaly(conn, db.list_anomalies(conn)[0].id)

    for _ in range(5):
        reconcile._sweep_transcripts(conn, audit=audit)

    assert kinds(conn) == ["no_transcript"], "one question, one answer"


def test_a_session_that_ended_without_a_transcript_is_reported(conn, audit):
    """FR-004/C5: ending is not evidence either way. Waiting for a session to end before
    judging it would leave a long-running unresumable session unreported for hours."""
    session_row(
        conn,
        session_id="died",
        confirmed_s_ago=GRACE + 10,
        state=SessionState.EXITED_ERROR,
        ended=True,
    )

    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (1, 1)
    assert db.list_anomalies(conn)[0].detail_obj["session_state"] == "exited_error"


def test_an_undateable_session_is_reported_with_an_unknown_wait(conn, audit):
    """A row we cannot date is one we cannot vouch for -- the precedent the
    ``dispatching_timeout`` sweep already set. ``waited_s: null`` says the wait is unknown
    rather than inventing a number for it."""
    row_id = session_row(conn, session_id="undateable", confirmed_s_ago=None)
    conn.execute("UPDATE sessions SET started_at = 'not-a-timestamp' WHERE id = ?", (row_id,))

    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (1, 1)
    assert db.list_anomalies(conn)[0].detail_obj["waited_s"] is None


def test_the_clock_starts_at_confirmation_not_at_insertion(conn, audit):
    """The session row is written *before* the process launches, and confirmation can take
    up to ``confirm_timeout_seconds``. Measuring from ``started_at`` would charge the
    session for time during which it did not exist."""
    session_row(
        conn,
        session_id="slow-to-confirm",
        started_s_ago=GRACE + 30,
        confirmed_s_ago=GRACE - 30,
    )

    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (0, 0), "still inside the grace period, measured honestly"
    assert kinds(conn) == []


def test_a_report_and_its_answer_commit_together(conn, audit, monkeypatch):
    """C7, and the Principle IV answer for this feature.

    If the anomaly landed and the column write did not, the next pass would report the same
    session again -- and again -- which is the multiplied-anomaly defect in a slower form.
    If the column landed and the anomaly did not, a genuinely unresumable session would be
    silently marked answered and never reported at all. One transaction makes both
    impossible.
    """
    session_row(conn, session_id="interrupted", confirmed_s_ago=GRACE + 1)

    real_mark = db.mark_transcript_checked

    def explode(*args, **kwargs):
        raise RuntimeError("killed mid-pass")

    monkeypatch.setattr(db, "mark_transcript_checked", explode)
    with pytest.raises(RuntimeError):
        reconcile._sweep_transcripts(conn, audit=audit)

    assert kinds(conn) == [], "the anomaly must have rolled back with the column write"

    monkeypatch.setattr(db, "mark_transcript_checked", real_mark)
    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (1, 1)
    assert kinds(conn) == ["no_transcript"], "reported exactly once, not zero times, not twice"


# -- US3: a rehearsal can exercise the detector ------------------------------


def test_a_session_that_never_ran_a_process_is_skipped_without_touching_the_disk(
    conn, audit, monkeypatch
):
    """A simulated session has no process, so nothing could have written a transcript and
    its absence says nothing. Checked before the filesystem is consulted, deliberately:
    reaching for a file to learn what the record already says would be a slower way to be
    wrong."""
    calls: list[str] = []
    monkeypatch.setattr(
        reconcile.sessions,
        "transcript_exists",
        lambda session_id, **kw: calls.append(session_id) or False,
    )
    row_id = session_row(
        conn, session_id="simulated", pid=0, dry_run=True, confirmed_s_ago=GRACE * 10
    )

    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (1, 0)
    assert kinds(conn) == []
    assert calls == [], "row 1 must precede row 2"
    assert checked_at(conn, row_id) is not None
    assert "session.transcript_skipped" in actions(audit)


def test_a_session_with_no_pid_recorded_at_all_is_skipped(conn, audit):
    """``NULL`` and ``0`` mean the same thing here -- no process was ever recorded."""
    session_row(conn, session_id="no-pid", pid=None, confirmed_s_ago=GRACE * 10)

    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (1, 0)
    assert kinds(conn) == []


def test_a_no_remote_session_with_a_real_process_is_judged_like_a_live_one(conn, audit):
    """**The assertion whose absence made every rehearsal blind to this detector.**

    ``dry_run`` answers "was the effect level below ``live``", which is true at
    ``no-remote`` -- where the session host is real, the process is real, and the transcript
    is real. The old check keyed its exemption on that flag, so no rehearsal could reach the
    detector at all and the first observation of a defect in it was a live dispatch against
    a real issue. Keying on the pid is the same correction issue #33 already made one sweep
    over in this module.
    """
    session_row(
        conn,
        session_id="no-remote",
        pid=4242,
        dry_run=True,
        confirmed_s_ago=GRACE + 1,
    )

    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (1, 1)
    assert kinds(conn) == ["no_transcript"]


def test_a_no_remote_session_that_wrote_a_transcript_is_satisfied_like_a_live_one(
    conn, audit, transcripts
):
    """The other half of the same guarantee: reachable does not mean always reported."""
    session_row(
        conn, session_id="no-remote-ok", pid=4242, dry_run=True, confirmed_s_ago=GRACE + 1
    )
    write_transcript(transcripts, "no-remote-ok")

    checked, reported = reconcile._sweep_transcripts(conn, audit=audit)

    assert (checked, reported) == (1, 0)
    assert kinds(conn) == []
