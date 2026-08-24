"""One test that binds a real port and drives it with ``urllib`` (T027, T040, R15).

Everything else about the web is a pure function of a parsed request, which is what makes
the failure cases cheap enough to write exhaustively. **This exists for the parts a
pure-function test cannot reach** — that the server actually binds, that a browser-shaped
request round-trips, that a ``303`` lands, that the confirm-then-post pair works across two
connections — and those are precisely the parts that break silently.

This mirrors 001's own split, where the single test needing a live session registry was the
one that caught the worst bug in the milestone.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest
from tests.conftest import make_boundaries, seed_item, seed_session

from robot_army import db, operations
from robot_army.states import WorkItemState
from robot_army.web.server import WebApp, build_server


@pytest.fixture
def live_server(config, conn, monkeypatch):
    """A real ``ThreadingHTTPServer`` on an ephemeral port, in a background thread."""
    from tests.conftest import FakeIssueReader, StubDisplay, StubSessionHost

    reader, display, host = FakeIssueReader(), StubDisplay(), StubSessionHost()
    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log: make_boundaries(
            log, level=level, reader=reader, display=display, host=host
        ),
    )
    operations.clear_resume_signal_cache()

    app = WebApp(config)
    server = build_server(app, bind="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.daemon = True
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def fetch(url: str, *, data: dict | None = None, accept: str = "text/html", follow=False):
    """A request that does **not** follow redirects, so a ``303`` is observable."""
    body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
    # S310 does not apply: the URL is this test's own ephemeral loopback server.
    request = urllib.request.Request(url, data=body, headers={"Accept": accept})  # noqa: S310

    opener = urllib.request.build_opener(
        urllib.request.HTTPRedirectHandler if follow else _NoRedirect
    )
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def test_the_server_binds_and_serves_a_page(live_server, conn):
    seed_item(conn, state="active", title="Fix the thing")
    seed_session(conn, 1, state="running")

    status, headers, body = fetch(f"{live_server}/active")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert headers["Cache-Control"] == "no-store"
    assert "Fix the thing" in body
    assert "<!DOCTYPE html>" in body


def test_a_json_suffix_returns_the_same_facts_as_the_page(live_server, conn):
    seed_item(conn, issue_number=1, state="ready")
    seed_item(conn, issue_number=2, state="ready")

    status, headers, body = fetch(f"{live_server}/queue.json")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    payload = json.loads(body)
    assert payload["counts"]["ready"] == 2
    assert payload["effect_level"] == "live"
    assert "rendered_at" in payload


def test_an_unknown_path_round_trips_as_a_404_page(live_server):
    status, headers, body = fetch(f"{live_server}/definitely-not-a-route")
    assert status == 404
    assert headers["Content-Type"].startswith("text/html")
    assert "not found" in body


def test_the_stylesheet_and_script_are_served(live_server):
    for path, media in (("/static/app.css", "text/css"), ("/static/app.js", "text/javascript")):
        status, headers, body = fetch(f"{live_server}{path}")
        assert status == 200, path
        assert headers["Content-Type"].startswith(media)
        assert body.strip()
        assert "max-age" in headers["Cache-Control"]


def test_a_get_on_a_write_route_is_405_with_allow(live_server):
    status, headers, _body = fetch(f"{live_server}/dispatch/pause")
    assert status == 405
    assert headers["Allow"] == "POST"


def test_the_root_redirects(live_server):
    status, headers, _body = fetch(f"{live_server}/")
    assert status == 303
    assert headers["Location"] == "/active"


# -- T040: the confirm-then-post round trip ---------------------------------


def test_confirm_then_post_advances_the_item(live_server, conn):
    """Two connections, exactly as a phone makes them: read the confirm page, submit it."""
    item_id = seed_item(conn, state="interrupted")

    status, _headers, confirm = fetch(f"{live_server}/item/{item_id}/confirm/abandon")
    assert status == 200
    assert f'action="/item/{item_id}/abandon"' in confirm
    assert 'method="post"' in confirm
    # The item has not moved merely by being looked at.
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED

    status, headers, _body = fetch(f"{live_server}/item/{item_id}/abandon", data={})
    assert status == 303
    assert headers["Location"] == f"/item/{item_id}?msg=abandoned"

    assert db.get_work_item(conn, item_id).state is WorkItemState.ABANDONED


def test_following_the_redirect_lands_on_a_page_with_the_banner(live_server, conn):
    item_id = seed_item(conn, state="interrupted")
    fetch(f"{live_server}/item/{item_id}/abandon", data={})
    _status, _headers, body = fetch(f"{live_server}/item/{item_id}?msg=abandoned")
    assert "worktree was left in place" in body


def test_a_reload_after_a_post_re_issues_a_get_and_does_not_re_post(live_server, conn):
    """R7's browser half, across a real connection."""
    item_id = seed_item(conn, state="interrupted")
    status, headers, _ = fetch(f"{live_server}/item/{item_id}/abandon", data={})
    assert status == 303

    # What the browser does next is a GET of the Location, and it is safe to repeat.
    for _ in range(3):
        status, _headers, _body = fetch(f"{live_server}{headers['Location']}")
        assert status == 200
    assert db.get_work_item(conn, item_id).state is WorkItemState.ABANDONED


def test_a_refused_action_reports_the_reason_over_the_wire(live_server, conn):
    item_id = seed_item(conn, state="done")
    status, headers, body = fetch(
        f"{live_server}/item/{item_id}/abandon", data={}, accept="application/json"
    )
    assert status == 409
    assert headers["Content-Type"].startswith("application/json")
    payload = json.loads(body)
    assert payload["ok"] is False
    assert payload["code"] == 3
    assert "done" in payload["reason"]


def test_pausing_over_the_wire_is_durable_in_the_database(live_server, conn):
    status, _headers, body = fetch(
        f"{live_server}/dispatch/pause", data={}, accept="application/json"
    )
    assert status == 303
    assert json.loads(body)["paused"] is True
    assert db.get_dispatch_control(conn).paused is True

    fetch(f"{live_server}/dispatch/unpause", data={}, accept="application/json")
    assert db.get_dispatch_control(conn).paused is False


def test_concurrent_requests_are_served(live_server, conn):
    """Threading exists so one slow request does not stall the page behind it."""
    seed_item(conn, state="ready")
    results: list[int] = []
    lock = threading.Lock()

    def hit() -> None:
        status, _headers, _body = fetch(f"{live_server}/queue.json")
        with lock:
            results.append(status)

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert results == [200] * 8


def test_an_oversized_body_is_refused_rather_than_read(live_server, conn):
    from robot_army.web.server import MAX_BODY_BYTES

    item_id = seed_item(conn, state="interrupted")
    payload = urllib.parse.urlencode({"junk": "x" * (MAX_BODY_BYTES + 1024)}).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - our own loopback server
        f"{live_server}/item/{item_id}/abandon",
        data=payload,
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    assert status == 413
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
