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

#: Every page an offline machine must render. Links to the two systems this project reads
#: from may point out; nothing else may (SC-009).
ALL_VIEWS = ("/active", "/queue", "/interrupted", "/cards", "/anomalies", "/log", "/item/1")

#: The only origins an href may name. Both are *sources this system already reads from*,
#: and both links are built from data already stored with no additional call — which is
#: the rule FR-043 states and ``github_link``/``card_link`` are the only implementations of.
ALLOWED_ORIGINS = ("https://github.com/", "https://trello.com/")

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
    """FR-019: excluded by default at ``live``, and unmistakable when asked for.

    Asserts on the badge markup rather than on the bare word "simulated", which 009 made
    useless as a signal: a page that is *withholding* simulated rows now says so, in the
    visibility toggle and in the withheld-row disclosure, so the word appears on pages with
    no simulated row on them at all. The badge is the thing FR-019 actually requires.
    """
    seed_item(conn, issue_number=7, dry_run=True, state="active")
    plain = web.get("/active").text
    assert 'class="sim"' not in plain

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
        offenders = [url for url in external if not url.startswith(ALLOWED_ORIGINS)]
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


# -- the cards view (milestone 003) -----------------------------------------


def seed_card(conn, **overrides):
    from robot_army import db

    values = {
        "board_id": "board-1",
        "card_id": "abc123def456abc123def456",
        "card_url": "https://trello.com/c/abc123def456abc123def456",
        "title": "Fix the widget",
        "body": "",
        "dry_run": False,
    }
    values.update(overrides)
    with db.transaction(conn):
        row_id = db.insert_card(conn, **values)
    return row_id


def test_a_hostile_card_title_is_escaped(board_web, conn):
    """A card's text is written by whoever can put a card on the board, and the planning
    document calls it semi-untrusted. It reaches a page the author trusts."""
    seed_card(conn, title='<script>alert("card")</script>')
    body = board_web.get("/cards").text
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body


def test_a_hostile_needs_info_reason_is_escaped_too(board_web, conn):
    """The reason is assembled from the card's own text, so it carries the same risk."""
    from robot_army import db
    from robot_army.cardstates import CardState

    row_id = seed_card(conn, title="Vague")
    with db.transaction(conn):
        conn.execute(
            "UPDATE cards SET state = ?, reason = ? WHERE id = ?",
            (str(CardState.NEEDS_INFO), '<img src=x onerror=alert(1)>', row_id),
        )
    body = board_web.get("/cards").text
    assert "<img" not in body
    assert "&lt;img" in body


def test_simulated_cards_are_excluded_by_default_and_marked_when_included(board_web, conn):
    """FR-019, on the new view: excluded by default, unmistakable when asked for."""
    seed_card(conn, card_id="simulatedcard000000000aa", title="Rehearsal", dry_run=True)

    plain = board_web.get("/cards").text
    assert "Rehearsal" not in plain

    marked = board_web.get("/cards?include_simulated=1").text
    assert "Rehearsal" in marked
    assert 'class="sim"' in marked


def test_a_card_links_to_the_board_and_to_its_issue(board_web, conn):
    from robot_army import db
    from robot_army.cardstates import CardState

    row_id = seed_card(conn)
    with db.transaction(conn):
        conn.execute(
            """UPDATE cards SET state = ?, repo_key = 'jantman/demo', issue_number = 7,
                                issue_url = 'https://github.com/jantman/demo/issues/7'
               WHERE id = ?""",
            (str(CardState.LINKED), row_id),
        )
    body = board_web.get("/cards").text
    assert 'href="https://trello.com/c/abc123def456abc123def456"' in body
    assert 'href="https://github.com/jantman/demo/issues/7"' in body


def test_a_work_item_shows_its_card_beside_its_issue(board_web, conn):
    """FR-017 and FR-048. Derived by join, with no column on ``work_items`` (R16)."""
    from robot_army import db
    from robot_army.cardstates import CardState

    row_id = seed_card(conn)
    with db.transaction(conn):
        conn.execute(
            """UPDATE cards SET state = ?, repo_key = 'jantman/demo', issue_number = 42
               WHERE id = ?""",
            (str(CardState.LINKED), row_id),
        )
    item_id = seed_item(conn, repo_key="jantman/demo", issue_number=42, state="active")

    body = board_web.get(f"/item/{item_id}").text
    assert "https://trello.com/c/abc123def456abc123def456" in body
    payload = board_web.get_json(f"/item/{item_id}").json()
    assert payload["card"]["card_id"] == "abc123def456abc123def456"


def test_a_work_item_with_no_card_says_so_rather_than_showing_a_gap(web, conn):
    item_id = seed_item(conn, state="active")
    payload = web.get_json(f"/item/{item_id}").json()
    assert payload["card"] is None


# -- milestone 009: the badge is on every view that shows a row --------------


def test_the_badge_appears_on_every_row_bearing_view(board_web, conn):
    """FR-019, pinned rather than trusted.

    The badge and its stylesheet rule both predate this milestone — the ``*`` suffix the
    issue describes is the terminal's convention, not this one's. What did not exist is
    anything asserting *coverage*: ``mark_simulated`` is called from a handful of places in
    ``pages.py`` and nothing said every table was one of them. This is the test that would
    catch a seventh table added later without one, which is the only part of that story
    still worth writing code for.
    """
    from robot_army import db

    item_id = seed_item(conn, issue_number=7, dry_run=True, state="interrupted")
    seed_session(conn, item_id, state="lost")
    with db.transaction(conn):
        db.insert_card(
            conn,
            board_id="board-1",
            card_id="c1",
            card_url="https://trello.com/c/c1",
            title="A card",
            body="",
            dry_run=True,
        )

    unmarked = []
    for path in (
        "/interrupted",
        "/cards",
        f"/item/{item_id}",
        f"/item/{item_id}/confirm/abandon",
    ):
        if 'class="sim"' not in board_web.get(f"{path}?include_simulated=1").text:
            unmarked.append(path)
    assert not unmarked, f"these views show a simulated row without marking it: {unmarked}"


def test_the_queue_marks_its_simulated_rows(web, conn):
    """The reader most likely to be misled is the one who reads the first table and stops."""
    seed_item(conn, issue_number=7, dry_run=True, state="ready")
    assert 'class="sim"' in web.get("/queue?include_simulated=1").text


def test_a_mixed_table_marks_only_the_simulated_row(web, conn):
    """FR-020, and the case the badge exists for: 009 made mixed tables reachable, because
    rows now arrive unrequested rather than because the reader asked to see them."""
    seed_item(conn, issue_number=7, dry_run=True, state="ready")
    seed_item(conn, issue_number=8, dry_run=False, state="ready")
    body = web.get("/queue?include_simulated=1").text
    assert body.count('class="sim"') == 1
    rows = [line for line in body.split("<tr>") if "issue" in line or "item" in line]
    assert rows, "no rows rendered"


def test_the_level_pill_has_a_stylesheet_rule_in_both_states(web):
    """The defect this milestone fixes was a class with no rule at all: `.pill.level` fell
    back to the neutral base style, so "none of this is real" rendered in the same weight as
    "order: oldest-first". Nothing would have caught its return."""
    css = web.get("/static/app.css").body.decode()
    assert ".pill.level.simulated" in css
    assert ".pill.level.live" in css
    assert ".sim {" in css


def test_a_dead_end_page_states_no_visibility_preference(web_at, conn):
    """A 404 has no database context, so it cannot resolve the level-dependent default.

    Treating that absence as a stated `0` put `?include_simulated=0` on every nav link of
    every error page: on a `plan` instance one tap from a 404 pinned "hide everything" and
    landed the reader on exactly the empty-looking page this milestone removes.
    """
    body = web_at("plan").get("/no-such-page").text
    assert "include_simulated" not in body
    assert 'href="/active"' in body


def test_a_dead_end_page_offers_no_visibility_toggle(web_at, conn):
    """A toggle reporting a state it had to guess is worse than no toggle."""
    body = web_at("plan").get("/no-such-page").text
    assert "simulated rows" not in body


def test_a_refused_action_links_its_toggle_somewhere_reachable(web_at, conn):
    """The refusal page renders the chrome built for the POST it refused, and the toggle
    builds its href from that path — so it pointed at an action route with no GET handler,
    and clicking it answered 405. Reachable whenever an action is illegal."""
    import re

    item_id = seed_item(conn, issue_number=1, dry_run=True, state="done")
    response = web_at("plan").post(f"/item/{item_id}/abandon")
    assert response.status == 409
    href = re.search(r'<a href="([^"]*)" class="pill quiet">simulated', response.text)
    assert href, "the refusal page rendered no visibility toggle"
    assert "abandon" not in href.group(1), href.group(1)
    # And the target actually answers a GET.
    assert web_at("plan").get(href.group(1)).status == 200


# -- RA-14: one capacity observation per render ------------------------------


def test_the_queue_reads_one_observation_of_a_moving_machine(web, conn, monkeypatch):
    """The correctness half of "observe the machine once", not just the cost half.

    ``/queue`` used to take two snapshots moments apart — one for the chrome's pill, one for
    its own block — and two observations of a *moving* machine disagree. One page saying two
    different things about how many sessions are running is worse than either answer, so the
    stub here deliberately answers differently every time it is asked.
    """
    from robot_army import capacity as capacity_mod
    from robot_army.web import pages, server

    real = capacity_mod.snapshot
    seen = {"n": 0}

    def moving(*args, **kwargs):
        seen["n"] += 1
        snap = real(*args, **kwargs)
        return replace_total(snap, seen["n"])

    def replace_total(snap, total):
        import dataclasses

        return dataclasses.replace(snap, total=total)

    monkeypatch.setattr(pages.capacity_mod, "snapshot", moving)
    monkeypatch.setattr(server.capacity_mod, "snapshot", moving)

    seed_item(conn, state="ready")
    payload = web.get_json("/queue").json()

    assert seen["n"] == 1, "the machine was observed more than once in one render"
    assert payload["capacity"]["total"] == 1


def test_the_chrome_and_the_queue_view_still_work_without_a_snapshot_handed_in(
    config, conn, monkeypatch
):
    """The ``None`` default exists for direct callers — a test, or a future second entry
    point — so it is covered rather than assumed."""
    from tests.conftest import make_boundaries

    from robot_army import operations
    from robot_army.web import pages

    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log: make_boundaries(log, level=level)
    )
    ctx = operations.build_context(config)
    chrome = pages.chrome(ctx)
    assert chrome["capacity"]["global_cap"] >= 0

    view = pages.queue_view(ctx)
    assert "capacity" in view.data
    ctx.close()
