"""Heartbeat writing and staleness detection (T136, T137).

The essential insight (R15): **a dead daemon cannot report its own death**, so the checker
is a separate process and the systemd timer is the actual dead-man's switch. These tests
cover the evidence that timer reads, and the boundaries at which it fires.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from robot_army import health


def write_at(path, *, age_seconds: float, **kwargs) -> None:
    stamp = (datetime.now(UTC) - timedelta(seconds=age_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "ts": stamp,
        "pid": 1234,
        "effect_level": "live",
        "activity": "idle",
        "cycles": 7,
        **kwargs,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_a_fresh_heartbeat_is_healthy(tmp_path):
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=5)
    report = health.check(path, max_age_seconds=180)
    assert report.healthy is True
    assert report.age_seconds is not None and report.age_seconds < 10


def test_the_staleness_boundary_is_exact(tmp_path):
    """The comparison is ``>``, so the threshold itself is inside the healthy window.
    Stated as a test because "stale after N seconds" is ambiguous about the boundary, and
    pinned with an injected clock because whole-second timestamps cannot express it."""
    path = tmp_path / "heartbeat.json"
    written = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
    path.write_text(
        json.dumps({"ts": written.strftime("%Y-%m-%dT%H:%M:%SZ"), "pid": 1}), encoding="utf-8"
    )

    at_threshold = written + timedelta(seconds=60)
    assert health.check(path, max_age_seconds=60, now=at_threshold).healthy is True

    just_past = written + timedelta(seconds=60, microseconds=1)
    assert health.check(path, max_age_seconds=60, now=just_past).healthy is False


def test_a_heartbeat_past_the_threshold_is_stale(tmp_path):
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=200)
    report = health.check(path, max_age_seconds=180)
    assert report.healthy is False
    assert "past the 180s threshold" in report.reason
    assert "pid 1234" in report.reason


def test_an_absent_heartbeat_is_distinguished_from_a_stale_one(tmp_path):
    """"Never started" and "died an hour ago" call for different actions."""
    report = health.check(tmp_path / "nothing.json", max_age_seconds=180)
    assert report.healthy is False
    assert "never run" in report.reason
    assert report.age_seconds is None


def test_an_unparseable_heartbeat_is_reported_as_such(tmp_path):
    path = tmp_path / "heartbeat.json"
    path.write_text("{not json", encoding="utf-8")
    report = health.check(path, max_age_seconds=180)
    assert report.healthy is False
    assert "not valid JSON" in report.reason


def test_a_heartbeat_with_no_timestamp_is_unhealthy(tmp_path):
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps({"pid": 1}), encoding="utf-8")
    assert health.check(path, max_age_seconds=180).healthy is False


def test_an_unparseable_timestamp_is_unhealthy(tmp_path):
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps({"ts": "yesterday"}), encoding="utf-8")
    report = health.check(path, max_age_seconds=180)
    assert report.healthy is False
    assert "unparseable" in report.reason


# -- writing ---------------------------------------------------------------


def test_the_heartbeat_carries_the_current_activity(tmp_path):
    """FR-063: a long preparation step must be visible as work rather than looking like
    a hang. That difference is the whole reason ``activity`` is in the payload."""
    path = tmp_path / "heartbeat.json"
    beat = health.write_heartbeat(
        path,
        effect_level="local",
        activity="preparing worktree for item 42",
        cycles=3,
        dispatched=1,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["activity"] == "preparing worktree for item 42"
    assert payload["effect_level"] == "local", "FR-057: the level is in the liveness signal"
    assert payload["pid"] == os.getpid()
    assert payload["cycles"] == 3
    assert beat.ts.endswith("Z")


def test_a_partial_heartbeat_is_never_observable(tmp_path):
    """T137. Write-fsync-rename: a process killed mid-write must not leave a truncated
    file, which the checker would read as corruption and report as a false alarm at the
    exact moment the daemon was healthy."""
    path = tmp_path / "heartbeat.json"
    health.write_heartbeat(path, effect_level="live", activity="idle", cycles=1)

    for cycle in range(2, 40):
        health.write_heartbeat(
            path, effect_level="live", activity="x" * cycle * 40, cycles=cycle
        )
        # Whatever a concurrent reader sees at any point, it is a complete document.
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["cycles"] == cycle

    assert not list(tmp_path.glob("*.tmp")), "no temporary file may be left behind"


def test_the_heartbeat_is_written_atomically_via_a_rename(tmp_path, monkeypatch):
    """Asserted mechanically: the final path must never be opened for writing directly."""
    path = tmp_path / "heartbeat.json"
    opened_for_write: list[str] = []
    real_open = os.open

    def recording_open(target, flags, *args, **kwargs):
        if flags & os.O_WRONLY or flags & os.O_RDWR:
            opened_for_write.append(str(target))
        return real_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)
    health.write_heartbeat(path, effect_level="live", activity="idle", cycles=1)

    assert opened_for_write, "the recorder saw nothing; the test would pass vacuously"
    assert str(path) not in opened_for_write
    assert all(target.endswith(".tmp") for target in opened_for_write)


def test_the_heartbeat_is_world_readable_so_a_timer_can_read_it(tmp_path):
    import stat

    path = tmp_path / "heartbeat.json"
    health.write_heartbeat(path, effect_level="live", activity="idle", cycles=1)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


# -- notification ----------------------------------------------------------


def test_notify_without_a_webhook_reports_that_rather_than_pretending(tmp_path):
    report = health.HealthReport(False, "stale")
    sent, message = health.notify("", report)
    assert sent is False
    assert "no webhook_url" in message


def test_notify_posts_a_plain_json_body(monkeypatch):
    """Vendor-neutral by design: a generic webhook covers ntfy and Pushover without
    either becoming a dependency."""
    import httpx

    captured: dict = {}

    def fake_post(url, json=None, timeout=None):
        captured.update({"url": url, "json": json, "timeout": timeout})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    report = health.HealthReport(False, "heartbeat is 400s old", age_seconds=400)
    sent, message = health.notify("https://ntfy.invalid/robot-army", report)

    assert sent is True
    assert captured["url"] == "https://ntfy.invalid/robot-army"
    assert captured["json"]["message"] == "heartbeat is 400s old"
    assert captured["json"]["healthy"] is False
    assert captured["timeout"] is not None, "every network call sets an explicit timeout"
    assert "200" in message


def test_a_failing_webhook_is_reported_not_swallowed(monkeypatch):
    import httpx

    def fake_post(url, json=None, timeout=None):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(httpx, "post", fake_post)
    sent, message = health.notify("https://x.invalid", health.HealthReport(False, "stale"))
    assert sent is False
    assert "webhook POST failed" in message


def test_a_webhook_error_status_is_reported(monkeypatch):
    import httpx

    def fake_post(url, json=None, timeout=None):
        return httpx.Response(500, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    sent, message = health.notify("https://x.invalid", health.HealthReport(False, "stale"))
    assert sent is False
    assert "HTTP 500" in message


@pytest.mark.parametrize("age", [0, 1, 59])
def test_ages_inside_the_window_are_healthy(tmp_path, age):
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=age)
    assert health.check(path, max_age_seconds=60).healthy is True


@pytest.mark.parametrize("age", [61, 120, 3600])
def test_beyond_the_threshold_is_stale(tmp_path, age):
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=age)
    assert health.check(path, max_age_seconds=60).healthy is False
