"""A session row that outlives its work item, and the slot it holds (issue #28).

Three things are being defended here and they are not equally important.

The first is that the slot comes back at all: a ``running`` row under an item nothing is
running for holds a global and a per-repository capacity slot that no sweep could reach,
and with the shipped ``default_repo_max_sessions = 1`` that stops the repository
dispatching forever, reporting ``repo_cap`` — which reads as the cap working correctly.

The second is the opposite failure, and it is the dangerous one. ``interrupted`` has never
meant "nothing is running" (M0 F17): a worker whose wrapper died keeps going, reparented.
Closing *that* row would make the reported capacity lower than the number of live workers,
which oversubscribes the one subscription the cap exists to protect. An under-count is the
only capacity error that causes harm, so the rule declines to close a row it can see is
alive and raises an anomaly instead.

The third is that neither of the first two may be re-derived at each call site. ``cancel``,
``abandon`` and the reconciliation sweep are three callers of one function, and the tests
below exercise the function directly as well as through all three.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import seed_item, seed_session, write_proc, write_registry

from robot_army import capacity, db, operations, reconcile, sessions
from robot_army.states import SessionState, WorkItemState, utcnow

REPO = "jantman/robot-army"


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    """An empty-but-present registry: the genuinely idle machine."""
    directory = tmp_path / "registry"
    directory.mkdir()
    return directory


@pytest.fixture
def proc(tmp_path: Path) -> Path:
    """A /proc holding one non-worker process, so enumeration demonstrably works."""
    root = tmp_path / "proc"
    write_proc(root, 1, starttime="1", exe="/usr/lib/systemd/systemd")
    return root


def scan_of(registry: Path, proc: Path) -> sessions.RegistryScan:
    return sessions.scan(registry_dir=registry, proc_root=proc)


def apply(conn, audit, item_id, registry, proc, *, reason="test"):
    """Run the decision on the item's latest session row."""
    session = db.latest_session_for_item(conn, item_id)
    assert session is not None
    with db.transaction(conn):
        return reconcile.reclaim_stale_session(
            conn,
            audit,
            session=session,
            scan=scan_of(registry, proc),
            proc_root=proc,
            reason=reason,
        )


def audit_actions(layout, action: str) -> list[dict]:
    lines = "\n".join(
        path.read_text(encoding="utf-8") for path in layout.log_dir.glob("*.jsonl")
    )
    out = []
    for line in lines.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("action") == action:
            out.append(record)
    return out


def live_worker(registry: Path, proc: Path, config, *, pid: int, session_id: str) -> str:
    """A registry entry whose process really is alive, under the worktree root."""
    cwd = Path(config.worktree_root) / "somewhere"
    cwd.mkdir(parents=True, exist_ok=True)
    write_registry(registry, pid=pid, session_id=session_id, proc_start=str(pid), cwd=str(cwd))
    write_proc(proc, pid, starttime=str(pid), cwd=str(cwd))
    return str(cwd)


# -- the three branches of the decision (contracts/slot-reclamation.md C1) ---


@pytest.mark.parametrize("item_state", ["dispatching", "active"])
def test_an_open_row_under_a_running_item_is_left_alone(
    conn, config, audit, registry, proc, item_state
):
    """The two states that may legitimately hold an open row. Sweeping these would end
    live work, which is a far worse bug than the one being fixed."""
    item = seed_item(conn, repo_key=REPO, dry_run=True, state=item_state)
    seed_session(conn, item, state="running", dry_run=True, pid=0)

    assert apply(conn, audit, item, registry, proc) == "left"
    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING


def test_a_simulated_row_under_an_interrupted_item_is_reclaimed(
    conn, config, audit, layout, registry, proc
):
    """The reported case. A simulated session has no process and no wrapper, so no exit
    record can ever arrive; nothing but this rule will ever close the row."""
    item = seed_item(conn, repo_key=REPO, dry_run=True, state="interrupted")
    seed_session(conn, item, state="running", dry_run=True, pid=0)

    assert apply(conn, audit, item, registry, proc, reason="cancelled") == "reclaimed"

    session = db.latest_session_for_item(conn, item)
    assert session.state is SessionState.LOST
    assert session.ended_at is not None
    # The row keeps its place in the item's history rather than being deleted (FR-010).
    assert len(db.list_sessions_for_item(conn, item)) == 1

    records = audit_actions(layout, "state.session")
    assert len(records) == 1
    assert records[0]["detail"]["to"] == "lost"
    assert "cancelled" in records[0]["detail"]["reason"]


def test_a_live_worker_is_reported_and_never_closed(
    conn, config, audit, registry, proc
):
    """FR-005. The row stays open because the worker really is holding the slot."""
    item = seed_item(conn, repo_key=REPO, state="interrupted")
    seed_session(conn, item, state="running", pid=777, session_id="live-1")
    live_worker(registry, proc, config, pid=777, session_id="live-1")

    assert apply(conn, audit, item, registry, proc) == "reported"

    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING
    kinds = [(a.kind, a.entity_id) for a in db.list_anomalies(conn)]
    assert ("orphan_session", "live-1") in kinds


# -- T004: the safety property the live branch exists for --------------------


def test_declining_to_close_keeps_the_count_from_erring_downward(
    conn, config, audit, registry, proc
):
    """An under-count is the only capacity error that causes harm. After the decision
    declines, the machine must still report the worker that is genuinely running."""
    item = seed_item(conn, repo_key=REPO, state="interrupted")
    seed_session(conn, item, state="running", pid=778, session_id="live-2")
    live_worker(registry, proc, config, pid=778, session_id="live-2")

    before = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert apply(conn, audit, item, registry, proc) == "reported"
    after = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)

    assert after.total == before.total == 1
    assert after.per_repo == {REPO: 1}


def test_reclaiming_releases_the_slot_the_row_was_holding(
    conn, config, audit, registry, proc
):
    item = seed_item(conn, repo_key=REPO, dry_run=True, state="interrupted")
    seed_session(conn, item, state="running", dry_run=True, pid=0)

    before = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert before.total == 1
    assert before.per_repo == {REPO: 1}

    apply(conn, audit, item, registry, proc)

    after = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert after.total == 0
    assert after.per_repo == {}


# -- T005: idempotency (FR-008) ---------------------------------------------


def test_a_reclaimed_row_is_not_reclaimed_twice(conn, config, audit, layout, registry, proc):
    item = seed_item(conn, repo_key=REPO, dry_run=True, state="interrupted")
    seed_session(conn, item, state="running", dry_run=True, pid=0)

    assert apply(conn, audit, item, registry, proc) == "reclaimed"
    assert apply(conn, audit, item, registry, proc) == "left"

    # One record, not two: transition_session no-ops when source equals target, and a
    # `lost` row is not open any more so the rule has nothing to decide about it.
    assert len(audit_actions(layout, "state.session")) == 1


def test_a_persistently_live_worker_raises_one_anomaly_not_one_per_pass(
    conn, config, audit, registry, proc
):
    """A 60-second reconciliation cycle must not turn one condition into 1,440 rows."""
    item = seed_item(conn, repo_key=REPO, state="interrupted")
    seed_session(conn, item, state="running", pid=779, session_id="live-3")
    live_worker(registry, proc, config, pid=779, session_id="live-3")

    for _ in range(3):
        assert apply(conn, audit, item, registry, proc) == "reported"

    matching = [a for a in db.list_anomalies(conn) if a.entity_id == "live-3"]
    assert len(matching) == 1


# -- User Story 1: cancel gives the slot back --------------------------------


def context(conn, config, audit, boundaries) -> operations.Context:
    return operations.Context(
        conn=conn,
        config=config,
        audit=audit,
        boundaries=boundaries,
        effect_level=boundaries.level,
    )


def test_cancel_releases_the_slot_before_it_returns(
    conn, config, audit, layout, boundaries, registry, proc
):
    """FR-012. No reconciliation pass is run anywhere in this test, and none may be: the
    rehearsal this bug was found in is CLI-only, with nothing sweeping on a timer."""
    item = seed_item(conn, repo_key=REPO, dry_run=True, state="active")
    seed_session(conn, item, state="running", dry_run=True, pid=0)

    ctx = context(conn, config, audit, boundaries)
    result = operations.cancel(ctx, item, force=True, registry_dir=registry)
    assert result.code == 0

    session = db.latest_session_for_item(conn, item)
    assert session.state is SessionState.LOST
    assert session.ended_at is not None
    assert db.get_work_item(conn, item).state is WorkItemState.INTERRUPTED

    snap = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert snap.total == 0
    assert snap.per_repo == {}

    reason = audit_actions(layout, "state.session")[-1]["detail"]["reason"]
    assert "cancel" in reason.lower()


def test_cancel_leaves_a_live_worker_holding_its_slot(
    conn, config, audit, boundaries, registry, proc
):
    """The process group was signalled, but a reparented worker can survive it. Until it
    is actually gone the slot is genuinely taken, and saying otherwise would over-dispatch."""
    item = seed_item(conn, repo_key=REPO, state="active")
    seed_session(conn, item, state="running", pid=780, session_id="live-cancel")
    live_worker(registry, proc, config, pid=780, session_id="live-cancel")

    ctx = context(conn, config, audit, boundaries)
    assert operations.cancel(
        ctx, item, force=True, registry_dir=registry, proc_root=proc
    ).code == 0

    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING
    assert db.get_work_item(conn, item).state is WorkItemState.INTERRUPTED
    snap = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert snap.total == 1


def test_cancel_does_not_touch_the_worktree_or_the_item_beyond_interrupted(
    conn, config, audit, boundaries, registry
):
    item = seed_item(conn, repo_key=REPO, dry_run=True, state="active")
    with db.transaction(conn):
        db.update_work_item_columns(conn, item, worktree_path="/somewhere/on/disk")
    seed_session(conn, item, state="running", dry_run=True, pid=0)

    ctx = context(conn, config, audit, boundaries)
    operations.cancel(ctx, item, force=True, registry_dir=registry)

    assert db.get_work_item(conn, item).worktree_path == "/somewhere/on/disk"


# -- User Story 2: abandon holds nothing -------------------------------------


def test_abandon_releases_the_slot_and_leaves_the_worktree(
    conn, config, audit, boundaries, registry, proc
):
    """The second route out of `active`, confirmed on the issue. `abandon` stops no
    process — it never could, since it is not reachable from `active` — so the row it
    finds open is one nothing is running for."""
    item = seed_item(conn, repo_key=REPO, dry_run=True, state="interrupted")
    with db.transaction(conn):
        db.update_work_item_columns(conn, item, worktree_path="/w/demo/issue-42")
    seed_session(conn, item, state="running", dry_run=True, pid=0)

    ctx = context(conn, config, audit, boundaries)
    assert operations.abandon(ctx, item, registry_dir=registry, proc_root=proc).code == 0

    assert db.get_work_item(conn, item).state is WorkItemState.ABANDONED
    assert db.latest_session_for_item(conn, item).state is SessionState.LOST
    # Reclaiming a slot touches nothing on disk (FR-011).
    assert db.get_work_item(conn, item).worktree_path == "/w/demo/issue-42"

    snap = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert snap.total == 0


def test_cancel_then_abandon_closes_the_row_once_and_does_not_error(
    conn, config, audit, layout, boundaries, registry, proc
):
    """The sequence from the issue's own comment. The second command finds the row already
    closed, which is a no-op rather than an illegal transition."""
    item = seed_item(conn, repo_key=REPO, dry_run=True, state="active")
    seed_session(conn, item, state="running", dry_run=True, pid=0)
    ctx = context(conn, config, audit, boundaries)

    assert operations.cancel(
        ctx, item, force=True, registry_dir=registry, proc_root=proc
    ).code == 0
    assert operations.abandon(ctx, item, registry_dir=registry, proc_root=proc).code == 0

    assert db.get_work_item(conn, item).state is WorkItemState.ABANDONED
    assert db.latest_session_for_item(conn, item).state is SessionState.LOST
    assert len(audit_actions(layout, "state.session")) == 1


def test_abandon_leaves_a_live_worker_holding_its_slot(
    conn, config, audit, boundaries, registry, proc
):
    """FR-005 is a property of the rule, not of the caller: `abandon` stops nothing, so a
    worker under the item it abandons is if anything more likely to still be running."""
    item = seed_item(conn, repo_key=REPO, state="awaiting_review")
    seed_session(conn, item, state="running", pid=781, session_id="live-abandon")
    live_worker(registry, proc, config, pid=781, session_id="live-abandon")

    ctx = context(conn, config, audit, boundaries)
    assert operations.abandon(ctx, item, registry_dir=registry, proc_root=proc).code == 0

    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING
    assert capacity.snapshot(
        conn, config=config, registry_dir=registry, proc_root=proc
    ).total == 1


# -- User Story 3: the sweep, and rows leaked before any of this existed ------


def sweep(conn, boundaries, audit, config, layout, registry, proc):
    return reconcile.reconcile(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        registry_dir=registry,
        proc_root=proc,
    )


def leaked_row(conn, *, state: str = "interrupted", issue_number: int = 42) -> int:
    """A database in the state `cancel` used to leave behind: the item moved on, the row
    did not, and no sweep could reach it because the session sweep iterates `active`."""
    item = seed_item(
        conn, repo_key=REPO, issue_number=issue_number, dry_run=True, state=state
    )
    seed_session(conn, item, state="running", dry_run=True, pid=0)
    return item


def test_the_sweep_reclaims_a_row_leaked_before_the_fix_existed(
    conn, boundaries, audit, config, layout, registry, proc
):
    item = leaked_row(conn)
    assert capacity.snapshot(
        conn, config=config, registry_dir=registry, proc_root=proc
    ).per_repo == {REPO: 1}

    result = sweep(conn, boundaries, audit, config, layout, registry, proc)

    assert result.reclaimed == 1
    assert db.latest_session_for_item(conn, item).state is SessionState.LOST
    assert capacity.snapshot(
        conn, config=config, registry_dir=registry, proc_root=proc
    ).per_repo == {}


def test_the_pass_summary_reports_the_reclamation_rather_than_nothing(
    conn, boundaries, audit, config, layout, registry, proc
):
    """FR-009. The issue's `checked 0` was the misleading half of the report: a pass that
    did work must not read as a pass that examined nothing."""
    leaked_row(conn)
    summary = sweep(conn, boundaries, audit, config, layout, registry, proc).summary()
    assert summary["reclaimed"] == 1


def test_a_second_pass_reclaims_nothing_and_writes_no_second_record(
    conn, boundaries, audit, config, layout, registry, proc
):
    leaked_row(conn)
    assert sweep(conn, boundaries, audit, config, layout, registry, proc).reclaimed == 1
    assert sweep(conn, boundaries, audit, config, layout, registry, proc).reclaimed == 0
    assert len(audit_actions(layout, "state.session")) == 1


def test_the_sweep_discards_no_work_item_and_no_session_history(
    conn, boundaries, audit, config, layout, registry, proc
):
    """The whole point of FR-004: today the only command that clears such a row is
    `purge-simulated`, which offers to delete every simulated item and every tracked card."""
    first = leaked_row(conn, issue_number=1)
    second = leaked_row(conn, issue_number=2)

    sweep(conn, boundaries, audit, config, layout, registry, proc)

    items = db.list_work_items(conn, include_simulated=True)
    assert {i.id for i in items} >= {first, second}
    assert len(db.list_sessions_for_item(conn, first)) == 1
    assert len(db.list_sessions_for_item(conn, second)) == 1


def test_the_sweep_leaves_a_dispatching_or_active_item_alone(
    conn, boundaries, audit, config, layout, registry, proc
):
    """The states that legitimately hold an open row. Sweeping these would end live work.

    The `dispatching` row needs a real `dispatching_at`: an item with none is infinitely
    old by construction, so the `dispatching_max_age_seconds` reaper fails it earlier in
    the same pass — at which point its row *is* stale and reclaiming it is correct. Dating
    it is what makes this a test of the allow-list rather than of the reaper.
    """
    dispatching = leaked_row(conn, state="dispatching", issue_number=1)
    with db.transaction(conn):
        db.update_work_item_columns(conn, dispatching, dispatching_at=utcnow())
    active = leaked_row(conn, state="active", issue_number=2)

    result = sweep(conn, boundaries, audit, config, layout, registry, proc)

    assert result.reclaimed == 0
    assert db.latest_session_for_item(conn, dispatching).state is SessionState.RUNNING
    assert db.latest_session_for_item(conn, active).state is SessionState.RUNNING


def test_a_live_worker_under_a_done_item_is_counted_as_an_orphan_not_a_reclamation(
    conn, boundaries, audit, config, layout, registry, proc
):
    """Research R6. Closing an issue while its worker is still running reaches `done` with
    a genuinely live session. The row is left open — the slot really is taken — and the
    condition is reported, which is exactly what M0 F17 says must not pass silently."""
    item = seed_item(conn, repo_key=REPO, state="done")
    seed_session(conn, item, state="running", pid=782, session_id="live-done")
    live_worker(registry, proc, config, pid=782, session_id="live-done")

    result = sweep(conn, boundaries, audit, config, layout, registry, proc)

    assert result.reclaimed == 0
    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING
    assert any(a.entity_id == "live-done" for a in db.list_anomalies(conn))


def test_reclaiming_discards_no_tracked_intake_card(
    conn, boundaries, audit, config, layout, registry, proc
):
    """The escape hatch this replaces would have cost all of them. `purge-simulated` is the
    only command that clears such a row today, and below `live` every row is `dry_run = 1`,
    so recovering one slot means discarding every work item and every tracked card."""
    item = leaked_row(conn)
    with db.transaction(conn):
        for n in range(3):
            db.insert_card(
                conn,
                board_id="board-1",
                card_id=f"card-{n}",
                card_url=f"https://trello.com/c/card-{n}",
                title=f"card {n}",
                body="body",
                dry_run=True,
            )

    sweep(conn, boundaries, audit, config, layout, registry, proc)

    assert db.latest_session_for_item(conn, item).state is SessionState.LOST
    assert len(db.list_cards(conn, include_simulated=True)) == 3


def test_a_reclaimed_row_is_still_what_a_resume_restores_from(
    conn, boundaries, audit, config, layout, registry, proc
):
    """FR-010. `operations.resume` reads `latest_session_for_item(...).session_id` and
    requires the item to be `interrupted` or `awaiting_review` — it never requires the
    session to still be open. Reclaiming transitions the row; it must never delete it."""
    item = leaked_row(conn)
    before = db.latest_session_for_item(conn, item)

    sweep(conn, boundaries, audit, config, layout, registry, proc)

    after = db.latest_session_for_item(conn, item)
    assert after is not None
    assert after.session_id == before.session_id
    assert after.id == before.id
    assert db.get_work_item(conn, item).state is WorkItemState.INTERRUPTED


def test_two_passes_over_a_live_worker_leave_it_alone_and_report_it_once(
    conn, boundaries, audit, config, layout, registry, proc
):
    """Quickstart scenario 3, through a full pass rather than the decision alone.

    This is the condition that is entirely invisible today: `_orphan_sweep` skips any
    registry entry whose session row is `running`, which is precisely this one, so before
    this feature a pass reported `orphans: 0` and raised nothing at all.
    """
    item = seed_item(conn, repo_key=REPO, state="interrupted")
    seed_session(conn, item, state="running", pid=783, session_id="live-pass")
    live_worker(registry, proc, config, pid=783, session_id="live-pass")

    first = sweep(conn, boundaries, audit, config, layout, registry, proc)
    second = sweep(conn, boundaries, audit, config, layout, registry, proc)

    assert first.reclaimed == 0
    assert second.reclaimed == 0
    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING
    # Never lower than the number of live workers, on either pass.
    assert capacity.snapshot(
        conn, config=config, registry_dir=registry, proc_root=proc
    ).total == 1
    matching = [a for a in db.list_anomalies(conn) if a.entity_id == "live-pass"]
    assert len(matching) == 1
