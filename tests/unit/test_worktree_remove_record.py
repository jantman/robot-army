"""``worktree remove`` records what it did, and settles what the sweep reports (issue #113).

Before this a manual removal wrote no cleanup record and forgot the path, so the row it
left could not be told apart from one whose directory was deleted by hand except by losing
"what was at this path?" — and a finished item whose directory *was* deleted by hand had no
command that would settle it once git had pruned its record.

See ``specs/20260915-174645-terminal-prunable-worktree/contracts/manual-removal-record.md``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import make_boundaries, seed_item, seed_session

from robot_army import db, operations, reconcile
from robot_army.audit import read_records
from robot_army.boundaries import RemovalResult
from robot_army.boundaries.git import SimulatedVersionControl
from robot_army.effects import EffectLevel
from robot_army.operations import EXIT_FAILED, EXIT_OK, EXIT_PRECONDITION, Context
from robot_army.states import WorkItemState

BRANCH = "robot-army/issue-42-fix"
TAIL = " — by `robot-army worktree remove`"
NOT_A_WORKING_TREE = "fatal: '...' is not a working tree"


class FakeGit(SimulatedVersionControl):
    """A version-control boundary that acts on the disk, as the real one does.

    ``simulated = False`` because the words and the "already gone" test both follow that
    flag, and these tests are about the real path. Every call is still audited, via the
    parent, so "git was never asked" is an assertion on the log.
    """

    simulated = False

    def __init__(
        self,
        audit,
        *,
        refuse: str | None = None,
        refuse_absent: bool = False,
        keep_directory: bool = False,
        delete_ok: bool = True,
        branch: str | type[Exception] | None = "present",
    ) -> None:
        super().__init__(audit)
        self.refuse = refuse
        self.refuse_absent = refuse_absent
        self.keep_directory = keep_directory
        self.delete_ok = delete_ok
        self.branch = branch
        self.deleted: list[str] = []

    def remove_worktree(
        self, worktree_path: str, force: bool = False, clone_path: str | None = None
    ) -> RemovalResult:
        super().remove_worktree(worktree_path, force=force, clone_path=clone_path)
        absent = not Path(worktree_path).is_dir()
        if self.refuse or (self.refuse_absent and absent):
            return RemovalResult(
                worktree_removed=False,
                branch_deleted=False,
                refused_reason=self.refuse or NOT_A_WORKING_TREE,
            )
        if not self.keep_directory:
            shutil.rmtree(worktree_path, ignore_errors=True)
        return RemovalResult(worktree_removed=True, branch_deleted=False)

    def delete_branch(self, clone_path: str, branch: str, force: bool = False) -> bool:
        super().delete_branch(clone_path, branch, force=force)
        self.deleted.append(branch)
        return self.delete_ok

    def rev_parse(self, clone_path: str, ref: str) -> str | None:
        if self.branch is RuntimeError:
            raise RuntimeError("git could not be asked")
        return None if self.branch is None else "a" * 40


def context(conn, audit, config, vcs: Any) -> Context:
    return Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, vcs=vcs),
        effect_level=EffectLevel.LIVE,
    )


def item(
    conn,
    config,
    *,
    state: WorkItemState = WorkItemState.DONE,
    present: bool = True,
    branch: str | None = BRANCH,
    repo_key: str = "demo",
    cleanup_state: str | None = None,
) -> tuple[int, Path]:
    item_id = seed_item(conn, state=str(state), repo_key=repo_key)
    path = config.worktree_root / "demo" / f"issue-{item_id}"
    if present:
        path.mkdir(parents=True)
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, worktree_path=str(path), branch=branch)
        if cleanup_state is not None:
            db.record_cleanup(conn, item_id, state=cleanup_state, reason="seeded")
    return item_id, path


def outcome(layout) -> dict[str, Any]:
    pair = [
        record
        for record, _ in read_records(layout.log_dir)
        if record is not None and record["action"] == "worktree.remove"
    ]
    assert pair[-1]["kind"] == "outcome"
    return pair[-1]["detail"]


def git_calls(layout) -> list[str]:
    return [
        record["action"]
        for record, _ in read_records(layout.log_dir)
        if record is not None
        and record["action"] in ("git.remove_worktree", "git.delete_branch")
    ]


def typed(item_id: int):
    return lambda _prompt: str(item_id)


def never_asks(_prompt: str) -> str:
    raise AssertionError("nothing here is a question")


def reconcile_pass(conn, audit, config, tmp_path):
    (tmp_path / "registry").mkdir(exist_ok=True)
    (tmp_path / "proc").mkdir(exist_ok=True)
    return reconcile.reconcile(
        conn,
        boundaries=make_boundaries(audit, vcs=SimulatedVersionControl(audit)),
        audit=audit,
        config=config,
        layout=config.layout,
        registry_dir=tmp_path / "registry",
        proc_root=tmp_path / "proc",
    )


# -- the record (T007; contract M5-M9, M13, M16) -------------------------------------


@pytest.mark.parametrize("state", [WorkItemState.DONE, WorkItemState.ABANDONED])
def test_a_finished_items_removal_leaves_cleanups_record_and_keeps_the_path(
    conn, audit, config, layout, state
):
    """M5, M7. The same row cleanup would have left: the outcome, why, when — and the path
    and branch, so "what was at this path?" stays answerable."""
    item_id, path = item(conn, config, state=state)

    result = operations.worktree_remove(context(conn, audit, config, FakeGit(audit)), item_id)

    assert result.code == EXIT_OK, result.lines
    after = db.get_work_item(conn, item_id)
    assert after.cleanup_state == "done"
    assert after.cleanup_reason == "worktree removed; branch deleted" + TAIL
    assert after.cleaned_at is not None
    assert after.worktree_path == str(path)
    assert after.branch == BRANCH
    assert outcome(layout)["cleanup_state"] == "done"
    assert result.data["cleanup_state"] == "done"


def test_a_branch_git_will_not_delete_is_recorded_as_retained(conn, audit, config):
    """M6. The worktree is gone and the branch is not — the disk ``branch_retained`` names."""
    item_id, _ = item(conn, config)

    result = operations.worktree_remove(
        context(conn, audit, config, FakeGit(audit, delete_ok=False)), item_id
    )

    assert result.code == EXIT_FAILED, "a surviving branch is still a warning"
    after = db.get_work_item(conn, item_id)
    assert after.cleanup_state == "branch_retained"
    assert after.cleanup_reason == "worktree removed; branch kept — git would not delete it" + TAIL


def test_a_forced_removal_says_so_in_the_record(conn, audit, config):
    item_id, _ = item(conn, config)

    operations.worktree_remove(
        context(conn, audit, config, FakeGit(audit)), item_id, force=True, confirm=typed(item_id)
    )

    assert db.get_work_item(conn, item_id).cleanup_reason == (
        "worktree removed (forced); branch deleted" + TAIL
    )


def test_a_simulated_removal_is_recorded_in_cleanups_simulated_words(conn, audit, config):
    """Issue #70's rule: a reason that is displayed for as long as the row exists must not
    claim a removal nothing performed."""
    item_id, _ = item(conn, config, present=False)

    result = operations.worktree_remove(
        context(conn, audit, config, SimulatedVersionControl(audit)), item_id
    )

    assert result.code == EXIT_OK, result.lines
    assert db.get_work_item(conn, item_id).cleanup_reason == (
        "worktree removal simulated; branch deletion simulated" + TAIL
    )


def test_no_branch_on_record_is_recorded_as_done(conn, audit, config):
    item_id, _ = item(conn, config, branch=None)

    operations.worktree_remove(context(conn, audit, config, FakeGit(audit)), item_id)

    after = db.get_work_item(conn, item_id)
    assert after.cleanup_state == "done"
    assert after.cleanup_reason == "worktree removed; no branch on record" + TAIL


@pytest.mark.parametrize(
    "state",
    [WorkItemState.FAILED, WorkItemState.INTERRUPTED, WorkItemState.AWAITING_REVIEW],
)
def test_an_unfinished_item_forgets_the_path_and_records_nothing(
    conn, audit, config, layout, state
):
    """M8. ``retry`` gives a failed item a fresh worktree, and a record describing the old one
    would then exempt the new one from the sweep and from cleanup, silently."""
    item_id, _ = item(conn, config, state=state)

    result = operations.worktree_remove(context(conn, audit, config, FakeGit(audit)), item_id)

    assert result.code == EXIT_OK, result.lines
    after = db.get_work_item(conn, item_id)
    assert after.worktree_path is None
    assert (after.cleanup_state, after.cleanup_reason, after.cleaned_at) == (None, None, None)
    assert "cleanup_state" not in outcome(layout)
    assert "cleanup_state" not in result.data


def assert_untouched(conn, item_id: int, path: Path) -> None:
    after = db.get_work_item(conn, item_id)
    assert (after.cleanup_state, after.cleanup_reason, after.cleaned_at) == (None, None, None)
    assert after.worktree_path == str(path)


def test_a_live_session_refusal_records_nothing(conn, audit, config):
    item_id, path = item(conn, config)
    seed_session(conn, item_id, state="running")

    result = operations.worktree_remove(
        context(conn, audit, config, FakeGit(audit)), item_id, confirm=never_asks
    )

    assert result.code == EXIT_PRECONDITION
    assert_untouched(conn, item_id, path)


def test_gits_dirty_tree_refusal_records_nothing(conn, audit, config):
    item_id, path = item(conn, config)

    result = operations.worktree_remove(
        context(conn, audit, config, FakeGit(audit, refuse="contains untracked files")), item_id
    )

    assert result.code == EXIT_FAILED
    assert_untouched(conn, item_id, path)


def test_a_surviving_directory_records_nothing(conn, audit, config):
    item_id, path = item(conn, config)

    result = operations.worktree_remove(
        context(conn, audit, config, FakeGit(audit, keep_directory=True)), item_id
    )

    assert result.code == EXIT_FAILED
    assert result.data["refused_by"] == "directory_survived"
    assert_untouched(conn, item_id, path)


def test_an_unresolved_repository_records_nothing(conn, audit, config):
    item_id, path = item(conn, config, repo_key="elsewhere")

    result = operations.worktree_remove(context(conn, audit, config, FakeGit(audit)), item_id)

    assert result.code == EXIT_PRECONDITION
    assert_untouched(conn, item_id, path)


def test_a_wrong_confirmation_records_nothing(conn, audit, config):
    item_id, path = item(conn, config)

    result = operations.worktree_remove(
        context(conn, audit, config, FakeGit(audit)),
        item_id,
        force=True,
        confirm=lambda _prompt: "no",
    )

    assert result.code == EXIT_FAILED
    assert_untouched(conn, item_id, path)


def test_a_recorded_removal_is_neither_a_cleanup_candidate_nor_reported(
    conn, audit, config, tmp_path
):
    """The two things the record is for: nothing left for cleanup, and nothing for the sweep
    to report — though the path is still on the row and the directory is gone."""
    item_id, path = item(conn, config)
    operations.worktree_remove(context(conn, audit, config, FakeGit(audit)), item_id)
    assert not path.exists()

    assert db.list_cleanup_candidates(conn, include_simulated=True) == []
    for _ in range(2):
        assert reconcile_pass(conn, audit, config, tmp_path).prunable == 0


# -- settling what the sweep reports (T011; contract M10-M12, M14, M15) ---------------


def test_an_absent_directory_git_no_longer_knows_is_removed_not_refused(
    conn, audit, config, layout
):
    """M10. After ``worktree prune`` git says "is not a working tree" — about its record,
    not about contents, since there are none. Cleanup has read it that way since 004."""
    item_id, _ = item(conn, config, present=False)

    result = operations.worktree_remove(
        context(conn, audit, config, FakeGit(audit, refuse_absent=True)), item_id
    )

    assert result.code == EXIT_OK, result.lines
    assert result.lines[0].endswith("was already gone")
    detail = outcome(layout)
    assert detail["worktree_already_gone"] is True
    assert detail["refused"] is False
    after = db.get_work_item(conn, item_id)
    assert after.cleanup_state == "done"
    assert after.cleanup_reason == "worktree directory was already gone; branch deleted" + TAIL


def test_an_absent_directory_git_still_lists_is_recorded_as_already_gone(conn, audit, config):
    """Before a prune git clears its record and exits 0. The directory was gone before it
    was asked either way, and the record says that rather than "worktree removed"."""
    item_id, _ = item(conn, config, present=False)

    result = operations.worktree_remove(context(conn, audit, config, FakeGit(audit)), item_id)

    assert result.code == EXIT_OK, result.lines
    assert db.get_work_item(conn, item_id).cleanup_reason.startswith(
        "worktree directory was already gone"
    )


def test_gits_refusal_over_a_present_directory_still_stands(conn, audit, config, layout):
    """M10's limit. Only an absent directory turns a refusal into a removal."""
    item_id, path = item(conn, config)

    result = operations.worktree_remove(
        context(conn, audit, config, FakeGit(audit, refuse=NOT_A_WORKING_TREE)), item_id
    )

    assert result.code == EXIT_FAILED
    assert outcome(layout)["refused_by"] == "git"
    assert "worktree_already_gone" not in outcome(layout)
    assert path.is_dir()
    assert_untouched(conn, item_id, path)


def test_a_branch_that_is_already_gone_is_not_reported_as_surviving(
    conn, audit, config, layout
):
    """M11. It used to be ``WARNING: … still exists`` and exit 1, on precisely the re-run
    that settles an interrupted removal."""
    item_id, _ = item(conn, config, present=False)
    vcs = FakeGit(audit, branch=None, delete_ok=False)

    result = operations.worktree_remove(context(conn, audit, config, vcs), item_id)

    assert result.code == EXIT_OK, result.lines
    assert f"branch {BRANCH} was already gone" in result.lines
    assert not any("WARNING" in line for line in result.lines)
    assert vcs.deleted == []
    assert "git.delete_branch" not in git_calls(layout)
    assert outcome(layout)["branch_already_gone"] is True
    after = db.get_work_item(conn, item_id)
    assert after.cleanup_state == "done"
    assert after.cleanup_reason.endswith("the branch was already gone" + TAIL)


def test_a_branch_whose_existence_cannot_be_asked_is_still_deleted(conn, audit, config):
    """"I could not check" never reads as "it is gone"."""
    item_id, _ = item(conn, config)
    vcs = FakeGit(audit, branch=RuntimeError)

    operations.worktree_remove(context(conn, audit, config, vcs), item_id)

    assert vcs.deleted == [BRANCH]


def test_a_removal_already_on_record_is_refused_before_anything_is_asked(
    conn, audit, config, layout
):
    """M12, M15. ``done`` says both halves are gone. Before the repository, before the
    sessions — a live row here is not the reason to refuse, and must not be what the
    operator is told."""
    item_id, _ = item(conn, config, present=False, cleanup_state="done")
    seed_session(conn, item_id, state="running")

    result = operations.worktree_remove(
        context(conn, audit, config, FakeGit(audit)), item_id, confirm=never_asks
    )

    assert result.code == EXIT_PRECONDITION
    assert result.data["refused_by"] == "already_removed"
    assert "done" in "\n".join(result.lines)
    assert git_calls(layout) == []
    detail = outcome(layout)
    assert detail["refused"] is True
    assert detail["refused_by"] == "already_removed"
    assert db.get_work_item(conn, item_id).cleanup_reason == "seeded"


@pytest.mark.parametrize("state", [WorkItemState.DONE, WorkItemState.ABANDONED])
def test_a_retained_branch_is_finished_by_forcing_the_rerun(conn, audit, config, layout, state):
    """Found in review of PR #178. ``branch_retained`` is what the first run leaves when
    git's ``-d`` refuses an unmerged branch — the ordinary outcome for abandoned work — and
    the re-run with ``--force`` is how that branch goes. Refusing it as ``already_removed``
    left an abandoned item's branch with no command able to delete it: cleanup considers
    ``done`` items only, and the path form refuses a path a row claims."""
    item_id, _ = item(
        conn, config, state=state, present=False, cleanup_state="branch_retained"
    )
    vcs = FakeGit(audit, refuse_absent=True)

    result = operations.worktree_remove(
        context(conn, audit, config, vcs), item_id, force=True, confirm=typed(item_id)
    )

    assert result.code == EXIT_OK, result.lines
    assert vcs.deleted == [BRANCH]
    [delete] = [
        record
        for record, _ in read_records(layout.log_dir)
        if record is not None and record["action"] == "git.delete_branch"
    ]
    assert delete["detail"]["force"] is True
    after = db.get_work_item(conn, item_id)
    assert after.cleanup_state == "done"
    assert after.cleanup_reason == (
        "worktree directory was already gone (forced); branch deleted" + TAIL
    )


def test_a_retained_branch_git_still_refuses_stays_retained(conn, audit, config):
    """Without ``--force`` the re-run asks ``-d`` again, and a refusal is recorded again."""
    item_id, _ = item(
        conn,
        config,
        state=WorkItemState.ABANDONED,
        present=False,
        cleanup_state="branch_retained",
    )

    result = operations.worktree_remove(
        context(conn, audit, config, FakeGit(audit, refuse_absent=True, delete_ok=False)),
        item_id,
    )

    assert result.code == EXIT_FAILED
    assert db.get_work_item(conn, item_id).cleanup_state == "branch_retained"


def test_a_retained_branch_deleted_since_is_recorded_done(conn, audit, config):
    """Someone ran ``git branch -D`` by hand. The re-run says so and closes the record."""
    item_id, _ = item(conn, config, present=False, cleanup_state="branch_retained")

    result = operations.worktree_remove(
        context(conn, audit, config, FakeGit(audit, refuse_absent=True, branch=None)), item_id
    )

    assert result.code == EXIT_OK, result.lines
    after = db.get_work_item(conn, item_id)
    assert after.cleanup_state == "done"
    assert after.cleanup_reason.endswith("the branch was already gone" + TAIL)


def test_a_directory_present_despite_the_record_is_removed(conn, audit, config):
    """The record is then wrong, and removing is the command's job."""
    item_id, path = item(conn, config, cleanup_state="done")

    result = operations.worktree_remove(context(conn, audit, config, FakeGit(audit)), item_id)

    assert result.code == EXIT_OK, result.lines
    assert not path.exists()


def test_a_reported_item_is_settled_and_stays_settled(conn, audit, config, tmp_path):
    """The whole of US3: reported, settled with the command the note names, acknowledged,
    and never raised again."""
    item_id, _ = item(conn, config, present=False)
    assert reconcile_pass(conn, audit, config, tmp_path).prunable == 1
    [anomaly] = [a for a in db.list_anomalies(conn) if a.kind == "prunable_worktree"]
    assert f"worktree remove {item_id}" in anomaly.detail_obj["note"]

    result = operations.worktree_remove(
        context(conn, audit, config, FakeGit(audit, refuse_absent=True)), item_id
    )
    assert result.code == EXIT_OK, result.lines
    with db.transaction(conn):
        db.acknowledge_anomaly(conn, anomaly.id)

    for _ in range(2):
        assert reconcile_pass(conn, audit, config, tmp_path).prunable == 0
    assert [a for a in db.list_anomalies(conn) if a.kind == "prunable_worktree"] == []
