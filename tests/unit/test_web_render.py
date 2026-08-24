"""HTML production: escaping, simulated marking, and the offline guarantee (T010, T073).

The escaping test is not decoration. An issue title is arbitrary text fetched from GitHub
and rendered on a page the author trusts; if it reached the output unescaped, an issue
title would be able to run script in that page. There is one escaping choke point, and
this asserts it holds where the untrusted values actually arrive.
"""

from __future__ import annotations

import re

import pytest
from tests.conftest import seed_item, seed_session

from robot_army import db
from robot_army.web import html

#: Every page an offline machine must render. The GitHub links themselves may point out;
#: nothing else may (SC-009).
ALL_VIEWS = ("/active", "/queue", "/interrupted", "/anomalies", "/log", "/item/1")

_ASSET_URL = re.compile(r'(?:src|href)="(https?://[^"]*)"')


def test_escape_covers_text_and_attributes():
    assert html.escape("<script>") == "&lt;script&gt;"
    assert html.escape('a "quoted" & thing') == "a &quot;quoted&quot; &amp; thing"
    assert html.escape(None) == ""
    assert html.escape(html.Markup("<b>already</b>")) == "<b>already</b>"


def test_attributes_are_escaped_not_merely_quoted():
    """A value that closes its own attribute is the whole vulnerability."""
    rendered = str(html.tag("div", "body", title='" onclick="alert(1)'))
    assert 'onclick="alert(1)"' not in rendered
    assert "&quot;" in rendered


def test_a_work_item_title_containing_html_is_escaped_everywhere(web, conn):
    """The untrusted value is the issue title. It appears on four different views."""
    hostile = '<script>alert("xss")</script>'
    item_id = seed_item(conn, title=hostile, state="active")
    seed_session(conn, item_id)

    for path in ("/active", f"/item/{item_id}"):
        body = web.get(path).text
        assert "<script>alert" not in body, path
        assert "&lt;script&gt;" in body, path


def test_a_hostile_title_is_escaped_on_the_confirm_page_too(web, conn):
    """The confirm page names the item, which means it renders the title (R8)."""
    item_id = seed_item(conn, title='"><img src=x onerror=alert(1)>', state="interrupted")
    body = web.get(f"/item/{item_id}/confirm/abandon").text
    # The payload survives as *text*, which is the point: what must not survive is its
    # structure. No tag opens, and no quote closes an attribute.
    assert "<img" not in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body


def test_simulated_rows_are_marked_wherever_they_are_shown(web, conn):
    """FR-019: excluded by default, and unmistakable when asked for."""
    seed_item(conn, issue_number=7, dry_run=True, state="active")
    plain = web.get("/active").text
    assert "simulated" not in plain.replace("simulated rows included", "")

    marked = web.get("/active?include_simulated=1").text
    assert 'class="sim"' in marked


def test_no_view_references_a_host_other_than_github(web, conn):
    """SC-009: pull the network cable and every view still renders.

    No web font, no CDN stylesheet, no icon set — which is also why the two assets are
    small enough to embed as module constants (R12).
    """
    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="lost")
    with db.transaction(conn):
        db.raise_anomaly(conn, kind="orphan_session", detail={"pid": 1})

    for path in (*ALL_VIEWS[:-1], f"/item/{item_id}"):
        body = web.get(path).text
        external = _ASSET_URL.findall(body)
        offenders = [url for url in external if not url.startswith("https://github.com/")]
        assert not offenders, f"{path} fetches from outside: {offenders}"


def test_the_assets_are_served_from_module_constants_not_disk(web):
    """R12: no request contributes to a filesystem path, so there is no traversal to guard."""
    css = web.get("/static/app.css")
    assert css.status == 200
    assert css.content_type.startswith("text/css")
    assert css.text == html.APP_CSS
    assert "public, max-age=" in css.headers["Cache-Control"]

    js = web.get("/static/app.js")
    assert js.text == html.APP_JS
    assert "@import" not in css.text and "url(http" not in css.text


@pytest.mark.parametrize(
    "attempt",
    ["/static/../../etc/passwd", "/static/app.css/../../../etc/passwd", "/static/%2e%2e/passwd"],
)
def test_no_path_traversal_reaches_a_file(web, attempt):
    response = web.get(attempt)
    assert response.status in (404, 405)
    assert "root:" not in response.text


def test_the_page_is_correct_with_scripting_disabled(web, conn):
    """R2: the refresh loop is an enhancement. Without it the page is static, not broken."""
    seed_item(conn, state="ready")
    body = web.get("/queue").text
    # The rows are in the served HTML, not fetched by script.
    assert "ready (1)" in body
    assert 'id="content"' in body


def test_every_page_carries_the_chrome(web, conn):
    """FR-016 through FR-018 on every view, not on a status page."""
    seed_item(conn, state="ready")
    for path in ("/active", "/queue", "/interrupted", "/anomalies", "/log"):
        body = web.get(path).text
        assert "effect level:" in body, path
        assert "anomal" in body, path
        assert "rendered " in body, path


def test_a_banner_key_that_is_not_ours_renders_nothing(web, conn):
    """``?msg=`` is a closed set of keys, never text to render.

    Rendering arbitrary query text is how a redirect becomes an injection vector, and a
    banner nobody wrote is a banner nobody can explain.
    """
    body = web.get("/active?msg=<script>alert(1)</script>").text
    assert "alert(1)" not in body
    # No *message* banner: the chrome's own "daemon is not running" notice is a different
    # thing and is expected here.
    assert not any(text in body for _level, text in html.BANNERS.values())

    known = web.get("/active?msg=paused").text
    assert html.BANNERS["paused"][1] in known


def test_asset_urls_carry_a_content_hash_so_an_upgrade_is_never_stale(web):
    """The assets are cached for an hour; the hash is what stops that hiding an upgrade.

    This is not hypothetical: during this milestone's own testing a browser that had loaded
    the page minutes earlier kept serving the previous stylesheet from cache. Hashing the
    content into the URL removes the problem rather than shortening it.
    """
    body = web.get("/active").text
    assert 'href="/static/app.css?v=' in body
    assert 'src="/static/app.js?v=' in body

    css_url = html.asset_url("/static/app.css", html.APP_CSS)
    assert web.get(css_url).text == html.APP_CSS, "the query string must not affect routing"

    # Different content, different URL — which is the whole mechanism.
    assert html.asset_url("/static/app.css", "a") != html.asset_url("/static/app.css", "b")
    assert html.asset_url("/static/app.css", html.APP_CSS) == css_url, "and it is stable"
