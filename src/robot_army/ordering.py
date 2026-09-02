"""What is next, where is everything in line, and why is it not moving?

The counterpart to :mod:`robot_army.capacity`: that module observes the machine, this one
applies the author's configuration to what it saw. The dependency runs one way only —
``ordering`` imports ``capacity``, never the reverse — because an order is a policy over an
observation and an observation knows nothing about policy.

:func:`plan` is the **only** producer of dispatch order (R1, R8). ``select_and_dispatch``
walks the list it returns, and ``robot-army status`` and the web queue view render the same
list. That is what makes SC-006 — the item the queue calls next is the item the next
dispatch selects — structural rather than a claim maintained by hand. ``web/pages.py``
previously carried a comment asserting its ordering agreed with the dispatcher's; the
agreement is now identity, so there is nothing left to assert.

Nothing here is stored. The order is a sort key (R7), the position is a list index (R8),
and the hold reason is computed at the moment it is displayed (R9). ``plan`` is a pure
function of the database, the configuration, and a capacity snapshot, and it writes
nothing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from robot_army import db, repos
from robot_army.models import WorkItem
from robot_army.states import WorkItemState

if TYPE_CHECKING:
    from robot_army.capacity import CapacitySnapshot
    from robot_army.config import Config, RepoConfig


class HoldReason(StrEnum):
    """Why an eligible item is not running, in precedence order (R9).

    Declaration order **is** the precedence, so the rule lives in one readable place
    rather than being distributed across the branches that apply it. The first reason that
    applies is the only one reported: two reasons shown at once is how a surface stops
    being read, and FR-013 requires the three main ones to be distinguishable without
    opening the log.

    Each rank is a decision, not an accident:

    * ``paused`` outranks everything because freeing capacity would change nothing. Showing
      a capacity reason to a paused system sends the author to fix the wrong thing (US3 AS4).
    * ``capacity_unobservable`` outranks both caps because when it applies the cap numbers
      are not trustworthy, and showing an untrustworthy number is worse than showing none.
    * ``global_cap`` outranks ``repo_cap`` because the machine-wide limit binds before any
      one repository's does.
    * ``repo_cap`` outranks ``awaiting_merge`` for the same reason one step down: while a
      session is still running there is a slot to free, and sending the author off to merge
      a pull request when what is actually missing is a session slot points at the wrong
      fix. In practice the two hand over rather than overlap — the cap holds while the
      session runs, and this takes over the moment it exits.
    * ``not_onboarded`` and ``preparation_failed`` come last because they are conditions of
      the item rather than of the queue — they would hold it on an empty machine too.
      ``awaiting_merge`` sits above them because it is a condition of the *queue*: an empty
      machine does not clear it.
    """

    PAUSED = "paused"
    CAPACITY_UNOBSERVABLE = "capacity_unobservable"
    GLOBAL_CAP = "global_cap"
    REPO_CAP = "repo_cap"
    AWAITING_MERGE = "awaiting_merge"
    NOT_ONBOARDED = "not_onboarded"
    PREPARATION_FAILED = "preparation_failed"


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One eligible item, where it sits, and why it is or is not moving.

    Nothing here is persisted (FR-019). ``position`` is a list index computed on read, so
    it cannot drift from the order the dispatcher will actually walk — because it *is*
    that order.
    """

    item: WorkItem
    #: 1-based index in the current order. Contiguous, and total across repeated calls.
    position: int
    #: ``None`` means dispatchable right now.
    hold: HoldReason | None
    #: Human-readable specifics for the reason — the repository and its two numbers, the
    #: counts and the cap, what a pause is waiting on. Empty when nothing is held.
    detail: str = ""

    @property
    def dispatchable(self) -> bool:
        return self.hold is None


#: What "unfinished" means for the wait-for-merge gate (milestone 047, FR-005).
#:
#: The complement is the load-bearing half. ``discovered`` and ``ready`` are **excluded**
#: because they are pre-dispatch: an item that has never run has produced no branch, no
#: pull request and no code to land, and counting it would make a repository hold itself —
#: two queued issues, each waiting on the other, forever. ``done`` and ``abandoned`` are
#: excluded because they are terminal, and their exclusion *is* the release mechanism: a
#: merged pull request that says *closes #N* closes the issue, and
#: ``reconcile._resolve_closed_issues`` already turns a closed issue into ``done``.
#:
#: ``failed`` and ``interrupted`` are included. They are non-terminal work in a repository
#: that asked to run one thing at a time, and ``robot-army retry`` and ``robot-army
#: abandon`` are how the author says which.
UNFINISHED_STATES: frozenset[WorkItemState] = frozenset(
    {
        WorkItemState.DISPATCHING,
        WorkItemState.ACTIVE,
        WorkItemState.AWAITING_REVIEW,
        WorkItemState.INTERRUPTED,
        WorkItemState.FAILED,
    }
)


def unfinished_by_repo(conn: sqlite3.Connection) -> dict[str, list[WorkItem]]:
    """``repo_key`` → the items in it that have been dispatched and have not finished.

    One scan, not one per candidate repository (R3). ``plan`` runs on every dispatch tick
    *and* on every web page render, and the module already establishes that a fact needed
    by many items is resolved once for the whole plan rather than per item.

    Simulated rows are included for the reason ``capacity.snapshot`` includes them in its
    own count: a dry run exists to rehearse the real behaviour, and a gate that ignored
    simulated work would rehearse the wrong thing. No outward request is made either way,
    so nothing about dry-run isolation changes.
    """
    grouped: dict[str, list[WorkItem]] = {}
    for item in db.list_work_items(
        conn, include_simulated=True, states=sorted(UNFINISHED_STATES)
    ):
        grouped.setdefault(item.repo_key, []).append(item)
    return grouped


def plan(
    conn: sqlite3.Connection,
    *,
    config: Config,
    capacity: CapacitySnapshot,
) -> list[QueueEntry]:
    """Every eligible item, in dispatch order, with its position and its hold reason.

    Pure: no writes, and no I/O beyond reading the database. That is what lets the web view
    call it on every page render and the dispatcher call it on every pass without the two
    disagreeing — they are not agreeing, they are the same function (R8). SC-006's
    requirement that the item the queue names as next is the item the next dispatch selects
    is structural here rather than a claim maintained by hand.

    ``db.list_work_items``'s ``ORDER BY id`` stays exactly as it is and serves as the
    *stable input* to the sort rather than as the policy itself (R7).
    """
    # Resolved once for the whole plan rather than per item: this function runs on every
    # web page render, and one query beats one per queued item.
    resolved = repos.resolved_all(conn, config)
    # Same reasoning, same shape: one query for the whole plan rather than one per queued
    # item (R3). Computed unconditionally rather than only when some repository has the
    # setting on, because deciding *that* would itself mean walking every repository's
    # configuration, and this is one scan of a table `plan` is already reading.
    unfinished = unfinished_by_repo(conn)
    items = sorted(
        db.list_work_items(conn, include_simulated=True, states=[WorkItemState.READY]),
        key=lambda item: order_key(item, resolved.get(item.repo_key), config.dispatch.order),
    )
    control = db.get_dispatch_control(conn)

    entries: list[QueueEntry] = []
    for position, item in enumerate(items, start=1):
        hold, detail = _hold_for(
            item,
            config=config,
            capacity=capacity,
            paused=control.paused,
            resolved=resolved,
            unfinished=unfinished,
        )
        entries.append(
            QueueEntry(item=item, position=position, hold=hold, detail=detail)
        )
    return entries


def order_key(item: WorkItem, repo: RepoConfig | None, mode: str) -> tuple[Any, ...]:
    """The sort key for one item under one ordering mode (R7).

    Applied in Python rather than in SQL, and that is not a matter of taste. Repository
    priority lives in TOML, not in the database; ordering by it in SQL would mean copying
    configuration into a table and keeping the copy fresh — a second source of truth for a
    value the author edits by hand, to sort a list that is a handful of rows long. The sync
    would be the bug. ``db.list_work_items``'s ``ORDER BY id`` stays exactly as it is and
    becomes the stable *input* to this sort rather than the policy itself.

    Both keys end in ``(discovered_at, id)``, which does three things at once: it gives
    FR-016's "ties broken oldest-first" for free, it makes each key **total** — which is
    what SC-006's hundred consecutive checks require — and it means ``repo-priority``
    degrades to ``oldest-first`` when nothing has been prioritised, which is the harmless
    reading of an unconfigured installation.

    An unrecognised mode cannot reach here: ``config.parse`` refuses to load one (FR-014).
    Falling back here would be a second, silent answer to a question the loader has already
    answered loudly.
    """
    priority = repo.priority if repo is not None else 0
    if mode == "repo-priority":
        return (-priority, item.discovered_at, item.id)
    return (item.discovered_at, item.id)


def _hold_for(
    item: WorkItem,
    *,
    config: Config,
    capacity: CapacitySnapshot,
    paused: bool,
    resolved: dict[str, RepoConfig],
    unfinished: dict[str, list[WorkItem]],
) -> tuple[HoldReason | None, str]:
    """The first applicable reason, in ``HoldReason``'s declaration order (R9).

    Written as a straight sequence of returns rather than as a table, because the order is
    the content: reading it top to bottom is reading the precedence.
    """
    if paused:
        return HoldReason.PAUSED, "dispatch is paused; resume it with `robot-army resume`"

    if not capacity.observable:
        return (
            HoldReason.CAPACITY_UNOBSERVABLE,
            capacity.reason or "the number of live sessions could not be determined",
        )

    if capacity.total >= capacity.global_cap:
        detail = (
            f"{capacity.total} of {capacity.global_cap} sessions running "
            f"({len(capacity.ours)} ours, {capacity.others} other)"
        )
        if capacity.degraded:
            detail += " — counted via /proc, so this is a ceiling rather than a fact"
        return HoldReason.GLOBAL_CAP, detail

    # The first *per-item* reason, and the distinction is the whole of FR-012 and FR-020.
    # Everything above holds the queue; this holds one repository's work and leaves every
    # other repository free to proceed in the same pass.
    running, cap, explicit = repo_capacity(item.repo_key, config=config, capacity=capacity)
    if running >= cap:
        source = "configured" if explicit else "the default"
        return (
            HoldReason.REPO_CAP,
            f"repository {item.repo_key}: {running} of {cap} sessions ({source})",
        )

    # The wait-for-merge gate (milestone 047, FR-005). Per-item like ``repo_cap`` above and
    # for the same reason: it is a statement about one repository, and a queue that stopped
    # on it would let one waiting repository stall every other one.
    #
    # ``item.id`` is excluded so an item can never hold itself. In practice a candidate here
    # is always ``ready``, which is not an unfinished state at all, so this is belt to
    # braces — but the alternative is a rule whose correctness depends on a caller's filter.
    if config.effective_wait_for_merge(item.repo_key)[0]:
        blockers = [
            other for other in unfinished.get(item.repo_key, ()) if other.id != item.id
        ]
        if blockers:
            # The oldest is named rather than all of them: one sentence the author can act
            # on beats a list they have to read, and the oldest is the one whose landing
            # will most likely free the queue.
            first = min(blockers, key=lambda other: other.id)
            more = f" (and {len(blockers) - 1} more)" if len(blockers) > 1 else ""
            return (
                HoldReason.AWAITING_MERGE,
                f"repository {item.repo_key}: #{first.issue_number} is {first.state} and "
                f"has not landed on {config.base_branch_for(item.repo_key)} yet{more}",
            )

    # Milestone 001's gate, reported through this milestone's vocabulary so the two
    # surfaces speak one language. Only one of its three halves is *checkable* here, and
    # the reasons the other two are not are worth stating rather than leaving to be
    # rediscovered:
    #
    # * **Onboarding** is already guaranteed by the schema. ``work_items.repo_key`` is a
    #   foreign key into ``repos``, which has a row only once onboarding happened, and
    #   ``poll.evaluate`` refuses the issue before then. A work item for an un-onboarded
    #   repository cannot exist, so a check for one here would be a branch that can never
    #   be taken — and a hold reason that never appears is worse than none, because it
    #   invites the reader to believe the queue is watching something it is not.
    # * **Trust and the committed-settings fingerprint** read ``~/.claude.json`` and the git
    #   object store. ``plan`` is pure and runs on every web page render, so they stay in
    #   ``dispatch.check_gates`` where they fail closed with the message they always had.
    #
    # What is left is the case the schema cannot prevent: the row exists but no longer
    # resolves to a clone — its onboarding record was deleted, or it predates migration
    # 005 and so was never recorded at a verified location. ``dispatch_item`` already
    # fails such an item; reporting it here means the author sees it in the queue instead
    # of after an attempt.
    if item.repo_key not in resolved:
        return (
            HoldReason.NOT_ONBOARDED,
            f"repository {item.repo_key!r} does not resolve to a clone — run "
            f"`robot-army onboard {item.repo_key} --reapprove`, or abandon the item",
        )

    # Last, because it is not a queueing condition at all: it would hold this item on a
    # completely empty machine, and no amount of freeing capacity changes it.
    residue = item.blocked_reason or item.failure_reason
    if residue:
        return HoldReason.PREPARATION_FAILED, residue

    return None, ""


def repo_capacity(
    repo_key: str, *, config: Config, capacity: CapacitySnapshot
) -> tuple[int, int, bool]:
    """``(running, effective cap, whether the cap was chosen rather than inherited)``.

    Shared with the surfaces so a repository's two numbers are computed once. The third
    element is what lets ``robot-army capacity`` distinguish "you chose 1" from "1 is what
    you get" (US2 AS4), which is the difference between a limit the author can raise in a
    file they already have and one they would have to discover.
    """
    cap, explicit = config.effective_repo_cap(repo_key)
    return capacity.per_repo.get(repo_key, 0), cap, explicit
