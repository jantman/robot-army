"""The cost split behind the four resume signals (T030, research.md R9).

This was the one design problem Phase 1 surfaced. An interrupted view with five items,
auto-refreshing every 10 seconds, would make 1,800 GitHub calls an hour asking a question
that cannot change as a result of anything happening on this machine — competing directly
with the polling budget FR-008 exists to protect.

The split follows the semantics, not just the cost: the local signals are volatile
*precisely because* the maintainer may be in the worktree with an editor open.
"""

from __future__ import annotations

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import db, operations
from robot_army.boundaries import PullRequest, TransportError


@pytest.fixture
def ctx(config, conn, audit, monkeypatch):
    from tests.conftest import FakeIssueReader

    # ``Context`` is slotted, so the reader is reached through the wired boundaries rather
    # than stashed on it — which is also how production code reaches it.
    reader = FakeIssueReader()
    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log: make_boundaries(log, level=level, reader=reader),
    )
    operations.clear_resume_signal_cache()
    built = operations.build_context(config)
    yield built
    built.close()
    operations.clear_resume_signal_cache()


def _item(conn, **kwargs):
    item_id = seed_item(conn, state="interrupted", **kwargs)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item_id, worktree_path="/nowhere", branch="robot-army/42"
        )
    return db.get_work_item(conn, item_id)


def test_the_local_signals_are_recomputed_on_every_call(ctx, conn, monkeypatch):
    """A stored copy would be wrong the moment the maintainer touched the directory."""
    item = _item(conn)
    calls: list[str] = []
    real = operations.worktree.condition

    def counting(*args, **kwargs):
        calls.append("condition")
        return real(*args, **kwargs)

    monkeypatch.setattr(operations.worktree, "condition", counting)
    for _ in range(3):
        operations.local_resume_signals(ctx, item)
    assert len(calls) == 3, "the local signals must never be cached"


def test_the_remote_signals_are_served_from_cache_inside_the_window(ctx, conn):
    item = _item(conn)
    ctx.boundaries.issue_reader.closed[("demo", 42)] = True

    first = operations.remote_resume_signals(ctx, item)
    assert first["issue_closed"] is True
    assert first["signals_age_seconds"] == 0
    assert len(ctx.boundaries.issue_reader.closed_calls) == 1

    second = operations.remote_resume_signals(ctx, item)
    assert second["issue_closed"] is True
    assert len(ctx.boundaries.issue_reader.closed_calls) == 1, "a second render must not re-ask GitHub"


def test_a_cached_remote_signal_carries_its_age(ctx, conn, monkeypatch):
    """Each rendered value carries its age, so a stale one is visible rather than implied."""
    item = _item(conn)
    clock = {"now": 1000.0}
    monkeypatch.setattr(operations, "_monotonic", lambda: clock["now"])

    fresh = operations.remote_resume_signals(ctx, item)
    assert fresh["signals_age_seconds"] == 0

    clock["now"] = 1030.0
    cached = operations.remote_resume_signals(ctx, item)
    assert cached["signals_age_seconds"] == 30
    assert len(ctx.boundaries.issue_reader.closed_calls) == 1


def test_the_remote_signals_are_refetched_after_the_window(ctx, conn, monkeypatch):
    item = _item(conn)
    clock = {"now": 1000.0}
    monkeypatch.setattr(operations, "_monotonic", lambda: clock["now"])

    operations.remote_resume_signals(ctx, item)
    clock["now"] = 1000.0 + operations.REMOTE_SIGNAL_TTL_SECONDS + 1
    operations.remote_resume_signals(ctx, item)
    assert len(ctx.boundaries.issue_reader.closed_calls) == 2


def test_the_cache_is_keyed_by_item_and_branch(ctx, conn):
    """A branch change is a different question, and answering the old one would be wrong."""
    first = _item(conn)
    operations.remote_resume_signals(ctx, first)
    with db.transaction(conn):
        db.update_work_item_columns(conn, first.id, branch="robot-army/42-take-two")
    changed = db.get_work_item(conn, first.id)
    operations.remote_resume_signals(ctx, changed)
    assert len(ctx.boundaries.issue_reader.closed_calls) == 2


def test_a_transport_failure_is_reported_and_not_cached(ctx, conn):
    """"I could not ask" is not an answer, and caching it would suppress the next attempt
    for a minute — the silent failure Principle III forbids."""
    item = _item(conn)
    ctx.boundaries.issue_reader.raise_on_remote = TransportError("GitHub is unreachable")

    first = operations.remote_resume_signals(ctx, item)
    assert first["issue_closed"] is None
    assert "GitHub is unreachable" in first["github_error"]

    ctx.boundaries.issue_reader.raise_on_remote = None
    ctx.boundaries.issue_reader.closed[("demo", 42)] = True
    second = operations.remote_resume_signals(ctx, item)
    assert second["issue_closed"] is True, "a failed lookup must not poison the cache"


def test_a_simulated_row_never_reaches_github(ctx, conn):
    """FR-055: a simulated row must cause no outward-facing effect, and asking GitHub
    about it would be exactly that."""
    item = _item(conn, issue_number=99, dry_run=True)
    signals = operations.remote_resume_signals(ctx, item)
    assert signals["issue_closed"] is None
    assert ctx.boundaries.issue_reader.closed_calls == []


def test_an_open_pull_request_is_reported_by_url(ctx, conn):
    item = _item(conn)
    ctx.boundaries.issue_reader.open_prs[("demo", "robot-army/42")] = PullRequest(
        number=7, url="https://github.com/x/demo/pull/7", state="open"
    )
    signals = operations.resume_signals(ctx, item)
    assert signals["open_pull_request"] == "https://github.com/x/demo/pull/7"


def test_the_merged_view_still_returns_all_four_signals(ctx, conn):
    """``show`` and the item page both call this; splitting must not have narrowed it."""
    item = _item(conn)
    signals = operations.resume_signals(ctx, item)
    for key in (
        "worktree_present",
        "uncommitted_changes",
        "commits_on_branch",
        "issue_closed",
        "open_pull_request",
    ):
        assert key in signals, key


def test_a_cached_value_renders_with_a_visible_age(web, conn, monkeypatch):
    """The age has to reach the page, not merely the payload."""
    clock = {"now": 5000.0}
    monkeypatch.setattr(operations, "_monotonic", lambda: clock["now"])
    item_id = seed_item(conn, state="interrupted")
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item_id, worktree_path="/nowhere", branch="robot-army/42"
        )

    assert "computed just now" in web.get("/interrupted").text

    clock["now"] = 5040.0
    later = web.get("/interrupted").text
    assert "40s old (cached)" in later
