"""Reclaiming the disk of finished work: two guards, two steps, four outcomes.

Runs from :func:`robot_army.reconcile._cleanup_worktrees` when ``[cleanup] on_issue_close``
is true — it defaults to false, because the Operating Constraints require irreversible
actions to be unreachable by default — and from ``robot-army cleanup`` under exactly the
same guards (FR-029).

**The two guards are different guards, and assuming otherwise is how this goes wrong**
(R12). For the worktree, git's own refusal is free and exactly right: ``git worktree
remove`` refuses on a dirty tree, including merely untracked files, so ``force`` is never
passed and a refusal is taken as the useful answer it is. For the branch, git's own guard is
the *wrong* guard: ``git branch -d`` accepts only a branch merged into the clone's current
``HEAD`` or its upstream, and the normal case here is a PR merged on GitHub while the
author's clone has a stale base checked out and the robot branch has no upstream — so ``-d``
refuses every time and ``robot-army/*`` branches accumulate forever. Containment is
therefore checked explicitly against the remote, and ``force=True`` on the delete means a
*stronger* guard than git's has already passed, never that a guard was skipped.

**"Against the remote" has to mean the remote, asked now.** Until issue #105 the second
containment test — the branch is published under its own name — was answered by reading
``refs/remotes/<remote>/<branch>``, which is a local cache of what the remote said the last
time anything asked, and the only fetch here is scoped to the base branch, so it neither
refreshes nor prunes it. A branch that was pushed and has since been deleted on the remote
went on reading as published for as long as nobody ran a full fetch, and it was deleted with
``force`` on that evidence. Measured end to end: after a routine ``gc`` on the remote, the
commit survived nowhere but the stale ref that had been mistaken for proof. The remote is
now asked directly (``VersionControl.remote_branch_head``) and no remote-tracking ref is
read here at all.

Every unresolved doubt keeps the branch. ``commits_ahead`` returning ``None`` means "could
not determine" and satisfies no test (R11).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from robot_army import db, repos
from robot_army.models import Session, WorkItem
from robot_army.states import SessionState, WorkItemState

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config
    from robot_army.effects import Boundaries

#: The four outcomes, per contracts/cleanup.md. Only ``SKIPPED`` is revisited automatically,
#: and that is the whole point of distinguishing it from ``RETAINED``: one means "not yet",
#: the other means "we looked and decided no".
DONE = "done"
BRANCH_RETAINED = "branch_retained"
RETAINED = "retained"
SKIPPED = "skipped"

#: The session states that mean a worker may still be running in the worktree.
#:
#: An **allow-list of open states**, not a deny-list of closed ones, for the reason
#: ``reconcile.SESSION_BEARING_STATES`` gives for its own set: a closed state added later
#: must not silently start counting as live, and a new *open* state has to be added here
#: deliberately. Getting that wrong in the permissive direction deletes a running worker's
#: directory, which is what issue #79 reported.
LIVE_SESSION_STATES: frozenset[SessionState] = frozenset(
    {SessionState.STARTING, SessionState.RUNNING}
)


@dataclass(frozen=True, slots=True)
class Decision:
    """What cleanup did to one item, and why. Returned as well as recorded."""

    item_id: int
    state: str
    reason: str
    worktree_removed: bool = False
    branch_deleted: bool = False

    @property
    def reclaimed(self) -> bool:
        return self.worktree_removed


def live_sessions(conn: sqlite3.Connection, item_id: int) -> list[Session]:
    """Every session row for the item that is still open, in attempt order.

    **Every** row, not the latest attempt: a session whose attempt has been superseded
    keeps running, reparented — that is what the ``orphan_session`` anomaly is for — so
    ``db.latest_session_for_item`` would answer "nothing is running" while a worker is
    still writing. Empty list means nothing is running.

    One definition with two callers, deliberately (issue #79 FR-014). :func:`eligible`
    asks it before reclaiming a finished item's disk automatically, and
    ``operations.worktree_remove`` asks it before doing the same thing on demand. The
    manual path had no such guard until #79, which is the wrong way round: cleanup is
    conservative and unattended, while ``worktree remove`` is what someone reaches for
    when the disk is full, and it is the one that can override git.

    Note what this does **not** consult: the work item's state, and the process table.
    The reported case was a ``done`` item — terminal, and therefore exactly what an
    operator reaches for — and a row whose process cannot be found is still a row nothing
    has closed.
    """
    return [
        session
        for session in db.list_sessions_for_item(conn, item_id)
        if session.state in LIVE_SESSION_STATES
    ]


def eligible(
    conn: sqlite3.Connection, item: WorkItem, *, reconsider: bool = False
) -> tuple[bool, str]:
    """May this item be considered for cleanup? Returns ``(eligible, reason)``.

    Four conditions, and the live-session one is the interesting one. An item can reach
    ``done`` while its session is still running — the issue closes, reconciliation notices,
    and the author is still typing in the window. Removing the worktree out from under a
    live session would destroy work in progress to reclaim disk, which is precisely
    backwards, so it is recorded as ``skipped`` (FR-027) and reconsidered next pass rather
    than being silently passed over.
    """
    if item.state is not WorkItemState.DONE:
        return False, f"item is {item.state}, not done"
    if not item.worktree_path:
        return False, "item has no worktree on record"
    if not item.cleanup_pending and not reconsider:
        return False, f"cleanup_state is {item.cleanup_state!r}, which is a decision"
    live = live_sessions(conn, item.id)
    if live:
        # The wording is load-bearing: ``clean_item`` below decides between ``skipped``
        # and plain ineligibility by matching "still live" in this string, and only
        # ``skipped`` is reconsidered on a later pass.
        return False, f"session {live[0].session_id} is still live"
    return True, "done, has a worktree, and nothing is running in it"


def clean_item(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    item: WorkItem,
    reconsider: bool = False,
) -> Decision:
    """Run both guards against one item and record what they decided.

    Never raises for an operational condition. A guard that refuses is the *expected*
    outcome, not an error, and every unresolved doubt keeps what it was unsure about.
    """
    ok, reason = eligible(conn, item, reconsider=reconsider)
    if not ok:
        live_session = "still live" in reason
        audit.record(
            "cleanup.considered",
            outcome="ok",
            entity_type="work_item",
            entity_id=item.id,
            detail={"decision": SKIPPED if live_session else "not eligible", "reason": reason},
            dry_run=item.dry_run,
        )
        if live_session:
            # ``skipped`` rather than ``retained``: "not yet" and "we looked and decided no"
            # are different answers, and only the first is revisited automatically.
            with db.transaction(conn):
                db.record_cleanup(conn, item.id, state=SKIPPED, reason=reason)
        return Decision(
            item_id=item.id, state=SKIPPED if live_session else "ineligible", reason=reason
        )

    repo = repos.resolve(conn, config, item.repo_key)
    if repo is None:
        # Nothing to remove *from*: the clone the worktree belongs to no longer resolves,
        # so neither guard can be evaluated. Retaining is the only safe answer.
        return _retain(
            conn,
            audit,
            item,
            RETAINED,
            f"repository {item.repo_key!r} no longer resolves to a clone",
        )

    audit.record(
        "cleanup.considered",
        outcome="ok",
        entity_type="work_item",
        entity_id=item.id,
        detail={"decision": "eligible", "worktree": item.worktree_path, "branch": item.branch},
        dry_run=item.dry_run,
    )

    # -- guard 1: the worktree, taking git's own refusal as the answer -----
    #
    # ``force`` is never passed. ``git worktree remove`` refuses on a dirty tree —
    # *including merely untracked files* — and ``boundaries/git.py`` already returns that
    # refusal as a RemovalResult rather than raising, because it is an expected and useful
    # outcome. FR-025 is satisfied by keeping that exactly as it is.
    vcs = boundaries.version_control
    try:
        removal = vcs.remove_worktree(
            item.worktree_path or "", force=False, clone_path=str(repo.path)
        )
    except Exception as exc:  # noqa: BLE001 - any boundary failure keeps what it was unsure about
        return _retain(conn, audit, item, RETAINED, f"worktree removal failed: {exc}")

    reclaimed_note = "worktree removed"
    if not removal.worktree_removed:
        if vcs.worktree_exists(item.worktree_path or ""):
            # The branch half is deliberately not attempted. A dirty worktree means the
            # branch may hold the only copy of something, and the two guards answer
            # different questions — assuming otherwise is how this goes wrong.
            return _retain(
                conn,
                audit,
                item,
                RETAINED,
                removal.refused_reason or "git refused to remove the worktree",
            )
        # The directory is already gone: a kill between the two removals, or a manual
        # `rm -rf`. Git refuses because there is no working tree there, which is a refusal
        # about *its record* rather than about the contents — nothing can be lost by
        # continuing, and stopping here is what would leave the branch orphaned forever.
        # This is the recovery the interruption table promises for that kill point; git's
        # own record is cleared by `robot-army worktree prune`, which FR-030 keeps separate.
        reclaimed_note = "worktree directory was already gone"

    # -- guard 2: the branch, our own containment check --------------------
    if not item.branch:
        return _record(
            conn, audit, item, DONE, f"{reclaimed_note}; no branch on record",
            worktree_removed=True,
        )

    if not _branch_exists(vcs, clone=str(repo.path), branch=item.branch):
        # Nothing to retain and nothing to delete. This is the second kill point in the
        # interruption table — killed after both removals, before the row was written — and
        # the re-attempt is supposed to resolve it to ``done`` rather than leaving it
        # ambiguous forever. Reporting a *retained* branch that does not exist would be an
        # answer that is wrong in the direction of looking like unfinished business.
        return _record(
            conn,
            audit,
            item,
            DONE,
            f"{reclaimed_note}; the branch was already gone",
            worktree_removed=True,
        )

    # Resolved from the clone (issue #150): a branch is judged contained against the branch
    # it was actually cut from, and in a ``master`` repository that is not ``main``.
    base = repos.base_ref(config, item.repo_key, vcs, repo.path).ref
    contained, evidence = _branch_is_contained(
        vcs, clone=str(repo.path), branch=item.branch, base=base
    )
    if not contained:
        return _retain(
            conn,
            audit,
            item,
            BRANCH_RETAINED,
            f"{reclaimed_note}; branch kept — {evidence}",
            worktree_removed=True,
        )

    # ``force=True`` here does **not** mean "skip the guard". It means a *stronger* guard
    # than git's has already passed: every commit on this branch is provably contained in a
    # ref that lives on the remote. Git's own ``-d`` is the wrong guard for this case — it
    # accepts only a branch merged into the clone's current HEAD or its upstream, and the
    # normal case here is a PR merged on GitHub while the author's clone has a stale base
    # checked out and the robot branch has no upstream, so ``-d`` refuses every time and
    # ``robot-army/*`` branches accumulate in every repository forever.
    try:
        deleted = vcs.delete_branch(str(repo.path), item.branch, force=True)
    except Exception as exc:  # noqa: BLE001 - a delete that failed is a branch that stayed
        return _retain(
            conn, audit, item, BRANCH_RETAINED,
            f"{reclaimed_note}; branch delete failed: {exc}", worktree_removed=True,
        )
    if not deleted:
        return _retain(
            conn, audit, item, BRANCH_RETAINED,
            f"{reclaimed_note}; git declined to delete the branch", worktree_removed=True,
        )
    return _record(
        conn, audit, item, DONE, f"{reclaimed_note}; branch removed — {evidence}",
        worktree_removed=True, branch_deleted=True,
    )


def _branch_exists(vcs: object, *, clone: str, branch: str) -> bool:
    """Does the branch still exist in the clone?

    ``True`` when the question cannot be answered, because "I could not check" must never
    read as "it is gone" — that would record a surviving branch as cleaned up, which is the
    one direction this answer is allowed to be wrong in.
    """
    try:
        return vcs.rev_parse(clone, f"refs/heads/{branch}") is not None  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - an unanswerable question keeps the safer assumption
        return True


def _branch_is_contained(
    vcs: object, *, clone: str, branch: str, base: str
) -> tuple[bool, str]:
    """Is every commit on ``branch`` provably somewhere on the remote? (R12)

    Two ways for that to be true, and either suffices: the branch is contained in the
    published base (the PR was merged), or the branch itself is pushed and up to date (the
    work is on the remote under its own name).

    The fetch is required, not defensive. Without it the check reads a stale
    remote-tracking ref and answers a question about the past — and for most of milestone
    004's life that reasoning was written here and applied to only the *first* test.
    ``{remote}/{branch}`` on the second one resolved to ``refs/remotes/<remote>/<branch>``,
    a local ref nothing here ever fetched, and the fetch above is scoped to the base so it
    neither refreshes nor prunes it. A branch that was pushed and has since been deleted on
    the remote therefore went on proving itself "pushed and up to date" from a leftover
    cache, and ``force=True`` deleted it. Issue #105 measured that end to end: after an
    ordinary ``gc`` on the remote the commit existed nowhere but the stale ref that had
    been mistaken for proof. The second test now asks the remote — see
    :func:`_pushed_to_remote` — and no remote-tracking ref is read here at all.

    ``commits_ahead`` returning ``None`` means *could not determine* and satisfies neither
    test (R11). Its previous ``return 0`` meant "no information" to the resume-signal caller
    and "safe to delete" to this one — the same value with opposite meanings — and this
    function is the reason that ambiguity had to end.
    """
    # Every ``except`` here is deliberately broad, and it is the same judgement
    # ``worktree.prepare`` already makes about its own fetch: the boundary can fail in more
    # ways than it declares — a timeout, a subprocess that exited non-zero, a remote that
    # has gone away — and *which* way it failed changes nothing here. Any failure means
    # containment is unproven, and unproven keeps the branch.
    remote = "origin"
    try:
        remote = vcs.default_remote(clone) or "origin"  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - the conventional name is a better guess than none
        remote = "origin"
    try:
        vcs.fetch(clone, remote, base)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - see above
        return False, f"could not fetch {remote}/{base} ({exc}), so containment is unproven"

    try:
        in_base = vcs.commits_ahead(clone, f"{remote}/{base}", branch)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - "could not determine" is the only honest answer
        in_base = None
    if in_base == 0:
        return True, f"every commit is contained in {remote}/{base}"

    pushed, pushed_evidence = _pushed_to_remote(vcs, clone=clone, remote=remote, branch=branch)
    if pushed:
        return True, pushed_evidence

    # Both answers, not the winning one. The reader asking "why is this branch still
    # here?" months from now is owed what each test said, because "1 commit ahead of the
    # base" and "the remote does not have this branch" are different problems with
    # different remedies, and reporting only one of them sends them to the wrong place.
    in_base_evidence = (
        "git could not compare it with the base"
        if in_base is None
        else f"{in_base} commit(s) are not on {remote}/{base}"
    )
    return False, f"{in_base_evidence}; {pushed_evidence}"


def _pushed_to_remote(
    vcs: object, *, clone: str, remote: str, branch: str
) -> tuple[bool, str]:
    """Is every commit on ``branch`` on the remote **under its own name**? (issue #105)

    Six outcomes, six visible returns, and exactly one of them authorises a delete. They
    are written out rather than folded together because the value of this function is that
    "the remote does not have this branch" and "the remote could not be asked" reach the
    same decision by different routes and must not reach the same *sentence* — the operator
    reading a retained branch's reason has to know which of them happened.

    The evidence names the commit the remote reported, not just the ref that was consulted.
    That distinction is the whole fix: *"the branch is pushed and up to date with
    origin/<branch>"* is what this said while reading a ref the remote had never been asked
    about, and a record that cannot tell a sound deletion from that one is not a record.

    Every ``except`` is broad for the reason the caller's are: the boundary can fail in more
    ways than it declares, which way changes nothing, and unproven keeps the branch.
    """
    try:
        head = vcs.remote_branch_head(clone, remote, branch)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - could not ask is never taken as an answer
        return False, f"could not ask {remote} about {branch} ({exc}), so it is unproven"

    if head is None:
        return False, f"{remote} does not have {branch}"

    try:
        # ``^{commit}`` is load-bearing, not decoration. ``git rev-parse --verify`` on a
        # bare forty-hex string echoes it back and exits zero **whether or not the object
        # is here** — it validates the syntax of a revision, not the presence of what it
        # names. Since ``head`` always is forty hex characters, asking without the peel
        # made this branch unreachable and sent the case to ``commits_ahead`` instead,
        # where it failed and produced "could not compare" rather than the sentence that
        # says what actually happened. Peeling to a commit forces the lookup.
        local = vcs.rev_parse(clone, f"{head}^{{commit}}")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - see above
        local = None
    if local is None:
        # Someone pushed to this branch from elsewhere. Their commit is not in this clone,
        # so there is no way to show our commits are reachable from it without fetching
        # objects — and this check is deliberately a question, not a synchronisation.
        return False, f"{remote} has {branch} at {head}, which this clone does not have"

    try:
        ahead = vcs.commits_ahead(clone, head, branch)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - see above
        ahead = None
    if ahead is None:
        return False, f"git could not compare {branch} with {head}, so it is unproven"
    if ahead > 0:
        # Named as the remote's branch at the commit that was actually compared. "not on
        # origin" would be both vaguer and wrong: a remote is not a ref, and those commits
        # may well be somewhere else on it — under another branch, or an open pull request.
        # What is true, and what decides this, is that they are not on *this* branch there.
        return False, (
            f"{ahead} commit(s) on {branch} are not on {remote}/{branch}, "
            f"which is at {head}"
        )
    return True, f"every commit is on {remote}/{branch}, which is at {head} now"


def _record(
    conn: sqlite3.Connection,
    audit: AuditLog,
    item: WorkItem,
    state: str,
    reason: str,
    *,
    worktree_removed: bool = False,
    branch_deleted: bool = False,
) -> Decision:
    with db.transaction(conn):
        db.record_cleanup(conn, item.id, state=state, reason=reason)
    return Decision(
        item_id=item.id,
        state=state,
        reason=reason,
        worktree_removed=worktree_removed,
        branch_deleted=branch_deleted,
    )


def _retain(
    conn: sqlite3.Connection,
    audit: AuditLog,
    item: WorkItem,
    state: str,
    reason: str,
    *,
    worktree_removed: bool = False,
) -> Decision:
    """Record a refusal loudly enough that "why is this 499 MB still here?" is answerable
    from the log alone, months later, without re-running anything.

    Both retentions come through here — a worktree git would not remove and a branch whose
    containment could not be proved — because both leave something on disk that the author
    will eventually ask about, and a record that covers only one of them answers the
    question only half the time.
    """
    audit.record(
        "cleanup.retained",
        outcome="ok",
        entity_type="work_item",
        entity_id=item.id,
        detail={
            "state": state,
            "reason": reason,
            "worktree": item.worktree_path,
            "branch": item.branch,
        },
        dry_run=item.dry_run,
    )
    return _record(conn, audit, item, state, reason, worktree_removed=worktree_removed)


def sweep(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    item_id: int | None = None,
    include_simulated: bool = True,
) -> list[Decision]:
    """Consider every eligible item, or one named item, under the same guards (FR-029).

    ``item_id`` also reconsiders an item the automatic pass has finished with — a
    ``retained`` decision is a decision, and the explicit command is what reconsiders it.
    """
    if item_id is not None:
        item = db.get_work_item(conn, item_id)
        if item is None:
            raise LookupError(f"no work item {item_id}")
        candidates = [item]
    else:
        candidates = db.list_cleanup_candidates(conn, include_simulated=include_simulated)

    return [
        clean_item(
            conn,
            boundaries=boundaries,
            audit=audit,
            config=config,
            item=item,
            # Naming an item is the act of reconsidering it. ``retained`` is a decision the
            # automatic pass will not revisit, and this command is what revisits it.
            reconsider=item_id is not None,
        )
        for item in candidates
    ]
