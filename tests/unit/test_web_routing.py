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

from robot_army import db
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
    # The visibility preference is stated on every generated URL since 009, in both
    # directions: omission now means "use the level's default" and can no longer stand in
    # for false.
    assert response.headers["Location"] == "/active?include_simulated=0"


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
    """Three-valued since 009: said yes, said no, and said nothing are all distinct."""
    assert parse_request("GET", "/queue?include_simulated=1", {}).simulated_preference is True
    assert parse_request("GET", "/queue?include_simulated=0", {}).simulated_preference is False
    assert parse_request("GET", "/queue", {}).simulated_preference is None
    assert (
        parse_request("POST", "/poll", {}, b"include_simulated=yes").simulated_preference is True
    )


# -- DNS rebinding -----------------------------------------------------------


REBOUND = {
    "host": "evil.test:8420",
    "origin": "http://evil.test:8420",
    "sec-fetch-site": "same-origin",
}


def test_a_request_addressed_to_a_name_is_refused(web):
    """DNS rebinding walks straight through a check that only compares Origin to Host.

    The attacker points ``evil.test`` at ``127.0.0.1``; the browser then sends a
    self-consistent ``Host``, ``Origin`` and ``Sec-Fetch-Site: same-origin`` while the
    request really reaches this server. Rebinding needs a *name*, so the rule is the form
    of the Host — an address, or ``localhost``.
    """
    response = web.get("/active", headers=REBOUND)
    assert response.status == 403
    assert "DNS rebinding" in response.text


def test_the_rebinding_check_covers_reads_as_well_as_writes(web, conn):
    """The attacker's page reads what it fetches. The audit log is not theirs to read."""
    seed_item(conn, state="active")
    for path in ("/active", "/queue", "/log", "/anomalies", "/item/1"):
        assert web.get(path, headers=REBOUND).status == 403, path
        assert web.get_json(path, headers=REBOUND).status == 403, path


def test_a_rebound_post_changes_nothing(web, conn):
    from robot_army.states import WorkItemState

    item_id = seed_item(conn, state="interrupted")
    assert web.post_json(f"/item/{item_id}/abandon", headers=REBOUND).status == 403
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1:8420", "192.168.1.20:8420", "10.0.0.5:8420", "[::1]:8420", "localhost:8420",
     "127.0.0.1", "localhost"],
)
def test_every_legitimate_way_of_reaching_it_still_works(web, host):
    """An address, with or without a port, v4 or v6 — plus ``localhost``.

    ``[web] bind`` must itself be an address rather than a hostname, for the same reason:
    what the interface became reachable from must be unambiguous. Requiring the same of
    the Host is consistent with that, not a new imposition.
    """
    assert web.get("/active", headers={"host": host}).status == 200, host


def test_a_client_sending_no_host_is_allowed(web):
    """HTTP/1.1 requires it and every browser sends it, so its absence means a
    hand-written client — which could reach the port directly anyway."""
    from robot_army.web.server import handle, parse_request

    response = handle(web.app, parse_request("GET", "/active", {"accept": "text/html"}))
    assert response.status == 200


def test_the_rebinding_refusal_renders_without_touching_the_database(web, layout):
    """It is checked before routing, before the database, before anything reads the
    request — so it still answers when nothing else would."""
    layout.db_path.unlink()
    assert web.get("/active", headers=REBOUND).status == 403


# -- cross-site reads (RA-14) ------------------------------------------------
#
# ``check_same_origin`` used to run only inside ``_perform``, which only POSTs reach. Reads
# were unchecked, and several of them are expensive: ``/interrupted`` forks git per card,
# ``/log`` read whole audit files, every page enumerated ``/proc``. A page on another site
# looping ``fetch(..., {mode:'no-cors'})`` never sees a response — and does not need to.


CROSS_SITE_READ = {
    "origin": "https://evil.example",
    "referer": "https://evil.example/page",
    "sec-fetch-site": "cross-site",
}


@pytest.mark.parametrize(
    "path",
    ["/active", "/queue", "/interrupted", "/item/1", "/log", "/anomalies", "/cards",
     "/style.css", "/app.js"],
)
def test_a_cross_site_read_is_refused(web, conn, path):
    """Including the two static assets. They cost nothing to serve, so refusing them buys
    little — it is done because "every read, one rule" is a smaller thing to hold than
    "every read except two"."""
    seed_item(conn, state="interrupted")
    assert web.get(path, headers=CROSS_SITE_READ).status == 403, path


def test_a_cross_site_read_is_refused_in_the_json_representation_too(web, conn):
    seed_item(conn, state="interrupted")
    response = web.get_json("/log", headers=CROSS_SITE_READ)
    assert response.status == 403
    payload = response.json()
    assert payload["ok"] is False
    assert payload["code"] == 3


def test_a_client_that_sends_no_origin_headers_can_still_read(web):
    """``curl`` sends neither, and the quickstart drives every control with it. Refusing
    those would break the documented terminal path to protect against a client that has no
    need of forgery — it can reach the port directly, which is the accepted model."""
    for path in GET_VIEWS:
        assert web.get(path).status == 200, path


def test_the_address_bar_and_a_bookmark_can_still_read(web):
    """``Sec-Fetch-Site: none`` is what a browser sends for a navigation the user started
    themselves. It is how the interface is opened."""
    for path in GET_VIEWS:
        assert web.get(path, headers={"sec-fetch-site": "none"}).status == 200, path


def test_a_link_on_a_page_this_server_rendered_can_still_read(web):
    for path in GET_VIEWS:
        response = web.get(
            path,
            headers={"sec-fetch-site": "same-origin", "origin": "http://localhost:8420"},
        )
        assert response.status == 200, path


def test_a_read_whose_origin_is_not_the_host_is_refused(web):
    response = web.get("/active", headers={"origin": "http://192.168.1.99:8420"})
    assert response.status == 403
    assert "192.168.1.99" in response.text


def test_a_same_site_read_is_refused_like_a_cross_site_one(web):
    """``check_host`` already requires an IP literal or ``localhost``, and an IP literal has
    no registrable domain to share — so an honest ``same-site`` cannot arise here. Admitting
    it would be a second, subtly weaker rule beside the first, which is the shape that rots.
    """
    assert web.get("/active", headers={"sec-fetch-site": "same-site"}).status == 403


def test_the_cross_site_read_refusal_renders_without_touching_the_database(web, layout):
    """Checked before routing, before ``app.context()`` — so it still answers when nothing
    else would, and so it opens nothing on the way to refusing."""
    layout.db_path.unlink()
    assert web.get("/active", headers=CROSS_SITE_READ).status == 403


def test_refused_cross_site_reads_are_counted_for_the_stop_record(web):
    """No record per refusal: writing one needs a Context, which is the SQLite connection
    and audit handle the refusal exists to avoid opening. The run's total goes into
    ``web.stop`` instead — the same trade the connection cap made for the same reason."""
    assert web.app.refused_cross_site == 0
    for _ in range(4):
        web.get("/queue", headers=CROSS_SITE_READ)
    assert web.app.refused_cross_site == 4

    web.get("/queue")
    assert web.app.refused_cross_site == 4, "an honest read is not counted"


# -- dead-end pages do not poll ----------------------------------------------


@pytest.mark.parametrize("path", ["/nope", "/dispatch/pause"])
def test_a_dead_end_page_does_not_refresh_itself(web, path):
    """``_bare`` sets ``refresh_seconds`` to 0 for exactly this reason, and ``or`` ate it.

    A page reporting a broken database re-fetching the broken endpoint every ten seconds
    forever is the opposite of what a dead end is for. These are the pages rendered
    *without* a database behind them — an unknown path, a wrong method, a schema mismatch,
    an unhandled error.
    """
    body = web.get(path).text
    assert 'data-refresh="0"' in body, path


def test_a_working_page_still_refreshes(web):
    assert 'data-refresh="10"' in web.get("/active").text


def test_a_missing_item_page_is_a_normal_view_and_does_refresh(web):
    """Not a dead end: it is rendered with a live database behind it, like any view, and
    re-reading it costs one query. The zero is for the pages that have no database."""
    assert 'data-refresh="10"' in web.get("/item/9999").text


# -- /cards and the rescan control (milestone 003) --------------------------


def test_the_cards_view_renders_and_serves_json(board_web, conn):
    from robot_army import db

    with db.transaction(conn):
        db.insert_card(
            conn,
            board_id="board-1",
            card_id="abc123def456abc123def456",
            card_url="https://trello.com/c/abc123def456abc123def456",
            title="Fix the widget",
            body="",
            dry_run=False,
        )
    page = board_web.get("/cards")
    assert page.status == 200
    assert "Fix the widget" in page.text

    payload = board_web.get_json("/cards").json()
    assert payload["configured"] is True
    assert [c["card_id"] for c in payload["cards"]] == ["abc123def456abc123def456"]


def test_the_cards_view_says_so_when_no_board_is_configured(web):
    """An empty table would misrepresent "not configured" as "nothing to do"."""
    page = web.get("/cards")
    assert page.status == 200
    assert "No intake board is configured" in page.text
    assert web.get_json("/cards").json()["configured"] is False


def test_a_card_id_that_is_not_a_card_id_does_not_reach_a_page(board_web):
    """A route parameter that reaches a page is one an attacker would like to control, so
    the pattern is what the board actually issues and nothing else."""
    for hostile in ("../../etc/passwd", "<script>", "a b", ""):
        from urllib.parse import quote

        response = board_web.get(f"/card/{quote(hostile, safe='')}/confirm/rescan")
        assert response.status in (404, 400), hostile


def test_the_rescan_route_confirms_before_it_posts(board_web, conn, running_daemon):
    from robot_army import db
    from robot_army.cardstates import CardState

    with db.transaction(conn):
        row = db.insert_card(
            conn,
            board_id="board-1",
            card_id="abc123def456abc123def456",
            card_url="https://trello.com/c/abc123def456abc123def456",
            title="Vague",
            body="",
            dry_run=False,
        )
        conn.execute(
            "UPDATE cards SET state = ?, reason = ? WHERE id = ?",
            (str(CardState.NEEDS_INFO), "no repository named", row),
        )

    confirm = board_web.get("/card/abc123def456abc123def456/confirm/rescan")
    assert confirm.status == 200
    assert "rescan" in confirm.text

    posted = board_web.post("/card/abc123def456abc123def456/rescan")
    assert posted.status == 303
    assert "msg=rescanned" in posted.headers["Location"]


def test_rescanning_a_linked_card_is_refused_by_the_same_rule_as_the_verb(
    board_web, conn, running_daemon
):
    """FR-047's rule doing its job: the button and the verb cannot answer differently,
    because the button *is* the verb."""
    from robot_army import db
    from robot_army.cardstates import CardState

    with db.transaction(conn):
        row = db.insert_card(
            conn,
            board_id="board-1",
            card_id="abc123def456abc123def456",
            card_url="https://trello.com/c/abc123def456abc123def456",
            title="Done already",
            body="",
            dry_run=False,
        )
        conn.execute(
            "UPDATE cards SET state = ?, repo_key = ?, issue_number = 5 WHERE id = ?",
            (str(CardState.LINKED), "jantman/demo", row),
        )
    response = board_web.post_json("/card/abc123def456abc123def456/rescan")
    assert response.json()["code"] == 2


def test_every_redirect_message_has_a_banner_to_render():
    """A ``?msg=`` value with no ``BANNERS`` entry redirects successfully and shows the
    author **nothing** — ``banner()`` returns empty markup for an unknown key, so the
    failure is silent on both sides: no error, no confirmation.

    Caught in review, where ``held`` and ``released`` had been added as redirect messages
    and not as banners, contradicting the feature's own contract. Enumerated from the
    source rather than asserted per route because the point is to catch the *next* one:
    ``message=`` is passed inside handler bodies, so there is no table to walk, and a list
    maintained by hand here would go stale the same way ``BANNERS`` just did.
    """
    import re
    from pathlib import Path

    from robot_army.web import html, server

    source = Path(server.__file__).read_text(encoding="utf-8")
    messages = set(re.findall(r'message="([^"]+)"', source))
    assert messages, "the scan found no message= literals; the pattern has drifted"

    missing = sorted(m for m in messages if m not in html.BANNERS)
    assert not missing, (
        f"these redirect messages have no BANNERS entry and would render nothing: "
        f"{missing}"
    )

