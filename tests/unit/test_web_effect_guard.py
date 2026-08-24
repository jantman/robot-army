"""The effect-level guard (T039, research.md R4).

The daemon can be started with ``--effect-level plan`` while the configuration file says
``live``. Nothing in milestone 001 detects that divergence, because until now the only
other actor was a terminal command the author was typing deliberately. **A tap on a phone
is not that.** Without this guard the interface would happily launch real sessions and
write real GitHub comments for a daemon the author believes is doing nothing.
"""

from __future__ import annotations

import pytest
from tests.conftest import beat, seed_item, seed_session

from robot_army import db
from robot_army.states import WorkItemState

#: Actions that touch work or reach outside. Each is refused during a mismatch.
GUARDED = ("abandon", "cancel", "retry", "attach", "resume", "restart")


def _mismatched(layout):
    """A *fresh* heartbeat naming a different level. Freshness is the whole condition."""
    beat(layout, effect_level="plan")


def test_a_fresh_heartbeat_at_another_level_refuses_a_mutation(web, conn, layout, running_daemon):
    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="lost")
    _mismatched(layout)

    response = web.post_json(f"/item/{item_id}/resume")
    assert response.status == 409
    payload = response.json()
    assert "plan" in payload["reason"] and "live" in payload["reason"], (
        "the refusal must name BOTH levels; naming one leaves the author guessing which "
        "of the two processes to restart"
    )
    assert payload["code"] == 3


@pytest.mark.parametrize("action", ["abandon", "retry"])
def test_every_work_touching_action_is_refused_during_a_mismatch(
    web, conn, layout, running_daemon, action
):
    state = "failed" if action == "retry" else "interrupted"
    item_id = seed_item(conn, state=state)
    _mismatched(layout)
    assert web.post_json(f"/item/{item_id}/{action}").status == 409


def test_forcing_a_job_is_refused_during_a_mismatch(web, layout, running_daemon):
    """A forced poll at the wrong level would spend the wrong budget and write the wrong
    rows."""
    _mismatched(layout)
    assert web.post_json("/poll").status == 409
    assert web.post_json("/reconcile").status == 409


def test_read_views_keep_working_throughout(web, conn, layout, running_daemon):
    """This refuses actions, not inspection. Losing the views during a mismatch would
    remove the one thing that explains it."""
    seed_item(conn, state="active")
    _mismatched(layout)
    for path in ("/active", "/queue", "/interrupted", "/anomalies", "/log"):
        assert web.get(path).status == 200, path


def test_the_mismatch_is_shown_on_every_page_until_it_clears(web, conn, layout, running_daemon):
    _mismatched(layout)
    for path in ("/active", "/queue", "/log"):
        body = web.get(path).text
        assert "EFFECT LEVEL MISMATCH" in body, path
        assert web.get_json(path).json()["effect_mismatch"] is not None, path

    beat(layout, effect_level="live")
    assert "EFFECT LEVEL MISMATCH" not in web.get("/active").text
    assert web.get_json("/active").json()["effect_mismatch"] is None


def _stale(layout, level: str = "plan") -> None:
    layout.heartbeat_path.write_text(
        f'{{"ts":"2020-01-01T00:00:00Z","pid":1,"effect_level":"{level}",'
        '"activity":"dispatching","cycles":1}',
        encoding="utf-8",
    )


def test_a_stale_heartbeat_from_a_live_daemon_is_still_evidence_of_its_level(
    web, conn, layout, running_daemon
):
    """The state this guard exists for, and the one it used to wave through.

    Staleness here is not ignorance. A daemon's effect level is fixed when it starts and
    cannot change while it runs, and a starting daemon writes its heartbeat before its
    first tick — so a stale heartbeat from the process currently holding the lock still
    names that process's level correctly. What staleness means is that a tick is running
    long, which is when a big clone is in progress and when launching more work at the
    wrong level would matter most.
    """
    _stale(layout, "plan")
    item_id = seed_item(conn, state="interrupted")

    mismatch = web.get_json("/active").json()["effect_mismatch"]
    assert mismatch is not None
    assert "plan" in mismatch and "live" in mismatch
    assert "running long" in mismatch, "the message must say why the evidence is old"

    response = web.post_json(f"/item/{item_id}/abandon")
    assert response.status == 409
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED


def test_a_stale_heartbeat_agreeing_with_us_is_not_a_mismatch(
    web, conn, layout, running_daemon
):
    """Failing closed on *agreement* would make a long clone block every action."""
    _stale(layout, "live")
    item_id = seed_item(conn, state="interrupted")
    assert web.get_json("/active").json()["effect_mismatch"] is None
    assert web.post_json(f"/item/{item_id}/abandon").status == 303


def test_a_live_daemon_with_no_readable_heartbeat_fails_closed(
    web, conn, layout, running_daemon
):
    """The level is genuinely unknown here, which is not the same as nothing to disagree
    with — so it refuses rather than acting at this interface's level on the chance."""
    layout.heartbeat_path.unlink()
    item_id = seed_item(conn, state="interrupted")

    mismatch = web.get_json("/active").json()["effect_mismatch"]
    assert mismatch is not None
    assert "EFFECT LEVEL UNKNOWN" in mismatch

    response = web.post_json(f"/item/{item_id}/abandon")
    assert response.status == 409
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    # And the read views keep working, as always.
    assert web.get("/active").status == 200


def test_an_absent_heartbeat_with_no_daemon_is_not_a_mismatch(web, conn, layout):
    """No daemon holds the lock, so there is nothing to disagree with. Refusing on the
    strength of a heartbeat left by a dead process would be the same class of surprise in
    the other direction."""
    assert not layout.heartbeat_path.exists()
    assert web.get_json("/active").json()["effect_mismatch"] is None
    item_id = seed_item(conn, state="interrupted")
    assert web.post_json(f"/item/{item_id}/abandon").status == 303


def test_a_stale_heartbeat_with_no_daemon_is_not_a_mismatch(web, conn, layout):
    """The daemon that wrote it is gone; its claim is not evidence about anything now."""
    _stale(layout, "plan")
    assert web.get_json("/active").json()["effect_mismatch"] is None
    item_id = seed_item(conn, state="interrupted")
    assert web.post_json(f"/item/{item_id}/abandon").status == 303


def test_pausing_dispatch_is_deliberately_not_guarded(web, layout, running_daemon):
    """Pausing is the *mitigation* for the condition the guard detects.

    Refusing it during a mismatch would leave the interface with no safe action at the
    moment one is most wanted, and it launches nothing and writes nothing outward.
    """
    _mismatched(layout)
    response = web.post_json("/dispatch/pause")
    assert response.status == 303
    assert response.json()["paused"] is True


def test_acknowledging_an_anomaly_is_deliberately_not_guarded(web, conn, layout, running_daemon):
    """Bookkeeping. It touches no work and reaches nothing outside this process."""
    with db.transaction(conn):
        db.raise_anomaly(conn, kind="stale_socket", detail={})
    anomaly_id = db.list_anomalies(conn)[0].id
    _mismatched(layout)
    assert web.post_json(f"/anomalies/{anomaly_id}/acknowledge").status == 303


def test_the_guard_produces_an_audit_record_like_every_other_refusal(
    web, conn, layout, running_daemon
):
    """FR-040: an error response with no corresponding record is a defect."""
    item_id = seed_item(conn, state="interrupted")
    _mismatched(layout)
    web.post_json(f"/item/{item_id}/restart")

    records = _web_records(layout)
    assert any(r["action"] == "web.restart" and r["kind"] == "intent" for r in records)
    outcome = [r for r in records if r["action"] == "web.restart" and r["kind"] == "outcome"]
    assert outcome and outcome[0]["outcome"] == "error"
    assert "MISMATCH" in outcome[0]["detail"]["error"]


def _web_records(layout):
    import json

    records = []
    for path in sorted(layout.log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                if record.get("component") == "web":
                    records.append(record)
    return records
