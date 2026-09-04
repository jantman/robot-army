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


@pytest.fixture
def count_conditions(monkeypatch):
    calls: list[str] = []
    real = operations.worktree.condition

    def counting(*args, **kwargs):
        calls.append("condition")
        return real(*args, **kwargs)

    monkeypatch.setattr(operations.worktree, "condition", counting)
    return calls


def test_the_local_signals_are_reused_inside_the_window(ctx, conn, count_conditions):
    """This assertion used to read "must never be cached", and it was right until it wasn't.

    The reasoning behind it survives intact: a stored copy would be wrong the moment the
    maintainer touched the directory, so the value must not outlive their next look at the
    page. What changed is that ``/interrupted`` renders one card per interrupted and
    awaiting-review item, each card costs several ``git`` subprocesses, and until RA-14 a
    cross-site page could ask for that page in a loop. Five seconds — deliberately below the
    ten-second page refresh — keeps the freshness and removes the burst.
    """
    item = _item(conn)
    for _ in range(3):
        operations.local_resume_signals(ctx, item)
    assert len(count_conditions) == 1, "three calls inside the window cost one observation"


def test_the_local_signals_are_observed_again_after_the_window(
    ctx, conn, count_conditions, monkeypatch
):
    """Five seconds is below the default ``[web] refresh_seconds``, so a page left open
    observes the checkout afresh on every refresh — which is the freshness the recompute-on-
    every-call rule existed to give."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(operations, "_monotonic", lambda: clock["now"])
    item = _item(conn)

    operations.local_resume_signals(ctx, item)
    clock["now"] = 1000.0 + operations.LOCAL_SIGNAL_TTL_SECONDS + 0.1
    operations.local_resume_signals(ctx, item)
    assert len(count_conditions) == 2


@pytest.mark.parametrize("column", ["branch", "worktree_path"])
def test_the_local_cache_is_keyed_on_what_it_observed(ctx, conn, count_conditions, column):
    """A worktree reclaimed or a branch renamed is a different question. Answering the old
    one would attribute an observation to somewhere it was never made."""
    item = _item(conn)
    operations.local_resume_signals(ctx, item)

    with db.transaction(conn):
        db.update_work_item_columns(conn, item.id, **{column: "/somewhere-else-entirely"})
    changed = db.get_work_item(conn, item.id)
    operations.local_resume_signals(ctx, changed)

    assert len(count_conditions) == 2


def test_the_local_cache_is_keyed_on_the_base_ref_too(ctx, conn, count_conditions, monkeypatch):
    """``commits_on_branch`` is counted against the base branch, so a reconfigured base is a
    different answer even though nothing about the item moved.

    ``Config`` is a frozen dataclass, so the base ref is redirected at the type rather than
    at the instance — which is also closer to what an edited config file does.
    """
    item = _item(conn)
    operations.local_resume_signals(ctx, item)

    monkeypatch.setattr(type(ctx.config), "base_branch_for", lambda self, repo_key: "release")
    operations.local_resume_signals(ctx, item)

    assert len(count_conditions) == 2


def test_a_failed_local_observation_is_not_cached(ctx, conn, monkeypatch):
    """Caching "I could not look" would suppress the next attempt for five seconds and hide
    the recovery — the silent failure Principle III forbids, and the rule the remote half
    already applies to a TransportError."""
    from robot_army.boundaries import BoundaryError

    item = _item(conn)
    failures = {"left": 1}

    real = operations.worktree.condition

    def sometimes(*args, **kwargs):
        if failures["left"]:
            failures["left"] -= 1
            raise BoundaryError("the checkout is unreadable")
        return real(*args, **kwargs)

    monkeypatch.setattr(operations.worktree, "condition", sometimes)

    first = operations.local_resume_signals(ctx, item)
    assert "the checkout is unreadable" in first["worktree_error"]

    second = operations.local_resume_signals(ctx, item)
    assert "worktree_error" not in second, "a failed lookup must not poison the cache"


def test_a_reused_local_signal_carries_its_age(ctx, conn, monkeypatch):
    """A reused value must be visible as reused rather than implied to be current — the same
    guarantee the GitHub-derived pair already gives."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(operations, "_monotonic", lambda: clock["now"])
    item = _item(conn)

    assert operations.local_resume_signals(ctx, item)["local_signals_age_seconds"] == 0
    clock["now"] = 1003.0
    assert operations.local_resume_signals(ctx, item)["local_signals_age_seconds"] == 3


def test_the_two_ages_do_not_overwrite_each_other(ctx, conn, monkeypatch):
    """Two separately-aged halves get two numbers. One number would have to misreport one of
    them, and the two TTLs differ by more than an order of magnitude."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(operations, "_monotonic", lambda: clock["now"])
    item = _item(conn)

    operations.resume_signals(ctx, item)
    clock["now"] = 1003.0
    both = operations.resume_signals(ctx, item)

    assert both["local_signals_age_seconds"] == 3
    assert both["signals_age_seconds"] == 3
    assert set(both) >= {
        "worktree_present",
        "uncommitted_changes",
        "commits_on_branch",
        "issue_closed",
        "open_pull_request",
    }


def test_acting_on_an_item_forgets_both_halves_of_its_signals(ctx, conn, count_conditions):
    """FR-010. An action can change either half — abandoning can close the issue, resuming
    can dirty the worktree — so forgetting one and keeping the other would be arbitrary."""
    item = _item(conn)
    ctx.boundaries.issue_reader.closed[("demo", 42)] = True
    operations.resume_signals(ctx, item)
    assert len(count_conditions) == 1
    assert len(ctx.boundaries.issue_reader.closed_calls) == 1

    operations.forget_resume_signals(item.id)

    operations.resume_signals(ctx, item)
    assert len(count_conditions) == 2
    assert len(ctx.boundaries.issue_reader.closed_calls) == 2


def test_forgetting_one_item_leaves_another_alone(ctx, conn, count_conditions):
    first = _item(conn, issue_number=1)
    second = _item(conn, issue_number=2)
    operations.local_resume_signals(ctx, first)
    operations.local_resume_signals(ctx, second)
    assert len(count_conditions) == 2

    operations.forget_resume_signals(first.id)

    operations.local_resume_signals(ctx, second)
    assert len(count_conditions) == 2, "the other item's observation still stands"
    operations.local_resume_signals(ctx, first)
    assert len(count_conditions) == 3


def test_expired_entries_do_not_accumulate(ctx, conn, monkeypatch):
    """FR-012. The key includes the worktree path and the branch, so a long-running process
    that watched items come and go could otherwise grow one entry per key ever seen."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(operations, "_monotonic", lambda: clock["now"])
    item = _item(conn)

    for step in range(20):
        clock["now"] = 1000.0 + step * (operations.LOCAL_SIGNAL_TTL_SECONDS + 0.1)
        with db.transaction(conn):
            db.update_work_item_columns(conn, item.id, branch=f"robot-army/42-take-{step}")
        operations.local_resume_signals(ctx, db.get_work_item(conn, item.id))

    assert len(operations._LOCAL_SIGNAL_CACHE) == 1, "expired entries are purged on insert"


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
