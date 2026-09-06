"""Making recorded state match physical reality.

Runs on startup **before any dispatch** (FR-037) and on a timer. The startup ordering is
not cosmetic: dispatching before reconciling would launch new sessions against a picture
of the world we already know to be stale.

**The subtlety that this whole module exists for**: ``interrupted`` does not mean "nothing
is running." M0 F17 — if the wrapper is killed uncleanly, the worker keeps running,
reparented, while dtach tears down its socket. The daemon then sees no socket and no exit
report and would conclude ``interrupted`` while a real session is still editing files. The
orphan sweep is what catches that, and it is why FR-043 exists.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from robot_army import cleanup, db, intake, notifications, procinfo, repos, sessions, speckit
from robot_army.boundaries import BoundaryError, HostHandle, TransportError
from robot_army.states import (
    SessionState,
    WorkItemState,
    transition_session,
    transition_work_item,
    utcnow,
)

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config
    from robot_army.effects import Boundaries
    from robot_army.models import Session, WorkItem
    from robot_army.paths import Layout


@dataclass(slots=True)
class ReconcileResult:
    checked: int = 0
    interrupted: int = 0
    dispatching_failed: int = 0
    closed_done: int = 0
    reclaimed: int = 0
    skipped_never_real: int = 0
    superseded: int = 0
    orphans: int = 0
    stale_sockets: int = 0
    prunable: int = 0
    transcripts_checked: int = 0
    no_transcript: int = 0
    cleaned: int = 0
    retained: int = 0
    speckit_phase_changes: int = 0
    #: Work items whose pull-request set changed this pass (issue #143). A *change* count
    #: rather than a check count, deliberately: with a 60-second cycle almost every check
    #: finds nothing new, and a number that ticks up every pass would say nothing.
    pull_request_changes: int = 0
    #: Sessions whose worker was ended because its work item is finished (issue #138).
    #: Distinct from ``reclaimed``, which counts rows closed because their process was
    #: *already* gone — the difference is whether this pass did the ending.
    retired: int = 0
    anomalies_resolved: int = 0
    #: Terminal windows closed because the work they were opened for is finished. Every
    #: launch passes ``--hold``, so a window outlives its process by design; nothing closed
    #: one until this counter existed.
    windows_closed: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "interrupted": self.interrupted,
            "dispatching_failed": self.dispatching_failed,
            "closed_done": self.closed_done,
            "reclaimed": self.reclaimed,
            "skipped_never_real": self.skipped_never_real,
            "superseded": self.superseded,
            "orphans": self.orphans,
            "stale_sockets": self.stale_sockets,
            "prunable_worktrees": self.prunable,
            "transcripts_checked": self.transcripts_checked,
            "no_transcript": self.no_transcript,
            "cleaned": self.cleaned,
            "retained": self.retained,
            "speckit_phase_changes": self.speckit_phase_changes,
            "pull_request_changes": self.pull_request_changes,
            "retired": self.retired,
            "anomalies_resolved": self.anomalies_resolved,
            "windows_closed": self.windows_closed,
            "notes": self.notes,
        }


def _age_seconds(timestamp: str | None) -> float:
    if not timestamp:
        return float("inf")
    try:
        stamp = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return float("inf")
    return (datetime.now(UTC) - stamp).total_seconds()


def scan_registry(
    conn: sqlite3.Connection,
    *,
    audit: AuditLog,
    config: Config,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
) -> sessions.RegistryScan:
    """Read the registry, degrading to ``/proc`` when its version is unrecognised.

    The degradation is announced: an unknown version raises a
    ``registry_version_unknown`` anomaly. The partial unique index means the 60-second
    loop cannot turn that into 1,440 rows a day, so raising it unconditionally is safe.
    """
    result = sessions.scan(registry_dir=registry_dir, proc_root=proc_root)
    if result.unknown_versions:
        with db.transaction(conn):
            db.raise_anomaly(
                conn,
                kind="registry_version_unknown",
                entity_type="repo",
                entity_id=None,
                detail={
                    "versions": [str(v) for v in result.unknown_versions],
                    "known": [
                        f"{major}.{minor}.x"
                        for major, minor in sorted(sessions.KNOWN_VERSIONS)
                    ],
                    "note": (
                        "falling back to /proc enumeration; session ids are unavailable "
                        "on that path, so only the orphan sweep can use it"
                    ),
                },
            )
        if not result.entries:
            result = sessions.scan_via_proc(
                (Path(config.worker.binary).name,), proc_root=proc_root
            )
    return result


#: The only two work item states that may legitimately hold an open session row.
#:
#: Derived from the dispatch path, which is the only code that opens one: ``dispatch_item``
#: moves the item to ``dispatching``, inserts the row, and only later moves the session to
#: ``running`` and the item to ``active``. Everything else — ``ready``, ``awaiting_review``,
#: ``interrupted``, ``failed``, ``done``, ``abandoned`` — means no session is in flight.
#:
#: Deliberately an allow-list rather than a deny-list of ``TERMINAL_WORK_ITEM_STATES``.
#: That set is only ``{done, abandoned}``, and issue #28's reported case leaves the item
#: ``interrupted``, which is not terminal — it is resumable. A rule written against the
#: terminal set would miss the very leak it was written for.
SESSION_BEARING_STATES: frozenset[WorkItemState] = frozenset(
    {WorkItemState.DISPATCHING, WorkItemState.ACTIVE}
)


def reclaim_stale_session(
    conn: sqlite3.Connection,
    audit: AuditLog,
    *,
    session: Session,
    scan: sessions.RegistryScan,
    proc_root: Path | None = None,
    reason: str,
) -> str:
    """Decide what one open session row is: legitimate, alive, or a leaked slot (#28).

    Returns ``"left"``, ``"reported"`` or ``"reclaimed"`` — the same shape as
    ``spool.apply_record``, and for the same reason: the caller usually wants to count the
    outcomes rather than re-derive them.

    A session row occupies a global and a per-repository capacity slot for exactly as long
    as it is ``starting`` or ``running``. Nothing closes it but the wrapper's exit record,
    and a **simulated** session has no wrapper and no process, so for one of those the
    record can never arrive. The row then holds its slot forever, and the queue stops
    dispatching with ``repo_cap`` as its reason — which reads as the cap working correctly.

    **The middle branch is the one that matters.** ``interrupted`` has never meant "nothing
    is running" (M0 F17): a worker whose wrapper died keeps going, reparented. Closing that
    row would make the reported capacity *lower* than the number of live workers, which
    oversubscribes the one subscription the cap exists to protect. An under-count is the
    only capacity error that causes harm, so a row whose worker can be seen is reported as
    an orphan and left exactly where it is.

    The caller owns the transaction, exactly as ``transition_session`` requires, so the
    state change and its audit record commit together with whatever else the caller is
    doing.

    ``reason`` names the route — cancellation, abandonment, or the sweep — because that is
    the difference between "the maintainer stopped this" and "this was found stale later",
    and the log is the only place that distinction survives.
    """
    if session.state not in (SessionState.STARTING, SessionState.RUNNING):
        return "left"

    item = db.get_work_item(conn, session.work_item_id)
    if item is not None and item.state in SESSION_BEARING_STATES:
        return "left"
    # An open row whose work item is gone still holds a global slot and nothing else can
    # ever close it, so it is stale by the same argument.
    item_state = str(item.state) if item is not None else "absent"

    entry = scan.find(session.session_id)
    if entry is not None and entry.alive(proc_root=proc_root):
        db.raise_anomaly(
            conn,
            kind="orphan_session",
            entity_type="session",
            entity_id=session.session_id,
            detail={
                "pid": entry.pid,
                "cwd": entry.cwd,
                "proc_start": entry.proc_start,
                "work_item_id": session.work_item_id,
                "work_item_state": item_state,
                "note": (
                    "a live worker under a work item that is no longer running one. Its "
                    "session row is left open on purpose: the slot really is taken, and "
                    "reporting a count lower than the number of live workers would "
                    "oversubscribe the very quota the cap protects"
                ),
            },
        )
        return "reported"

    transition_session(
        conn,
        audit,
        session_row_id=session.id,
        target=SessionState.LOST,
        reason=f"{reason} (work item is {item_state}, so no session is in flight)",
    )
    return "reclaimed"




def _sweep_superseded_sessions(
    conn: sqlite3.Connection,
    *,
    audit: AuditLog,
    item: WorkItem,
    current: Session,
    scan: sessions.RegistryScan,
    claimed_pids: set[int],
    proc_root: Path | None,
) -> int:
    """Open session rows an ``active`` item owns that are **not** its current attempt (#33).

    Resuming or restarting an item opens a second row without closing the first. Nothing
    visited that first row: the sweep above reads only ``latest_session_for_item``, and
    ``_orphan_sweep`` passes over any worker whose row still says ``running`` -- which it
    does precisely because nothing visits it. Each blind spot held the other up.

    ``_sweep_stale_sessions`` (#28) does not reach these either, and deliberately so: its
    rule is that an open row is legitimate while its item is ``dispatching`` or ``active``,
    and here the item *is* ``active``. That rule is right for the question #28 asked -- is
    this row's item still running something? -- and simply does not ask whether the item is
    running *this* row. Measured: with the current attempt alive, an earlier attempt's live
    worker produces no anomaly from any sweep.

    **The middle branch is the one that matters**, for the same reason it did in #28. A
    worker that can be seen is left open and reported, never closed: reporting fewer running
    sessions than exist would oversubscribe the one subscription the cap protects, and an
    under-count is the only direction of capacity error that does real harm.

    The caller owns no transaction here -- each row is decided and committed independently,
    so a pass killed midway leaves the rows it reached settled and the rest for next time.
    """
    acted = 0
    for other in db.list_sessions_for_item(conn, item.id):
        if other.id == current.id:
            continue
        if other.state not in (SessionState.STARTING, SessionState.RUNNING):
            continue

        entry = scan.find(other.session_id)
        if entry is not None and entry.alive(proc_root=proc_root):
            # Claimed so ``_orphan_sweep`` does not report it a second time, and left open
            # because the slot it holds really is taken.
            claimed_pids.add(entry.pid)
            with db.transaction(conn):
                created = db.raise_anomaly(
                    conn,
                    kind="orphan_session",
                    entity_type="session",
                    entity_id=other.session_id,
                    detail={
                        "pid": entry.pid,
                        "cwd": entry.cwd,
                        "proc_start": entry.proc_start,
                        "work_item_id": item.id,
                        "attempt": other.attempt,
                        "current_attempt": current.attempt,
                        "note": (
                            "a live worker from an attempt this item has already replaced. "
                            "Its session row is left open on purpose: the slot really is "
                            "taken, and reporting a count lower than the number of live "
                            "workers would oversubscribe the very quota the cap protects"
                        ),
                    },
                )
            if created:
                acted += 1
            continue

        if not other.pid:
            # It never had a process, so its absence is not evidence of anything -- the
            # same rule the current attempt is judged by, applied to a superseded one.
            continue

        with db.transaction(conn):
            transition_session(
                conn,
                audit,
                session_row_id=other.id,
                target=SessionState.LOST,
                reason=(
                    f"attempt {other.attempt} was superseded by attempt {current.attempt} "
                    "and its process is gone"
                ),
            )
        acted += 1
    return acted


def reconcile(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    layout: Layout,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
) -> ReconcileResult:
    """One full pass. Never raises for an operational condition; records and continues."""
    result = ReconcileResult()
    scan = scan_registry(
        conn, audit=audit, config=config, registry_dir=registry_dir, proc_root=proc_root
    )
    by_session = scan.by_session_id()
    claimed_pids: set[int] = set()

    # -- active items: is the session really there? (FR-038, FR-040) -------
    active = db.list_work_items(
        conn, include_simulated=True, states=[WorkItemState.ACTIVE]
    )
    for item in active:
        result.checked += 1
        session = db.latest_session_for_item(conn, item.id)
        if session is None:
            _interrupt(conn, audit, item.id, "active item has no session row at all")
            result.interrupted += 1
            continue

        # Before judging the current attempt, settle any the item has already replaced.
        # Placed here rather than as a pass of its own so it runs before #28's sweep sees
        # these rows, which is what keeps one worker to one report (C5).
        result.superseded += _sweep_superseded_sessions(
            conn,
            audit=audit,
            item=item,
            current=session,
            scan=scan,
            claimed_pids=claimed_pids,
            proc_root=proc_root,
        )

        entry = by_session.get(session.session_id)
        alive = entry is not None and entry.alive(proc_root=proc_root)
        if alive and entry is not None:
            claimed_pids.add(entry.pid)
            continue

        # Not alive. Before concluding, check whether the session's own row already knows
        # it exited — a spool record applied earlier in this same tick, say.
        if session.state in (SessionState.EXITED_CLEAN, SessionState.EXITED_ERROR):
            continue

        if not session.pid:
            # A session that never had a process has none to be alive, so there is nothing
            # to reconcile it against and marking it interrupted would be a lie about a
            # machine that was never asked to do anything (FR-055).
            #
            # The question is **"did this session have a host?"**, not "was the effect level
            # live". Those are different, and conflating them is issue #33: `dry_run` means
            # the level was below `live`, which is true at `no-remote` — where the session
            # host is real and the pid below is a real process. Keying the skip on `dry_run`
            # therefore switched the whole sweep off at the one level the quickstart
            # recommends for rehearsing with real sessions.
            #
            # The pid answers the real question without anyone having to remember to ask it
            # correctly: it is written from whatever `SessionHost.confirm_session()` returned,
            # and `SimulatedSessionHost` returns 0 by construction. `NULL` and `0` mean the
            # same thing here — no process was ever recorded — and both are falsey.
            #
            # Deliberately derived from the record rather than from the effect level:
            # reconciliation must never consult that (FR-053), and
            # `test_only_effects_py_knows_the_effect_level_exists` greps this file's text --
            # comments included -- so even naming the type here fails the suite.
            result.skipped_never_real += 1
            continue

        with db.transaction(conn):
            transition_session(
                conn,
                audit,
                session_row_id=session.id,
                target=SessionState.LOST,
                reason="reconciliation found no live process and no exit record",
            )
        _interrupt(
            conn,
            audit,
            item.id,
            (
                f"session {session.session_id} is not alive (pid={session.pid}, "
                f"proc_start={session.proc_start}) and no exit record ever arrived"
            ),
        )
        result.interrupted += 1

    # -- dispatching items past their max age (FR-041) ---------------------
    max_age = config.daemon.dispatching_max_age_seconds
    for item in db.list_work_items(
        conn, include_simulated=True, states=[WorkItemState.DISPATCHING]
    ):
        age = _age_seconds(item.dispatching_at)
        if age <= max_age:
            continue
        # An item with no usable `dispatching_at` is infinitely old by construction —
        # failing closed, because an item we cannot date is one we cannot vouch for.
        age_text = "an unknown time" if age == float("inf") else f"{int(age)}s"
        reason = (
            f"stuck in dispatching for {age_text}, past the {max_age}s limit. "
            "Preparation output, if any, is recorded on the item"
        )
        with db.transaction(conn):
            transition_work_item(
                conn,
                audit,
                item_id=item.id,
                target=WorkItemState.FAILED,
                reason=reason,
                extra_columns={"failure_reason": reason},
            )
            db.raise_anomaly(
                conn,
                kind="dispatching_timeout",
                entity_type="work_item",
                entity_id=str(item.id),
                detail={
                    "age_s": None if age == float("inf") else int(age),
                    "max_age_s": max_age,
                    "worktree_path": item.worktree_path,
                    "prepare_output": (item.prepare_output or "")[:4000],
                },
            )
        # Outside the transaction (R14).
        notifications.emit(
            boundaries=boundaries,
            audit=audit,
            config=config,
            kind="failure",
            item_id=item.id,
            repo_key=item.repo_key,
            title=f"robot-army: item {item.id} failed to dispatch",
            detail=reason,
            url=item.source_url,
        )
        result.dispatching_failed += 1

    # -- what pull requests each item has (issue #143) ---------------------
    #
    # **Before** ``_resolve_closed_issues``, and the order is load-bearing. The ordinary
    # successful ending is *pull request merges → issue closes → item goes done*. Refreshing
    # first means the pass that retires an item has already recorded its pull request as
    # ``merged``; refreshing after would freeze it at ``open``, and a done item is only
    # re-checked by the narrow second clause in ``_refresh_pull_requests`` — so the page
    # would carry a stale ``open`` for the whole of the following minute for no reason.
    result.pull_request_changes += _refresh_pull_requests(
        conn, boundaries=boundaries, audit=audit
    )

    # -- issues that have been closed (FR-035, FR-042) ---------------------
    result.closed_done += _resolve_closed_issues(
        conn, boundaries=boundaries, audit=audit, config=config
    )

    # -- finished work that is still running a session (issue #138) --------
    #
    # Positioned deliberately, and all three halves are load-bearing.
    #
    # *After* ``_resolve_closed_issues``, because that pass is what produces the ``done``
    # items this one acts on — so merging a pull request takes effect one tick later rather
    # than two, and so ``done`` carries the meaning this sweep depends on.
    #
    # *Before* the cleanup block below, because cleanup's session guard is what records
    # ``skipped``. Retiring first means a finished item's worktree is reclaimed in **this**
    # pass rather than the next, instead of being reported "not yet" forever.
    #
    # *Before* ``_sweep_stale_sessions``, which is what makes "no anomaly for the ordinary
    # successful path" reachable. That sweep reaches a row this one has already closed, sees
    # a state that is neither ``starting`` nor ``running``, and leaves it — so the
    # ``orphan_session`` is never *reached*, rather than being suppressed by a flag saying
    # "this one was retired". There is a second, independent reason it cannot fire there:
    # ``scan`` is a snapshot but ``RegistryEntry.alive()`` re-reads ``/proc`` at call time,
    # so even an open row would take the reclaim branch.
    #
    # **This paragraph used to claim the ordering made that property "free", and it was
    # wrong** (issue #149). The ordering is necessary and was never sufficient: it only
    # helps on a pass where retirement actually *acts*, and under the single 30-minute idle
    # gate it never could — the maintainer merges within minutes of the worker going quiet,
    # so ``done`` always arrived inside the quiet period and the sweep below reached a row
    # this one had declined to close. Every ordinary completion raised the anomaly for ~29
    # minutes. What makes the property hold is this ordering *plus* ``_retire_signal``
    # acting on the merge, on the same pass the item reaches ``done``.
    #
    # ``_orphan_sweep`` needed the same property and did **not** have it — it read the
    # snapshot directly — so it raised an anomaly against every worker this sweep retired.
    # Review of PR #140 caught it. It now re-checks liveness itself; see its docstring.
    result.retired += _retire_finished_sessions(
        conn, boundaries=boundaries, audit=audit, scan=scan, proc_root=proc_root
    )

    # -- reclaiming the disk of finished work (milestone 004, R10) ---------
    #
    # Immediately after ``_resolve_closed_issues`` and in the same pass, because that pass
    # already asks the exact question cleanup needs — is the issue closed? — and a daemon
    # job of its own would re-ask it on a different clock. Running as a *pass* rather than
    # as a side effect of the ``done`` transition also means items that finished before
    # cleanup was enabled are picked up on the next pass, with no backfill command.
    if config.cleanup.on_issue_close:
        decisions = _cleanup_worktrees(conn, boundaries=boundaries, audit=audit, config=config)
        result.cleaned += sum(1 for d in decisions if d.state == cleanup.DONE)
        result.retained += sum(
            1 for d in decisions if d.state in (cleanup.RETAINED, cleanup.BRANCH_RETAINED)
        )

    # -- session rows that outlived their work item (#28) ------------------
    #
    # Positioned deliberately. *After* the active-item sweep and the closed-issue pass, so
    # every item is seen in the state this pass has already settled it into and no row they
    # closed is examined twice. *Before* the orphan sweep, whose inputs are left exactly as
    # they were — this feature adds a caller of the anomaly, not a change to that sweep.
    result.reclaimed += _sweep_stale_sessions(
        conn, audit=audit, scan=scan, proc_root=proc_root
    )

    # -- transcripts that never appeared (issue #58) ------------------------
    #
    # Here rather than in dispatch, which is the whole point: the worker has not written its
    # transcript a second after exec, so asking there reported every healthy session. Placed
    # with the other session-focused sweeps and after everything that could have settled a
    # session's state this tick, so what is recorded describes the session as this pass
    # leaves it.
    checked, reported = _sweep_transcripts(conn, audit=audit)
    result.transcripts_checked += checked
    result.no_transcript += reported

    # -- the orphan sweep (FR-043, M0 F17) ---------------------------------
    result.orphans += _orphan_sweep(
        conn,
        audit=audit,
        config=config,
        scan=scan,
        claimed_pids=claimed_pids,
        proc_root=proc_root,
    )

    # -- anomalies that have resolved themselves (issue #138) --------------
    #
    # Last among the detectors, after the sweep that raises this kind, so what it leaves
    # describes the pass as it ends — the same argument `_sweep_transcripts` carries for its
    # own position. The two cannot fight: `_orphan_sweep` raises only for processes it can
    # see alive, and this resolves only ones it can see gone.
    result.anomalies_resolved += _resolve_orphan_anomalies(
        conn, audit=audit, proc_root=proc_root
    )

    # -- how far Spec Kit runs have got (milestone 007, FR-012) ------------
    result.speckit_phase_changes += _observe_speckit(conn, audit=audit)

    # -- stale sockets and prunable worktrees (FR-044) ---------------------
    result.stale_sockets += _sweep_sockets(conn, boundaries=boundaries, audit=audit, layout=layout)

    # -- terminal windows outliving the work they were opened for (#138) ---
    #
    # With the other physical-residue sweeps, because it does the same kind of job: look at
    # something outside the database, decide what is left over, and clean it up.
    #
    # After ``_retire_finished_sessions`` **and** ``_sweep_stale_sessions``, and both halves
    # matter: a session retired earlier in this pass has had its row closed by then, so its
    # item qualifies immediately and its window goes in the *same* pass rather than the
    # next. Ordering is also what keeps the guard honest in the other direction — an item
    # whose worker survived termination still has an open row here, so its windows stay.
    result.windows_closed += _close_finished_windows(
        conn, boundaries=boundaries, audit=audit
    )

    result.prunable += _sweep_worktrees(conn, boundaries=boundaries, audit=audit, config=config)

    audit.record(
        "reconcile.pass",
        outcome="ok",
        detail={**result.summary(), **sessions.summarise(scan, config.worktree_root)},
    )
    return result


def _observe_speckit(conn: sqlite3.Connection, *, audit: AuditLog) -> int:
    """Re-derive each running item's lifecycle phase from its worktree.

    Here rather than in the poll loop because this module's stated job is making recorded
    state match physical reality, and a phase read from files is exactly that — it has
    nothing to do with GitHub's clock or GitHub's availability.

    ``awaiting_review`` is included so the last stage a session reached is observed *after*
    it exits, rather than frozen at whatever the final cycle happened to catch. Terminal
    states are not observed at all: by then the recorded phase is history.

    **No record is written for a cycle in which nothing changed.** That is the omission the
    plan enumerates under Principle III: with a 60-second cycle and sessions that run for
    hours, the alternative is a log in which almost every line says a phase did not change,
    and every transition is still recorded with its time. The count below is what appears in
    the pass summary.
    """
    changed = 0
    for item in db.list_work_items(
        conn,
        include_simulated=True,
        states=[WorkItemState.ACTIVE, WorkItemState.AWAITING_REVIEW],
    ):
        if speckit.record_phase(conn, audit, item) is not None:
            changed += 1
    return changed


def _cleanup_worktrees(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
) -> list[cleanup.Decision]:
    """Reclaim what finished work left on disk, under both of ``cleanup``'s guards.

    A thin wrapper on purpose: the guards, the outcomes, and the records live in
    ``cleanup.py``, and ``robot-army cleanup`` calls the same function so the manual path
    cannot drift from the automatic one (FR-029).
    """
    return cleanup.sweep(conn, boundaries=boundaries, audit=audit, config=config)


def _interrupt(conn: sqlite3.Connection, audit: AuditLog, item_id: int, reason: str) -> None:
    """Move an item to ``interrupted``.

    Deliberately **not** an error-level record: a reboot legitimately produces one of
    these for every session that was running, and treating that as an error would train
    the maintainer to ignore errors.
    """
    with db.transaction(conn):
        transition_work_item(
            conn,
            audit,
            item_id=item_id,
            target=WorkItemState.INTERRUPTED,
            reason=reason,
        )


def _refresh_pull_requests(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
) -> int:
    """Record what pull requests each work item has, so no page render has to ask (#143).

    Every surface of this feature reads a stored answer. That is what makes rendering free
    of GitHub entirely — and, less obviously, what makes the answers agree with each other:
    before this, the resume-decision block asked GitHub live while a page rendered, and
    nothing stopped it disagreeing with anything else on the screen.

    **Which items are asked about** is one SQL question, in
    ``db.list_pull_request_candidates`` — *can this item's answer still change?* Its
    docstring carries the three clauses and why each runs out on its own, which is what lets
    this feature have no interval, no cap and no configuration key.

    **Nothing before migration 013 is backfilled.** An item that finished before this
    existed keeps a ``NULL`` column and reads as "not checked", which is what it is. The
    alternative is one GraphQL call per item of history, in a single pass, to re-litigate
    finished work.

    **No per-pass cache**, unlike ``_resolve_closed_issues`` beneath. It would never hit:
    ``idx_work_items_identity`` is unique on ``(source, source_id, dry_run)`` and
    ``source_id`` is ``repo#issue``, so two non-simulated items cannot share an issue — and
    simulated ones are excluded by the query. A cache that cannot hit is a claim no test can
    back.
    """
    changed = 0
    for item in db.list_pull_request_candidates(conn):
        try:
            found = boundaries.issue_reader.pull_requests_for(
                item.repo_key, item.issue_number, item.branch
            )
        except TransportError as exc:
            # "I could not ask" is not "there are none". Neither column is written, so the
            # stored answer stands and its age keeps growing — which is the truth, and which
            # the interface shows. Recorded and moved on: one unreachable repository must
            # not hide every other item's pull request.
            audit.error(
                "reconcile.pull_requests_check",
                error=exc,
                entity_type="work_item",
                entity_id=item.id,
            )
            continue
        if _record_pull_requests(conn, audit, item, found):
            changed += 1
    return changed


def _record_pull_requests(
    conn: sqlite3.Connection,
    audit: AuditLog,
    item: WorkItem,
    found: list[Any],
) -> bool:
    """Store one item's set. Returns whether it changed.

    An unchanged set advances ``pull_requests_at`` and writes **no** audit record. That is
    the omission the feature plan enumerates under Principle III's exception path: with a
    60-second cycle and sessions that run for hours, recording every unchanged check would
    fill the log with lines saying a pull request did not change. Every transition is still
    recorded with its time, and the column carries the last confirmation. It is the same
    trade ``_observe_speckit`` makes, for the same reason.
    """
    fresh = json.dumps(
        [{"number": pr.number, "url": pr.url, "state": pr.state} for pr in found],
        separators=(",", ":"),
    )
    changed = fresh != item.pull_requests
    with db.transaction(conn):
        if changed:
            # Record first, then write, inside the transaction — ``speckit.record_phase``'s
            # order. The audit log is an append-only file rather than a table, so a rollback
            # cannot unwrite the record: the order is chosen so the failure that *can*
            # happen is a record for a change that did not land, which the next pass corrects
            # by writing the same change again. The other order risks the opposite — a
            # committed change with no record — which Principle III does not tolerate.
            audit.record(
                "work_item.pull_requests",
                outcome="ok",
                entity_type="work_item",
                entity_id=item.id,
                target=f"{item.repo_key}#{item.issue_number}",
                dry_run=item.dry_run,
                detail={
                    "from": _pull_request_summary(item.pull_request_list),
                    "to": [f"{pr.number}:{pr.state}" for pr in found],
                    # ``NULL`` → ``[]`` is a real transition and the only one whose "before"
                    # is not a set at all. Saying so here is what lets the log distinguish
                    # "we first looked and found none" from "the one it had went away".
                    "first_check": item.pull_requests is None,
                },
            )
        db.record_pull_requests(conn, item.id, found=fresh, at=utcnow())
    return changed


def _pull_request_summary(stored: list[dict[str, Any]]) -> list[str]:
    return [f"{pr.get('number')}:{pr.get('state')}" for pr in stored]


def _resolve_closed_issues(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
) -> int:
    """A closed issue makes its item ``done``, whatever the session was doing (FR-035).

    Cached within the pass, because several items can share a repository and the check
    costs a real API call each time.

    Skipped entirely for simulated items (FR-055): they exist to exercise the local
    machinery, and spending rate-limit budget asking GitHub about them would be the
    dry-run mode causing exactly the outward effect it is meant to avoid. That is a
    decision about *a simulated row*, not about which implementation to call — the latter
    lives only in ``effects.py``.
    """
    resolved = 0
    cache: dict[str, bool] = {}
    candidates = db.list_work_items(
        conn,
        include_simulated=True,
        states=[
            WorkItemState.ACTIVE,
            WorkItemState.AWAITING_REVIEW,
            WorkItemState.INTERRUPTED,
        ],
    )
    for item in candidates:
        if item.dry_run:
            continue
        key = f"{item.repo_key}#{item.issue_number}"
        if key not in cache:
            try:
                cache[key] = boundaries.issue_reader.is_closed(
                    item.repo_key, item.issue_number
                )
            except TransportError as exc:
                # "I could not ask" is not "it is open". Record and move on.
                audit.error(
                    "reconcile.issue_closed_check",
                    error=exc,
                    entity_type="work_item",
                    entity_id=item.id,
                )
                cache[key] = False
                continue
        if not cache[key]:
            continue
        with db.transaction(conn):
            transition_work_item(
                conn,
                audit,
                item_id=item.id,
                target=WorkItemState.DONE,
                reason=f"source issue {key} is closed",
            )
        # Outside the transaction (R14). The state change is already committed and already
        # in the log, so a webhook that never answers costs a message, never a pass.
        notifications.emit(
            boundaries=boundaries,
            audit=audit,
            config=config,
            kind="completion",
            item_id=item.id,
            repo_key=item.repo_key,
            title=f"robot-army: {key} is done",
            detail=f"{item.title} — the source issue is closed",
            url=item.source_url,
        )
        # The board's other half (FR-028). Best-effort for the same reason the dispatch
        # side is: the work item is already done, and a board that cannot be written must
        # not undo that or stall the rest of the pass.
        try:
            intake.on_issue_closed(
                conn,
                boundaries=boundaries,
                audit=audit,
                config=config,
                repo_key=item.repo_key,
                issue_number=item.issue_number,
                dry_run=bool(item.dry_run),
            )
        except Exception as exc:  # noqa: BLE001 - the item is done; the board is cosmetic
            audit.error(
                "trello.card.move",
                error=exc,
                entity_type="work_item",
                entity_id=item.id,
                detail={"stage": "moving the card to the done list"},
            )
        resolved += 1
    return resolved


#: How long a finished item's worker must have been idle before we end it (issue #138).
#:
#: A worker never ends itself. It opens the pull request and then sits at a prompt, so the
#: exit record that closes a session row never arrives, the slot is held forever, and the
#: ordinary successful path terminates in an `orphan_session`. This constant is the only
#: thing standing between that fix and ending a session the maintainer is in the middle of
#: using, so it is set from measurement rather than taste.
#:
#: Measured on the two finished sessions that produced #138: idle for 84 and 198 minutes,
#: with the work merged and the issue closed in both cases. 1800s sits far above any pause
#: inside an agent's turn.
#:
#: **This applies to one path only, and issue #149 is why.** It shipped as the whole gate,
#: on the argument that "erring long is nearly free". That was measured against the wrong
#: case. Where the maintainer has *merged the pull request*, waiting is not nearly free: the
#: cost is an `orphan_session`, a held slot and an open tab on **every successful item**,
#: which is the thing #138 was filed about — bounded to half an hour rather than forever,
#: but on exactly the path the whole feature exists to make quiet. `_retire_signal` now
#: takes that path, and this constant governs the other one.
#:
#: Where it still applies — a `done` item with no merged pull request, so an issue closed by
#: hand or as not-planned — the original argument holds unchanged and is the reason the
#: value stays at 1800. There is no explicit "this is complete" there, only the inference
#: from idleness, and the session may be the very thing the maintainer is about to attach
#: to. A threshold too high costs a capacity slot for a while longer. A threshold too low
#: ends a session someone was reading — which destroys nothing: the transcript is untouched,
#: the worktree is untouched, and `claude --resume <id>` brings the whole thing back. That
#: asymmetry is why this is safe to have on with no configuration key to turn it off.
#:
#: A constant rather than configuration, deliberately — one caller, no second use in hand
#: (Principle I), exactly as ``TRANSCRIPT_GRACE_SECONDS`` above. If the value proves wrong,
#: the value changes.
RETIRE_IDLE_SECONDS = 1800


def _retire_signal(item: WorkItem, idle_s: float) -> str | None:
    """What authorises retiring this item's worker now, or ``None`` for "not yet" (#149).

    A helper rather than a compound ``if`` because this is a decision *table*, and a table
    that has to be read out of a loop body is a table nobody checks against its contract.
    Callers have already established that the item is ``done`` and that the worker is idle;
    this answers only "why may it be ended".

    **A merged pull request is the signal, and an idleness timer is the fallback.** Merging
    is the maintainer saying "yes, this is complete" in as many words — a stronger and
    earlier statement than any inference from how long a process has been quiet. From that
    point there is nothing left for the worker to do and nothing in its tab about to be
    read. Where no merge exists the issue was closed by hand or as not-planned, there is no
    explicit acceptance, and ``RETIRE_IDLE_SECONDS`` is still the right guard.

    **No floor on the merged path, and that is arithmetic rather than taste.** Issue #149
    weighed a 60-second one. On the completion it was filed about the worker had been idle
    **47 seconds** when its item reached ``done`` — so a 60-second floor declines on exactly
    the pass that matters, ``_sweep_stale_sessions`` raises the anomaly a few lines later,
    and the reported bug reproduces. A floor low enough to be safe is not a number anyone
    would defend. The case a floor was meant to cover — merging while still reading the
    session — is already covered by retirement destroying nothing: the transcript survives
    and ``claude --resume`` returns, so the cost is a keystroke.

    **What this does not decide is whether the worker is idle at all.** That question is
    settled before this is called and is unchanged by the merge, which is a statement about
    the work rather than an observation about the process. See ``RegistryEntry.idle_for``.
    """
    if item.has_merged_pull_request:
        return "merged_pull_request"
    if idle_s >= RETIRE_IDLE_SECONDS:
        return "quiet_period"
    return None


def _retire_finished_sessions(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    scan: sessions.RegistryScan,
    proc_root: Path | None,
) -> int:
    """End the worker of a finished work item, so the successful path has an ending (#138).

    Nothing in this system used to own the moment when a session's work had been accepted
    and the session should stop. Every part behaved correctly and the whole produced, for
    every successful item, an anomaly plus a capacity slot held for as long as the machine
    stayed up. Three of those were enough to stop dispatch permanently at the shipped cap.

    **The precondition is ``done`` and nothing else, and that is load-bearing.**
    ``_resolve_closed_issues`` is the only thing in the codebase that writes that state, so
    ``done`` already *means* "the source issue was observed closed" — no second API call,
    no column, no matching on a transition's reason. ``tests/unit/test_done_single_writer``
    keeps it true.

    **``done`` is the precondition; what authorises acting on it is one of two signals**
    (issue #149). A merged pull request retires the worker at once; anything else waits out
    ``RETIRE_IDLE_SECONDS``. ``_retire_signal`` holds that decision and the reasoning for it.
    The split exists because the shipped single gate was never crossed by an item finishing
    normally: the worker goes quiet, the maintainer merges within minutes, and ``done``
    therefore arrives *inside* the quiet period every time — so the ordinary successful path
    still ended in an anomaly, a held slot and an open tab, just for half an hour instead of
    for ever. Neither signal weakens the idleness check below, which both must pass.

    ``abandoned`` and ``failed`` items are deliberately untouched. Those are the states
    where the work is *not* finished and the session may be the very thing the maintainer
    is about to attach to; ``robot-army cancel`` is the route out of those.

    Every rule but the last leaves the row exactly as found and writes **nothing at all** —
    not a record, not a column. That silence is deliberate and is this feature's one
    documented Principle III gap: a 60-second loop reporting "still busy" about a session
    someone is using would write ~1,440 records a day carrying one bit, and the condition
    is re-derivable from the registry at any instant. ``_sweep_transcripts`` sets the same
    precedent for the same shape of decision.

    Bounded by the number of open session rows, which the global cap bounds in turn.
    """
    retired = 0
    for session in db.list_sessions(
        conn,
        include_simulated=True,
        states=[SessionState.STARTING, SessionState.RUNNING],
    ):
        item = db.get_work_item(conn, session.work_item_id)
        if item is None or item.state is not WorkItemState.DONE:
            continue

        # No process was ever recorded, so there is nothing to end. A simulated row is
        # `_sweep_stale_sessions`'s business, which closes it without signalling anything;
        # reaching the real session host with `pid=0` is the `killpg(getpgid(0), ...)`
        # hazard `operations.cancel` documents at length. The question is "did this session
        # have a process?", not "was the effect level live" — those differ at `no-remote`,
        # and conflating them is issue #33.
        if not session.pid:
            continue

        entry = scan.find(session.session_id)
        if entry is None or not entry.alive(proc_root=proc_root):
            # Nothing to end. `_sweep_stale_sessions` reclaims the row later in this pass.
            continue

        idle_s = entry.idle_for()
        if idle_s is None:
            # Idleness could not be established: the status is not `idle`, or the timestamp
            # is absent, malformed, or in the future. See `RegistryEntry.idle_for` for why
            # every unknown lands here rather than in the branch below.
            #
            # **This is the branch a merged pull request does not bypass**, and the
            # separation from the duration test below is the whole of issue #149's care.
            # Merging says the *work* is accepted; it says nothing about whether the process
            # is between tool calls. Removing the duration requirement is safe because the
            # transcript survives a retirement; removing this one would end a worker
            # mid-turn.
            continue

        signal = _retire_signal(item, idle_s)
        if signal is None:
            # Idle, but not long enough, and nothing has been merged to say otherwise. "Not
            # yet": the question is asked again next pass and nothing is written (C6).
            continue

        retired += _retire_one(
            conn,
            boundaries=boundaries,
            audit=audit,
            session=session,
            scan=scan,
            proc_root=proc_root,
            idle_s=idle_s,
            signal=signal,
        )
    return retired


def _retire_one(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    session: Session,
    scan: sessions.RegistryScan,
    proc_root: Path | None,
    idle_s: float,
    signal: str,
) -> int:
    """Terminate one finished session and settle its row. Returns 1 if it was retired.

    ``signal`` is which of ``_retire_signal``'s two conditions authorised this, and it is
    recorded rather than derived. With one gate ``idle_s`` implied the reason; with two it
    does not — an ``idle_s`` of 47 is either a merged pull request or a bug, and Principle
    III's standard is that the log alone answers which, without the reader having to know
    which release wrote it.
    """
    detail = {
        "item_id": session.work_item_id,
        "session_id": session.session_id,
        "pid": session.pid,
        "proc_start": session.proc_start,
        "idle_s": int(idle_s),
        "signal": signal,
    }
    # Before the signal, not after. Ending a process cannot be undone from this side, and
    # Principle III puts the burden on the irreversible act being visible even if the
    # daemon dies between this line and the next.
    audit.record("session.retire", outcome="ok", entity_type="session",
                 entity_id=session.session_id, detail=detail, dry_run=bool(session.dry_run))

    # **No host selection here, deliberately.** `operations.cancel` picks between the real
    # and the simulated host by reading the record, and its own guard test says that if a
    # second module ever needs to do that, it is the moment to ask whether the selection
    # belongs back in the wiring. Asked, and the answer is that this sweep does not need it
    # at all: a simulated row is `pid = 0` by construction — that signature is what
    # `SimulatedSessionHost.confirm_session` writes precisely so nothing mistakes it for a
    # process — and the `if not session.pid` guard above has already skipped every one of
    # them. Reaching a simulated host from here is unreachable, and an unreachable branch
    # that selects an implementation is exactly the drift FR-053 exists to prevent.
    #
    # If that guard is ever loosened, the failure is safe rather than silent: `terminate`
    # refuses a recorded pid of 0 outright, sends nothing, and says so — because
    # `getpgid(0)` asks about the *caller*, which is how signalling it would end the daemon.
    handle = HostHandle(
        socket_path=session.host_socket or "",
        argv=(),
        simulated=False,
        pid=session.pid,
    )

    try:
        outcome = boundaries.session_host.terminate(
            handle, session.scope, expected_start=session.proc_start
        )
    except BoundaryError as exc:
        # A pass never raises for an operational condition. The row is untouched, so the
        # next pass tries again — and if the worker outlives every attempt, the row stays
        # open and `_sweep_stale_sessions` reports it as the orphan it is.
        audit.error(
            "session.retire",
            error=exc,
            entity_type="session",
            entity_id=session.session_id,
            detail=detail,
        )
        return 0

    result = {
        **detail,
        "method": outcome.method,
        "confirmed": outcome.confirmed,
        "escalated": outcome.escalated,
    }
    if outcome.refused_reason is not None:
        # The boundary declined to act and sent nothing. Distinct from "it survived", and
        # the message must not imply a signal was sent (069 S-K3).
        audit.record(
            "session.retire_refused",
            outcome="error",
            entity_type="session",
            entity_id=session.session_id,
            detail={**result, "refused_reason": outcome.refused_reason},
            dry_run=bool(session.dry_run),
        )
        return 0
    if not outcome.confirmed:
        # We tried and could not, which is never recorded as "it is gone". Leaving the row
        # open is what keeps the slot honestly subscribed and puts the session in front of
        # `_sweep_stale_sessions`, which raises `orphan_session` for exactly this.
        audit.record(
            "session.retire_unconfirmed",
            outcome="error",
            entity_type="session",
            entity_id=session.session_id,
            detail=result,
            dry_run=bool(session.dry_run),
        )
        return 0

    # **Re-read before settling** (FR-008). The daemon drains the exit spool in its own
    # process while this call is in flight, so a worker killed by our own signal can record
    # its own ending before we get here. Settling the row we read *before* the signal would
    # attempt a transition out of a terminal state and raise, reporting a perfectly
    # successful retirement as a failure. `operations.cancel` and `dispatch.py` both ask
    # the same question at the equivalent moment, for the same reason.
    fresh = db.get_session(conn, session.session_id)
    if fresh is None:
        # The row went away entirely. Nothing holds a slot, which is the outcome wanted.
        audit.record(
            "session.retired",
            outcome="ok",
            entity_type="session",
            entity_id=session.session_id,
            detail={**result, "settled": "row is gone"},
            dry_run=bool(session.dry_run),
        )
        return 1
    with db.transaction(conn):
        settled = reclaim_stale_session(
            conn,
            audit,
            session=fresh,
            scan=scan,
            proc_root=proc_root,
            # Names the condition, not just the clock, so a session row read on its own
            # says why its worker was ended rather than leaving the reader to infer it
            # from a number that no longer carries the answer.
            reason=(
                "retired: the work item is done and its pull request was merged"
                if signal == "merged_pull_request"
                else (
                    f"retired: the work item is done and its worker had been idle for "
                    f"{int(idle_s)}s"
                )
            ),
        )
    audit.record(
        "session.retired",
        outcome="ok",
        entity_type="session",
        entity_id=session.session_id,
        detail={**result, "settled": settled},
        dry_run=bool(session.dry_run),
    )
    # `left` means the row reached a terminal state between the decision and the settle —
    # the daemon drained this session's own exit record in its own process while we were
    # signalling. That is an ordinary outcome of a successful retirement, not a failure,
    # and it is counted as one.
    return 1 if settled in ("reclaimed", "left") else 0


def _sweep_stale_sessions(
    conn: sqlite3.Connection,
    *,
    audit: AuditLog,
    scan: sessions.RegistryScan,
    proc_root: Path | None,
) -> int:
    """Close every session row that outlived the work item it belongs to (#28).

    The invariant the reported bug violates: a row is only ``starting`` or ``running``
    while its item is ``dispatching`` or ``active``. Nothing else in this module could
    reach the leaked rows, because the active-item sweep above iterates items in
    ``active`` and every route that leaks has already moved the item off that list.

    Bounded by the number of open rows, which the global cap bounds in turn — this is not
    a scan of the sessions table's history.
    """
    reclaimed = 0
    for session in db.list_sessions(
        conn,
        include_simulated=True,
        states=[SessionState.STARTING, SessionState.RUNNING],
    ):
        with db.transaction(conn):
            outcome = reclaim_stale_session(
                conn,
                audit,
                session=session,
                scan=scan,
                proc_root=proc_root,
                reason=(
                    "reconciliation found a session row still open under a work item "
                    "that is not running one"
                ),
            )
        if outcome == "reclaimed":
            reclaimed += 1
    return reclaimed


#: How long a session gets to write its transcript before its absence means anything.
#:
#: Issue #58: the check this constant exists for used to run inline in dispatch, one line
#: after the session was confirmed running. The worker writes its transcript when it begins
#: processing, not at exec, so the file reliably did not exist yet and the anomaly fired on
#: every healthy dispatch — including the first live one ever performed.
#:
#: 300s against a single measurement of under eight seconds, on a warm cache, which the
#: report explicitly declines to treat as a bound. Roughly forty times the one observation
#: available, which absorbs a cold cache, a loaded machine, and a worker that takes a while
#: to reach its first write. Erring long is nearly free: a late report concerns a session
#: that is already unrecoverable, while an early one recreates the bug this replaced.
#:
#: A constant rather than configuration, deliberately — one caller, no second use in hand
#: (Principle I). If the value proves wrong, the value changes.
TRANSCRIPT_GRACE_SECONDS = 300

#: What the anomaly says, and the reason it no longer says what it used to.
#:
#: The old note asserted a cause: "check for CLAUDE_CODE_* variables in the terminal
#: daemon's environment". On the machine where it fired, that environment was verifiably
#: clean — `doctor` reported zero such variables — so the guidance led away from the answer
#: rather than toward it (issue #58).
#:
#: This check observes an absence. It genuinely cannot tell a transcript that was never
#: saved from one whose session died before writing it, and pretending otherwise is what
#: made the last note worse than useless. So it names both, names what settles each, and
#: ends with the one instruction that holds either way.
_NO_TRANSCRIPT_NOTE = (
    "no resumable transcript for this session after waiting {waited}. Two causes are "
    "possible and this check cannot tell them apart: the worker never saved one — check "
    "`robot-army doctor` for CLAUDE_CODE_* in the session host's environment — or the "
    "session ended before it wrote one, which its exit record will show. Either way this "
    "session cannot be resumed; restart the item rather than resuming it."
)


def _sweep_transcripts(conn: sqlite3.Connection, *, audit: AuditLog) -> tuple[int, int]:
    """Answer, once per session, whether it left anything resumable behind (issue #58).

    The observation is M0 F19's and has not changed: a session can run, exit 0, and be
    permanently unresumable, and the missing transcript is the only sign. What changed is
    *when* the question is asked. Here it can wait, and the waiting is the whole feature.

    Returns ``(checked, reported)``.

    Three properties worth stating, because each is load-bearing:

    * **The population is the open questions.** ``transcript_checked_at IS NULL`` and
      nothing else — no state filter, no age window. Every session is resolved exactly once
      and then leaves the set permanently, so it bounds itself.
    * **A session younger than the grace period is left exactly as it was found.** That
      branch writes nothing at all, which is what makes the next pass ask again.
    * **The report and the row's answer commit together.** A kill between them would
      otherwise either report twice or mark a session answered that was never reported.
    """
    checked = 0
    reported = 0
    for session in db.sessions_awaiting_transcript_check(conn):
        detail: dict[str, Any] = {
            "session_id": session.session_id,
            "item_id": session.work_item_id,
        }

        # A session with no process never ran, so nothing could have written a transcript
        # and its absence says nothing at all.
        #
        # The question is **"did this session have a process?"**, not "was the effect level
        # live". Those are different, and conflating them is issue #33 — which this module
        # has already corrected once, in the active-item sweep above, for the same reason.
        # `dry_run` means the level was below `live`, which is true at `no-remote`, where
        # the session host is real and the pid below is a real process. Keying on it is what
        # made every rehearsal blind to this detector, so that the first observation of a
        # defect in it was a live dispatch against a real issue (issue #58).
        #
        # The pid answers the real question without anyone having to remember to ask it
        # correctly: it is written from whatever `SessionHost.confirm_session()` returned,
        # and the simulated host returns 0 by construction. `NULL` and `0` mean the same
        # thing here — no process was ever recorded — and both are falsey.
        #
        # Checked before the filesystem is consulted, deliberately: reaching for a file to
        # learn what the record already says would be a slower way to be wrong.
        if not session.pid:
            with db.transaction(conn):
                db.mark_transcript_checked(conn, session.id)
            audit.record(
                "session.transcript_skipped",
                outcome="ok",
                entity_type="session",
                entity_id=session.session_id,
                detail={**detail, "reason": "no process was ever recorded"},
            )
            checked += 1
            continue

        # The clock starts at confirmation, not at insertion: the row is written *before*
        # the process launches, and confirmation can take up to `confirm_timeout_seconds`.
        # Measuring from `started_at` would charge the session for time it did not have.
        # `started_at` is the fallback only because it is NOT NULL and always answers.
        age = _age_seconds(session.confirmed_at or session.started_at)
        waited = None if age == float("inf") else int(age)

        if sessions.transcript_exists(session.session_id):
            with db.transaction(conn):
                db.mark_transcript_checked(conn, session.id)
            audit.record(
                "session.transcript_found",
                outcome="ok",
                entity_type="session",
                entity_id=session.session_id,
                detail={**detail, "waited_s": waited},
            )
            checked += 1
            continue

        if age < TRANSCRIPT_GRACE_SECONDS:
            # Too early to mean anything. Write nothing — not the column, not a record —
            # and ask again next pass. This branch is the bug fix.
            continue

        # The anomaly and the answer commit together or not at all. Split, an interruption
        # between them would either report this session again on every later pass, or mark
        # it answered without ever having reported it.
        with db.transaction(conn):
            db.raise_anomaly(
                conn,
                kind="no_transcript",
                entity_type="session",
                entity_id=session.session_id,
                detail={
                    # Not `**detail`: the anomaly's own `entity_id` is the session id, and
                    # repeating it in the body prints it twice in the terminal.
                    "item_id": session.work_item_id,
                    "waited_s": waited,
                    "session_state": str(session.state),
                    **({"ended_at": session.ended_at} if session.ended_at else {}),
                    "note": _NO_TRANSCRIPT_NOTE.format(
                        waited=f"{waited}s" if waited is not None else "an unknown time"
                    ),
                },
            )
            db.mark_transcript_checked(conn, session.id)
        audit.record(
            "session.transcript_missing",
            outcome="ok",
            entity_type="session",
            entity_id=session.session_id,
            detail={**detail, "waited_s": waited, "session_state": str(session.state)},
        )
        checked += 1
        reported += 1
    return checked, reported


def _orphan_sweep(
    conn: sqlite3.Connection,
    *,
    audit: AuditLog,
    config: Config,
    scan: sessions.RegistryScan,
    claimed_pids: set[int],
    proc_root: Path | None = None,
) -> int:
    """Live worker processes under the worktree root that match no ``active`` row.

    This is the M0 F17 case made visible: the wrapper died, dtach tore down its socket,
    and the worker carried on reparented. Without this sweep the daemon would report
    ``interrupted`` while a real session was still editing files.

    **"Live" is re-established here, not inherited from the scan.** ``scan`` is taken once
    at the top of the pass and filters on liveness *at that moment*; several sweeps run
    between then and here, and one of them — ``_retire_finished_sessions`` — deliberately
    kills processes. Trusting the snapshot therefore raised a fresh ``orphan_session``
    against the very worker this pass had just retired on purpose, on every ordinary
    successful item: none of the three guards below catches it, because the pid was never
    claimed (only ``active`` items claim), the cwd really is under the worktree root, and
    the row is ``lost`` rather than ``running``. The anomaly was then resolved later in the
    same pass by ``_resolve_orphan_anomalies``, so ``robot-army anomalies`` looked clean
    while ``result.orphans`` was inflated and a raise/resolve pair was written to the log
    for every successful retirement.

    The re-check costs one ``/proc`` read per candidate and makes the docstring above true
    rather than aspirational. It cannot suppress a genuine report: an orphan is by
    definition a process that is *running* unaccounted for, so a pid that is gone by the
    time we ask is not one.
    """
    found = 0
    for entry in scan.entries:
        if entry.pid in claimed_pids:
            continue
        if not sessions.under_root(entry.cwd, config.worktree_root):
            continue  # the maintainer's own session; none of our business
        if not entry.alive(proc_root=proc_root):
            continue  # gone since the scan — including anything retired earlier this pass
        row = (
            conn.execute(
                "SELECT id, work_item_id, state FROM sessions WHERE session_id = ?",
                (entry.session_id,),
            ).fetchone()
            if entry.session_id
            else None
        )
        if row is not None and SessionState(row["state"]) is SessionState.RUNNING:
            continue
        with db.transaction(conn):
            created = db.raise_anomaly(
                conn,
                kind="orphan_session",
                entity_type="session",
                entity_id=entry.session_id or f"pid:{entry.pid}",
                detail={
                    "pid": entry.pid,
                    "cwd": entry.cwd,
                    "proc_start": entry.proc_start,
                    "session_id": entry.session_id,
                    "note": (
                        "a live worker process under the worktree root that no active "
                        "work item claims. `interrupted` does not mean nothing is running"
                    ),
                },
            )
        if created:
            found += 1
    return found


def _resolve_orphan_anomalies(
    conn: sqlite3.Connection, *, audit: AuditLog, proc_root: Path | None
) -> int:
    """Close an ``orphan_session`` whose process is no longer there (issue #138).

    ``robot-army anomalies`` is read as a list of things needing attention, and nothing ever
    took a row off it but a maintainer typing ``--acknowledge``. A condition that resolved
    itself therefore stayed on the list forever — the report that prompted this feature
    named pid 498936, which had not existed for hours. The cost is not the row; it is that a
    list which is mostly stale teaches the habit of clearing it without reading it, which is
    how the anomaly that mattered gets acknowledged along with the noise.

    **Only this kind, and only this condition.** ``orphan_session`` is the one anomaly whose
    truth can be positively re-established as false, because both places that raise it write
    the pid and the process start time into ``detail``. Every other kind has its own
    settling story and none of them is guessed at here.

    Identity, not the number: ``procinfo.is_alive`` compares ``/proc/<pid>/stat`` field 22
    against the recorded start time, so a *recycled* pid — the number reused by an unrelated
    process — answers ``False`` and the anomaly resolves, which is correct. The original
    process is what the report was about, and it is gone.

    Positioned after ``_orphan_sweep`` so what it leaves describes the pass as it ends. The
    two cannot fight, because that sweep raises only for processes it can see alive *now*
    and this resolves only ones it can see gone. That symmetry is load-bearing and was not
    free: until PR #140's review, ``_orphan_sweep`` trusted the pass's opening snapshot, so
    every retirement produced a raise here and a resolve immediately after — leaving the
    listing correct while the counters and the log both lied.
    """
    resolved = 0
    for anomaly in db.open_orphan_session_anomalies(conn):
        try:
            detail = anomaly.detail_obj
        except (ValueError, TypeError):
            detail = {}
        pid = detail.get("pid") if isinstance(detail, dict) else None
        if not isinstance(pid, int) or isinstance(pid, bool):
            # No evidence to re-check against. Left alone permanently and deliberately:
            # "we could not check" must never be recorded as "it is fine".
            continue
        proc_start = detail.get("proc_start")
        if procinfo.is_alive(pid, proc_start, root=proc_root):
            continue

        with db.transaction(conn):
            if not db.resolve_anomaly(conn, anomaly.id):
                continue
            audit.record(
                "anomaly.resolved",
                outcome="ok",
                entity_type="anomaly",
                entity_id=str(anomaly.id),
                detail={
                    "kind": anomaly.kind,
                    "anomaly_entity_id": anomaly.entity_id,
                    "pid": pid,
                    "proc_start": proc_start,
                    "reason": (
                        "the process this anomaly named is no longer running, so the "
                        "condition it reported no longer holds"
                    ),
                },
            )
        resolved += 1
    return resolved


def _sweep_sockets(
    conn: sqlite3.Connection, *, boundaries: Boundaries, audit: AuditLog, layout: Layout
) -> int:
    """Detect session sockets whose master is gone, by probing rather than trusting.

    Sockets live under ``XDG_RUNTIME_DIR`` precisely so a reboot clears them for free; the
    ones that survive are from a session that died without cleaning up in this boot.
    """
    from robot_army.boundaries import HostHandle

    stale = 0
    if not layout.socket_dir.is_dir():
        return 0
    live_sockets = {
        s.host_socket
        for s in db.list_sessions(
            conn, include_simulated=True, states=[SessionState.STARTING, SessionState.RUNNING]
        )
        if s.host_socket
    }
    for path in sorted(layout.socket_dir.glob("*.sock")):
        if str(path) in live_sockets:
            continue
        handle = HostHandle(socket_path=str(path), argv=())
        try:
            if boundaries.session_host.is_alive(handle):
                continue
        except BoundaryError as exc:
            audit.error("reconcile.socket_probe", error=exc, detail={"socket": str(path)})
            continue
        try:
            path.unlink()
        except OSError as exc:
            audit.error("reconcile.socket_unlink", error=exc, detail={"socket": str(path)})
            continue
        audit.record(
            "reconcile.stale_socket",
            outcome="ok",
            target=str(path),
            detail={"note": "probe refused; the dtach master is gone"},
        )
        stale += 1
    return stale


#: The user variable every launched window carries, naming the work item it was opened for.
#:
#: Written by ``dispatch.build_launch_plan`` as ``user_vars={"ra_item": str(item_id)}`` and
#: turned into ``--var ra_item=<id>`` by the display. **This is the identity**, and the
#: recorded ``sessions.window_id`` deliberately is not: kitty numbers windows per kitty
#: process and restarts from 1 when kitty restarts, so a stored 50 can name an unrelated
#: window months later. Closing on a stored number would be the PID-reuse incident this
#: project already carries two guards against, aimed at the maintainer's own screen.
#:
#: Nothing else sets this variable, so a window carrying it was opened by us, and a window
#: without it is never ours to close whatever it appears to contain.
WINDOW_ITEM_VAR = "ra_item"

#: Items whose windows this process has already settled — closed, or looked for and found
#: none. Volatile on purpose, and this is the second thing that made the gate below work.
#:
#: Without it the gate is dead after the first item ever finishes. ``done`` is terminal and
#: rows are never deleted, so a completed item satisfies every database condition on **every
#: future pass forever**, long after its window went. The candidate set would therefore never
#: be empty again, ``kitty @ ls`` would run on every pass indefinitely, and a machine with no
#: kitty would collect the ~1,440 failures a day the gate exists to prevent. Caught in review
#: of the pull request that introduced it.
#:
#: Nothing new can appear for a settled item: ``done`` has no outgoing transition, so no
#: dispatch can open it another window. Once a listing has answered for an item, the answer
#: is final.
#:
#: In process memory rather than a column, following the precedent milestone 004 set for the
#: capacity hold and the notifier's cycle counter: losing it costs **one** extra listing after
#: a restart, which is far less than a table costs to keep correct.
#:
#: Keyed on ``(id, done_at)`` rather than the id alone. SQLite reuses a freed ``INTEGER
#: PRIMARY KEY``, so ``purge_simulated`` deleting the highest rows lets a later item inherit
#: a settled id and never have its window closed. The timestamp makes that collision
#: impossible without costing anything.
_WINDOWS_SETTLED: set[tuple[int, str | None]] = set()


def forget_settled_windows() -> None:
    """Drop the volatile window bookkeeping. Exactly what a daemon restart does."""
    _WINDOWS_SETTLED.clear()


def _close_finished_windows(
    conn: sqlite3.Connection, *, boundaries: Boundaries, audit: AuditLog
) -> int:
    """Close the terminal windows of items that are finished (issue #138 follow-up).

    Every window is launched with ``--hold`` so that a launch which fails instantly leaves
    something readable — that window is often the only evidence of what went wrong (M0
    F11). The consequence nobody had acted on is that a window outlives its process *by
    design*: retirement ends the worker and the tab stays, one per completed item, forever.
    ``Display.close`` has existed since M0 and had no caller until this function.

    **The hold's purpose survives, and is preserved by the ``done`` gate rather than by a
    second rule.** A failed launch's item never reaches ``done``, so its window is never a
    candidate. ``failed`` and ``abandoned`` keep their windows indefinitely.

    **The database is asked before the terminal is touched.** When nothing qualifies this
    returns without listing windows at all, which is the ordinary state of an idle machine.
    That is not an optimisation: a sweep that always listed would raise on every pass on a
    machine with no kitty, writing ~1,440 failures a day. Gating on the candidate set makes
    the failure that *is* recorded mean "there was work to do and the terminal could not be
    reached".
    """
    candidates: dict[int, str | None] = {}
    for item in db.list_work_items(
        conn, include_simulated=True, states=[WorkItemState.DONE]
    ):
        sessions_for_item = db.list_sessions_for_item(conn, item.id)
        if not sessions_for_item:
            # A ``done`` item that never had a session — a rebuilt database. Nothing
            # establishes that its session ended, and ``live_sessions`` answers the empty
            # list both for "all of them finished" and for "there were never any". Only the
            # first qualifies, so the two are told apart here rather than conflated.
            continue
        if (item.id, item.done_at) in _WINDOWS_SETTLED:
            # Asked and answered in this process. See `_WINDOWS_SETTLED`: without this the
            # gate below never fires again after the first item ever completes.
            continue
        if cleanup.live_sessions(conn, item.id):
            # Something may still be running in one of this item's windows. The shared
            # definition from issue #79, reused so the window rule cannot drift from the
            # disk rule — including its deliberate choice to check *every* attempt rather
            # than the latest.
            continue
        candidates[item.id] = item.done_at

    if not candidates:
        return 0

    try:
        windows = boundaries.display.list_by_var(WINDOW_ITEM_VAR)
    except BoundaryError as exc:
        audit.error("window.list", error=exc, detail={"candidates": sorted(candidates)})
        # Nothing is settled: the question was not answered, so it is asked again next pass.
        return 0

    closed = 0
    unfinished: set[int] = set()
    for handle in windows:
        raw = handle.user_vars.get(WINDOW_ITEM_VAR)
        try:
            item_id = int(str(raw))
        except (TypeError, ValueError):
            # A marker we cannot read is not evidence. Left alone, permanently.
            continue
        if item_id not in candidates:
            continue
        try:
            really_closed = boundaries.display.close(handle)
        except BoundaryError as exc:
            # One terminal refusing must not abandon the sweep: every other window is still
            # considered, and this one is simply reconsidered next pass — which is what
            # keeping it out of the settled set below arranges.
            unfinished.add(item_id)
            audit.error(
                "window.close",
                error=exc,
                entity_type="work_item",
                entity_id=item_id,
                detail={"window_id": handle.window_id, "title": handle.title},
            )
            continue
        # ``False`` means one specific thing: the terminal had no such window, because the
        # maintainer closed it in the moments between the listing above and this call. That
        # is success — the item is still settled, since nothing will ever need doing for it
        # again — but it is not something *this* pass did, and counting it would overstate
        # the system's own work in the summary and the log.
        #
        # Every *other* way a close can fail raises instead, and is handled above. That
        # split is load-bearing rather than tidy: a transient failure or a timeout reported
        # as ``False`` would settle the item and leak its window for the life of the
        # process, with nothing recorded as an error.
        if really_closed:
            closed += 1

    # Everything the listing answered for is settled, including the candidates that turned
    # out to have no windows at all — that is the answer, and it cannot change.
    _WINDOWS_SETTLED.update(
        (item_id, done_at)
        for item_id, done_at in candidates.items()
        if item_id not in unfinished
    )
    return closed


def _sweep_worktrees(
    conn: sqlite3.Connection, *, boundaries: Boundaries, audit: AuditLog, config: Config
) -> int:
    """Surface checkouts whose directory no longer exists (FR-017, FR-044).

    Reported, never removed. There is no automatic removal in this milestone (FR-016), so
    the correct action here is to make the condition visible and let the maintainer decide.
    """
    flagged = 0
    tracked = [
        item
        for item in db.list_work_items(conn, include_simulated=True)
        if item.worktree_path and item.state not in (WorkItemState.DONE, WorkItemState.ABANDONED)
    ]
    if not tracked:
        return 0

    prunable_by_clone: dict[str, set[str]] = {}
    for item in tracked:
        repo = repos.resolve(conn, config, item.repo_key)
        if repo is None:
            with db.transaction(conn):
                db.raise_anomaly(
                    conn,
                    kind="config_missing_repo",
                    entity_type="work_item",
                    entity_id=str(item.id),
                    detail={
                        "repo_key": item.repo_key,
                        "note": "the item's repository no longer resolves to a clone",
                    },
                )
            continue
        clone = str(repo.path)
        if clone not in prunable_by_clone:
            try:
                prunable_by_clone[clone] = {
                    info.path
                    for info in boundaries.version_control.list_worktrees(clone)
                    if info.prunable
                }
            except BoundaryError as exc:
                audit.error("reconcile.list_worktrees", error=exc, detail={"clone": clone})
                prunable_by_clone[clone] = set()
        missing = item.worktree_path in prunable_by_clone[clone] or not Path(
            item.worktree_path or ""
        ).is_dir()
        if not missing:
            continue
        with db.transaction(conn):
            created = db.raise_anomaly(
                conn,
                kind="prunable_worktree",
                entity_type="work_item",
                entity_id=str(item.id),
                detail={
                    "worktree_path": item.worktree_path,
                    "branch": item.branch,
                    "state": str(item.state),
                    "note": "directory is gone; `robot-army worktree prune` clears git's record",
                },
            )
        if created:
            flagged += 1
    return flagged


def sweep_startup_note(audit: AuditLog, summary: dict[str, Any]) -> None:
    """Record the startup pass distinctly, since FR-037 makes its ordering load-bearing.

    Takes the already-computed summary rather than a ``ReconcileResult`` so the daemon
    can route its startup pass through the same job as every later one.
    """
    audit.record("reconcile.startup", outcome="ok", detail={**summary, "at": utcnow()})


def within(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
