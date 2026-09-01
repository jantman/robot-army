"""Cleanup's eligibility rules and its branch decision table (T059, T060).

The two guards are different guards, and assuming otherwise is how this goes wrong. Git's
refusal to remove a dirty worktree is free and exactly right; git's own branch guard is the
*wrong* guard here and would accumulate ``robot-army/*`` branches forever. So the branch
half is our own containment check, and every unresolved doubt keeps the branch.

The decision table below is the part worth reading twice. Two of its four rows delete and
two retain, and the fourth — ``commits_ahead`` returning ``None`` — is the one that used to
be indistinguishable from the first.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from tests.conftest import seed_item, seed_session

from robot_army import cleanup, db
from robot_army.boundaries import BoundaryError, RemovalResult
from robot_army.states import SessionState, WorkItemState


class FakeVcs:
    """A version control boundary whose every answer the test dictates.

    Real git elsewhere — ``tests/integration/test_cleanup.py`` drives the whole pass against
    real repositories — but the *decision table* is about what cleanup does with each
    possible answer, including ones real git only produces under conditions that are awkward
    to arrange on purpose.
    """

    def __init__(
        self,
        *,
        removal: RemovalResult | None = None,
        ahead: dict[str, int | None] | None = None,
        fetch_raises: bool = False,
        delete_ok: bool = True,
        worktree_present: bool = True,
        remote_head: str | None = None,
        remote_raises: bool = False,
        head_is_local: bool = True,
    ) -> None:
        self.removal = removal or RemovalResult(worktree_removed=True, branch_deleted=False)
        self.worktree_present = worktree_present
        self.ahead = ahead or {}
        self.fetch_raises = fetch_raises
        self.delete_ok = delete_ok
        #: What the remote says it has at ``refs/heads/<branch>``, right now. ``None`` is a
        #: real answer — "the remote does not have this branch" — and is the default,
        #: because a branch nobody pushed is the ordinary case (issue #105).
        self.remote_head = remote_head
        self.remote_raises = remote_raises
        #: Whether the commit the remote named is in this clone. ``False`` is the
        #: someone-else-pushed case, where containment cannot be shown without fetching.
        self.head_is_local = head_is_local
        self.removals: list[tuple[str, bool]] = []
        self.deletes: list[tuple[str, bool]] = []
        self.fetches: list[tuple[str, str]] = []
        self.asked_remote: list[tuple[str, str]] = []

    def default_remote(self, clone_path: str) -> str | None:
        return "origin"

    def remote_branch_head(self, clone_path: str, remote: str, branch: str) -> str | None:
        if self.remote_raises:
            raise BoundaryError("the remote could not be reached")
        self.asked_remote.append((remote, branch))
        return self.remote_head

    def rev_parse(self, clone_path: str, ref: str) -> str | None:
        # Two callers, and only one of them is being varied. ``_branch_exists`` asks about
        # ``refs/heads/<branch>`` and must keep getting "yes", or ``clean_item`` concludes
        # the branch was already gone and never reaches the containment check at all.
        # ``head_is_local`` is about the commit the *remote* named, and nothing else.
        if not self.head_is_local and ref == self.remote_head:
            return None
        return ref

    def worktree_exists(self, worktree_path: str) -> bool:
        return self.worktree_present

    def fetch(self, clone_path: str, remote: str, ref: str) -> None:
        if self.fetch_raises:
            raise BoundaryError("network is down")
        self.fetches.append((remote, ref))

    def remove_worktree(
        self, worktree_path: str, force: bool = False, clone_path: str | None = None
    ) -> RemovalResult:
        self.removals.append((worktree_path, force))
        return self.removal

    def commits_ahead(self, clone_path: str, base_ref: str, branch: str) -> int | None:
        return self.ahead.get(base_ref)

    def delete_branch(self, clone_path: str, branch: str, force: bool = False) -> bool:
        self.deletes.append((branch, force))
        return self.delete_ok


def boundaries_with(audit: Any, vcs: Any) -> Any:
    from tests.conftest import make_boundaries

    return make_boundaries(audit, vcs=vcs)


def finished(conn, **kwargs) -> Any:
    item_id = seed_item(conn, state=str(WorkItemState.DONE), **kwargs)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn,
            item_id,
            worktree_path=f"/w/demo/issue-{kwargs.get('issue_number', 42)}",
            branch="robot-army/42-fix",
        )
    return db.get_work_item(conn, item_id)


def run(conn, audit, config, vcs, item, **kwargs) -> cleanup.Decision:
    return cleanup.clean_item(
        conn,
        boundaries=boundaries_with(audit, vcs),
        audit=audit,
        config=config,
        item=item,
        **kwargs,
    )


# -- eligibility (T059) -----------------------------------------------------


def test_a_done_item_with_a_worktree_is_eligible(conn, config):
    item = finished(conn)
    ok, reason = cleanup.eligible(conn, item)
    assert ok is True
    assert "nothing is running" in reason


@pytest.mark.parametrize(
    "state", [WorkItemState.ACTIVE, WorkItemState.AWAITING_REVIEW, WorkItemState.FAILED]
)
def test_an_unfinished_item_is_not_eligible(conn, config, state):
    item = seed_item(conn, state=str(state), issue_number=7)
    ok, reason = cleanup.eligible(conn, db.get_work_item(conn, item))
    assert ok is False
    assert "not done" in reason


def test_an_item_with_no_worktree_on_record_is_not_eligible(conn, config):
    item = seed_item(conn, state=str(WorkItemState.DONE), issue_number=8)
    ok, reason = cleanup.eligible(conn, db.get_work_item(conn, item))
    assert ok is False
    assert "no worktree" in reason


def test_a_live_session_yields_skipped_rather_than_a_removal(conn, config, audit):
    """An item can reach ``done`` while its session is still running — the issue closes,
    reconciliation notices, and the author is still typing in the window. Removing the
    worktree out from under them would destroy work in progress to reclaim disk."""
    item = finished(conn)
    seed_session(conn, item.id, state=str(SessionState.RUNNING), session_id="s-live")
    vcs = FakeVcs()

    decision = run(conn, audit, config, vcs, db.get_work_item(conn, item.id))
    assert decision.state == cleanup.SKIPPED
    assert vcs.removals == [], "nothing may be removed while a session is live"
    assert db.get_work_item(conn, item.id).cleanup_state == cleanup.SKIPPED


def test_the_skipped_routing_still_keys_on_the_reason_wording(conn, config, audit):
    """A regression pin, not a behaviour test (issue #79 R3).

    ``clean_item`` decides between ``skipped`` and plain ineligibility by matching the
    substring ``"still live"`` in the reason :func:`cleanup.eligible` just produced. Only
    ``skipped`` is reconsidered on a later pass, so rewording that sentence would silently
    convert every live-session skip into a decision nothing revisits — with the whole suite
    still green. Issue #79 moved the *computation* of the live list into
    ``cleanup.live_sessions``; this asserts it left the sentence alone.
    """
    item = finished(conn, issue_number=11)
    seed_session(conn, item.id, state=str(SessionState.RUNNING), session_id="s-wording")

    ok, reason = cleanup.eligible(conn, db.get_work_item(conn, item.id))
    assert ok is False
    assert "still live" in reason, "clean_item routes on this substring"
    assert "s-wording" in reason, "the reason names which session held it up"

    decision = run(conn, audit, config, FakeVcs(), db.get_work_item(conn, item.id))
    assert decision.state == cleanup.SKIPPED
    assert db.get_work_item(conn, item.id).id in {
        candidate.id for candidate in db.list_cleanup_candidates(conn)
    }, "skipped means 'not yet', so the item must still be a candidate"


# -- the shared live-session definition (issue #79 FR-014) ------------------


def test_live_sessions_is_empty_when_nothing_is_open(conn):
    item = finished(conn, issue_number=12)
    seed_session(conn, item.id, state=str(SessionState.EXITED_CLEAN), session_id="s-done")
    assert cleanup.live_sessions(conn, item.id) == []


@pytest.mark.parametrize("state", [SessionState.STARTING, SessionState.RUNNING])
def test_every_open_state_counts_as_live(conn, state):
    item = finished(conn, issue_number=13)
    seed_session(conn, item.id, state=str(state), session_id=f"s-{state}")
    live = cleanup.live_sessions(conn, item.id)
    assert [session.session_id for session in live] == [f"s-{state}"]


@pytest.mark.parametrize(
    "state", [SessionState.EXITED_CLEAN, SessionState.EXITED_ERROR, SessionState.LOST]
)
def test_no_closed_state_counts_as_live(conn, state):
    item = finished(conn, issue_number=14)
    seed_session(conn, item.id, state=str(state), session_id=f"s-{state}")
    assert cleanup.live_sessions(conn, item.id) == []


def test_an_earlier_attempt_still_open_is_found(conn):
    """The case ``db.latest_session_for_item`` would miss (issue #79 R1).

    A superseded attempt's worker keeps running, reparented — that is what the
    ``orphan_session`` anomaly exists to report — so asking only about the newest row
    answers "nothing is running" while a worker is still writing in the directory.
    """
    item = finished(conn, issue_number=15)
    seed_session(conn, item.id, state=str(SessionState.RUNNING), session_id="s-first")
    seed_session(conn, item.id, state=str(SessionState.EXITED_CLEAN), session_id="s-second")

    assert db.latest_session_for_item(conn, item.id).session_id == "s-second"
    assert [s.session_id for s in cleanup.live_sessions(conn, item.id)] == ["s-first"]


def test_live_sessions_come_back_in_attempt_order(conn):
    item = finished(conn, issue_number=16)
    seed_session(conn, item.id, state=str(SessionState.RUNNING), session_id="s-1")
    seed_session(conn, item.id, state=str(SessionState.STARTING), session_id="s-2")
    live = cleanup.live_sessions(conn, item.id)
    assert [s.session_id for s in live] == ["s-1", "s-2"]
    assert [s.attempt for s in live] == sorted(s.attempt for s in live)


def test_skipped_is_revisited_automatically_and_retained_is_not(conn, config):
    """The whole point of distinguishing them: one means "not yet", the other means "we
    looked and decided no"."""
    skipped = finished(conn, issue_number=1)
    retained = finished(conn, issue_number=2)
    branch_retained = finished(conn, issue_number=3)
    done = finished(conn, issue_number=4)
    with db.transaction(conn):
        db.record_cleanup(conn, skipped.id, state=cleanup.SKIPPED, reason="live")
        db.record_cleanup(conn, retained.id, state=cleanup.RETAINED, reason="dirty")
        db.record_cleanup(conn, branch_retained.id, state=cleanup.BRANCH_RETAINED, reason="x")
        db.record_cleanup(conn, done.id, state=cleanup.DONE, reason="removed")

    revisited = {i.id for i in db.list_cleanup_candidates(conn)}
    assert revisited == {skipped.id}


def test_naming_an_item_explicitly_reconsiders_a_retained_decision(conn, config, audit):
    """FR-029: the explicit command is what reconsiders a decision the automatic pass has
    finished with — the author has looked at the worktree and decided."""
    item = finished(conn)
    with db.transaction(conn):
        db.record_cleanup(conn, item.id, state=cleanup.RETAINED, reason="was dirty")
    vcs = FakeVcs(ahead={"origin/main": 0})

    decisions = cleanup.sweep(
        conn,
        boundaries=boundaries_with(audit, vcs),
        audit=audit,
        config=config,
        item_id=item.id,
    )
    assert [d.state for d in decisions] == [cleanup.DONE]


# -- guard 1: the worktree --------------------------------------------------


def test_force_is_never_passed_to_the_worktree_removal(conn, config, audit):
    """FR-025. Git's refusal on a dirty tree — including merely untracked files — is the
    guard, and passing force would be removing the guard rather than passing it."""
    item = finished(conn)
    vcs = FakeVcs(ahead={"origin/main": 0})
    run(conn, audit, config, vcs, item)
    assert vcs.removals == [(item.worktree_path, False)]


def test_a_refused_worktree_retains_and_does_not_attempt_the_branch(conn, config, audit):
    """A dirty worktree means the branch may hold the only copy of something. The two
    guards answer different questions, and running the second after the first refused would
    be treating them as one."""
    item = finished(conn)
    vcs = FakeVcs(
        removal=RemovalResult(
            worktree_removed=False,
            branch_deleted=False,
            refused_reason="contains modified or untracked files, use --force to delete it",
        ),
        ahead={"origin/main": 0},
    )
    decision = run(conn, audit, config, vcs, item)

    assert decision.state == cleanup.RETAINED
    assert "untracked" in decision.reason, "git's own message is what is recorded"
    assert vcs.deletes == []
    assert vcs.fetches == []


def test_the_record_keeps_the_path_and_branch_after_a_successful_removal(conn, config, audit):
    """FR-024. "What was at this path?" is exactly the question a retained-branch record has
    to answer, and ``_sweep_worktrees`` keys on the path being present."""
    item = finished(conn)
    run(conn, audit, config, FakeVcs(ahead={"origin/main": 0}), item)
    after = db.get_work_item(conn, item.id)
    assert after.cleanup_state == cleanup.DONE
    assert after.worktree_path == item.worktree_path
    assert after.branch == item.branch
    assert after.cleaned_at is not None


# -- guard 2: the branch decision table (T060) ------------------------------


def test_a_branch_contained_in_the_published_base_is_deleted(conn, config, audit):
    """The normal case: the PR was merged on GitHub. Note that ``git branch -d`` would
    *refuse* this — the clone's HEAD is stale and the branch has no upstream — which is
    exactly why git's own guard is the wrong guard here."""
    item = finished(conn)
    vcs = FakeVcs(ahead={"origin/main": 0, "origin/robot-army/42-fix": None})
    decision = run(conn, audit, config, vcs, item)

    assert decision.state == cleanup.DONE
    assert decision.branch_deleted is True
    assert vcs.deletes == [(item.branch, True)]
    assert "contained in origin/main" in decision.reason


def test_a_branch_pushed_and_up_to_date_is_deleted(conn, config, audit):
    """The other way for the work to be safe: it lives on the remote under its own name,
    even though it was never merged.

    Note what the ``ahead`` key is. Since issue #105 the comparison is against the commit
    the remote just reported, not against a ``origin/<branch>`` ref name — that ref is a
    local cache, and reading it was how a deleted remote branch went on proving itself
    published.
    """
    item = finished(conn)
    vcs = FakeVcs(ahead={"origin/main": 3, "sha-on-the-remote": 0},
                  remote_head="sha-on-the-remote")
    decision = run(conn, audit, config, vcs, item)

    assert decision.state == cleanup.DONE
    assert vcs.deletes == [(item.branch, True)]
    assert vcs.asked_remote == [("origin", item.branch)]
    assert "sha-on-the-remote" in decision.reason, "the record names the commit, not a ref"


def test_a_branch_the_remote_no_longer_has_is_retained(conn, config, audit):
    """Issue #105's reported case, at the decision table.

    The remote answered, and it has no such branch. That is information — distinct from
    "could not ask" — and the two must reach the same decision by visibly different
    sentences, because they send the operator to different places.
    """
    item = finished(conn)
    vcs = FakeVcs(ahead={"origin/main": 1}, remote_head=None)
    decision = run(conn, audit, config, vcs, item)

    assert decision.state == cleanup.BRANCH_RETAINED
    assert vcs.deletes == []
    assert "does not have" in decision.reason


def test_a_remote_that_cannot_be_asked_is_not_an_answer(conn, config, audit):
    """The failure direction, for the new question as for the old one."""
    item = finished(conn)
    vcs = FakeVcs(ahead={"origin/main": 1}, remote_raises=True)
    decision = run(conn, audit, config, vcs, item)

    assert decision.state == cleanup.BRANCH_RETAINED
    assert vcs.deletes == []
    assert "could not ask" in decision.reason


def test_a_remote_commit_this_clone_lacks_proves_nothing(conn, config, audit):
    """Someone else pushed to the branch. Their commit is not here, so nothing can be shown
    reachable from it — and this check is a question, never a synchronisation."""
    item = finished(conn)
    vcs = FakeVcs(
        ahead={"origin/main": 1, "theirs": 0}, remote_head="theirs", head_is_local=False
    )
    decision = run(conn, audit, config, vcs, item)

    assert decision.state == cleanup.BRANCH_RETAINED
    assert vcs.deletes == []
    assert "which this clone does not have" in decision.reason


def test_commits_ahead_of_the_remote_commit_retains(conn, config, audit):
    """The remote has the branch, behind us — a force-push that rewound it, or a push that
    never carried the last commits. What is here is not published."""
    item = finished(conn)
    vcs = FakeVcs(ahead={"origin/main": 2, "older": 2}, remote_head="older")
    decision = run(conn, audit, config, vcs, item)

    assert decision.state == cleanup.BRANCH_RETAINED
    assert vcs.deletes == []
    assert "2 commit(s)" in decision.reason


def test_a_branch_that_is_neither_contained_nor_pushed_is_retained(conn, config, audit):
    """SC-009's counted outcome. These commits exist nowhere else."""
    item = finished(conn)
    vcs = FakeVcs(ahead={"origin/main": 2}, remote_head=None)
    decision = run(conn, audit, config, vcs, item)

    assert decision.state == cleanup.BRANCH_RETAINED
    assert vcs.deletes == []
    assert decision.worktree_removed is True, "the clean worktree is still reclaimed"
    assert "2 commit(s)" in decision.reason


def test_commits_ahead_returning_none_never_satisfies_either_test(conn, config, audit):
    """R11, and the reason the signature had to change.

    ``None`` means *could not determine*. Its predecessor was ``0``, which to this caller
    reads as "every commit here exists elsewhere, delete it" — so a transient git failure
    would have authorised destroying commits that exist nowhere else.
    """
    item = finished(conn)
    vcs = FakeVcs(ahead={"origin/main": None, "sha": None}, remote_head="sha")
    decision = run(conn, audit, config, vcs, item)

    assert decision.state == cleanup.BRANCH_RETAINED
    assert vcs.deletes == []
    assert "could not compare it with the base" in decision.reason
    assert "could not compare robot-army/42-fix with sha" in decision.reason


def test_a_failed_fetch_leaves_containment_unproven(conn, config, audit):
    """The fetch is required, not defensive: without it the check reads a stale
    remote-tracking ref and answers a question about the past."""
    item = finished(conn)
    vcs = FakeVcs(ahead={"origin/main": 0}, fetch_raises=True)
    decision = run(conn, audit, config, vcs, item)

    assert decision.state == cleanup.BRANCH_RETAINED
    assert vcs.deletes == []
    assert "could not fetch" in decision.reason


def test_the_containment_check_fetches_before_it_reads(conn, config, audit):
    item = finished(conn)
    vcs = FakeVcs(ahead={"origin/main": 0})
    run(conn, audit, config, vcs, item)
    assert vcs.fetches == [("origin", "main")]


def test_a_worktree_whose_directory_already_vanished_still_gets_its_branch_decided(conn, config, audit):
    """The kill point between the two removals, and a plain ``rm -rf``, look identical.

    Git refuses because there is no working tree at that path — a refusal about *its
    record*, not about the contents. Nothing can be lost by continuing, and stopping here is
    what would leave the branch orphaned forever. Clearing git's own record stays
    ``robot-army worktree prune``'s job (FR-030).
    """
    item = finished(conn)
    vcs = FakeVcs(
        removal=RemovalResult(
            worktree_removed=False,
            branch_deleted=False,
            refused_reason="fatal: '/w/demo/issue-42' is not a working tree",
        ),
        worktree_present=False,
        ahead={"origin/main": 0},
    )
    decision = run(conn, audit, config, vcs, item)

    assert decision.state == cleanup.DONE
    assert vcs.deletes == [(item.branch, True)]
    assert "already gone" in decision.reason


def test_git_declining_the_delete_retains_the_branch_rather_than_claiming_success(
    conn, config, audit
):
    item = finished(conn)
    vcs = FakeVcs(ahead={"origin/main": 0}, delete_ok=False)
    decision = run(conn, audit, config, vcs, item)
    assert decision.state == cleanup.BRANCH_RETAINED
    assert decision.branch_deleted is False


def test_an_item_with_no_branch_on_record_is_done_after_the_worktree_goes(
    conn, config, audit
):
    item = finished(conn)
    with db.transaction(conn):
        db.update_work_item_columns(conn, item.id, branch=None)
    vcs = FakeVcs()
    decision = run(conn, audit, config, vcs, db.get_work_item(conn, item.id))
    assert decision.state == cleanup.DONE
    assert vcs.deletes == []


def test_a_repository_that_no_longer_resolves_retains_everything(conn, config, audit):
    """Neither guard can be evaluated without the clone. Retaining is the only safe answer,
    and it is recorded rather than passed over in silence.

    After milestone 005 "no clone" means the *resolved* view has none — the onboarding
    record was deleted, or it predates migration 005 and no section supplies a path —
    rather than "the config file lost its section"."""
    item = finished(conn)
    config = replace(config, repos={})
    vcs = FakeVcs()
    decision = run(conn, audit, config, vcs, item)
    assert decision.state == cleanup.RETAINED
    assert vcs.removals == []
    assert "no longer resolves to a clone" in decision.reason


# -- the log answers "why is this 499 MB still here?" -----------------------


def test_every_consideration_is_recorded_even_when_nothing_is_removed(
    conn, config, audit, layout
):
    import json

    item = finished(conn)
    vcs = FakeVcs(ahead={"origin/main": 5, "origin/robot-army/42-fix": 5})
    run(conn, audit, config, vcs, item)
    audit.close()

    records = [
        json.loads(line)
        for path in sorted(layout.log_dir.glob("audit-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    actions = [r["action"] for r in records]
    assert "cleanup.considered" in actions
