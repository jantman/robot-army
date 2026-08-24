"""Filesystem layout (research.md R16), in one place so nothing hardcodes a path twice.

The split matters and is deliberate: sockets live under ``XDG_RUNTIME_DIR`` because it
is tmpfs and cleared on reboot — letting the kernel delete stale sockets is free, and it
removes a class of state reconciliation would otherwise have to reason about. Everything
that must survive a reboot lives under ``~/.local/state``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP = "robot-army"


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
