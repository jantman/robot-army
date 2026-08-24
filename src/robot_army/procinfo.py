"""Reads of ``/proc``. Stable kernel interfaces — the only consumed formats here not at
some upstream's whim (contracts/consumed-formats.md).

Two rules govern everything in this module:

1. **A process may vanish mid-read.** ``/proc/<pid>/*`` raises ``ProcessLookupError`` or
   ``FileNotFoundError`` at any moment, and that means "gone", not "error". Every
   function here returns ``None`` for a vanished process rather than raising.
2. **Never identify a process by its command line** (FR-039). ``cmdline`` is not read by
   anything in this codebase, and there is a test asserting no code path shells out to
   ``pgrep -f`` or ``pkill -f``. M0 recorded two real incidents behind this rule: a
   ``pkill -f`` that killed the invoking shell, and a ``pgrep -f`` that matched kitty's
   ``run-shell`` wrapper and produced a wrong conclusion.

The ``root`` parameter exists so tests can point at a fixture tree instead of mocking
the filesystem globally (research.md R20).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

PROC_ROOT = Path("/proc")

#: ``kitty-<pid>-<n>.scope`` or any other systemd scope, as it appears in a cgroup line.
_SCOPE_RE = re.compile(r"/([A-Za-z0-9@_.\\-]+\.scope)\b")


def _proc(root: Path | None) -> Path:
    return Path(root) if root is not None else PROC_ROOT


def exists(pid: int, *, root: Path | None = None) -> bool:
    return (_proc(root) / str(pid)).exists()


def starttime(pid: int, *, root: Path | None = None) -> str | None:
    """Field 22 of ``/proc/<pid>/stat`` — the kernel start-time in clock ticks.

    This is the PID-reuse guard (FR-038). ``pid`` alone is never sufficient identity:
    the kernel recycles PIDs, and a recycled one belonging to something unrelated must
    not be mistaken for a live session.

    Parsing note: field 2 (``comm``) is parenthesised and may itself contain spaces and
    parentheses, so the split has to start after the **last** ``)``, not the first.
    """
    stat_path = _proc(root) / str(pid) / "stat"
    try:
        raw = stat_path.read_text(encoding="utf-8", errors="replace")
    except (ProcessLookupError, FileNotFoundError, PermissionError):
        return None
    except OSError:
        return None
    close = raw.rfind(")")
    if close == -1:
        return None
    fields = raw[close + 2 :].split()
    # After comm, the remaining fields start at index 0 == field 3. starttime is
    # field 22, so index 22 - 3 == 19.
    if len(fields) <= 19:
        return None
    return fields[19]


def exe(pid: int, *, root: Path | None = None) -> str | None:
    """Resolved ``/proc/<pid>/exe``. Process identity in the fallback path (R8)."""
    link = _proc(root) / str(pid) / "exe"
    try:
        return os.readlink(link)
    except (ProcessLookupError, FileNotFoundError, PermissionError, OSError):
        return None


def cwd(pid: int, *, root: Path | None = None) -> str | None:
    """Resolved ``/proc/<pid>/cwd``. Classifies a process as orchestrator-owned or not."""
    link = _proc(root) / str(pid) / "cwd"
    try:
        return os.readlink(link)
    except (ProcessLookupError, FileNotFoundError, PermissionError, OSError):
        return None


def cgroup(pid: int, *, root: Path | None = None) -> str | None:
    """Raw ``/proc/<pid>/cgroup`` contents."""
    path = _proc(root) / str(pid) / "cgroup"
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (ProcessLookupError, FileNotFoundError, PermissionError, OSError):
        return None


def systemd_scope(pid: int, *, root: Path | None = None) -> str | None:
    """The systemd scope a process belongs to, e.g. ``kitty-1996044-3.scope``.

    Recorded at confirmation and treated as an **opaque handle** thereafter (M0 F18,
    research.md R9). Never recomputed: if kitty's naming scheme changes, we want a clear
    failure to stop the session rather than stopping the wrong one.
    """
    raw = cgroup(pid, root=root)
    if raw is None:
        return None
    for line in raw.splitlines():
        match = _SCOPE_RE.search(line)
        if match:
            return match.group(1)
    return None


def is_alive(pid: int, expected_start: str | None, *, root: Path | None = None) -> bool:
    """Liveness by ``pid`` **and** ``starttime`` (FR-038).

    A ``None`` ``expected_start`` degrades to a bare existence check, which is weaker —
    callers that have a recorded start time must pass it.
    """
    actual = starttime(pid, root=root)
    if actual is None:
        return False
    if expected_start is None:
        return True
    return actual == str(expected_start)


def iter_pids(*, root: Path | None = None) -> list[int]:
    """Every numeric entry under ``/proc``, tolerating the directory changing under us."""
    base = _proc(root)
    pids: list[int] = []
    try:
        entries = list(base.iterdir())
    except OSError:
        return []
    for entry in entries:
        if entry.name.isdigit():
            pids.append(int(entry.name))
    return sorted(pids)


def find_by_exe(
    binary_names: tuple[str, ...], *, root: Path | None = None
) -> list[tuple[int, str, str | None]]:
    """Enumerate processes whose resolved ``exe`` basename matches one of ``binary_names``.

    The fallback identification path for when the session registry's version is
    unrecognised (FR-039, R8). Returns ``(pid, exe, cwd)`` triples.
    """
    found: list[tuple[int, str, str | None]] = []
    for pid in iter_pids(root=root):
        path = exe(pid, root=root)
        if path is None:
            continue
        if os.path.basename(path) in binary_names:
            found.append((pid, path, cwd(pid, root=root)))
    return found
