"""The daemon: one process, one thread, one loop.

Principle I names "obvious top-to-bottom control flow" as the default shape and requires
concurrency to be justified against a demonstrated need. There is none here: the workload
is a handful of HTTP calls and process checks per minute against a global cap of two
sessions. A single loop is also what makes the audit log readable, because events appear
in causal order with no interleaving.

**The multi-rate scheduler is the one piece of design in the loop** (R6). Coupling
exit-detection latency to the GitHub poll interval would force a choice between prompt
status updates and a sustainable rate-limit budget. Separating a 5-second base tick from
each job's own interval removes the tradeoff: the spool drains and the heartbeat is
written every tick, while polling and reconciliation run on their own slower clocks.

Blocking risk is real and bounded rather than avoided: a long operation on the tick thread
stalls everything, which is acceptable *because* every blocking operation is already
required to be bounded — HTTP by FR-008, preparation steps by FR-013, socket probes by
FR-019. During a long worktree preparation the daemon is legitimately busy, and the
heartbeat says so rather than looking like a hang.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import signal
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any

from robot_army import (
    control,
    db,
    dispatch,
    health,
    intake,
    notifications,
    poll,
    reconcile,
    spool,
)
from robot_army.audit import AuditLog
from robot_army.boundaries.kitty import describe_refusals
from robot_army.effects import Boundaries, EffectLevel
from robot_army.migrations import SCHEMA_VERSION

if TYPE_CHECKING:
    from robot_army.config import Config
    from robot_army.paths import Layout


class PreconditionFailed(Exception):
    """A startup precondition is unmet. Exit code 3, with the reason on stderr."""


class LockHeld(PreconditionFailed):
    """Another daemon instance holds the lock. Carries the holding PID (FR-070)."""

    def __init__(self, path: Path, holder: str) -> None:
        super().__init__(
            f"another robot-army daemon holds {path} (pid {holder}). "
            "Only one instance may run against the same state"
        )
        self.holder = holder


class SingleInstanceLock:
    """``flock(LOCK_EX | LOCK_NB)`` held for the daemon's lifetime (R17, FR-070).

    ``flock`` is released by the kernel when the process dies **by any means, including
    SIGKILL**, which a PID file alone cannot promise. The PID is written into the file so
    the failure message can name the holder rather than saying only "something else".
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            holder = "unknown"
            with contextlib.suppress(OSError):
                holder = os.read(fd, 64).decode("utf-8", "replace").strip() or "unknown"
            os.close(fd)
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise LockHeld(self.path, holder) from exc
            raise
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.fsync(fd)
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> SingleInstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def read_lock_holder(path: Path) -> str | None:
    """Who holds the lock, for CLI commands that need to explain a refusal."""
    try:
        return Path(path).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def is_locked(path: Path) -> bool:
    """Non-destructive check: is a daemon holding the lock right now?

    Used by lock-aware commands to decide between delegating to a running daemon and
    acting directly, and by the web interface's chrome on every page.

    **The probe takes a SHARED lock, not an exclusive one, and that is load-bearing.**
    A shared lock still conflicts with the daemon's ``LOCK_EX``, so a running daemon is
    detected exactly as before — but two concurrent probes no longer conflict with *each
    other*. With ``LOCK_EX`` they did, and each reported the other's transient hold as "a
    daemon is running": measured at 1,558 false positives in 2,400 probes across six
    threads, with no daemon running at all.

    Milestone 001 only ever probed from a single-threaded CLI, so the race had no way to
    occur. Milestone 002 serves concurrent requests, and the first page load with two
    requests in flight produced a page claiming the daemon was alive while it was dead —
    which is precisely what SC-010 forbids.

    The remaining window is the reverse and is negligible: a daemon starting in the
    microseconds a probe holds its shared lock would fail to acquire and say so loudly.
    That is a deliberate human action, immediately retryable, with a message naming the
    lock — as against a misleading page on every concurrent load.
    """
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


#: An interval no uptime reaches, for jobs that only ever run when explicitly forced.
#: Ten years in seconds — a number, rather than a special case in the scheduler, because
#: the scheduler having one shape is worth more than the tidiness of an ``Optional``.
_FORCED_ONLY = 315_360_000.0


# -- the multi-rate scheduler ----------------------------------------------


@dataclass(slots=True)
class Job:
    """One periodic task and its own interval, in monotonic seconds."""

    name: str
    interval: float
    run: Callable[[], Any]
    next_due: float = 0.0
    #: Set by ``request()`` so ``robot-army poll`` can ask a running daemon to act now.
    forced: bool = False

    def due(self, now: float) -> bool:
        return self.forced or now >= self.next_due

    def schedule(self, now: float) -> None:
        self.forced = False
        self.next_due = now + self.interval


# -- preconditions ----------------------------------------------------------


def check_preconditions(
    *,
    config: Config,
    layout: Layout,
    boundaries: Boundaries,
    conn: sqlite3.Connection,
) -> list[str]:
    """Everything FR-067 requires, checked before any work is dispatched.

    Returns the list of failures; an empty list means go. Reported together rather than
    one per restart, for the same reason config validation aggregates.
    """
    problems: list[str] = []

    if not os.access(layout.state_dir, os.W_OK):
        problems.append(f"state directory is not writable: {layout.state_dir}")

    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version != SCHEMA_VERSION:
            problems.append(
                f"database schema is at version {version}, expected {SCHEMA_VERSION}"
            )
    except sqlite3.Error as exc:
        problems.append(f"database is not usable: {exc}")

    # The terminal socket must answer, because a real interactive session in the running
    # kitty instance *is* the product. At effect levels where the display is simulated,
    # the simulated probe answers, so this check is level-appropriate without branching.
    if boundaries.display.probe() is None:
        # Whatever was refused is named here too, so the startup problem distinguishes a
        # terminal that is not running from one being impersonated (RA-15). The simulated
        # display has no refusals, so this reads exactly as before at those levels.
        refusals = getattr(boundaries.display, "refusals", ())
        problems.append(
            f"no terminal control socket answered {config.terminal.socket_glob!r}. "
            "kitty must be running with `allow_remote_control yes` and `listen_on` set"
            + describe_refusals(refusals)
        )

    return problems


def warn_about_environment(audit: AuditLog, config: Config) -> list[str]:
    """Name any ``CLAUDE_CODE_*`` variable that would silently degrade a session (R11).

    M0 F19 cost the spike the most time of any single finding: a stray
    ``CLAUDE_CODE_CHILD_SESSION=1`` disabled transcript saving, producing sessions that
    looked perfect, exited 0, and could never be resumed. The variable is cheap to look
    for and the failure is invisible without it.
    """
    dangerous = {
        "CLAUDE_CODE_CHILD_SESSION": "disables transcript saving; sessions become unresumable",
    }
    warnings: list[str] = []
    for name, why in dangerous.items():
        if os.environ.get(name):
            message = f"{name} is set in this environment: {why}"
            warnings.append(message)
            audit.record(
                "daemon.environment_warning",
                outcome="error",
                detail={"variable": name, "why": why},
            )
    return warnings


# -- the loop ---------------------------------------------------------------


class Daemon:
    def __init__(
        self,
        *,
        config: Config,
        layout: Layout,
        boundaries: Boundaries,
        audit: AuditLog,
        conn: sqlite3.Connection,
        effect_level: EffectLevel,
        registry_dir: Path | None = None,
        proc_root: Path | None = None,
        trust_file: Path | None = None,
    ) -> None:
        self.config = config
        self.layout = layout
        self.boundaries = boundaries
        self.audit = audit
        self.conn = conn
        self.effect_level = effect_level
        self.registry_dir = registry_dir
        self.proc_root = proc_root
        self.trust_file = trust_file
        self.stopping = False
        self.cycles = 0
        self.dispatched = 0
        self.errors = 0
        self.activity = "idle"
        self._jobs: list[Job] = []
        #: The board preconditions, checked once at startup (R10, R11). ``None`` until
        #: ``startup()`` runs, and on an installation with no ``[trello]`` section it stays
        #: ``None`` — there is nothing to check and nothing to ingest.
        self.board: intake.BoardStatus | None = None

    # -- signals ---------------------------------------------------------

    def install_signal_handlers(self) -> None:
        """SIGTERM/SIGINT finish the current tick, release the lock, and exit 0.

        They never touch running sessions (FR-049). Sessions live in their own systemd
        scopes and their own dtach masters precisely so that restarting the daemon is a
        non-event for work in progress.
        """

        def _handle(signum: int, _frame: FrameType | None) -> None:
            if self.stopping:
                return  # a second signal does not escalate; the tick is already bounded
            self.stopping = True
            self.audit.record(
                "daemon.signal",
                outcome="ok",
                detail={
                    "signal": signal.Signals(signum).name,
                    "note": "finishing the current tick; running sessions are untouched",
                },
            )

        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)

    # -- jobs ------------------------------------------------------------

    def _build_jobs(self) -> list[Job]:
        daemon_cfg = self.config.daemon
        tick = float(daemon_cfg.tick_seconds)
        # The order within a tick is causal, and it is R6's: learn what happened
        # (spool), make the picture true (reconcile), find new work (poll), then act
        # (dispatch). Dispatching before polling would leave an issue discovered this
        # tick waiting for the next one — a whole tick of latency for no reason — and
        # dispatching before reconciling would act on a picture we already know is stale.
        jobs = [
            Job("spool", tick, self.job_drain_spool),
            Job("reconcile", float(daemon_cfg.reconcile_seconds), self.job_reconcile),
            Job("poll", float(daemon_cfg.poll_seconds), self.job_poll),
        ]
        # The board job, on its own slower interval (R13, R14), between poll and dispatch.
        # The ordering is a nicety rather than a correctness requirement: an issue this job
        # creates is picked up by the *next* GitHub poll regardless, and it cannot dispatch
        # until the author labels it, which will be later than either.
        #
        # **Skipped entirely when no board is configured** (FR-001). Not a job that checks
        # and returns — no job at all, so an unconfigured installation's tick is the one
        # milestone 002 shipped.
        if self.config.trello is not None:
            jobs.append(Job("board", float(self.config.trello.poll_seconds), self.job_board))
            # Forced-only: a rescan never runs on a schedule of its own, because there is
            # nothing periodic about it. `_FORCED_ONLY` is far beyond any uptime, so the
            # job is due exactly when a marker says so and never otherwise.
            jobs.append(Job("rescan", _FORCED_ONLY, self.job_rescan, next_due=_FORCED_ONLY))
        jobs.append(Job("dispatch", tick, self.job_dispatch))
        return jobs

    def job_drain_spool(self) -> dict[str, Any]:
        self.activity = "draining exit spool"
        result = spool.drain(
            self.conn,
            audit=self.audit,
            layout=self.layout,
            boundaries=self.boundaries,
            config=self.config,
        )
        return {"applied": result.applied, "quarantined": result.quarantined}

    def job_poll(self) -> dict[str, Any]:
        self.activity = "polling github"
        outcomes = poll.poll_all(
            self.conn,
            boundaries=self.boundaries,
            audit=self.audit,
            config=self.config,
            dry_run=self.effect_level.is_simulated,
        )
        self.errors += sum(1 for o in outcomes if o.error)
        return {
            "repos": len(outcomes),
            "created": sum(o.created for o in outcomes),
            "rejected": sum(o.rejected for o in outcomes),
            "errors": sum(1 for o in outcomes if o.error),
        }

    def job_rescan(self) -> dict[str, Any]:
        """Re-evaluate every held card now, whether or not the author has touched it.

        A separate job rather than a flag on ``job_board`` so the marker mechanism needs no
        change at all: ``control.take_requests`` returns a job name, ``request()`` finds a
        job with that name, and this is that job. Its interval is effectively infinite —
        it only ever runs when forced.
        """
        if not self.ingesting:
            return {"skipped": True, "reason": "board ingestion is disabled"}
        assert self.board is not None  # noqa: S101 - narrowed by self.ingesting
        self.activity = "rescanning held cards"
        outcome = intake.run_cycle(
            self.conn,
            boundaries=self.boundaries,
            audit=self.audit,
            config=self.config,
            status=self.board,
            dry_run=self.effect_level.is_simulated,
            forced=True,
        )
        return {
            "evaluated": outcome.evaluated,
            "issues_created": outcome.issues_created,
            "held": outcome.held,
            "error": outcome.error,
        }

    def job_board(self) -> dict[str, Any]:
        """One board cycle: recover what an interruption left, then poll and evaluate.

        Gated on the startup preconditions rather than re-checking them: R10 fixes the
        frequency at once per process plus a documented restart, because the thing being
        checked changes approximately never and the alternative is extra calls a minute.
        """
        if not self.ingesting:
            self.activity = "board ingestion disabled"
            return {
                "skipped": True,
                "reason": "board preconditions failed at startup",
                "failed_checks": [c.name for c in self.board.failures] if self.board else [],
            }
        assert self.board is not None  # noqa: S101 - narrowed by self.ingesting
        self.activity = "polling the board"
        outcome = intake.run_cycle(
            self.conn,
            boundaries=self.boundaries,
            audit=self.audit,
            config=self.config,
            status=self.board,
            dry_run=self.effect_level.is_simulated,
        )
        if outcome.error:
            self.errors += 1
        return {
            "found": outcome.found,
            "created": outcome.created,
            "evaluated": outcome.evaluated,
            "issues_created": outcome.issues_created,
            "held": outcome.held,
            "dropped": outcome.dropped,
            "recovered": outcome.recovered,
            "failed": outcome.failed,
            "error": outcome.error,
            "skipped_reason": outcome.skipped_reason,
        }

    def job_dispatch(self) -> dict[str, Any]:
        # FR-033: the pause is read **first**, before any selection, and gates only this
        # job. Polling, eligibility evaluation, reconciliation and the heartbeat all keep
        # running — a paused system must stay observably alive, not go quiet.
        #
        # Items simply remain `ready` (FR-034). Nothing is rejected, nothing is lost, and
        # nothing needs unwinding when the pause is lifted.
        paused = db.get_dispatch_control(self.conn)
        if paused.paused:
            self.activity = "dispatch paused"
            return {
                "dispatched": 0,
                "paused": True,
                "paused_at": paused.paused_at,
                "paused_by": paused.paused_by,
            }
        self.activity = "dispatching"
        count = dispatch.select_and_dispatch(
            self.conn,
            boundaries=self.boundaries,
            audit=self.audit,
            config=self.config,
            layout=self.layout,
            trust_file=self.trust_file,
            registry_dir=self.registry_dir,
            proc_root=self.proc_root,
        )
        self.dispatched += count
        return {"dispatched": count}

    def job_reconcile(self) -> dict[str, Any]:
        self.activity = "reconciling"
        result = reconcile.reconcile(
            self.conn,
            boundaries=self.boundaries,
            audit=self.audit,
            config=self.config,
            layout=self.layout,
            registry_dir=self.registry_dir,
            proc_root=self.proc_root,
        )
        return result.summary()

    def request(self, job_name: str) -> bool:
        """Ask a job to run on the next tick rather than waiting for its interval."""
        for job in self._jobs:
            if job.name == job_name:
                job.forced = True
                return True
        return False

    # -- startup and loop -------------------------------------------------

    def startup(self) -> None:
        """The sequence contracts/cli.md fixes, in order, all before any dispatch."""
        # 4. Log the effect level loudly (FR-057).
        self.audit.record(
            "daemon.start",
            outcome="ok",
            detail={
                "effect_level": str(self.effect_level),
                "boundaries": self.boundaries.describe(),
                "pid": os.getpid(),
                "config": str(self.config.path),
                "state_dir": str(self.layout.state_dir),
                "max_concurrent_sessions": self.config.daemon.max_concurrent_sessions,
            },
        )
        for warning in self.config.warnings:
            self.audit.record(
                "daemon.config_warning", outcome="error", detail={"warning": warning}
            )
        warn_about_environment(self.audit, self.config)

        # The board preconditions, before any board work and after the effect level has
        # been announced. Their failure disables **ingestion only**: the rest of this
        # startup sequence, and every job below, runs exactly as it did in milestone 002.
        # An unrelated board misconfiguration must not take down dispatch of issues the
        # author wrote themselves.
        self._check_board()

        # Whatever the last process was killed in the middle of. Run here as well as at
        # the head of every cycle, because a restart is the one moment we *know* an
        # interruption may have happened, and the board interval is 300 seconds.
        if self.ingesting:
            assert self.board is not None  # noqa: S101 - narrowed by self.ingesting
            recovered = intake.recovery_sweep(
                self.conn,
                boundaries=self.boundaries,
                audit=self.audit,
                config=self.config,
                dry_run=self.effect_level.is_simulated,
            )
            if any(recovered.values()):
                self.audit.record(
                    "trello.recovered",
                    outcome="ok",
                    detail={"at": "startup", **recovered},
                )

        # A record that arrived while we were down is applied before anything reasons
        # about state — otherwise reconciliation would see a session with no exit and
        # correctly-but-wrongly call it interrupted.
        self.job_drain_spool()

        # 5. Reconcile (FR-037), before any dispatch. Routed through the job rather than
        # calling reconcile.reconcile() directly, so there is exactly one reconciliation
        # path and the startup pass cannot drift from the periodic one.
        summary = self.job_reconcile()
        reconcile.sweep_startup_note(self.audit, summary)

    def _check_board(self) -> None:
        """Run the board preconditions and record the verdict on the daemon.

        Deliberately not part of ``check_preconditions``: that function's failures stop
        the daemon starting, and a board problem must not. The distinction is the whole
        point of R10's "refuse ingestion, not startup".
        """
        if self.config.trello is None:
            return
        status = intake.check_board(
            boundaries=self.boundaries, audit=self.audit, config=self.config
        )
        self.board = status
        if not status.ok:
            intake.board_disabled_anomaly(
                self.conn, self.audit, config=self.config, status=status
            )

    @property
    def ingesting(self) -> bool:
        """Whether the board job may run. False when unconfigured or precondition-failed."""
        return self.board is not None and self.board.ok

    def tick(self) -> dict[str, Any]:
        """One pass over the due jobs. Returns what ran, for ``--once`` and for tests."""
        # Drain cross-process job requests first (R5), so a marker written a moment ago is
        # honoured by *this* tick rather than the next one. Unlinking before running means
        # an interruption costs one ordinary interval; unlinking after would risk running
        # the job twice, and a duplicate poll spends rate limit the daemon needs.
        # The per-cycle notification bound is scoped to exactly this: one tick (R15). It
        # bounds a *burst* rather than an event, because a backlog produces different items
        # and per-item de-duplication would not bound it at all.
        notifications.begin_cycle()
        for name in control.take_requests(self.layout, self.audit):
            if self.request(name):
                self.audit.record(
                    "control.request_taken",
                    outcome="ok",
                    detail={"job": name, "note": "forced for this tick"},
                )
        now = time.monotonic()
        ran: dict[str, Any] = {}
        for job in self._jobs:
            if not job.due(now):
                continue
            try:
                ran[job.name] = job.run()
            except Exception as exc:  # noqa: BLE001 - one job must not kill the loop
                self.errors += 1
                self.audit.error("daemon.job", error=exc, detail={"job": job.name})
                ran[job.name] = {"error": str(exc)}
            finally:
                job.schedule(time.monotonic())
        # One summary for whatever the bound held back, so a suppressed burst is visible
        # rather than silent — the difference between a bound and a channel that lies by
        # omission. Outside every job, so a slow webhook delays nothing that mattered.
        try:
            notifications.end_cycle(
                boundaries=self.boundaries, audit=self.audit, config=self.config
            )
        except Exception as exc:  # noqa: BLE001 - a channel failure is never the loop's
            self.audit.error("notify.summary", error=exc)
        self.cycles += 1
        self.activity = "idle"
        self._heartbeat()
        return ran

    def _heartbeat(self) -> None:
        # FR-036: the pause travels with the liveness signal, so a check that "the daemon
        # is healthy" cannot be true while it is silently doing nothing.
        try:
            paused = db.get_dispatch_control(self.conn).paused
        except sqlite3.Error:
            # The heartbeat is the last thing that should fail. An unreadable pause is
            # reported rather than swallowed, and the beat still gets written.
            self.audit.error("daemon.heartbeat_pause_read", error="could not read dispatch_control")
            paused = False
        health.write_heartbeat(
            self.layout.heartbeat_path,
            effect_level=str(self.effect_level),
            activity=self.activity,
            cycles=self.cycles,
            dispatched=self.dispatched,
            errors=self.errors,
            dispatch_paused=paused,
            board=health.board_signal(
                self.conn,
                config=self.config,
                ingesting=self.ingesting,
                failures=[c.name for c in self.board.failures] if self.board else [],
            ),
            # Issue #30: what this daemon is actually enforcing, so no other process has to
            # guess it from a configuration file it may have read at a different time. The
            # value is trustworthy for the life of the heartbeat because it cannot move: the
            # cap is fixed when this process loads its configuration and there is no path
            # that rereads it, so even a heartbeat written an hour ago names the right one.
            max_concurrent_sessions=self.config.daemon.max_concurrent_sessions,
            extra={"config": str(self.config.path)},
        )

    def run(self, *, once: bool = False) -> int:
        self._jobs = self._build_jobs()
        self.startup()
        # Everything is due on the first tick, which is what makes `--once` a complete
        # cycle rather than an arbitrary slice of one.
        self._heartbeat()

        if once:
            self.tick()
            self.audit.record(
                "daemon.stop", outcome="ok", detail={"reason": "--once", "cycles": self.cycles}
            )
            return 0

        tick_seconds = float(self.config.daemon.tick_seconds)
        while not self.stopping:
            started = time.monotonic()
            self.tick()
            elapsed = time.monotonic() - started
            # Sleep in short slices so a signal is noticed within a fraction of a tick
            # rather than at the end of a full one.
            remaining = max(0.0, tick_seconds - elapsed)
            deadline = time.monotonic() + remaining
            while not self.stopping and time.monotonic() < deadline:
                time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))

        self.audit.record(
            "daemon.stop",
            outcome="ok",
            detail={
                "reason": "signal",
                "cycles": self.cycles,
                "dispatched": self.dispatched,
                "note": "running sessions were not touched",
            },
        )
        return 0


def run_daemon(
    *,
    config: Config,
    effect_level: EffectLevel,
    once: bool = False,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
    trust_file: Path | None = None,
) -> int:
    """Acquire the lock, wire the boundaries, check preconditions, and loop.

    The ordering here is contracts/cli.md's startup sequence and is not rearrangeable:
    the lock comes first so a second instance fails before it touches anything, and
    reconciliation comes before dispatch so no new session is launched against stale state.
    """
    layout = config.layout
    layout.ensure()

    with SingleInstanceLock(layout.lock_path):
        audit = AuditLog(layout.log_dir, component="daemon")
        conn, _ = db.open_database(layout.db_path)
        try:
            boundaries = wire_boundaries(effect_level, config, audit, conn)
            problems = check_preconditions(
                config=config, layout=layout, boundaries=boundaries, conn=conn
            )
            if problems:
                audit.record(
                    "daemon.preconditions",
                    outcome="error",
                    detail={"problems": problems},
                )
                raise PreconditionFailed(
                    "startup preconditions not met:\n"
                    + "\n".join(f"  - {p}" for p in problems)
                )

            daemon = Daemon(
                config=config,
                layout=layout,
                boundaries=boundaries,
                audit=audit,
                conn=conn,
                effect_level=effect_level,
                registry_dir=registry_dir,
                proc_root=proc_root,
                trust_file=trust_file,
            )
            daemon.install_signal_handlers()
            return daemon.run(once=once)
        finally:
            conn.close()
            audit.close()


def wire_boundaries(
    level: EffectLevel, config: Config, audit: AuditLog, conn: sqlite3.Connection
) -> Boundaries:
    """Indirection so tests can substitute the whole wired set in one place."""
    from robot_army.effects import wire

    return wire(level, config, audit, conn)
