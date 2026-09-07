"""What a reconciliation pass that could not see the registry may conclude (issue #44).

Reconciliation asked the session registry whether a session was there and treated "not
there" as a fact, without ever asking whether the registry could be read at all. Measured
against the tree this feature was written from: three ``active`` items with live-looking
sessions, one pass, a registry directory that is absent -- three ``interrupted``.

Every property here is asserted as a **pair**, and that is the point of the module rather
than a stylistic habit. What is under test is the *separation* of "the machine is idle"
from "the registry moved", and a fix that wins one half by losing the other is the same bug
with its sign flipped: guarding too little leaves the wholesale interruption, guarding too
much switches off the safety sweep issue #33 exists for. So the unusable conditions and the
empty-but-present directory are always checked together, in one test, against one setup.
"""

from __future__ import annotations

import pytest
from tests.conftest import make_boundaries, seed_item, seed_session, write_registry

from robot_army import capacity, db, reconcile, sessions
from robot_army.states import SessionState, WorkItemState

#: A real session's shape, matching ``test_session_liveness``: a pid the registry could
#: have confirmed and a start time to confirm it against. A pid alone is not identity.
REAL_PID = 4321
REAL_START = "777"

#: The registry conditions that make an observation unusable, and the one that does not.
#:
#: ``unlistable`` and ``partly_unknown`` are not in the issue's own table and were found by
#: measurement. The first reaches the same wrong conclusion through ``directory_missing``
#: rather than through an absent path; the second is the case where a *usable entry came
#: back* and the pass must still decline -- which is where this rule parts company with
#: ``capacity._registry_unusable``.
UNUSABLE = ("missing_dir", "unlistable", "unknown_version", "partly_unknown")


def build_registry(tmp_path, condition: str):
    """A registry directory in one of the five conditions. Returns its path.

    ``unlistable`` leaves the directory at mode ``000``; every caller restores it through
    ``restore``, because pytest cannot clean up a directory it may not enter.
    """
    registry = tmp_path / "registry"
    if condition == "missing_dir":
        return registry
    registry.mkdir(parents=True, exist_ok=True)
    if condition == "unlistable":
        registry.chmod(0o000)
    elif condition == "unknown_version":
        write_registry(registry, pid=REAL_PID, session_id="s-0", version="9.9.9")
    elif condition == "partly_unknown":
        write_registry(registry, pid=REAL_PID, session_id="s-0", version="9.9.9")
        write_registry(registry, pid=99999, session_id="s-stranger")
    return registry


def restore(registry) -> None:
    """Undo ``unlistable`` so the temporary directory can be removed."""
    if registry.is_dir() or registry.exists():
        registry.chmod(0o755)


def run(conn, audit, config, tmp_path, *, condition: str):
    """One reconciliation pass against a registry in the given condition."""
    registry = build_registry(tmp_path, condition)
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    try:
        return reconcile.reconcile(
            conn,
            boundaries=make_boundaries(audit),
            audit=audit,
            config=config,
            layout=config.layout,
            registry_dir=registry,
            proc_root=proc,
        )
    finally:
        restore(registry)


def active_item(conn, *, session_id, issue_number, pid=REAL_PID, state="running"):
    """An ``active`` item owning one open session with a real process identifier."""
    item_id = seed_item(
        conn, repo_key="demo", issue_number=issue_number, state=str(WorkItemState.ACTIVE)
    )
    row_id = seed_session(conn, item_id, state=state, pid=pid, session_id=session_id)
    conn.execute("UPDATE sessions SET proc_start = ? WHERE id = ?", (REAL_START, row_id))
    return item_id


def kinds(conn) -> set[str]:
    return {a.kind for a in db.list_anomalies(conn, include_simulated=True)}


# -- the predicate ----------------------------------------------------------


@pytest.mark.parametrize("condition", [*UNUSABLE, "empty_dir"])
def test_only_an_empty_present_directory_is_a_usable_observation(tmp_path, condition):
    """The five registry conditions, and which of them a pass may conclude from.

    A matrix rather than five tests, because the property is the separation. Note
    ``partly_unknown``: one file parsed and became an entry, and the observation is still
    unusable -- a conclusion about a session whose file we refused to parse is unfounded,
    however many other files we did read.
    """
    registry = build_registry(tmp_path / condition, condition)
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    scan = sessions.scan(registry_dir=registry, proc_root=proc)
    restore(registry)

    reason = reconcile._registry_unobservable(scan)
    if condition == "empty_dir":
        assert reason is None
    else:
        assert reason is not None, condition
        assert reason.strip(), condition


def test_the_reason_names_the_condition(tmp_path):
    """The predicate returns *why*, not merely *that*, because the record carries it."""
    proc = tmp_path / "proc"
    proc.mkdir()

    absent = sessions.scan(registry_dir=tmp_path / "gone", proc_root=proc)
    assert "directory" in reconcile._registry_unobservable(absent)

    refused = build_registry(tmp_path / "refused", "unknown_version")
    scan = sessions.scan(registry_dir=refused, proc_root=proc)
    assert "9.9.9" in reconcile._registry_unobservable(scan)

    degraded = sessions.scan_via_proc((), proc_root=proc)
    assert "/proc" in reconcile._registry_unobservable(degraded)


def test_the_predicate_does_not_count_entries(tmp_path):
    """An observation that saw nothing is blind whether or not it also found a process.

    The guard against the fix that would reintroduce the bug: a clause reading
    ``not scan.entries`` would pass every test above -- all five conditions yield zero
    live entries -- and would then let a partly-refused registry conclude death.
    """
    proc = tmp_path / "proc"
    proc.mkdir()
    registry = build_registry(tmp_path / "partly", "partly_unknown")
    scan = sessions.scan(registry_dir=registry, proc_root=proc, live_only=False)

    assert scan.entries, "the fixture must produce a readable entry, or it proves nothing"
    assert reconcile._registry_unobservable(scan) is not None


def test_unreadable_files_do_not_make_a_pass_blind(conn, audit, config, tmp_path):
    """A truncated file is what reading while the worker writes looks like.

    Treating it as blindness would switch the liveness sweep off at random on a healthy
    busy machine, which is the failure that sweep exists to prevent. The residual exposure
    -- one item, one pass -- is accepted in writing in the feature plan.
    """
    registry = tmp_path / "registry"
    write_registry(registry, pid=REAL_PID, session_id="s-0", truncate=True)
    proc = tmp_path / "proc"
    proc.mkdir()
    scan = sessions.scan(registry_dir=registry, proc_root=proc)

    assert scan.unreadable, "the fixture must produce an unreadable file"
    assert reconcile._registry_unobservable(scan) is None


# -- US1: the active-item sweep --------------------------------------------


@pytest.mark.parametrize("condition", [*UNUSABLE, "empty_dir"])
def test_a_blind_pass_interrupts_nothing_and_an_idle_one_still_does(
    conn, audit, config, tmp_path, condition
):
    """The headline measurement, in both directions (US1, US4).

    Three ``active`` items with live-looking sessions. Before this feature every one of the
    five conditions produced ``interrupted=3``; four of them were blind and one was a
    genuine observation of an idle machine, and nothing could tell them apart.
    """
    ids = [
        active_item(conn, session_id=f"s-{n}", issue_number=100 + n, pid=REAL_PID + n)
        for n in range(3)
    ]
    result = run(conn, audit, config, tmp_path, condition=condition)

    states = [db.get_work_item(conn, i).state for i in ids]
    sessions_after = [db.latest_session_for_item(conn, i).state for i in ids]
    if condition == "empty_dir":
        assert result.interrupted == 3
        assert result.liveness_withheld == 0
        assert states == [WorkItemState.INTERRUPTED] * 3
        assert sessions_after == [SessionState.LOST] * 3
    else:
        assert result.interrupted == 0, condition
        assert result.liveness_withheld == 3, condition
        assert states == [WorkItemState.ACTIVE] * 3, condition
        assert sessions_after == [SessionState.RUNNING] * 3, condition


def test_conclusions_that_do_not_need_the_registry_survive(conn, audit, config, tmp_path):
    """FR-004, and the reason the guard's *position* is the requirement.

    Every branch above the guard reaches its conclusion from the database. An item with no
    session row has nothing to be alive; a row with no process identifier never had one.
    Withholding either would be a different bug, and the guard is placed below both so
    they survive by construction rather than because this test remembered them.
    """
    orphaned = seed_item(
        conn, repo_key="demo", issue_number=201, state=str(WorkItemState.ACTIVE)
    )
    never_real = active_item(conn, session_id="s-sim", issue_number=202, pid=0)

    result = run(conn, audit, config, tmp_path, condition="missing_dir")

    assert db.get_work_item(conn, orphaned).state is WorkItemState.INTERRUPTED
    assert result.interrupted == 1
    assert result.skipped_never_real == 1
    assert db.get_work_item(conn, never_real).state is WorkItemState.ACTIVE
    assert db.latest_session_for_item(conn, never_real).state is SessionState.RUNNING
    assert result.liveness_withheld == 0, "neither item's conclusion needed the registry"


@pytest.mark.parametrize("terminal", ["exited_clean", "exited_error"])
def test_a_recorded_exit_is_left_alone_by_a_blind_pass(
    conn, audit, config, tmp_path, terminal
):
    """The exit-record check sits above the guard, and must stay there.

    A spool record applied earlier in the same tick already knows how the session ended.
    Reaching the guard first would count a settled session as a withheld conclusion, which
    would be a lie about a pass that had nothing to withhold.
    """
    item_id = active_item(conn, session_id="s-done", issue_number=203, state=terminal)

    result = run(conn, audit, config, tmp_path, condition="missing_dir")

    assert db.latest_session_for_item(conn, item_id).state is SessionState(terminal)
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE
    assert result.liveness_withheld == 0


# -- US2: the sweeps that close session rows --------------------------------


def stale_row(conn, *, session_id="s-stale", issue_number=210):
    """A ``done`` work item still holding one open session row.

    The #28 shape: nothing but the wrapper's exit record closes a row, so a row under a
    finished item holds a capacity slot until a sweep reclaims it.
    """
    item_id = seed_item(
        conn, repo_key="demo", issue_number=issue_number, state=str(WorkItemState.DONE)
    )
    row_id = seed_session(conn, item_id, state="running", pid=REAL_PID, session_id=session_id)
    conn.execute("UPDATE sessions SET proc_start = ? WHERE id = ?", (REAL_START, row_id))
    return item_id


@pytest.mark.parametrize("condition", [*UNUSABLE, "empty_dir"])
def test_a_blind_pass_does_not_reclaim_a_row_it_cannot_see(
    conn, audit, config, tmp_path, condition
):
    """The stale-row sweep, in both directions (US2, US4).

    Closing a row whose worker is alive reports fewer running sessions than exist, which
    oversubscribes the quota the cap protects. #28 settled that an under-count is the only
    direction of capacity error that does real harm; a blind scan reaches it from the other
    side, by making every live worker look absent at once.
    """
    item_id = stale_row(conn)
    result = run(conn, audit, config, tmp_path, condition=condition)

    session = db.latest_session_for_item(conn, item_id)
    if condition == "empty_dir":
        assert result.reclaimed == 1
        assert result.liveness_withheld == 0
        assert session.state is SessionState.LOST
    else:
        assert result.reclaimed == 0, condition
        assert result.liveness_withheld == 1, condition
        assert session.state is SessionState.RUNNING, condition


@pytest.mark.parametrize("condition", [*UNUSABLE, "empty_dir"])
def test_a_blind_pass_does_not_settle_a_superseded_attempt(
    conn, audit, config, tmp_path, condition
):
    """The superseded sweep, in both directions (US2, US4).

    An item resumed or restarted leaves its earlier attempt's row open, and #33 added the
    sweep that settles it. Its rule is the same one, so it inherited the same blindness.
    """
    item_id = seed_item(
        conn, repo_key="demo", issue_number=211, state=str(WorkItemState.ACTIVE)
    )
    old = seed_session(conn, item_id, state="running", pid=REAL_PID, session_id="s-old")
    new = seed_session(conn, item_id, state="running", pid=REAL_PID + 1, session_id="s-new")
    conn.execute(
        "UPDATE sessions SET proc_start = ? WHERE id IN (?, ?)", (REAL_START, old, new)
    )

    result = run(conn, audit, config, tmp_path, condition=condition)

    rows = {r.session_id: r.state for r in db.list_sessions_for_item(conn, item_id)}
    if condition == "empty_dir":
        assert result.superseded == 1
        assert rows["s-old"] is SessionState.LOST
    else:
        assert result.superseded == 0, condition
        # Two withheld conclusions, not one: the superseded row and the current attempt.
        assert result.liveness_withheld == 2, condition
        assert rows["s-old"] is SessionState.RUNNING, condition
        assert rows["s-new"] is SessionState.RUNNING, condition


def test_a_blind_pass_returns_no_capacity_it_cannot_account_for(
    conn, audit, config, tmp_path
):
    """SC-002, measured the way the cap measures it.

    Capacity is read against a *readable* empty registry both times, so what is being
    compared is the effect of the blind pass on the rows, not two unobservable snapshots.
    An open row is a subscribed slot; a blind pass must not hand one back.
    """
    stale_row(conn, session_id="s-a", issue_number=212)
    stale_row(conn, session_id="s-b", issue_number=213)
    readable = tmp_path / "readable"
    readable.mkdir()
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)

    def total() -> int:
        snapshot = capacity.snapshot(
            conn, config=config, registry_dir=readable, proc_root=proc
        )
        assert snapshot.observable
        return snapshot.total

    before = total()
    run(conn, audit, config, tmp_path, condition="missing_dir")

    assert before == 2
    assert total() == before


def test_reclaim_stale_session_withholds_rather_than_guessing(
    conn, audit, config, tmp_path
):
    """The fourth outcome, and why the guard lives in this function (US2).

    ``reclaim_stale_session`` already holds the rule that a worker which *can be seen* is
    reported and left open rather than closed. Blindness is a case of that same rule, so
    the guard belongs beside it rather than copied into each of its three callers -- which
    is what carries the fix into ``operations.abandon`` without a second decision.
    """
    item_id = stale_row(conn, session_id="s-lone", issue_number=214)
    session = db.latest_session_for_item(conn, item_id)
    blind = sessions.scan(registry_dir=tmp_path / "gone", proc_root=tmp_path / "proc")

    with db.transaction(conn):
        outcome = reconcile.reclaim_stale_session(
            conn, audit, session=session, scan=blind, reason="a test"
        )

    assert outcome == "withheld"
    assert db.get_session(conn, "s-lone").state is SessionState.RUNNING
    assert "orphan_session" not in kinds(conn), (
        "an orphan report is a claim about a live process, which a blind scan cannot make"
    )
