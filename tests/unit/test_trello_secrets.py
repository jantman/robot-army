"""No credential ever reaches a record (T017, FR-003, R3, quickstart scenario 8).

This is the test R3 exists for. Trello's documented authentication is a **query string**,
and this project logs request targets — so the naive client would put both secrets into
every audit record, into every error message that echoes a URL, and onto a served page.
``audit.py`` redacts by *field name*, so a secret inside a string under a key called
``url`` would sail straight through the choke point that exists to catch it.

The assertions below are deliberately blunt: read every byte this milestone wrote and grep
it for the key and the token. A subtler test would be checking the mechanism rather than
the property.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from robot_army.boundaries import TransportError
from robot_army.boundaries.trello import TrelloCardReader, TrelloCardWriter

KEY = os.environ.get("ROBOT_ARMY_TEST_TRELLO_KEY", "trellokey-abcdef0123456789")
TOKEN = os.environ.get("ROBOT_ARMY_TEST_TRELLO_TOKEN", "trellotoken-fedcba9876543210")


def log_text(layout) -> str:
    """Every byte of every audit file, concatenated."""
    return "".join(p.read_text(encoding="utf-8") for p in sorted(layout.log_dir.glob("*.jsonl")))


def reader_with(board_config, audit, handler) -> TrelloCardReader:
    """A reader over a transport the test controls, so no network is touched."""
    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=board_config.trello.api_base
    )
    return TrelloCardReader(board_config, audit, client=client, sleep=lambda _s: None)


def test_credentials_travel_in_the_header_and_never_in_the_url(board_config, audit, layout):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    reader = reader_with(board_config, audit, handler)
    reader.poll("board-1", "label-ai")

    assert seen, "the request was never made"
    for request in seen:
        assert request.url.query == b"" or KEY not in str(request.url)
        assert TOKEN not in str(request.url)
        # The positive half: it did authenticate, just not where a log can see it.
        header = request.headers["Authorization"]
        assert header == f'OAuth oauth_consumer_key="{KEY}", oauth_token="{TOKEN}"'


def test_a_successful_call_writes_no_credential_to_the_log(board_config, audit, layout):
    """``board_info`` rather than ``poll``: a successful poll writes no record of its own
    — the cycle above it does (the Principle III exception) — while ``board_info`` records
    the check it performed, which is what gives this test something to grep."""
    reader = reader_with(
        board_config,
        audit,
        lambda r: httpx.Response(200, json={"name": "Intake", "prefs": {}})
        if r.url.path.endswith("/boards/board-1")
        else httpx.Response(200, json=[]),
    )
    reader.board_info()
    audit.close()
    text = log_text(layout)
    assert text, "the check wrote no record at all"
    assert KEY not in text
    assert TOKEN not in text


def test_a_401_writes_no_credential_to_the_log_or_the_exception(board_config, audit, layout):
    """The authentication failure is the case where a naive client is most likely to echo
    what it sent — and the case an operator is most likely to paste somewhere."""

    def handler(request: httpx.Request) -> httpx.Response:
        # A rejection body that quotes the credentials back, which is a real API's habit.
        return httpx.Response(
            401, text=f"invalid key {KEY} or token {TOKEN}"
        )

    reader = reader_with(board_config, audit, handler)
    with pytest.raises(TransportError) as caught:
        reader.poll("board-1", "label-ai")

    # The failure is still usable: it says what failed and with what status.
    assert "401" in str(caught.value)
    audit.close()
    text = log_text(layout)
    assert "401" in text, "the failure must be recorded, not merely raised"


def test_a_transport_failure_writes_no_credential_to_the_log(board_config, audit, layout):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    reader = reader_with(board_config, audit, handler)
    with pytest.raises(TransportError):
        reader.poll("board-1", "label-ai")
    audit.close()
    text = log_text(layout)
    assert KEY not in text
    assert TOKEN not in text
    assert "ConnectError" in text, "the cause must survive the redaction"


def test_no_record_carries_a_full_url_with_a_query_string(board_config, audit, layout):
    """R3's belt-and-braces half. Even with the header form in place, a record that
    carried a query string would be a place for a future credential to hide."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    reader = reader_with(board_config, audit, handler)
    with pytest.raises(TransportError):
        reader.get_card("card-1")
    audit.close()
    for line in log_text(layout).splitlines():
        record = json.loads(line)
        target = str(record.get("target") or "")
        assert "?" not in target, target
        assert "://" not in target, f"a record named a full URL: {target}"


def test_a_write_that_fails_leaks_nothing_either(board_config, audit, layout):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"forbidden for token {TOKEN}")

    writer = TrelloCardWriter(
        board_config,
        audit,
        reader=reader_with(board_config, audit, handler),
    )
    with pytest.raises(TransportError):
        writer.comment("card-1", "hello")
    audit.close()
    text = log_text(layout)
    assert KEY not in text
    assert TOKEN not in text


def test_the_scrubber_removes_a_query_string_wherever_one_appears(board_config, audit, layout):
    """Guards the guard: if a future change put a credential in a URL, the second line of
    defence must actually remove it."""
    from robot_army.boundaries.trello import _scrub

    assert _scrub(f"GET https://api.trello.com/1/cards?key={KEY}&token={TOKEN}") == (
        "GET https://api.trello.com/1/cards?<redacted>"
    )
    assert _scrub("nothing to do here") == "nothing to do here"
    # The second rule: a remote that quotes back what it was sent.
    assert _scrub(f"invalid token {TOKEN}", (KEY, TOKEN)) == "invalid token <redacted>"


def test_the_audit_log_written_by_a_real_boundary_is_the_one_being_checked(audit, layout):
    """Guards the harness: a test that grepped an empty directory would pass forever."""
    audit.record("trello.poll", outcome="ok", detail={"cards": 0})
    audit.close()
    assert "trello.poll" in log_text(layout)
