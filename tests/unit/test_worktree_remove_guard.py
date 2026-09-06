"""``worktree remove`` refuses while a session for the item is open (issue #79).

The command's only guards were git's own — it refuses a dirty or *merely untracked* tree —
and that refusal is deliberately the design. But a **read-only session leaves the tree
clean**, so git has no objection, and on 2026-08-31 the worktree of a still-running worker
was removed along with its branch. The worker carried on with its working directory
reported as ``(deleted)``.

Every "nothing was removed" assertion here is written as **no ``git.remove_worktree`` and
no ``git.delete_branch`` record exists in the audit log**, via ``SimulatedVersionControl``,
which records every intended git operation and touches no disk. A surviving directory is
the weaker claim: it is also what you get when removal was attempted and merely failed.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from tests.conftest import make_boundaries, seed_item, seed_session

from robot_army import db, operations, procinfo
from robot_army.audit import read_records
from robot_army.boundaries import RemovalResult
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


class RefusingVcs(SimulatedVersionControl):
    """Simulated git that refuses the removal, as real git does on a dirty tree.

    Subclassed rather than hand-rolled so the refusal path is still audited exactly as
    every other simulated git call is — the tests below read those records.
    """

    def remove_worktree(
        self, worktree_path: str, force: bool = False, clone_path: str | None = None
    ) -> RemovalResult:
        super().remove_worktree(worktree_path, force=force, clone_path=clone_path)
        return RemovalResult(
            worktree_removed=False,
            branch_deleted=False,
            refused_reason="fatal: '...' contains modified or untracked files",
        )


def make_context(conn, audit, config, *, vcs: Any = None) -> Context:
    return Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, vcs=vcs or SimulatedVersionControl(audit)),
        effect_level=EffectLevel.LIVE,
    )


def item_with_worktree(conn, *, state=WorkItemState.DONE, issue_number: int = 42) -> int:
    item_id = seed_item(conn, state=str(state), issue_number=issue_number)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn,
            item_id,
            worktree_path=f"/w/demo/issue-{issue_number}",
            branch=BRANCH,
        )
    return item_id


def records(layout, action: str | None = None) -> list[dict[str, Any]]:
    """Every record on disk. ``AuditLog`` flushes per line, so nothing is buffered."""
    return [
        record
        for record, _ in read_records(layout.log_dir)
        if record is not None and (action is None or record["action"] == action)
    ]


def git_touched(layout) -> list[str]:
    """Every git operation the command actually reached, in order."""
    return [
        record["action"]
        for record in records(layout)
        if record["action"] in ("git.remove_worktree", "git.delete_branch")
    ]


def never_asks(_prompt: str) -> str:
    raise AssertionError("the refusal is not a question; confirm() must not be called")


# -- the action record, which did not exist before #79 (T005) ---------------


def test_a_successful_removal_is_an_intent_outcome_pair(conn, audit, config, layout):
    """``worktree_remove`` wrote no record under its own name at all before this.

    The only records the command produced came from the git boundary, which names a *path*
    and never the work item — so "what happened when I removed item 21's worktree" could
    not be answered without mapping a path back to an item by hand.
    """
    item_id = item_with_worktree(conn)

    result = operations.worktree_remove(make_context(conn, audit, config), item_id)
    assert result.code == EXIT_OK, result.lines

    pair = records(layout, "worktree.remove")
    assert [r["kind"] for r in pair] == ["intent", "outcome"]
    assert pair[0]["action_id"] == pair[1]["action_id"]
    assert pair[0]["entity_type"] == "work_item"
    assert pair[0]["entity_id"] == item_id
    assert pair[0]["target"] == "/w/demo/issue-42"
    assert pair[1]["outcome"] == "ok"
    assert pair[1]["detail"]["refused"] is False
    assert pair[1]["detail"]["worktree_removed"] is True
    assert pair[1]["detail"]["branch_deleted"] is True


def test_gits_refusal_is_recorded_as_a_refusal_not_an_error(conn, audit, config, layout):
    """``outcome`` is fixed to ok/error/pending, and a guard firing is not a failure.

    ``cleanup.considered`` is the precedent: a ``skipped`` decision is recorded ``ok``.
    Nothing broke — the command was asked a question and answered it.
    """
    item_id = item_with_worktree(conn)
    ctx = make_context(conn, audit, config, vcs=RefusingVcs(audit))

    result = operations.worktree_remove(ctx, item_id)
    assert result.code == EXIT_FAILED

    outcome = records(layout, "worktree.remove")[-1]
    assert outcome["outcome"] == "ok"
    assert outcome["detail"]["refused"] is True
    assert outcome["detail"]["refused_by"] == "git"
    assert outcome["detail"]["worktree_removed"] is False
    assert result.data["refused_by"] == "git"


def test_the_intent_is_written_before_anything_is_removed(conn, audit, config, layout):
    """Principle IV's crash signature, and the Operating Constraints' rule that an
    irreversible action is logged *before* it executes."""
    item_id = item_with_worktree(conn)
    operations.worktree_remove(make_context(conn, audit, config), item_id)

    ordered = [
        r["action"]
        for r in records(layout)
        if r["action"] in ("worktree.remove", "git.remove_worktree")
    ]
    assert ordered[0] == "worktree.remove", "the intent precedes the removal"
    assert "git.remove_worktree" in ordered


# -- US1: the guard (T006, T007) --------------------------------------------


def test_the_reported_case_a_terminal_item_whose_session_is_still_running(
    conn, audit, config, layout
):
    """Issue #79, exactly as filed.

    The item is ``done`` — terminal, and therefore *precisely* what an operator reaches
    for when reclaiming disk — while its session runs on. That is not an exotic state: the
    issue closes, the item goes ``done``, and the session keeps going, which is deliberate
    and is what the ``orphan_session`` anomaly text describes.

    It is also the case the report's own suggested fix would have missed.
    ``reconcile.SESSION_BEARING_STATES`` is a set of *work item* states,
    ``{dispatching, active}``; a guard written against it would have permitted this
    removal. So the guard reads session rows and never the item's state.
    """
    item_id = item_with_worktree(conn, state=WorkItemState.DONE)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-live")

    result = operations.worktree_remove(
        make_context(conn, audit, config), item_id, confirm=never_asks
    )

    assert result.code == EXIT_PRECONDITION
    assert git_touched(layout) == [], "nothing may be removed while a worker is in there"

    item = db.get_work_item(conn, item_id)
    assert item.worktree_path == "/w/demo/issue-42"
    assert item.branch == BRANCH
    assert item.state is WorkItemState.DONE
    assert item.cleanup_state is None


@pytest.mark.parametrize("session_state", [SessionState.STARTING, SessionState.RUNNING])
@pytest.mark.parametrize("item_state", [WorkItemState.DONE, WorkItemState.ACTIVE])
def test_refuses_for_every_open_session_whatever_the_item_is_doing(
    conn, audit, config, layout, session_state, item_state
):
    """``starting`` counts: a session that has not reported itself running yet is not a
    session that is safely absent. And the item's own state is never consulted."""
    item_id = item_with_worktree(conn, state=item_state)
    seed_session(conn, item_id, state=str(session_state), session_id="s-open")

    result = operations.worktree_remove(
        make_context(conn, audit, config), item_id, confirm=never_asks
    )
    assert result.code == EXIT_PRECONDITION
    assert git_touched(layout) == []


def test_refuses_when_only_an_earlier_attempt_is_still_open(conn, audit, config, layout):
    """The case ``db.latest_session_for_item`` would miss (research R1).

    A superseded attempt's worker keeps running, reparented — that is what the
    ``orphan_session`` anomaly exists to report — so asking only about the newest row
    answers "nothing is running" while a worker is still writing in the directory.
    """
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-first")
    seed_session(conn, item_id, state=str(SessionState.EXITED_CLEAN), session_id="s-second")
    assert db.latest_session_for_item(conn, item_id).session_id == "s-second"

    result = operations.worktree_remove(
        make_context(conn, audit, config), item_id, confirm=never_asks
    )
    assert result.code == EXIT_PRECONDITION
    assert "s-first" in "\n".join(result.lines)
    assert git_touched(layout) == []


@pytest.mark.parametrize(
    "closed",
    [SessionState.EXITED_CLEAN, SessionState.EXITED_ERROR, SessionState.LOST],
)
def test_a_closed_session_does_not_refuse(conn, audit, config, layout, closed):
    """The negative control. No removal that succeeds today may become a refusal."""
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(closed), session_id=f"s-{closed}")

    result = operations.worktree_remove(make_context(conn, audit, config), item_id)
    assert result.code == EXIT_OK, result.lines
    assert git_touched(layout) == ["git.remove_worktree", "git.delete_branch"]
    assert db.get_work_item(conn, item_id).worktree_path is None


def test_an_item_with_no_sessions_at_all_does_not_refuse(conn, audit, config, layout):
    item_id = item_with_worktree(conn)
    result = operations.worktree_remove(make_context(conn, audit, config), item_id)
    assert result.code == EXIT_OK, result.lines
    assert git_touched(layout) == ["git.remove_worktree", "git.delete_branch"]


# -- US1: what the refusal says (T010) --------------------------------------


def set_process_identity(conn, session_id: str, *, pid: int | None, proc_start: Any) -> None:
    """Write the two identity columns directly; ``seed_session`` writes only ``pid``."""
    with db.transaction(conn):
        conn.execute(
            "UPDATE sessions SET pid = ?, proc_start = ? WHERE session_id = ?",
            (pid, proc_start, session_id),
        )


def refuse(conn, audit, config, item_id) -> Any:
    return operations.worktree_remove(
        make_context(conn, audit, config), item_id, confirm=never_asks
    )


def test_a_running_process_is_reported_as_running(conn, audit, config):
    """The one case that consults ``/proc``, driven off this very process."""
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-alive")
    mine = os.getpid()
    set_process_identity(
        conn, "s-alive", pid=mine, proc_start=procinfo.starttime(mine)
    )

    result = refuse(conn, audit, config, item_id)
    assert f"pid {mine} is alive" in "\n".join(result.lines)
    assert result.data["live_session"]["liveness"] == "running"


def test_a_recycled_or_dead_pid_is_reported_as_gone(conn, audit, config):
    """A recorded start time that does not match the process now holding that number.

    Not a reason to proceed: the row is still open, and something has to close it before
    the disk is anyone's to reclaim.
    """
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-dead")
    set_process_identity(conn, "s-dead", pid=os.getpid(), proc_start="1")

    result = refuse(conn, audit, config, item_id)
    assert "is no longer there" in "\n".join(result.lines)
    assert result.data["live_session"]["liveness"] == "gone"


def test_a_pid_with_no_recorded_start_time_is_never_called_alive(conn, audit, config):
    """The case that must not read as ``running`` (contract W10).

    ``procinfo.is_alive(pid, None)`` returns ``True`` for *any* process holding that
    number — the degradation that let a pid of ``1`` through the termination guard in #69 —
    and a real session row can legitimately carry a pid and no start time.
    """
    mine = os.getpid()
    item_id = item_with_worktree(conn)
    seed_session(
        conn, item_id, state=str(SessionState.RUNNING), session_id="s-bare", pid=mine
    )

    result = refuse(conn, audit, config, item_id)
    rendered = "\n".join(result.lines)
    assert "with no start time to identify it by" in rendered
    assert f"pid {mine} is alive" not in rendered, (
        "the process claim must not be made at all; the session state saying `running` "
        "is a different sentence about a different thing"
    )
    assert result.data["live_session"]["liveness"] == "unidentified"


def test_no_recorded_pid_says_so_rather_than_guessing(conn, audit, config):
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-nopid")
    set_process_identity(conn, "s-nopid", pid=None, proc_start=None)

    result = refuse(conn, audit, config, item_id)
    assert "no process id recorded" in "\n".join(result.lines)
    assert result.data["live_session"]["liveness"] == "unrecorded"


def test_the_refusal_names_the_session_and_offers_the_reattach_line(conn, audit, config):
    item_id = item_with_worktree(conn)
    seed_session(
        conn,
        item_id,
        state=str(SessionState.RUNNING),
        session_id="s-look",
        host_socket="/run/robot-army/demo-42.sock",
    )

    rendered = "\n".join(refuse(conn, audit, config, item_id).lines)
    assert "s-look" in rendered
    assert "dtach -a /run/robot-army/demo-42.sock" in rendered, (
        "the same line `show` prints, so the operator can go and look before deciding"
    )
    assert f"robot-army cancel {item_id}" in rendered
    assert f"robot-army worktree remove {item_id} --force" in rendered


def test_no_socket_means_no_reattach_line_and_nothing_invented(conn, audit, config):
    item_id = item_with_worktree(conn)
    seed_session(
        conn, item_id, state=str(SessionState.RUNNING), session_id="s-nosock", host_socket=None
    )

    rendered = "\n".join(refuse(conn, audit, config, item_id).lines)
    assert "dtach" not in rendered
    assert "None" not in rendered


def test_several_open_sessions_render_one_and_count_the_rest(conn, audit, config):
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-a")
    seed_session(conn, item_id, state=str(SessionState.STARTING), session_id="s-b")

    rendered = "\n".join(refuse(conn, audit, config, item_id).lines)
    assert "s-a" in rendered
    assert "1 other open session" in rendered


def test_the_two_refusals_are_distinguishable_without_reading_the_message(
    conn, audit, config
):
    """Exit status alone separates them, and so does the payload.

    A live session is a precondition that is not met; git's dirty-tree refusal is the
    command failing to do what it was asked. Conflating them would leave a script unable to
    tell "come back when the worker is done" from "commit your changes first".
    """
    live_item = item_with_worktree(conn, issue_number=42)
    seed_session(conn, live_item, state=str(SessionState.RUNNING), session_id="s-x")
    live = refuse(conn, audit, config, live_item)

    dirty_item = item_with_worktree(conn, issue_number=43)
    dirty = operations.worktree_remove(
        make_context(conn, audit, config, vcs=RefusingVcs(audit)), dirty_item
    )

    assert live.code == EXIT_PRECONDITION
    assert dirty.code == EXIT_FAILED
    assert live.data["refused_by"] == "live_session"
    assert dirty.data["refused_by"] == "git"
    assert live.data["worktree_removed"] is False
    assert live.data["branch_deleted"] is False
    assert "session s-x" in live.data["refused_reason"]
    assert "modified or untracked files" in dirty.data["refused_reason"]


# -- US2: the override, and its honesty (T012, T014) ------------------------


class Prompter:
    """Captures what the operator was asked, and answers whatever the test says."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answer


def test_without_force_the_operator_is_never_asked_anything(conn, audit, config, layout):
    """The refusal is not a question (FR-004).

    A prompt here would become a way to talk past the guard by reflex, which is the
    opposite of what it is for.
    """
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-live")
    prompter = Prompter(str(item_id))

    result = operations.worktree_remove(
        make_context(conn, audit, config), item_id, confirm=prompter
    )
    assert result.code == EXIT_PRECONDITION
    assert prompter.prompts == []
    assert git_touched(layout) == []


def test_the_forced_prompt_names_the_session_before_reading_any_input(
    conn, audit, config
):
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-live")
    prompter = Prompter("no")

    operations.worktree_remove(
        make_context(conn, audit, config), item_id, force=True, confirm=prompter
    )
    assert len(prompter.prompts) == 1, "one prompt, not two"
    asked = prompter.prompts[0]
    assert "s-live" in asked
    assert "deleted directory" in asked
    assert f"Type the item id ({item_id})" in asked


def test_the_prompt_is_unchanged_when_nothing_is_running(conn, audit, config):
    """The common case must not silently acquire different wording."""
    item_id = item_with_worktree(conn)
    prompter = Prompter("no")

    operations.worktree_remove(
        make_context(conn, audit, config), item_id, force=True, confirm=prompter
    )
    assert prompter.prompts == [
        f"Type the item id ({item_id}) to force-remove /w/demo/issue-42 "
        "and discard its uncommitted work: "
    ]


def test_a_wrong_answer_aborts_with_nothing_removed(conn, audit, config, layout):
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-live")

    result = operations.worktree_remove(
        make_context(conn, audit, config), item_id, force=True, confirm=Prompter("yes")
    )
    assert result.code == EXIT_FAILED
    assert git_touched(layout) == [], "a generic 'yes' must not be enough"
    assert db.get_work_item(conn, item_id).worktree_path == "/w/demo/issue-42"


def test_the_typed_id_removes_the_worktree_over_a_live_session(conn, audit, config, layout):
    """The escape hatch has to work, or a session row nothing will ever close — a
    simulated one, say — strands its worktree forever."""
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-live")

    result = operations.worktree_remove(
        make_context(conn, audit, config),
        item_id,
        force=True,
        confirm=Prompter(str(item_id)),
    )
    assert result.code == EXIT_OK, result.lines
    assert git_touched(layout) == ["git.remove_worktree", "git.delete_branch"]
    assert db.get_work_item(conn, item_id).worktree_path is None


# -- US3: the record (T016, T017) -------------------------------------------


def outcome_detail(layout) -> dict[str, Any]:
    return records(layout, "worktree.remove")[-1]["detail"]


def test_a_live_session_refusal_is_reconstructible_from_the_log_alone(
    conn, audit, config, layout
):
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-live")

    operations.worktree_remove(
        make_context(conn, audit, config), item_id, confirm=never_asks
    )

    pair = records(layout, "worktree.remove")
    assert [r["kind"] for r in pair] == ["intent", "outcome"]
    assert pair[1]["outcome"] == "ok", "a guard firing is not a failure"
    detail = pair[1]["detail"]
    assert detail["refused"] is True
    assert detail["refused_by"] == "live_session"
    assert "s-live" in detail["reason"]
    assert detail["live_session"]["session_id"] == "s-live"
    assert detail["live_session"]["liveness"] in {
        "running",
        "gone",
        "unidentified",
        "unrecorded",
    }
    assert detail["worktree_removed"] is False
    assert detail["branch_deleted"] is False
    assert pair[0]["entity_id"] == item_id


def test_an_override_of_a_live_worker_is_recorded_as_exactly_that(
    conn, audit, config, layout
):
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-live")

    operations.worktree_remove(
        make_context(conn, audit, config),
        item_id,
        force=True,
        confirm=Prompter(str(item_id)),
    )

    detail = outcome_detail(layout)
    assert detail["force"] is True
    assert detail["forced_over_live_session"] is True
    assert detail["live_session"]["session_id"] == "s-live"
    assert detail["worktree_removed"] is True


def test_forcing_past_a_dirty_tree_is_not_recorded_as_overriding_a_worker(
    conn, audit, config, layout
):
    """``force: true`` cannot distinguish the two, which is the whole reason for the
    second key. One discards uncommitted edits; the other pulls the floor out from under
    a running process."""
    item_id = item_with_worktree(conn)

    operations.worktree_remove(
        make_context(conn, audit, config),
        item_id,
        force=True,
        confirm=Prompter(str(item_id)),
    )

    detail = outcome_detail(layout)
    assert detail["force"] is True
    assert detail["forced_over_live_session"] is False
    assert "live_session" not in detail


def test_an_aborted_confirmation_leaves_the_reason_it_was_asked_on_the_record(
    conn, audit, config, layout
):
    item_id = item_with_worktree(conn)
    seed_session(conn, item_id, state=str(SessionState.RUNNING), session_id="s-live")

    operations.worktree_remove(
        make_context(conn, audit, config), item_id, force=True, confirm=Prompter("no")
    )

    detail = outcome_detail(layout)
    assert detail["aborted"] is True
    assert detail["worktree_removed"] is False
    assert detail["forced_over_live_session"] is False
    assert detail["live_session"]["session_id"] == "s-live", (
        "the log should say what the operator was warned about and declined"
    )


def test_the_payload_discriminator_covers_all_three_outcomes(conn, audit, config):
    """``refused_reason`` alone would leave a reader deciding which guard it belongs to."""
    ctx = make_context(conn, audit, config)

    clean = item_with_worktree(conn, issue_number=42)
    ok = operations.worktree_remove(ctx, clean)
    assert "refused_by" not in ok.data
    assert ok.data["refused_reason"] is None

    live_item = item_with_worktree(conn, issue_number=43)
    seed_session(conn, live_item, state=str(SessionState.RUNNING), session_id="s-live")
    live = operations.worktree_remove(ctx, live_item, confirm=never_asks)
    assert live.data["refused_by"] == "live_session"
    assert "s-live" in live.data["refused_reason"]

    dirty_item = item_with_worktree(conn, issue_number=44)
    dirty = operations.worktree_remove(
        make_context(conn, audit, config, vcs=RefusingVcs(audit)), dirty_item
    )
    assert dirty.data["refused_by"] == "git"
    assert "modified or untracked files" in dirty.data["refused_reason"]


# -- issue #23: giving up at the force prompt --------------------------------
#
# The prompt that discards uncommitted work. Before #23, Ctrl-C or a closed stdin here
# produced a traceback and an outcome record whose entire content was the exception name —
# so the log could say a force-removal had been *started* and nothing about how it ended.


class GivesUp:
    """A maintainer who walks away, or a stdin that was never there."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        raise self.error


@pytest.mark.parametrize(
    ("error", "code", "line", "cause"),
    [
        (KeyboardInterrupt(), EXIT_FAILED, "interrupted", "interrupted_at_prompt"),
        (
            EOFError(),
            EXIT_CHECK_FAILED,
            "no answer available: input ended before the prompt was answered",
            "no_answer_available",
        ),
    ],
    ids=["ctrl-c", "eof"],
)
def test_giving_up_at_the_force_prompt_removes_nothing_and_says_so(
    conn, audit, config, layout, error, code, line, cause
):
    item_id = item_with_worktree(conn)
    prompter = GivesUp(error)

    result = operations.worktree_remove(
        make_context(conn, audit, config), item_id, force=True, confirm=prompter
    )

    assert prompter.prompts, "it did ask before giving up"
    assert result.code == code
    assert result.lines == [line]
    assert git_touched(layout) == [], "nothing removed, and nothing merely attempted"
    assert db.get_work_item(conn, item_id).worktree_path == "/w/demo/issue-42"
    assert db.get_work_item(conn, item_id).branch == BRANCH


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (KeyboardInterrupt(), "interrupted_at_prompt"),
        (EOFError(), "no_answer_available"),
    ],
    ids=["ctrl-c", "eof"],
)
def test_the_abandoned_force_removal_is_readable_from_the_pair_alone(
    conn, audit, config, layout, error, cause
):
    """Issue #23's actual complaint. The intent record was always written before the
    prompt blocked — what was missing was an outcome that said how the answer went.

    "A forced removal of that path was attempted, and abandoned, and nothing was removed"
    has to be readable without re-running anything (Principle III).
    """
    item_id = item_with_worktree(conn)

    operations.worktree_remove(
        make_context(conn, audit, config),
        item_id,
        force=True,
        confirm=GivesUp(error),
    )
    audit.close()

    intent, outcome = records(layout, "worktree.remove")
    assert intent["kind"] == "intent" and intent["outcome"] == "pending"
    assert intent["target"] == "/w/demo/issue-42"
    assert intent["detail"]["force"] is True
    assert intent["entity_id"] == item_id

    assert outcome["kind"] == "outcome" and outcome["outcome"] == "error"
    assert outcome["detail"]["abandoned"] is True
    assert outcome["detail"]["cause"] == cause
    assert outcome["detail"]["worktree_removed"] is False
    assert outcome["detail"]["branch_deleted"] is False


def test_an_absent_answer_is_not_the_item_id(conn, audit, config, layout):
    """The note issue #23 closes on. This prompt does not ask for `y` — it asks the
    operator to type the item id — so "an empty answer is not consent" has to be checked
    where the comparison actually happens, not assumed from the `[y/N]` prompts.

    And it is a distinct outcome from typing the wrong thing: a decline is `aborted`,
    while this is a question that was never answered at all.
    """
    item_id = item_with_worktree(conn)

    given_up = operations.worktree_remove(
        make_context(conn, audit, config), item_id, force=True, confirm=GivesUp(EOFError())
    )
    declined = operations.worktree_remove(
        make_context(conn, audit, config), item_id, force=True, confirm=Prompter("")
    )

    assert git_touched(layout) == [], "neither one removed anything"
    assert declined.lines == ["aborted"] and declined.code == EXIT_FAILED
    assert given_up.lines != ["aborted"], "an unanswered question is not a decline"
    assert given_up.code == EXIT_CHECK_FAILED
