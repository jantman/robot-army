"""Reconciliation: liveness, the dispatching sweep, closed issues, orphans (T093, T094, T096).

The orphan test is the one that earns its keep. M0 F17: if the wrapper is killed uncleanly
the worker keeps running, reparented, while dtach tears down its socket — so the daemon
sees no socket and no exit report and would conclude ``interrupted`` while a real session
is still editing files. **``interrupted`` does not mean "nothing is running."**
"""

from __future__ import annotations

from dataclasses import replace

from tests.conftest import (
    FakeIssueReader,
    make_boundaries,
    seed_item,
    write_proc,
    write_registry,
)

from robot_army import db, reconcile
from robot_army.states import SessionState, WorkItemState


def active_item(conn, *, session_id="s-1", pid=4242, proc_start="777", **kwargs):
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE), **kwargs)
    with db.transaction(conn):
        row_id = db.insert_session(
            conn,
            work_item_id=item_id,
            session_id=session_id,
            attempt=1,
            dry_run=kwargs.get("dry_run", False),
        )
    conn.execute(
        "UPDATE sessions SET state = 'running', pid = ?, proc_start = ? WHERE id = ?",
        (pid, proc_start, row_id),
    )
    return item_id


def run(conn, audit, config, tmp_path, **kwargs):
    return reconcile.reconcile(
        conn,
        boundaries=kwargs.pop("boundaries", None) or make_boundaries(audit),
        audit=audit,
        config=config,
        layout=config.layout,
        registry_dir=kwargs.pop("registry_dir", tmp_path / "registry"),
        proc_root=kwargs.pop("proc_root", tmp_path / "proc"),
    )


def test_an_active_item_with_a_live_session_is_left_alone(conn, audit, config, tmp_path):
    item_id = active_item(conn)
    registry = tmp_path / "registry"
    proc = tmp_path / "proc"
    write_registry(registry, pid=4242, session_id="s-1", proc_start="777")
    write_proc(proc, 4242, starttime="777", cwd=str(config.worktree_root))

    result = run(conn, audit, config, tmp_path)
    assert result.interrupted == 0
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


def test_an_active_item_with_no_live_session_becomes_interrupted(conn, audit, config, tmp_path):
    """FR-040: no live session and no exit record means the session was lost."""
    item_id = active_item(conn)
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()

    result = run(conn, audit, config, tmp_path)
    assert result.interrupted == 1
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    assert db.get_session(conn, "s-1").state is SessionState.LOST


def test_reboot_like_state_reconciles_quietly(conn, audit, config, tmp_path, layout):
    """T096. After a reboot every session is gone and every row still says ``active``.
    That is expected, not an error — treating it as one would train the maintainer to
    ignore errors."""
    ids = [active_item(conn, session_id=f"s-{n}", issue_number=n) for n in range(1, 4)]
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()

    result = run(conn, audit, config, tmp_path)
    assert result.interrupted == 3
    for item_id in ids:
        assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED

    audit.close()
    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    error_actions = [
        line for line in text.splitlines() if '"outcome":"error"' in line
    ]
    assert not error_actions, f"reconciling a reboot raised error records: {error_actions}"


def test_pid_reuse_does_not_keep_an_item_active(conn, audit, config, tmp_path):
    """T095, at the reconciliation level: the PID exists again, but it is not our process."""
    item_id = active_item(conn, pid=4242, proc_start="777")
    write_registry(tmp_path / "registry", pid=4242, session_id="s-1", proc_start="777")
    write_proc(tmp_path / "proc", 4242, starttime="999")  # recycled

    result = run(conn, audit, config, tmp_path)
    assert result.interrupted == 1
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED


def test_a_session_that_already_reported_its_exit_is_not_re_interrupted(
    conn, audit, config, tmp_path
):
    """A spool record applied earlier in the same tick must not be undone by the sweep."""
    item_id = active_item(conn)
    conn.execute(
        "UPDATE sessions SET state = ?, exit_code = 0 WHERE session_id = 's-1'",
        (str(SessionState.EXITED_CLEAN),),
    )
    conn.execute(
        "UPDATE work_items SET state = ? WHERE id = ?",
        (str(WorkItemState.AWAITING_REVIEW), item_id),
    )
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()

    result = run(conn, audit, config, tmp_path)
    assert result.interrupted == 0
    assert db.get_work_item(conn, item_id).state is WorkItemState.AWAITING_REVIEW


def test_a_simulated_session_is_not_reconciled_against_proc(conn, audit, config, tmp_path):
    """A simulated session has no process to be alive; reconciling it against the
    registry would mark every simulated item interrupted on the very next pass."""
    item_id = active_item(conn, session_id="s-sim", dry_run=True)
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()

    result = run(conn, audit, config, tmp_path)
    assert result.interrupted == 0
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


def test_an_active_item_with_no_session_row_is_interrupted(conn, audit, config, tmp_path):
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE))
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    result = run(conn, audit, config, tmp_path)
    assert result.interrupted == 1
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED


# -- the dispatching sweep --------------------------------------------------


def test_an_item_stuck_in_dispatching_past_max_age_fails(conn, audit, config, tmp_path):
    """FR-041. This is what stops a hung preparation wedging an item forever."""
    item_id = seed_item(conn, state=str(WorkItemState.DISPATCHING))
    conn.execute(
        "UPDATE work_items SET dispatching_at = ?, prepare_output = ? WHERE id = ?",
        ("2020-01-01T00:00:00Z", "make setup: hung", item_id),
    )
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()

    result = run(conn, audit, config, tmp_path)
    assert result.dispatching_failed == 1
    item = db.get_work_item(conn, item_id)
    assert item.state is WorkItemState.FAILED
    assert "stuck in dispatching" in (item.failure_reason or "")

    anomaly = db.list_anomalies(conn)[0]
    assert anomaly.kind == "dispatching_timeout"
    assert anomaly.detail_obj["prepare_output"] == "make setup: hung"


def test_a_recently_dispatching_item_is_left_alone(conn, audit, config, tmp_path):
    from robot_army.states import utcnow

    item_id = seed_item(conn, state=str(WorkItemState.DISPATCHING))
    conn.execute("UPDATE work_items SET dispatching_at = ? WHERE id = ?", (utcnow(), item_id))
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()

    result = run(conn, audit, config, tmp_path)
    assert result.dispatching_failed == 0
    assert db.get_work_item(conn, item_id).state is WorkItemState.DISPATCHING


def test_an_unparseable_dispatching_timestamp_is_treated_as_infinitely_old(
    conn, audit, config, tmp_path
):
    """Failing closed: an item we cannot date is one we cannot vouch for."""
    item_id = seed_item(conn, state=str(WorkItemState.DISPATCHING))
    conn.execute("UPDATE work_items SET dispatching_at = NULL WHERE id = ?", (item_id,))
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    assert run(conn, audit, config, tmp_path).dispatching_failed == 1


# -- closed issues ----------------------------------------------------------


def test_a_closed_issue_makes_the_item_done_whatever_the_session_was_doing(
    conn, audit, config, tmp_path
):
    """FR-035: regardless of session state."""
    item_id = active_item(conn, issue_number=42)
    write_registry(tmp_path / "registry", pid=4242, session_id="s-1", proc_start="777")
    write_proc(tmp_path / "proc", 4242, starttime="777")

    reader = FakeIssueReader()
    reader.closed[("demo", 42)] = True
    result = run(
        conn, audit, config, tmp_path, boundaries=make_boundaries(audit, reader=reader)
    )
    assert result.closed_done == 1
    assert db.get_work_item(conn, item_id).state is WorkItemState.DONE


def test_the_closed_check_is_skipped_for_simulated_items(conn, audit, config, tmp_path):
    """FR-055: spending rate-limit budget on a dry run would be the mode causing exactly
    the outward effect it exists to avoid."""

    class ExplodingReader(FakeIssueReader):
        def is_closed(self, repo_key, number):
            raise AssertionError("a simulated item must not reach GitHub")

    item_id = active_item(conn, session_id="s-sim", dry_run=True)
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    run(conn, audit, config, tmp_path, boundaries=make_boundaries(audit, reader=ExplodingReader()))
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


def test_a_failed_closed_check_does_not_move_the_item(conn, audit, config, tmp_path):
    """"I could not ask" is not "it is open", and it is certainly not "it is closed"."""
    from robot_army.boundaries import TransportError

    class BrokenReader(FakeIssueReader):
        def is_closed(self, repo_key, number):
            raise TransportError("github unreachable")

    item_id = active_item(conn)
    write_registry(tmp_path / "registry", pid=4242, session_id="s-1", proc_start="777")
    write_proc(tmp_path / "proc", 4242, starttime="777")

    result = run(
        conn, audit, config, tmp_path, boundaries=make_boundaries(audit, reader=BrokenReader())
    )
    assert result.closed_done == 0
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


def test_the_closed_check_is_cached_within_one_pass(conn, audit, config, tmp_path):
    """Several items can share a repository and the check costs a real API call each."""
    calls: list[int] = []

    class CountingReader(FakeIssueReader):
        def is_closed(self, repo_key, number):
            calls.append(number)
            return False

    for n in (1, 2, 3):
        seed_item(conn, issue_number=n, state=str(WorkItemState.AWAITING_REVIEW))
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()
    run(conn, audit, config, tmp_path, boundaries=make_boundaries(audit, reader=CountingReader()))
    assert sorted(calls) == [1, 2, 3], "one call per distinct issue, not per pass per item"


# -- the orphan sweep -------------------------------------------------------


def test_a_live_worker_under_the_worktree_root_with_no_row_is_an_orphan(
    conn, audit, config, tmp_path
):
    """T094, and M0 F17. The wrapper died, dtach tore down its socket, and the worker
    carried on reparented."""
    worktree = config.worktree_root / "demo" / "issue-5"
    worktree.mkdir(parents=True)
    write_registry(
        tmp_path / "registry", pid=8888, session_id="unknown-session", proc_start="1",
        cwd=str(worktree),
    )
    write_proc(tmp_path / "proc", 8888, starttime="1", cwd=str(worktree))

    result = run(conn, audit, config, tmp_path)
    assert result.orphans == 1
    anomaly = db.list_anomalies(conn)[0]
    assert anomaly.kind == "orphan_session"
    assert anomaly.detail_obj["pid"] == 8888
    assert anomaly.detail_obj["cwd"] == str(worktree)


def test_the_maintainers_own_session_is_not_an_orphan(conn, audit, config, tmp_path):
    """A session outside the worktree root is none of our business."""
    elsewhere = tmp_path / "personal"
    elsewhere.mkdir()
    write_registry(
        tmp_path / "registry", pid=8889, session_id="theirs", proc_start="1",
        cwd=str(elsewhere),
    )
    write_proc(tmp_path / "proc", 8889, starttime="1", cwd=str(elsewhere))

    result = run(conn, audit, config, tmp_path)
    assert result.orphans == 0
    assert db.list_anomalies(conn) == []


def test_a_claimed_session_is_not_an_orphan(conn, audit, config, tmp_path):
    worktree = config.worktree_root / "demo" / "issue-1"
    worktree.mkdir(parents=True)
    active_item(conn, session_id="s-1", pid=4242, proc_start="777")
    write_registry(
        tmp_path / "registry", pid=4242, session_id="s-1", proc_start="777", cwd=str(worktree)
    )
    write_proc(tmp_path / "proc", 4242, starttime="777", cwd=str(worktree))

    result = run(conn, audit, config, tmp_path)
    assert result.orphans == 0


def test_a_repeated_orphan_does_not_multiply_anomalies(conn, audit, config, tmp_path):
    worktree = config.worktree_root / "demo" / "issue-5"
    worktree.mkdir(parents=True)
    write_registry(
        tmp_path / "registry", pid=8888, session_id="ghost", proc_start="1", cwd=str(worktree)
    )
    write_proc(tmp_path / "proc", 8888, starttime="1", cwd=str(worktree))

    for _ in range(5):
        run(conn, audit, config, tmp_path)
    assert len(db.list_anomalies(conn)) == 1


# -- the registry version guard --------------------------------------------


def test_an_unknown_registry_version_raises_an_anomaly_and_degrades(
    conn, audit, config, tmp_path
):
    """FR-039's degraded path: never crash, because a worker upgrade must not take the
    daemon down — and never silently continue either."""
    write_registry(tmp_path / "registry", pid=9999, session_id="x", proc_start="1", version=42)
    write_proc(tmp_path / "proc", 9999, starttime="1", exe="/usr/bin/claude")

    run(conn, audit, config, tmp_path)
    kinds = [a.kind for a in db.list_anomalies(conn)]
    assert "registry_version_unknown" in kinds


def test_the_version_anomaly_is_raised_once_not_once_per_pass(conn, audit, config, tmp_path):
    write_registry(tmp_path / "registry", pid=9999, session_id="x", proc_start="1", version=42)
    write_proc(tmp_path / "proc", 9999, starttime="1")
    for _ in range(4):
        run(conn, audit, config, tmp_path)
    versions = [a for a in db.list_anomalies(conn) if a.kind == "registry_version_unknown"]
    assert len(versions) == 1


# -- worktree and socket sweeps --------------------------------------------


def test_a_missing_worktree_directory_is_flagged_not_removed(conn, audit, config, tmp_path):
    """FR-017. There is no automatic removal in this milestone, so the right action is to
    make the condition visible and let the maintainer decide."""
    item_id = seed_item(conn, state=str(WorkItemState.INTERRUPTED))
    gone = config.worktree_root / "demo" / "issue-42"
    conn.execute(
        "UPDATE work_items SET worktree_path = ?, branch = ? WHERE id = ?",
        (str(gone), "robot-army/issue-42", item_id),
    )
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()

    result = run(conn, audit, config, tmp_path)
    assert result.prunable == 1
    anomaly = [a for a in db.list_anomalies(conn) if a.kind == "prunable_worktree"][0]
    assert anomaly.detail_obj["worktree_path"] == str(gone)


def test_an_item_whose_repository_left_the_config_is_flagged(conn, audit, config, tmp_path):
    item_id = seed_item(conn, state=str(WorkItemState.INTERRUPTED))
    conn.execute(
        "UPDATE work_items SET worktree_path = ? WHERE id = ?", ("/tmp/wt", item_id)
    )
    stripped = replace(config, repos={})
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()

    reconcile.reconcile(
        conn,
        boundaries=make_boundaries(audit),
        audit=audit,
        config=stripped,
        layout=stripped.layout,
        registry_dir=tmp_path / "registry",
        proc_root=tmp_path / "proc",
    )
    assert [a.kind for a in db.list_anomalies(conn)] == ["config_missing_repo"]


def test_a_stale_socket_is_probed_and_removed(conn, audit, config, tmp_path, layout):
    """Probing rather than trusting the file's existence: stale sockets do not clean
    themselves up, and a live one must not be deleted."""
    stale = layout.socket_dir / "99.sock"
    stale.touch()
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()

    result = run(conn, audit, config, tmp_path)
    assert result.stale_sockets == 1
    assert not stale.exists()


def test_a_socket_belonging_to_a_live_session_is_left_alone(conn, audit, config, tmp_path, layout):
    item_id = active_item(conn)
    socket_path = layout.socket_dir / f"{item_id}.sock"
    socket_path.touch()
    conn.execute(
        "UPDATE sessions SET host_socket = ? WHERE work_item_id = ?",
        (str(socket_path), item_id),
    )
    write_registry(tmp_path / "registry", pid=4242, session_id="s-1", proc_start="777")
    write_proc(tmp_path / "proc", 4242, starttime="777")

    result = run(conn, audit, config, tmp_path)
    assert result.stale_sockets == 0
    assert socket_path.exists()
