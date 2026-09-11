"""``worktree remove <path>``, and orphans in ``worktree list`` (issue #59).

``purge-simulated`` used to advise ``worktree remove`` for the directories it left — a
command that took only an item id, for rows the purge had just deleted. The path form is
what makes that advice, and the ``orphan_worktree`` anomaly's, something that can be done.

Removal is asserted against the disk: ``ListingVcs`` deletes the directory as git would.
Refusals are asserted as *nothing reached git*: no ``git.remove_worktree`` record at all,
which is the stronger claim than a surviving directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.conftest import make_boundaries, write_proc, write_registry
from tests.unit.test_orphan_worktrees import (
    DIRTY,
    ListingVcs,
    claim,
    make_dir,
    onboard,
    records,
)

from robot_army import cli, operations
from robot_army.boundaries import RemovalResult
from robot_army.boundaries.git import SimulatedVersionControl
from robot_army.effects import EffectLevel
from robot_army.operations import (
    EXIT_CHECK_FAILED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_PRECONDITION,
    Context,
)

BRANCH = "robot-army/29-fix-the-thing"


class ClaimsOnly(ListingVcs):
    """Lists the worktree, and reports removing it without doing so — the simulated
    boundary's behaviour, over a directory that is really there."""

    def remove_worktree(
        self, worktree_path: str, force: bool = False, clone_path: str | None = None
    ) -> RemovalResult:
        SimulatedVersionControl.remove_worktree(
            self, worktree_path, force=force, clone_path=clone_path
        )
        return RemovalResult(worktree_removed=True, branch_deleted=False)


def make_ctx(conn, audit, config, vcs: Any) -> Context:
    return Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, vcs=vcs),
        effect_level=EffectLevel.LIVE,
    )


def never_asks(prompt: str) -> str:
    raise AssertionError(f"a refusal is not a question: {prompt!r}")


@pytest.fixture
def scan_dirs(tmp_path) -> dict[str, Path]:
    """An empty registry and ``/proc`` of our own, so no real worker on the machine running
    the suite can be seen inside a test's directory — or hide one."""
    registry, proc = tmp_path / "registry", tmp_path / "proc"
    registry.mkdir()
    proc.mkdir()
    return {"registry_dir": registry, "proc_root": proc}


@pytest.fixture
def orphan(conn, config) -> Path:
    onboard(conn)
    return make_dir(config, "issue-29").resolve()


def remove(ctx, path, scan_dirs, **kwargs) -> Any:
    kwargs.setdefault("confirm", never_asks)
    return operations.worktree_remove_path(ctx, str(path), **scan_dirs, **kwargs)


def git_touched(layout) -> list[str]:
    return [
        r["action"]
        for r in records(layout)
        if r["action"] in ("git.remove_worktree", "git.delete_branch")
    ]


# -- success (T019) -----------------------------------------------------------


def test_an_unclaimed_worktree_is_removed_with_its_branch(
    conn, audit, config, layout, orphan, scan_dirs
):
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH})

    result = remove(make_ctx(conn, audit, config, vcs), orphan, scan_dirs)

    assert result.code == EXIT_OK, result.lines
    assert not orphan.exists()
    assert result.lines == [f"removed worktree {orphan}", f"deleted branch {BRANCH}"]
    assert result.data["worktree_removed"] is True and result.data["branch_deleted"] is True
    assert git_touched(layout) == ["git.remove_worktree", "git.delete_branch"]

    intent, outcome = records(layout, "worktree.remove")
    assert intent["kind"] == "intent" and intent["action_id"] == outcome["action_id"]
    assert intent["entity_type"] == "worktree"
    assert intent["entity_id"] == str(orphan) and intent["target"] == str(orphan)
    assert intent["detail"] == {"force": False, "by": "path"}
    assert outcome["detail"]["worktree_removed"] is True
    assert outcome["detail"]["repo_key"] == "demo"


def test_a_trailing_slash_names_the_same_worktree(conn, audit, config, orphan, scan_dirs):
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH})

    result = remove(make_ctx(conn, audit, config, vcs), f"{orphan}/", scan_dirs)

    assert result.code == EXIT_OK and not orphan.exists()


def test_a_detached_worktree_has_no_branch_half(
    conn, audit, config, layout, orphan, scan_dirs
):
    vcs = ListingVcs(audit, worktrees={str(orphan): None})

    result = remove(make_ctx(conn, audit, config, vcs), orphan, scan_dirs)

    assert result.code == EXIT_OK, result.lines
    assert git_touched(layout) == ["git.remove_worktree"]
    assert not any("WARNING" in line for line in result.lines)


# -- refusals, in the contract's order (T019) ----------------------------------


def test_a_path_outside_the_worktree_root_is_refused(
    conn, audit, config, layout, tmp_path, scan_dirs
):
    onboard(conn)
    elsewhere = tmp_path / "elsewhere" / "issue-29"
    elsewhere.mkdir(parents=True)
    ctx = make_ctx(conn, audit, config, ListingVcs(audit, worktrees={str(elsewhere): BRANCH}))

    for target in (elsewhere, Path(config.worktree_root)):
        result = remove(ctx, target, scan_dirs)
        assert result.code == EXIT_PRECONDITION
        assert result.data["refused_by"] == "outside_root"

    assert elsewhere.is_dir()
    assert git_touched(layout) == []


def test_a_claimed_worktree_is_refused_and_the_id_form_named(
    conn, audit, config, layout, orphan, scan_dirs
):
    """Otherwise the path form would be a way round #79's session guard, which lives on
    the id form because only a row has sessions to ask."""
    item_id = claim(conn, orphan, issue_number=29)
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH})

    result = remove(make_ctx(conn, audit, config, vcs), orphan, scan_dirs)

    assert result.code == EXIT_PRECONDITION
    assert result.data["refused_by"] == "claimed"
    assert result.data["claimed_by_item"] == item_id
    assert any(f"robot-army worktree remove {item_id}" in line for line in result.lines)
    assert orphan.is_dir() and git_touched(layout) == []


def test_a_path_with_no_directory_is_refused_and_prune_named(
    conn, audit, config, layout, scan_dirs
):
    onboard(conn)
    missing = Path(config.worktree_root) / "demo" / "issue-30"

    result = remove(make_ctx(conn, audit, config, ListingVcs(audit)), missing, scan_dirs)

    assert result.code == EXIT_FAILED
    assert result.data["refused_by"] == "not_a_directory"
    assert any("worktree prune" in line for line in result.lines)
    assert git_touched(layout) == []


def test_a_directory_no_clone_lists_is_not_deleted(
    conn, audit, config, layout, orphan, scan_dirs
):
    """robot-army removes worktrees through git; it is not ``rm -rf`` for the root."""
    result = remove(make_ctx(conn, audit, config, ListingVcs(audit)), orphan, scan_dirs)

    assert result.code == EXIT_PRECONDITION
    assert result.data["refused_by"] == "not_a_worktree"
    assert orphan.is_dir() and git_touched(layout) == []


def test_a_clone_that_cannot_be_listed_refuses_rather_than_guesses(
    conn, audit, config, layout, orphan, scan_dirs
):
    result = remove(
        make_ctx(conn, audit, config, ListingVcs(audit, fail=True)), orphan, scan_dirs
    )

    assert result.code == EXIT_FAILED
    assert result.data["refused_by"] == "listing_failed"
    assert orphan.is_dir() and git_touched(layout) == []


def test_a_live_worker_inside_refuses_without_force(
    conn, audit, config, layout, orphan, scan_dirs
):
    write_registry(
        scan_dirs["registry_dir"], pid=4242, session_id="s-1", proc_start="777", cwd=str(orphan)
    )
    write_proc(scan_dirs["proc_root"], 4242, starttime="777", cwd=str(orphan))
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH})

    result = remove(make_ctx(conn, audit, config, vcs), orphan, scan_dirs)

    assert result.code == EXIT_PRECONDITION
    assert result.data["refused_by"] == "live_worker"
    assert result.data["live_worker"]["pid"] == 4242
    assert any("--force" in line for line in result.lines)
    assert orphan.is_dir() and git_touched(layout) == []


def test_a_dead_worker_recorded_inside_does_not_refuse(
    conn, audit, config, orphan, scan_dirs
):
    """Liveness is pid *and* start time: a recycled pid is not the worker. The pid now
    belongs to something that is not a worker at all, which is what recycling looks like —
    a *worker* holding it with its cwd in there would be a live worker in there."""
    write_registry(
        scan_dirs["registry_dir"], pid=4242, session_id="s-1", proc_start="777", cwd=str(orphan)
    )
    write_proc(
        scan_dirs["proc_root"], 4242, starttime="999", cwd=str(orphan), exe="/usr/bin/sleep"
    )
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH})

    result = remove(make_ctx(conn, audit, config, vcs), orphan, scan_dirs)

    assert result.code == EXIT_OK, result.lines


def test_a_worker_is_seen_when_the_registry_directory_is_missing(
    conn, audit, config, layout, orphan, scan_dirs, tmp_path
):
    """Review on #165. An absent registry scans as empty, which is not "nothing running";
    ``/proc`` is asked directly, so the worker is still seen and the removal refused."""
    write_proc(scan_dirs["proc_root"], 4242, starttime="777", cwd=str(orphan))
    blind = {**scan_dirs, "registry_dir": tmp_path / "no-registry-here"}
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH})

    result = remove(make_ctx(conn, audit, config, vcs), orphan, blind)

    assert result.code == EXIT_PRECONDITION
    assert result.data["refused_by"] == "live_worker"
    assert result.data["live_worker"]["pid"] == 4242
    assert result.data["live_worker"]["seen_by"] == "/proc"
    assert result.data["live_worker"]["session_id"] is None
    assert any("pid 4242 (seen in /proc)" in line for line in result.lines)
    assert orphan.is_dir() and git_touched(layout) == []


def test_a_worker_is_seen_when_the_registry_version_is_refused(
    conn, audit, config, layout, orphan, scan_dirs
):
    """A worker upgrade changes the version every registry file is written with, so every
    file is refused at once and the registry reads empty while the worker runs."""
    write_registry(
        scan_dirs["registry_dir"],
        pid=4242,
        session_id="s-1",
        proc_start="777",
        cwd=str(orphan),
        version="9.9.9",
    )
    write_proc(scan_dirs["proc_root"], 4242, starttime="777", cwd=str(orphan))
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH})

    result = remove(make_ctx(conn, audit, config, vcs), orphan, scan_dirs)

    assert result.code == EXIT_PRECONDITION
    assert result.data["refused_by"] == "live_worker"
    assert result.data["live_worker"]["seen_by"] == "/proc"
    assert orphan.is_dir() and git_touched(layout) == []


def test_a_process_that_is_not_a_worker_does_not_refuse(
    conn, audit, config, orphan, scan_dirs
):
    """A shell left ``cd``'d in there is not a worker; git's dirty-tree refusal covers
    anything it wrote."""
    write_proc(scan_dirs["proc_root"], 5151, starttime="1", cwd=str(orphan), exe="/bin/bash")
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH})

    result = remove(make_ctx(conn, audit, config, vcs), orphan, scan_dirs)

    assert result.code == EXIT_OK, result.lines


def test_every_refusal_is_a_recorded_outcome(conn, audit, config, layout, orphan, scan_dirs):
    remove(make_ctx(conn, audit, config, ListingVcs(audit)), orphan, scan_dirs)

    outcome = records(layout, "worktree.remove")[-1]
    assert outcome["kind"] == "outcome"
    assert outcome["detail"]["refused"] is True
    assert outcome["detail"]["refused_by"] == "not_a_worktree"


# -- git's refusal and --force (T019) ------------------------------------------


def test_git_refusing_a_dirty_tree_stands(conn, audit, config, layout, orphan, scan_dirs):
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH}, refuse={str(orphan)})

    result = remove(make_ctx(conn, audit, config, vcs), orphan, scan_dirs)

    assert result.code == EXIT_FAILED
    assert result.data["refused_by"] == "git"
    assert f"  {DIRTY}" in result.lines
    assert orphan.is_dir()
    assert git_touched(layout) == ["git.remove_worktree"], "no branch half after a refusal"


def test_force_asks_for_the_directory_name(conn, audit, config, orphan, scan_dirs):
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH}, refuse={str(orphan)})
    ctx = make_ctx(conn, audit, config, vcs)
    asked: list[str] = []

    declined = remove(ctx, orphan, scan_dirs, force=True, confirm=lambda p: "29")
    assert declined.code == EXIT_FAILED and declined.lines == ["aborted"]
    assert orphan.is_dir()

    forced = remove(
        ctx, orphan, scan_dirs, force=True, confirm=lambda p: asked.append(p) or "issue-29"
    )
    assert forced.code == EXIT_OK, forced.lines
    assert "(issue-29)" in asked[0]
    assert not orphan.exists()


def test_giving_up_at_the_force_prompt_removes_nothing(
    conn, audit, config, layout, orphan, scan_dirs
):
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH})

    def gives_up(_prompt: str) -> str:
        raise EOFError

    result = remove(
        make_ctx(conn, audit, config, vcs), orphan, scan_dirs, force=True, confirm=gives_up
    )

    assert result.code == EXIT_CHECK_FAILED
    assert orphan.is_dir() and git_touched(layout) == []
    assert records(layout, "worktree.remove")[-1]["detail"]["abandoned"] is True


def test_forcing_over_a_live_worker_says_so_and_is_recorded_as_such(
    conn, audit, config, layout, orphan, scan_dirs
):
    write_registry(
        scan_dirs["registry_dir"], pid=4242, session_id="s-1", proc_start="777", cwd=str(orphan)
    )
    write_proc(scan_dirs["proc_root"], 4242, starttime="777", cwd=str(orphan))
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH})
    asked: list[str] = []

    result = remove(
        make_ctx(conn, audit, config, vcs),
        orphan,
        scan_dirs,
        force=True,
        confirm=lambda p: asked.append(p) or "issue-29",
    )

    assert result.code == EXIT_OK, result.lines
    assert "pid 4242" in asked[0]
    assert result.data["forced_over_live_worker"] is True
    assert records(layout, "worktree.remove")[-1]["detail"]["forced_over_live_worker"] is True


def test_a_removal_that_is_only_claimed_is_a_refusal(
    conn, audit, config, layout, orphan, scan_dirs
):
    vcs = ClaimsOnly(audit, worktrees={str(orphan): BRANCH})

    result = remove(make_ctx(conn, audit, config, vcs), orphan, scan_dirs)

    assert result.code == EXIT_FAILED
    assert result.data["refused_by"] == "directory_survived"
    assert orphan.is_dir()
    assert git_touched(layout) == ["git.remove_worktree"], "the branch was left alone"


# -- worktree list (T020) ------------------------------------------------------


def test_orphans_are_listed_after_the_claimed_rows(conn, audit, config, orphan):
    claimed = make_dir(config, "issue-42")
    claim(conn, claimed, issue_number=42)
    vcs = ListingVcs(audit, worktrees={str(orphan): BRANCH})

    result = operations.worktree_list(make_ctx(conn, audit, config, vcs))

    first, last = result.data["worktrees"]
    assert first["claimed"] is True and first["path"] == str(claimed)
    assert last["claimed"] is False and last["item_id"] is None
    assert last["path"] == str(orphan) and last["branch"] == BRANCH
    assert last["condition"] == "unclaimed" and last["simulated"] is None
    row = next(line for line in result.lines if str(orphan) in line)
    assert row.startswith("—") and "unclaimed" in row and BRANCH in row
    assert any("claimed by no work item" in line for line in result.lines)


@pytest.mark.parametrize("include_simulated", [False, True])
def test_orphans_alone_are_not_no_worktrees_recorded(
    conn, audit, config, orphan, include_simulated
):
    result = operations.worktree_list(
        make_ctx(conn, audit, config, ListingVcs(audit)),
        include_simulated=include_simulated,
    )

    assert "no worktrees recorded" not in result.lines
    assert [w["path"] for w in result.data["worktrees"]] == [str(orphan)]


# -- the CLI argument (T021) ---------------------------------------------------


def test_digits_are_an_item_id_and_anything_else_is_a_path(monkeypatch):
    calls: list[tuple[str, Any, bool]] = []
    monkeypatch.setattr(
        operations,
        "worktree_remove",
        lambda ctx, item_id, *, force: calls.append(("id", item_id, force)),
    )
    monkeypatch.setattr(
        operations,
        "worktree_remove_path",
        lambda ctx, path, *, force: calls.append(("path", path, force)),
    )
    parser = cli.build_parser()

    for argv in (
        ["worktree", "remove", "42"],
        ["worktree", "remove", "/home/me/worktrees/demo/issue-29", "--force"],
        ["worktree", "remove", "./42", "--json"],
    ):
        cli._worktree(parser.parse_args(argv), ctx=None)

    assert calls == [
        ("id", 42, False),
        ("path", "/home/me/worktrees/demo/issue-29", True),
        ("path", "./42", False),
    ]
