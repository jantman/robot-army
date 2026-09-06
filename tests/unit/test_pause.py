"""Pausing dispatch: durability, scope, and what keeps running (T047).

The requirement with teeth is FR-035, durability. **A pause that lapses when the daemon
restarts is worse than no pause**, because the author believes work is held when it is not
— which is why it lives in the database rather than in a marker file or a config key (R6).

The other half is scope: while paused the daemon still polls, evaluates eligibility,
reconciles and heartbeats. A paused system must stay observably alive, not go quiet.
"""

from __future__ import annotations

import json

import pytest
from tests.conftest import beat, make_boundaries, seed_item

from robot_army import control, db, health, operations
from robot_army import daemon as daemon_mod
from robot_army.daemon import Daemon
from robot_army.states import WorkItemState


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


def make_daemon(config, conn, audit, boundaries, layout, **kwargs):
    from robot_army.effects import EffectLevel

    daemon = Daemon(
        config=config,
        layout=layout,
        boundaries=boundaries,
        audit=audit,
        conn=conn,
        effect_level=EffectLevel.LIVE,
        **kwargs,
    )
    daemon._jobs = daemon._build_jobs()
    return daemon


# -- the operation ----------------------------------------------------------


def test_pausing_records_when_and_by_which_interface(ctx, conn):
    result = operations.pause_dispatch(ctx, by="web")
    assert result.code == 0
    assert result.data["paused"] is True
    assert result.data["paused_by"] == "web"
    assert result.data["paused_at"]

    stored = db.get_dispatch_control(conn)
    assert stored.paused is True
    assert stored.paused_by == "web"


def test_a_redundant_pause_is_a_reported_no_op_not_an_error(ctx, conn):
    """FR-033. Pausing twice is not a mistake, and the *existing* pause with its original
    timestamp is the useful answer."""
    first = operations.pause_dispatch(ctx, by="cli")
    second = operations.pause_dispatch(ctx, by="web")
    assert second.code == 0
    assert second.data["redundant"] is True
    assert second.data["paused_at"] == first.data["paused_at"]
    assert second.data["paused_by"] == "cli"
    assert "already paused" in "\n".join(second.lines)


def test_the_attempt_is_still_recorded_when_it_changed_nothing(ctx, layout):
    operations.pause_dispatch(ctx, by="cli")
    operations.pause_dispatch(ctx, by="web")
    records = [
        json.loads(line)
        for path in sorted(layout.log_dir.glob("audit-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pauses = [r for r in records if r["action"] == "dispatch.pause" and r["kind"] == "outcome"]
    assert len(pauses) == 2
    assert pauses[1]["detail"]["redundant"] is True


def test_unpausing_clears_the_timestamp_and_the_actor(ctx, conn):
    operations.pause_dispatch(ctx, by="web")
    operations.unpause_dispatch(ctx, by="cli")
    stored = db.get_dispatch_control(conn)
    assert stored.paused is False
    assert stored.paused_at is None
    assert stored.paused_by is None


# -- what the daemon does with it -------------------------------------------


def test_dispatch_is_held_while_paused_and_items_accumulate_in_ready(
    config, conn, audit, boundaries, layout
):
    """FR-034: nothing is rejected and nothing is lost — the items simply stay ``ready``."""
    first = seed_item(conn, issue_number=1, state="ready")
    second = seed_item(conn, issue_number=2, state="ready")
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="cli")

    daemon = make_daemon(config, conn, audit, boundaries, layout)
    outcome = daemon.job_dispatch()

    assert outcome["dispatched"] == 0
    assert outcome["paused"] is True
    assert daemon.activity == "dispatch paused"
    for item_id in (first, second):
        assert db.get_work_item(conn, item_id).state is WorkItemState.READY


def test_polling_and_reconciliation_are_unaffected_by_the_pause(
    config, conn, audit, boundaries, layout
):
    """A paused system must stay observably alive, not go quiet."""
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="cli")
    daemon = make_daemon(config, conn, audit, boundaries, layout)

    poll_outcome = daemon.job_poll()
    reconcile_outcome = daemon.job_reconcile()
    assert "repos" in poll_outcome
    assert reconcile_outcome is not None

    ran = daemon.tick()
    assert set(ran) >= {"spool", "reconcile", "poll", "dispatch"}
    assert ran["dispatch"]["paused"] is True
    assert layout.heartbeat_path.exists(), "the heartbeat must keep being written"


def test_the_heartbeat_carries_the_pause(config, conn, audit, boundaries, layout):
    """FR-036: a check that "the daemon is healthy" must not be true while it is silently
    doing nothing."""
    daemon = make_daemon(config, conn, audit, boundaries, layout)
    daemon.tick()
    assert json.loads(layout.heartbeat_path.read_text())["dispatch_paused"] is False

    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="web")
    daemon.tick()
    assert json.loads(layout.heartbeat_path.read_text())["dispatch_paused"] is True


def test_an_older_heartbeat_without_the_field_still_parses(layout):
    """The field defaults to ``False``, so a heartbeat written by an older build reads."""
    layout.heartbeat_path.write_text(
        json.dumps(
            {
                "ts": "2026-08-24T00:00:00Z",
                "pid": 1,
                "effect_level": "live",
                "activity": "idle",
                "cycles": 5,
            }
        ),
        encoding="utf-8",
    )
    report = health.check(layout.heartbeat_path, max_age_seconds=10**9)
    assert report.healthy
    assert report.heartbeat.get("dispatch_paused") is None


def test_the_pause_survives_a_simulated_daemon_restart(
    config, conn, audit, boundaries, layout
):
    """SC-007. The database is what makes this free; a marker file would not be."""
    seed_item(conn, state="ready")
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="cli")

    first = make_daemon(config, conn, audit, boundaries, layout)
    first.tick()
    conn.close()

    # A completely new process would open the database again. Same thing.
    reopened, _ = db.open_database(layout.db_path)
    try:
        assert db.get_dispatch_control(reopened).paused is True
        second = make_daemon(config, reopened, audit, boundaries, layout)
        assert second.job_dispatch()["paused"] is True
    finally:
        reopened.close()


def test_a_rolled_back_pause_leaves_dispatch_running(conn):
    """Mid-``set_dispatch_paused``: rolled back, never half-applied."""
    with pytest.raises(RuntimeError), db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="web")
        raise RuntimeError("killed before commit")
    assert db.get_dispatch_control(conn).paused is False


def test_unpausing_lets_the_held_items_dispatch_again(
    config, conn, audit, boundaries, layout
):
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="cli")
    daemon = make_daemon(config, conn, audit, boundaries, layout)
    assert daemon.job_dispatch()["paused"] is True

    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=False, by="cli")
    resumed = daemon.job_dispatch()
    assert "paused" not in resumed
    assert daemon.activity == "dispatching"


# -- both front ends --------------------------------------------------------


def test_status_reports_the_pause_and_when_it_was_set(ctx):
    """FR-036: shown wherever work items are listed, not on a separate page."""
    operations.pause_dispatch(ctx, by="web")
    result = operations.status(ctx)
    assert result.data["dispatch_paused"] is True
    assert result.data["dispatch_paused_by"] == "web"
    assert any("PAUSED since" in line for line in result.lines)


def test_the_web_shows_the_pause_state_on_every_view(web, conn):
    assert web.post_json("/dispatch/pause").status == 303
    for path in ("/active", "/queue", "/interrupted", "/log"):
        payload = web.get_json(path).json()
        assert payload["dispatch_paused"] is True, path
        assert payload["dispatch_paused_by"] == "web", path
        assert "DISPATCH PAUSED" in web.get(path).text, path


def test_the_queue_explains_that_held_items_are_not_lost(web, conn):
    seed_item(conn, state="ready")
    web.post_json("/dispatch/pause")
    body = web.get("/queue").text
    assert "accumulate here" in body
    assert "nothing is being rejected or lost" in body


def test_the_web_and_the_terminal_write_the_same_state(web, conn, ctx):
    """FR-047: one operation, two callers. A second implementation would drift."""
    web.post_json("/dispatch/pause")
    assert db.get_dispatch_control(conn).paused is True

    operations.unpause_dispatch(ctx, by="cli")
    assert db.get_dispatch_control(conn).paused is False
    assert web.get_json("/queue").json()["dispatch_paused"] is False


def test_pausing_works_with_no_daemon_running(ctx, conn, layout):
    """Pausing a stopped daemon is meaningful — it takes effect when it starts."""
    assert not daemon_mod.is_locked(layout.lock_path)
    operations.pause_dispatch(ctx, by="cli")
    assert db.get_dispatch_control(conn).paused is True
    assert control.pending(layout) == [], "pausing is state, not a request"


# -- the controls themselves (T046, T051) -----------------------------------


def test_the_queue_view_renders_a_pause_control(web, conn):
    """Registering the route is only half of T046. Without a form there is no way to
    pause from a phone, which is the entire point of the milestone."""
    body = web.get("/queue").text
    assert '<form action="/dispatch/pause" method="post">' in body
    assert "pause dispatch" in body


def test_the_control_becomes_unpause_once_paused(web, conn):
    web.post_json("/dispatch/pause")
    body = web.get("/queue").text
    assert '<form action="/dispatch/unpause" method="post">' in body
    assert "resume dispatch" in body
    assert '<form action="/dispatch/pause" method="post">' not in body, (
        "offering a control that is already in force is FR-029's complaint in miniature"
    )


def test_the_queue_view_renders_poll_and_reconcile_controls(web, conn):
    body = web.get("/queue").text
    assert '<form action="/poll" method="post">' in body
    assert '<form action="/reconcile" method="post">' in body


def test_the_paused_pill_links_to_where_the_control_lives(web, conn):
    """The pause is visible from every view, so the control that lifts it has to be
    reachable from every view."""
    web.post_json("/dispatch/pause")
    for path in ("/active", "/interrupted", "/anomalies", "/log"):
        body = web.get(path).text
        # The href carries the visibility preference since 009, like every internal link.
        assert 'class="pill warn">DISPATCH PAUSED' in body, path
        assert 'href="/queue?include_simulated=' in body, path


def test_a_pending_job_request_is_reported_on_the_control(web, layout):
    from robot_army.daemon import SingleInstanceLock

    lock = SingleInstanceLock(layout.lock_path)
    lock.acquire()
    beat(layout)  # a daemon that holds the lock has always written one
    try:
        web.post_json("/poll")
        body = web.get("/queue").text
        assert "the daemon runs it within one tick" in body
    finally:
        lock.release()


def test_the_controls_say_what_happens_with_no_daemon_running(web, conn):
    body = web.get("/queue").text
    assert "performed directly rather than" in body


def test_every_rendered_form_targets_a_registered_route(web, conn):
    """The gap this closes ran the other way — routes with no form. This asserts both
    directions, so neither half can land without the other."""
    import re

    from robot_army.web.server import match

    seed_item(conn, state="failed")
    seen: set[str] = set()
    for path in ("/queue", "/active", "/interrupted", "/anomalies", "/log"):
        for action in re.findall(r'<form action="([^"?]+)"[^>]*method="post"', web.get(path).text):
            seen.add(action)
            route, _params, _allowed = match("POST", action)
            assert route is not None, f"{path} renders a form to unrouted {action}"
    assert {"/dispatch/pause", "/poll", "/reconcile"} <= seen
