"""The liveness signal and its checker.

The essential insight (research.md R15): **a dead daemon cannot report its own death**, so
the checker must be a separate process. That makes the systemd user timer the actual
dead-man's switch, and the daemon's heartbeat merely the evidence it reads.

The heartbeat carries the *current activity*, not just a timestamp, so a long preparation
step is visible as work rather than looking like a hang (FR-063). That distinction is the
difference between "it is busy fetching a 400 MB repository" and "it is wedged".
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from robot_army.paths import atomic_write


@dataclass(frozen=True, slots=True)
class Heartbeat:
    ts: str
    pid: int
    effect_level: str
    activity: str
    cycles: int
    dispatched: int = 0
    errors: int = 0
    #: FR-036. A first-class field rather than a member of ``extra`` because
    #: ``docs/state.md`` documents this file's shape for a human reading it at 2am, and a
    #: named field is what that reader will look for. It defaults to ``False``, so a
    #: heartbeat written by an older build still parses.
    dispatch_paused: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


def write_heartbeat(
    path: Path,
    *,
    effect_level: str,
    activity: str,
    cycles: int,
    dispatched: int = 0,
    errors: int = 0,
    dispatch_paused: bool = False,
    extra: dict[str, Any] | None = None,
) -> Heartbeat:
    """Write the heartbeat atomically.

    Write-fsync-rename, so a process killed mid-write never leaves a partial file
    observable to the checker — which would otherwise read as corruption and report a
    false alarm at the exact moment the daemon was healthy.
    """
    beat = Heartbeat(
        ts=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        pid=os.getpid(),
        effect_level=effect_level,
        activity=activity,
        cycles=cycles,
        dispatched=dispatched,
        errors=errors,
        dispatch_paused=dispatch_paused,
        extra=extra or {},
    )
    atomic_write(Path(path), beat.to_json(), mode=0o644)
    return beat


@dataclass(frozen=True, slots=True)
class HealthReport:
    healthy: bool
    reason: str
    age_seconds: float | None = None
    heartbeat: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "reason": self.reason,
            "age_seconds": self.age_seconds,
            "heartbeat": self.heartbeat,
        }


def check(
    path: Path, *, max_age_seconds: float, now: datetime | None = None
) -> HealthReport:
    """Read the heartbeat and judge it.

    Absent, unreadable, and stale are three different reasons and are reported as such:
    "never started" and "died an hour ago" call for different actions.

    ``now`` exists so the staleness boundary can be tested exactly. Timestamps are
    written to whole-second resolution, so a test that writes "sixty seconds ago" and
    reads immediately measures 60.4 seconds and cannot pin down the comparison.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return HealthReport(False, f"no heartbeat file at {path} — the daemon has never run")
    except OSError as exc:
        return HealthReport(False, f"could not read {path}: {exc}")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return HealthReport(False, f"heartbeat at {path} is not valid JSON: {exc}")
    if not isinstance(payload, dict) or "ts" not in payload:
        return HealthReport(False, f"heartbeat at {path} has no timestamp")

    try:
        stamp = datetime.strptime(str(payload["ts"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return HealthReport(False, f"heartbeat timestamp is unparseable: {payload['ts']!r}")

    age = ((now or datetime.now(UTC)) - stamp).total_seconds()
    if age > max_age_seconds:
        return HealthReport(
            False,
            (
                f"heartbeat is {int(age)}s old, past the {int(max_age_seconds)}s threshold "
                f"(pid {payload.get('pid')}, last activity {payload.get('activity')!r})"
            ),
            age_seconds=age,
            heartbeat=payload,
        )
    return HealthReport(
        True,
        f"heartbeat is {int(age)}s old (pid {payload.get('pid')}, {payload.get('activity')})",
        age_seconds=age,
        heartbeat=payload,
    )


def notify(webhook_url: str, report: HealthReport, *, timeout: float = 10.0) -> tuple[bool, str]:
    """POST a plain JSON body to the configured webhook. Vendor-neutral by design.

    A generic webhook covers ntfy and Pushover — both named in the planning document —
    without either becoming a dependency. The timeout is explicit because Principle IV
    requires it of every network call, including this one.
    """
    if not webhook_url:
        return False, "no webhook_url configured"
    import httpx

    body = {
        "title": "robot-army health check failed",
        "message": report.reason,
        "healthy": report.healthy,
        "age_seconds": report.age_seconds,
        "host": os.uname().nodename,
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        response = httpx.post(webhook_url, json=body, timeout=timeout)
    except httpx.HTTPError as exc:
        return False, f"webhook POST failed: {exc}"
    if response.status_code >= 400:
        return False, f"webhook returned HTTP {response.status_code}"
    return True, f"notified {webhook_url} (HTTP {response.status_code})"
