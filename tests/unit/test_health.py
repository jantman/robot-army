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

from robot_army import channels, health


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


def webhook(url: str) -> channels.WebhookChannel:
    """The generic webhook as a channel.

    ``health.notify`` used to be this composer welded to this transport. Milestone 106
    separated them so a second channel could carry the same alert; these tests follow the
    seam and assert the same body they always did.
    """
    return channels.WebhookChannel(url)


def send_alert(url: str, report: health.HealthReport) -> tuple[bool, str]:
    return webhook(url).send(*health.alert_fields(report))


def test_notify_without_a_webhook_reports_that_rather_than_pretending(tmp_path):
    report = health.HealthReport(False, "stale")
    sent, message = send_alert("", report)
    assert sent is False
    assert "no webhook_url" in message


def test_notify_posts_a_plain_json_body(monkeypatch):
    """Vendor-neutral by design — and, since issue #106, honest about its limits.

    This docstring used to say a generic webhook "covers ntfy and Pushover without either
    becoming a dependency". The ntfy half is true and is what this test pins. The Pushover
    half was not: Pushover takes form-encoded parameters and rejects a JSON body, which is
    why ``channels.PushoverChannel`` exists and why this body did **not** have to change.
    """
    import httpx

    captured: dict = {}

    def fake_post(url, json=None, timeout=None):
        captured.update({"url": url, "json": json, "timeout": timeout})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    report = health.HealthReport(False, "heartbeat is 400s old", age_seconds=400)
    sent, message = send_alert("https://ntfy.invalid/robot-army", report)

    assert sent is True
    assert captured["url"] == "https://ntfy.invalid/robot-army"
    assert captured["json"]["message"] == "heartbeat is 400s old"
    assert captured["json"]["healthy"] is False
    assert captured["timeout"] is not None, "every network call sets an explicit timeout"
    assert "200" in message


def test_the_health_alert_body_is_unchanged_by_the_second_channel(monkeypatch):
    """The FR-016 regression gate for the health path.

    An installation with only a webhook must see byte-for-byte what it saw before, so the
    exact key set is pinned rather than a sample of it.
    """
    import httpx

    captured: dict = {}

    def fake_post(url, json=None, timeout=None):
        captured.update(json)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    send_alert("https://x.invalid", health.HealthReport(False, "stale", age_seconds=400))
    assert set(captured) == {"title", "message", "healthy", "age_seconds", "host", "ts"}
    assert captured["title"] == "robot-army health check failed"


def test_a_failing_webhook_is_reported_not_swallowed(monkeypatch):
    import httpx

    def fake_post(url, json=None, timeout=None):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(httpx, "post", fake_post)
    sent, message = send_alert("https://x.invalid", health.HealthReport(False, "stale"))
    assert sent is False
    assert "webhook POST failed" in message


def test_a_webhook_error_status_is_reported(monkeypatch):
    import httpx

    def fake_post(url, json=None, timeout=None):
        return httpx.Response(500, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    sent, message = send_alert("https://x.invalid", health.HealthReport(False, "stale"))
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


# -- the alert on every channel (issue #106) --------------------------------
#
# The dead-man's switch is the highest-value message this system sends. A channel that
# could carry item failures but not "the daemon is dead" would be the wrong half, so the
# alert fans out exactly as notifications do — with one deliberate difference, pinned at
# the bottom of this file.


def alert_context(config, conn, tmp_path, *, webhook="", pushover=False):
    """A context whose config has the channels this test wants."""
    from dataclasses import replace

    from robot_army import operations
    from robot_army.config import PushoverConfig

    creds = None
    if pushover:
        token, user = tmp_path / "po-token", tmp_path / "po-user"
        token.write_text("aTokenThatIs30CharactersLong00", encoding="utf-8")
        user.write_text("uUserKeyThatIs30CharsLong00000", encoding="utf-8")
        token.chmod(0o600)
        user.chmod(0o600)
        creds = PushoverConfig(token_file=token, user_key_file=user)

    adjusted = replace(
        config, health=replace(config.health, webhook_url=webhook), pushover=creds
    )
    return operations.build_context(adjusted)


def stale(layout):
    write_at(layout.heartbeat_path, age_seconds=4000)


def audit_records(layout, ctx, action):
    ctx.audit.close()
    out = []
    for path in sorted(layout.log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["action"] == action:
                out.append(record)
    return out


@pytest.fixture
def sent(monkeypatch):
    """Capture both transports, so a test can see which channel got what."""
    calls: list[dict] = []

    from robot_army import health as health_mod

    monkeypatch.setattr(
        health_mod,
        "post_json",
        lambda url, body, **k: (calls.append({"channel": "webhook", "url": url}), (True, "ok"))[1],
    )
    monkeypatch.setattr(
        health_mod,
        "post_form",
        lambda url, data, **k: (calls.append({"channel": "pushover", "url": url}), (True, "ok"))[1],
    )
    return calls


def test_the_alert_reaches_pushover_when_that_is_the_only_channel(
    config, conn, layout, tmp_path, sent
):
    """US4 AS1. The configuration this feature exists to make possible: no webhook at all,
    and the daemon's death still reaches a phone."""
    from robot_army import operations

    stale(layout)
    ctx = alert_context(config, conn, tmp_path, pushover=True)
    try:
        result = operations.health_check(ctx, do_notify=True)
    finally:
        ctx.close()

    assert result.code == 4
    assert [c["channel"] for c in sent] == ["pushover"]
    records = audit_records(layout, ctx, "health.notify")
    assert [r["detail"]["channel"] for r in records] == ["pushover"]


def test_the_alert_reaches_both_channels_with_independent_records(
    config, conn, layout, tmp_path, sent
):
    """US4 AS2."""
    from robot_army import operations

    stale(layout)
    ctx = alert_context(config, conn, tmp_path, webhook="https://hook", pushover=True)
    try:
        operations.health_check(ctx, do_notify=True)
    finally:
        ctx.close()

    assert [c["channel"] for c in sent] == ["webhook", "pushover"]
    records = audit_records(layout, ctx, "health.notify")
    assert [r["detail"]["channel"] for r in records] == ["webhook", "pushover"]
    assert all(r["outcome"] == "ok" for r in records)


def test_a_webhook_only_install_behaves_exactly_as_before(config, conn, layout, tmp_path, sent):
    """US4 AS3, and the FR-016 gate for this path."""
    from robot_army import operations

    stale(layout)
    ctx = alert_context(config, conn, tmp_path, webhook="https://hook")
    try:
        result = operations.health_check(ctx, do_notify=True)
    finally:
        ctx.close()

    assert [c["channel"] for c in sent] == ["webhook"]
    assert result.data["notified"] is True


def test_no_channel_configured_says_so_without_erroring(config, conn, layout, tmp_path, sent):
    """US4 AS4, FR-019. Nothing configured is an author who has not asked to be told, not a
    failure — but a silence indistinguishable from a delivered alert would be worse."""
    from robot_army import operations

    stale(layout)
    ctx = alert_context(config, conn, tmp_path)
    try:
        result = operations.health_check(ctx, do_notify=True)
    finally:
        ctx.close()

    assert result.code == 4, "the heartbeat is still stale"
    assert sent == []
    assert result.data["notified"] is False
    assert any("no notification channel configured" in line for line in result.lines)


def test_one_failing_channel_does_not_stop_the_alert_reaching_the_other(
    config, conn, layout, tmp_path, monkeypatch
):
    """A dead notification channel must not cost the author the one message that matters."""
    from robot_army import health as health_mod
    from robot_army import operations

    monkeypatch.setattr(health_mod, "post_json", lambda *a, **k: (False, "webhook POST failed"))
    delivered: list[str] = []
    monkeypatch.setattr(
        health_mod,
        "post_form",
        lambda url, data, **k: (delivered.append(url), (True, "ok"))[1],
    )

    stale(layout)
    ctx = alert_context(config, conn, tmp_path, webhook="https://hook", pushover=True)
    try:
        operations.health_check(ctx, do_notify=True)
    finally:
        ctx.close()

    assert len(delivered) == 1, "pushover still got it"
    outcomes = {
        r["detail"]["channel"]: r["outcome"] for r in audit_records(layout, ctx, "health.notify")
    }
    assert outcomes == {"webhook": "error", "pushover": "ok"}


@pytest.mark.parametrize("level", ["plan", "local", "no-remote", "live"])
def test_the_health_alert_ignores_the_effect_level_deliberately(
    config, conn, layout, tmp_path, sent, level
):
    """**Do not "fix" this into an effect-level check.**

    The stale-heartbeat alert has never been gated by the effect level: ``health_check``
    does not touch ``ctx.boundaries``, and it is the only sender that does not. That is a
    decision, not an oversight (research.md R2).

    ``robot-army health --notify`` takes no ``--effect-level`` flag, so it resolves its
    level from ``[daemon] effect_level``. Routing the alert through the notifier boundary
    would therefore silently disable the dead-man's switch for anyone running their daemon
    at ``local`` — a documented, supported posture. The effect level governs what the
    *daemon* does autonomously on the author's behalf; a human, or that human's systemd
    timer, running this command has already made the decision the effect level exists to
    withhold.

    Pushover joins the webhook on exactly those terms, which is why this is parametrised
    over every level rather than asserted once at ``live``.
    """
    from dataclasses import replace

    from robot_army import operations
    from robot_army.effects import EffectLevel

    stale(layout)
    lowered = replace(config, daemon=replace(config.daemon, effect_level=EffectLevel(level)))
    ctx = alert_context(lowered, conn, tmp_path, webhook="https://hook", pushover=True)
    try:
        assert ctx.effect_level == EffectLevel(level), "the context really is at that level"
        operations.health_check(ctx, do_notify=True)
    finally:
        ctx.close()

    assert [c["channel"] for c in sent] == ["webhook", "pushover"], (
        f"the alert must still be delivered at {level}"
    )
