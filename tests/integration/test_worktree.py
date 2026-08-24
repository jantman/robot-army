"""Worktree preparation against real temporary git repositories (T061, T062).

Real git rather than a mock, deliberately: the behaviour we depend on most — git's refusal
to remove a dirty worktree — is git's, and mocking it would test the mock.

The hanging-hook test is the one that matters most. M0 F15 measured
``git submodule update --init --recursive`` hanging *indefinitely* on a real repository,
because its ``.gitmodules`` uses ``git://`` URLs and port 9418 is now dropped rather than
refused. It does not error; it hangs. A hung hook wedges a work item in ``dispatching``
forever with no session, no error, and nothing for reconciliation to observe.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from tests.conftest import make_boundaries, make_repo

from robot_army import worktree
from robot_army.boundaries.git import GitVersionControl
from robot_army.boundaries.hooks import SubprocessHookRunner
from robot_army.config import HookStep, RepoConfig

pytestmark = pytest.mark.requires_git


def repo_with(clone: Path, steps: tuple[HookStep, ...] = (), **kwargs) -> RepoConfig:
    return RepoConfig(
        key="demo",
        path=clone,
        base_branch="main",
        post_create=steps,
        env=kwargs.pop("env", {}),
        **kwargs,
    )


def test_preparation_creates_a_worktree_on_a_new_branch(config, audit, repo_clone):
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(repo_clone),
        item_id=1,
        issue_number=42,
        title="Fix the login flow",
        dry_run=False,
    )
    assert result.ok, result.failure_reason
    path = Path(result.worktree_path)
    assert path.is_dir()
    assert (path / "README.md").exists()
    assert result.branch == "robot-army/issue-42-fix-the-login-flow"
    # The worktree directory is keyed on the issue number alone, so it stays stable if
    # the issue is retitled (R18).
    assert path.name == "issue-42"


def test_run_steps_execute_in_order_inside_the_worktree(config, audit, repo_clone):
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    steps = (
        HookStep(kind="run", value="echo first > order.txt", timeout=10),
        HookStep(kind="run", value="echo second >> order.txt", timeout=10),
    )
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(repo_clone, steps),
        item_id=1,
        issue_number=1,
        title="steps",
        dry_run=False,
    )
    assert result.ok, result.failure_reason
    assert (Path(result.worktree_path) / "order.txt").read_text() == "first\nsecond\n"


def test_a_hanging_hook_is_killed_at_its_timeout(config, audit, repo_clone):
    """M0 F15 reproduced deliberately — the hang is the realistic case, not a contrived
    one. Without the timeout the item sits in ``dispatching`` forever."""
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    steps = (HookStep(kind="run", value="sleep 300", timeout=2),)

    started = time.monotonic()
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(repo_clone, steps),
        item_id=1,
        issue_number=1,
        title="hang",
        dry_run=False,
    )
    elapsed = time.monotonic() - started

    assert not result.ok
    assert elapsed < 30, f"the timeout did not fire; took {elapsed:.1f}s"
    assert "timed out" in (result.failure_reason or "")
    assert "process group was killed" in result.output


def test_a_timeout_kills_the_whole_process_group_not_just_the_child(config, audit, repo_clone):
    """A shell command that spawned a grandchild leaves it running if only the direct
    child is killed — the timeout then *appears* to work while the real work continues."""
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    marker = Path(config.worktree_root) / "grandchild-survived"
    marker.parent.mkdir(parents=True, exist_ok=True)
    steps = (
        HookStep(
            kind="run",
            value=f"( sleep 4; touch {marker} ) & sleep 300",
            timeout=2,
        ),
    )
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(repo_clone, steps),
        item_id=1,
        issue_number=1,
        title="grandchild",
        dry_run=False,
    )
    assert not result.ok
    time.sleep(4)
    assert not marker.exists(), "the grandchild outlived the timeout"


def test_a_failing_hook_fails_the_item_and_captures_output(config, audit, repo_clone):
    """T062. ``HookResult(ok=False)`` means the work item fails; a session is never
    launched into a partially prepared worktree (FR-014)."""
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    steps = (
        HookStep(kind="run", value="echo 'setup exploded' >&2; exit 3", timeout=10),
    )
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(repo_clone, steps),
        item_id=1,
        issue_number=1,
        title="boom",
        dry_run=False,
    )
    assert not result.ok
    assert "exited 3" in result.output
    assert "setup exploded" in result.output, "a failure with no output is unactionable"


def test_a_later_step_does_not_run_after_an_earlier_one_fails(config, audit, repo_clone):
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    steps = (
        HookStep(kind="run", value="exit 1", timeout=10),
        HookStep(kind="run", value="touch should-not-exist", timeout=10),
    )
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(repo_clone, steps),
        item_id=1,
        issue_number=1,
        title="stop",
        dry_run=False,
    )
    assert not result.ok
    assert not (Path(result.worktree_path) / "should-not-exist").exists()


def test_link_and_copy_steps_place_files_from_the_primary_clone(config, audit, tmp_path):
    clone = make_repo(tmp_path / "linked")
    (clone / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (clone / "data.txt").write_text("payload\n", encoding="utf-8")

    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    steps = (
        HookStep(kind="link", value=".env", timeout=10),
        HookStep(kind="copy", value="data.txt", timeout=10),
    )
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(clone, steps),
        item_id=1,
        issue_number=1,
        title="place",
        dry_run=False,
    )
    assert result.ok, result.failure_reason
    path = Path(result.worktree_path)
    assert path.joinpath(".env").is_symlink()
    assert path.joinpath(".env").read_text() == "SECRET=1\n"
    assert not path.joinpath("data.txt").is_symlink()
    assert path.joinpath("data.txt").read_text() == "payload\n"


def test_placing_a_file_is_idempotent(config, audit, tmp_path):
    """Preparation can be re-run after an interruption; an existing correct symlink is
    success, not a collision."""
    clone = make_repo(tmp_path / "idem")
    (clone / ".env").write_text("A=1\n", encoding="utf-8")
    runner = SubprocessHookRunner(audit)
    worktree_path = tmp_path / "wt"
    worktree_path.mkdir()
    steps = [HookStep(kind="link", value=".env", timeout=10)]

    assert runner.run(steps, str(worktree_path), str(clone), {}).ok
    assert runner.run(steps, str(worktree_path), str(clone), {}).ok
    assert (worktree_path / ".env").is_symlink()


def test_a_missing_link_source_fails_with_a_clear_reason(config, audit, repo_clone):
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    steps = (HookStep(kind="link", value="not-there", timeout=10),)
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(repo_clone, steps),
        item_id=1,
        issue_number=1,
        title="missing",
        dry_run=False,
    )
    assert not result.ok
    assert "does not exist in the primary clone" in result.output


def test_env_auto_allocates_a_free_port(config, audit, repo_clone):
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(repo_clone, env={"APP_PORT": "auto", "FIXED": "9"}),
        item_id=1,
        issue_number=1,
        title="ports",
        dry_run=False,
    )
    assert result.ok
    assert result.env is not None
    assert result.env["FIXED"] == "9"
    assert 1024 < int(result.env["APP_PORT"]) < 65536


def test_hook_env_reaches_the_command(config, audit, repo_clone):
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    steps = (HookStep(kind="run", value="echo $APP_PORT > port.txt", timeout=10),)
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(repo_clone, steps, env={"APP_PORT": "auto"}),
        item_id=1,
        issue_number=1,
        title="env",
        dry_run=False,
    )
    assert result.ok, result.failure_reason
    written = (Path(result.worktree_path) / "port.txt").read_text().strip()
    assert written == result.env["APP_PORT"]


def test_a_second_worktree_on_the_same_branch_fails_rather_than_reusing(
    config, audit, repo_clone
):
    """Silently reusing leftover state would launch a session into a worktree whose
    contents we cannot vouch for."""
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    args = dict(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(repo_clone),
        item_id=1,
        issue_number=7,
        title="dup",
        dry_run=False,
    )
    assert worktree.prepare(**args).ok
    second = worktree.prepare(**args)
    assert not second.ok
    assert "could not create worktree" in (second.failure_reason or "")


def test_condition_reports_dirty_present_and_missing(config, audit, repo_clone):
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(repo_clone),
        item_id=1,
        issue_number=5,
        title="cond",
        dry_run=False,
    )
    vcs = GitVersionControl(audit)
    condition = worktree.condition(
        vcs, str(repo_clone), result.worktree_path, result.branch, "main"
    )
    assert condition.label == "present"
    assert condition.commits_ahead == 0

    # An untracked file counts as dirty — quickstart scenario 9 relies on that.
    Path(result.worktree_path, "scratch.txt").write_text("x", encoding="utf-8")
    condition = worktree.condition(
        vcs, str(repo_clone), result.worktree_path, result.branch, "main"
    )
    assert condition.dirty is True
    assert condition.label == "dirty"


def test_a_fetch_failure_fails_the_item_rather_than_raising(config, audit, tmp_path):
    """The caller's job is to fail the work item with a reason the maintainer can act on,
    and a traceback is not that."""
    clone = make_repo(tmp_path / "noremote")
    import subprocess

    subprocess.run(
        ["git", "remote", "add", "origin", "https://127.0.0.1:1/nope.git"],
        cwd=clone,
        check=True,
        capture_output=True,
    )
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(clone),
        item_id=1,
        issue_number=1,
        title="unreachable",
        dry_run=False,
    )
    assert not result.ok
    assert "git fetch failed" in (result.failure_reason or "")


def test_a_real_remote_is_fetched_and_the_worktree_starts_from_it(config, audit, tmp_path):
    """The normal case, exercised against a real remote rather than skipped.

    The worktree must start from ``origin/main``, not from the clone's local ``main``: a
    clone that is behind produces a worktree built on a stale base, which is a subtle
    problem that surfaces only as confusing conflicts much later.
    """
    import subprocess

    upstream = make_repo(tmp_path / "upstream")
    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", "-q", str(upstream), str(bare)], check=True, capture_output=True
    )
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(bare), str(clone)], check=True, capture_output=True
    )
    subprocess.run(["git", "config", "core.excludesFile", "/dev/null"], cwd=clone, check=True)

    # Advance the remote after cloning, so the clone's local `main` is now behind.
    subprocess.run(
        ["git", "remote", "add", "origin", str(bare)], cwd=upstream, check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q",
         "--allow-empty", "-m", "moved on"],
        cwd=upstream, check=True, capture_output=True,
    )
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=upstream, check=True,
                   capture_output=True)

    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=repo_with(clone),
        item_id=1,
        issue_number=9,
        title="fetched",
        dry_run=False,
    )
    assert result.ok, result.failure_reason

    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=result.worktree_path, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert log == "moved on", "the worktree was created from a stale local base"
