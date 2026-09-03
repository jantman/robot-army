"""What the terminal says about a repository's board (issue #48, T035).

The property that matters most here is negative: none of this contacts GitHub. The
resolution and the snapshot are stored precisely so "why is this repository not ordered by
its board?" stays answerable with the board unreachable.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import db, operations
from robot_army.models import RepoProject
from robot_army.states import WorkItemState


class ExplodingReader:
    """A reader that fails any call. Anything reading it here is a bug, not a slow test."""

    def __getattr__(self, name):
        def boom(*args, **kwargs):
            raise AssertionError(f"status must not call the GitHub reader ({name})")

        return boom


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log: make_boundaries(log, level=level, reader=ExplodingReader()),
    )
    built = operations.build_context(config)
    yield built
    built.close()


def govern(conn, repo_key="demo", **overrides):
    fields = {
        "repo_key": repo_key,
        "project_id": "PVT_3",
        "project_number": 3,
        "project_title": "robot-army",
        "project_url": "https://github.com/users/jantman/projects/3",
        "project_source": "discovered",
        "column_name": "Ready",
        "column_source": "discovered",
        "resolved_at": "2026-09-02T00:00:00Z",
        "last_read_at": "2026-09-02T00:00:00Z",
    }
    fields.update(overrides)
    with db.transaction(conn):
        db.save_repo_project(conn, RepoProject(**fields))


def render(ctx, idle_machine, **kwargs):
    registry, proc = idle_machine
    result = operations.status(ctx, registry_dir=registry, proc_root=proc, **kwargs)
    return result, "\n".join(result.lines)


def ready(conn, number, *, repo_key="demo"):
    return seed_item(
        conn, repo_key=repo_key, issue_number=number, state=str(WorkItemState.READY)
    )


def test_a_governed_repository_reports_its_board_and_both_sources(
    ctx, conn, idle_machine
):
    ready(conn, 1)
    govern(conn)

    result, text = render(ctx, idle_machine)

    row = next(r for r in result.data["projects"] if r["repo_key"] == "demo")
    assert row["governs"] is True
    assert row["project_number"] == 3
    assert row["column"] == "Ready"
    assert row["project_source"] == "discovered"
    assert row["column_source"] == "discovered"
    assert row["last_read_age_seconds"] is not None
    assert "#3 robot-army" in text
    assert "'Ready'" in text


def test_an_unresolved_repository_reports_why(ctx, conn, idle_machine):
    ready(conn, 1)
    govern(conn, resolved_at=None, last_read_at=None, unresolved_reason="two are linked")

    result, text = render(ctx, idle_machine)

    row = next(r for r in result.data["projects"] if r["repo_key"] == "demo")
    assert row["governs"] is False
    assert row["unresolved_reason"] == "two are linked"
    assert "two are linked" in text


def test_a_repository_with_no_board_says_nothing(ctx, conn, idle_machine):
    """Most installations have none. A line on every one of them would bury the rows that
    matter."""
    ready(conn, 1)

    _, text = render(ctx, idle_machine)

    assert "project boards:" not in text


def test_a_repository_switched_off_is_still_shown(ctx, conn, idle_machine, config):
    """A choice the author made, and may have forgotten making."""
    from robot_army.config import RepoConfig

    ready(conn, 1)
    section = RepoConfig(key="demo", path=None, base_branch="main", project_ordering=False)
    ctx.config = replace(config, repos={**config.repos, "demo": section})

    result, text = render(ctx, idle_machine)

    row = next(r for r in result.data["projects"] if r["repo_key"] == "demo")
    assert row["enabled"] is False
    assert row["enabled_explicit"] is True
    assert "board ordering off" in text


def test_an_inherited_off_switch_is_not_reported_as_configured(ctx, conn, idle_machine, config):
    """Found in review. Telling the author "configured" when they inherited the value
    sends them looking for a line that is not in their file — the exact failure the
    explicit flag exists to prevent."""
    ready(conn, 1)
    govern(conn, resolved_at=None, last_read_at=None, unresolved_reason="two are linked")
    ctx.config = replace(config, dispatch=replace(config.dispatch, project_ordering=False))

    result, text = render(ctx, idle_machine)

    row = next(r for r in result.data["projects"] if r["repo_key"] == "demo")
    assert (row["enabled"], row["enabled_explicit"]) == (False, False)
    assert "board ordering off (default)" in text
    assert "(configured)" not in text


def test_the_off_column_count_is_reported(ctx, conn, idle_machine):
    """FR-030. A repository whose entire backlog is parked has ready items and dispatches
    none of them, which without a count reads exactly like having no work."""
    first = ready(conn, 1)
    second = ready(conn, 2)
    govern(conn)
    conn.execute(
        "UPDATE work_items SET board_column = 'Backlog' WHERE id IN (?, ?)",
        (first, second),
    )

    result, text = render(ctx, idle_machine)

    row = next(r for r in result.data["projects"] if r["repo_key"] == "demo")
    assert row["held_off_column"] == 2
    assert "2 held off-column" in text


def test_a_failed_read_is_reported_with_the_error(ctx, conn, idle_machine):
    ready(conn, 1)
    govern(conn, consecutive_failures=2, last_error="GitHub is down")

    result, _ = render(ctx, idle_machine)

    row = next(r for r in result.data["projects"] if r["repo_key"] == "demo")
    assert row["consecutive_failures"] == 2
    assert row["last_error"] == "GitHub is down"
    assert row["governs"] is True, "a stale board still governs (FR-025)"


def test_capacity_reports_the_setting_and_its_source(ctx, conn, idle_machine, config):
    from robot_army.config import RepoConfig

    ready(conn, 1)
    registry, proc = idle_machine

    result = operations.capacity(ctx, registry_dir=registry, proc_root=proc)
    row = next(r for r in result.data["repos"] if r["repo_key"] == "demo")
    assert row["project_ordering"] is True
    assert row["project_explicit"] is False
    assert "board-order: on  (default)" in "\n".join(result.lines)

    section = RepoConfig(key="demo", path=None, base_branch="main", project_ordering=False)
    ctx.config = replace(config, repos={**config.repos, "demo": section})
    result = operations.capacity(ctx, registry_dir=registry, proc_root=proc)
    row = next(r for r in result.data["repos"] if r["repo_key"] == "demo")
    assert (row["project_ordering"], row["project_explicit"]) == (False, True)
