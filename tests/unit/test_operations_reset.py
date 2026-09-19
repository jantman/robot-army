"""``reset`` throws the work away and starts again from the live issue (issue #179).

Before this verb there were three manual steps — a worktree removal, a hand-edit of the
GitHub issue, and a hand-written ``UPDATE`` against ``state.db``. The last of those is a
state change with no record, so the *absence* of this command was a standing invitation to
violate Principle III by the only route available.

The command is composed rather than written: ``_reread_and_refresh`` for the half that
re-reads and re-evaluates, ``worktree_remove`` for the half that discards. Most of what is
worth testing is therefore not "does each half work" — each half has its own suite — but
the three things composition can get wrong, and two of those encode judgements a later
reader would otherwise rediscover by breaking them:

* **the order** (research R1): the checkout is destroyed *after* the issue is known to be
  eligible, never before, so a reset that refuses leaves the work intact —
  :func:`test_an_ineligible_issue_leaves_the_checkout_alone`;
* **what "the removal failed" means** (research R3): ``worktree_remove`` exits non-zero with
  the worktree already gone when git keeps an unmerged branch, which is the *ordinary* case
  for work being thrown away, so reset asks the disk and not the exit code —
  :func:`test_a_branch_git_would_not_delete_still_reaches_ready`;
* **that the guards are reached and not reimplemented** — the open-session refusal, git's
  dirty-tree refusal and the author check are asserted here as passing *through*.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from tests.conftest import make_boundaries, make_issue, seed_item, seed_session

from robot_army import db, operations
from robot_army.boundaries import RemovalResult, TransportError
from robot_army.boundaries.git import SimulatedVersionControl
from robot_army.effects import EffectLevel
from robot_army.operations import (
    EXIT_CHECK_FAILED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_PRECONDITION,
    Context,
)
from robot_army.states import SessionState, WorkItemState

BRANCH = "robot-army/42-fix"
WORKTREE = "/w/demo/issue-42"


# -- harness ----------------------------------------------------------------


class RefusingVcs(SimulatedVersionControl):
    """Simulated git that refuses the removal, as real git does on a dirty tree."""

    def remove_worktree(
        self, worktree_path: str, force: bool = False, clone_path: str | None = None
    ) -> RemovalResult:
        super().remove_worktree(worktree_path, force=force, clone_path=clone_path)
        return RemovalResult(
            worktree_removed=False,
            branch_deleted=False,
            refused_reason="fatal: '...' contains modified or untracked files",
        )


class KeepsTheBranchVcs(SimulatedVersionControl):
    """Removes the worktree and declines the branch, as git does for unmerged work.

    The ordinary case for a reset, not an exotic one: the branch being discarded is by
    definition not merged, so ``git branch -d`` refuses it and only ``--force`` would not.
    """

    def delete_branch(self, clone_path: str, branch: str, force: bool = False) -> bool:
        super().delete_branch(clone_path, branch, force=force)
        return False


@pytest.fixture
def reader():
    from tests.conftest import FakeIssueReader

    return FakeIssueReader([make_issue()])


@pytest.fixture
def ctx(config, conn, audit, monkeypatch, reader):
    """A context whose reader the test controls and whose trust gate passes.

    ``is_trusted`` reads the real ``~/.claude.json``, stubbed for the reason
    ``test_operations_retry`` stubs it: a machine without a trusted clone would refuse
    before the read and prove nothing about what is under test.
    """
    monkeypatch.setattr(
        operations.dispatch,
        "is_trusted",
        lambda path, trust_file=None: (True, "trusted in test"),
    )
    return Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, reader=reader, vcs=SimulatedVersionControl(audit)),
        effect_level=EffectLevel.LIVE,
    )


def resettable(conn, config, *, state: str = "interrupted", worktree: bool = True) -> int:
    """An item in a resettable state, on a repository that passes every local gate."""
    item_id = seed_item(conn, state=state, clone_path=config.repos["demo"].path)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn,
            item_id,
            title="the title as stored",
            body="the body as stored",
            failure_reason="something went wrong",
            blocked_reason="something went wrong",
            **({"worktree_path": WORKTREE, "branch": BRANCH} if worktree else {}),
        )
    return item_id


def records(layout, action: str | None = None) -> list[dict[str, Any]]:
    found = []
    for path in sorted(layout.log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if action is None or record.get("action") == action:
                found.append(record)
    return found


def git_touched(layout) -> list[str]:
    return [
        record["action"]
        for record in records(layout)
        if record["action"] in ("git.remove_worktree", "git.delete_branch")
    ]


def never_asks(_prompt: str) -> str:
    raise AssertionError("nothing here is a question; confirm() must not be called")


def says_yes(_prompt: str) -> str:
    return "y"


# -- the success paths ------------------------------------------------------


@pytest.mark.parametrize("state", ["interrupted", "awaiting_review", "failed"])
def test_a_rested_item_is_discarded_re_read_and_requeued(ctx, conn, config, layout, state):
    item_id = resettable(conn, config, state=state)
    ctx.boundaries.issue_reader.issues = [
        make_issue(title="the title as edited", body="the body as edited")
    ]

    result = operations.reset(ctx, item_id, confirm=says_yes)

    assert result.code == EXIT_OK
    item = db.get_work_item(conn, item_id)
    assert item.state is WorkItemState.READY
    # The four columns come from the read, which is the entire reason this verb exists:
    # `restart` and `resume` would have redispatched the stored copy.
    assert item.title == "the title as edited"
    assert item.body == "the body as edited"
    assert item.author == "jantman"
    assert item.label_list == ["robot-army"]
    # The checkout is forgotten, so dispatch builds a fresh one.
    assert item.worktree_path is None
    assert item.failure_reason is None
    assert item.blocked_reason is None
    assert git_touched(layout) == ["git.remove_worktree", "git.delete_branch"]


def test_the_item_keeps_its_place_in_the_queue(ctx, conn, config):
    """``discovered_at`` is a fact, not a lever (research R6).

    Ordering ranks on it, so rewriting it would move the item's place in line — and would
    do so by falsifying when the issue was found. "As if freshly discovered" in the issue's
    words is about the content and the checkout, not about queue position.
    """
    item_id = resettable(conn, config)
    before = db.get_work_item(conn, item_id).discovered_at

    operations.reset(ctx, item_id, confirm=says_yes)

    assert db.get_work_item(conn, item_id).discovered_at == before


def test_an_item_with_no_checkout_is_still_reset(ctx, conn, config, layout):
    """Nothing to discard is not a failure.

    An item reaches a resettable state with no checkout in three ordinary ways: never
    dispatched, removed by hand, or a previous reset killed between its removal and its
    transition. In all three the rest of the verb is exactly what was asked for.
    """
    item_id = resettable(conn, config, worktree=False)

    result = operations.reset(ctx, item_id, confirm=never_asks)

    assert result.code == EXIT_OK
    assert db.get_work_item(conn, item_id).state is WorkItemState.READY
    assert git_touched(layout) == []
    assert any("no worktree to discard" in line for line in result.lines)


# -- the two decisions ------------------------------------------------------


def test_an_ineligible_issue_leaves_the_checkout_alone(ctx, conn, config, layout):
    """**Research R1.** Destroy last, so a refusal costs nothing but a read.

    The occasion for a reset is an issue that has changed, and a changed issue is exactly
    the one that may now be closed, unlabelled, or written by somebody else. Reordering
    these steps would destroy the work *and* refuse to requeue — strictly worse than the
    hand-work this verb replaces. This test is what fails if someone reorders them.
    """
    item_id = resettable(conn, config)
    ctx.boundaries.issue_reader.issues = [make_issue(author="somebody-else")]

    result = operations.reset(ctx, item_id, confirm=never_asks)

    assert result.code == EXIT_PRECONDITION
    item = db.get_work_item(conn, item_id)
    assert item.state is WorkItemState.INTERRUPTED
    assert item.worktree_path == WORKTREE
    assert git_touched(layout) == []
    # The content *was* refreshed: the read happened, and the queue should describe the
    # issue as it currently is. This is `retry`'s existing, deliberate behaviour.
    assert item.author == "somebody-else"
    assert item.blocked_reason


def test_a_branch_git_would_not_delete_still_reaches_ready(conn, config, audit, layout, monkeypatch, reader):
    """**Research R3.** Ask the disk, not the exit code.

    ``_remove_checkout`` returns a non-zero ``Result`` when the worktree is gone but git
    kept an unmerged branch — which is the *ordinary* outcome for work being thrown away,
    since a discarded branch is by definition unmerged. A reset that stopped on the code
    would abandon itself with the destruction already done, leaving the item neither reset
    nor intact.
    """
    monkeypatch.setattr(
        operations.dispatch, "is_trusted", lambda path, trust_file=None: (True, "trusted")
    )
    ctx = Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, reader=reader, vcs=KeepsTheBranchVcs(audit)),
        effect_level=EffectLevel.LIVE,
    )
    item_id = resettable(conn, config)

    result = operations.reset(ctx, item_id, confirm=says_yes)

    assert db.get_work_item(conn, item_id).state is WorkItemState.READY
    assert db.get_work_item(conn, item_id).worktree_path is None
    # Reported, not swallowed: the surviving branch is the other half of the only record of
    # that work, and silence about it is what Principle III forbids.
    assert any("still exists" in line for line in result.lines)
    assert result.data["worktree_removed"] is True
    assert result.data["branch_deleted"] is False


# -- refusals ---------------------------------------------------------------


def test_no_such_item(ctx):
    result = operations.reset(ctx, 9999, confirm=never_asks)
    assert result.code == EXIT_FAILED
    assert "no work item with id 9999" in result.lines[0]


@pytest.mark.parametrize(
    "state", ["discovered", "ready", "dispatching", "active", "done", "abandoned"]
)
def test_a_state_reset_does_not_accept(ctx, conn, config, layout, state):
    item_id = resettable(conn, config, state=state)

    result = operations.reset(ctx, item_id, confirm=never_asks)

    assert result.code == EXIT_PRECONDITION
    assert db.get_work_item(conn, item_id).state is WorkItemState(state)
    assert git_touched(layout) == []
    assert result.data["state"] == state


def test_an_active_item_is_told_to_cancel_first(ctx, conn, config):
    """The refusal names the remedy, which the state machine's own message never would."""
    item_id = resettable(conn, config, state="active")
    result = operations.reset(ctx, item_id, confirm=never_asks)
    assert f"robot-army cancel {item_id}" in result.lines[0]


@pytest.mark.parametrize("state", ["done", "abandoned"])
def test_a_terminal_item_is_told_it_is_finished(ctx, conn, config, state):
    item_id = resettable(conn, config, state=state)
    result = operations.reset(ctx, item_id, confirm=never_asks)
    assert "does not revive a terminal item" in result.lines[0]


def test_an_unreachable_issue_refuses_and_says_so(ctx, conn, config, layout):
    item_id = resettable(conn, config)
    ctx.boundaries.issue_reader.raise_on_get_issue = TransportError("connection reset")

    result = operations.reset(ctx, item_id, confirm=never_asks)

    assert result.code == EXIT_FAILED
    assert result.data["cause"] == "issue_unreachable"
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    # Never a fallback to the stored copy: the stored copy is what cannot be trusted here,
    # and falling back would be the original defect with a network hiccup as its trigger.
    assert db.get_work_item(conn, item_id).body == "the body as stored"
    assert git_touched(layout) == []


def test_an_absent_issue_refuses_differently(ctx, conn, config, layout):
    """"It does not exist" and "I could not ask" are different facts (Principle III)."""
    item_id = resettable(conn, config)
    ctx.boundaries.issue_reader.issues = []

    result = operations.reset(ctx, item_id, confirm=never_asks)

    assert result.code == EXIT_FAILED
    assert result.data["cause"] == "issue_absent"
    assert git_touched(layout) == []


def test_an_unresolved_repository_refuses_before_the_read(ctx, conn, config, layout):
    item_id = resettable(conn, config)
    with db.transaction(conn):
        conn.execute("UPDATE repos SET clone_path = NULL WHERE repo_key = 'demo'")

    result = operations.reset(ctx, item_id, confirm=never_asks)

    assert result.code == EXIT_PRECONDITION
    assert ctx.boundaries.issue_reader.get_issue_calls == []
    assert records(layout, "reset.blocked")
    assert git_touched(layout) == []


def test_a_blocking_gate_refuses_before_the_read(ctx, conn, config, layout, monkeypatch):
    monkeypatch.setattr(
        operations.dispatch,
        "is_trusted",
        lambda path, trust_file=None: (False, "the clone is not a trusted directory"),
    )
    item_id = resettable(conn, config)

    result = operations.reset(ctx, item_id, confirm=never_asks)

    assert result.code == EXIT_PRECONDITION
    assert ctx.boundaries.issue_reader.get_issue_calls == []
    assert git_touched(layout) == []


def test_the_author_check_is_on_the_path(ctx, conn, config, layout):
    """RA-01's whole point, and the reason the read path is shared and not copied.

    ``check_gates`` takes a ``RepoConfig`` and cannot see an issue, so it never checks who
    wrote it. A second verb with a read of its own would have been the second way round the
    one control that stops "anyone may open an issue on a public repository" becoming
    "anyone may run an agent in the maintainer's checkout".
    """
    item_id = resettable(conn, config)
    ctx.boundaries.issue_reader.issues = [make_issue(author="a-stranger")]

    result = operations.reset(ctx, item_id, confirm=never_asks)

    assert result.code == EXIT_PRECONDITION
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    [record] = records(layout, "reset.evaluate")
    assert record["detail"]["eligible"] is False
    assert record["detail"]["author"] == "a-stranger"


# -- the guards, reached rather than reimplemented ---------------------------


def test_an_open_session_refuses_and_names_cancel(ctx, conn, config, layout):
    item_id = resettable(conn, config)
    seed_session(conn, item_id, state=str(SessionState.RUNNING))

    result = operations.reset(ctx, item_id, confirm=says_yes)

    assert result.code != EXIT_OK
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    assert db.get_work_item(conn, item_id).worktree_path == WORKTREE
    assert git_touched(layout) == []
    assert any(f"robot-army cancel {item_id}" in line for line in result.lines)


def test_git_refusing_over_uncommitted_work_stops_the_reset(
    conn, config, audit, layout, monkeypatch, reader
):
    monkeypatch.setattr(
        operations.dispatch, "is_trusted", lambda path, trust_file=None: (True, "trusted")
    )
    ctx = Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, reader=reader, vcs=RefusingVcs(audit)),
        effect_level=EffectLevel.LIVE,
    )
    item_id = resettable(conn, config)

    result = operations.reset(ctx, item_id, confirm=says_yes)

    assert result.code != EXIT_OK
    item = db.get_work_item(conn, item_id)
    assert item.state is WorkItemState.INTERRUPTED
    assert item.worktree_path == WORKTREE
    assert any("--force overrides it" in line for line in result.lines)
    assert "git.delete_branch" not in git_touched(layout)


# -- the confirmation -------------------------------------------------------


def test_declining_the_confirmation_destroys_nothing(ctx, conn, config, layout):
    item_id = resettable(conn, config)

    result = operations.reset(ctx, item_id, confirm=lambda _prompt: "n")

    assert result.code == EXIT_FAILED
    assert result.lines[-1] == "aborted"
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    assert git_touched(layout) == []


def test_the_confirmation_names_what_it_will_destroy(ctx, conn, config):
    asked: list[str] = []

    def record_and_decline(prompt: str) -> str:
        asked.append(prompt)
        return "n"

    operations.reset(ctx, resettable(conn, config), confirm=record_and_decline)

    assert WORKTREE in asked[0]
    assert BRANCH in asked[0]


def test_an_abandoned_confirmation_is_recorded_not_raised(ctx, conn, config, layout):
    """Issue #23's rule: giving up on a prompt is a result, never a traceback."""
    def ends(_prompt: str) -> str:
        raise EOFError

    item_id = resettable(conn, config)
    result = operations.reset(ctx, item_id, confirm=ends)

    assert result.code == EXIT_CHECK_FAILED
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    assert git_touched(layout) == []
    assert [r for r in records(layout, "reset") if r["kind"] == "outcome"]


def test_force_asks_the_typed_id_and_a_wrong_answer_aborts(ctx, conn, config, layout):
    """With ``--force`` the harder question is the only question (research R4)."""
    asked: list[str] = []

    def wrong_id(prompt: str) -> str:
        asked.append(prompt)
        return "not the id"

    item_id = resettable(conn, config)
    result = operations.reset(ctx, item_id, force=True, confirm=wrong_id)

    assert result.code == EXIT_FAILED
    assert len(asked) == 1, "one question, not two — a question asked twice is answered reflexively"
    assert f"Type the item id ({item_id})" in asked[0]
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    assert git_touched(layout) == []


def test_assume_yes_asks_nothing_and_still_cannot_override_git(
    conn, config, audit, monkeypatch, reader
):
    """What the web passes. It suppresses the question and nothing else (FR-019)."""
    monkeypatch.setattr(
        operations.dispatch, "is_trusted", lambda path, trust_file=None: (True, "trusted")
    )
    ctx = Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, reader=reader, vcs=RefusingVcs(audit)),
        effect_level=EffectLevel.LIVE,
    )
    item_id = resettable(conn, config)

    result = operations.reset(ctx, item_id, assume_yes=True, confirm=never_asks)

    assert result.code != EXIT_OK
    assert db.get_work_item(conn, item_id).worktree_path == WORKTREE


def test_assume_yes_succeeds_without_asking_when_git_does_not_object(ctx, conn, config):
    item_id = resettable(conn, config)
    result = operations.reset(ctx, item_id, assume_yes=True, confirm=never_asks)
    assert result.code == EXIT_OK
    assert db.get_work_item(conn, item_id).state is WorkItemState.READY


# -- interruption (Principle IV) --------------------------------------------


def test_a_reset_killed_after_the_removal_is_completed_by_a_second(ctx, conn, config, layout):
    """The worktree is gone and the path cleared, but the state never moved.

    Reproduced as the state such a kill leaves rather than by killing anything: the removal
    clears ``worktree_path`` in its own transaction and the transition happens in another,
    so this is exactly what is on disk between them.
    """
    item_id = resettable(conn, config, worktree=False)

    result = operations.reset(ctx, item_id, confirm=never_asks)

    assert result.code == EXIT_OK
    assert db.get_work_item(conn, item_id).state is WorkItemState.READY


def test_a_reset_killed_after_the_refresh_is_completed_by_a_second(ctx, conn, config):
    """Refreshed content, old state: the next attempt corrects it completely."""
    item_id = resettable(conn, config)
    ctx.boundaries.issue_reader.raise_on_get_issue = TransportError("gone")
    first = operations.reset(ctx, item_id, confirm=never_asks)
    assert first.code == EXIT_FAILED

    ctx.boundaries.issue_reader.raise_on_get_issue = None
    ctx.boundaries.issue_reader.issues = [make_issue(body="the body as edited")]
    second = operations.reset(ctx, item_id, confirm=says_yes)

    assert second.code == EXIT_OK
    item = db.get_work_item(conn, item_id)
    assert item.state is WorkItemState.READY
    assert item.body == "the body as edited"


# -- the record (Principle III) ---------------------------------------------


def test_the_whole_reset_reconstructs_from_the_log(ctx, conn, config, layout):
    item_id = resettable(conn, config)

    operations.reset(ctx, item_id, confirm=says_yes)

    actions = [r["action"] for r in records(layout)]
    assert actions.index("reset") < actions.index("git.remove_worktree"), (
        "the intent must be flushed before anything is destroyed"
    )
    for action in ("reset", "reset.evaluate", "worktree.remove", "state.work_item"):
        assert action in actions, action
    [outcome] = [r for r in records(layout, "reset") if r["kind"] == "outcome"]
    assert outcome["detail"]["worktree_removed"] is True
    assert outcome["detail"]["branch_deleted"] is True
    assert outcome["detail"]["requeued"] is True
    [transition] = records(layout, "state.work_item")
    assert transition["detail"]["from"] == "interrupted"
    assert transition["detail"]["to"] == "ready"


def test_a_refusal_records_what_refused_it(ctx, conn, config, layout):
    item_id = resettable(conn, config)
    ctx.boundaries.issue_reader.issues = [make_issue(author="a-stranger")]

    operations.reset(ctx, item_id, confirm=never_asks)

    [outcome] = [r for r in records(layout, "reset") if r["kind"] == "outcome"]
    assert outcome["detail"]["refused_by"] == "not_requeueable"
    assert outcome["detail"]["requeued"] is False


def test_the_audit_names_the_verb_that_asked(ctx, conn, config, layout):
    """``reset.evaluate``, not ``retry.evaluate``, from the shared helper."""
    operations.reset(ctx, resettable(conn, config), confirm=says_yes)
    assert records(layout, "reset.evaluate")
    assert not records(layout, "retry.evaluate")


# -- simulation -------------------------------------------------------------


def test_below_the_live_level_it_says_what_it_would_do(conn, config, audit, monkeypatch, reader):
    monkeypatch.setattr(
        operations.dispatch, "is_trusted", lambda path, trust_file=None: (True, "trusted")
    )
    ctx = Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, reader=reader, vcs=SimulatedVersionControl(audit)),
        effect_level=EffectLevel.PLAN,
    )
    item_id = resettable(conn, config)

    result = operations.reset(ctx, item_id, confirm=says_yes)

    assert result.code == EXIT_OK
    assert any("would remove" in line for line in result.lines)
    assert any("simulated" in line for line in result.lines)


# -- the race the wide window makes possible --------------------------------


def test_a_concurrent_move_is_reported_rather_than_tracebacked(ctx, conn, config, monkeypatch):
    """Something else abandons the item while the reset is between its steps.

    Reset's window is the wide one — a network read, a worktree removal, and possibly a
    prompt, all between the state check and the transition — so this is the verb where a
    concurrent terminal command is actually reachable. The state machine stays the arbiter;
    what is asserted here is that the destruction which already happened is *reported*,
    rather than buried under a traceback where nothing says the checkout is gone.
    """
    item_id = resettable(conn, config)
    real_remove = operations.worktree_remove

    def remove_then_abandon(*args: Any, **kwargs: Any):
        result = real_remove(*args, **kwargs)
        with db.transaction(conn):
            conn.execute("UPDATE work_items SET state = 'abandoned' WHERE id = ?", (item_id,))
        return result

    monkeypatch.setattr(operations, "worktree_remove", remove_then_abandon)

    result = operations.reset(ctx, item_id, confirm=says_yes)

    assert result.code == EXIT_PRECONDITION
    assert db.get_work_item(conn, item_id).state is WorkItemState.ABANDONED
    assert any("moved to another state" in line for line in result.lines)
    assert any("already discarded" in line for line in result.lines)
