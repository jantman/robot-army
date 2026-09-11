"""``purge-simulated`` takes the worktrees its rows own with it, when asked (issue #59).

Before this, the purge deleted the rows that were robot-army's only route to a worktree and
then pointed at ``worktree remove <id>`` — a command that needs those rows. The directories
were left where nothing in robot-army could reach them: 80 MB of them on a disk at 93%.

Removal is asserted against the **disk** wherever it can be: ``DeletingVcs`` really removes
the ``tmp_path`` directory it is asked to, the way git would, so "the directory is gone"
is a fact about the filesystem rather than about a log line. Plain
``SimulatedVersionControl`` is used where the point is that it *claims* a removal it did
not make.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import make_boundaries, seed_item, seed_session

from robot_army import db, operations
from robot_army.audit import read_records
from robot_army.boundaries import RemovalResult
from robot_army.boundaries.git import SimulatedVersionControl
from robot_army.effects import EffectLevel
from robot_army.models import Session
from robot_army.operations import EXIT_FAILED, EXIT_OK, Context
from robot_army.states import SessionState


def _session(**overrides: Any) -> Session:
    fields: dict[str, Any] = {
        "id": 1,
        "work_item_id": 1,
        "session_id": "sess-1",
        "attempt": 1,
        "state": SessionState.RUNNING,
        "dry_run": True,
        "started_at": "2026-09-10T00:00:00Z",
        "pid": 0,
        "proc_start": None,
    }
    fields.update(overrides)
    return Session(**fields)


# -- the simulated host's signature (T002) ----------------------------------


def test_the_simulated_hosts_signature_is_recognised():
    assert _session().hosted_by_simulation is True


@pytest.mark.parametrize(
    "overrides",
    [
        # `no-remote`: a rehearsal row with a real worker behind a real pid. The case the
        # #79 guard exists for, and the one `dry_run` alone would wrongly wave through.
        {"pid": 51234, "proc_start": "987654"},
        {"pid": 51234, "proc_start": None},
        # A real row with no pid is a row whose process could not be recorded — absence of
        # evidence, which is exactly what must still be treated as possibly running.
        {"dry_run": False},
        {"pid": None},
        {"proc_start": "987654"},
    ],
    ids=["no-remote", "no-remote-unstarted", "live-row", "null-pid", "has-start-time"],
)
def test_anything_short_of_the_full_signature_is_not_simulated(overrides):
    assert _session(**overrides).hosted_by_simulation is False


# -- fixtures for the purge ---------------------------------------------------

DIRTY = "fatal: '...' contains modified or untracked files, use --force to delete it"


class DeletingVcs(SimulatedVersionControl):
    """Simulated git whose removal really removes the directory, as real git would.

    Subclassed so every call is still audited as a simulated git operation, which the
    ordering assertions below read. ``refuse`` names paths git declines, as it does a
    dirty tree.
    """

    def __init__(self, audit: Any, *, refuse: set[str] | None = None) -> None:
        super().__init__(audit)
        self.refuse = refuse or set()

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


class Answers:
    """Answers the purge's questions in order and remembers them. An exception instance in
    the sequence is raised instead of answered; running out is a question too many."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"asked one question too many: {prompt!r}")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def never_asks(prompt: str) -> str:
    raise AssertionError(f"this invocation must not ask anything: {prompt!r}")


def make_ctx(conn, audit, config, *, vcs: Any = None) -> Context:
    return Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, vcs=vcs or DeletingVcs(audit)),
        effect_level=EffectLevel.LIVE,
    )


def branch_for(issue_number: int) -> str:
    return f"robot-army/{issue_number}-fix-the-thing"


def simulated_worktree(conn, config, issue_number: int, *, exists: bool = True) -> Path:
    """A simulated item recording a worktree at the path robot-army would have used."""
    item_id = seed_item(conn, dry_run=True, issue_number=issue_number)
    path = Path(config.worktree_root) / "demo" / f"issue-{issue_number}"
    if exists:
        path.mkdir(parents=True)
        (path / "notes.txt").write_text("work\n", encoding="utf-8")
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item_id, worktree_path=str(path), branch=branch_for(issue_number)
        )
    return path


def records(layout, action: str | None = None) -> list[dict[str, Any]]:
    return [
        record
        for record, _ in read_records(layout.log_dir)
        if record is not None and (action is None or record["action"] == action)
    ]


# -- US1: both questions, and what each answer does (T005) -------------------


def test_the_worktrees_are_named_and_removing_them_takes_worktree_and_branch(
    conn, audit, config, layout
):
    first = simulated_worktree(conn, config, 24)
    second = simulated_worktree(conn, config, 25)
    answers = Answers("y", "y")

    result = operations.purge_simulated(make_ctx(conn, audit, config), confirm=answers)
    audit.close()

    assert result.code == EXIT_OK, result.lines
    assert not first.exists() and not second.exists()
    assert db.count_simulated(conn)["work_items"] == 0

    rows_question, disk_question = answers.prompts
    for path, issue in ((first, 24), (second, 25)):
        assert str(path) in rows_question
        assert branch_for(issue) in rows_question
    assert "2 worktree(s)" in disk_question and "[y/N]" in disk_question

    assert result.data["worktrees_removed"] == [str(first), str(second)]
    assert result.data["worktrees_left"] == []
    assert not any("worktree remove" in line for line in result.lines)
    deleted = [r["detail"]["branch"] for r in records(layout, "git.delete_branch")]
    assert deleted == [branch_for(24), branch_for(25)], "both halves of removal, per worktree"


def test_every_removal_is_recorded_before_the_rows_go(conn, audit, config, layout):
    path = simulated_worktree(conn, config, 24)

    operations.purge_simulated(make_ctx(conn, audit, config), confirm=Answers("y", "y"))
    audit.close()

    sequence = [
        (r["action"], r["kind"])
        for r in records(layout)
        if r["action"] in ("purge.simulated", "worktree.remove")
    ]
    assert sequence == [
        ("purge.simulated", "intent"),
        ("worktree.remove", "intent"),
        ("worktree.remove", "outcome"),
        ("purge.simulated", "outcome"),
    ]
    intent = records(layout, "purge.simulated")[0]
    assert intent["detail"]["worktrees"] == [str(path)]
    assert intent["detail"]["remove_worktrees"] is True
    removal = records(layout, "worktree.remove")[-1]
    assert removal["detail"]["by"] == "purge-simulated"
    assert removal["detail"]["worktree_removed"] is True
    assert removal["target"] == str(path)


def test_declining_removal_purges_rows_and_names_each_survivor(conn, audit, config, layout):
    first = simulated_worktree(conn, config, 24)
    second = simulated_worktree(conn, config, 25)

    result = operations.purge_simulated(
        make_ctx(conn, audit, config), confirm=Answers("y", "n")
    )
    audit.close()

    assert result.code == EXIT_OK, "the maintainer chose to keep them; nothing failed"
    assert first.is_dir() and second.is_dir()
    assert db.count_simulated(conn)["work_items"] == 0
    for path in (first, second):
        assert f"  remove it with: robot-army worktree remove {path}" in result.lines
    assert not any("NOT removed" in line for line in result.lines), "the old dead-end advice"
    assert records(layout, "worktree.remove") == []


def test_declining_the_rows_asks_nothing_more_and_changes_nothing(conn, audit, config):
    path = simulated_worktree(conn, config, 24)

    result = operations.purge_simulated(make_ctx(conn, audit, config), confirm=Answers("n"))

    assert result.code == EXIT_FAILED and result.lines == ["aborted"]
    assert path.is_dir()
    assert db.count_simulated(conn)["work_items"] == 1


# -- US1: the flags (T006, research R10) --------------------------------------


def test_yes_alone_answers_only_the_rows_question(conn, audit, config, layout):
    """``--yes`` meant "skip the confirmation" when it deleted rows only. A script already
    passing it must not start deleting directories because this feature exists."""
    path = simulated_worktree(conn, config, 24)

    result = operations.purge_simulated(
        make_ctx(conn, audit, config), assume_yes=True, confirm=never_asks
    )
    audit.close()

    assert result.code == EXIT_OK
    assert path.is_dir()
    assert db.count_simulated(conn)["work_items"] == 0
    assert result.data["remove_worktrees"] is False
    assert f"left on disk: {path}" in result.lines
    assert records(layout, "worktree.remove") == []


def test_yes_with_remove_worktrees_removes_without_asking(conn, audit, config):
    path = simulated_worktree(conn, config, 24)

    result = operations.purge_simulated(
        make_ctx(conn, audit, config),
        assume_yes=True,
        remove_worktrees=True,
        confirm=never_asks,
    )

    assert result.code == EXIT_OK, result.lines
    assert not path.exists()


def test_remove_worktrees_answers_only_the_disk_question(conn, audit, config):
    path = simulated_worktree(conn, config, 24)
    answers = Answers("y")

    result = operations.purge_simulated(
        make_ctx(conn, audit, config), remove_worktrees=True, confirm=answers
    )

    assert result.code == EXIT_OK, result.lines
    assert len(answers.prompts) == 1 and str(path) in answers.prompts[0]
    assert not path.exists()


def test_a_row_whose_directory_is_already_gone_is_not_offered(conn, audit, config, layout):
    path = simulated_worktree(conn, config, 24, exists=False)
    answers = Answers("y")

    result = operations.purge_simulated(make_ctx(conn, audit, config), confirm=answers)
    audit.close()

    assert result.code == EXIT_OK
    assert len(answers.prompts) == 1, "nothing to offer, so no second question"
    assert str(path) not in answers.prompts[0]
    assert result.data["worktrees_offered"] == []
    assert records(layout, "worktree.remove") == [], "no removal reported that nobody made"


# -- US1: refusals and interruptions (T007) -----------------------------------


def test_git_refusing_one_leaves_it_named_and_removes_the_rest(conn, audit, config):
    dirty = simulated_worktree(conn, config, 24)
    clean = simulated_worktree(conn, config, 25)
    ctx = make_ctx(conn, audit, config, vcs=DeletingVcs(audit, refuse={str(dirty)}))

    result = operations.purge_simulated(ctx, confirm=Answers("y", "y"))

    assert result.code == EXIT_FAILED, "asked for the disk back and did not get all of it"
    assert dirty.is_dir() and not clean.exists()
    assert db.count_simulated(conn)["work_items"] == 0, "the rows were still purged"
    assert f"refused to remove {dirty}:" in result.lines
    assert f"  {DIRTY}" in result.lines
    assert f"  remove it with: robot-army worktree remove {dirty}" in result.lines
    assert result.data["worktrees_left"] == [str(dirty)]


def test_a_real_open_session_refuses_and_a_simulated_one_does_not(
    conn, audit, config, layout
):
    """At ``no-remote`` a rehearsal row has a real worker behind it (#79's case); at
    ``local`` every row carries the simulated host's signature and is never closed."""
    worked_in = simulated_worktree(conn, config, 24)
    rehearsed = simulated_worktree(conn, config, 25)
    items = {i.issue_number: i.id for i in db.list_work_items(conn, include_simulated=True)}
    seed_session(conn, items[24], dry_run=True, pid=os.getpid(), proc_start="1")
    seed_session(conn, items[25], dry_run=True, pid=0, proc_start=None)

    result = operations.purge_simulated(
        make_ctx(conn, audit, config), confirm=Answers("y", "y")
    )
    audit.close()

    assert worked_in.is_dir() and not rehearsed.exists()
    refusal = [
        r
        for r in records(layout, "worktree.remove")
        if r["kind"] == "outcome" and r["detail"]["refused"]
    ]
    assert [r["target"] for r in refusal] == [str(worked_in)]
    assert refusal[0]["detail"]["refused_by"] == "live_session"
    assert result.code == EXIT_FAILED


def test_a_removal_the_boundary_only_claims_is_reported_as_left(
    conn, audit, config, layout
):
    """The effect-level edge case: a real directory from a ``local`` round, purged where
    version control is simulated. It must never be reported as removed."""
    path = simulated_worktree(conn, config, 24)
    ctx = make_ctx(conn, audit, config, vcs=SimulatedVersionControl(audit))

    result = operations.purge_simulated(ctx, confirm=Answers("y", "y"))
    audit.close()

    assert path.is_dir()
    assert not any(line.startswith("removed worktree") for line in result.lines)
    assert result.data["worktrees_removed"] == []
    assert result.data["worktrees_left"] == [str(path)]
    assert records(layout, "git.delete_branch") == [], "the branch half was not attempted"
    assert records(layout, "worktree.remove")[-1]["detail"]["refused_by"] == (
        "directory_survived"
    )
    assert result.code == EXIT_FAILED


def test_a_purge_killed_after_some_removals_re_runs_cleanly(conn, audit, config):
    """Stands in for a kill between two removals: one worktree is already gone and its row
    has had its path cleared, exactly as the removal core leaves it, and the rows remain."""
    gone = simulated_worktree(conn, config, 24)
    survivor = simulated_worktree(conn, config, 25)
    ctx = make_ctx(conn, audit, config)
    first_id = next(
        i.id
        for i in db.list_work_items(conn, include_simulated=True)
        if i.issue_number == 24
    )
    assert operations.worktree_remove(ctx, first_id).code == EXIT_OK
    answers = Answers("y", "y")

    result = operations.purge_simulated(ctx, confirm=answers)

    assert str(gone) not in answers.prompts[0], "only what is still on disk is offered"
    assert str(survivor) in answers.prompts[0]
    assert result.data["worktrees_removed"] == [str(survivor)]
    assert not survivor.exists()
    assert db.count_simulated(conn)["work_items"] == 0


@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt()], ids=["eof", "ctrl-c"])
def test_giving_up_at_the_disk_question_removes_and_deletes_nothing(
    conn, audit, config, layout, error
):
    path = simulated_worktree(conn, config, 24)

    result = operations.purge_simulated(
        make_ctx(conn, audit, config), confirm=Answers("y", error)
    )
    audit.close()

    assert result.code != EXIT_OK
    assert path.is_dir()
    assert db.count_simulated(conn)["work_items"] == 1, "no row was deleted"
    written = records(layout, "purge.simulated")
    assert len(written) == 1 and written[0]["outcome"] == "error"
    assert written[0]["detail"]["abandoned"] is True
    assert written[0]["detail"]["worktrees"] == [str(path)]
