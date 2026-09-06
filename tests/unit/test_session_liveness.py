"""Whether a session is checked for liveness, and what decides it (issue #33).

Three things are defended here and they are not equally important.

The first is the defect itself. Reconciliation decided whether to check a session against
the machine by reading ``dry_run``, which means "the effect level was not ``live``". The
question it needed to ask is "did this session ever have a process". Those agree at
``plan``, ``local`` and ``live`` and disagree at ``no-remote``, where the session host is
real -- so the sweep that notices a dead worker was switched off at the one level the
quickstart recommends for rehearsing with real sessions.

The second is the opposite failure, and it is the one that would be caught late. A
simulated session has no process and never will; reconciling it against ``/proc`` would
mark every simulated item ``interrupted`` on the very next pass. The discriminator has to
fix the first without causing the second, which is why the shapes are tested as a matrix
rather than one case at a time.

The third is that an item's *current* attempt is not the only session it owns. A resumed
item leaves an earlier row open, and nothing visited it: the liveness sweep reads only the
latest attempt, and the orphan sweep passes over any worker whose row still says
``running`` -- which it does because nothing visits it. Each blind spot held the other up.
"""

from __future__ import annotations

import pytest
from tests.conftest import make_boundaries, seed_item, seed_session, write_proc, write_registry

from robot_army import db, reconcile
from robot_army.states import SessionState, WorkItemState

#: A real session's shape: a pid the registry could confirm, and a start time to confirm it
#: against. ``proc_start`` is not decoration -- a pid alone is not identity (FR-038).
REAL_PID = 4321
REAL_START = "777"


def active_item(conn, *, dry_run, pid, proc_start=REAL_START, session_id="s-1", issue_number=33):
    """An ``active`` item owning one ``running`` session of the given shape."""
    item_id = seed_item(
        conn, repo_key="demo", issue_number=issue_number, dry_run=dry_run,
        state=str(WorkItemState.ACTIVE),
    )
    row_id = seed_session(
        conn, item_id, state="running", dry_run=dry_run, pid=pid, session_id=session_id
    )
    conn.execute("UPDATE sessions SET proc_start = ? WHERE id = ?", (proc_start, row_id))
    return item_id


def run(conn, audit, config, tmp_path, *, registry=None, proc=None):
    """One full pass against a machine on which nothing is running."""
    registry = registry if registry is not None else tmp_path / "registry"
    proc = proc if proc is not None else tmp_path / "proc"
    registry.mkdir(exist_ok=True)
    proc.mkdir(exist_ok=True)
    return reconcile.reconcile(
        conn,
        boundaries=make_boundaries(audit),
        audit=audit,
        config=config,
        layout=config.layout,
        registry_dir=registry,
        proc_root=proc,
    )


# -- US1: the discriminator -------------------------------------------------


@pytest.mark.parametrize(
    "label,dry_run,pid,expect_interrupted",
    [
        # dry_run alone cannot separate these two, which is the whole defect: the middle
        # row is `no-remote`, where the row is flagged simulated and the process is real.
        ("live", False, REAL_PID, True),
        ("no-remote", True, REAL_PID, True),
        ("plan/local", True, 0, False),
        ("unconfirmed", True, None, False),
    ],
)
def test_liveness_is_checked_iff_the_session_had_a_process(
    conn, audit, config, tmp_path, label, dry_run, pid, expect_interrupted
):
    """The four record shapes that reach this sweep, and what each must produce.

    Asserted as a matrix rather than four tests because the property under test is the
    *separation*: any discriminator that gets one row right by getting another wrong is
    the bug, not a partial fix.
    """
    item_id = active_item(conn, dry_run=dry_run, pid=pid)
    run(conn, audit, config, tmp_path)

    item = db.get_work_item(conn, item_id)
    session = db.latest_session_for_item(conn, item_id)
    if expect_interrupted:
        assert item.state is WorkItemState.INTERRUPTED, label
        assert session.state is SessionState.LOST, label
    else:
        assert item.state is WorkItemState.ACTIVE, label
        assert session.state is SessionState.RUNNING, label


def test_a_dead_session_below_live_is_reconciled(conn, audit, config, tmp_path):
    """Issue #33 exactly: at ``no-remote`` the row is flagged simulated and the pid is real.

    Nothing covered this shape before, which is why the defect shipped. The record carried
    every fact needed -- no registry entry, no exit report, a pid belonging to nothing --
    and the sweep skipped it on the strength of a flag about the *effect level*.
    """
    item_id = active_item(conn, dry_run=True, pid=REAL_PID)
    result = run(conn, audit, config, tmp_path)

    assert result.interrupted == 1
    assert result.skipped_never_real == 0
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    session = db.latest_session_for_item(conn, item_id)
    assert session.state is SessionState.LOST
    assert session.dry_run is True, "the row is still a simulated row; only the sweep changed"


def test_live_is_untouched(conn, audit, config, tmp_path):
    """FR-013. The fully live level must behave exactly as it did before this feature."""
    item_id = active_item(conn, dry_run=False, pid=REAL_PID)
    result = run(conn, audit, config, tmp_path)

    assert (result.interrupted, result.skipped_never_real, result.superseded) == (1, 0, 0)
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    assert db.latest_session_for_item(conn, item_id).state is SessionState.LOST


def test_a_live_session_below_live_is_left_alone(conn, audit, config, tmp_path):
    """The other half of the fix: a real process at ``no-remote`` must keep its item active."""
    item_id = active_item(conn, dry_run=True, pid=REAL_PID)
    registry, proc = tmp_path / "registry", tmp_path / "proc"
    write_registry(registry, pid=REAL_PID, session_id="s-1", proc_start=REAL_START)
    write_proc(proc, REAL_PID, starttime=REAL_START, cwd=str(config.worktree_root))

    result = run(conn, audit, config, tmp_path, registry=registry, proc=proc)

    assert result.interrupted == 0
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE
    assert db.latest_session_for_item(conn, item_id).state is SessionState.RUNNING


@pytest.mark.parametrize("state", [SessionState.EXITED_CLEAN, SessionState.EXITED_ERROR])
def test_a_recorded_exit_wins_over_an_absent_process(conn, audit, config, tmp_path, state):
    """Ordering, not decoration: a spool record applied earlier in this same tick must not
    be overwritten by a slower observation of the same fact."""
    item_id = active_item(conn, dry_run=True, pid=REAL_PID)
    conn.execute("UPDATE sessions SET state = ? WHERE session_id = 's-1'", (str(state),))
    conn.execute(
        "UPDATE work_items SET state = ? WHERE id = ?",
        (str(WorkItemState.AWAITING_REVIEW), item_id),
    )

    result = run(conn, audit, config, tmp_path)

    assert result.interrupted == 0
    assert db.get_session(conn, "s-1").state is state
    assert db.get_work_item(conn, item_id).state is WorkItemState.AWAITING_REVIEW


def test_a_row_written_before_this_change_needs_no_backfill(conn, audit, config, tmp_path):
    """FR-008, and the reason no column was added.

    A row stored before this feature carries exactly the ``pid`` it always did -- written
    from the session host boundary at confirmation -- so the first pass after the change
    classifies it correctly with no migration step. This test *is* that claim: the rows
    below are written the pre-change way and never touched up.
    """
    simulated = active_item(conn, dry_run=True, pid=0, session_id="old-sim", issue_number=91)
    real = active_item(conn, dry_run=True, pid=REAL_PID, session_id="old-real", issue_number=92)

    result = run(conn, audit, config, tmp_path)

    assert db.get_work_item(conn, simulated).state is WorkItemState.ACTIVE
    assert db.get_work_item(conn, real).state is WorkItemState.INTERRUPTED
    assert (result.interrupted, result.skipped_never_real) == (1, 1)


# -- US2: the invariant the original skip existed to protect ----------------


def test_a_simulated_item_survives_repeated_passes(conn, audit, config, tmp_path):
    """Three passes, not one.

    The failure this guards against is not "interrupted on this pass" but "interrupted on
    the *next* one" -- a discriminator that reads the registry for a session that can never
    appear there marks every simulated item interrupted the moment a second pass runs.
    """
    item_id = active_item(conn, dry_run=True, pid=0)

    for _ in range(3):
        result = run(conn, audit, config, tmp_path)
        assert result.interrupted == 0
        assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE
        assert db.latest_session_for_item(conn, item_id).state is SessionState.RUNNING
    assert result.skipped_never_real == 1


def test_a_skipped_session_is_distinguishable_from_a_live_one(conn, audit, config, tmp_path):
    """FR-009. The reported defect looked like a clean pass: ``checked: 2, interrupted: 0``.

    Both numbers were true and together they were misleading, because nothing said that one
    of those sessions had not been examined at all. ``checked`` keeps its meaning -- items
    visited -- and the new figure says how many of them were skipped.
    """
    active_item(conn, dry_run=True, pid=0, session_id="sim", issue_number=61)
    active_item(conn, dry_run=True, pid=REAL_PID, session_id="real", issue_number=62)

    result = run(conn, audit, config, tmp_path)

    assert result.checked == 2, "both items were visited"
    assert result.skipped_never_real == 1, "one of them was never examined against /proc"
    assert result.interrupted == 1, "and the one that was, was found dead"


def test_the_pass_summary_reports_the_new_figures(conn, audit, config, tmp_path):
    """They have to reach the log, not just the dataclass -- ``reconcile.pass`` is where a
    reader reconstructs what a pass did without re-running it."""
    active_item(conn, dry_run=True, pid=0)
    summary = run(conn, audit, config, tmp_path).summary()

    assert summary["skipped_never_real"] == 1
    assert summary["superseded"] == 0
    # The keys #28 and milestone 001 established must keep their names and meanings.
    for key in ("checked", "interrupted", "reclaimed", "orphans"):
        assert key in summary


# -- US3: attempts the item has already replaced ----------------------------


def superseded_item(conn, *, old_pid, current_pid=8002, dry_run=True, issue_number=70):
    """An ``active`` item with two open rows: attempt 1 superseded, attempt 2 current."""
    item_id = seed_item(
        conn, repo_key="demo", issue_number=issue_number, dry_run=dry_run,
        state=str(WorkItemState.ACTIVE),
    )
    old = seed_session(conn, item_id, state="running", dry_run=dry_run, pid=old_pid,
                       session_id="ghost")
    conn.execute("UPDATE sessions SET proc_start = '900' WHERE id = ?", (old,))
    current = seed_session(conn, item_id, state="running", dry_run=dry_run,
                           pid=current_pid, session_id="current")
    conn.execute("UPDATE sessions SET proc_start = '901' WHERE id = ?", (current,))
    return item_id


def _alive(config, registry, proc, *, pid, session_id, start):
    worktree = str(config.worktree_root / "wt")
    write_proc(proc, pid, starttime=start, cwd=worktree)
    write_registry(registry, pid=pid, session_id=session_id, proc_start=start, cwd=worktree)


def test_a_live_ghost_is_reported_and_left_open(conn, audit, config, tmp_path):
    """FR-017. The row stays open because the slot it holds really is taken."""
    item_id = superseded_item(conn, old_pid=8001)
    registry, proc = tmp_path / "registry", tmp_path / "proc"
    registry.mkdir(), proc.mkdir()
    _alive(config, registry, proc, pid=8001, session_id="ghost", start="900")
    _alive(config, registry, proc, pid=8002, session_id="current", start="901")

    result = run(conn, audit, config, tmp_path, registry=registry, proc=proc)

    # ``include_simulated`` because the subject here is *whether the anomaly was raised*,
    # not what the default view shows. These fixtures seed rehearsed rows, so since issue
    # #21 gave anomalies a ``dry_run`` of their own the default scope correctly hides them.
    anomalies = [
        a
        for a in db.list_anomalies(conn, include_simulated=True)
        if a.kind == "orphan_session"
    ]
    assert [a.entity_id for a in anomalies] == ["ghost"]
    assert anomalies[0].detail_obj["attempt"] == 1
    assert anomalies[0].detail_obj["current_attempt"] == 2
    assert result.superseded == 1
    assert db.get_session(conn, "ghost").state is SessionState.RUNNING
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


def test_a_live_ghost_is_not_reported_twice(conn, audit, config, tmp_path):
    """FR-019. Two sweeps could claim this worker; exactly one must.

    ``_orphan_sweep`` declines because the row is deliberately left ``running`` -- the same
    composition #28 relied on, reached from the other side.
    """
    superseded_item(conn, old_pid=8001)
    registry, proc = tmp_path / "registry", tmp_path / "proc"
    registry.mkdir(), proc.mkdir()
    _alive(config, registry, proc, pid=8001, session_id="ghost", start="900")
    _alive(config, registry, proc, pid=8002, session_id="current", start="901")

    result = run(conn, audit, config, tmp_path, registry=registry, proc=proc)

    assert result.orphans == 0
    assert (
        len([
            a
            for a in db.list_anomalies(conn, include_simulated=True)
            if a.kind == "orphan_session"
        ])
        == 1
    )


def test_reporting_a_ghost_is_idempotent(conn, audit, config, tmp_path):
    """A 60-second loop must not turn one ghost into 1,440 anomalies a day."""
    superseded_item(conn, old_pid=8001)
    registry, proc = tmp_path / "registry", tmp_path / "proc"
    registry.mkdir(), proc.mkdir()
    _alive(config, registry, proc, pid=8001, session_id="ghost", start="900")
    _alive(config, registry, proc, pid=8002, session_id="current", start="901")

    run(conn, audit, config, tmp_path, registry=registry, proc=proc)
    second = run(conn, audit, config, tmp_path, registry=registry, proc=proc)

    assert (
        len([
            a
            for a in db.list_anomalies(conn, include_simulated=True)
            if a.kind == "orphan_session"
        ])
        == 1
    )
    assert second.superseded == 0, "an already-open anomaly is not a new finding"


def test_a_dead_ghost_is_closed_without_touching_its_item(conn, audit, config, tmp_path):
    """FR-018. A resumed item must never be interrupted by the attempt the resume replaced."""
    item_id = superseded_item(conn, old_pid=8001)
    registry, proc = tmp_path / "registry", tmp_path / "proc"
    registry.mkdir(), proc.mkdir()
    _alive(config, registry, proc, pid=8002, session_id="current", start="901")

    result = run(conn, audit, config, tmp_path, registry=registry, proc=proc)

    assert result.superseded == 1
    assert db.get_session(conn, "ghost").state is SessionState.LOST
    assert db.get_session(conn, "current").state is SessionState.RUNNING
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE
    assert not db.list_anomalies(conn, include_simulated=True), (
        "a dead ghost is closed, not reported"
    )


def test_simulated_ghosts_are_left_entirely_alone(conn, audit, config, tmp_path):
    """The US2 invariant, applied to the code path US3 adds.

    Two open rows that never had a process, under one active item: neither is closed and
    neither is reported, by the same rule the current attempt is judged by.
    """
    item_id = superseded_item(conn, old_pid=0, current_pid=0)

    result = run(conn, audit, config, tmp_path)

    assert result.superseded == 0
    assert result.skipped_never_real == 1
    rows = {s.session_id: s.state for s in db.list_sessions_for_item(conn, item_id)}
    assert rows == {"ghost": SessionState.RUNNING, "current": SessionState.RUNNING}
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


def test_an_item_with_one_session_is_unaffected(conn, audit, config, tmp_path):
    """FR-011's non-regression half: the ordinary case must not have changed."""
    item_id = active_item(conn, dry_run=True, pid=REAL_PID)

    result = run(conn, audit, config, tmp_path)

    assert result.superseded == 0
    assert result.interrupted == 1
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED


def test_a_closed_superseded_row_is_not_revisited(conn, audit, config, tmp_path):
    """Idempotency for the dead branch: once ``lost``, the row is out of scope."""
    superseded_item(conn, old_pid=8001)
    registry, proc = tmp_path / "registry", tmp_path / "proc"
    registry.mkdir(), proc.mkdir()
    _alive(config, registry, proc, pid=8002, session_id="current", start="901")

    first = run(conn, audit, config, tmp_path, registry=registry, proc=proc)
    second = run(conn, audit, config, tmp_path, registry=registry, proc=proc)

    assert (first.superseded, second.superseded) == (1, 0)
