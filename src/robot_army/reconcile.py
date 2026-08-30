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

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from robot_army import cleanup, db, intake, notifications, repos, sessions, speckit
from robot_army.boundaries import BoundaryError, TransportError
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
    cleaned: int = 0
    retained: int = 0
    speckit_phase_changes: int = 0
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
            "cleaned": self.cleaned,
            "retained": self.retained,
            "speckit_phase_changes": self.speckit_phase_changes,
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

    # -- issues that have been closed (FR-035, FR-042) ---------------------
    result.closed_done += _resolve_closed_issues(
        conn, boundaries=boundaries, audit=audit, config=config
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

    # -- the orphan sweep (FR-043, M0 F17) ---------------------------------
    result.orphans += _orphan_sweep(
        conn, audit=audit, config=config, scan=scan, claimed_pids=claimed_pids
    )

    # -- how far Spec Kit runs have got (milestone 007, FR-012) ------------
    result.speckit_phase_changes += _observe_speckit(conn, audit=audit)

    # -- stale sockets and prunable worktrees (FR-044) ---------------------
    result.stale_sockets += _sweep_sockets(conn, boundaries=boundaries, audit=audit, layout=layout)
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


def _orphan_sweep(
    conn: sqlite3.Connection,
    *,
    audit: AuditLog,
    config: Config,
    scan: sessions.RegistryScan,
    claimed_pids: set[int],
) -> int:
    """Live worker processes under the worktree root that match no ``active`` row.

    This is the M0 F17 case made visible: the wrapper died, dtach tore down its socket,
    and the worker carried on reparented. Without this sweep the daemon would report
    ``interrupted`` while a real session was still editing files.
    """
    found = 0
    for entry in scan.entries:
        if entry.pid in claimed_pids:
            continue
        if not sessions.under_root(entry.cwd, config.worktree_root):
            continue  # the maintainer's own session; none of our business
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
