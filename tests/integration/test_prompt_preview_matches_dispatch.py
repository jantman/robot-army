"""The claim the whole feature rests on: the preview *is* the dispatch's prompt.

Not "looks like", not "contains the same sections" — the same string. That is why this test
compares against ``dispatch.build_launch_plan``'s output rather than against a golden file:
a golden file would keep passing after someone reimplemented composition inside
``operations``, which is the exact drift the design forbids (research R3, R12).

If this ever fails, the defect is in ``operations.prompt_preview``. ``prompt.compose`` is
dispatch's, and the preview has no business disagreeing with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.conftest import (
    config_dict,
    make_boundaries,
    make_speckit_tree,
    monkey_token,
    onboard_repo,
)

from robot_army import db, dispatch, operations
from robot_army.boundaries import Issue
from robot_army.config import parse

REPO = "jantman/demo"

ISSUE = Issue(
    number=11,
    title="Teach the poller to back off",
    body="It hammers the API when a repository 404s.\n\nSee the log.",
    url="https://github.com/jantman/demo/issues/11",
    labels=("robot-army",),
    author="jantman",
    state="open",
)


@pytest.fixture
def wired(conn, repo_clone, layout, tmp_path, monkeypatch):
    monkey_token()
    raw = config_dict(repo_clone, layout, tmp_path / "worktrees")
    raw["repos"] = {REPO: {"path": str(repo_clone), "base_branch": "main"}}
    config = parse(raw, tmp_path / "config.toml")
    onboard_repo(conn, REPO, repo_clone)
    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log: make_boundaries(log, level=level, reader=_reader()),
    )
    ctx = operations.build_context(config)
    yield ctx
    ctx.close()


def _reader():
    from tests.conftest import FakeIssueReader

    return FakeIssueReader([ISSUE])


def dispatch_prompt(ctx: Any, *, worktree: Path, branch: str, item_id: int) -> str:
    """The prompt argument a real dispatch would put in the worker's argv.

    Read back off ``worker_argv`` rather than by calling ``prompt.compose`` here, so this
    test is anchored to what the *dispatcher* produces and not to the same helper the
    preview happens to call.
    """
    plan = dispatch.build_launch_plan(
        config=ctx.config,
        layout=ctx.config.layout,
        boundaries=ctx.boundaries,
        audit=ctx.audit,
        repo_key=REPO,
        item_id=item_id,
        issue=ISSUE,
        worktree_path=str(worktree),
        branch=branch,
        session_id="session-under-test",
    )
    return plan.worker_argv[-1]


def test_an_untracked_issue_previews_exactly_what_a_dispatch_would_send(wired, repo_clone):
    """No work item, so the preview reads the clone — and a dispatch cut from that clone
    at that moment composes the identical string."""
    (repo_clone / ".claude").mkdir(exist_ok=True)
    (repo_clone / ".claude" / "robot-army.md").write_text(
        "Run the linter before pushing.", encoding="utf-8"
    )
    make_speckit_tree(repo_clone)

    preview = operations.prompt_preview(wired, REPO, 11)
    expected = dispatch_prompt(
        wired,
        worktree=repo_clone,
        branch="robot-army/issue-11-teach-the-poller-to-back-off",
        item_id=1,
    )

    assert preview.code == operations.EXIT_OK
    assert preview.data["prompt"] == expected


def test_a_dispatched_item_previews_from_its_own_worktree(wired, tmp_path, repo_clone):
    """User Story 2: the worktree can carry instructions the clone does not, and the
    preview must follow the item rather than the repository."""
    worktree = tmp_path / "worktrees" / "demo" / "issue-11"
    (worktree / ".claude").mkdir(parents=True)
    (worktree / ".claude" / "robot-army.md").write_text(
        "This worktree has its own rules.", encoding="utf-8"
    )
    make_speckit_tree(worktree)
    branch = "robot-army/issue-11-something-else-entirely"

    with db.transaction(wired.conn):
        item_id = db.insert_work_item(
            conn=wired.conn,
            source="github",
            source_id=f"{REPO}#11",
            source_url=ISSUE.url,
            repo_key=REPO,
            issue_number=11,
            title=ISSUE.title,
            body=ISSUE.body,
            labels='["robot-army"]',
            author=ISSUE.author,
            dry_run=False,
        )
        # State is irrelevant here — the preview reads the worktree and the branch, not
        # where the item sits in its lifecycle — and state changes are the transition
        # function's business, not a test's.
        db.update_work_item_columns(
            wired.conn, item_id, worktree_path=str(worktree), branch=branch
        )

    preview = operations.prompt_preview(wired, REPO, 11)
    expected = dispatch_prompt(wired, worktree=worktree, branch=branch, item_id=item_id)

    assert preview.data["prompt"] == expected
    assert "This worktree has its own rules." in preview.data["prompt"]
