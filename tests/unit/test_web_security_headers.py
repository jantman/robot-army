"""Every response carries the security headers, and carries what it carried before (RA-12).

The finding these close is that the same-origin check cannot tell a click inside a hostile
frame from an honest one: the browser reports ``Sec-Fetch-Site: same-origin`` in both cases,
because in both cases it is true. The frame has to be refused rather than the click.

The shape of this file matters more than any single assertion in it. The coverage matrix
below is written against :data:`server.SECURITY_HEADERS` itself rather than against a list
of header names copied into the test, so adding a header to the constant automatically
requires it on all eight response paths without anyone remembering to widen the matrix.
That is the same property the implementation has — attach once, at construction — expressed
in the direction a test can fail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tests.conftest import seed_item

from robot_army import operations
from robot_army.web import html, server
from robot_army.web.server import handle, parse_request

CSP = "Content-Security-Policy"


def every_response(web, conn):
    """One of each: every distinct way this server builds a response.

    Named rather than parametrised so a failure says *which* path lost the headers, which
    is the only thing worth knowing when this fails.
    """
    seed_item(conn, issue_number=1, state="ready")
    return {
        "html page": web.get("/queue"),
        "json page": web.get_json("/queue"),
        "confirm page": web.get("/item/1/confirm/abandon"),
        "redirect": web.get("/"),
        "404": web.get("/nope"),
        "405": web.post("/active"),
        "css": web.get("/static/app.css"),
        "js": web.get("/static/app.js"),
    }


# -- the matrix -------------------------------------------------------------


def test_every_response_path_carries_every_security_header(web, conn):
    """The whole point. Eight paths, and whatever the constant says, on all of them."""
    assert server.SECURITY_HEADERS, "the constant is empty; there is nothing to enforce"
    for name, response in every_response(web, conn).items():
        for header, value in server.SECURITY_HEADERS.items():
            assert response.headers.get(header) == value, f"{header} missing from {name}"


def test_a_bare_response_already_carries_them(web):
    """FR-005, pinned.

    This can only pass if the headers attach at construction. A ``Response`` built by no
    handler, on no route, with no arguments, is the sixth response path that does not exist
    yet — and it is already covered.
    """
    assert server.Response().headers == dict(server.SECURITY_HEADERS)


def test_a_caller_that_sets_one_deliberately_wins():
    """The merge order, stated as a test rather than left to whichever line ran last.

    Nothing in the codebase sets these names today. If something ever does, it will be
    because it meant to, and this says what happens then.
    """
    response = server.Response(headers={"X-Frame-Options": "SAMEORIGIN"})
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_the_database_less_refusals_carry_them(web, monkeypatch):
    """A page that only appears when something is already broken is not a gap in the fence.

    The ``503`` is the interesting one: it is rendered by ``_bare`` precisely because there
    is no usable database behind it, so it takes none of the normal path.
    """

    def explode() -> None:
        raise operations.SchemaMismatch(3, 4, Path("/tmp/robot-army.db"))

    monkeypatch.setattr(web.app, "context", explode)
    response = web.get("/queue")
    assert response.status == 503
    for header, value in server.SECURITY_HEADERS.items():
        assert response.headers.get(header) == value


def test_a_rebound_host_is_refused_with_the_headers_on_it(web):
    """The host check answers before routing, before the database, before anything."""
    response = web.get("/queue", headers={"host": "attacker.example.com"})
    assert response.status >= 400
    for header, value in server.SECURITY_HEADERS.items():
        assert response.headers.get(header) == value


# -- the framing refusal (US1) ----------------------------------------------


def test_framing_is_refused_outright(web, conn):
    for name, response in every_response(web, conn).items():
        assert response.headers["X-Frame-Options"] == "DENY", name
        assert "frame-ancestors 'none'" in response.headers[CSP], name


def test_the_confirm_page_is_not_the_soft_target(web, conn):
    """A confirm-gated verb needs two baited clicks — unless the attacker frames the
    confirmation page itself, which is a plain ``GET`` link, and baits one click on the
    real button. So it needs the refusal as much as any action page does."""
    seed_item(conn, issue_number=1, state="ready")
    response = web.get("/item/1/confirm/abandon")
    assert response.status == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers[CSP]


# -- the content policy (US2) -----------------------------------------------


def directives(response) -> dict[str, str]:
    """``{name: value}`` from a policy, split on ``;`` so a reordering is not a failure."""
    parsed = {}
    for directive in response.headers[CSP].split(";"):
        name, _, value = directive.strip().partition(" ")
        parsed[name] = value
    return parsed


@pytest.mark.parametrize(
    ("directive", "value"),
    [
        ("frame-ancestors", "'none'"),
        ("default-src", "'self'"),
        ("base-uri", "'none'"),
        ("form-action", "'self'"),
    ],
)
def test_the_policy_states_each_directive(web, directive, value):
    assert directives(web.get("/queue"))[directive] == value


#: Tags whose ``href``/``src`` the browser *fetches*. An ``<a href>`` is not one of them:
#: CSP governs resource loading, not navigation, and the item and audit views link out to
#: ``github.com`` and ``trello.com`` by design.
SUBRESOURCE = re.compile(r"<(?:link|script|img|source|iframe|embed|object)\b[^>]*>")
LOADED_URL = re.compile(r'(?:href|src|data)="([^"]*)"')


def subresource_urls(body: str) -> list[str]:
    return [url for tag in SUBRESOURCE.findall(body) for url in LOADED_URL.findall(tag)]


def test_the_pages_load_nothing_the_policy_would_refuse(web, conn):
    """FR-007, from the other end: the policy is only free because the pages are austere.

    ``default-src 'self'`` breaks the interface the moment a page grows a web font, a CDN
    script or an icon set. This fails there, in a unit test, rather than silently in a
    browser nobody has open.

    The item is seeded deliberately: a populated ``/queue`` carries an outbound
    ``github.com`` anchor, and this test has to *tolerate* that while still refusing an
    external subresource. An empty page would prove neither.
    """
    seed_item(conn, issue_number=1, state="ready")
    for path in ("/queue", "/active", "/item/1", "/cards", "/log"):
        body = web.get(path).text
        assert "github.com" in body or path != "/queue", "the anchor case is not covered"
        for url in subresource_urls(body):
            assert url.startswith(("/", "#")), f"{path} loads {url}"
        assert "<script>" not in body, "an inline script would need 'unsafe-inline'"
        assert "<style" not in body and "style=" not in body, "an inline style would too"
        assert "onerror=" not in body and "onload=" not in body


def test_an_external_subresource_would_be_caught(web):
    """The check above is only worth having if it fails on the thing it is watching for."""
    cdn = '<link rel="stylesheet" href="https://fonts.example.com/x.css">'
    assert subresource_urls(cdn) == ["https://fonts.example.com/x.css"]
    assert subresource_urls('<a href="https://github.com/x/y/issues/1">#1</a>') == []


def test_the_refresh_loop_only_ever_fetches_its_own_url(web):
    """``connect-src`` falls through to ``default-src 'self'``, so this must stay true."""
    assert "fetch(window.location.href" in html.APP_JS


# -- sniffing and referrers (US3) -------------------------------------------


def test_nothing_is_sniffed_and_nothing_leaks_a_referrer(web, conn):
    """Three content types, because guessing past the declared one is the whole attack."""
    responses = every_response(web, conn)
    for name in ("html page", "json page", "css", "js"):
        assert responses[name].headers["X-Content-Type-Options"] == "nosniff", name
        assert responses[name].headers["Referrer-Policy"] == "no-referrer", name


# -- nothing displaced (FR-006) ---------------------------------------------


def test_the_headers_a_response_already_set_are_untouched(web, conn):
    responses = every_response(web, conn)
    assert responses["html page"].headers["Cache-Control"] == "no-store"
    assert responses["json page"].headers["Cache-Control"] == "no-store"
    assert responses["css"].headers["Cache-Control"] == "public, max-age=3600"
    assert responses["js"].headers["Cache-Control"] == "public, max-age=3600"
    assert responses["redirect"].headers["Location"] == "/active?include_simulated=0"
    assert "GET" in responses["405"].headers["Allow"]


def test_the_oversized_body_refusal_keeps_its_close(web):
    """The ``413`` is built at the socket, not by the renderer. Same construction, though —
    which is exactly why it needs no special case here."""
    response = server.Response(
        status=413, headers={**server.NO_STORE, "Connection": "close"}
    )
    assert response.headers["Connection"] == "close"
    assert response.headers["Cache-Control"] == "no-store"
    for header, value in server.SECURITY_HEADERS.items():
        assert response.headers[header] == value


def test_no_security_header_collides_with_a_header_a_response_sets(web, conn):
    """Stated as its own test because FR-006 is a claim about the *names*, not the values:
    if a future header here were called ``Cache-Control``, every assertion above would
    still pass while the interface quietly started caching."""
    reserved = {"Cache-Control", "Location", "Allow", "Connection", "Content-Type"}
    assert not reserved & set(server.SECURITY_HEADERS)


def test_a_handler_cannot_reach_back_into_the_constant(web, conn):
    """The merge builds a new dict each time, so mutating one response's headers — as
    ``_bare`` does to add ``Allow`` — cannot edit the headers of every later response."""
    before = dict(server.SECURITY_HEADERS)
    response = handle(
        web.app, parse_request("GET", "/queue", {"host": "localhost:8420"})
    )
    response.headers["X-Frame-Options"] = "tampered"
    assert before == server.SECURITY_HEADERS
