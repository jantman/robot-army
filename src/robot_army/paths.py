"""Filesystem layout (research.md R16), in one place so nothing hardcodes a path twice.

The split matters and is deliberate: sockets live under ``XDG_RUNTIME_DIR`` because it
is tmpfs and cleared on reboot — letting the kernel delete stale sockets is free, and it
removes a class of state reconciliation would otherwise have to reason about. Everything
that must survive a reboot lives under ``~/.local/state``.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

APP = "robot-army"


def unsafe_ancestor(path: Path) -> str | None:
    """The first directory at or above ``path`` a stranger could rearrange, as a reason.

    Lives here rather than beside its caller because two of them ask the same question:
    socket discovery, before it will speak to a candidate (RA-15), and configuration
    load, about the directory a configured glob is rooted in. One rule, one definition.

    Why the question is worth asking at all: ``lstat`` describes a file at an instant,
    and whatever resolves the name next does so a moment later. If any directory on the
    path lets somebody else unlink an entry, the file inspected and the file used can be
    different, and an ownership check becomes a check with a window after it.

    The sticky bit is the exemption rather than an oversight: it restricts unlinking and
    renaming to the entry's owner, which is exactly the missing property — and it is why
    ``/tmp`` (root-owned, ``1777``) may hold a socket even though a name in it proves
    nothing on its own. A directory owned by a third party is refused whatever its mode,
    because its owner can always replace what is inside it.

    Walked to the filesystem root rather than stopping at the parent, so there is no "how
    far up is far enough" to answer: a hostile directory anywhere on the path is the same
    attack. Four ``lstat`` calls for the runtime directory, two for ``/tmp``.

    ``path`` itself is included when it is a directory; pass a file and the walk starts
    at its parent.
    """
    ours = os.getuid()
    walk = [path, *path.parents] if path.is_dir() else list(path.parents)
    for directory in walk:
        try:
            info = os.lstat(directory)
        except OSError as exc:
            return f"directory {directory} cannot be inspected: {exc.strerror}"
        if stat.S_ISLNK(info.st_mode):
            # Named for what it is rather than left to the mode check below, which would
            # refuse it anyway — a symlink's own mode is 0777 on Linux — with a reason
            # about permissions that says nothing true about the link. What it resolves
            # to may differ by the time the name is used, and following it here to find
            # out is the substitution the caller is refusing in the first place.
            return f"directory {directory} is a symbolic link"
        if info.st_uid not in (ours, 0):
            return f"directory {directory} is owned by uid {info.st_uid}"
        writable_by_others = bool(info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
        if writable_by_others and not info.st_mode & stat.S_ISVTX:
            return f"directory {directory} is writable by others without the sticky bit"
    return None


def _xdg(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    return Path(value).expanduser() if value else default


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config")


def state_home() -> Path:
    return _xdg("XDG_STATE_HOME", Path.home() / ".local" / "state")


def runtime_dir() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR")
    if value:
        return Path(value)
    # No XDG_RUNTIME_DIR (a bare cron or ssh session): fall back to state, which is
    # durable rather than tmpfs. Stale sockets then need explicit pruning, which
    # reconcile does anyway.
    return state_home()


def default_config_path() -> Path:
    return config_home() / APP / "config.toml"


@dataclass(frozen=True, slots=True)
class Layout:
    """Every path the daemon writes, derived once from the state directory."""

    state_dir: Path
    socket_dir: Path

    @classmethod
    def default(cls) -> Layout:
        return cls(state_dir=state_home() / APP, socket_dir=runtime_dir() / APP)

    @classmethod
    def build(cls, state_dir: Path | None = None, socket_dir: Path | None = None) -> Layout:
        base = cls.default()
        return cls(
            state_dir=Path(state_dir).expanduser() if state_dir else base.state_dir,
            socket_dir=Path(socket_dir).expanduser() if socket_dir else base.socket_dir,
        )

    @property
    def db_path(self) -> Path:
        return self.state_dir / "state.db"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def session_log_dir(self) -> Path:
        return self.log_dir / "sessions"

    @property
    def spool_dir(self) -> Path:
        return self.state_dir / "spool" / "exits"

    @property
    def spool_rejected_dir(self) -> Path:
        return self.spool_dir / "rejected"

    @property
    def heartbeat_path(self) -> Path:
        return self.state_dir / "heartbeat.json"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "daemon.lock"

    @property
    def requests_dir(self) -> Path:
        """Cross-process job requests (milestone 002, research.md R5).

        Under ``state_dir`` rather than the runtime directory because a marker written
        while the daemon is down must still be there when it starts — including across a
        reboot, where the cost of a leftover marker is one redundant job.
        """
        return self.state_dir / "requests"

    def socket_for(self, item_id: int | str) -> Path:
        return self.socket_dir / f"{item_id}.sock"

    def ensure(self) -> None:
        """Create every directory the daemon writes into. Idempotent."""
        for directory in (
            self.state_dir,
            self.log_dir,
            self.session_log_dir,
            self.spool_dir,
            self.spool_rejected_dir,
            self.requests_dir,
            self.socket_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def claude_registry_dir() -> Path:
    """``~/.claude/sessions`` — the live session registry (contracts/consumed-formats.md)."""
    return Path.home() / ".claude" / "sessions"


def claude_projects_dir() -> Path:
    """``~/.claude/projects`` — where the worker writes session transcripts.

    A function rather than a constant for the same reason ``claude_registry_dir`` is one:
    it is the seam the test suite replaces. Without it a suite run reads the maintainer's
    real transcripts and the result depends on which sessions they happen to have run.
    """
    return Path.home() / ".claude" / "projects"


def claude_trust_file() -> Path:
    """``~/.claude.json`` — workspace trust."""
    return Path.home() / ".claude.json"


def atomic_write(path: Path, data: str | bytes, *, mode: int | None = None) -> None:
    """Write-fsync-rename, the pattern Principle IV prescribes.

    A partially written file is never observable to a later run, because the rename is
    atomic within a directory on Linux and the content is on disk before it happens.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if mode is None else mode)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)
