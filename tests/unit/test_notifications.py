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
from tests.conftest import (
    RecordingChannel,
    RecordingNotifier,
    config_dict,
    make_boundaries,
    monkey_token,
)

from robot_army import channels, notifications
from robot_army.boundaries import NotificationEvent
from robot_army.boundaries.notifier import MultiNotifier, SimulatedNotifier, compose
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


def test_the_real_notifier_makes_no_request_without_a_channel(audit, config):
    """Configured to notify with nowhere to notify. The config loader already warned at
    startup; repeating it as an error per event would be noise."""
    assert config.health.webhook_url == ""
    assert config.pushover is None
    assert channels.build(config) == ()
    event = NotificationEvent(
        kind="failure", item_id=1, repo_key="demo", title="t", detail="d", url=None
    )
    assert MultiNotifier(channels.build(config), audit).send(event) is False


def test_the_real_notifier_reuses_the_health_transport(monkeypatch, audit, config, layout):
    """One bounded-timeout POST for the whole project — one timeout to keep correct, and
    one HTTP client rather than a second to configure and then forget (R14).

    Milestone 106 added a second *channel*, not a second client: ``post_json`` is still the
    webhook's only transport and its body is unchanged."""
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
    assert MultiNotifier(channels.build(config), audit).send(event) is True
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
    assert any("no notification channel is set" in w for w in config.warnings)


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


# -- the fan-out (issue #106) -----------------------------------------------
#
# One message, zero to two deliveries. Everything below is about the seam between those
# two numbers, which did not exist while there was only ever one channel.


def pushover_config(tmp_path):
    """Two credential files and the config that points at them."""
    from robot_army.config import PushoverConfig

    token, user = tmp_path / "po-token", tmp_path / "po-user"
    token.write_text("aTokenThatIs30CharactersLong00", encoding="utf-8")
    user.write_text("uUserKeyThatIs30CharsLong00000", encoding="utf-8")
    token.chmod(0o600)
    user.chmod(0o600)
    return PushoverConfig(token_file=token, user_key_file=user)


def test_build_returns_nothing_when_no_channel_is_configured(config):
    """Not "a request is built and skipped at the last moment" — there is nothing to build,
    which is the distinction milestone 004 drew for ``events`` and the one that makes an
    unconfigured installation provably silent."""
    assert channels.build(config) == ()


def test_build_returns_the_webhook_alone_when_that_is_all_there_is(config):
    built = channels.build(configured(config))
    assert channels.names(built) == ("webhook",)


def test_build_returns_pushover_alone_when_there_is_no_webhook(config, tmp_path):
    """The Pushover-only installation — US1's independent test, and the configuration this
    feature exists to make possible."""
    only = replace(configured(config, webhook=""), pushover=pushover_config(tmp_path))
    assert channels.names(channels.build(only)) == ("pushover",)


def test_build_returns_both_in_a_stable_order(config, tmp_path):
    both = replace(configured(config), pushover=pushover_config(tmp_path))
    assert channels.names(channels.build(both)) == ("webhook", "pushover")


def test_the_webhook_body_is_byte_for_byte_what_it_always_was(config):
    """The FR-016 regression gate for the notification path. The exact key set, not a
    sample of it: an installation with only a webhook must see no change at all."""
    event = NotificationEvent(
        kind="failure", item_id=1, repo_key="demo", title="t", detail="d", url="u"
    )
    body = compose(event)
    assert set(body) == {"title", "message", "kind", "item_id", "repo_key", "url", "host", "ts"}
    assert body["title"] == "t"
    assert body["message"] == "d"


def test_one_message_to_two_channels_is_one_send_record_and_two_channel_records(
    audit, config, layout
):
    """US2 AS1. A message and a delivery are different things, and the log has to be able
    to say "the webhook took it and Pushover did not"."""
    webhook, pushover = RecordingChannel("webhook"), RecordingChannel("pushover")
    wired = make_boundaries(audit, notifier=MultiNotifier((webhook, pushover), audit))
    assert say(wired, audit, configured(config, "failure")) is True

    assert len(webhook.sends) == 1
    assert len(pushover.sends) == 1
    assert len(records(layout, audit, "notify.send")) == 1
    channel_records = records(layout, audit, "notify.channel")
    assert [r["detail"]["channel"] for r in channel_records] == ["webhook", "pushover"]
    assert [r["outcome"] for r in channel_records] == ["ok", "ok"]


def test_one_channel_failing_does_not_stop_the_other(audit, config, layout):
    """US2 AS2. The loop does not short-circuit, and the caller is neither failed, delayed,
    nor made to retry."""
    webhook = RecordingChannel("webhook", ok=True)
    pushover = RecordingChannel("pushover", ok=False)
    wired = make_boundaries(audit, notifier=MultiNotifier((webhook, pushover), audit))

    assert say(wired, audit, configured(config, "failure")) is True
    assert len(webhook.sends) == 1, "the healthy channel still delivered"
    assert len(pushover.sends) == 1, "the broken channel was still attempted"
    outcomes = {
        r["detail"]["channel"]: r["outcome"] for r in records(layout, audit, "notify.channel")
    }
    assert outcomes == {"webhook": "ok", "pushover": "error"}


def test_the_broken_channel_being_first_does_not_stop_the_second(audit, config, layout):
    """Order must not matter. A ``break`` or an ``and`` would pass the previous test and
    fail this one."""
    first = RecordingChannel("pushover", ok=False)
    second = RecordingChannel("webhook", ok=True)
    wired = make_boundaries(audit, notifier=MultiNotifier((first, second), audit))
    assert say(wired, audit, configured(config, "failure")) is True
    assert len(second.sends) == 1


def test_both_channels_failing_still_does_not_fail_the_caller(audit, config, layout):
    """Two independent failure records, and an operation that still succeeds. The state
    change already happened and is already in the log."""
    a, b = RecordingChannel("webhook", ok=False), RecordingChannel("pushover", ok=False)
    wired = make_boundaries(audit, notifier=MultiNotifier((a, b), audit))

    say(wired, audit, configured(config, "failure"))
    channel_records = records(layout, audit, "notify.channel")
    assert len(channel_records) == 2
    assert all(r["outcome"] == "error" for r in channel_records)


def test_a_channel_that_raises_does_not_escape_the_notifier(audit, config, layout):
    """The channels promise not to raise; this is the second belt to those braces, tested
    against a channel that breaks its own contract."""
    exploding = RecordingChannel("pushover", raises=True)
    wired = make_boundaries(audit, notifier=MultiNotifier((exploding,), audit))
    say(wired, audit, configured(config, "failure"))
    assert records(layout, audit, "notify.send")[0]["outcome"] == "error"


def test_the_cap_counts_messages_not_deliveries(audit, config, layout):
    """FR-013. Adding a second channel must not halve how many things the author is told
    about: the bound is on a burst of news, not a burst of packets (R15, R7)."""
    webhook, pushover = RecordingChannel("webhook"), RecordingChannel("pushover")
    wired = make_boundaries(audit, notifier=MultiNotifier((webhook, pushover), audit))
    capped = configured(config, "failure", max_per_cycle=3)

    notifications.begin_cycle()
    for _ in range(5):
        say(wired, audit, capped)

    assert len(webhook.sends) == 3, "three messages, not one and a half"
    assert len(pushover.sends) == 3


def test_the_suppression_summary_reaches_every_channel(audit, config, layout):
    """The bound is visible rather than silent, on each channel the author configured."""
    webhook, pushover = RecordingChannel("webhook"), RecordingChannel("pushover")
    wired = make_boundaries(audit, notifier=MultiNotifier((webhook, pushover), audit))
    capped = configured(config, "failure", max_per_cycle=1)

    notifications.begin_cycle()
    for _ in range(3):
        say(wired, audit, capped)
    notifications.end_cycle(boundaries=wired, audit=audit, config=capped)

    assert "suppressed" in webhook.sends[-1][0]
    assert "suppressed" in pushover.sends[-1][0]


def test_a_notifier_with_no_channels_reports_that_it_delivered_nothing(audit, config, layout):
    """Configured to notify with nowhere to notify. The loader already warned; an error per
    event would be noise.

    Note the two different questions being asked. ``emit`` returns whether a send was
    *attempted* — the caller must not care whether it landed — while the notifier returns
    whether anything was delivered. With no channel the first is still True and the second
    is False, and it is the record that has to say so.
    """
    wired = make_boundaries(audit, notifier=MultiNotifier((), audit))
    assert say(wired, audit, configured(config, "failure", webhook="")) is True

    assert records(layout, audit, "notify.channel") == []
    assert records(layout, audit, "notify.send")[0]["detail"]["delivered"] is False


def test_the_simulated_notifier_names_the_channels_it_would_have_used(audit, layout):
    """Below ``live`` there are no deliveries to record — only an intent, and the intent is
    one message aimed at a known list. One record, not one per channel (R9)."""
    simulated = SimulatedNotifier(
        audit, (RecordingChannel("webhook"), RecordingChannel("pushover"))
    )
    event = NotificationEvent(
        kind="dispatch", item_id=3, repo_key="demo", title="t", detail="d", url=None
    )
    assert simulated.send(event) is True

    logged = records(layout, audit, "notify.send")[0]
    assert logged["simulated"] is True
    assert logged["detail"]["channels"] == ["webhook", "pushover"]
    assert logged["detail"]["body"]["item_id"] == 3


def test_nothing_is_delivered_below_live_even_with_pushover_configured(
    audit, config, layout, tmp_path
, conn):
    """US1 AS3. ``REAL_AT["notifier"]`` is unchanged: a notification is an outward-facing
    write and is simulated at every level below ``live``."""
    from robot_army.effects import EffectLevel, wire

    both = replace(configured(config, "failure"), pushover=pushover_config(tmp_path))
    for level in (EffectLevel.PLAN, EffectLevel.LOCAL, EffectLevel.NO_REMOTE):
        wired = wire(level, both, audit, conn)
        assert type(wired.notifier).__name__ == "SimulatedNotifier"
    assert type(wire(EffectLevel.LIVE, both, audit, conn).notifier).__name__ == "MultiNotifier"


def test_an_empty_events_list_sends_nothing_even_with_pushover_configured(
    audit, config, layout, tmp_path
):
    """US1 AS2. Adding a channel is not asking to be notified; ``events`` is."""
    channel = RecordingChannel("pushover")
    wired = make_boundaries(audit, notifier=MultiNotifier((channel,), audit))
    only_pushover = replace(config, pushover=pushover_config(tmp_path))
    assert only_pushover.notifications.events == ()

    for kind in ALL_KINDS:
        assert say(wired, audit, only_pushover, kind) is False
    assert channel.sends == []


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_kind_reaches_pushover_when_asked_for(audit, config, layout, kind):
    """US1 AS1, across the closed set of kinds."""
    channel = RecordingChannel("pushover")
    wired = make_boundaries(audit, notifier=MultiNotifier((channel,), audit))
    assert say(wired, audit, configured(config, kind, webhook=""), kind) is True
    assert channel.sends[0][2]["kind"] == kind


def test_the_startup_record_names_the_live_channels(audit):
    """``MultiNotifier`` alone would hide the one fact a reader of that record wants."""
    notifier = MultiNotifier(
        (RecordingChannel("webhook"), RecordingChannel("pushover")), audit
    )
    assert notifier.describe_name() == "MultiNotifier(webhook, pushover)"
