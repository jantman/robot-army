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

from robot_army.boundaries import BoundaryError, HostCapabilities, HostHandle
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

    def terminate(self, handle: HostHandle, scope: str | None = None) -> None:
        """Stop this session's whole process tree and no other (FR-050).

        The recorded systemd scope is the primary path — kitty places each launched
        window in its own ``kitty-<pid>-<n>.scope``, so stopping the scope kills exactly
        that session's tree. The fallback signals the process group and **logs that the
        degraded path was taken**, because silently doing something weaker is the kind of
        difference that matters later.
        """
        with self._audit.action(
            "session.terminate",
            target=handle.socket_path,
            detail={"scope": scope, "pid": handle.pid},
        ) as outcome:
            if scope:
                result = run(
                    ["systemctl", "--user", "stop", scope],
                    timeout=TERMINATE_TIMEOUT,
                    audit=self._audit,
                    action="systemctl.stop",
                    check=False,
                )
                outcome["method"] = "systemd_scope"
                outcome["exit"] = result.returncode
                if result.ok:
                    return
                outcome["scope_stop_failed"] = result.output

            if handle.pid is None:
                outcome["method"] = "none"
                raise BoundaryError(
                    "cannot terminate: no systemd scope recorded and no pid known"
                )

            outcome["method"] = "process_group_signal"
            outcome["degraded"] = True
            _signal_group(handle.pid, outcome)

    def attach_command(self, handle: HostHandle) -> list[str]:
        """What the maintainer types to reattach. Printed by ``show``."""
        return [self._dtach, "-a", handle.socket_path]


def _signal_group(pid: int, outcome: dict[str, object]) -> None:
    import signal
    import time

    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        outcome["already_gone"] = True
        return
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

    def terminate(self, handle: HostHandle, scope: str | None = None) -> None:
        self._alive.discard(handle.socket_path)
        self._audit.record(
            "session.terminate",
            outcome="ok",
            target=handle.socket_path,
            simulated=True,
            detail={"scope": scope},
        )

    def attach_command(self, handle: HostHandle) -> list[str]:
        return ["dtach", "-a", handle.socket_path]
