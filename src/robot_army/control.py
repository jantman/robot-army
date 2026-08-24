"""Cross-process job requests: an empty marker file per forcible job (research.md R5).

This closes a gap milestone 001 left open rather than adding a feature. 001's CLI contract
promised that ``robot-army poll`` "signals it to poll on its next tick"; in fact
``operations.poll_now`` only printed how often the daemon polls, because ``Daemon.request()``
had no caller outside the process. FR-023 needs a real force, so the mechanism exists now
and both front ends use it.

**A file rather than a signal**, for three reasons that are all failure modes avoided:

* The daemon may be mid-tick. A marker waits; a signal needs a handler in a loop whose
  entire design is "one thread, no interleaving".
* Signalling means reading a PID from a lock file and trusting it — the identify-a-process-
  by-weaker-evidence pattern this project has already been bitten by (M0 F17).
* A marker is testable without a running process.

The base tick is 5 seconds, so "immediate" means "within one tick", which the response says
rather than implying instantaneity.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.paths import Layout

#: The only two job names a marker may carry. A literal tuple rather than a registry:
#: there are two forcible jobs and Principle I forbids machinery with one caller.
VALID_REQUESTS: tuple[str, ...] = ("poll", "reconcile")

#: Unrecognised filenames already reported, so a 5-second tick reports each once rather
#: than 17,000 times a day. Process-local by design: a restart re-reports, which is right,
#: because a restart is when someone is looking.
_REPORTED_UNKNOWN: set[str] = set()


class UnknownJob(ValueError):
    """A request for a job that does not exist. A usage error, never written to disk."""


def request_job(layout: Layout, name: str) -> bool:
    """Ask the daemon to run ``name`` on its next tick. Returns whether it was new.

    ``O_CREAT | O_EXCL`` makes the create atomic, so re-requesting a job whose marker is
    still pending is a harmless no-op rather than a second request — which matters because
    a double tap on a phone is the normal case, not the exceptional one.
    """
    if name not in VALID_REQUESTS:
        raise UnknownJob(f"unknown job {name!r}; valid jobs are {', '.join(VALID_REQUESTS)}")
    directory = layout.requests_dir
    directory.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(directory / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def pending(layout: Layout) -> list[str]:
    """Which markers are currently waiting. Read-only; used by tests and by ``status``."""
    directory = layout.requests_dir
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if p.name in VALID_REQUESTS)


def take_requests(layout: Layout, audit: AuditLog | None = None) -> list[str]:
    """Unlink every pending marker and return the job names it asked for.

    Unlinking **before** the job runs is deliberate. Interrupted between the unlink and the
    run, the forced flag is lost and the job happens on its ordinary interval — the cost is
    waiting out one interval. The alternative, unlinking after, risks running it twice, and
    a duplicate poll spends rate limit the daemon needs.

    An unrecognised file is **ignored and reported, never deleted**: deleting something the
    system does not understand is worse than leaving it, and the report is what stops it
    being a silent gap.
    """
    directory = layout.requests_dir
    if not directory.is_dir():
        return []
    taken: list[str] = []
    for entry in sorted(directory.iterdir()):
        if entry.name not in VALID_REQUESTS:
            if entry.name not in _REPORTED_UNKNOWN:
                _REPORTED_UNKNOWN.add(entry.name)
                if audit is not None:
                    audit.record(
                        "control.unknown_request",
                        outcome="error",
                        target=str(entry),
                        detail={
                            "name": entry.name,
                            "valid": list(VALID_REQUESTS),
                            "action": "left in place — deleting an unrecognised file is worse",
                        },
                    )
            continue
        try:
            entry.unlink()
        except FileNotFoundError:
            # Another reader took it. Only the daemon drains, so this is theoretical;
            # treating it as "already handled" is the only correct reading either way.
            continue
        taken.append(entry.name)
    return taken
