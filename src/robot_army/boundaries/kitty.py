"""The display: kitty, driven through its remote-control socket.

**Socket discovery, not prediction.** Kitty appends its PID to ``listen_on``, so no fixed
path exists. We glob the configured pattern, probe each candidate with
``kitty @ --to <s> ls`` under a short timeout, and take whichever answers. A dead socket
refuses in 14–25 ms (measured in M0), so probing several is cheap. ``--to`` is mandatory:
there is no ``KITTY_LISTEN_ON`` in a service environment.

**``open`` returning a window id is not evidence a session started.** It returns ``0``
and a valid id even when nothing ran — demonstrated three times in M0 (F16). Callers must
confirm independently, which is what ``dispatch.confirm`` does and why FR-025 exists.
"""

from __future__ import annotations

import glob
import json
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any

from robot_army.boundaries import BoundaryError, DisplayHandle
from robot_army.paths import unsafe_ancestor
from robot_army.subproc import run

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config

LAUNCH_TIMEOUT = 20.0

#: What kitty says when ``--match id:N`` names no window, measured on 0.48.2:
#: ``Error: No matching windows for expression: id:999999``, exit **1**.
#:
#: Matched as a substring because it is the only signal kitty gives, and the direction of a
#: mismatch is the safe one: if the wording ever changes, a genuinely-gone window starts
#: raising instead of returning ``False``, so the sweep retries it and logs an error rather
#: than silently marking it dealt with. A leaked window is the worse failure of the two.
NO_SUCH_WINDOW = "No matching windows"


def _refuse(candidate: str) -> str | None:
    """Why this candidate must not be spoken to, or ``None`` if it may be.

    A glob returns *names*, and a name in a directory somebody else can write is a name
    somebody else chose. Nothing about matching the pattern is evidence of who is
    listening, so a candidate earns the probe by being a socket, owned by this user,
    under directories no stranger can rearrange — and until it does, it is not addressed
    at all. That matters more than it sounds: the probe is the first thing an impostor
    would receive, and a launch carries the whole composed prompt and every ``--env``
    pair as arguments.

    The clauses live in ``contracts/discovery.md`` of the RA-15 feature. Returning a
    reason rather than a bool is deliberate: three surfaces quote it, because "kitty is
    not running" and "something is answering for kitty" send a maintainer to opposite
    ends of the machine.
    """
    path = candidate.removeprefix("unix:")
    try:
        # lstat, never stat. A stranger can create a *symbolic link* to the genuine
        # socket; following it would report our own uid and our own socket type for a
        # name they chose, which is the whole substitution this function exists to
        # prevent. Inspecting the link itself reports theirs — and a link is not a
        # socket, so it fails the next clause regardless.
        info = os.lstat(path)
    except OSError as exc:
        return f"cannot be inspected: {exc.strerror}"
    if not stat.S_ISSOCK(info.st_mode):
        return "not a socket"
    if info.st_uid != os.getuid():
        return f"owned by uid {info.st_uid}"
    return unsafe_ancestor(Path(path))


def describe_refusals(refusals: tuple[dict[str, str], ...]) -> str:
    """The refused candidates as a trailing sentence, or nothing at all.

    Three surfaces report a missing socket — the diagnostic, the daemon's startup check,
    and the error every launch failure quotes — and all three used to say the same thing
    whether nothing was running or something was answering in kitty's place. Those send a
    maintainer to opposite ends of the machine, so they get different words; when there
    are no refusals the wording is exactly what it was.
    """
    if not refusals:
        return ""
    listed = "; ".join(f"{r['socket']} ({r['reason']})" for r in refusals)
    return f" {len(refusals)} candidate(s) were found and refused: {listed}"


class KittyDisplay:
    def __init__(self, config: Config, audit: AuditLog) -> None:
        self._config = config
        self._audit = audit
        self._socket: str | None = None
        self._refusals: tuple[dict[str, str], ...] = ()

    # -- discovery ---------------------------------------------------------

    @property
    def refusals(self) -> tuple[dict[str, str], ...]:
        """Candidates the last discovery declined, and why.

        Read by ``doctor``, by the daemon's startup check, and by the error raised when
        no socket is available, so that "nothing matched" and "something matched and was
        refused" are never reported with the same words.
        """
        return self._refusals

    def probe(self) -> str | None:
        """Find a control socket that answers. Cached for the process's lifetime.

        Cached deliberately: kitty restarting means a new PID and a new socket, and the
        right response to that is a clear dispatch failure that a human notices, not a
        silent re-discovery that hides a terminal having died and come back.

        The *path* is cached; the trust in it is not. Checking once at discovery would
        have left the finding half-closed: the sticky bit stops a stranger unlinking
        kitty's socket, but not claiming the path after kitty exits and frees it itself.
        A daemon outliving a kitty restart would then keep dispatching down a name that
        had become somebody else's, having checked it only when it was still ours. So the
        cached path is re-checked on the way out, every time.

        A cache that fails the check is *not* re-discovered — that is the silent recovery
        this docstring already refuses. It keeps failing loudly until a human restarts the
        daemon, which is the same answer a restarted kitty has always got.
        """
        if self._socket is not None:
            reason = _refuse(self._socket)
            if reason is None:
                return self._socket
            self._refusals = ({"socket": self._socket, "reason": reason},)
            self._audit.record(
                "kitty.probe",
                outcome="error",
                target=self._socket,
                detail={
                    "refused": list(self._refusals),
                    "error": "the cached socket is no longer usable",
                },
            )
            return None
        pattern = self._config.terminal.socket_glob
        timeout = float(self._config.terminal.probe_timeout_seconds)
        candidates = sorted(glob.glob(pattern), reverse=True)
        tried: list[dict[str, Any]] = []
        refused: list[dict[str, str]] = []
        for candidate in candidates:
            target = candidate if candidate.startswith("unix:") else f"unix:{candidate}"
            reason = _refuse(target)
            if reason is not None:
                # Nothing is run against it. A refusal that probed first would be a
                # refusal issued after the disclosure it exists to prevent.
                refused.append({"socket": target, "reason": reason})
                continue
            result = run(
                [self._config.terminal.binary, "@", "--to", target, "ls"],
                timeout=timeout,
                audit=None,  # probes are aggregated into the one record below
                check=False,
            )
            tried.append({"socket": target, "exit": result.returncode})
            if result.ok:
                self._socket = target
                self._refusals = tuple(refused)
                self._audit.record(
                    "kitty.probe",
                    outcome="ok",
                    target=target,
                    detail={
                        "pattern": pattern,
                        "candidates": len(candidates),
                        "tried": tried,
                        "refused": refused,
                    },
                )
                return target
        self._refusals = tuple(refused)
        self._audit.record(
            "kitty.probe",
            outcome="error",
            detail={
                "pattern": pattern,
                "candidates": len(candidates),
                "tried": tried,
                "refused": refused,
                "error": "no candidate socket answered",
            },
        )
        return None

    def _require_socket(self) -> str:
        socket = self.probe()
        if socket is None:
            raise BoundaryError(
                f"no kitty control socket answered {self._config.terminal.socket_glob!r}; "
                "is kitty running with allow_remote_control and listen_on set?"
                + describe_refusals(self.refusals)
            )
        return socket

    def _kitty(self, args: list[str], *, timeout: float, action: str) -> Any:
        socket = self._require_socket()
        return run(
            [self._config.terminal.binary, "@", "--to", socket, *args],
            timeout=timeout,
            audit=self._audit,
            action=action,
            check=False,
        )

    # -- windows -----------------------------------------------------------

    def open(
        self,
        cwd: str,
        argv: list[str],
        title: str,
        user_vars: dict[str, str],
        env: dict[str, str],
    ) -> DisplayHandle:
        """Launch a new tab running ``argv``.

        ``--hold`` is always passed so a failed launch leaves a readable window instead
        of one that vanishes instantly (M0 F11) — that window is often the only evidence
        of what went wrong.

        Sessions inherit the *terminal daemon's* environment, not this process's (M0
        F19), so anything the session needs is passed explicitly via ``--env``.
        """
        args = ["launch", "--type=tab", "--hold", "--cwd", cwd, "--title", title]
        for key, value in user_vars.items():
            args += ["--var", f"{key}={value}"]
        for key, value in env.items():
            args += ["--env", f"{key}={value}"]
        args += ["--", *argv]

        with self._audit.action(
            "kitty.launch",
            target=title,
            detail={"cwd": cwd, "user_vars": dict(user_vars), "argv": argv, "env": dict(env)},
        ) as outcome:
            result = self._kitty(args, timeout=LAUNCH_TIMEOUT, action="kitty.subprocess")
            outcome["exit"] = result.returncode
            if not result.ok:
                raise BoundaryError(f"kitty launch failed: {result.output}")
            try:
                window_id = int(result.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError) as exc:
                raise BoundaryError(
                    f"kitty launch returned unparseable window id: {result.stdout!r}"
                ) from exc
            outcome["window_id"] = window_id
            # Deliberately recorded, because it is the finding most likely to mislead a
            # future reader of this log: this id proves a window, not a session.
            outcome["note"] = "window id is not evidence a session started (M0 F16)"
        return DisplayHandle(window_id=window_id, title=title, user_vars=dict(user_vars))

    def _ls(self) -> list[dict[str, Any]]:
        result = self._kitty(["ls"], timeout=LAUNCH_TIMEOUT, action="kitty.subprocess")
        if not result.ok:
            raise BoundaryError(f"kitty ls failed: {result.output}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise BoundaryError(f"kitty ls returned unparseable JSON: {exc}") from exc

    def _windows(self) -> list[dict[str, Any]]:
        windows: list[dict[str, Any]] = []
        for os_window in self._ls():
            for tab in os_window.get("tabs", []):
                windows.extend(tab.get("windows", []))
        return windows

    def is_open(self, handle: DisplayHandle) -> bool:
        return any(w.get("id") == handle.window_id for w in self._windows())

    def close(self, handle: DisplayHandle) -> bool:
        """Close one window. ``True`` if kitty really closed one.

        **Measured, because the obvious assumption is wrong**: ``kitty @ close-window``
        against an id that no longer matches exits **1** with ``No matching windows for
        expression: id:N``, not 0. ``_kitty`` passes ``check=False``, so that failure was
        being swallowed and an already-closed window read as a successful close — which
        made ``windows_closed`` overstate what the system had done.

        Returning the answer costs nothing: the exit status is already in hand. A ``False``
        with an unusual cause is still fully reconstructable, because ``kitty.subprocess``
        records the command and its output either way.
        """
        with self._audit.action(
            "kitty.close_window", target=str(handle.window_id)
        ) as outcome:
            result = self._kitty(
                ["close-window", "--match", f"id:{handle.window_id}"],
                timeout=LAUNCH_TIMEOUT,
                action="kitty.subprocess",
            )
            if result.ok:
                outcome["closed"] = True
                return True
            # **"It failed" and "there was nothing to close" are different answers, and
            # collapsing them leaks a window permanently.** `_kitty` passes `check=False`,
            # so a transient non-zero exit and a timeout arrive here looking exactly like a
            # window that was already gone. The caller settles an item on ``False`` — that
            # is the whole point of returning it — so a real failure reported as ``False``
            # would mark the item answered-for and never revisit it, with nothing logged as
            # an error. Caught in review of the pull request that added this return value.
            if not result.timed_out and NO_SUCH_WINDOW in result.output:
                outcome["closed"] = False
                outcome["output"] = result.output
                return False
            raise BoundaryError(f"kitty close-window failed: {result.output}")

    def find_by_var(self, key: str, value: str) -> DisplayHandle | None:
        """Exact lookup by user variable.

        Walking ``foreground_processes`` instead would be fragile: ``--hold`` inserts a
        ``kitten run-shell`` layer that repeats the whole command in its own argv, so the
        same string appears at several depths. M0 records that producing a wrong
        conclusion during the spike.
        """
        for window in self._windows():
            user_vars = window.get("user_vars") or {}
            if user_vars.get(key) == value:
                return DisplayHandle(
                    window_id=int(window["id"]),
                    title=str(window.get("title") or ""),
                    user_vars={str(k): str(v) for k, v in user_vars.items()},
                )
        return None

    def list_by_var(self, key: str) -> list[DisplayHandle]:
        """Every window carrying ``key``. One ``kitty @ ls`` for the whole answer.

        The sweep that closes a finished item's windows cannot use ``find_by_var``: it
        needs *all* of an item's windows, because every attempt that was resumed or
        restarted left one behind and they all carry the same marker. Looping the singular
        lookup would also mean one subprocess per candidate item per pass.

        A window with no user variables at all — everything the maintainer opened
        themselves — is skipped here rather than filtered by the caller, so nothing that
        this system did not open can reach a decision about closing it.
        """
        found: list[DisplayHandle] = []
        for window in self._windows():
            user_vars = window.get("user_vars") or {}
            if key not in user_vars:
                continue
            found.append(
                DisplayHandle(
                    window_id=int(window["id"]),
                    title=str(window.get("title") or ""),
                    user_vars={str(k): str(v) for k, v in user_vars.items()},
                )
            )
        return found

    def send_text(self, handle: DisplayHandle, text: str) -> None:
        """Type into a window. Terminated with ``\\r``, never ``\\n``.

        ``\\n`` types the text without submitting it, which looks exactly like the
        command silently failing.
        """
        payload = text if text.endswith("\r") else text + "\r"
        with self._audit.action(
            "kitty.send_text", target=str(handle.window_id), detail={"chars": len(payload)}
        ):
            self._kitty(
                ["send-text", "--match", f"id:{handle.window_id}", payload],
                timeout=LAUNCH_TIMEOUT,
                action="kitty.subprocess",
            )

    def window_state(self, handle: DisplayHandle) -> dict[str, Any] | None:
        """Whatever kitty knows about a window — captured when a launch is unconfirmed."""
        try:
            for window in self._windows():
                if window.get("id") == handle.window_id:
                    return {
                        "id": window.get("id"),
                        "title": window.get("title"),
                        "cwd": window.get("cwd"),
                        "pid": window.get("pid"),
                        "user_vars": window.get("user_vars"),
                    }
        except BoundaryError:
            return None
        return None


class SimulatedDisplay:
    """Records the launch it would have made and hands back a valid window id."""

    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit
        self._next_id = 9000
        self._windows: dict[int, DisplayHandle] = {}

    def probe(self) -> str | None:
        return "unix:/tmp/simulated-kitty"

    def open(
        self,
        cwd: str,
        argv: list[str],
        title: str,
        user_vars: dict[str, str],
        env: dict[str, str],
    ) -> DisplayHandle:
        self._next_id += 1
        handle = DisplayHandle(
            window_id=self._next_id,
            title=title,
            user_vars=dict(user_vars),
            simulated=True,
        )
        self._windows[handle.window_id] = handle
        self._audit.record(
            "kitty.launch",
            outcome="ok",
            target=title,
            simulated=True,
            detail={
                "cwd": cwd,
                "argv": argv,
                "user_vars": dict(user_vars),
                "env": dict(env),
                "window_id": handle.window_id,
            },
        )
        return handle

    def is_open(self, handle: DisplayHandle) -> bool:
        return handle.window_id in self._windows

    def close(self, handle: DisplayHandle) -> bool:
        """``True`` only if this object was holding that window, mirroring the real one."""
        removed = self._windows.pop(handle.window_id, None) is not None
        self._audit.record(
            "kitty.close_window",
            outcome="ok",
            target=str(handle.window_id),
            simulated=True,
            detail={"closed": removed},
        )
        return removed

    def find_by_var(self, key: str, value: str) -> DisplayHandle | None:
        for handle in self._windows.values():
            if handle.user_vars.get(key) == value:
                return handle
        return None

    def list_by_var(self, key: str) -> list[DisplayHandle]:
        """Answered from the windows this object was asked to open.

        A simulated run therefore exercises the whole decision path — listing, identifying
        and closing — against windows it created itself, rather than skipping it.
        """
        return [handle for handle in self._windows.values() if key in handle.user_vars]

    def send_text(self, handle: DisplayHandle, text: str) -> None:
        self._audit.record(
            "kitty.send_text",
            outcome="ok",
            target=str(handle.window_id),
            simulated=True,
            detail={"text": text},
        )

    def window_state(self, handle: DisplayHandle) -> dict[str, Any] | None:
        found = self._windows.get(handle.window_id)
        return {"id": found.window_id, "title": found.title, "simulated": True} if found else None
