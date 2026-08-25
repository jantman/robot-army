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

from robot_army import db, intake, sessions
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
    from robot_army.paths import Layout


@dataclass(slots=True)
class ReconcileResult:
    checked: int = 0
    interrupted: int = 0
    dispatching_failed: int = 0
    closed_done: int = 0
    orphans: int = 0
    stale_sockets: int = 0
    prunable: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "interrupted": self.interrupted,
            "dispatching_failed": self.dispatching_failed,
            "closed_done": self.closed_done,
            "orphans": self.orphans,
            "stale_sockets": self.stale_sockets,
            "prunable_worktrees": self.prunable,
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

        entry = by_session.get(session.session_id)
        alive = entry is not None and entry.alive(proc_root=proc_root)
        if alive and entry is not None:
            claimed_pids.add(entry.pid)
            continue

        # Not alive. Before concluding, check whether the session's own row already knows
        # it exited — a spool record applied earlier in this same tick, say.
        if session.state in (SessionState.EXITED_CLEAN, SessionState.EXITED_ERROR):
            continue

        if session.dry_run:
            # A simulated session has no process to be alive. Its life is bounded by the
            # simulated host, not by /proc, so reconciling it against the registry would
            # mark every simulated item interrupted on the very next pass (FR-055).
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
        result.dispatching_failed += 1

    # -- issues that have been closed (FR-035, FR-042) ---------------------
    result.closed_done += _resolve_closed_issues(
        conn, boundaries=boundaries, audit=audit, config=config
    )

    # -- the orphan sweep (FR-043, M0 F17) ---------------------------------
    result.orphans += _orphan_sweep(
        conn, audit=audit, config=config, scan=scan, claimed_pids=claimed_pids
    )

    # -- stale sockets and prunable worktrees (FR-044) ---------------------
    result.stale_sockets += _sweep_sockets(conn, boundaries=boundaries, audit=audit, layout=layout)
    result.prunable += _sweep_worktrees(conn, boundaries=boundaries, audit=audit, config=config)

    audit.record(
        "reconcile.pass",
        outcome="ok",
        detail={**result.summary(), **sessions.summarise(scan, config.worktree_root)},
    )
    return result


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
        repo = config.repos.get(item.repo_key)
        if repo is None:
            with db.transaction(conn):
                db.raise_anomaly(
                    conn,
                    kind="config_missing_repo",
                    entity_type="work_item",
                    entity_id=str(item.id),
                    detail={
                        "repo_key": item.repo_key,
                        "note": "the item's repository has no [repos.*] section any more",
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
