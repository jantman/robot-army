"""How full is the machine? One observation, one answer, no policy.

This module observes and nothing else. It reads the live session registry, falls back to
``/proc`` when the registry is unusable, and returns a :class:`CapacitySnapshot` saying how
many worker sessions are running, how many of them the system started, how many are running
in each repository, and **which cap that count is being reported against**. It does not
decide what to do about any of that.

The last of those is issue #30, and it is an observation rather than a setting: the cap in
force is whatever the running daemon is enforcing, which is not necessarily what the file
this process read at startup says. A caller that has read the daemon's heartbeat passes the
cap it published; one that has not gets its own configuration and is told nothing, because
there is then nothing to tell.

The split from :mod:`robot_army.ordering` is the plan's Structure Decision, and it is a
split by *input*: this module's input is the machine, ``ordering``'s input is the
configuration. Only ``ordering`` depends on ``capacity``; the dependency never runs the
other way, which is why a surface that wants to say "the machine is full" can import this
without dragging in a queue.

Two properties are load-bearing and neither is a matter of care:

* **The count never errs downward.** Every unresolved doubt resolves upward, and a doubt
  that cannot be resolved at all sets ``observable=False`` so dispatch is withheld
  entirely (R4, FR-007). An under-count is the only capacity error that causes harm: it
  oversubscribes the author's own subscription while claiming to protect it.
* **No handle to a session the system did not start is ever carried** (R5, FR-006).
  ``ours`` holds session ids; ``others`` is a bare integer. FR-006's prohibition on
  signalling, terminating, resuming, or attaching to an out-of-band session is kept by
  making the handle unavailable rather than by remembering not to use it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from robot_army import db, sessions
from robot_army.models import Session
from robot_army.states import SessionState

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config

#: The anomaly and audit action raised when neither the registry nor ``/proc`` can be
#: read. Named once so the log, the anomaly table, and the tests all spell it the same.
UNOBSERVABLE = "capacity_unobservable"


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    """How full the machine is, right now (data-model.md).

    Never stored. A stored copy is a second source of truth that can disagree with the
    dispatcher, and the whole point of this milestone is that it cannot.

    ``ours`` carries session ids and ``others`` carries only a count, and that asymmetry is
    the whole of FR-006 (R5): the system may *notice* the author's own sessions and may not
    *touch* them. Since no control path can obtain a pid, a handle, or even a session id for
    an out-of-band session, no control path can signal, terminate, resume, or attach to one.
    The rule is kept by the type rather than by an audit of every call site.
    """

    #: ``False`` withholds dispatch entirely (R4, FR-007). Not "assume it is empty".
    observable: bool
    #: Counted via ``/proc`` because the registry was unusable. Session ids are
    #: unavailable on that path, so ``ours`` is empty and ``total`` over-counts by at most
    #: the number of our own live sessions — the safe direction, announced on every surface.
    degraded: bool
    #: Every live worker session on the machine, ours and the author's alike.
    total: int
    #: Session ids of sessions this system started.
    ours: tuple[str, ...]
    #: How many sessions are running that this system did not start. A count, never a handle.
    others: int
    #: **The cap in force**: the running daemon's published cap when one could be learned,
    #: and this process's own configured cap otherwise. Every fraction rendered from this
    #: snapshot, ``at_capacity``, and every ``ordering.plan`` built on it use it, so a
    #: surface cannot show one number and explain itself with another (issue #30).
    global_cap: int
    #: This process's own configured cap, **present only when it differs from the cap in
    #: force**. ``None`` means there is nothing to report — either the two agree, or no
    #: enforced cap could be learned. Its presence *is* the disagreement, so no consumer
    #: compares two integers and no two consumers can disagree about the answer.
    configured_cap: int | None = None
    #: ``repo_key`` → live sessions in that repository. Only repositories the system
    #: started something in appear: an out-of-band session is not attributable to a
    #: repository, because the author's own clone is not under the worktree root.
    per_repo: dict[str, int] = field(default_factory=dict)
    #: Why observation failed, when it did. ``None`` whenever ``observable`` is true.
    reason: str | None = None

    @property
    def at_capacity(self) -> bool:
        return self.total >= self.global_cap

    @property
    def cap_disagreement(self) -> str | None:
        """The one sentence about a stale cap, or ``None`` (issue #30).

        Built here, once, and rendered verbatim by the terminal and the web. Two surfaces
        each composing this from two integers is how they come to word it differently, or
        to disagree about when there is anything to say — which is the class of defect this
        whole feature exists to remove.

        **It does not say which of the two processes is stale, because it cannot.** Both
        directions are reachable and their remedies are opposite: a long-running interface
        in front of a restarted daemon needs the interface restarted, while an edited file
        and a daemon nobody restarted needs the daemon. Nothing here can tell those apart —
        neither process knows when the other read its configuration — and a confident wrong
        instruction would send the reader to restart the process that was already right.
        """
        if self.configured_cap is None:
            return None
        return (
            f"SESSION CAP MISMATCH: the running daemon is enforcing a cap of "
            f"{self.global_cap}, and this process is configured for {self.configured_cap}. "
            "The cap shown is the daemon's, because the daemon is what enforces it. One of "
            "the two has been running since before the configuration changed — restart that "
            "one and they will agree."
        )

    def describe(self) -> str:
        """One line, for a terminal summary or the web chrome."""
        if not self.observable:
            return f"capacity unobservable: {self.reason}"
        parts = [
            f"{self.total}/{self.global_cap} sessions",
            f"{len(self.ours)} ours",
            f"{self.others} other",
        ]
        if self.degraded:
            parts.append("degraded (/proc)")
        line = ", ".join(parts)
        # Appended rather than kept for a second line, because every caller of this prints
        # it as one: a reader who sees the fraction has seen the reason it is not the one
        # in their editor.
        disagreement = self.cap_disagreement
        return f"{line} — {disagreement}" if disagreement else line


def _registry_unusable(scan: sessions.RegistryScan) -> str | None:
    """Why the registry cannot be trusted for a count, or ``None`` if it can.

    Three conditions, and the third is the one milestone 004 added. A *degraded* scan is
    already on the ``/proc`` path. A *missing directory* is the failure that reads as an
    idle machine (R4). An *unrecognised version with no usable entries* is the condition
    ``reconcile.scan_registry`` already degrades on, repeated here rather than shared
    because reconciliation also raises an anomaly for it and a capacity snapshot must not.
    """
    if scan.degraded:
        return "registry scan was already degraded"
    if scan.directory_missing:
        return "registry directory is absent or unreadable"
    if scan.unknown_versions and not scan.entries:
        seen = ", ".join(str(v) for v in scan.unknown_versions)
        return f"registry version(s) not recognised: {seen}"
    return None


def snapshot(
    conn: sqlite3.Connection,
    *,
    config: Config,
    enforced_cap: int | None = None,
    audit: AuditLog | None = None,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
) -> CapacitySnapshot:
    """Observe the machine (contracts/dispatch-policy.md).

    Writes nothing except, when observation fails outright, one audit record and one
    de-duplicated anomaly. ``audit`` is optional because a surface that merely *renders*
    capacity — the web chrome on every page — should not append to the log seventeen
    thousand times a day to say the same thing; the anomaly is raised either way, because
    that is what makes a persistent observation failure visible in
    ``robot-army anomalies`` rather than only to whoever happened to be looking.

    ``enforced_cap`` is the cap the running daemon published, from
    :func:`robot_army.health.published_cap`, and is what the fraction is reported against
    when it is known (issue #30). ``None`` means *no daemon-published cap is available*, and
    covers two callers that want the same thing: a read surface that could not learn one,
    and **the daemon itself**, which must plan against its own configuration rather than
    consult a file it wrote — that would be circular, would put a file read in the dispatch
    path, and would make a safety decision from a value originating outside the process.
    """
    cap, configured_cap = _resolve_cap(config, enforced_cap)
    scan = sessions.scan(registry_dir=registry_dir, proc_root=proc_root)
    degraded_reason = _registry_unusable(scan)
    degraded = degraded_reason is not None

    if degraded:
        scan = sessions.scan_via_proc(
            (Path(config.worker.binary).name,), proc_root=proc_root
        )
        # Zero *worker* processes is a perfectly ordinary state — an idle machine, or one
        # where Claude has never run and so has no registry directory either, which is
        # precisely the pair of conditions a fresh install starts in. Zero pids *of any
        # kind* is a different fact: this process is itself in /proc, so an enumeration
        # that returns nothing did not observe an idle machine, it failed. R4's rule is
        # written against the second reading, and only the second reading is safe to act
        # on: taking the first would leave a fresh install unable to dispatch its first
        # session, forever, for the crime of not having run one yet.
        if not scan.entries and not _proc_enumeration_worked(proc_root):
            return _unobservable(
                conn,
                audit=audit,
                cap=cap,
                configured_cap=configured_cap,
                reason=(
                    f"{degraded_reason}, and /proc enumeration returned no processes at "
                    "all — the enumeration failed rather than the machine being idle"
                ),
            )

    # Ours by working directory, exactly as milestone 001's orphan sweep classifies them.
    # No second classification rule is invented: §10's rule is that a session's working
    # directory decides.
    ours = tuple(
        entry.session_id
        for entry in scan.entries
        if entry.session_id and sessions.under_root(entry.cwd, config.worktree_root)
    )

    # The launch window (R3). Between ``kitty @ launch`` returning and the worker writing
    # its registry file, a dispatch in flight is invisible to the registry — so a
    # registry-only count would offer the same free slot to a second dispatch in the same
    # tick, and FR-009's guarantee would fail in exactly the case it exists for. The union
    # is exact rather than approximate because the session id is generated *before* the
    # process starts, so the join key exists on both sides from the beginning.
    #
    # Simulated rows are included deliberately (FR-004, and FR-055 before it): they burn
    # the same subscription quota, so pretending they are free would make a dry run
    # misleading about the one thing it is meant to rehearse. This is the reasoning
    # ``db.count_live_sessions`` used to carry.
    known = {entry.session_id for entry in scan.entries if entry.session_id}
    live_rows = db.list_sessions(
        conn,
        include_simulated=True,
        states=[SessionState.STARTING, SessionState.RUNNING],
    )
    unmatched = [row for row in live_rows if row.session_id not in known]

    total = len(scan.entries) + len(unmatched)
    others = max(len(scan.entries) - len(ours), 0)

    return CapacitySnapshot(
        observable=True,
        degraded=degraded,
        total=total,
        ours=ours,
        others=others,
        global_cap=cap,
        configured_cap=configured_cap,
        per_repo=_per_repo(conn, live_rows),
        reason=None,
    )


def _resolve_cap(config: Config, enforced_cap: int | None) -> tuple[int, int | None]:
    """Which cap to report against, and the stale one to mention (issue #30).

    One place, so that "is there a disagreement?" is decided once rather than by each
    surface comparing two integers and reaching its own conclusion about when to say so.
    """
    configured = config.daemon.max_concurrent_sessions
    if enforced_cap is None or enforced_cap == configured:
        return configured, None
    return enforced_cap, configured


def _per_repo(conn: sqlite3.Connection, live_rows: list[Session]) -> dict[str, int]:
    """Group our live sessions by the repository their work item belongs to (FR-004).

    Only *our* sessions appear, and that is not an omission. An out-of-band session is not
    attributable to a repository: the author works in their own clone, which is not under
    the worktree root, so there is no repository key to attribute it to. The per-repo cap
    therefore governs what the system starts, while the global cap is what accounts for
    everything running.
    """
    counts: dict[str, int] = {}
    for row in live_rows:
        item = db.get_work_item(conn, row.work_item_id)
        if item is None:
            continue
        counts[item.repo_key] = counts.get(item.repo_key, 0) + 1
    return counts


def _proc_enumeration_worked(proc_root: Path | None) -> bool:
    """Can ``/proc`` be enumerated at all?

    The question the caller actually needs answered is "did the enumeration fail, or is
    the machine genuinely running no workers?", and those are different facts. A ``/proc``
    holding at least one numeric entry answers the second; one holding none can only mean
    the first, because the process asking is itself in there.
    """
    root = Path(proc_root) if proc_root else Path("/proc")
    try:
        return any(entry.name.isdigit() for entry in root.iterdir())
    except OSError:
        return False


def _unobservable(
    conn: sqlite3.Connection,
    *,
    audit: AuditLog | None,
    cap: int,
    configured_cap: int | None,
    reason: str,
) -> CapacitySnapshot:
    """Record the failure and return the snapshot that withholds dispatch (R4, FR-007).

    Every unresolved doubt resolves to "hold". A visible stall is a better failure than an
    invisible over-dispatch, and the stall announces itself through an anomaly the partial
    unique index already de-duplicates, so a 5-second tick cannot turn it into a flood.
    """
    if audit is not None:
        audit.record(
            "capacity.unobservable",
            outcome="error",
            detail={
                "reason": reason,
                "consequence": "dispatch withheld until capacity can be observed",
            },
        )
    with db.transaction(conn):
        db.raise_anomaly(
            conn,
            kind=UNOBSERVABLE,
            entity_type=None,
            entity_id=None,
            detail={"reason": reason},
        )
    return CapacitySnapshot(
        observable=False,
        degraded=True,
        total=0,
        ours=(),
        others=0,
        global_cap=cap,
        # Carried even here. "How full is it?" being unanswerable does not make "what is the
        # limit?" unanswerable, and a reader looking at an unobservable machine is not owed
        # a stale cap on top of it.
        configured_cap=configured_cap,
        per_repo={},
        reason=reason,
    )
