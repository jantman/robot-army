"""Worktree removal refuses on a dirty tree, and removes **both** parts (T127).

Two things are being asserted, and both are FR-016:

1. Git's refusal to remove a worktree with uncommitted *or merely untracked* changes is
   the guard — not something we implement, something we decline to override. The test
   covers untracked separately because that is the case a hand-rolled "is it dirty?"
   check would most plausibly miss.
2. Removal is **two steps**. A caller that removes the worktree and stops accumulates
   ``robot-army/*`` branches in every repository forever.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from tests.conftest import make_boundaries, seed_item, seed_session

from robot_army import db, operations, worktree
from robot_army.boundaries.hooks import SubprocessHookRunner
from robot_army.config import RepoConfig
from robot_army.effects import EffectLevel
from robot_army.operations import EXIT_FAILED, EXIT_OK, EXIT_PRECONDITION, Context
from robot_army.states import SessionState, WorkItemState

pytestmark = pytest.mark.requires_git


def prepared_item(
    conn, audit, config, layout, *, state: WorkItemState = WorkItemState.INTERRUPTED
) -> tuple[int, Path, str]:
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    item_id = seed_item(conn, state=str(state))
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=RepoConfig(key="demo", path=config.repos["demo"].path, base_branch="main"),
        item_id=item_id,
        issue_number=42,
        title="Fix the thing",
        dry_run=False,
    )
    assert result.ok, result.failure_reason
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item_id, worktree_path=result.worktree_path, branch=result.branch
        )
    return item_id, Path(result.worktree_path), result.branch


def make_context(conn, audit, config) -> Context:
    return Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )


def branches(clone: Path) -> set[str]:
    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=clone, capture_output=True, text=True, check=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def test_a_clean_worktree_is_removed_along_with_its_branch(conn, audit, config, layout):
    item_id, path, branch = prepared_item(conn, audit, config, layout)
    clone = config.repos["demo"].path
    assert branch in branches(clone)

    result = operations.worktree_remove(make_context(conn, audit, config), item_id)
    assert result.code == EXIT_OK, result.lines
    assert not path.exists()
    assert branch not in branches(clone), (
        "worktree removal alone always leaves the branch behind; skipping the second "
        "step accumulates robot-army/* branches in every repository forever"
    )
    assert db.get_work_item(conn, item_id).worktree_path is None


def test_removal_refuses_on_uncommitted_changes(conn, audit, config, layout):
    item_id, path, branch = prepared_item(conn, audit, config, layout)
    (path / "README.md").write_text("modified\n", encoding="utf-8")

    result = operations.worktree_remove(make_context(conn, audit, config), item_id)
    assert result.code == EXIT_FAILED
    assert path.exists(), "the worktree must survive the refusal"
    assert branch in branches(config.repos["demo"].path)
    assert "refused" in "\n".join(result.lines)


def test_removal_refuses_on_merely_untracked_files(conn, audit, config, layout):
    """The case a hand-rolled dirty check would miss. Git's refusal covers it for free,
    which is exactly why ``--force`` is never passed by default (M0 E6.5)."""
    item_id, path, branch = prepared_item(conn, audit, config, layout)
    (path / "scratch.txt").write_text("notes\n", encoding="utf-8")

    result = operations.worktree_remove(make_context(conn, audit, config), item_id)
    assert result.code == EXIT_FAILED
    assert path.exists()
    assert (path / "scratch.txt").exists()
    assert branch in branches(config.repos["demo"].path)


def test_force_requires_a_typed_confirmation(conn, audit, config, layout):
    item_id, path, _ = prepared_item(conn, audit, config, layout)
    (path / "scratch.txt").write_text("notes\n", encoding="utf-8")

    refused = operations.worktree_remove(
        make_context(conn, audit, config), item_id, force=True, confirm=lambda _: "yes"
    )
    assert refused.code == EXIT_FAILED
    assert path.exists(), "a generic 'yes' must not be enough to discard work"


def test_force_with_the_typed_id_removes_a_dirty_worktree(conn, audit, config, layout):
    item_id, path, branch = prepared_item(conn, audit, config, layout)
    (path / "scratch.txt").write_text("notes\n", encoding="utf-8")

    result = operations.worktree_remove(
        make_context(conn, audit, config),
        item_id,
        force=True,
        confirm=lambda _: str(item_id),
    )
    assert result.code == EXIT_OK, result.lines
    assert not path.exists()
    assert branch not in branches(config.repos["demo"].path)


def test_a_live_session_survives_the_removal_directory_branch_and_all(
    conn, audit, config, layout
):
    """Issue #79, against real git rather than a stub.

    A read-only session leaves the tree clean, so git has no objection at all — which is
    why this defect was reachable through the ordinary lifecycle and why the guard cannot
    be git's. The item is left ``done`` on purpose: terminal is exactly the state an
    operator reclaims disk from, and the session keeps running by design.
    """
    item_id, path, branch = prepared_item(
        conn, audit, config, layout, state=WorkItemState.DONE
    )
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-live")

    result = operations.worktree_remove(
        make_context(conn, audit, config),
        item_id,
        confirm=lambda _: pytest.fail("the refusal is not a question"),
    )

    assert result.code == EXIT_PRECONDITION, result.lines
    assert path.exists(), "the worker is still writing in there"
    assert (path / "README.md").exists()
    assert branch in branches(config.repos["demo"].path), (
        "the branch is deleted in the same command, so losing it loses the only copy"
    )
    assert db.get_work_item(conn, item_id).worktree_path == str(path)


def test_the_same_worktree_is_removed_once_the_session_closes(conn, audit, config, layout):
    """The other half of the guarantee: no removal that works today becomes a refusal."""
    item_id, path, branch = prepared_item(conn, audit, config, layout)
    seed_session(conn, item_id, state=str(SessionState.EXITED_CLEAN), session_id="s-done")

    result = operations.worktree_remove(make_context(conn, audit, config), item_id)
    assert result.code == EXIT_OK, result.lines
    assert not path.exists()
    assert branch not in branches(config.repos["demo"].path)


def test_removing_an_item_with_no_worktree_fails_clearly(conn, audit, config):
    item_id = seed_item(conn)
    result = operations.worktree_remove(make_context(conn, audit, config), item_id)
    assert result.code == EXIT_FAILED
    assert "has no worktree" in "\n".join(result.lines)


def test_listing_shows_condition_and_size(conn, audit, config, layout):
    item_id, path, _ = prepared_item(conn, audit, config, layout)
    result = operations.worktree_list(make_context(conn, audit, config))
    assert result.code == EXIT_OK
    entry = result.data["worktrees"][0]
    assert entry["item_id"] == item_id
    assert entry["condition"] == "present"
    assert entry["size_bytes"] > 0

    (path / "scratch.txt").write_text("x", encoding="utf-8")
    entry = operations.worktree_list(make_context(conn, audit, config)).data["worktrees"][0]
    assert entry["condition"] == "dirty"
    assert entry["dirty"] is True


def test_a_deleted_directory_shows_as_missing(conn, audit, config, layout):
    import shutil

    item_id, path, _ = prepared_item(conn, audit, config, layout)
    shutil.rmtree(path)
    entry = operations.worktree_list(make_context(conn, audit, config)).data["worktrees"][0]
    assert entry["condition"] == "missing"
    assert entry["item_id"] == item_id


def test_prune_clears_gits_record_of_a_vanished_worktree(conn, audit, config, layout):
    import shutil

    _, path, _ = prepared_item(conn, audit, config, layout)
    clone = config.repos["demo"].path
    shutil.rmtree(path)

    listed = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=clone, capture_output=True, text=True, check=True,
    ).stdout
    assert "prunable" in listed

    result = operations.worktree_prune(make_context(conn, audit, config))
    assert result.code == EXIT_OK
    listed = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=clone, capture_output=True, text=True, check=True,
    ).stdout
    assert str(path) not in listed


def test_abandon_does_not_remove_the_worktree(conn, audit, config, layout):
    """Deliberately separate, so abandoning is never destructive."""
    item_id, path, _ = prepared_item(conn, audit, config, layout)
    result = operations.abandon(make_context(conn, audit, config), item_id)
    assert result.code == EXIT_OK
    assert path.exists()
    assert db.get_work_item(conn, item_id).state is WorkItemState.ABANDONED
