"""A hand-deleted worktree on a finished item is reported, and settleable (issue #113).

The missing-worktree sweep used to skip ``done`` and ``abandoned`` items, so with cleanup off
— the default — a finished item whose directory was deleted by hand was reported by nothing
while ``git worktree list`` said ``prunable``. The state filter was standing in for the real
distinction, which is the cleanup record: cleanup keeps ``worktree_path`` after removing a
worktree, so only a record saying so separates "removed" from "deleted by hand".

See ``specs/20260915-174645-terminal-prunable-worktree/contracts/manual-removal-record.md``.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import db, reconcile
from robot_army.boundaries.git import SimulatedVersionControl
from robot_army.states import WorkItemState

BRANCH = "robot-army/issue-42-fix"
UNFINISHED_NOTE = "directory is gone; `robot-army worktree prune` clears git's record"


def run(conn, audit, config, tmp_path, *, vcs: Any = None):
    (tmp_path / "registry").mkdir(exist_ok=True)
    (tmp_path / "proc").mkdir(exist_ok=True)
    return reconcile.reconcile(
        conn,
        boundaries=make_boundaries(audit, vcs=vcs),
        audit=audit,
        config=config,
        layout=config.layout,
        registry_dir=tmp_path / "registry",
        proc_root=tmp_path / "proc",
    )


def item_with_missing_worktree(
    conn, config, *, state: WorkItemState, cleanup_state: str | None = None
) -> int:
    """An item whose recorded worktree directory does not exist."""
    item_id = seed_item(conn, state=str(state))
    gone = config.worktree_root / "demo" / f"issue-{item_id}"
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, worktree_path=str(gone), branch=BRANCH)
        if cleanup_state is not None:
            db.record_cleanup(conn, item_id, state=cleanup_state, reason="seeded")
    return item_id


def prunable(conn, *, unacknowledged_only: bool = True) -> list[Any]:
    return [
        a
        for a in db.list_anomalies(
            conn, include_simulated=True, unacknowledged_only=unacknowledged_only
        )
        if a.kind == "prunable_worktree"
    ]


class CountingVcs(SimulatedVersionControl):
    """Counts the clones the sweep asks git to list."""

    def __init__(self, audit) -> None:
        super().__init__(audit)
        self.listed: list[str] = []

    def list_worktrees(self, clone_path: str):
        self.listed.append(clone_path)
        return super().list_worktrees(clone_path)


# -- the predicate (T003) -------------------------------------------------------


@pytest.mark.parametrize(
    ("cleanup_state", "reclaimed"),
    [
        (None, False),
        ("skipped", False),
        ("retained", False),
        ("branch_retained", True),
        ("done", True),
    ],
)
def test_only_a_removal_on_record_counts_as_reclaimed(conn, config, cleanup_state, reclaimed):
    """``retained`` and ``skipped`` mean the worktree was *kept*; a missing directory is as
    unexplained under them as under no record at all."""
    item_id = item_with_missing_worktree(
        conn, config, state=WorkItemState.DONE, cleanup_state=cleanup_state
    )
    assert db.get_work_item(conn, item_id).worktree_reclaimed is reclaimed


# -- the sweep (T004; contract M1-M4) -----------------------------------------


@pytest.mark.parametrize("state", [WorkItemState.DONE, WorkItemState.ABANDONED])
def test_a_finished_items_hand_deleted_worktree_is_reported(
    conn, audit, config, tmp_path, state
):
    """The reported case: scenario 10's item d, with cleanup off."""
    item_id = item_with_missing_worktree(conn, config, state=state)

    result = run(conn, audit, config, tmp_path)

    assert result.prunable == 1
    [anomaly] = prunable(conn)
    assert anomaly.entity_id == str(item_id)
    assert anomaly.detail_obj["state"] == str(state)
    assert anomaly.detail_obj["branch"] == BRANCH


@pytest.mark.parametrize("cleanup_state", ["retained", "skipped"])
def test_a_kept_worktree_that_then_went_missing_is_reported(
    conn, audit, config, tmp_path, cleanup_state
):
    """M3. "We looked and kept it" does not account for it being gone now."""
    item_with_missing_worktree(
        conn, config, state=WorkItemState.DONE, cleanup_state=cleanup_state
    )
    assert run(conn, audit, config, tmp_path).prunable == 1


@pytest.mark.parametrize("cleanup_state", ["done", "branch_retained"])
@pytest.mark.parametrize(
    "state", [WorkItemState.DONE, WorkItemState.ABANDONED, WorkItemState.INTERRUPTED]
)
def test_a_worktree_the_record_says_was_removed_is_never_reported(
    conn, audit, config, tmp_path, cleanup_state, state
):
    """M2, the reason the state filter existed: cleanup keeps the path after a removal, so
    dropping the filter alone would report every worktree cleanup legitimately removed."""
    item_with_missing_worktree(conn, config, state=state, cleanup_state=cleanup_state)

    for _ in range(3):
        assert run(conn, audit, config, tmp_path).prunable == 0
    assert prunable(conn, unacknowledged_only=False) == []


def test_a_reclaimed_item_does_not_send_the_sweep_to_git(conn, audit, config, tmp_path):
    """M2. There is nothing to ask git about a worktree the record says is gone."""
    item_with_missing_worktree(
        conn, config, state=WorkItemState.DONE, cleanup_state="done"
    )
    vcs = CountingVcs(audit)

    run(conn, audit, config, tmp_path, vcs=vcs)

    assert vcs.listed == []


def test_repeated_passes_raise_one_anomaly(conn, audit, config, tmp_path):
    item_with_missing_worktree(conn, config, state=WorkItemState.DONE)
    for _ in range(4):
        run(conn, audit, config, tmp_path)
    assert len(prunable(conn)) == 1


def test_a_done_items_note_names_both_ways_to_settle_it(conn, audit, config, tmp_path):
    """M4. An acknowledged report comes back while the row is unchanged, so the note has to
    say what changes it."""
    item_id = item_with_missing_worktree(conn, config, state=WorkItemState.DONE)
    run(conn, audit, config, tmp_path)

    note = prunable(conn)[0].detail_obj["note"]
    assert f"robot-army worktree remove {item_id}" in note
    assert f"robot-army cleanup {item_id}" in note


def test_an_abandoned_items_note_does_not_offer_cleanup(conn, audit, config, tmp_path):
    """Cleanup considers ``done`` items only, so naming it for an abandoned one would send
    the maintainer to a command that answers "not eligible"."""
    item_id = item_with_missing_worktree(conn, config, state=WorkItemState.ABANDONED)
    run(conn, audit, config, tmp_path)

    note = prunable(conn)[0].detail_obj["note"]
    assert f"robot-army worktree remove {item_id}" in note
    assert "cleanup" not in note


def test_an_unfinished_items_note_is_unchanged(conn, audit, config, tmp_path):
    item_with_missing_worktree(conn, config, state=WorkItemState.INTERRUPTED)
    run(conn, audit, config, tmp_path)

    assert prunable(conn)[0].detail_obj["note"] == UNFINISHED_NOTE
