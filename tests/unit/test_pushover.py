"""The Pushover channel: what goes on the wire, and what must never (issue #106).

Four properties, in descending order of how much they matter.

**No credential can reach a record.** The whole point of the file-based credential
convention is defeated if the token rides along inside an error string. Asserted across a
run that includes an authentication failure, because that is the case where it would.

**The request is form-encoded, not JSON.** This is the defect the feature exists to fix.
``health.post_json``'s docstring claimed a generic JSON webhook covered Pushover; Pushover
takes ``application/x-www-form-urlencoded`` parameters and rejects a JSON body, so pointing
``[health] webhook_url`` at it produced a rejected request rather than a notification.

**A message too long is truncated, not rejected.** Pushover answers 4xx rather than
truncating, and a rejected message tells the author nothing.

**A channel failure is the channel's problem.** Never raises, whatever happens — the state
change it describes already happened and is already in the log.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from robot_army import channels
from robot_army.config import PushoverConfig

TOKEN = "aTokenThatIs30CharactersLong00"  # noqa: S105 - a fake credential is the fixture
USER_KEY = "uUserKeyThatIs30CharsLong00000"


@pytest.fixture
def creds(tmp_path: Path) -> PushoverConfig:
    token = tmp_path / "token"
    user = tmp_path / "user"
    token.write_text(TOKEN, encoding="utf-8")
    user.write_text(USER_KEY, encoding="utf-8")
    token.chmod(0o600)
    user.chmod(0o600)
    return PushoverConfig(token_file=token, user_key_file=user)


@pytest.fixture
def posted(monkeypatch) -> list[dict[str, Any]]:
    """Capture every form POST instead of making one."""
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, data: dict, *, timeout: float = 10.0) -> tuple[bool, str]:
        calls.append({"url": url, "data": data, "timeout": timeout})
        return True, "notified (HTTP 200)"

    from robot_army import health

    monkeypatch.setattr(health, "post_form", fake_post)
    return calls


# -- what goes on the wire --------------------------------------------------


def test_the_request_is_form_encoded_to_pushovers_message_endpoint(creds, posted):
    """The defect this feature fixes. A JSON body is rejected by Pushover; these are the
    parameters its message API actually takes."""
    ok, _ = channels.PushoverChannel(creds).send("a title", "a message", {})
    assert ok is True
    assert posted[0]["url"] == "https://api.pushover.net/1/messages.json"
    assert posted[0]["data"] == {
        "token": TOKEN,
        "user": USER_KEY,
        "title": "a title",
        "message": "a message",
    }


def test_the_url_field_is_passed_through_when_there_is_one(creds, posted):
    channels.PushoverChannel(creds).send("t", "d", {"url": "https://example.invalid/1"})
    assert posted[0]["data"]["url"] == "https://example.invalid/1"


@pytest.mark.parametrize("fields", [{}, {"url": None}, {"url": ""}])
def test_no_url_field_is_sent_when_there_is_no_url(creds, posted, fields):
    """An empty ``url`` is a 4xx from Pushover, so absence must mean absence rather than
    an empty string."""
    channels.PushoverChannel(creds).send("t", "d", fields)
    assert "url" not in posted[0]["data"]


def test_fields_pushover_does_not_understand_are_ignored(creds, posted):
    """One ``send`` signature serves both senders; each channel takes what it understands.
    The webhook splices these into its body, and a push notification has nowhere to put
    them."""
    channels.PushoverChannel(creds).send(
        "t", "d", {"kind": "failure", "item_id": 7, "repo_key": "demo", "healthy": False}
    )
    assert set(posted[0]["data"]) == {"token", "user", "title", "message"}


def test_every_call_sets_an_explicit_timeout(creds, posted):
    """Principle IV requires it of every network call."""
    channels.PushoverChannel(creds).send("t", "d", {})
    assert posted[0]["timeout"] is not None


def test_a_trailing_newline_in_a_credential_file_is_stripped(tmp_path, posted):
    """A file written with ``echo`` ends in a newline, and a newline in a form parameter is
    a 4xx nobody enjoys diagnosing."""
    token, user = tmp_path / "t", tmp_path / "u"
    token.write_text(f"{TOKEN}\n", encoding="utf-8")
    user.write_text(f"{USER_KEY}\n", encoding="utf-8")
    token.chmod(0o600)
    user.chmod(0o600)
    config = PushoverConfig(token_file=token, user_key_file=user)

    channels.PushoverChannel(config).send("t", "d", {})
    assert posted[0]["data"]["token"] == TOKEN
    assert posted[0]["data"]["user"] == USER_KEY


def test_the_credentials_are_read_at_send_time_not_at_construction(creds, posted):
    """FR-003. A channel that read them once would hold a secret in memory for the life of
    the process, and would not notice the author rotating one."""
    channel = channels.PushoverChannel(creds)
    creds.token_file.write_text("aRotatedTokenThatIs30CharsLon0", encoding="utf-8")
    channel.send("t", "d", {})
    assert posted[0]["data"]["token"] == "aRotatedTokenThatIs30CharsLon0"  # noqa: S105


# -- truncation (research.md R4) --------------------------------------------


def test_a_long_message_is_truncated_rather_than_rejected(creds, posted):
    channels.PushoverChannel(creds).send("t", "x" * 2000, {})
    assert len(posted[0]["data"]["message"]) == channels.PUSHOVER_MESSAGE_LIMIT


def test_a_long_title_is_truncated_rather_than_rejected(creds, posted):
    channels.PushoverChannel(creds).send("y" * 400, "d", {})
    assert len(posted[0]["data"]["title"]) == channels.PUSHOVER_TITLE_LIMIT


def test_a_message_inside_the_limit_is_untouched(creds, posted):
    channels.PushoverChannel(creds).send("t", "x" * 1024, {})
    assert len(posted[0]["data"]["message"]) == 1024


# -- a channel failure is the channel's problem -----------------------------


def test_a_transport_failure_is_returned_not_raised(creds, monkeypatch):
    from robot_army import health

    monkeypatch.setattr(
        health, "post_form", lambda *a, **k: (False, "POST to https://x failed: unreachable")
    )
    ok, detail = channels.PushoverChannel(creds).send("t", "d", {})
    assert ok is False
    assert "unreachable" in detail


def test_an_error_status_is_returned_not_raised(creds, monkeypatch):
    from robot_army import health

    monkeypatch.setattr(health, "post_form", lambda *a, **k: (False, "returned HTTP 400"))
    ok, detail = channels.PushoverChannel(creds).send("t", "d", {})
    assert ok is False
    assert "400" in detail


def test_a_missing_credential_file_is_a_failure_not_an_exception(tmp_path, posted):
    """The file vanished between load and send. The message names the *path*, never the
    contents, because it travels into an audit record."""
    config = PushoverConfig(
        token_file=tmp_path / "gone", user_key_file=tmp_path / "also-gone"
    )
    ok, detail = channels.PushoverChannel(config).send("t", "d", {})
    assert ok is False
    assert "gone" in detail
    assert posted == [], "no request should be attempted without a credential"


def test_an_unexpected_exception_in_the_transport_is_contained(creds, monkeypatch):
    """The never-raises contract, tested against the case it exists for: something the
    channel did not anticipate. A dead channel must never fail a reconciliation pass."""
    from robot_army import health

    def explode(*args: Any, **kwargs: Any):
        raise RuntimeError("boom")

    monkeypatch.setattr(health, "post_form", explode)
    ok, detail = channels.PushoverChannel(creds).send("t", "d", {})
    assert ok is False
    assert "boom" in detail


# -- no credential reaches a record (FR-007, SC-004) ------------------------


def test_no_credential_appears_in_any_returned_message(creds, monkeypatch):
    """Across every failure mode, including an authentication failure — the case where a
    token would otherwise ride along inside an error string rather than in a field anyone
    chose to add."""
    from robot_army import health

    outcomes: list[str] = []

    for reason in (
        "https://api.pushover.net/1/messages.json returned HTTP 400",
        "POST to https://api.pushover.net/1/messages.json failed: timed out",
    ):
        monkeypatch.setattr(health, "post_form", lambda *a, r=reason, **k: (False, r))
        _, detail = channels.PushoverChannel(creds).send("t", "d", {})
        outcomes.append(detail)

    def explode(*args: Any, **kwargs: Any):
        raise RuntimeError("auth rejected")

    monkeypatch.setattr(health, "post_form", explode)
    _, detail = channels.PushoverChannel(creds).send("t", "d", {})
    outcomes.append(detail)

    rendered = json.dumps(outcomes)
    assert TOKEN not in rendered
    assert USER_KEY not in rendered


def test_the_credential_never_reaches_the_url(creds, posted):
    """Pushover authenticates in the request body. ``post_form``'s messages interpolate the
    URL, so keeping the credentials out of it is what makes those messages safe to log by
    construction rather than by a rule someone has to remember."""
    channels.PushoverChannel(creds).send("t", "d", {})
    assert TOKEN not in posted[0]["url"]
    assert USER_KEY not in posted[0]["url"]


def test_the_upstream_response_body_is_never_returned(creds, monkeypatch):
    """Only the status and our own message. Recording an upstream body verbatim is how a
    credential leaks the day the upstream starts echoing the request."""
    import httpx


    def fake_post(url, data=None, timeout=None):
        return httpx.Response(
            400,
            request=httpx.Request("POST", url),
            json={"status": 0, "errors": ["application token is invalid"], "echo": data},
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    ok, detail = channels.PushoverChannel(creds).send("t", "d", {})
    assert ok is False
    assert "HTTP 400" in detail
    assert "application token is invalid" not in detail
    assert TOKEN not in detail
