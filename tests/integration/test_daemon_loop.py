"""The daemon loop: startup ordering, the multi-rate scheduler, and signals.

Three properties here are requirements rather than implementation detail:

* **Reconciliation runs before any dispatch on startup** (FR-037). Dispatching first would
  launch new sessions against a picture of the world already known to be stale.
* **The spool drains before reconciliation** — otherwise a session that exited while the
  daemon was down looks like one that vanished, and reconciliation correctly-but-wrongly
  calls it ``interrupted``.
* **SIGTERM finishes the current tick and never touches running sessions** (FR-049).
"""

from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import replace

import pytest
from tests.conftest import (
    FakeIssueReader,
    StubDisplay,
    make_boundaries,
    make_issue,
    onboard_repo,
    seed_item,
    write_exit_record,
)

from robot_army import db
from robot_army.daemon import Daemon, check_preconditions, warn_about_environment
from robot_army.effects import EffectLevel
from robot_army.states import WorkItemState

pytestmark = pytest.mark.requires_git


def make_daemon(conn, audit, config, layout, tmp_path, **kwargs) -> Daemon:
    boundaries = kwargs.pop(
        "boundaries", make_boundaries(audit, reader=FakeIssueReader([make_issue()]))
    )
    return Daemon(
        config=config,
        layout=layout,
        boundaries=boundaries,
        audit=audit,
        conn=conn,
        effect_level=kwargs.pop("effect_level", EffectLevel.LIVE),
        registry_dir=tmp_path / "registry",
        proc_root=tmp_path / "proc",
        trust_file=kwargs.pop("trust_file", None),
        **kwargs,
    )


def trust_file(tmp_path, clone):
    path = tmp_path / "claude.json"
    path.write_text(
        json.dumps({"projects": {str(clone.resolve()): {"hasTrustDialogAccepted": True}}}),
        encoding="utf-8",
    )
    return path


# -- startup ---------------------------------------------------------------


def test_startup_drains_the_spool_before_reconciling(conn, audit, config, layout, tmp_path):
    """A record that arrived while we were down must be applied before anything reasons
    about state, or reconciliation calls a clean exit ``interrupted``."""
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE))
    with db.transaction(conn):
        row_id = db.insert_session(
            conn, work_item_id=item_id, session_id="s-1", attempt=1, dry_run=False
        )
    conn.execute("UPDATE sessions SET state = 'running' WHERE id = ?", (row_id,))
    write_exit_record(layout.spool_dir, session_id="s-1", exit_code=0)
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()

    daemon = make_daemon(conn, audit, config, layout, tmp_path)
    daemon._jobs = daemon._build_jobs()
    daemon.startup()

    item = db.get_work_item(conn, item_id)
    assert item.state is WorkItemState.AWAITING_REVIEW, (
        "the exit record was applied; reconciliation must not have overridden it"
    )


def test_startup_reconciles_before_any_dispatch(conn, audit, config, layout, tmp_path):
    """FR-037, asserted by ordering rather than by outcome."""
    order: list[str] = []
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()

    daemon = make_daemon(
        conn, audit, config, layout, tmp_path, trust_file=trust_file(tmp_path, config.repos["demo"].path)
    )
    seed_item(conn, state=str(WorkItemState.READY))

    original_reconcile = daemon.job_reconcile
    original_dispatch = daemon.job_dispatch

    def traced_reconcile():
        order.append("reconcile")
        return original_reconcile()

    def traced_dispatch():
        order.append("dispatch")
        return original_dispatch()

    daemon.job_reconcile = traced_reconcile
    daemon.job_dispatch = traced_dispatch
    daemon.run(once=True)

    assert order, "neither job ran"
    assert order[0] == "reconcile", f"dispatch ran before reconciliation: {order}"


def test_startup_logs_the_effect_level_loudly(conn, audit, config, layout, tmp_path):
    """FR-057. If this record is missing, nothing else in the log identifies what the
    session's effects were allowed to be."""
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    daemon = make_daemon(
        conn, audit, config, layout, tmp_path, effect_level=EffectLevel.LOCAL
    )
    daemon._jobs = daemon._build_jobs()
    daemon.startup()
    audit.close()

    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    start = [json.loads(line) for line in text.splitlines() if '"daemon.start"' in line][0]
    assert start["detail"]["effect_level"] == "local"
    assert start["detail"]["boundaries"]["version_control"]
    assert start["detail"]["pid"] == os.getpid()


def test_a_dangerous_environment_variable_is_named_at_startup(audit, config, monkeypatch):
    """M0 F19 cost the spike the most time of any single finding. The variable is cheap
    to look for and the failure it causes is invisible without it."""
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    warnings = warn_about_environment(audit, config)
    assert warnings
    assert "CLAUDE_CODE_CHILD_SESSION" in warnings[0]
    assert "unresumable" in warnings[0]


def test_a_clean_environment_produces_no_warning(audit, config, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_CHILD_SESSION", raising=False)
    assert warn_about_environment(audit, config) == []


# -- preconditions ----------------------------------------------------------


def test_preconditions_pass_when_everything_is_in_place(conn, audit, config, layout):
    problems = check_preconditions(
        config=config, layout=layout, boundaries=make_boundaries(audit), conn=conn
    )
    assert problems == []


def test_an_unreachable_terminal_socket_is_a_precondition_failure(conn, audit, config, layout):
    """A real interactive session in the running terminal *is* the product, so this is a
    precondition rather than a warning."""
    boundaries = make_boundaries(audit, display=StubDisplay(answers=False))
    problems = check_preconditions(
        config=config, layout=layout, boundaries=boundaries, conn=conn
    )
    assert any("no terminal control socket answered" in p for p in problems)


def test_an_unmigrated_database_is_a_precondition_failure(conn, audit, config, layout):
    conn.execute("PRAGMA user_version = 0")
    problems = check_preconditions(
        config=config, layout=layout, boundaries=make_boundaries(audit), conn=conn
    )
    assert any("schema is at version 0" in p for p in problems)


def test_preconditions_report_every_problem_at_once(conn, audit, config, layout):
    conn.execute("PRAGMA user_version = 0")
    boundaries = make_boundaries(audit, display=StubDisplay(answers=False))
    problems = check_preconditions(
        config=config, layout=layout, boundaries=boundaries, conn=conn
    )
    assert len(problems) >= 2


# -- the multi-rate scheduler ----------------------------------------------


def test_fast_jobs_run_every_tick_and_slow_ones_do_not(conn, audit, config, layout, tmp_path):
    """R6's whole point: coupling exit-detection latency to the GitHub poll interval
    would force a choice between prompt status updates and a sustainable rate budget."""
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    config = replace(
        config, daemon=replace(config.daemon, tick_seconds=1, poll_seconds=3600, reconcile_seconds=3600)
    )
    daemon = make_daemon(conn, audit, config, layout, tmp_path)
    daemon._jobs = daemon._build_jobs()

    first = daemon.tick()
    assert set(first) == {"spool", "dispatch", "poll", "reconcile"}, (
        "everything is due on the first tick, which is what makes --once a complete cycle"
    )

    # Deterministic: each job rescheduled itself by its *own* interval, which is the
    # whole of the multi-rate design.
    intervals = {job.name: job.interval for job in daemon._jobs}
    assert intervals == {"spool": 1.0, "dispatch": 1.0, "poll": 3600.0, "reconcile": 3600.0}

    # And the loop really does re-run only the fast ones once they come due. The sleep is
    # real rather than mocked because the scheduler is monotonic-clock-driven and a fake
    # clock here would test the fake.
    time.sleep(1.05)
    second = daemon.tick()
    assert set(second) == {"spool", "dispatch"}
    assert "poll" not in second, "a 3600s poll must not run again one second later"


def test_a_forced_job_runs_on_the_next_tick(conn, audit, config, layout, tmp_path):
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    config = replace(config, daemon=replace(config.daemon, poll_seconds=3600))
    daemon = make_daemon(conn, audit, config, layout, tmp_path)
    daemon._jobs = daemon._build_jobs()
    daemon.tick()

    assert daemon.request("poll") is True
    assert "poll" in daemon.tick()
    assert daemon.request("nonexistent") is False


def test_one_failing_job_does_not_kill_the_loop(conn, audit, config, layout, tmp_path):
    """A single loop means one bad job could stop everything; it must not."""
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    daemon = make_daemon(conn, audit, config, layout, tmp_path)
    daemon._jobs = daemon._build_jobs()

    def explode():
        raise RuntimeError("poll exploded")

    daemon.job_poll = explode
    daemon._jobs = daemon._build_jobs()
    for job in daemon._jobs:
        if job.name == "poll":
            job.run = explode

    ran = daemon.tick()
    assert "error" in ran["poll"]
    assert "spool" in ran, "the other jobs still ran"
    assert daemon.errors == 1


def test_every_tick_writes_a_heartbeat(conn, audit, config, layout, tmp_path):
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    daemon = make_daemon(conn, audit, config, layout, tmp_path)
    daemon._jobs = daemon._build_jobs()

    daemon.tick()
    first = json.loads(layout.heartbeat_path.read_text(encoding="utf-8"))
    assert first["cycles"] == 1
    assert first["effect_level"] == "live"

    daemon.tick()
    second = json.loads(layout.heartbeat_path.read_text(encoding="utf-8"))
    assert second["cycles"] == 2


def test_once_runs_a_complete_cycle_and_returns(conn, audit, config, layout, tmp_path):
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    daemon = make_daemon(conn, audit, config, layout, tmp_path)
    started = time.monotonic()
    assert daemon.run(once=True) == 0
    assert time.monotonic() - started < 20
    assert daemon.cycles == 1
    assert layout.heartbeat_path.exists()


# -- signals ---------------------------------------------------------------


def test_sigterm_finishes_the_tick_and_exits_cleanly(conn, audit, config, layout, tmp_path):
    """FR-049. Sessions live in their own systemd scopes and their own dtach masters
    precisely so that restarting the daemon is a non-event for work in progress."""
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    config = replace(config, daemon=replace(config.daemon, tick_seconds=1))
    daemon = make_daemon(conn, audit, config, layout, tmp_path)
    daemon.install_signal_handlers()

    ticks: list[int] = []
    original = daemon.tick

    def counting_tick():
        ticks.append(1)
        result = original()
        if len(ticks) == 2:
            os.kill(os.getpid(), signal.SIGTERM)
        return result

    daemon.tick = counting_tick
    try:
        assert daemon.run() == 0
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    assert len(ticks) == 2, "the signal arrived mid-tick; that tick must still complete"
    assert daemon.stopping is True


def test_stopping_does_not_terminate_any_session(conn, audit, config, layout, tmp_path):
    from tests.conftest import StubSessionHost

    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    host = StubSessionHost()
    daemon = make_daemon(
        conn, audit, config, layout, tmp_path, boundaries=make_boundaries(audit, host=host)
    )
    daemon.install_signal_handlers()
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)
        daemon.run()
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    assert host.terminated == [], "stopping the daemon must not touch running sessions"


def test_a_second_signal_does_not_escalate(conn, audit, config, layout, tmp_path):
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    daemon = make_daemon(conn, audit, config, layout, tmp_path)
    daemon.install_signal_handlers()
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.05)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
    assert daemon.stopping is True


# -- the full cycle ---------------------------------------------------------


def test_one_cycle_takes_a_labelled_issue_all_the_way_to_active(
    conn, audit, config, layout, tmp_path
):
    """The MVP, end to end through the loop rather than through dispatch directly."""
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    onboard_repo(conn, "demo", config.repos["demo"].path)

    from robot_army.boundaries.hooks import SubprocessHookRunner

    boundaries = make_boundaries(
        audit,
        reader=FakeIssueReader([make_issue(number=7)]),
        hooks=SubprocessHookRunner(audit),
    )
    daemon = make_daemon(
        conn,
        audit,
        config,
        layout,
        tmp_path,
        boundaries=boundaries,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    daemon.run(once=True)

    items = db.list_work_items(conn)
    assert len(items) == 1
    assert items[0].issue_number == 7
    assert items[0].state is WorkItemState.ACTIVE
    assert daemon.dispatched == 1
