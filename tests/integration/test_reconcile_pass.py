"""A whole reconciliation pass, for the two cases issue #33 left unwatched.

Unit coverage in ``tests/unit/test_session_liveness.py`` pins the decision. These pin the
*consequence*: that the pass which makes the decision also gives the capacity slot back, and
that the pass's several sweeps compose without reporting the same worker twice.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_boundaries, seed_item, seed_session, write_proc, write_registry

from robot_army import capacity, db, reconcile
from robot_army.states import SessionState, WorkItemState

REAL_PID = 7001
REAL_START = "31337"


def _pass(conn, audit, config, registry: Path, proc: Path):
    return reconcile.reconcile(
        conn,
        boundaries=make_boundaries(audit),
        audit=audit,
        config=config,
        layout=config.layout,
        registry_dir=registry,
        proc_root=proc,
    )


def test_a_dead_no_remote_session_frees_its_slot(conn, audit, config, tmp_path):
    """The reported bug, end to end: the item, the row and the slot in one pass.

    At ``no-remote`` the row is flagged simulated and the pid is real, so before this
    feature the pass reported ``checked: 1, interrupted: 0`` and the slot stayed held --
    which surfaces as a queue that quietly stops dispatching with ``repo_cap`` as its
    reason, reading exactly like the cap working correctly.
    """
    item_id = seed_item(
        conn, repo_key="demo", issue_number=33, dry_run=True,
        state=str(WorkItemState.ACTIVE),
    )
    seed_session(conn, item_id, state="running", dry_run=True, pid=REAL_PID,
                 session_id="s-dead")
    registry, proc = tmp_path / "registry", tmp_path / "proc"
    registry.mkdir()
    proc.mkdir()

    before = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert before.per_repo.get("demo") == 1, "the dead session is holding the slot"

    result = _pass(conn, audit, config, registry, proc)

    assert result.interrupted == 1
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    assert db.get_session(conn, "s-dead").state is SessionState.LOST

    after = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert after.per_repo.get("demo", 0) == 0, "the slot must come back in the same pass"


def test_a_resumed_items_surviving_worker_is_reported_once(conn, audit, config, tmp_path):
    """A superseded attempt whose worker outlived it, while the item is still working.

    The current attempt is deliberately **alive** here, because that is the only shape no
    sweep covered. Once the current attempt dies the item leaves ``active`` and #28's
    reclamation reports the ghost correctly; measured, that case was never the gap. With
    the item still ``active``, #28's rule says the row is legitimate -- its item *is*
    running something -- and the orphan sweep passes over the worker because its row still
    says ``running``. Before this feature the pass produced no anomaly at all.

    Exactly one report is the property under test: two sweeps could plausibly claim this
    worker and only one must.
    """
    item_id = seed_item(
        conn, repo_key="demo", issue_number=34, dry_run=True,
        state=str(WorkItemState.ACTIVE),
    )
    old = seed_session(conn, item_id, state="running", dry_run=True, pid=REAL_PID,
                       session_id="s-attempt-1")
    conn.execute("UPDATE sessions SET proc_start = ? WHERE id = ?", (REAL_START, old))
    current = seed_session(conn, item_id, state="running", dry_run=True, pid=7002,
                           session_id="s-attempt-2")
    conn.execute("UPDATE sessions SET proc_start = ? WHERE id = ?", ("42", current))

    registry, proc = tmp_path / "registry", tmp_path / "proc"
    worktree = str(config.worktree_root / "wt")
    # Both workers are alive: attempt 1 is the ghost, attempt 2 is the real session.
    write_proc(proc, REAL_PID, starttime=REAL_START, cwd=worktree)
    write_registry(registry, pid=REAL_PID, session_id="s-attempt-1",
                   proc_start=REAL_START, cwd=worktree)
    write_proc(proc, 7002, starttime="42", cwd=worktree)
    write_registry(registry, pid=7002, session_id="s-attempt-2", proc_start="42",
                   cwd=worktree)

    result = _pass(conn, audit, config, registry, proc)

    # ``include_simulated`` because the subject here is *whether the anomaly was raised*,
    # not what the default view shows. These fixtures seed rehearsed rows, so since issue
    # #21 gave anomalies a ``dry_run`` of their own the default scope correctly hides them.
    anomalies = [
        a
        for a in db.list_anomalies(conn, include_simulated=True)
        if a.kind == "orphan_session"
    ]
    assert len(anomalies) == 1, f"expected exactly one report, got {anomalies}"
    assert anomalies[0].entity_id == "s-attempt-1"
    assert anomalies[0].detail_obj["attempt"] == 1
    assert result.orphans == 0, "the orphan sweep must decline; the new site reports it"
    assert result.superseded == 1

    # The ghost's row stays open on purpose: the slot really is taken.
    assert db.get_session(conn, "s-attempt-1").state is SessionState.RUNNING
    # The item is working, and its current attempt decides that alone.
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


def test_a_superseded_dead_attempt_stops_holding_its_slot(conn, audit, config, tmp_path):
    """The quieter half: a resumed item's *dead* first attempt leaked a slot forever.

    Its item is ``active``, so #28's sweep leaves the row alone; its process is gone, so no
    orphan is reported. The row simply stayed ``running`` and kept counting against both
    caps with nothing able to reach it.
    """
    item_id = seed_item(
        conn, repo_key="demo", issue_number=35, dry_run=True,
        state=str(WorkItemState.ACTIVE),
    )
    old = seed_session(conn, item_id, state="running", dry_run=True, pid=REAL_PID,
                       session_id="s-old")
    conn.execute("UPDATE sessions SET proc_start = ? WHERE id = ?", (REAL_START, old))
    current = seed_session(conn, item_id, state="running", dry_run=True, pid=7002,
                           session_id="s-current")
    conn.execute("UPDATE sessions SET proc_start = ? WHERE id = ?", ("42", current))

    registry, proc = tmp_path / "registry", tmp_path / "proc"
    worktree = str(config.worktree_root / "wt")
    write_proc(proc, 7002, starttime="42", cwd=worktree)
    write_registry(registry, pid=7002, session_id="s-current", proc_start="42", cwd=worktree)

    before = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert before.per_repo.get("demo") == 2, "both rows are counting against the cap"

    result = _pass(conn, audit, config, registry, proc)

    assert result.superseded == 1
    assert db.get_session(conn, "s-old").state is SessionState.LOST
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE
    after = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert after.per_repo.get("demo") == 1, "only the live attempt should still count"


def test_a_whole_pass_over_a_healthy_session_reports_no_anomaly(
    conn, audit, config, tmp_path, transcripts
):
    """Issue #58 at the level the daemon actually runs at.

    The unit test pins the decision; this pins that the pass carrying it composes with every
    other sweep without reporting a healthy session as anything. ``transcripts_checked: 1``
    is the positive half: the detector ran and was satisfied, rather than not running.
    """
    from tests.conftest import write_transcript

    item_id = seed_item(
        conn, repo_key="demo", issue_number=58, state=str(WorkItemState.ACTIVE)
    )
    seed_session(conn, item_id, state="running", session_id="healthy-58", pid=REAL_PID)
    conn.execute("UPDATE sessions SET confirmed_at = started_at WHERE session_id = 'healthy-58'")
    write_transcript(transcripts, "healthy-58")

    registry, proc = tmp_path / "registry", tmp_path / "proc"
    write_registry(registry, session_id="healthy-58", pid=REAL_PID, proc_start=REAL_START,
                   cwd=str(config.worktree_root / "x"))
    write_proc(proc, REAL_PID, starttime=REAL_START, cwd=str(config.worktree_root / "x"))

    result = _pass(conn, audit, config, registry, proc)

    assert db.list_anomalies(conn, include_simulated=True) == []
    assert result.summary()["transcripts_checked"] == 1
    assert result.summary()["no_transcript"] == 0


def test_a_whole_pass_reports_a_session_that_never_wrote_a_transcript(
    conn, audit, config, tmp_path
):
    """The other branch, at the same level: the detector still detects.

    Silencing the false positive by removing the check would have traded a noisy signal for
    no signal at all, and the failure it catches -- a session that looks perfect and can
    never be resumed -- is invisible by any other means.
    """
    item_id = seed_item(
        conn, repo_key="demo", issue_number=59, state=str(WorkItemState.ACTIVE)
    )
    seed_session(conn, item_id, state="running", session_id="silent-59", pid=REAL_PID)
    old = "2026-01-01T00:00:00Z"
    conn.execute("UPDATE sessions SET confirmed_at = ? WHERE session_id = 'silent-59'", (old,))

    registry, proc = tmp_path / "registry", tmp_path / "proc"
    write_registry(registry, session_id="silent-59", pid=REAL_PID, proc_start=REAL_START,
                   cwd=str(config.worktree_root / "x"))
    write_proc(proc, REAL_PID, starttime=REAL_START, cwd=str(config.worktree_root / "x"))

    result = _pass(conn, audit, config, registry, proc)

    assert [
        a.kind for a in db.list_anomalies(conn, include_simulated=True)
    ] == ["no_transcript"]
    assert result.summary()["no_transcript"] == 1
