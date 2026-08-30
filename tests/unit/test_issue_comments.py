"""What robot-army writes onto an issue, and how it works out what came before.

Issue #38: an issue that a session worked on has to say *which machine* it worked on it
from. The comment already named the branch, the worktree and the session id; on a second
machine none of those is an address.

Everything here is a rule about a string, so it is tested as one — no worktree, no git
binary, no stub session host. The wiring that carries these strings to GitHub is exercised
in ``tests/integration/test_dispatch.py``; what is pinned here is the wording, and the
lookup the reassignment wording depends on.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from tests.conftest import seed_item, seed_session

from robot_army import db, dispatch

# -- the host line ----------------------------------------------------------


class _FakeUname:
    def __init__(self, nodename: str) -> None:
        self.nodename = nodename


def test_the_host_is_the_kernels_answer(monkeypatch: Any) -> None:
    monkeypatch.setattr(os, "uname", lambda: _FakeUname("orion"))
    assert dispatch.host_name() == "orion"


def test_an_empty_nodename_becomes_unknown(monkeypatch: Any) -> None:
    """Not an empty string, and not an absent line.

    Both of those read as "there is nothing to say about the host", when what is true is
    "this machine would not say". The comment exists to answer that question, so it answers
    it even when the answer is that there is no answer.
    """
    monkeypatch.setattr(os, "uname", lambda: _FakeUname(""))
    assert dispatch.host_name() == "unknown"


def test_a_uname_that_fails_becomes_unknown(monkeypatch: Any) -> None:
    def boom() -> _FakeUname:
        raise OSError("no")

    monkeypatch.setattr(os, "uname", boom)
    assert dispatch.host_name() == "unknown"


# -- the dispatch comment ---------------------------------------------------


def body(**overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "host": "orion",
        "session_name": "ra-demo-42",
        "session_id": "11111111-1111-4111-8111-111111111111",
        "branch": "robot-army/issue-42-fix-the-thing",
        "worktree_path": "/home/someone/worktrees/demo/issue-42",
    }
    kwargs.update(overrides)
    return dispatch.dispatch_comment_body(**kwargs)


def test_a_first_dispatch_names_the_machine_and_both_session_handles() -> None:
    """The whole of issue #38's request, in one assertion per fact.

    The session *name* and the session *id* are both published because they are the two
    different handles the maintainer searches with: the name appears in the tab title and
    the resume picker, the id names the transcript and the log records.
    """
    text = body()
    assert "- Host: `orion`" in text
    assert "- Session: `ra-demo-42`" in text
    assert "- Session id: `11111111-1111-4111-8111-111111111111`" in text
    assert "- Branch: `robot-army/issue-42-fix-the-thing`" in text
    assert "- Worktree: `/home/someone/worktrees/demo/issue-42`" in text
    assert text.startswith("🤖 robot-army dispatched a session for this issue.")


def test_a_first_dispatch_says_nothing_about_attempts_or_predecessors() -> None:
    text = body()
    assert "attempt" not in text
    assert "Supersedes" not in text
    assert "Continues" not in text


def test_an_unknown_host_still_renders_a_host_line() -> None:
    text = body(host="unknown")
    assert "- Host: `unknown`" in text
    assert "- Host: ``" not in text


def test_the_branch_is_present_because_it_is_the_link_to_the_pull_request() -> None:
    """The PR correlation the issue asks for is the branch, and nothing else.

    A pull request for this work is opened from this branch, so naming it is what ties the
    issue, the session and the eventual PR together. If this line ever disappears, that
    chain breaks silently.
    """
    assert "- Branch: `robot-army/issue-42-fix-the-thing`" in body()


# -- the reassignment variant -----------------------------------------------


def test_a_reassignment_says_so_and_numbers_the_attempt() -> None:
    text = body(attempt=2, previous_session_id="prev-id", resumed=True)
    assert text.startswith("🤖 robot-army reassigned this issue to a new session (attempt 2).")


def test_a_resume_names_the_session_whose_context_it_restored() -> None:
    text = body(attempt=2, previous_session_id="prev-id", resumed=True)
    assert "- Continues: `prev-id` (that session's context was restored)" in text
    assert "Supersedes" not in text


def test_a_restart_names_what_it_replaced_and_says_it_kept_nothing() -> None:
    """The distinction is not decoration.

    A resumed session carries the prior conversation; a restarted one does not. That is the
    difference between reading the earlier transcript for context and reading it for facts
    that no longer apply.
    """
    text = body(attempt=3, previous_session_id="prev-id", resumed=False)
    assert "- Supersedes: `prev-id` (this session starts without that session's context)" in text
    assert "Continues" not in text


def test_no_predecessor_is_said_plainly_rather_than_invented() -> None:
    text = body(attempt=2, previous_session_id=None)
    assert "- Supersedes: no earlier session is on record" in text


def test_a_restored_session_wins_over_a_looked_up_one() -> None:
    """Both facts can be present; only one of them is the interesting one.

    ``resumed`` means the id came from the resume itself — what the new session actually
    restored — which is a stronger statement than "this is the row before ours".
    """
    text = body(attempt=2, previous_session_id="restored-id", resumed=True)
    assert "- Continues: `restored-id` (that session's context was restored)" in text


def test_every_fact_is_its_own_labelled_line() -> None:
    """The shape matters: this renders on GitHub, at whatever width the reader has."""
    lines = [line for line in body(attempt=2, previous_session_id="p").splitlines() if line]
    assert lines[0].startswith("🤖")
    assert all(line.startswith("- ") for line in lines[1:])
    assert len(lines[1:]) == 6


# -- the failure comment ----------------------------------------------------


def test_a_failure_comment_names_the_host_and_fences_the_reason() -> None:
    """A failure that happens on one machine and not another is attributable by this line."""
    text = dispatch.failure_comment_body(host="orion", reason="kitty: no such window")
    assert text.startswith("🤖 robot-army could not start a session for this issue.")
    assert "- Host: `orion`" in text
    assert "```\nkitty: no such window\n```" in text


def test_a_failure_comment_never_claims_a_session() -> None:
    text = dispatch.failure_comment_body(host="orion", reason="whatever")
    assert "dispatched" not in text
    assert "reassigned" not in text


# -- the lookup the reassignment wording depends on -------------------------


@pytest.fixture
def item_with_three_attempts(conn: Any) -> int:
    item_id = seed_item(conn)
    for attempt in (1, 2, 3):
        seed_session(conn, item_id, state="exited_clean", session_id=f"sess-{attempt}")
    return item_id


def test_the_previous_session_is_the_one_before_this_attempt(
    conn: Any, item_with_three_attempts: int
) -> None:
    found = db.previous_session_for_item(conn, item_with_three_attempts, 3)
    assert found is not None
    assert found.session_id == "sess-2"


def test_the_previous_session_is_never_this_attempts_own_row(
    conn: Any, item_with_three_attempts: int
) -> None:
    """The bug this function exists to prevent.

    A session row is inserted *before* its process is launched, so by the time anything
    asks what came earlier, this attempt's own row is already the latest one.
    ``latest_session_for_item`` would hand it back and the comment would say a session
    supersedes itself — which is exactly the kind of confident, wrong sentence this
    feature exists to remove from these issues.
    """
    latest = db.latest_session_for_item(conn, item_with_three_attempts)
    assert latest is not None and latest.session_id == "sess-3"

    found = db.previous_session_for_item(conn, item_with_three_attempts, 3)
    assert found is not None and found.session_id != latest.session_id


def test_a_first_attempt_has_no_predecessor(conn: Any) -> None:
    item_id = seed_item(conn)
    seed_session(conn, item_id, session_id="sess-only")
    assert db.previous_session_for_item(conn, item_id, 1) is None


def test_a_gap_in_the_attempts_is_answered_rather_than_failed(conn: Any) -> None:
    """A rebuilt database, or pruned history, leaves an attempt whose predecessor is gone."""
    item_id = seed_item(conn)
    seed_session(conn, item_id, session_id="sess-1")
    conn.execute("DELETE FROM sessions WHERE session_id = 'sess-1'")
    assert db.previous_session_for_item(conn, item_id, 2) is None
