"""The session host: ``dtach``. Owns the process and its PTY.

Separate from ``Display`` because this is the axis along which *work survival* varies.
Kitty renders a PTY someone else may own; dtach owns one. Modelling them as
interchangeable would force a lowest-common-denominator interface that could express
neither.

**Three measured M0 findings live here, and all three are easy to "fix" back into bugs:**

1. ``dtach`` takes **no ``--`` separator**. It rejects one outright with
   ``Invalid option '--'``. The form is ``dtach -A <socket> <cmd> [args...]``, and the
   wrapper needs its own ``--`` after it. This broke the planning document's documented
   launch chain (M0 F10) and is the single easiest thing here to get wrong.
2. ``is_alive`` **probes the socket**; it never trusts the file's existence. Stale
   sockets do not clean themselves up, and a dead dtach socket fails in ~7 ms, so there
   is no hang risk in probing.
3. ``terminate`` uses the systemd scope recorded at confirmation as an **opaque handle**
   — never recomputed (M0 F18).
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from robot_army import procinfo
from robot_army.boundaries import (
    BoundaryError,
    HostCapabilities,
    HostHandle,
    TerminationOutcome,
)
from robot_army.sessions import RegistryEntry
from robot_army.sessions import scan as sessions_scan
from robot_army.subproc import run

if TYPE_CHECKING:
    from robot_army.audit import AuditLog

#: A dead dtach socket refuses in ~7 ms (measured in M0), so this is generous.
PROBE_TIMEOUT = 3.0
TERMINATE_TIMEOUT = 15.0
#: How often confirmation re-reads the registry while waiting.
CONFIRM_POLL_INTERVAL = 1.0
#: How long ``terminate`` waits for a signalled process to actually disappear before it
#: says it could not confirm. A ``/proc`` read is cheap and local, so this is generous for
#: what it measures: the gap between a signal being delivered and the kernel reaping the
#: process. It is a constant rather than a configuration knob because it has one caller and
#: no second use in hand (Principle I).
TERMINATE_CONFIRM_TIMEOUT = 5.0
#: How often that wait re-reads ``/proc``.
TERMINATE_POLL_INTERVAL = 0.1


class DtachHost:
    #: All three measured in M0 rather than assumed. The orchestrator branches on them.
    capabilities = HostCapabilities(
        survives_display_death=True,
        reattachable=True,
        multi_viewer=True,
    )

    def __init__(self, audit: AuditLog, *, binary: str = "dtach") -> None:
        self._audit = audit
        self._dtach = binary

    def build_argv(self, socket_path: str, argv: list[str]) -> list[str]:
        """The dtach invocation. Note the deliberate absence of a ``--`` separator.

        ``-A`` attaches to an existing socket or creates one, which is what makes a
        session reattachable after its display dies.
        """
        # DO NOT insert "--" here. dtach rejects it outright (M0 F10). The wrapper that
        # follows takes its own "--"; this one would break the whole chain.
        return [self._dtach, "-A", socket_path, *argv]

    def spawn(self, cwd: str, argv: list[str], socket_path: str) -> HostHandle:
        """Return the handle describing how the session will be hosted.

        The host does **not** fork the process itself: kitty does, because a real
        interactive terminal session in the running kitty instance is the product. This
        method builds and records the invocation; ``Display.open`` executes it. That
        split is why the daemon cannot ``waitpid`` on a session, and therefore why the
        wrapper and the exit spool exist at all.
        """
        Path(socket_path).parent.mkdir(parents=True, exist_ok=True)
        full = self.build_argv(socket_path, argv)
        self._audit.record(
            "dtach.spawn",
            outcome="ok",
            target=socket_path,
            detail={"cwd": cwd, "argv": full},
        )
        return HostHandle(socket_path=socket_path, argv=tuple(full))

    def confirm_session(
        self,
        session_id: str,
        timeout_seconds: float,
        *,
        registry_dir: Path | None = None,
        proc_root: Path | None = None,
        sleep: Any = time.sleep,
        clock: Any = time.monotonic,
    ) -> RegistryEntry | None:
        """Poll the session registry for an entry carrying **our** ``session_id`` (FR-025).

        This is the only observation that distinguishes a healthy session from a
        convincing imitation of one: ``kitty @ launch`` returns ``0`` and a valid window
        id even when nothing started (M0 F16). A registry entry with the id *we generated
        and passed via ``--session-id``* cannot be produced by anything else.

        An entry carrying a *different* id is recorded as a mismatch and reported, not
        accepted as success (FR-065).
        """
        deadline = clock() + timeout_seconds
        mismatched: set[str] = set()
        while True:
            scan = sessions_scan(registry_dir=registry_dir, proc_root=proc_root)
            entry = scan.find(session_id)
            if entry is not None:
                return entry
            mismatched.update(e.session_id for e in scan.entries if e.session_id)
            if clock() >= deadline:
                self._audit.record(
                    "session.confirm",
                    outcome="error",
                    entity_type="session",
                    entity_id=session_id,
                    detail={
                        "waited_s": timeout_seconds,
                        "registry_entries": len(scan.entries),
                        "other_session_ids": sorted(mismatched),
                        "unknown_versions": list(scan.unknown_versions),
                        "error": "no registry entry appeared with the requested session id",
                    },
                )
                return None
            sleep(CONFIRM_POLL_INTERVAL)

    def is_alive(self, handle: HostHandle) -> bool:
        """Probe the socket rather than trusting that the file exists.

        The probe is a bare ``connect()`` on the unix stream socket, not a ``dtach -a``:
        attaching requires a terminal, so it would fail against a *live* socket too and
        tell us nothing. A live dtach master accepts the connection; a stale socket file
        whose master is gone refuses with ``ECONNREFUSED`` in about 7 ms (measured in M0),
        which is why probing carries no hang risk. We disconnect immediately without
        sending anything, so an attached viewer is unaffected.
        """
        socket_path = handle.socket_path
        if not Path(socket_path).exists():
            return False
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(PROBE_TIMEOUT)
            sock.connect(socket_path)
        except (ConnectionRefusedError, FileNotFoundError):
            return False  # a stale socket file: the master is gone
        except TimeoutError as exc:
            # A probe that hangs is not evidence of death; say so rather than guessing.
            raise BoundaryError(f"dtach probe on {socket_path} timed out") from exc
        except OSError as exc:
            raise BoundaryError(f"dtach probe on {socket_path} failed: {exc}") from exc
        else:
            return True
        finally:
            sock.close()

    def terminate(
        self,
        handle: HostHandle,
        scope: str | None = None,
        *,
        expected_start: str | None = None,
        proc_root: Path | None = None,
        sleep: Any = time.sleep,
        clock: Any = time.monotonic,
    ) -> TerminationOutcome:
        """Stop this session's whole process tree and no other (FR-050), and **confirm it**.

        The recorded systemd scope is the primary path — kitty places each launched
        window in its own ``kitty-<pid>-<n>.scope``, so stopping the scope kills exactly
        that session's tree. The fallback signals the process group and **logs that the
        degraded path was taken**, because silently doing something weaker is the kind of
        difference that matters later.

        **Every rung is followed by an observation, and no rung returns on its exit
        status.** This used to read ``if result.ok: return``, and that line is the whole of
        issue #34: ``systemctl --user stop`` exits 0 in about four milliseconds for a unit
        that is *already inactive*, killing nothing, while a live process remains in its
        cgroup. The scope stop therefore "succeeded" in exactly the case that needed the
        fallback, the fallback below was unreachable, and ``cancel`` reported a stopped
        session that was still running — scheduled, editing a worktree, spending quota —
        twenty-six minutes later. Do not restore the early return as an optimisation; the
        confirmation *is* the feature.

        The project already knew this in the other direction. FR-025 exists because
        ``kitty @ launch`` returns 0 and a valid window id for a session that never
        started, so dispatch confirms against the session registry rather than trusting
        the call. Launching was confirmed; terminating was trusted. This closes that.
        """
        with self._audit.action(
            "session.terminate",
            target=handle.socket_path,
            detail={"scope": scope, "pid": handle.pid, "proc_start": expected_start},
        ) as outcome:
            rungs: list[dict[str, Any]] = []
            outcome["rungs"] = rungs

            def settle(result: TerminationOutcome) -> TerminationOutcome:
                outcome["method"] = result.method
                outcome["confirmed"] = result.confirmed
                outcome["escalated"] = result.escalated
                return result

            def confirm() -> tuple[bool, float]:
                assert handle.pid is not None  # noqa: S101 - guarded by every call site
                return _confirm_gone(
                    handle.pid,
                    expected_start,
                    proc_root=proc_root,
                    timeout=TERMINATE_CONFIRM_TIMEOUT,
                    sleep=sleep,
                    clock=clock,
                )

            def refuse(reason: str) -> TerminationOutcome:
                outcome["refused"] = True
                outcome["refused_reason"] = reason
                # Stated rather than implied. "We refused" and "we refused and sent
                # nothing" are the same claim only to a reader who trusts the code, and
                # this whole guard exists because that trust was misplaced once.
                outcome["signals_sent"] = 0
                return settle(
                    TerminationOutcome(
                        confirmed=False,
                        method="refused",
                        refused_reason=reason,
                        detail={"rungs": rungs},
                    )
                )

            # Before anything is attempted: is this pid ours to signal at all? A row whose
            # pid cannot be a session process is a malformed row, and its recorded scope is
            # no more trustworthy than its pid — so this sits ahead of the scope rung too,
            # not just ahead of the signal (069 S5).
            #
            # ``None`` is not judged here. It means there is nothing to signal, and the
            # no-pid paths below already refuse to signal *and* refuse to claim a
            # confirmation (014 C7, C8). Turning that into a refusal would change two cases
            # this feature has no business changing.
            if handle.pid is not None:
                impossible = _refusal_reason(handle.pid)
                if impossible is not None:
                    return refuse(impossible)

            # And is it *identified*? A pid with no recorded start time is a bare number.
            # ``procinfo.is_alive`` degrades to a plain "/proc/<pid> exists" check when the
            # start time is ``None``; that degradation is documented and fine for liveness,
            # and it is exactly what carried a recorded pid of 1 past the pre-check below
            # and into ``killpg`` on 2026-08-31. Termination gets the stricter rule (069
            # S1), and it lives here rather than in ``procinfo`` because the other callers
            # are only ever *reading* — this one is about to act.
            #
            # No legitimate row is lost to this: ``pid`` and ``proc_start`` are written in
            # the same transaction at session confirmation, so a row carrying one carries
            # the other. A row that does not was never safely cancellable.
            if handle.pid is not None and expected_start is None:
                return refuse(
                    f"the session row records pid {handle.pid} but no process start time, "
                    "so there is no way to tell that pid apart from any other process that "
                    "happens to hold the number"
                )

            # Ask before acting. A session that already died on its own needs no signal,
            # and a *recycled* pid — one whose start time no longer matches — means our
            # process is gone and that a stranger now holds its number. Signalling that
            # stranger is the FR-039 incident this project has already had once.
            if handle.pid is not None and not procinfo.is_alive(
                handle.pid, expected_start, root=proc_root
            ):
                return settle(
                    TerminationOutcome(
                        confirmed=True, method="already_gone", detail={"rungs": rungs}
                    )
                )

            escalated = False
            if scope:
                result = run(
                    ["systemctl", "--user", "stop", scope],
                    timeout=TERMINATE_TIMEOUT,
                    audit=self._audit,
                    action="systemctl.stop",
                    check=False,
                )
                rung: dict[str, Any] = {
                    "method": "systemd_scope",
                    "exit": result.returncode,
                    "ok": result.ok,
                }
                rungs.append(rung)
                if not result.ok:
                    rung["output"] = result.output
                    outcome["scope_stop_failed"] = result.output

                if handle.pid is None:
                    # T5/C8: the stop was attempted and there is nothing to confirm it
                    # against. That is an unconfirmed stop, not a successful one — an exit
                    # status alone is exactly what this milestone stopped accepting.
                    rung["alive_after"] = None
                    return settle(
                        TerminationOutcome(
                            confirmed=False,
                            method="none",
                            detail={"rungs": rungs, "why": "no pid recorded to confirm against"},
                        )
                    )

                gone, waited = confirm()
                rung["alive_after"] = not gone
                rung["waited_s"] = waited
                if gone:
                    return settle(
                        TerminationOutcome(
                            confirmed=True, method="systemd_scope", detail={"rungs": rungs}
                        )
                    )
                if result.ok:
                    # The issue's exact shape, recorded as such: both the reported success
                    # and the observation that contradicts it (FR-002).
                    escalated = True
                    rung["reported_success_but_alive"] = True

            if handle.pid is None:
                outcome["method"] = "none"
                raise BoundaryError(
                    "cannot terminate: no systemd scope recorded and no pid known"
                )

            signal_detail: dict[str, object] = {}
            try:
                _signal_group(handle.pid, signal_detail)
            except BoundaryError as exc:
                # The pid looked ordinary and its start time matched, so the guard above
                # passed it; only resolving the process group revealed that signalling it
                # would be ``kill(-1)``. That is a refusal like any other from the caller's
                # side — but note the scope rung has already run by now, and ``rungs``
                # records that it did. A refusal decided here is not the same as one
                # decided up front, and the record must not pretend otherwise (069 S5).
                return refuse(str(exc))
            gone, waited = confirm()
            rungs.append(
                {
                    "method": "process_group_signal",
                    **signal_detail,
                    "alive_after": not gone,
                    "waited_s": waited,
                }
            )
            outcome["degraded"] = True
            return settle(
                TerminationOutcome(
                    confirmed=gone,
                    method="process_group_signal",
                    escalated=escalated,
                    detail={"rungs": rungs},
                )
            )

    def attach_command(self, handle: HostHandle) -> list[str]:
        """What the maintainer types to reattach. Printed by ``show``."""
        return [self._dtach, "-a", handle.socket_path]


def _confirm_gone(
    pid: int,
    expected_start: str | None,
    *,
    proc_root: Path | None,
    timeout: float,
    sleep: Any,
    clock: Any,
) -> tuple[bool, float]:
    """Poll ``/proc`` until the tracked process is gone, or the bound elapses.

    Returns ``(gone, waited_s)``. The waited time is returned rather than discarded
    because the record has to answer "how long did confirmation take" without re-running
    anything (FR-011).

    ``expected_start`` is passed through to ``procinfo.is_alive``, which compares it
    against ``/proc/<pid>/stat`` field 22. That comparison is the whole guard: a pid whose
    start time no longer matches belongs to something else, which means *our* process is
    gone (and that the stranger holding its pid must never be signalled).

    T4: a bound that elapses returns ``False`` — not confirmed — never a success.
    """
    started = clock()
    while True:
        if not procinfo.is_alive(pid, expected_start, root=proc_root):
            return True, round(clock() - started, 3)
        if clock() - started >= timeout:
            return False, round(clock() - started, 3)
        sleep(TERMINATE_POLL_INTERVAL)


def _refusal_reason(pid: int | None) -> str | None:
    """Why this pid must not be signalled, or ``None`` if there is no reason (069 S2).

    Three values, three separate reasons, and none of them is implied by the others —
    which is why this is a flat test and not a clever one:

    * **1** — ``os.getpgid(1)`` is ``1``, and ``killpg(1, sig)`` is ``kill(-1, sig)``:
      POSIX for *every process the caller may signal*. The daemon runs as the desktop
      user, so on 2026-08-31 that was the whole session, robot-army included. It reported
      the cancel confirmed afterwards, because the recorded pid was certainly gone.
    * **0** — ``getpgid(0)`` does not ask about pid 0. It asks about **the caller**, and
      returns the caller's own group: an ordinary number nowhere near 1, so a
      ``pgid <= 1`` test does not catch it. Signalling it ends the daemon, or the
      operator's shell when the CLI is doing the asking.
    * **None** — nothing to signal. Reachable only by a caller that skipped the no-pid
      paths in ``terminate``; those refuse to signal *and* refuse to claim a
      confirmation, which is a different and already-correct answer.

    A sentence rather than a code: it is what the maintainer reads and what the record
    carries, and there is nobody to translate a code back for.
    """
    if pid is None:
        return "no pid recorded, so there is nothing that can be signalled"
    if pid == 1:
        return (
            "the recorded pid is 1, which cannot be a session process: its process group "
            "is 1, and signalling that group signals every process this user owns"
        )
    if pid == 0:
        return (
            "the recorded pid is 0, which cannot be a session process: it resolves to the "
            "caller's own process group, so signalling it would signal this process"
        )
    if pid < 0:
        return f"the recorded pid is {pid}, which is not a process id"
    return None


def _signal_group(pid: int, outcome: dict[str, object]) -> None:
    import signal
    import time

    # Belt and braces, and deliberately so. ``terminate`` has already refused these values
    # before reaching here, so this is unreachable in the shipped path — which is exactly
    # the argument that was available for not checking at all, right up until the day the
    # call below took the machine down. One layer of "this cannot happen" is not enough
    # when the failure is unrecoverable, so the primitive that makes the call also refuses
    # to make it (069 S7).
    reason = _refusal_reason(pid)
    if reason is not None:
        raise BoundaryError(f"refusing to signal: {reason}")

    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        outcome["already_gone"] = True
        return

    # The pid was ordinary; the group it resolves to is not. This is the third route to
    # ``kill(-1)`` and the only one visible from here — a session leader that has died can
    # leave its children in a group we must not signal wholesale (069 S3).
    if pgid <= 1:
        raise BoundaryError(
            f"refusing to signal: pid {pid} resolves to process group {pgid}, and "
            f"signalling that group would signal every process this user owns"
        )

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        outcome["already_gone"] = True
        return
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            outcome["signal"] = "SIGTERM"
            return
        time.sleep(0.25)
    try:
        os.killpg(pgid, signal.SIGKILL)
        outcome["signal"] = "SIGKILL"
    except ProcessLookupError:
        outcome["signal"] = "SIGTERM"


class SimulatedSessionHost:
    """Records the invocation it would have made and returns a valid handle.

    Its capabilities deliberately mirror ``DtachHost``'s: code that branches on
    ``survives_display_death`` must take the same branch under simulation, or the
    simulated path diverges from the real one at exactly the wrong place.
    """

    capabilities = HostCapabilities(
        survives_display_death=True,
        reattachable=True,
        multi_viewer=True,
    )

    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit
        self._alive: set[str] = set()

    def build_argv(self, socket_path: str, argv: list[str]) -> list[str]:
        return ["dtach", "-A", socket_path, *argv]

    def spawn(self, cwd: str, argv: list[str], socket_path: str) -> HostHandle:
        full = self.build_argv(socket_path, argv)
        self._alive.add(socket_path)
        self._audit.record(
            "dtach.spawn",
            outcome="ok",
            target=socket_path,
            simulated=True,
            detail={"cwd": cwd, "argv": full},
        )
        return HostHandle(socket_path=socket_path, argv=tuple(full), simulated=True)

    def confirm_session(
        self, session_id: str, timeout_seconds: float, **_: Any
    ) -> RegistryEntry:
        """Return a structurally valid stand-in immediately.

        At a simulated effect level no real registry entry can ever appear, so waiting
        would guarantee a timeout and send every simulated dispatch down the *failure*
        branch — which is precisely the divergence between the simulated and real paths
        that contracts/boundaries.md forbids. ``pid=0`` is deliberate: nothing can mistake
        it for a real process, and the liveness check treats it as absent.
        """
        self._audit.record(
            "session.confirm",
            outcome="ok",
            entity_type="session",
            entity_id=session_id,
            simulated=True,
            detail={"would_wait_s": timeout_seconds},
        )
        return RegistryEntry(
            session_id=session_id,
            pid=0,
            proc_start=None,
            cwd=None,
            status="simulated",
            version=None,
            source_file="<simulated>",
        )

    def is_alive(self, handle: HostHandle) -> bool:
        return handle.socket_path in self._alive

    def terminate(
        self,
        handle: HostHandle,
        scope: str | None = None,
        *,
        expected_start: str | None = None,
        proc_root: Path | None = None,
    ) -> TerminationOutcome:
        """Confirmed by construction, and deliberately observing nothing (C10, T8).

        A simulated session has no process. Its pid is ``0`` by construction — see
        ``confirm_session`` below — so putting it through the real path's ``/proc``
        observation would find nothing alive to kill and nothing to confirm against, and
        every simulated cancel would take the *failure* branch. That is exactly the
        divergence between the simulated and real paths contracts/boundaries.md forbids,
        and it is the same trap ``confirm_session`` documents at length (FR-014).
        """
        self._alive.discard(handle.socket_path)
        self._audit.record(
            "session.terminate",
            outcome="ok",
            target=handle.socket_path,
            simulated=True,
            detail={"scope": scope, "confirmed": True, "method": "simulated"},
        )
        return TerminationOutcome(
            confirmed=True, method="simulated", escalated=False, detail={"scope": scope}
        )

    def attach_command(self, handle: HostHandle) -> list[str]:
        return ["dtach", "-a", handle.socket_path]
