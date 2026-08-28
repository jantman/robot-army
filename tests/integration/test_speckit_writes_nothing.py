"""FR-018 and SC-004: this milestone writes nothing into a worktree.

Asserted by hashing rather than by reading the code, and by hashing **everything** rather
than by checking ``git status``: an injected file would most plausibly be one that git had
been told to ignore, which is exactly what a status check would miss. That is not a
hypothetical — the design this milestone rejected would have written an extensions
registration and a hook command into every dispatched worktree and then had to keep them out
of the author's commits.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from tests.conftest import make_boundaries, make_repo

from robot_army import reconcile
from robot_army.boundaries import Issue
from robot_army.boundaries.hooks import SubprocessHookRunner

from .test_speckit_dispatch import phase_records, prepare_item, speckit_files

pytestmark = pytest.mark.requires_git


def snapshot(root: Path) -> dict[str, str]:
    """Every path under ``root``, with a hash of its contents. Ignored files included."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_symlink():
            out[key] = f"symlink:{path.readlink()}"
        elif path.is_dir():
            out[key] = "dir"
        else:
            out[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def test_detection_and_observation_leave_the_worktree_byte_identical(
    conn, audit, config, layout, tmp_path
):
    from robot_army import dispatch

    clone = make_repo(tmp_path / "clones" / "demo", files=speckit_files())
    item_id, path = prepare_item(conn, audit, config, clone)

    before = snapshot(path)

    # Everything milestone 007 does at dispatch time: detect, decide, compose.
    block = dispatch.speckit_block(
        config=config,
        audit=audit,
        repo_key="demo",
        item_id=item_id,
        worktree_path=str(path),
    )
    assert block is not None, "the fixture repository does use Spec Kit"

    plan = dispatch.build_launch_plan(
        config=config,
        layout=layout,
        boundaries=make_boundaries(audit, hooks=SubprocessHookRunner(audit)),
        audit=audit,
        repo_key="demo",
        item_id=item_id,
        issue=Issue(
            number=42,
            title="Add a thing",
            body="please",
            url="https://github.example/42",
            labels=(),
            author="jantman",
            state="open",
        ),
        worktree_path=str(path),
        branch="robot-army/issue-42",
        session_id="fixed-uuid",
    )
    assert "This repository uses Spec Kit" in plan.worker_argv[-1]

    # And everything it does afterwards, twice, with a real transition in between.
    reconcile._observe_speckit(conn, audit=audit)
    (path / "specs" / "007-new").mkdir(parents=True)
    (path / "specs" / "007-new" / "spec.md").write_text("# spec\n", encoding="utf-8")
    reconcile._observe_speckit(conn, audit=audit)
    reconcile._observe_speckit(conn, audit=audit)
    assert len(phase_records(layout)) == 1, "the observation really did do something"

    after = snapshot(path)

    # The one difference is the file the *test* wrote, standing in for the session.
    written_by_the_test = {"specs/007-new", "specs/007-new/spec.md"}
    assert set(after) - set(before) == written_by_the_test
    assert set(before) - set(after) == set()
    unchanged = {k: v for k, v in after.items() if k not in written_by_the_test}
    assert unchanged == before


def test_a_worktree_of_a_plain_repository_is_untouched_too(
    conn, audit, config, layout, repo_clone
):
    """The no-op path writes nothing either — including no ``.specify`` of our own."""
    from robot_army import dispatch

    item_id, path = prepare_item(conn, audit, config, repo_clone)
    before = snapshot(path)

    assert (
        dispatch.speckit_block(
            config=config,
            audit=audit,
            repo_key="demo",
            item_id=item_id,
            worktree_path=str(path),
        )
        is None
    )
    reconcile._observe_speckit(conn, audit=audit)

    assert snapshot(path) == before


def test_nothing_named_extensions_is_ever_created(conn, audit, config, tmp_path):
    """FR-020, stated as a file that must not exist. The milestone's own Out of Scope
    section is the argument; this is the assertion."""
    clone = make_repo(tmp_path / "clones" / "demo", files=speckit_files())
    _, path = prepare_item(conn, audit, config, clone)
    reconcile._observe_speckit(conn, audit=audit)

    assert not (path / ".specify" / "extensions.yml").exists()
    assert list(path.rglob("extensions.yml")) == []
