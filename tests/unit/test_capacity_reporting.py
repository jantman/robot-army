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
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level)
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
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level)
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
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level)
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


# -- the cap in force, not the one in the file (issue #30) ------------------


def at_cap(config, n: int):
    return replace(config, daemon=replace(config.daemon, max_concurrent_sessions=n))


def context_for(config, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level)
    )
    return operations.build_context(config)


def test_capacity_reports_the_daemons_cap_when_the_file_is_newer(
    config, conn, layout, monkeypatch, running_daemon
):
    """The direction a fresh terminal command hides best: it has just read the file, so it
    looks maximally trustworthy while printing a limit nothing is applying."""
    from tests.conftest import beat

    beat(layout, max_concurrent_sessions=7)
    built = context_for(at_cap(config, 9), monkeypatch)
    try:
        result = operations.capacity(built)
        document = json.loads(result.render(as_json=True))
        assert document["global_cap"] == 7
        assert document["configured_cap"] == 9
        assert "of 7 sessions running" in result.render(as_json=False)
        assert "SESSION CAP MISMATCH" in result.render(as_json=False)
    finally:
        built.close()


def test_capacity_reports_the_daemons_cap_when_the_file_is_older(
    config, conn, layout, monkeypatch, running_daemon
):
    from tests.conftest import beat

    beat(layout, max_concurrent_sessions=7)
    built = context_for(at_cap(config, 5), monkeypatch)
    try:
        document = json.loads(operations.capacity(built).render(as_json=True))
        assert (document["global_cap"], document["configured_cap"]) == (7, 5)
    finally:
        built.close()


def test_capacity_says_nothing_extra_when_the_two_agree(
    config, conn, layout, monkeypatch, running_daemon
):
    from tests.conftest import beat

    beat(layout, max_concurrent_sessions=5)
    built = context_for(at_cap(config, 5), monkeypatch)
    try:
        result = operations.capacity(built)
        document = json.loads(result.render(as_json=True))
        assert document["configured_cap"] is None
        assert document["cap_disagreement"] is None
        assert "SESSION CAP MISMATCH" not in result.render(as_json=False)
    finally:
        built.close()


def test_capacity_keeps_its_own_configured_cap_with_no_daemon_running(
    config, conn, layout, monkeypatch
):
    """No lock held: nothing is enforcing anything, so a heartbeat left by a dead process
    does not get to overrule the configuration this command actually read."""
    from tests.conftest import beat

    beat(layout, max_concurrent_sessions=7)
    built = context_for(at_cap(config, 5), monkeypatch)
    try:
        document = json.loads(operations.capacity(built).render(as_json=True))
        assert (document["global_cap"], document["configured_cap"]) == (5, None)
    finally:
        built.close()


def test_status_reports_the_same_cap_the_web_chrome_does(
    config, conn, layout, monkeypatch, running_daemon
):
    """SC-003: two surfaces read a second apart must print the same denominator. Both
    render ``_capacity_dict``, so the keys are the same by identity, not by agreement."""
    from tests.conftest import beat

    beat(layout, max_concurrent_sessions=7)
    built = context_for(at_cap(config, 5), monkeypatch)
    try:
        result = operations.status(built)
        document = json.loads(result.render(as_json=True))
        assert document["capacity"]["global_cap"] == 7
        assert document["capacity"]["configured_cap"] == 5
        assert "SESSION CAP MISMATCH" in result.render(as_json=False), "the status line carries it too"
    finally:
        built.close()


def test_the_terminal_names_the_process_to_restart(
    config, conn, layout, monkeypatch, running_daemon
):
    """The shared sentence will not say which of the two is behind, because on the web
    either can be. A command can: it read the file milliseconds ago, so the daemon is the
    one that has been running since before the change — and "restart that one" is not a
    remedy someone can act on once the process saying it has exited."""
    from tests.conftest import beat

    beat(layout, max_concurrent_sessions=7)
    built = context_for(at_cap(config, 5), monkeypatch)
    try:
        for text in (
            operations.capacity(built).render(as_json=False),
            operations.status(built).render(as_json=False),
        ):
            assert "the daemon is the one behind" in text
            assert "restart it to apply the cap in the file" in text
    finally:
        built.close()


def test_a_repositorys_limit_is_clamped_by_the_cap_in_force(
    config, conn, repo_clone, layout, monkeypatch, running_daemon
):
    """A per-repository cap is ``min(repo, global)``, so a stale global would report a
    repository limit that nothing is enforcing either — the same defect one level down."""
    from tests.conftest import beat

    beat(layout, max_concurrent_sessions=7)
    section = replace(config.repos["demo"], max_sessions=4)
    lowered = at_cap(replace(config, repos={**config.repos, "demo": section}), 2)
    built = context_for(lowered, monkeypatch)
    try:
        onboard_repo(built.conn, "demo", repo_clone)
        row = {r["repo_key"]: r for r in json.loads(
            operations.capacity(built).render(as_json=True)
        )["repos"]}["demo"]
        assert row["cap"] == 4, "clamped by the enforced 7, not by this process's 2"
    finally:
        built.close()
