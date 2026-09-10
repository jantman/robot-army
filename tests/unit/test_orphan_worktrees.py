"""Worktrees no work item claims: reported, retracted, and removable by path (issue #59).

``prunable_worktree`` has always answered "is there a directory for this row?". Nothing
asked the reverse, so after ``purge-simulated`` deleted a round's rows its worktrees sat
under the root — 80 MB of them — while every pass reported ``prunable_worktrees 0`` and
``worktree list`` said "no worktrees recorded".

``ListingVcs`` stands in for git where git's answer matters: it lists the worktrees it is
told about, and really deletes a directory it is asked to remove, so removal is asserted
against the filesystem.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from tests.conftest import make_boundaries, seed_item

from robot_army import db, reconcile, worktree
from robot_army.audit import read_records
from robot_army.boundaries import BoundaryError, RemovalResult, WorktreeInfo
from robot_army.boundaries.git import SimulatedVersionControl
from robot_army.states import WorkItemState

DIRTY = "fatal: '...' contains modified or untracked files, use --force to delete it"


class ListingVcs(SimulatedVersionControl):
    """Git that lists the worktrees it is given (path → branch) and really removes them."""

    def __init__(
        self,
        audit: Any,
        *,
        worktrees: dict[str, str | None] | None = None,
        fail: bool = False,
        refuse: set[str] | None = None,
    ) -> None:
        super().__init__(audit)
        self.worktrees = dict(worktrees or {})
        self.fail = fail
        self.refuse = refuse or set()
        self.listed = 0

    def list_worktrees(self, clone_path: str) -> list[WorktreeInfo]:
        self.listed += 1
        if self.fail:
            raise BoundaryError("git worktree list timed out")
        return [
            WorktreeInfo(path=path, branch=branch, head=None)
            for path, branch in self.worktrees.items()
        ]

    def remove_worktree(
        self, worktree_path: str, force: bool = False, clone_path: str | None = None
    ) -> RemovalResult:
        super().remove_worktree(worktree_path, force=force, clone_path=clone_path)
        if worktree_path in self.refuse and not force:
            return RemovalResult(
                worktree_removed=False, branch_deleted=False, refused_reason=DIRTY
            )
        shutil.rmtree(worktree_path)
        return RemovalResult(worktree_removed=True, branch_deleted=False)


def onboard(conn) -> None:
    """Onboard ``demo`` with one item that has no worktree, so it claims nothing."""
    seed_item(conn, issue_number=1)


def make_dir(config, name: str, *, repo: str = "demo") -> Path:
    path = Path(config.worktree_root) / repo / name
    path.mkdir(parents=True)
    (path / "notes.txt").write_text("work\n", encoding="utf-8")
    return path


def claim(conn, path: str | Path, *, issue_number: int, **item: Any) -> int:
    item_id = seed_item(conn, issue_number=issue_number, **item)
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, worktree_path=str(path))
    return item_id


def sweep(conn, audit, config, *, vcs: Any = None) -> int:
    return reconcile._sweep_orphan_worktrees(
        conn,
        boundaries=make_boundaries(audit, vcs=vcs or ListingVcs(audit)),
        audit=audit,
        config=config,
    )


def open_orphans(conn) -> list[Any]:
    return [a for a in db.list_anomalies(conn) if a.kind == "orphan_worktree"]


def records(layout, action: str | None = None) -> list[dict[str, Any]]:
    return [
        record
        for record, _ in read_records(layout.log_dir)
        if record is not None and (action is None or record["action"] == action)
    ]


# -- what counts as an orphan (T012) ------------------------------------------


def test_an_unclaimed_issue_directory_is_an_orphan(conn, config):
    onboard(conn)
    orphan = make_dir(config, "issue-99")

    assert worktree.orphans(conn, config) == [orphan.resolve()]


def test_a_claimed_directory_is_not_an_orphan_whoever_claims_it(conn, config):
    """Real, rehearsed, and a ``done`` item cleanup has not reached: each is still
    reachable through ``worktree remove <id>``, which is what an orphan is not."""
    onboard(conn)
    claim(conn, make_dir(config, "issue-42"), issue_number=42)
    claim(conn, make_dir(config, "issue-43"), issue_number=43, dry_run=True)
    claim(
        conn,
        make_dir(config, "issue-44"),
        issue_number=44,
        state=str(WorkItemState.DONE),
    )

    assert worktree.orphans(conn, config) == []


def test_only_the_shape_robot_army_creates_is_considered(conn, config, tmp_path):
    """The root may be shared with the maintainer's own checkouts; none of these are ours."""
    onboard(conn)
    make_dir(config, "my-checkout")
    make_dir(config, "issue-x")
    make_dir(config, "issue-1", repo="unrelated")  # a folder no onboarded repository owns
    (Path(config.worktree_root) / "demo" / "issue-3").write_text("a file", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (Path(config.worktree_root) / "demo" / "issue-7").symlink_to(elsewhere)

    assert worktree.orphans(conn, config) == []


def test_a_repository_that_is_not_onboarded_has_no_orphans(conn, config):
    make_dir(config, "issue-9")

    assert worktree.orphans(conn, config) == []


def test_paths_are_compared_resolved(conn, config, tmp_path):
    """A stored path with a trailing slash, or written through a symlinked root, still
    claims its directory — otherwise every such item would be reported as an orphan."""
    onboard(conn)
    trailing = make_dir(config, "issue-42")
    via_alias = make_dir(config, "issue-43")
    alias = tmp_path / "alias"
    alias.symlink_to(Path(config.worktree_root))
    claim(conn, f"{trailing}/", issue_number=42)
    claim(conn, alias / "demo" / "issue-43", issue_number=43)

    assert via_alias.is_dir()
    assert worktree.orphans(conn, config) == []


def test_no_worktree_root_means_nothing_to_report(conn, config):
    onboard(conn)
    assert not Path(config.worktree_root).exists()

    assert worktree.orphans(conn, config) == []


# -- the sweep (T013) ----------------------------------------------------------


def test_an_orphan_is_reported_with_the_command_that_removes_it(conn, audit, config):
    onboard(conn)
    orphan = make_dir(config, "issue-99").resolve()
    vcs = ListingVcs(audit, worktrees={str(orphan): "robot-army/99-thing"})

    assert sweep(conn, audit, config, vcs=vcs) == 1

    (anomaly,) = open_orphans(conn)
    assert anomaly.entity_type == "worktree"
    assert anomaly.entity_id == str(orphan)
    assert anomaly.dry_run is False, "no row to take a run's flag from; visible is safe"
    detail = anomaly.detail_obj
    assert detail["path"] == str(orphan)
    assert detail["listed_by"] == "demo"
    assert detail["branch"] == "robot-army/99-thing"
    assert f"robot-army worktree remove {orphan}" in detail["note"]
    assert orphan.is_dir(), "reported, never removed"


def test_a_standing_orphan_is_one_anomaly_not_one_per_pass(conn, audit, config):
    onboard(conn)
    make_dir(config, "issue-99")

    assert sweep(conn, audit, config) == 1
    assert sweep(conn, audit, config) == 0
    assert sweep(conn, audit, config) == 0
    assert len(open_orphans(conn)) == 1


def test_a_directory_no_clone_lists_is_reported_but_not_offered_for_removal(
    conn, audit, config
):
    onboard(conn)
    make_dir(config, "issue-99")

    sweep(conn, audit, config)

    detail = open_orphans(conn)[0].detail_obj
    assert detail["listed_by"] is None and detail["branch"] is None
    assert "worktree remove" not in detail["note"]
    assert "will not remove it" in detail["note"]


def test_a_clone_that_cannot_be_listed_does_not_hide_an_orphan(
    conn, audit, config, layout
):
    """The verdict comes from the disk and the database; git only adds detail. A failed
    listing is recorded once per pass, not once per directory."""
    onboard(conn)
    make_dir(config, "issue-98")
    make_dir(config, "issue-99")
    vcs = ListingVcs(audit, fail=True)

    assert sweep(conn, audit, config, vcs=vcs) == 2

    assert all(a.detail_obj["listing_failed"] is True for a in open_orphans(conn))
    assert len(records(layout, "reconcile.list_worktrees")) == 1
    assert vcs.listed == 1, "each clone is listed once per pass, failure included"


def test_a_full_pass_counts_new_orphans_in_its_summary(conn, audit, config, tmp_path):
    onboard(conn)
    make_dir(config, "issue-99")
    (tmp_path / "registry").mkdir()
    (tmp_path / "proc").mkdir()

    def run() -> Any:
        return reconcile.reconcile(
            conn,
            boundaries=make_boundaries(audit, vcs=ListingVcs(audit)),
            audit=audit,
            config=config,
            layout=config.layout,
            registry_dir=tmp_path / "registry",
            proc_root=tmp_path / "proc",
        )

    first = run()
    assert first.orphan_worktrees == 1
    assert first.summary()["orphan_worktrees"] == 1
    assert run().summary()["orphan_worktrees"] == 0


# -- retraction (T014) ---------------------------------------------------------


def resolve(conn, audit) -> int:
    return reconcile._resolve_orphan_worktree_anomalies(conn, audit=audit)


def test_an_orphan_that_is_gone_retracts_itself(conn, audit, config, layout):
    onboard(conn)
    orphan = make_dir(config, "issue-99")
    sweep(conn, audit, config)
    shutil.rmtree(orphan)

    assert resolve(conn, audit) == 1
    assert open_orphans(conn) == []
    (record,) = records(layout, "anomaly.resolved")
    assert record["detail"]["kind"] == "orphan_worktree"
    assert record["detail"]["reason"] == "directory_gone"
    assert record["detail"]["anomaly_entity_id"] == str(orphan.resolve())


def test_an_orphan_a_work_item_claims_retracts_itself(conn, audit, config, layout):
    onboard(conn)
    orphan = make_dir(config, "issue-99")
    sweep(conn, audit, config)
    item_id = claim(conn, orphan, issue_number=99)

    assert resolve(conn, audit) == 1
    (record,) = records(layout, "anomaly.resolved")
    assert record["detail"]["reason"] == "claimed_by_item"
    assert record["detail"]["claimed_by_item"] == item_id


def test_an_orphan_still_there_and_unclaimed_stays_open(conn, audit, config, layout):
    onboard(conn)
    make_dir(config, "issue-99")
    sweep(conn, audit, config)

    assert resolve(conn, audit) == 0
    assert len(open_orphans(conn)) == 1
    assert records(layout, "anomaly.resolved") == []


def test_a_retraction_is_recorded_once(conn, audit, config, layout):
    onboard(conn)
    orphan = make_dir(config, "issue-99")
    sweep(conn, audit, config)
    shutil.rmtree(orphan)

    assert resolve(conn, audit) == 1
    assert resolve(conn, audit) == 0
    assert len(records(layout, "anomaly.resolved")) == 1
