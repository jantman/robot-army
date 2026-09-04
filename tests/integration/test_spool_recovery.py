"""T078: a record written while the daemon is down is applied on next startup.

**This is the case an HTTP POST would have lost permanently** (research.md R5). Under the
planning document's design the exit record would vanish, silently downgrading a clean
completion into a phantom that reconciliation could only ever classify as ``interrupted``.
It is the single reason this milestone departs from planning §9, so it gets a test that
exercises the whole path — including the real bash wrapper writing the record.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from tests.conftest import seed_item, write_exit_record

from robot_army import db, spool
from robot_army.audit import AuditLog
from robot_army.states import SessionState, WorkItemState

WRAPPER = Path(__file__).resolve().parents[2] / "share" / "robot-army-session-wrapper.sh"

# The wrapper validates the session id's shape (RA-16), so the tests that drive the real
# script use ids of the shape the daemon actually issues --- `str(uuid.uuid4())` --- rather
# than readable stand-ins. The ids the *daemon-side* tests use are unconstrained and stay as
# they were; only what crosses into the script has to be real.
CLEAN_SESSION = "6f1c9d2a-4b3e-4a58-9c17-2d8e5f0a1b34"
FAILING_SESSION = "7a2d0e3b-5c4f-4b69-8d28-3e9f6a1b2c45"
KILLED_SESSION = "8b3e1f4c-6d5a-4c7a-9e39-4f0a7b2c3d56"


def active_session(conn, session_id: str = "s-1") -> int:
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE))
    with db.transaction(conn):
        row_id = db.insert_session(
            conn, work_item_id=item_id, session_id=session_id, attempt=1, dry_run=False
        )
    conn.execute("UPDATE sessions SET state = 'running' WHERE id = ?", (row_id,))
    return item_id


def test_a_record_written_while_the_daemon_is_down_is_applied_on_next_startup(
    layout, tmp_path
):
    # The daemon is not running. Nothing is listening; nothing could receive a POST.
    conn, _ = db.open_database(layout.db_path)
    item_id = active_session(conn)
    conn.close()

    write_exit_record(layout.spool_dir, session_id="s-1", exit_code=0)

    # ... time passes, the machine reboots, the daemon starts again ...
    conn, _ = db.open_database(layout.db_path)
    audit = AuditLog(layout.log_dir, component="daemon")
    result = spool.drain(conn, audit=audit, layout=layout)

    assert result.applied == 1
    assert db.get_work_item(conn, item_id).state is WorkItemState.AWAITING_REVIEW, (
        "the clean completion survived the daemon being down — the whole point of R5"
    )
    assert db.get_session(conn, "s-1").state is SessionState.EXITED_CLEAN
    audit.close()
    conn.close()


def test_several_records_accumulated_during_downtime_are_all_applied(layout):
    conn, _ = db.open_database(layout.db_path)
    items = []
    for index in range(3):
        item_id = seed_item(
            conn, issue_number=100 + index, state=str(WorkItemState.ACTIVE)
        )
        with db.transaction(conn):
            row_id = db.insert_session(
                conn,
                work_item_id=item_id,
                session_id=f"s-{index}",
                attempt=1,
                dry_run=False,
            )
        conn.execute("UPDATE sessions SET state = 'running' WHERE id = ?", (row_id,))
        items.append(item_id)
    conn.close()

    write_exit_record(layout.spool_dir, session_id="s-0", exit_code=0)
    write_exit_record(layout.spool_dir, session_id="s-1", exit_code=1)
    write_exit_record(layout.spool_dir, session_id="s-2", exit_code=137, signal=9)

    conn, _ = db.open_database(layout.db_path)
    audit = AuditLog(layout.log_dir)
    assert spool.drain(conn, audit=audit, layout=layout).applied == 3

    assert db.get_work_item(conn, items[0]).state is WorkItemState.AWAITING_REVIEW
    assert db.get_work_item(conn, items[1]).state is WorkItemState.FAILED
    assert db.get_work_item(conn, items[2]).state is WorkItemState.INTERRUPTED
    audit.close()
    conn.close()


def test_a_crash_between_apply_and_unlink_replays_harmlessly(conn, audit, layout):
    """The ordering that makes the record safe — commit, *then* unlink — necessarily
    means a crash in between causes reapplication. That must be a no-op."""
    item_id = active_session(conn)
    path = write_exit_record(layout.spool_dir, session_id="s-1", exit_code=0)

    payload = spool.parse_record(path.read_text(encoding="utf-8"))
    with db.transaction(conn):
        assert spool.apply_record(conn, audit, payload) == "applied"
    # ... killed here, before the unlink. The file is still on disk.
    assert path.exists()

    result = spool.drain(conn, audit=audit, layout=layout)
    assert result.duplicates == 1
    assert result.applied == 0
    assert db.get_work_item(conn, item_id).state is WorkItemState.AWAITING_REVIEW


# -- the real wrapper -------------------------------------------------------


@pytest.mark.skipif(not WRAPPER.exists(), reason="wrapper script not installed")
def test_the_real_wrapper_writes_a_record_the_daemon_can_apply(conn, audit, layout):
    """End to end across the process boundary: the actual bash script writes the file and
    the actual drain applies it. Anything less would test our idea of the format rather
    than the format."""
    item_id = active_session(conn, CLEAN_SESSION)

    result = subprocess.run(
        [
            "bash",
            str(WRAPPER),
            str(item_id),
            "--",
            "/bin/sh",
            "-c",
            "exit 0",
            "--session-id",
            CLEAN_SESSION,
        ],
        env={
            **os.environ,
            "ROBOT_ARMY_SESSION_ID": CLEAN_SESSION,
            "ROBOT_ARMY_SPOOL_DIR": str(layout.spool_dir),
            "ROBOT_ARMY_LOG_DIR": str(layout.session_log_dir),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    written = sorted(p.name for p in layout.spool_dir.glob("*.json"))
    assert written == [f"{CLEAN_SESSION}.exit.json", f"{CLEAN_SESSION}.start.json"]
    assert not list(layout.spool_dir.glob("*.tmp")), "no temporary file may be left behind"

    drained = spool.drain(conn, audit=audit, layout=layout)
    # Both records are consumed. Which of them counts as "applied" depends on the order
    # they are drained in — the session was already `running`, so a `start` arriving
    # after the `exit` is absorbed as a duplicate. That the outcome is the same either
    # way is the property worth having, and the next test pins it down directly.
    assert drained.applied + drained.duplicates == 2
    assert list(layout.spool_dir.glob("*.json")) == []
    assert db.get_work_item(conn, item_id).state is WorkItemState.AWAITING_REVIEW


@pytest.mark.skipif(not WRAPPER.exists(), reason="wrapper script not installed")
def test_the_real_wrapper_reports_a_non_zero_exit_and_its_own_status(conn, audit, layout):
    item_id = active_session(conn, FAILING_SESSION)
    result = subprocess.run(
        [
            "bash",
            str(WRAPPER),
            str(item_id),
            "--",
            "/bin/sh",
            "-c",
            "exit 42",
            "--session-id",
            FAILING_SESSION,
        ],
        env={
            **os.environ,
            "ROBOT_ARMY_SESSION_ID": FAILING_SESSION,
            "ROBOT_ARMY_SPOOL_DIR": str(layout.spool_dir),
            "ROBOT_ARMY_LOG_DIR": str(layout.session_log_dir),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 42, "the wrapper propagates the worker's exit status"

    record = json.loads(
        (layout.spool_dir / f"{FAILING_SESSION}.exit.json").read_text(encoding="utf-8")
    )
    assert record["schema"] == 1
    assert record["exit"] == 42
    assert record["signal"] is None
    assert record["session_id"] == FAILING_SESSION

    spool.drain(conn, audit=audit, layout=layout)
    assert db.get_work_item(conn, item_id).state is WorkItemState.FAILED


@pytest.mark.skipif(not WRAPPER.exists(), reason="wrapper script not installed")
def test_the_real_wrapper_decodes_a_signal_death(conn, audit, layout):
    """The wrapper decodes at the point where the information is unambiguous (FR-032)."""
    item_id = active_session(conn, KILLED_SESSION)
    subprocess.run(
        [
            "bash",
            str(WRAPPER),
            str(item_id),
            "--",
            "/bin/sh",
            "-c",
            "kill -TERM $$",
            "--session-id",
            KILLED_SESSION,
        ],
        env={
            **os.environ,
            "ROBOT_ARMY_SESSION_ID": KILLED_SESSION,
            "ROBOT_ARMY_SPOOL_DIR": str(layout.spool_dir),
            "ROBOT_ARMY_LOG_DIR": str(layout.session_log_dir),
        },
        capture_output=True,
        text=True,
    )
    record = json.loads(
        (layout.spool_dir / f"{KILLED_SESSION}.exit.json").read_text(encoding="utf-8")
    )
    assert record["exit"] == 143
    assert record["signal"] == 15

    spool.drain(conn, audit=audit, layout=layout)
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED


@pytest.mark.skipif(not WRAPPER.exists(), reason="wrapper script not installed")
def test_the_wrapper_does_not_exec_the_worker(conn):
    """``exec`` replaces the shell, and the exit code could then never be captured —
    which is the wrapper's entire reason to exist. The comment saying so is load-bearing
    documentation and must survive future editing."""
    text = WRAPPER.read_text(encoding="utf-8")
    assert "NOT using `exec`" in text
    body = text.split("# --- Run the payload")[1]
    assert 'exec "$@"' not in body


@pytest.mark.skipif(not WRAPPER.exists(), reason="wrapper script not installed")
def test_the_wrapper_needs_only_the_permitted_tools(conn):
    """It runs in a bare launch environment (M0 F19): bash, printf, date, mv, mkdir."""
    text = WRAPPER.read_text(encoding="utf-8")
    for forbidden in ("jq ", "curl ", "python3 ", "python "):
        assert forbidden not in text, f"the wrapper must not require {forbidden.strip()}"


@pytest.mark.skipif(not WRAPPER.exists(), reason="wrapper script not installed")
def test_the_wrapper_refuses_without_a_session_id(layout, tmp_path):
    """The session id is the join key; a record without one is unusable.

    Since RA-16 the environment is its only source, so an unset variable is now the whole
    of the missing-id case rather than one half of it."""
    result = subprocess.run(
        ["bash", str(WRAPPER), "1", "--", "/bin/true"],
        env={
            **{k: v for k, v in os.environ.items() if k != "ROBOT_ARMY_SESSION_ID"},
            "ROBOT_ARMY_SPOOL_DIR": str(layout.spool_dir),
            "ROBOT_ARMY_LOG_DIR": str(layout.session_log_dir),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "ROBOT_ARMY_SESSION_ID is not set" in result.stderr
    assert list(layout.spool_dir.glob("*.json")) == []


def test_the_outcome_does_not_depend_on_the_order_records_are_drained_in(layout, audit):
    """A `start` and an `exit` can land in either order — the wrapper writes them at
    different times and the daemon reads a directory, which has no ordering guarantee it
    is entitled to rely on."""
    outcomes = []
    for order in (("start", "exit"), ("exit", "start")):
        conn, _ = db.open_database(layout.db_path)
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM work_items")
        conn.execute("DELETE FROM anomalies")
        item_id = seed_item(conn, state=str(WorkItemState.ACTIVE))
        with db.transaction(conn):
            db.insert_session(
                conn, work_item_id=item_id, session_id="ordered", attempt=1, dry_run=False
            )
        for event in order:
            payload = spool.parse_record(
                (
                    write_exit_record(
                        layout.spool_dir, session_id="ordered", event=event, exit_code=0
                    )
                ).read_text(encoding="utf-8")
            )
            with db.transaction(conn):
                spool.apply_record(conn, audit, payload)
        outcomes.append(
            (
                db.get_work_item(conn, item_id).state,
                db.get_session(conn, "ordered").state,
            )
        )
        for path in layout.spool_dir.glob("*.json"):
            path.unlink()
        conn.close()

    assert outcomes[0] == outcomes[1]
    assert outcomes[0] == (WorkItemState.AWAITING_REVIEW, SessionState.EXITED_CLEAN)


def test_a_cancel_that_loses_the_race_to_the_exit_record_still_succeeds(
    conn, audit, layout, config
):
    """The daemon drains the spool in its own process while a cancel is in flight.

    A worker killed by the cancel's own SIGTERM writes an exit record on its way out; if
    the daemon applies it before the cancel reaches its settle, the session is already
    terminal and the item already `interrupted`. Forcing the cancel's transitions on top
    of that raises `IllegalTransition` — reporting a perfectly successful stop as a
    failure, and overwriting a decoded signal with "lost". Milestone 013 fixed this exact
    collision on the launch side; this is the same race at the other end of a session's
    life (014 research R5).
    """
    from tests.conftest import make_boundaries

    from robot_army import operations, spool
    from robot_army.audit import read_records
    from robot_army.boundaries import TerminationOutcome
    from robot_army.effects import EffectLevel

    item_id = active_session(conn, "raced-session")
    conn.execute(
        "UPDATE sessions SET pid = ?, proc_start = ?, scope = ?, host_socket = ? "
        "WHERE session_id = ?",
        (31337, "998877", "kitty-1-2.scope", "/tmp/raced.sock", "raced-session"),
    )
    conn.commit()
    write_exit_record(layout.spool_dir, session_id="raced-session", exit_code=143, signal=15)

    class DrainsWhileWeWait:
        """Stands in for the daemon's drain landing during the kill."""

        def terminate(self, handle, scope=None, **kwargs):
            spool.drain(conn, audit=audit, layout=layout)
            return TerminationOutcome(confirmed=True, method="process_group_signal")

        def attach_command(self, handle):
            return ["dtach", "-a", handle.socket_path]

    ctx = operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, host=DrainsWhileWeWait()),
        effect_level=EffectLevel.LIVE,
    )
    result = operations.cancel(ctx, item_id, force=True)

    assert result.code == operations.EXIT_OK, "the session is gone; that is what was asked for"
    session = db.get_session(conn, "raced-session")
    assert session.state is SessionState.EXITED_ERROR
    assert session.exit_code == 143 and session.signal == 15, (
        "the record's own account of how it died is more informative than 'lost'"
    )
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED

    audit.close()
    text = "\n".join(str(record) for record, _ in read_records(layout.log_dir) if record)
    assert "IllegalTransition" not in text
