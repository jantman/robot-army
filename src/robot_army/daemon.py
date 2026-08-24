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

from robot_army import db, dispatch, health, poll, reconcile, spool
from robot_army.audit import AuditLog
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
    """Non-destructive check: can we take the lock right now?

    Used by lock-aware commands to decide between delegating to a running daemon and
    acting directly. Takes and immediately releases, so it never blocks the daemon.
    """
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


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
        problems.append(
            f"no terminal control socket answered {config.terminal.socket_glob!r}. "
            "kitty must be running with `allow_remote_control yes` and `listen_on` set"
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
        return [
            Job("spool", tick, self.job_drain_spool),
            Job("reconcile", float(daemon_cfg.reconcile_seconds), self.job_reconcile),
            Job("poll", float(daemon_cfg.poll_seconds), self.job_poll),
            Job("dispatch", tick, self.job_dispatch),
        ]

    def job_drain_spool(self) -> dict[str, Any]:
        self.activity = "draining exit spool"
        result = spool.drain(self.conn, audit=self.audit, layout=self.layout)
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

    def job_dispatch(self) -> dict[str, Any]:
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

        # A record that arrived while we were down is applied before anything reasons
        # about state — otherwise reconciliation would see a session with no exit and
        # correctly-but-wrongly call it interrupted.
        self.job_drain_spool()

        # 5. Reconcile (FR-037), before any dispatch. Routed through the job rather than
        # calling reconcile.reconcile() directly, so there is exactly one reconciliation
        # path and the startup pass cannot drift from the periodic one.
        summary = self.job_reconcile()
        reconcile.sweep_startup_note(self.audit, summary)

    def tick(self) -> dict[str, Any]:
        """One pass over the due jobs. Returns what ran, for ``--once`` and for tests."""
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
        self.cycles += 1
        self.activity = "idle"
        self._heartbeat()
        return ran

    def _heartbeat(self) -> None:
        health.write_heartbeat(
            self.layout.heartbeat_path,
            effect_level=str(self.effect_level),
            activity=self.activity,
            cycles=self.cycles,
            dispatched=self.dispatched,
            errors=self.errors,
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
            boundaries = wire_boundaries(effect_level, config, audit)
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


def wire_boundaries(level: EffectLevel, config: Config, audit: AuditLog) -> Boundaries:
    """Indirection so tests can substitute the whole wired set in one place."""
    from robot_army.effects import wire

    return wire(level, config, audit)
