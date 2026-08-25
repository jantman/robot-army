"""Cleanup driven against real git repositories with a real remote (T061, T062, T063).

Real git and a real bare remote, deliberately. The two behaviours this depends on most are
git's refusal to remove a dirty worktree and ``rev-list --count``'s answer about
containment, and mocking either would test the mock. The remote has to be real too: the
containment check fetches, and a fetch against nothing proves nothing.

**The assertion that matters most in this file is that no branch holding unpushed commits
is ever deleted** (SC-009). Everything else here is annoying when it goes wrong. That one
destroys work.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import (
    config_dict,
    make_boundaries,
    make_repo,
    monkey_token,
    seed_item,
    seed_session,
)

from robot_army import cleanup, db, reconcile, worktree
from robot_army.boundaries.hooks import SubprocessHookRunner
from robot_army.config import parse
from robot_army.effects import EffectLevel
from robot_army.states import SessionState, WorkItemState

pytestmark = pytest.mark.requires_git

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def git(cwd: Path, *args: str) -> str:
    import os

    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def branches(clone: Path) -> set[str]:
    out = git(clone, "branch", "--format=%(refname:short)")
    return {line.strip() for line in out.splitlines() if line.strip()}


@pytest.fixture
def published(tmp_path: Path) -> Path:
    """A clone whose ``main`` really is on a real ``origin``.

    Without a remote, ``commits_ahead(origin/main, branch)`` cannot answer and every branch
    would be retained — which would make the whole file pass while proving nothing.
    """
    bare = tmp_path / "remote.git"
    bare.mkdir()
    git(bare, "init", "--bare", "-q", "-b", "main")
    clone = make_repo(tmp_path / "clones" / "demo")
    git(clone, "remote", "add", "origin", str(bare))
    git(clone, "push", "-q", "origin", "main")
    git(clone, "fetch", "-q", "origin", "main")
    return clone


@pytest.fixture
def config(published: Path, layout: Any, tmp_path: Path) -> Any:
    monkey_token()
    raw = config_dict(published, layout, tmp_path / "worktrees", cleanup={"on_issue_close": True})
    return parse(raw, tmp_path / "config.toml")


@pytest.fixture
def boundaries(audit: Any) -> Any:
    return make_boundaries(audit, hooks=SubprocessHookRunner(audit))


def finished_item(conn, audit, config, boundaries, *, issue_number: int) -> tuple[int, Path, str]:
    """A ``done`` item with a real worktree on a real branch."""
    item_id = seed_item(conn, issue_number=issue_number, state=str(WorkItemState.DONE))
    prepared = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=config.repos["demo"],
        item_id=item_id,
        issue_number=issue_number,
        title=f"item {issue_number}",
        dry_run=False,
    )
    assert prepared.ok, prepared.failure_reason
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item_id, worktree_path=prepared.worktree_path, branch=prepared.branch
        )
    return item_id, Path(prepared.worktree_path), prepared.branch


def sweep(conn, audit, config, boundaries, **kwargs) -> list[cleanup.Decision]:
    return cleanup.sweep(
        conn, boundaries=boundaries, audit=audit, config=config, **kwargs
    )


def commit_in(path: Path, name: str, text: str) -> None:
    (path / name).write_text(text, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", f"add {name}")


# -- the four-item fixture the milestone is graded on (T061) ---------------


def test_the_pass_removes_only_what_is_provably_safe(
    conn, audit, config, boundaries, published
):
    """Four items, one of each hazard, one pass, zero wrong removals.

    * clean and contained in the published base → removed
    * a dirty worktree → kept, both halves
    * an unpushed branch → worktree reclaimed, **branch kept**
    * a live session → nothing touched, revisited later
    """
    safe, safe_path, safe_branch = finished_item(
        conn, audit, config, boundaries, issue_number=1
    )
    dirty, dirty_path, dirty_branch = finished_item(
        conn, audit, config, boundaries, issue_number=2
    )
    unpushed, unpushed_path, unpushed_branch = finished_item(
        conn, audit, config, boundaries, issue_number=3
    )
    busy, busy_path, busy_branch = finished_item(
        conn, audit, config, boundaries, issue_number=4
    )

    (dirty_path / "scratch.txt").write_text("notes the author still wants\n", encoding="utf-8")
    commit_in(unpushed_path, "invention.py", "# exists nowhere else\n")
    unpushed_sha = git(unpushed_path, "rev-parse", "HEAD").strip()
    seed_session(conn, busy, state=str(SessionState.RUNNING), session_id="s-busy")

    states = {d.item_id: d.state for d in sweep(conn, audit, config, boundaries)}

    assert states[safe] == cleanup.DONE
    assert not safe_path.exists()
    assert safe_branch not in branches(published)

    assert states[dirty] == cleanup.RETAINED
    assert dirty_path.exists()
    assert (dirty_path / "scratch.txt").exists()
    assert dirty_branch in branches(published)

    assert states[unpushed] == cleanup.BRANCH_RETAINED
    assert not unpushed_path.exists(), "a clean worktree is still reclaimed"
    assert unpushed_branch in branches(published)

    assert states[busy] == cleanup.SKIPPED
    assert busy_path.exists()
    assert busy_branch in branches(published)

    # SC-009, stated as the thing that must not have happened: the commit that existed only
    # on that branch is still reachable.
    assert git(published, "cat-file", "-t", unpushed_sha).strip() == "commit"
    assert unpushed_sha in git(published, "rev-parse", unpushed_branch)


def test_a_branch_pushed_under_its_own_name_is_deleted(
    conn, audit, config, boundaries, published
):
    """The second way for work to be safe: it is on the remote, even though unmerged."""
    _, path, branch = finished_item(conn, audit, config, boundaries, issue_number=5)
    commit_in(path, "feature.py", "# real work\n")
    git(path, "push", "-q", "origin", f"{branch}:{branch}")

    decisions = sweep(conn, audit, config, boundaries)
    assert [d.state for d in decisions] == [cleanup.DONE]
    assert not path.exists()
    assert branch not in branches(published)


def test_a_skipped_item_is_reconsidered_once_its_session_ends(
    conn, audit, config, boundaries, published
):
    item, path, _branch = finished_item(conn, audit, config, boundaries, issue_number=6)
    row = seed_session(conn, item, state=str(SessionState.RUNNING), session_id="s-live")
    assert [d.state for d in sweep(conn, audit, config, boundaries)] == [cleanup.SKIPPED]
    assert path.exists()

    conn.execute(
        "UPDATE sessions SET state = ? WHERE id = ?", (str(SessionState.EXITED_CLEAN), row)
    )
    conn.commit()
    assert [d.state for d in sweep(conn, audit, config, boundaries)] == [cleanup.DONE]
    assert not path.exists()


def test_a_retained_decision_is_not_revisited_automatically(
    conn, audit, config, boundaries, published
):
    """"Not yet" and "we looked and decided no" are different answers. Only the explicit
    command reconsiders the second."""
    item, path, _ = finished_item(conn, audit, config, boundaries, issue_number=7)
    (path / "scratch.txt").write_text("x\n", encoding="utf-8")
    assert [d.state for d in sweep(conn, audit, config, boundaries)] == [cleanup.RETAINED]

    assert sweep(conn, audit, config, boundaries) == [], "the automatic pass leaves it alone"

    (path / "scratch.txt").unlink()
    reconsidered = sweep(conn, audit, config, boundaries, item_id=item)
    assert [d.state for d in reconsidered] == [cleanup.DONE]


def test_cleanup_touches_nothing_outside_the_worktree_root(
    conn, audit, config, boundaries, published
):
    """FR-031. The author's own clone is read for fetch, rev-list and branch -D, and is
    never removed from."""
    _, path, _branch = finished_item(conn, audit, config, boundaries, issue_number=8)
    assert Path(path).resolve().is_relative_to(config.worktree_root.resolve())

    sweep(conn, audit, config, boundaries)
    assert published.is_dir()
    assert (published / "README.md").exists()


# -- interruption (T062) ----------------------------------------------------


def test_killed_between_the_two_removals_resolves_on_the_next_pass(
    conn, audit, config, boundaries, published
):
    """Worktree gone, branch present, ``cleanup_state`` never written. Git refuses the
    removal because there is no working tree there — a refusal about its *record* — so the
    next pass completes the branch half rather than leaving the branch orphaned forever."""
    item, path, branch = finished_item(conn, audit, config, boundaries, issue_number=9)
    subprocess.run(["rm", "-rf", str(path)], check=True)
    assert branch in branches(published)
    assert db.get_work_item(conn, item).cleanup_state is None

    decisions = sweep(conn, audit, config, boundaries)
    assert [d.state for d in decisions] == [cleanup.DONE]
    assert branch not in branches(published)


def test_killed_after_both_removals_before_the_row_is_written(
    conn, audit, config, boundaries, published
):
    """Both refusals are harmless — there is nothing left to remove — and the row is written
    ``done`` on the re-attempt rather than left ambiguous forever."""
    item, path, branch = finished_item(conn, audit, config, boundaries, issue_number=10)
    subprocess.run(["rm", "-rf", str(path)], check=True)
    # Git still holds a record of the worktree, and it will not delete a branch it believes
    # is checked out — so pruning is part of reproducing "both removals happened".
    git(published, "worktree", "prune")
    git(published, "branch", "-D", branch)
    conn.execute("UPDATE work_items SET cleanup_state = NULL WHERE id = ?", (item,))
    conn.commit()

    decisions = sweep(conn, audit, config, boundaries)
    assert [d.state for d in decisions] == [cleanup.DONE]
    assert db.get_work_item(conn, item).cleanup_state == cleanup.DONE


def test_a_failed_containment_fetch_keeps_the_branch_and_is_reconsidered(
    conn, audit, config, boundaries, published
):
    """The failure direction is always "keep". A fetch against a remote that has gone away
    leaves containment unproven, which is not the same as disproven — but it is treated the
    same way, because only proof authorises a delete."""
    _, path, branch = finished_item(conn, audit, config, boundaries, issue_number=11)
    git(published, "remote", "set-url", "origin", str(path.parent / "does-not-exist.git"))

    decisions = sweep(conn, audit, config, boundaries)
    assert [d.state for d in decisions] == [cleanup.BRANCH_RETAINED]
    assert branch in branches(published)


def test_an_interrupted_pass_leaves_no_half_written_row(
    conn, audit, config, boundaries, published
):
    """No subprocess and no network call runs inside a transaction, so a kill can never
    leave a row that is partly one outcome and partly another."""
    item, _path, _branch = finished_item(conn, audit, config, boundaries, issue_number=12)
    sweep(conn, audit, config, boundaries)
    row = db.get_work_item(conn, item)
    assert row.cleanup_state == cleanup.DONE
    assert row.cleanup_reason
    assert row.cleaned_at
    assert conn.in_transaction is False


# -- effect levels (T063, FR-039) ------------------------------------------


def wired_at(level: EffectLevel, config: Any, audit: Any) -> Any:
    from robot_army.effects import wire

    return wire(level, config, audit)


def test_below_local_the_removals_are_simulated_and_nothing_leaves_the_disk(
    conn, audit, config, published, boundaries
):
    """Cleanup follows worktree *creation*'s effect rule, not the board's: simulated at
    ``plan``, real at ``local`` and above. The simulated ``VersionControl`` logs both calls
    with their full arguments, so the intent is fully recorded either way."""
    _, path, branch = finished_item(conn, audit, config, boundaries, issue_number=13)

    decisions = sweep(conn, audit, config, wired_at(EffectLevel.PLAN, config, audit))
    assert [d.state for d in decisions] == [cleanup.DONE]
    assert path.exists(), "at plan level nothing may leave the disk"
    assert branch in branches(published)

    audit.close()
    records = [
        json.loads(line)
        for log in sorted(config.layout.log_dir.glob("audit-*.jsonl"))
        for line in log.read_text(encoding="utf-8").splitlines()
    ]
    simulated = [r for r in records if r.get("simulated") and r["action"] == "git.remove_worktree"]
    assert simulated, "the intended removal must be logged with its full arguments"
    assert simulated[0]["detail"]["force"] is False


@pytest.mark.parametrize("level", [EffectLevel.LOCAL, EffectLevel.NO_REMOTE, EffectLevel.LIVE])
def test_at_local_and_above_the_removals_are_real(
    conn, audit, config, published, boundaries, level
):
    _, path, branch = finished_item(conn, audit, config, boundaries, issue_number=14)
    sweep(conn, audit, config, wired_at(level, config, audit))
    assert not path.exists()
    assert branch not in branches(published)


# -- the reconciliation pass wires it, and only when asked -----------------


def test_the_pass_does_nothing_while_on_issue_close_is_false(
    conn, audit, config, boundaries, published
):
    """The Operating Constraints require irreversible actions to be unreachable by default,
    and worktree removal and branch deletion are both."""
    off = replace(config, cleanup=replace(config.cleanup, on_issue_close=False))
    item, path, _branch = finished_item(conn, audit, off, boundaries, issue_number=15)

    result = reconcile.reconcile(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=off,
        layout=off.layout,
        registry_dir=path.parent / "no-registry",
    )
    assert result.cleaned == 0
    assert path.exists()
    assert db.get_work_item(conn, item).cleanup_state is None


def test_the_explicit_command_runs_even_when_the_automatic_path_is_disabled(
    conn, audit, config, boundaries, published
):
    """FR-029. The setting governs *when* cleanup happens, not whether it is possible."""
    off = replace(config, cleanup=replace(config.cleanup, on_issue_close=False))
    item, path, _branch = finished_item(conn, audit, off, boundaries, issue_number=16)

    decisions = sweep(conn, audit, off, boundaries, item_id=item)
    assert [d.state for d in decisions] == [cleanup.DONE]
    assert not path.exists()
