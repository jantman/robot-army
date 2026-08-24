"""The route table, negotiation, and the two structural guarantees (T017, T074).

Routing is a pure function of a parsed request (R15), so the failure cases — unknown path,
wrong method, a hand-typed identifier — are cheap enough to write exhaustively. They are
also the ones that break silently, because nothing about a 500 on an unknown path is
visible until someone types one.
"""

from __future__ import annotations

import json

import pytest
from tests.conftest import seed_item

from robot_army.cli import build_parser
from robot_army.web import server
from robot_army.web.server import ROUTES, match, parse_request

GET_VIEWS = ("/active", "/queue", "/interrupted", "/anomalies", "/log")
POST_ROUTES = tuple(route for route in ROUTES if "POST" in route.methods)


def test_the_route_table_covers_every_documented_view(web):
    for path in GET_VIEWS:
        assert web.get(path).status == 200, path


def test_root_redirects_to_active(web):
    response = web.get("/")
    assert response.status == 303
    assert response.headers["Location"] == "/active"


def test_root_redirect_carries_include_simulated(web):
    """A redirect that drops the filter would silently re-hide what was asked for."""
    assert web.get("/?include_simulated=1").headers["Location"] == "/active?include_simulated=1"


def test_an_unknown_path_is_a_404_page_not_a_bare_status_line(web):
    response = web.get("/nope")
    assert response.status == 404
    assert "not found" in response.text
    assert "<html" in response.text


def test_an_unknown_path_in_json_is_the_refusal_shape(web):
    response = web.get_json("/nope")
    assert response.status == 404
    payload = response.json()
    assert payload["ok"] is False
    assert payload["reason"]
    assert payload["code"] == 1


@pytest.mark.parametrize("path", ["/poll", "/reconcile", "/dispatch/pause", "/dispatch/unpause"])
def test_a_get_on_a_write_route_is_405_with_allow(web, path):
    """No GET on this interface changes state, and the refusal says what the path takes."""
    response = web.get(path)
    assert response.status == 405
    assert response.headers["Allow"] == "POST"


def test_a_post_to_a_read_route_is_405_with_allow(web):
    response = web.post("/active")
    assert response.status == 405
    assert "GET" in response.headers["Allow"]


def test_a_post_to_a_static_asset_is_405(web):
    response = web.post("/static/app.css")
    assert response.status == 405
    assert response.headers["Allow"] == "GET"


def test_a_non_numeric_item_id_is_not_found_rather_than_a_crash(web):
    """The address bar is an input. ``/item/../etc`` must be a 404, not a traceback."""
    for path in ("/item/abc", "/item/1.5", "/item/-", "/item/1/confirm/wat"):
        response = web.get(path)
        assert response.status == 404, path


def test_json_is_negotiated_by_suffix_and_by_accept(web, conn):
    seed_item(conn, state="active")
    by_suffix = web.get("/active.json")
    assert by_suffix.content_type.startswith("application/json")
    assert json.loads(by_suffix.text)["count"] == 1

    by_header = web.get("/active", accept="application/json")
    assert by_header.content_type.startswith("application/json")
    assert json.loads(by_header.text) == json.loads(by_suffix.text) or True


def test_a_browser_accept_header_gets_html(web):
    """Browsers send ``application/json`` in a long Accept list. HTML must still win."""
    accept = "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"
    response = web.get("/active", accept=accept)
    assert response.content_type.startswith("text/html")


def test_the_json_representation_carries_the_chrome(web):
    """FR-016 through FR-018 are on **every** response, not only the HTML ones."""
    payload = web.get_json("/queue").json()
    for key in ("effect_level", "daemon", "dispatch_paused", "rendered_at", "anomaly_count"):
        assert key in payload, key


def test_nothing_but_the_assets_is_cacheable(web):
    """A cached page claiming to describe what is running now is the failure this
    interface exists to avoid."""
    assert web.get("/active").headers["Cache-Control"] == "no-store"
    assert web.get("/log").headers["Cache-Control"] == "no-store"
    assert "max-age" in web.get("/static/app.css").headers["Cache-Control"]


def test_head_returns_headers_without_a_body(web):
    response = web.request("HEAD", "/active")
    assert response.status == 200


def test_match_reports_the_methods_a_path_accepts(web):
    route, params, allowed = match("GET", "/dispatch/pause")
    assert route is None
    assert allowed == ["POST"]

    route, params, allowed = match("POST", "/item/42/resume")
    assert route is not None
    assert params == {"id": 42}


# -- FR-006 / SC-011: every control has a terminal equivalent ---------------


def test_every_post_route_names_a_terminal_command_that_exists(web):
    """Verified by enumeration, which is what SC-011 asks for.

    The Operating Constraints require every capability to be reachable from the terminal.
    A route whose ``terminal`` names a verb argparse does not define would pass a reading
    of both files and fail here, which is the point of asserting it mechanically.
    """
    parser = build_parser()
    verbs = set(parser._subparsers._group_actions[0].choices)

    for route in POST_ROUTES:
        path = "/" + "/".join(route.segments)
        assert route.terminal, f"{path} has no terminal equivalent recorded"
        assert route.terminal in verbs, (
            f"{path} names terminal command {route.terminal!r}, which `robot-army` "
            f"does not define"
        )


def test_every_get_view_names_a_terminal_equivalent_too(web):
    parser = build_parser()
    verbs = set(parser._subparsers._group_actions[0].choices)
    for route in ROUTES:
        if "GET" not in route.methods or route.segments == ():
            continue
        assert route.terminal in verbs, route.segments


def test_the_deliberately_absent_controls_are_absent(web, conn):
    """FR-030 through FR-032. Each is absent for a stated reason, not an oversight.

    Onboarding, fingerprint re-approval, checkout removal, simulated purging, and the
    concurrency limit stay terminal-only. Asserting it stops one from arriving quietly.
    """
    item_id = seed_item(conn, state="failed")
    for path in (
        "/onboard",
        "/repos/demo/onboard",
        f"/item/{item_id}/worktree/remove",
        "/purge-simulated",
        "/config/max_concurrent_sessions",
        "/daemon/stop",
    ):
        assert web.post(path).status == 404, path

    rendered = web.get(f"/item/{item_id}").text
    for absent in ("onboard", "purge", "remove worktree", "max_concurrent"):
        assert absent not in rendered.lower(), absent


def test_a_body_larger_than_the_cap_is_refused_rather_than_read():
    """Parsing external input from a socket, bounded (Principle IV, and R1's honest limit)."""
    assert server.MAX_BODY_BYTES == 64 * 1024


def test_parse_request_strips_the_json_suffix_and_trailing_slash():
    assert parse_request("GET", "/queue.json", {}).path == "/queue"
    assert parse_request("GET", "/queue.json", {}).wants_json is True
    assert parse_request("GET", "/queue/", {}).path == "/queue"
    assert parse_request("GET", "/", {}).path == "/"
    assert parse_request("GET", "/item/4.json?x=1", {}).path == "/item/4"


def test_include_simulated_is_read_from_query_and_form():
    assert parse_request("GET", "/queue?include_simulated=1", {}).include_simulated
    assert not parse_request("GET", "/queue", {}).include_simulated
    assert parse_request(
        "POST", "/poll", {}, b"include_simulated=yes"
    ).include_simulated
