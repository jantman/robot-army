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


# -- the lock, the other half of the evidence (issue #52) -------------------
#
# The heartbeat's age can only say a daemon has *stopped beating*, and never sooner than the
# staleness threshold. The lock says the process is gone, and says it at once. These pin the
# derivation in contracts/health-verdict.md row by row.


def held(path, holder=1234):
    """A lock reading as ``daemon.observe_lock`` would return it for a running daemon."""
    return health.LockReading(health.LockState.HELD, path, str(holder))


def unheld(path):
    return health.LockReading(health.LockState.UNHELD, path)


def unreadable_lock(path):
    return health.LockReading(health.LockState.UNKNOWN, path)


@pytest.mark.parametrize("age", [1, 30, 179, 300])
def test_a_released_lock_is_a_death_at_every_heartbeat_age(tmp_path, age):
    """The bug, and the whole of the ~180s detection floor.

    A heartbeat written a second ago says only that something was alive a second ago. The
    reported incident had ``health`` printing ``ok`` and exiting 0 for 183 seconds while the
    web interface, which reads the lock, said ``DAEMON NOT RUNNING`` within one.
    """
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=age)

    report = health.check(path, max_age_seconds=180, lock=unheld(tmp_path / "daemon.lock"))

    assert report.state is health.HealthState.DIED
    assert report.healthy is False
    assert "the daemon is gone" in report.reason
    assert str(tmp_path / "daemon.lock") in report.reason
    assert report.age_seconds is not None


def test_a_held_lock_and_a_fresh_heartbeat_reads_exactly_as_it_always_did(tmp_path):
    """The regression that matters most: the switch must not learn to cry wolf."""
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=5)

    with_lock = health.check(path, max_age_seconds=180, lock=held(tmp_path / "daemon.lock"))
    without = health.check(path, max_age_seconds=180)

    assert with_lock.state is health.HealthState.OK
    assert with_lock.healthy is True
    assert with_lock.reason == without.reason


def test_a_wedged_daemon_is_hung_not_died(tmp_path):
    """The case a lock check alone would miss, and why the heartbeat has to stay."""
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=400)

    report = health.check(path, max_age_seconds=180, lock=held(tmp_path / "daemon.lock", 1234))

    assert report.state is health.HealthState.HUNG
    assert report.healthy is False
    assert "wedged, not gone" in report.reason
    assert "past the 180s threshold" in report.reason


def test_a_stale_heartbeat_from_another_pid_is_a_restart_not_a_wedge(tmp_path):
    """``run_daemon`` takes the lock, then wires boundaries and runs ``startup`` — network
    work — before its first beat, and nothing unlinks the previous daemon's heartbeat. So
    this state is an ordinary restart, and sending somebody to take a stack of a healthy
    starting process is the wrong instruction."""
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=400, pid=111)

    report = health.check(path, max_age_seconds=180, lock=held(tmp_path / "daemon.lock", 222))

    assert report.state is health.HealthState.STARTING
    assert report.healthy is False, "a daemon that holds the lock and never beats is a fault"
    assert "not the holder" in report.reason
    assert "wedged" not in report.reason


def test_a_fresh_heartbeat_from_another_pid_stays_ok(tmp_path):
    """The same restart window, caught a moment earlier. Something holds the lock and
    something beat a second ago; alarming here would fire on every restart, which is a daily
    event, and an alarm that fires daily is one the reader learns to ignore."""
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=2, pid=111)

    report = health.check(path, max_age_seconds=180, lock=held(tmp_path / "daemon.lock", 222))

    assert report.state is health.HealthState.OK
    assert report.healthy is True


def test_a_holder_that_cannot_be_read_is_a_doubt_rather_than_a_match(tmp_path):
    """The comparison ``published_cap`` already makes, for the same window: a pid missing
    from either side is a doubt, so the verdict is the one that claims less."""
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=400)

    reading = health.LockReading(health.LockState.HELD, tmp_path / "daemon.lock", None)
    report = health.check(path, max_age_seconds=180, lock=reading)

    assert report.state is health.HealthState.STARTING
    # And it does not claim a mismatch it could not observe: the holder's pid is exactly
    # what could not be read here, so "is not the holder" would be an invention.
    assert "cannot be matched to the holder" in report.reason
    assert "is not the holder" not in report.reason


def test_a_daemon_holding_the_lock_with_no_heartbeat_at_all_is_starting(tmp_path):
    report = health.check(
        tmp_path / "nothing.json", max_age_seconds=180, lock=held(tmp_path / "daemon.lock")
    )
    assert report.state is health.HealthState.STARTING
    assert report.healthy is False
    assert "stopped before its first beat" in report.reason


def test_no_lock_and_no_heartbeat_is_still_never_started(tmp_path):
    report = health.check(
        tmp_path / "nothing.json", max_age_seconds=180, lock=unheld(tmp_path / "daemon.lock")
    )
    assert report.state is health.HealthState.NEVER_STARTED
    assert "never run" in report.reason


def test_an_unreadable_lock_falls_back_to_the_heartbeat_and_says_so(tmp_path):
    """A permission error must not manufacture a death notice. It must also not pass itself
    off as a verdict reached on both signals."""
    path = tmp_path / "heartbeat.json"
    lock = unreadable_lock(tmp_path / "daemon.lock")

    write_at(path, age_seconds=5)
    fresh = health.check(path, max_age_seconds=180, lock=lock)
    assert fresh.state is health.HealthState.OK
    assert "could not be read" in fresh.reason

    write_at(path, age_seconds=400)
    stale = health.check(path, max_age_seconds=180, lock=lock)
    assert stale.state is health.HealthState.STALE
    assert "could not be read" in stale.reason
    assert "the daemon is gone" not in stale.reason


def test_a_corrupt_heartbeat_is_neither_a_death_nor_a_hang(tmp_path):
    """FR-007. It keeps its own reason — and still tells the reader what the lock said,
    because that is the next thing they will want to know."""
    path = tmp_path / "heartbeat.json"
    path.write_text("{not json", encoding="utf-8")

    report = health.check(path, max_age_seconds=180, lock=unheld(tmp_path / "daemon.lock"))

    assert report.state is health.HealthState.UNREADABLE
    assert "not valid JSON" in report.reason
    assert "no process holds" in report.reason


def test_consulting_no_lock_at_all_behaves_exactly_as_before(tmp_path):
    """FR-016. A caller that has not looked gets today's verdicts and today's sentences —
    and, in the machine output, an honest ``None`` rather than a claim about the lock."""
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=400)

    report = health.check(path, max_age_seconds=180)

    assert report.state is health.HealthState.STALE
    assert report.lock is None
    assert report.to_dict()["lock"] is None
    assert "could not be read" not in report.reason


def test_the_machine_output_names_the_verdict_and_the_lock(tmp_path):
    """FR-009: a consumer names the state by reading a key, never by matching English."""
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=5)

    payload = health.check(
        path, max_age_seconds=180, lock=unheld(tmp_path / "daemon.lock")
    ).to_dict()

    assert payload["state"] == "died"
    assert payload["lock"] == "unheld"
    assert payload["healthy"] is False
    assert json.dumps(payload), "the payload must survive --json"


def test_every_state_has_a_label_and_only_ok_is_lowercase():
    """One definition, because three surfaces print it and the point of the feature is that
    they cannot differ."""
    assert health.HealthState.OK.label == "ok"
    assert health.HealthState.DIED.label == "DIED"
    assert health.HealthState.NEVER_STARTED.label == "NEVER STARTED"
    assert all(state.label for state in health.HealthState)


def test_healthy_is_true_for_ok_and_nothing_else(tmp_path):
    """The invariant runs one way: the flag is derived from the verdict."""
    path = tmp_path / "heartbeat.json"
    lock_path = tmp_path / "daemon.lock"
    seen = {}

    write_at(path, age_seconds=5)
    seen["ok"] = health.check(path, max_age_seconds=180, lock=held(lock_path))
    seen["died"] = health.check(path, max_age_seconds=180, lock=unheld(lock_path))
    write_at(path, age_seconds=400)
    seen["hung"] = health.check(path, max_age_seconds=180, lock=held(lock_path))
    seen["stale"] = health.check(path, max_age_seconds=180)
    seen["starting"] = health.check(
        tmp_path / "absent.json", max_age_seconds=180, lock=held(lock_path)
    )

    for name, report in seen.items():
        assert report.healthy is (report.state is health.HealthState.OK), name
        assert str(report.state) == name


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


def test_the_alert_carries_the_verdict_to_every_channel(tmp_path):
    """FR-010. The reader of a 2am notification is not at a terminal, and "restart it" and
    "look at it before you restart it" are different instructions."""
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=5)
    report = health.check(path, max_age_seconds=180, lock=unheld(tmp_path / "daemon.lock"))

    title, message, fields = health.alert_fields(report)

    assert title == "robot-army health check failed"
    assert message == report.reason
    assert fields["state"] == "died"
    assert fields["healthy"] is False


def test_notify_without_a_webhook_reports_that_rather_than_pretending(tmp_path):
    report = health.HealthReport(False, "stale", health.HealthState.STALE)
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
    report = health.HealthReport(False, "heartbeat is 400s old", health.HealthState.STALE, age_seconds=400)
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

    ``state`` joined that set in issue #52, deliberately and for a reason the gate is happy
    to record: the alert is what a person reads when they are not at a terminal, and it now
    has to say whether the daemon died or hung. The gate's actual subject — that adding a
    *channel* changes nothing about this body — is untouched.
    """
    import httpx

    captured: dict = {}

    def fake_post(url, json=None, timeout=None):
        captured.update(json)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    send_alert(
        "https://x.invalid",
        health.HealthReport(False, "stale", health.HealthState.STALE, age_seconds=400),
    )
    assert set(captured) == {
        "title",
        "message",
        "healthy",
        "state",
        "age_seconds",
        "host",
        "ts",
    }
    assert captured["title"] == "robot-army health check failed"


def test_a_failing_webhook_is_reported_not_swallowed(monkeypatch):
    import httpx

    def fake_post(url, json=None, timeout=None):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(httpx, "post", fake_post)
    sent, message = send_alert("https://x.invalid", health.HealthReport(False, "stale", health.HealthState.STALE))
    assert sent is False
    assert "webhook POST failed" in message


def test_a_webhook_error_status_is_reported(monkeypatch):
    import httpx

    def fake_post(url, json=None, timeout=None):
        return httpx.Response(500, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    sent, message = send_alert("https://x.invalid", health.HealthReport(False, "stale", health.HealthState.STALE))
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


def test_health_and_status_describe_one_machine_the_same_way(config, conn, layout, tmp_path):
    """SC-007, and the incident in issue #52 read in the other direction.

    ``status`` prints exactly one line about the daemon and it is the health line — there is
    no separate "running" line to contradict it — so a verdict from the heartbeat alone let
    it say ``ok`` beside a dead daemon for as long as the threshold ran. The two commands
    are asserted together because "they agree" is the property, not "each is right".
    """
    from robot_army import operations

    write_at(layout.heartbeat_path, age_seconds=1)
    ctx = alert_context(config, conn, tmp_path)
    try:
        switch = operations.health_check(ctx)
        overview = operations.status(ctx)
    finally:
        ctx.close()

    assert switch.code == 4
    assert switch.lines[0].startswith("DIED: ")
    assert switch.data["state"] == "died"

    health_line = next(line for line in overview.lines if line.startswith("health "))
    assert "DIED — " in health_line
    assert overview.data["health"]["state"] == "died"
    assert health_line.endswith(switch.data["reason"])


def test_a_live_daemon_still_reads_ok_on_both(config, conn, layout, tmp_path):
    """The other half of the same property: neither command may cry wolf."""
    from robot_army import operations
    from robot_army.daemon import SingleInstanceLock

    write_at(layout.heartbeat_path, age_seconds=1, pid=os.getpid())
    ctx = alert_context(config, conn, tmp_path)
    try:
        with SingleInstanceLock(layout.lock_path):
            switch = operations.health_check(ctx)
            overview = operations.status(ctx)
    finally:
        ctx.close()

    assert switch.code == 0
    assert switch.lines[0].startswith("ok: ")
    assert any(line.startswith("health       : ok — ") for line in overview.lines)


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


# -- the cap the daemon publishes (issue #30) -------------------------------


def test_the_heartbeat_carries_the_cap_the_daemon_is_enforcing(tmp_path):
    """The whole point: the file alone answers "what cap is in force?"."""
    path = tmp_path / "heartbeat.json"
    health.write_heartbeat(
        path, effect_level="live", activity="idle", cycles=1, max_concurrent_sessions=7
    )
    assert json.loads(path.read_text(encoding="utf-8"))["max_concurrent_sessions"] == 7


def test_a_heartbeat_written_without_a_cap_still_parses(tmp_path):
    """An older build's file, or one written before this field existed."""
    path = tmp_path / "heartbeat.json"
    health.write_heartbeat(path, effect_level="live", activity="idle", cycles=1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["max_concurrent_sessions"] is None
    assert health.check(path, max_age_seconds=180).healthy is True


#: The pid every helper below agrees on: the heartbeat's writer *is* the lock holder.
HOLDER = 4242


def report_with(**beat):
    """A health report carrying whatever heartbeat the case needs."""
    if beat:
        beat.setdefault("pid", HOLDER)
    return health.HealthReport(
        True, "fresh", health.HealthState.OK, age_seconds=1.0, heartbeat=beat or None
    )


def cap_of(report, *, running=True, lock_holder=str(HOLDER)):
    """``published_cap`` with the ordinary case's arguments, so a case states its own."""
    return health.published_cap(report, running=running, lock_holder=lock_holder)


def test_no_daemon_running_publishes_no_cap(tmp_path):
    """Nothing is enforcing anything, so a heartbeat left behind is not an authority."""
    assert cap_of(report_with(max_concurrent_sessions=7), running=False) is None


def test_a_daemon_with_an_unreadable_heartbeat_publishes_no_cap(tmp_path):
    path = tmp_path / "heartbeat.json"
    report = health.check(path, max_age_seconds=180)
    assert report.heartbeat is None
    assert cap_of(report) is None


def test_a_running_daemons_cap_is_taken_from_its_heartbeat(tmp_path):
    assert cap_of(report_with(max_concurrent_sessions=7)) == 7


def test_a_stale_heartbeat_still_names_the_cap_in_force(tmp_path):
    """R5. A daemon's cap cannot change while it runs, so staleness is not ignorance.

    Staleness means a tick is running long — which is when the machine is busy, which is
    exactly when the fraction is being read.
    """
    path = tmp_path / "heartbeat.json"
    write_at(path, age_seconds=3600, max_concurrent_sessions=7, pid=HOLDER)
    report = health.check(path, max_age_seconds=180)
    assert report.healthy is False, "the report really is stale"
    assert cap_of(report) == 7


def test_a_heartbeat_with_no_cap_field_publishes_nothing(tmp_path):
    assert cap_of(report_with(effect_level="live")) is None


@pytest.mark.parametrize("value", [0, -1, "7", True, False, None, 7.5, [7], {"n": 7}])
def test_an_unusable_cap_is_not_published_rather_than_believed(value):
    """Replacing a stale number with a nonsensical one is not an improvement.

    ``True`` is in the list because ``bool`` is an ``int`` in Python, and a heartbeat
    carrying ``true`` would otherwise be read as a cap of one session.
    """
    assert cap_of(report_with(max_concurrent_sessions=value)) is None


def test_a_heartbeat_from_a_process_that_is_not_the_lock_holder_publishes_nothing():
    """The restart window, which is a real window and not a hypothetical.

    ``run_daemon`` takes the lock and then wires boundaries, checks preconditions and runs
    ``startup`` — network work, seconds of it — before its first beat, and nothing unlinks
    the previous daemon's heartbeat. So mid-restart the lock is held by the new process
    while the newest heartbeat on disk is the dead one's.

    Lower the cap from 7 to 2 and restart: believing the file here would report 7 on every
    surface and, worse, admit sessions up to 7 through the launch gate against a daemon
    about to enforce 2 — an over-dispatch, the one direction that does harm.
    """
    dead = health.HealthReport(
        True,
        "fresh",
        health.HealthState.OK,
        age_seconds=1.0,
        heartbeat={"pid": 111, "max_concurrent_sessions": 7},
    )
    assert health.published_cap(dead, running=True, lock_holder="222") is None


def test_an_unreadable_lock_holder_is_a_doubt_rather_than_a_match():
    """Fails to "use your own configuration", which is what every other doubt here does."""
    report = report_with(max_concurrent_sessions=7)
    assert health.published_cap(report, running=True, lock_holder=None) is None
    assert health.published_cap(report, running=True, lock_holder="   ") is None


def test_a_heartbeat_with_no_pid_publishes_nothing():
    report = health.HealthReport(
        True,
        "fresh",
        health.HealthState.OK,
        age_seconds=1.0,
        heartbeat={"max_concurrent_sessions": 7},
    )
    assert health.published_cap(report, running=True, lock_holder="222") is None


def test_the_lock_holders_trailing_newline_is_not_a_mismatch():
    """``read_lock_holder`` returns the file's line; the lock is written with a newline."""
    assert cap_of(report_with(max_concurrent_sessions=7), lock_holder=f"{HOLDER}\n") == 7
