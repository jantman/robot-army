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
from robot_army.subproc import run

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config

LAUNCH_TIMEOUT = 20.0


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
    return _unsafe_directory(Path(path))


def _unsafe_directory(candidate: Path) -> str | None:
    """The first directory above ``candidate`` a stranger could rearrange, as a reason.

    ``lstat`` describes the file at an instant; ``kitty @ --to`` resolves the name again
    a moment later. If any directory on the path lets somebody else unlink an entry, the
    name inspected and the name used can be different files, and the ownership check
    becomes a check with a window after it.

    The sticky bit is the exemption rather than an oversight: it restricts unlinking and
    renaming to the entry's owner, which is precisely the missing property — and it is
    why ``/tmp`` (root-owned, ``1777``) may hold a socket even though a name in it is
    worth nothing on its own. A directory owned by a third party is refused whatever its
    mode, because its owner can always replace what is inside it.

    Walked to the filesystem root rather than stopping at the parent, so there is no
    "how far up is far enough" to get wrong: a hostile directory anywhere on the path is
    the same attack. Four ``stat`` calls for the runtime directory, two for ``/tmp``.
    """
    ours = os.getuid()
    for directory in candidate.parents:
        try:
            info = os.lstat(directory)
        except OSError as exc:
            return f"directory {directory} cannot be inspected: {exc.strerror}"
        if info.st_uid not in (ours, 0):
            return f"directory {directory} is owned by uid {info.st_uid}"
        writable_by_others = bool(info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
        if writable_by_others and not info.st_mode & stat.S_ISVTX:
            return f"directory {directory} is writable by others without the sticky bit"
    return None


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
        """
        if self._socket is not None:
            return self._socket
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

    def close(self, handle: DisplayHandle) -> None:
        with self._audit.action("kitty.close_window", target=str(handle.window_id)):
            self._kitty(
                ["close-window", "--match", f"id:{handle.window_id}"],
                timeout=LAUNCH_TIMEOUT,
                action="kitty.subprocess",
            )

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

    def close(self, handle: DisplayHandle) -> None:
        self._windows.pop(handle.window_id, None)
        self._audit.record(
            "kitty.close_window", outcome="ok", target=str(handle.window_id), simulated=True
        )

    def find_by_var(self, key: str, value: str) -> DisplayHandle | None:
        for handle in self._windows.values():
            if handle.user_vars.get(key) == value:
                return handle
        return None

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
