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


def fetch(
    url: str,
    *,
    data: dict | None = None,
    accept: str = "text/html",
    follow=False,
    headers: dict[str, str] | None = None,
):
    """A request that does **not** follow redirects, so a ``303`` is observable."""
    body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
    # S310 does not apply: the URL is this test's own ephemeral loopback server.
    request = urllib.request.Request(  # noqa: S310
        url, data=body, headers={"Accept": accept, **(headers or {})}
    )

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
    assert headers["Location"] == "/active?include_simulated=0"


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
    assert headers["Location"] == f"/item/{item_id}?msg=abandoned&include_simulated=0"

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


def test_an_oversized_body_does_not_desync_the_keep_alive_connection(live_server, conn):
    """The refusal must not corrupt whatever the client sends next on the same connection.

    The body is never read, so its bytes stay queued on the socket. Under HTTP/1.1
    keep-alive the next read parses them as the start of the following request line: a
    perfectly good ``GET`` came back as a garbage 414. ``urllib`` opens a fresh connection
    per request, which is exactly why the existing test could not see this — so this one
    speaks the protocol directly.
    """
    import socket as socketlib

    from robot_army.web.server import MAX_BODY_BYTES

    item_id = seed_item(conn, state="interrupted")
    host, port = urllib.parse.urlsplit(live_server).hostname, urllib.parse.urlsplit(
        live_server
    ).port

    connection = socketlib.create_connection((host, port), timeout=10)
    try:
        payload = b"x" * (MAX_BODY_BYTES + 512)
        connection.sendall(
            b"POST /item/%d/abandon HTTP/1.1\r\nHost: %s\r\n"
            b"Content-Type: application/x-www-form-urlencoded\r\n"
            b"Content-Length: %d\r\n\r\n"
            % (item_id, host.encode(), len(payload))
            + payload
        )
        received = b""
        while b"\r\n\r\n" not in received:
            chunk = connection.recv(4096)
            if not chunk:
                break
            received += chunk

        head = received.split(b"\r\n\r\n")[0].decode("latin-1")
        assert head.split("\r\n")[0].startswith("HTTP/1.1 413")
        assert "Connection: close" in head, (
            "without this the client reuses a connection with unread body bytes on it"
        )
    finally:
        connection.close()

    # The action did not happen, and the server is still healthy for the next client.
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    status, _headers, _body = fetch(f"{live_server}/active")
    assert status == 200


def test_a_browser_shaped_confirm_then_post_lands_on_a_page_that_says_it_worked(
    live_server, conn
):
    """What a phone actually sends, including the ``Referer`` the test client omitted.

    The pure-function tests cover the redirect target; this one exercises it over the wire
    with the header a browser really attaches, because that header is what turned the
    success path into a 409.
    """
    item_id = seed_item(conn, state="interrupted")
    confirm = f"{live_server}/item/{item_id}/confirm/abandon"

    status, _headers, page = fetch(confirm)
    assert status == 200
    assert f'action="/item/{item_id}/abandon"' in page

    status, response_headers, _body = fetch(
        f"{live_server}/item/{item_id}/abandon",
        data={},
        headers={
            "Referer": confirm,
            "Origin": live_server,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert status == 303
    location = response_headers["Location"]

    assert "/confirm/" not in location
    status, _headers, landed = fetch(f"{live_server}{location}")
    assert status == 200
    assert "worktree was left in place" in landed


def test_a_cross_site_post_over_the_wire_is_refused(live_server, conn):
    item_id = seed_item(conn, state="interrupted")
    status, _headers, _body = fetch(
        f"{live_server}/item/{item_id}/abandon",
        data={},
        accept="application/json",
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert status == 403
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED


def test_a_cross_site_get_over_the_wire_is_refused(live_server, conn):
    """RA-14, over a real socket. The response is opaque to the page that sent it either way
    — what changes is that the interface no longer does the work it asked for."""
    seed_item(conn, state="interrupted")
    status, _headers, body = fetch(
        f"{live_server}/interrupted",
        accept="application/json",
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert status == 403
    assert json.loads(body)["code"] == 3


def test_the_queue_renders_without_ever_asking_github(config, conn, layout, monkeypatch):
    """FR-005 and SC-004, asserted rather than eyeballed.

    ``ordering.plan`` runs on every page render, and the whole reason the board is read at
    poll time and stored is that a render must never make a network call. A reader that
    raises on *any* attribute access turns a regression here into a failure rather than
    into a page that is merely slower.
    """
    from tests.conftest import WebHarness, make_boundaries, seed_item

    from robot_army import db, operations
    from robot_army.models import RepoProject
    from robot_army.web.server import WebApp

    class ExplodingReader:
        def __getattr__(self, name):
            def boom(*args, **kwargs):
                raise AssertionError(f"rendering must not reach GitHub ({name})")

            return boom

    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log: make_boundaries(
            log, level=level, reader=ExplodingReader()
        ),
    )
    operations.clear_resume_signal_cache()

    ranked = seed_item(conn, issue_number=1, state="ready")
    parked = seed_item(conn, issue_number=2, state="ready")
    with db.transaction(conn):
        db.save_repo_project(
            conn,
            RepoProject(
                repo_key="demo",
                project_id="PVT_3",
                project_number=3,
                project_title="robot-army",
                column_name="Ready",
                project_source="discovered",
                column_source="discovered",
                resolved_at="2026-09-02T00:00:00Z",
                last_read_at="2026-09-02T00:00:00Z",
            ),
        )
    conn.execute(
        "UPDATE work_items SET board_column = 'Ready', board_position = 1 WHERE id = ?",
        (ranked,),
    )
    conn.execute(
        "UPDATE work_items SET board_column = 'Backlog' WHERE id = ?", (parked,)
    )

    harness = WebHarness(WebApp(config), reader=None, display=None, host=None, vcs=None)
    try:
        page = harness.get("/queue").text
        payload = harness.get_json("/queue").json()
        harness.get("/")
    finally:
        operations.clear_resume_signal_cache()

    assert "not the dispatch column" in page
    assert payload["held_off_column"] == 1
    assert payload["ready"][0]["id"] == ranked


# -- RA-12: the security headers, across a real socket ----------------------


def test_the_security_headers_survive_the_wire(live_server, conn):
    """The unit tests prove ``handle`` puts them on the ``Response``. This proves nothing
    between there and the socket drops them — and covers the ``303``, which no browser
    renders but every action returns."""
    from robot_army.web.server import SECURITY_HEADERS

    item_id = seed_item(conn, state="interrupted")
    responses = {
        "page": fetch(f"{live_server}/active"),
        "json": fetch(f"{live_server}/queue.json", accept="application/json"),
        "stylesheet": fetch(f"{live_server}/static/app.css"),
        "404": fetch(f"{live_server}/definitely-not-a-route"),
        "405": fetch(f"{live_server}/dispatch/pause"),
        "303": fetch(f"{live_server}/item/{item_id}/abandon", data={}),
    }
    for name, (_status, headers, _body) in responses.items():
        for header, value in SECURITY_HEADERS.items():
            assert headers.get(header) == value, f"{header} missing from {name}"

    # And what each of them carried before is still there (FR-006).
    assert responses["page"][1]["Cache-Control"] == "no-store"
    assert "max-age" in responses["stylesheet"][1]["Cache-Control"]
    assert responses["405"][1]["Allow"] == "POST"
    assert responses["303"][1]["Location"].startswith(f"/item/{item_id}?")


def test_a_head_sends_the_same_headers_as_the_get(live_server):
    """A ``HEAD`` withholds the body and nothing else. Worth asserting because the headers
    and the body are written by different branches of ``_respond``."""
    from robot_army.web.server import SECURITY_HEADERS

    request = urllib.request.Request(  # noqa: S310 - our own loopback server
        f"{live_server}/active", method="HEAD"
    )
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=10) as response:
        headers = dict(response.headers)
        assert response.read() == b""

    for header, value in SECURITY_HEADERS.items():
        assert headers.get(header) == value


def test_the_oversized_body_refusal_carries_the_headers(live_server, conn):
    """The one response written directly at the socket boundary, which never reaches the
    page renderer — and would have been the hole in any per-call-site fix."""
    from robot_army.web.server import MAX_BODY_BYTES, SECURITY_HEADERS

    item_id = seed_item(conn, state="interrupted")
    payload = urllib.parse.urlencode({"junk": "x" * (MAX_BODY_BYTES + 1024)}).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - our own loopback server
        f"{live_server}/item/{item_id}/abandon",
        data=payload,
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=10) as response:
            status, headers = response.status, dict(response.headers)
    except urllib.error.HTTPError as exc:
        status, headers = exc.code, dict(exc.headers)

    assert status == 413
    for header, value in SECURITY_HEADERS.items():
        assert headers.get(header) == value
    assert headers["Connection"] == "close"
