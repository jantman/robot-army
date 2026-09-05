"""The live session registry: ``~/.claude/sessions/<pid>.json``.

An undocumented internal format, and load-bearing anyway — M0 measured it as an exact
1:1 registry of live sessions with no stale entries in the happy path, which turns the
database↔process join from best-effort into exact (R8).

Because it is undocumented, parsing is **gated on the ``version`` field**. An unrecognised
version raises a ``registry_version_unknown`` anomaly *once* and degrades to enumerating
``/proc/*/exe`` — never crashes, because a worker upgrade must not take the daemon down,
and never silently continues, because a degraded identification path is exactly the kind
of thing that must be visible.

**Absolute prohibition**: ``<pid>.<hash>.key`` files sit alongside these, mode 0600, and
appear to be session credentials. Nothing here opens, reads, copies, or logs them. There
is a test (T060) asserting no code path does.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from robot_army import procinfo
from robot_army.paths import claude_projects_dir, claude_registry_dir

#: The registry's ``version`` field is the **worker's own version string**, e.g.
#: ``"2.1.239"`` — measured, not assumed. It is gated on ``(major, minor)`` rather than the
#: exact string because patch releases happen constantly and a guard that trips on every
#: one of them is a guard that gets removed.
#:
#: Widening this set is a deliberate act after looking at a sample. Getting it *wrong* is
#: expensive in a specific way: an over-strict guard rejects every live entry, degrades
#: permanently to ``/proc``, and silently destroys the exact ``sessionId`` join that
#: dispatch confirmation depends on — so nothing would ever reach ``active``.
KNOWN_VERSIONS: frozenset[tuple[int, int]] = frozenset({(2, 1)})

#: Files we must never touch. Named here so the prohibition is greppable.
FORBIDDEN_SUFFIX = ".key"


def parse_version(value: object) -> tuple[int, int] | None:
    """``"2.1.239"`` → ``(2, 1)``. Anything else → ``None``."""
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def version_is_known(value: object) -> bool:
    parsed = parse_version(value)
    return parsed is not None and parsed in KNOWN_VERSIONS


#: The one ``status`` value that means "this worker is waiting for someone to type".
#:
#: Compared for equality rather than matched against a set of "busy" values, because the
#: set of things a worker can be doing is not ours to enumerate and will grow without
#: telling us. An unrecognised status is therefore *not idle*, which is the safe direction.
IDLE_STATUS = "idle"


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    session_id: str
    pid: int
    proc_start: str | None
    cwd: str | None
    status: str | None
    version: str | None
    source_file: str
    #: ``statusUpdatedAt``: when the worker last changed what it was doing, in epoch
    #: milliseconds. ``None`` when the field is absent or not an integer.
    status_updated_at: int | None = None

    def alive(self, *, proc_root: Path | None = None) -> bool:
        """Liveness by ``pid`` **and** ``proc_start`` (FR-038).

        ``pid`` alone is never sufficient identity: the kernel recycles PIDs, and a
        recycled one belonging to something unrelated must not read as a live session.
        """
        return procinfo.is_alive(self.pid, self.proc_start, root=proc_root)

    def idle_for(self, *, now_ms: int | None = None) -> float | None:
        """Seconds this worker has been idle, or ``None`` if that cannot be established.

        ``None`` is not "zero seconds" and must never be treated as a number by a caller.
        It is returned for every way of *not knowing*: a status that is not exactly
        ``idle``, an absent or non-integer ``statusUpdatedAt``, and a timestamp in the
        future (a clock that disagrees with ours is not evidence of anything).

        The asymmetry is deliberate and is what makes issue #138's retirement safe to hang
        off an undocumented file. Every unknown resolves to "not idle", so being wrong
        about this registry can *delay* a retirement; it can never cause one.
        """
        if self.status != IDLE_STATUS or self.status_updated_at is None:
            return None
        now = int(time.time() * 1000) if now_ms is None else now_ms
        elapsed = (now - self.status_updated_at) / 1000
        return None if elapsed < 0 else elapsed


@dataclass(frozen=True, slots=True)
class RegistryScan:
    entries: tuple[RegistryEntry, ...]
    unknown_versions: tuple[str | None, ...]
    unreadable: tuple[str, ...]
    degraded: bool = False
    #: The registry directory was absent, was not a directory, or could not be listed.
    #:
    #: Distinguished from an empty-but-present directory because those two are otherwise
    #: byte-for-byte identical at the glob — no version is refused, no file is unreadable,
    #: the scan simply returns nothing — and one of them means "the machine is idle" while
    #: the other means "the registry moved and this observation is worthless" (R4). It is
    #: the only failure mode in this file that reads as *free capacity*, which is the one
    #: direction a capacity error is allowed to cause harm in, so it is distinguished here
    #: at the only place that can still tell the difference.
    directory_missing: bool = False

    def by_session_id(self) -> dict[str, RegistryEntry]:
        return {entry.session_id: entry for entry in self.entries}

    def find(self, session_id: str) -> RegistryEntry | None:
        for entry in self.entries:
            if entry.session_id == session_id:
                return entry
        return None


@dataclass(frozen=True, slots=True)
class ParsedFile:
    """The outcome of reading one registry file. Exactly one field is meaningful.

    Three outcomes are deliberately distinguished, because collapsing any two of them
    would lose information the caller needs: a usable entry, a *refused version* (which
    must raise an anomaly and trigger the degraded path), and an unreadable file (which
    must be reported but does not imply the format changed). A fourth case — the file
    vanished between the glob and the read — is none of the above and is simply ignored.
    """

    entry: RegistryEntry | None = None
    version_refused: bool = False
    version_value: str | None = None
    error: str | None = None


def parse_entry(path: Path) -> ParsedFile:
    """Parse one registry file. Truncated and absent files are normal, not errors —
    the worker writes them while we read."""
    if path.name.endswith(FORBIDDEN_SUFFIX):
        # Defence in depth: callers already filter, but this is a hard rule and it is
        # cheap to enforce at the only function that opens one of these files.
        return ParsedFile(error=f"refusing to read credential-shaped file {path.name}")
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return ParsedFile()  # vanished mid-scan
    except OSError as exc:
        return ParsedFile(error=f"{path.name}: {exc}")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ParsedFile(error=f"{path.name}: unparseable JSON ({exc})")
    if not isinstance(payload, dict):
        return ParsedFile(error=f"{path.name}: expected a JSON object")

    version = payload.get("version")
    if not version_is_known(version):
        # A missing or unparseable version counts as unknown too. Guessing that an
        # unversioned file follows the version we know would be exactly the assumption
        # this guard exists to refuse.
        return ParsedFile(
            version_refused=True, version_value=str(version) if version is not None else None
        )

    session_id = payload.get("sessionId")
    pid = payload.get("pid")
    if not isinstance(session_id, str) or not isinstance(pid, int):
        return ParsedFile(error=f"{path.name}: missing sessionId or pid")

    proc_start = payload.get("procStart")

    # `status` and `statusUpdatedAt` ARE now used for a control decision, and this comment
    # used to say the opposite. Issue #138: the ordinary successful path ended with a
    # worker idling at a prompt forever, holding a capacity slot and raising an
    # `orphan_session` on every pass, because nothing in the system could tell "finished
    # and waiting" from "still working". These two fields can, and no other observable
    # could: transcript mtime was measured running 29 and 163 minutes ahead of the last
    # record inside the same file, so it reports activity that did not happen.
    #
    # Depending on an undocumented file for something that ends a process is safe here for
    # two reasons, and both must survive future editing. The `version` gate above already
    # refuses a shape we have not seen. And `RegistryEntry.idle_for` resolves *every*
    # unknown — absent status, unrecognised status, absent timestamp, wrong type, a
    # timestamp from the future — to "not idle", so a registry that changes under us delays
    # a retirement rather than causing one.
    #
    # `statusUpdatedAt` is epoch milliseconds. A non-integer is treated as absent rather
    # than raising, for the same reason `sessionId` and `pid` are checked rather than
    # trusted: a worker upgrade must not take the daemon down. `bool` is excluded
    # explicitly because it is an `int` subclass in Python and `True` is not a timestamp.
    status_updated_at = payload.get("statusUpdatedAt")
    if not isinstance(status_updated_at, int) or isinstance(status_updated_at, bool):
        status_updated_at = None

    return ParsedFile(
        entry=RegistryEntry(
            session_id=session_id,
            pid=pid,
            proc_start=str(proc_start) if proc_start is not None else None,
            cwd=str(payload["cwd"]) if payload.get("cwd") else None,
            status=str(payload["status"]) if payload.get("status") else None,
            version=str(version),
            source_file=str(path),
            status_updated_at=status_updated_at,
        )
    )


def scan(
    *,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
    live_only: bool = True,
) -> RegistryScan:
    """Read the whole registry, keeping only entries whose process is really alive."""
    directory = Path(registry_dir) if registry_dir else claude_registry_dir()
    entries: list[RegistryEntry] = []
    unknown: list[str | None] = []
    unreadable: list[str] = []

    # ``is_dir()`` answers False rather than raising for an absent path, a path that is
    # not a directory, and a parent we may not traverse — all three of which are the same
    # thing to a caller: no usable registry here.
    directory_missing = not directory.is_dir()
    candidates: list[Path] = []
    if not directory_missing:
        try:
            # ``os.listdir`` rather than ``Path.glob``: glob quietly swallows a directory
            # it is not permitted to read and yields nothing, which is exactly the silence
            # this flag exists to break. listdir raises, so "will not be listed" stays
            # distinguishable from "has nothing in it".
            names = sorted(os.listdir(directory))
        except OSError:
            # It exists but will not be listed. Same conclusion, different cause.
            directory_missing = True
        else:
            candidates = [directory / name for name in names if name.endswith(".json")]

    for path in candidates:
        # Never even stat a .key file. glob("*.json") already excludes them; this is the
        # belt to that braces.
        if path.name.endswith(FORBIDDEN_SUFFIX):
            continue
        parsed = parse_entry(path)
        if parsed.error:
            unreadable.append(parsed.error)
            continue
        if parsed.version_refused:
            unknown.append(parsed.version_value)
            continue
        if parsed.entry is None:
            continue  # vanished between the glob and the read
        if live_only and not parsed.entry.alive(proc_root=proc_root):
            continue
        entries.append(parsed.entry)

    return RegistryScan(
        entries=tuple(entries),
        unknown_versions=tuple(unknown),
        unreadable=tuple(unreadable),
        directory_missing=directory_missing,
    )


def scan_via_proc(
    worker_binaries: tuple[str, ...], *, proc_root: Path | None = None
) -> RegistryScan:
    """The degraded identification path, used when the registry version is unrecognised.

    Enumerates ``/proc/*/exe`` and classifies by ``/proc/<pid>/cwd``. It cannot recover a
    ``sessionId`` — that only exists in the registry — so entries come back with an empty
    session id and are usable only for the orphan sweep, never for the database join.

    **Never matches on command lines** (FR-039). M0 recorded two real incidents behind
    that rule: a ``pkill -f`` that killed the invoking shell, and a ``pgrep -f`` that
    matched kitty's ``run-shell`` wrapper and produced a wrong conclusion.
    """
    entries = [
        RegistryEntry(
            session_id="",
            pid=pid,
            proc_start=procinfo.starttime(pid, root=proc_root),
            cwd=cwd,
            status=None,
            version=None,
            source_file=exe_path,
        )
        for pid, exe_path, cwd in procinfo.find_by_exe(worker_binaries, root=proc_root)
    ]
    return RegistryScan(
        entries=tuple(entries), unknown_versions=(), unreadable=(), degraded=True
    )


def under_root(path: str | None, root: Path) -> bool:
    """Is a working directory inside the worktree root? Classifies orchestrator-owned
    sessions against the maintainer's own."""
    if not path:
        return False
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return True


def transcript_exists(session_id: str, *, home: Path | None = None) -> bool:
    """Does a resumable transcript exist for this session **right now**?

    M0 F19: a session can run, exit 0, and be permanently unresumable. One cause is a stray
    ``CLAUDE_CODE_CHILD_SESSION`` in the terminal daemon's environment silently disabling
    transcript saving; another is a session that died before it wrote anything. The session
    looks perfect either way, and only the missing transcript reveals it. Detecting that is
    what turns a silent failure into a ``no_transcript`` anomaly.

    **This function answers only "is it there now", and the timing is the caller's
    problem.** The worker writes its transcript when it starts processing, not at exec, so
    asking immediately after launch reliably gets ``False`` about a perfectly healthy
    session — which is issue #58, and why the only caller now waits out a grace period
    before believing the answer.
    """
    projects = Path(home) / ".claude" / "projects" if home else claude_projects_dir()
    if not projects.is_dir():
        return False
    try:
        return any(projects.rglob(f"{session_id}.jsonl"))
    except OSError:
        return False


def summarise(scan_result: RegistryScan, worktree_root: Path) -> dict[str, Any]:
    """The aggregate record a reconciliation pass logs, per the plan's Principle III gap."""
    ours = [e for e in scan_result.entries if under_root(e.cwd, worktree_root)]
    return {
        "sessions_found": len(scan_result.entries),
        "under_worktree_root": len(ours),
        "unknown_versions": list(dict.fromkeys(scan_result.unknown_versions)),
        "unreadable": list(scan_result.unreadable),
        "degraded": scan_result.degraded,
    }
