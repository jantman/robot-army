"""What each effect level actually does to the world (T112, T113).

``test_every_cell_of_the_table`` in ``tests/unit/test_effects.py`` proves the *wiring*.
This file proves the *consequence*: at ``plan``, zero GitHub writes, zero sessions, and
zero filesystem changes under the worktree root — which is quickstart scenario 1's
expectation, checked here rather than only by hand.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from tests.conftest import (
    FakeIssueReader,
    RecordingWriter,
    make_issue,
    seed_item,
)

from robot_army import db, dispatch, poll
from robot_army.effects import EffectLevel, wire
from robot_army.states import WorkItemState

pytestmark = pytest.mark.requires_git


def trust_file(tmp_path: Path, clone: Path) -> Path:
    path = tmp_path / "claude.json"
    path.write_text(
        json.dumps({"projects": {str(clone.resolve()): {"hasTrustDialogAccepted": True}}}),
        encoding="utf-8",
    )
    return path


def snapshot(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def wired_at(level: EffectLevel, config, audit, *, writer=None, reader=None):
    """Wire the real table, then substitute only the two boundaries a test must observe.

    Substituting the *reader* is unavoidable — reads are real at every level and we are
    not calling GitHub in a test. Substituting the writer at ``live`` is how the test
    counts writes without performing them.
    """
    real = wire(level, config, audit)
    return replace(
        real,
        issue_reader=reader or FakeIssueReader([make_issue()]),
        **({"issue_writer": writer} if writer is not None else {}),
    )


def test_plan_makes_no_filesystem_changes_under_the_worktree_root(
    conn, audit, config, tmp_path, layout
):
    """quickstart scenario 1's central expectation."""
    before = snapshot(config.worktree_root)
    boundaries = wired_at(EffectLevel.PLAN, config, audit)
    item_id = seed_item(conn, state=str(WorkItemState.READY), dry_run=True)

    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    assert snapshot(config.worktree_root) == before, "plan touched the worktree root"


def test_plan_creates_no_branches_in_the_repository(conn, audit, config, tmp_path, layout):
    import subprocess

    clone = config.repos["demo"].path

    def branches() -> set[str]:
        out = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            cwd=clone, capture_output=True, text=True, check=True,
        ).stdout
        return {line.strip() for line in out.splitlines() if line.strip()}

    before = branches()
    boundaries = wired_at(EffectLevel.PLAN, config, audit)
    item_id = seed_item(conn, state=str(WorkItemState.READY), dry_run=True)
    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, clone),
    )
    assert branches() == before


def test_plan_performs_zero_github_writes(conn, audit, config, tmp_path, layout):
    # The writer plan selects is the simulated one, so a real write is unreachable rather
    # than merely unlikely. The intended call still reaches the log, marked as simulated.
    assert type(wire(EffectLevel.PLAN, config, audit).issue_writer).__name__ == (
        "SimulatedIssueWriter"
    )
    boundaries = wired_at(EffectLevel.PLAN, config, audit)
    item_id = seed_item(conn, state=str(WorkItemState.READY), dry_run=True)
    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    audit.close()
    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    comments = [json.loads(line) for line in text.splitlines() if '"github.comment"' in line]
    assert comments, "the intended comment must still be visible in the log"
    assert all(record.get("simulated") for record in comments)


def test_plan_still_polls_and_evaluates_for_real(conn, audit, config):
    """FR-052. A dry run that fakes its reads tells you nothing about eligibility, which
    is the main thing you want to check."""
    with db.transaction(conn):
        db.upsert_repo(conn, repo_key="demo", settings_fingerprint=None, trust_verified=True)
    reader = FakeIssueReader([make_issue()])
    boundaries = replace(wire(EffectLevel.PLAN, config, audit), issue_reader=reader)

    outcome = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=True
    )
    assert reader.poll_calls, "polling must really have happened"
    assert outcome.created == 1
    item = db.list_work_items(conn, include_simulated=True)[0]
    assert item.state is WorkItemState.READY
    assert item.dry_run is True


def test_plan_rows_are_hidden_from_status_without_the_flag(conn, audit, config):
    """quickstart scenario 1's failure-to-watch-for: if `status` shows simulated rows
    *without* the flag, FR-056's default query scope is not enforced."""
    with db.transaction(conn):
        db.upsert_repo(conn, repo_key="demo", settings_fingerprint=None, trust_verified=True)
    reader = FakeIssueReader([make_issue()])
    boundaries = replace(wire(EffectLevel.PLAN, config, audit), issue_reader=reader)
    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=True
    )
    assert db.list_work_items(conn) == []
    assert len(db.list_work_items(conn, include_simulated=True)) == 1


def test_local_creates_a_real_worktree_but_launches_no_session(
    conn, audit, config, tmp_path, layout
):
    """The loop for getting a repository's ``post_create`` right without burning
    subscription quota (quickstart scenario 2)."""
    boundaries = wired_at(EffectLevel.LOCAL, config, audit)
    item_id = seed_item(conn, state=str(WorkItemState.READY), dry_run=True)

    assert dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    item = db.get_work_item(conn, item_id)
    assert item.state is WorkItemState.ACTIVE
    assert Path(item.worktree_path).is_dir(), "local must create a real worktree"
    assert (Path(item.worktree_path) / "README.md").exists()

    audit.close()
    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    launches = [json.loads(line) for line in text.splitlines() if '"kitty.launch"' in line]
    assert launches and all(record.get("simulated") for record in launches), (
        "local must not open a real terminal window"
    )


def test_local_still_performs_no_github_writes(conn, audit, config, tmp_path, layout):
    boundaries = wired_at(EffectLevel.LOCAL, config, audit)
    item_id = seed_item(conn, state=str(WorkItemState.READY), dry_run=True)
    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    audit.close()
    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    comments = [json.loads(line) for line in text.splitlines() if '"github.comment"' in line]
    assert all(record.get("simulated") for record in comments)


def test_a_simulated_item_still_counts_against_the_concurrency_cap(
    conn, audit, config, tmp_path, layout
):
    """T113, FR-055. They burn the same subscription quota."""
    config = replace(config, daemon=replace(config.daemon, max_concurrent_sessions=1))
    boundaries = wired_at(EffectLevel.LOCAL, config, audit)
    simulated = seed_item(
        conn, issue_number=1, state=str(WorkItemState.READY), dry_run=True
    )
    second = seed_item(conn, issue_number=2, state=str(WorkItemState.READY), dry_run=True)

    dispatched = dispatch.select_and_dispatch(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    assert dispatched == 1
    assert db.count_live_sessions(conn) == 1
    assert db.get_work_item(conn, simulated).state is WorkItemState.ACTIVE
    assert db.get_work_item(conn, second).state is WorkItemState.READY


def test_purging_simulated_rows_leaves_their_worktrees_on_disk(
    conn, audit, config, tmp_path, layout
):
    """Those are real directories, and removing them is ``worktree remove``'s job —
    deliberately separate so purging is never destructive."""
    boundaries = wired_at(EffectLevel.LOCAL, config, audit)
    item_id = seed_item(conn, state=str(WorkItemState.READY), dry_run=True)
    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    worktree_path = Path(db.get_work_item(conn, item_id).worktree_path)
    assert worktree_path.is_dir()

    with db.transaction(conn):
        purged = db.purge_simulated(conn)
    assert purged["work_items"] == 1
    assert worktree_path.is_dir(), "purge must not touch the filesystem"


def test_live_is_the_only_level_that_writes_to_github(conn, audit, config, tmp_path, layout):
    writer = RecordingWriter()
    boundaries = wired_at(EffectLevel.LIVE, config, audit, writer=writer)
    item_id = seed_item(conn, state=str(WorkItemState.READY), dry_run=False)

    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    assert writer.comments, "live must post the dispatch comment"
    assert writer.comments[0][0] == "demo"


def test_a_simulated_item_traverses_the_same_states_as_a_live_one(
    conn, audit, config, tmp_path, layout
):
    """FR-054: simulated work must be *observably progressing through the same states by
    the same code path*, not shunted into a failure branch because no real directory
    exists for it. This is what the ``worktree_exists`` boundary method buys."""
    boundaries = wired_at(EffectLevel.PLAN, config, audit)
    item_id = seed_item(conn, state=str(WorkItemState.READY), dry_run=True)

    assert dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    item = db.get_work_item(conn, item_id)
    assert item.state is WorkItemState.ACTIVE
    assert item.dispatching_at is not None and item.active_at is not None
    assert snapshot(config.worktree_root) == set(), "still no filesystem changes"
