"""What ``robot-army capacity`` says about each repository's two limits (milestone 047).

The widening under test is small and the reason for it is not: before this milestone the
per-repository block listed only repositories with a *live session*, and a repository
holding its queue for a merge has none. The listing therefore omitted exactly the
repository the author had opened the command to ask about.

The second property is US2 AS4's, extended to the new setting: a surface reporting a limit
should say whether the author chose it or inherited it, because those send them to
different files.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from tests.conftest import make_boundaries, onboard_repo, seed_item, seed_session

from robot_army import operations
from robot_army.states import SessionState, WorkItemState


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


def payload(ctx) -> dict:
    return json.loads(operations.capacity(ctx).render(as_json=True))


def rows(ctx) -> dict[str, dict]:
    return {row["repo_key"]: row for row in payload(ctx)["repos"]}


def test_an_onboarded_repository_with_no_live_session_is_still_listed(ctx, conn, repo_clone):
    """The whole reason for the widening: a repository waiting for a merge has no live
    session, so the old listing left out the one the author came to look at."""
    onboard_repo(conn, "demo", repo_clone)

    assert rows(ctx)["demo"]["sessions"] == 0


def test_a_live_session_is_counted_against_the_repositorys_cap(ctx, conn, repo_clone):
    onboard_repo(conn, "demo", repo_clone)
    item = seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.ACTIVE))
    seed_session(conn, item, state=str(SessionState.RUNNING), session_id="s-1")

    row = rows(ctx)["demo"]
    assert (row["sessions"], row["cap"]) == (1, 1)


def test_an_inherited_cap_is_reported_as_inherited(ctx, conn, repo_clone):
    onboard_repo(conn, "demo", repo_clone)

    row = rows(ctx)["demo"]
    assert row["cap"] == 1
    assert row["cap_explicit"] is False


def test_an_inherited_wait_for_merge_is_reported_as_inherited(config, conn, repo_clone, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log: make_boundaries(log, level=level)
    )
    onboard_repo(conn, "demo", repo_clone)
    built = operations.build_context(
        replace(config, dispatch=replace(config.dispatch, wait_for_merge=True))
    )
    try:
        row = {r["repo_key"]: r for r in payload(built)["repos"]}["demo"]
        assert row["wait_for_merge"] is True
        assert row["wait_explicit"] is False
    finally:
        built.close()


def test_a_chosen_wait_for_merge_is_reported_as_chosen(config, conn, repo_clone, monkeypatch):
    """US3 AS1. "You chose this" and "this is what you get" send the author to different
    files, and only one of them is a file they already have open."""
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log: make_boundaries(log, level=level)
    )
    onboard_repo(conn, "demo", repo_clone)
    section = replace(config.repos["demo"], wait_for_merge=True)
    built = operations.build_context(
        replace(config, repos={**config.repos, "demo": section})
    )
    try:
        row = {r["repo_key"]: r for r in payload(built)["repos"]}["demo"]
        assert row["wait_for_merge"] is True
        assert row["wait_explicit"] is True
    finally:
        built.close()


def test_the_terminal_rendering_carries_both_limits_and_both_sources(ctx, conn, repo_clone):
    onboard_repo(conn, "demo", repo_clone)

    text = operations.capacity(ctx).render(as_json=False)

    assert "per repository:" in text
    assert "0 of 1 sessions" in text
    assert "wait-for-merge: off (default)" in text
    assert "wait-merge   : off globally" in text


def test_per_repo_keeps_its_existing_key_and_meaning(ctx, conn, repo_clone):
    """Nothing reading the live-session count today changes meaning: the new facts arrived
    beside it under their own key rather than by redefining this one."""
    onboard_repo(conn, "demo", repo_clone)
    item = seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.ACTIVE))
    seed_session(conn, item, state=str(SessionState.RUNNING), session_id="s-1")

    assert payload(ctx)["per_repo"] == {"demo": 1}


def test_nothing_onboarded_says_so_rather_than_showing_an_empty_block(ctx):
    text = operations.capacity(ctx).render(as_json=False)
    assert "no repository is onboarded" in text
