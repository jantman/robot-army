"""Notifications: off by default, bounded per cycle, and never carrying a secret
(T071, T072).

Three properties, in descending order of how much they matter.

**Nothing is sent that was not asked for.** An unconfigured installation makes no outbound
request at all — not "constructs one and skips it at the last moment", which is a
distinction that only shows up the day someone moves the check.

**A channel failure is the channel's problem.** The state change already happened and is
already in the log; a webhook that never answers must not fail, delay, or retry the pass
that triggered it.

**No credential can reach a message.** Checked across a run that includes an authentication
failure, because that is the case where a token would otherwise ride along inside an error
string rather than in a field anyone chose to add.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from tests.conftest import RecordingNotifier, config_dict, make_boundaries, monkey_token

from robot_army import notifications
from robot_army.boundaries import NotificationEvent
from robot_army.boundaries.notifier import SimulatedNotifier, WebhookNotifier, compose
from robot_army.config import parse

ALL_KINDS = ("dispatch", "completion", "failure", "needs_info")


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def wired(audit: Any, notifier: RecordingNotifier) -> Any:
    return make_boundaries(audit, notifier=notifier)


def configured(config, *kinds: str, max_per_cycle: int = 5, webhook: str = "https://hook"):
    return replace(
        config,
        health=replace(config.health, webhook_url=webhook),
        notifications=replace(
            config.notifications, events=tuple(kinds), max_per_cycle=max_per_cycle
        ),
    )


def say(wired, audit, config, kind: str = "failure", **overrides) -> bool:
    payload: dict[str, Any] = {
        "kind": kind,
        "item_id": 1,
        "repo_key": "demo",
        "title": f"robot-army: {kind}",
        "detail": "something happened",
        "url": "https://github.com/x/demo/issues/1",
    }
    payload.update(overrides)
    return notifications.emit(boundaries=wired, audit=audit, config=config, **payload)


def records(layout, audit, action: str) -> list[dict]:
    audit.close()
    out = []
    for path in sorted(layout.log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["action"] == action:
                out.append(record)
    return out


# -- off by default ---------------------------------------------------------


def test_an_unconfigured_install_makes_no_outbound_request(wired, audit, config, notifier):
    """``events`` is empty by default, which is the Operating Constraints' rule for
    outward-facing actions and the same one that keeps cleanup off."""
    assert config.notifications.events == ()
    for kind in ALL_KINDS:
        assert say(wired, audit, config, kind) is False
    assert notifier.events == []


def test_only_the_configured_kinds_are_sent(wired, audit, config, notifier):
    config = configured(config, "failure")
    for kind in ALL_KINDS:
        say(wired, audit, config, kind)
    assert notifier.kinds == ["failure"]


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_kind_can_be_asked_for(wired, audit, config, notifier, kind):
    assert say(wired, audit, configured(config, kind), kind) is True
    assert notifier.kinds == [kind]


# -- a channel failure is the channel's problem (FR-035) -------------------


def test_a_refusing_channel_is_recorded_and_does_not_raise(audit, config, layout):
    failing = RecordingNotifier(ok=False)
    wired = make_boundaries(audit, notifier=failing)
    assert say(wired, audit, configured(config, "failure")) is True
    assert failing.events, "the attempt is still made"
    sent = records(layout, audit, "notify.send")
    assert sent and sent[-1]["outcome"] == "error"


def test_a_raising_channel_does_not_escape(audit, config, layout):
    class Exploding:
        def send(self, event: Any) -> bool:
            raise RuntimeError("connection reset by peer")

    wired = make_boundaries(audit, notifier=Exploding())
    say(wired, audit, configured(config, "failure"))
    sent = records(layout, audit, "notify.send")
    assert sent[-1]["outcome"] == "error"
    assert "connection reset" in sent[-1]["detail"]["error"]


def test_nothing_is_retried(audit, config, notifier, wired):
    """"A duplicate notification is noise; a retry loop is a Principle IV violation." One
    attempt per event, and the transport's own bounded policy is the only retry there is."""
    failing = RecordingNotifier(ok=False)
    wired = make_boundaries(audit, notifier=failing)
    say(wired, audit, configured(config, "failure"))
    assert len(failing.events) == 1


def test_a_send_never_happens_inside_an_open_transaction(conn, audit, config):
    """R14, asserted rather than reviewed. An HTTP POST inside ``BEGIN IMMEDIATE`` holds a
    write transaction open for as long as a slow webhook takes to answer — which is why
    hooking ``states.transition()`` was rejected despite being the more elegant design."""
    seen: list[bool] = []

    class Watching:
        def send(self, event: Any) -> bool:
            seen.append(conn.in_transaction)
            return True

    wired = make_boundaries(audit, notifier=Watching())
    say(wired, audit, configured(config, "failure"))
    assert seen == [False]


# -- the per-cycle bound (R15, FR-036, T072) -------------------------------


def test_a_backlog_produces_exactly_the_limit_plus_one_summary(
    audit, config, layout, notifier, wired
):
    """Per-``(kind, item)`` de-duplication would not bound this: a backlog produces
    *different* items, which is the very case that would flood."""
    config = configured(config, "completion", max_per_cycle=3)
    notifications.begin_cycle()
    for n in range(10):
        say(wired, audit, config, "completion", item_id=n)
    notifications.end_cycle(boundaries=wired, audit=audit, config=config)

    assert notifier.kinds == ["completion", "completion", "completion", "summary"]
    summary = notifier.events[-1]
    assert "7" in summary.title
    assert "7 completion" in summary.detail


def test_nothing_is_dropped_silently(audit, config, layout, notifier, wired):
    """Principle III forbids discarding records silently, so every suppressed event is in
    the log even though it never reached the channel."""
    config = configured(config, "failure", max_per_cycle=2)
    notifications.begin_cycle()
    for n in range(5):
        say(wired, audit, config, "failure", item_id=n)
    notifications.end_cycle(boundaries=wired, audit=audit, config=config)

    sent = records(layout, audit, "notify.send")
    suppressed = [r for r in sent if r["detail"].get("suppressed")]
    assert len(suppressed) == 3
    summary = records(layout, audit, "notify.suppressed")
    assert summary and summary[0]["detail"]["count"] == 3
    assert summary[0]["detail"]["kinds"] == {"failure": 3}


def test_a_new_cycle_permits_sending_again(audit, config, notifier, wired):
    config = configured(config, "failure", max_per_cycle=1)
    notifications.begin_cycle()
    say(wired, audit, config, "failure", item_id=1)
    say(wired, audit, config, "failure", item_id=2)
    notifications.end_cycle(boundaries=wired, audit=audit, config=config)
    notifier.events.clear()

    notifications.begin_cycle()
    assert say(wired, audit, config, "failure", item_id=3) is True
    assert notifier.kinds == ["failure"]


def test_a_cycle_within_the_limit_produces_no_summary(audit, config, notifier, wired):
    config = configured(config, "failure", max_per_cycle=5)
    notifications.begin_cycle()
    say(wired, audit, config, "failure")
    notifications.end_cycle(boundaries=wired, audit=audit, config=config)
    assert notifier.kinds == ["failure"]


def test_the_counter_is_deliberately_not_persisted():
    """It exists to bound one burst. A restart mid-burst re-permitting a handful of
    messages is not a failure worth a table to prevent."""
    assert isinstance(notifications._CYCLE, dict)
    notifications.begin_cycle()
    assert notifications._CYCLE["sent"] == 0


# -- no credential can reach a message (FR-037, SC-010, T072) --------------

SECRETS = ("ghp_" + "a" * 36, "trellokey-abcdef0123456789", "s3cret-token-value")


def test_the_event_shape_has_no_field_a_secret_could_reach():
    """Structural rather than behavioural: FR-037 is kept by the shape of the dataclass, so
    adding a field carelessly is the only way to break it — and that is reviewable."""
    import dataclasses

    fields = {f.name for f in dataclasses.fields(NotificationEvent)}
    assert fields == {"kind", "item_id", "repo_key", "title", "detail", "url"}
    # ``repo_key`` is a repository *name*, so "key" is not on this list — a substring
    # check that flags it would be a check nobody keeps.
    for suspicious in ("token", "secret", "headers", "auth", "response", "body", "request"):
        assert not any(suspicious in name for name in fields), suspicious


def test_no_credential_appears_in_a_composed_message_or_its_log(
    audit, config, layout, notifier, wired
):
    """Across a run that includes an authentication failure — the case where a token would
    otherwise ride along inside an error string."""
    import os

    for secret in SECRETS:
        os.environ.setdefault("ROBOT_ARMY_TEST_SECRET", secret)

    config = configured(config, "failure")
    say(
        wired,
        audit,
        config,
        "failure",
        detail="GitHub returned HTTP 401: Bad credentials while polling demo",
    )
    notifications.end_cycle(boundaries=wired, audit=audit, config=config)

    rendered = json.dumps([compose(e) for e in notifier.events])
    log_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(layout.log_dir.glob("audit-*.jsonl"))
    )
    for secret in SECRETS:
        assert secret not in rendered
        assert secret not in log_text


def test_the_composed_body_carries_only_identifiers_and_state_names(notifier):
    body = compose(
        NotificationEvent(
            kind="failure",
            item_id=7,
            repo_key="demo",
            title="t",
            detail="d",
            url="https://example.invalid/1",
        )
    )
    assert set(body) == {"title", "message", "kind", "item_id", "repo_key", "url", "host", "ts"}


# -- the boundary itself ----------------------------------------------------


def test_the_simulated_notifier_logs_the_call_with_its_full_arguments(audit, layout):
    """Exactly as the other simulated writers do, so the simulated path cannot quietly
    diverge from the real one."""
    event = NotificationEvent(
        kind="dispatch", item_id=3, repo_key="demo", title="t", detail="d", url=None
    )
    assert SimulatedNotifier(audit).send(event) is True
    logged = records(layout, audit, "notify.send")
    assert logged[0]["simulated"] is True
    assert logged[0]["detail"]["body"]["item_id"] == 3


def test_the_real_notifier_makes_no_request_without_a_webhook(audit, config):
    """Configured to notify with nowhere to notify. The config loader already warned at
    startup; repeating it as an error per event would be noise."""
    assert config.health.webhook_url == ""
    event = NotificationEvent(
        kind="failure", item_id=1, repo_key="demo", title="t", detail="d", url=None
    )
    assert WebhookNotifier(config, audit).send(event) is False


def test_the_real_notifier_reuses_the_health_transport(monkeypatch, audit, config, layout):
    """One bounded-timeout POST for the whole project — one timeout to keep correct, and
    one channel rather than a second URL knob to configure and then forget (R14)."""
    from robot_army import health

    calls: list[tuple[str, dict]] = []

    def fake_post(url: str, body: dict, *, timeout: float = 10.0) -> tuple[bool, str]:
        calls.append((url, body))
        return True, "ok"

    monkeypatch.setattr(health, "post_json", fake_post)
    config = replace(config, health=replace(config.health, webhook_url="https://hook"))
    event = NotificationEvent(
        kind="failure", item_id=1, repo_key="demo", title="t", detail="d", url=None
    )
    assert WebhookNotifier(config, audit).send(event) is True
    assert calls[0][0] == "https://hook"
    assert calls[0][1]["kind"] == "failure"


# -- configuration (T073) ---------------------------------------------------


def build_config(repo_clone, layout, tmp_path, **overrides):
    monkey_token()
    return parse(
        config_dict(repo_clone, layout, tmp_path / "worktrees", **overrides),
        tmp_path / "config.toml",
    )


def test_notifications_default_to_sending_nothing(repo_clone, layout, tmp_path):
    config = build_config(repo_clone, layout, tmp_path)
    assert config.notifications.events == ()
    assert config.notifications.max_per_cycle == 5


def test_an_unknown_event_kind_refuses_to_load(repo_clone, layout, tmp_path):
    """Silently ignoring it means an event the author asked for never arrives, and a
    channel that is quiet for the wrong reason is worse than no channel."""
    from robot_army.config import ConfigError

    with pytest.raises(ConfigError) as caught:
        build_config(repo_clone, layout, tmp_path, notifications={"events": ["explosion"]})
    joined = "\n".join(caught.value.problems)
    assert "unknown event kind" in joined
    assert "needs_info" in joined


def test_events_without_a_webhook_warn_rather_than_refuse(repo_clone, layout, tmp_path):
    """The intent is legible and the fix is obvious. Refusing to start over a stretch
    feature would be disproportionate (R17)."""
    config = build_config(repo_clone, layout, tmp_path, notifications={"events": ["failure"]})
    assert config.notifications.events == ("failure",)
    assert any("webhook_url is empty" in w for w in config.warnings)


def test_a_typo_in_the_notifications_section_refuses_to_load(repo_clone, layout, tmp_path):
    from robot_army.config import ConfigError

    with pytest.raises(ConfigError) as caught:
        build_config(repo_clone, layout, tmp_path, notifications={"event": ["failure"]})
    assert any("[notifications] unknown key 'event'" in p for p in caught.value.problems)


def test_a_non_positive_max_per_cycle_refuses_to_load(repo_clone, layout, tmp_path):
    from robot_army.config import ConfigError

    with pytest.raises(ConfigError) as caught:
        build_config(repo_clone, layout, tmp_path, notifications={"max_per_cycle": 0})
    assert any("max_per_cycle" in p for p in caught.value.problems)
