"""Cross-process job requests (T052, research.md R5).

This closes a gap milestone 001 left open rather than adding a feature: its CLI contract
promised that ``robot-army poll`` "signals it to poll on its next tick", when in fact
``operations.poll_now`` only printed how often the daemon polls, because ``Daemon.request()``
had no caller outside the process.
"""

from __future__ import annotations

import json

import pytest
from tests.conftest import make_boundaries

from robot_army import control, operations
from robot_army.control import UnknownJob, pending, request_job, take_requests
from robot_army.daemon import Daemon, SingleInstanceLock


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


def test_a_marker_is_written_and_consumed_exactly_once(layout, audit):
    assert request_job(layout, "poll") is True
    assert pending(layout) == ["poll"]

    assert take_requests(layout, audit) == ["poll"]
    assert pending(layout) == []
    assert take_requests(layout, audit) == [], "a consumed marker must not come back"


def test_re_requesting_while_one_is_pending_is_a_harmless_no_op(layout, audit):
    """A double tap on a phone is the normal case, not the exceptional one."""
    assert request_job(layout, "poll") is True
    assert request_job(layout, "poll") is False
    assert request_job(layout, "poll") is False
    assert pending(layout) == ["poll"]
    assert take_requests(layout, audit) == ["poll"]


def test_both_job_names_are_independent(layout, audit):
    request_job(layout, "poll")
    request_job(layout, "reconcile")
    assert take_requests(layout, audit) == ["poll", "reconcile"]


def test_an_unknown_job_name_is_a_usage_error_never_written_to_disk(layout):
    with pytest.raises(UnknownJob):
        request_job(layout, "rm-rf")
    assert pending(layout) == []


def test_an_unrecognised_file_is_ignored_and_reported_once_never_deleted(
    layout, audit, monkeypatch
):
    """Deleting something the system does not understand is worse than leaving it.

    Reporting it once rather than every tick is the other half: a 5-second loop would
    otherwise produce 17,000 identical records a day, burying the record this project
    exists to keep readable.
    """
    monkeypatch.setattr(control, "_REPORTED_UNKNOWN", set())
    stray = layout.requests_dir / "something-else"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("", encoding="utf-8")
    request_job(layout, "poll")

    assert take_requests(layout, audit) == ["poll"]
    assert stray.exists(), "an unrecognised file must be left in place"

    # Reported once, not once per tick.
    for _ in range(5):
        take_requests(layout, audit)
    audit.close()
    records = [
        json.loads(line)
        for path in sorted(layout.log_dir.glob("audit-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    reports = [r for r in records if r["action"] == "control.unknown_request"]
    assert len(reports) == 1
    assert reports[0]["outcome"] == "error"
    assert stray.exists()


def test_the_daemon_drains_markers_at_the_top_of_a_tick(
    config, conn, audit, boundaries, layout
):
    """Honoured by *this* tick rather than the next one — that is why it drains first."""
    from robot_army.effects import EffectLevel

    daemon = Daemon(
        config=config,
        layout=layout,
        boundaries=boundaries,
        audit=audit,
        conn=conn,
        effect_level=EffectLevel.LIVE,
    )
    daemon._jobs = daemon._build_jobs()
    # Push every job far into the future, so only a forced one can run.
    for job in daemon._jobs:
        job.next_due = float("inf")

    request_job(layout, "poll")
    ran = daemon.tick()

    assert "poll" in ran, "the marker must have forced the job on this tick"
    assert pending(layout) == []
    assert not any(job.forced for job in daemon._jobs), "the flag is cleared after running"


def test_a_marker_written_while_the_daemon_is_down_survives(layout, audit):
    """It persists and is consumed on the next tick or the next start — including across a
    reboot, where the cost of a leftover marker is one redundant job."""
    request_job(layout, "reconcile")
    assert (layout.requests_dir / "reconcile").exists()
    assert take_requests(layout, audit) == ["reconcile"]


# -- what the operations do -------------------------------------------------


def test_poll_now_writes_a_marker_when_a_daemon_holds_the_lock(ctx, layout):
    lock = SingleInstanceLock(layout.lock_path)
    lock.acquire()
    try:
        result = operations.poll_now(ctx)
        assert result.code == 0
        assert result.data["delegated"] is True
        assert result.data["requested"] == "poll"
        assert result.data["marker_created"] is True
        assert pending(layout) == ["poll"]
        # The response says "requested", not "here is what it found" — the daemon reports
        # the result into the audit log, which is where a forced job's answer lives.
        assert any("within one tick" in line for line in result.lines)
        assert not any("polls every" in line for line in result.lines)
    finally:
        lock.release()


def test_a_second_request_while_one_is_pending_is_reported_honestly(ctx, layout):
    lock = SingleInstanceLock(layout.lock_path)
    lock.acquire()
    try:
        operations.poll_now(ctx)
        second = operations.poll_now(ctx)
        assert second.data["marker_created"] is False
        assert any("already requested" in line for line in second.lines)
    finally:
        lock.release()


def test_reconcile_now_delegates_the_same_way(ctx, layout):
    lock = SingleInstanceLock(layout.lock_path)
    lock.acquire()
    try:
        result = operations.reconcile_now(ctx)
        assert result.data["requested"] == "reconcile"
        assert pending(layout) == ["reconcile"]
    finally:
        lock.release()


def test_with_no_daemon_running_the_work_is_done_directly(ctx, layout):
    """Unchanged from 001: the command works the same whether or not the daemon is up."""
    result = operations.reconcile_now(ctx)
    assert result.data["delegated"] is False
    assert pending(layout) == [], "no marker is written when there is nothing to ask"


def test_the_web_control_forces_a_real_poll(web, layout):
    lock = SingleInstanceLock(layout.lock_path)
    lock.acquire()
    try:
        response = web.post_json("/poll")
        assert response.status == 303
        assert response.json()["requested"] == "poll"
        assert pending(layout) == ["poll"]
    finally:
        lock.release()


def test_the_pending_request_is_visible_in_the_chrome(web, layout):
    """A request that vanished without trace would be indistinguishable from one that was
    never made."""
    lock = SingleInstanceLock(layout.lock_path)
    lock.acquire()
    try:
        web.post_json("/reconcile")
        assert web.get_json("/queue").json()["pending_job_requests"] == ["reconcile"]
    finally:
        lock.release()
