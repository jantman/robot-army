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

from robot_army import db
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
    * ``not_onboarded`` and ``preparation_failed`` come last because they are conditions of
      the item rather than of the queue — they would hold it on an empty machine too.
    """

    PAUSED = "paused"
    CAPACITY_UNOBSERVABLE = "capacity_unobservable"
    GLOBAL_CAP = "global_cap"
    REPO_CAP = "repo_cap"
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
    items = sorted(
        db.list_work_items(conn, include_simulated=True, states=[WorkItemState.READY]),
        key=lambda item: order_key(item, config.repos.get(item.repo_key), config.dispatch.order),
    )
    control = db.get_dispatch_control(conn)

    entries: list[QueueEntry] = []
    for position, item in enumerate(items, start=1):
        hold, detail = _hold_for(
            item,
            config=config,
            capacity=capacity,
            paused=control.paused,
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
    # What is left is the case the schema cannot prevent: the repository was onboarded, and
    # then removed from the configuration file. ``dispatch_item`` already fails such an item;
    # reporting it here means the author sees it in the queue instead of after an attempt.
    if item.repo_key not in config.repos:
        return (
            HoldReason.NOT_ONBOARDED,
            f"repository {item.repo_key!r} is no longer in the config — "
            "re-add its [repos.*] section or abandon the item",
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
