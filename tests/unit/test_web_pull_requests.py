"""How the interface renders a work item's pull requests (issue #143, contracts C4, C5).

The assertions worth having here are the ones about *absence*. A test that only checks a
link renders is not testing this feature: the point of it is that "there is no pull request"
and "nobody has asked GitHub" are different sentences on the page, and a version that
rendered both as an em dash would pass a happy-path test while telling the maintainer
something untrue.
"""

from __future__ import annotations

import pytest
from tests.conftest import seed_item, seed_session

from robot_army import db, operations
from robot_army.boundaries import TransportError

PR_144 = '[{"number":144,"url":"https://github.com/x/demo/pull/144","state":"merged"}]'
TWO_PRS = (
    '[{"number":140,"url":"https://github.com/x/demo/pull/140","state":"closed"},'
    '{"number":144,"url":"https://github.com/x/demo/pull/144","state":"open"}]'
)


def build(
    conn,
    *,
    state="active",
    stored=None,
    at="2026-09-05T22:00:00Z",
    session=None,
    issue_number=42,
):
    item_id = seed_item(conn, state=state, issue_number=issue_number)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item_id, worktree_path="/w/demo/issue-42", branch="robot-army/42"
        )
        if stored is not None:
            db.record_pull_requests(conn, item_id, found=stored, at=at)
    if session:
        seed_session(conn, item_id, state=session)
    return item_id


# -- the item page (FR-013) -------------------------------------------------


def test_the_item_page_links_a_known_pull_request_with_its_state(web, conn):
    item_id = build(conn, stored=PR_144)

    body = web.get(f"/item/{item_id}").body.decode()

    assert 'href="https://github.com/x/demo/pull/144"' in body
    assert "#144 (merged)" in body


def test_the_item_page_says_none_when_github_answered_and_there_are_none(web, conn):
    item_id = build(conn, stored="[]")

    body = web.get(f"/item/{item_id}").body.decode()

    assert ">none<" in body
    assert "not checked" not in body


def test_the_item_page_says_not_checked_when_nobody_has_asked(web, conn):
    """The third state, and the reason the column is nullable. Rendering this as "none"
    would put a confident "there is no pull request" on the page, earned by never having
    asked — which is the failure the whole feature exists to prevent."""
    item_id = build(conn)

    body = web.get(f"/item/{item_id}").body.decode()

    assert "not checked" in body
    assert ">none<" not in body


def test_the_item_page_lists_every_known_pull_request(web, conn):
    """FR-013. The compressed listing cell shows one; the item's own page has room, so it
    shows them all — which is what makes the ``+N`` there a deferral rather than a loss."""
    item_id = build(conn, stored=TWO_PRS)

    body = web.get(f"/item/{item_id}").body.decode()

    assert "#140 (closed)" in body
    assert "#144 (open)" in body


def test_the_item_page_says_when_the_answer_was_confirmed(web, conn):
    item_id = build(conn, stored=PR_144, at="2026-09-05T22:00:00Z")

    body = web.get(f"/item/{item_id}").body.decode()

    assert "confirmed" in body


def test_the_item_payload_carries_what_the_html_shows(web, conn):
    """FR-017. One renderer over one payload is what stops the JSON and the HTML drifting,
    so the three keys have to be in the payload under the names the page reads."""
    item_id = build(conn, stored=PR_144)

    item = web.get_json(f"/item/{item_id}").json()["item"]

    assert item["pull_requests"] == [
        {"number": 144, "url": "https://github.com/x/demo/pull/144", "state": "merged"}
    ]
    assert item["pull_requests_at"] == "2026-09-05T22:00:00Z"
    assert item["pull_requests_known"] is True


def test_a_pull_request_url_that_is_not_githubs_renders_as_text(web, conn):
    """Every outbound href goes through the one gate, including this one. The URL comes from
    the GitHub API rather than from a user, so this should be unreachable — which is exactly
    when a fallback earns its place."""
    item_id = build(
        conn,
        stored='[{"number":9,"url":"https://evil.example/pull/9","state":"open"}]',
    )

    body = web.get(f"/item/{item_id}").body.decode()

    assert "evil.example" not in body
    assert "#9 (open)" in body


# -- the listings (FR-014, FR-015) ------------------------------------------


def test_the_active_listing_links_a_row_that_has_a_pull_request(web, conn):
    build(conn, stored=PR_144, session="running")

    body = web.get("/active").body.decode()

    assert "<th>PR</th>" in body
    assert 'href="https://github.com/x/demo/pull/144"' in body


def test_the_active_listing_shows_the_latest_of_several_and_says_how_many_more(web, conn):
    """FR-015. Numbers are issued in the order pull requests were opened, so the highest is
    the current attempt — the one that represents the item's outcome when a first was closed
    and a second opened."""
    build(conn, stored=TWO_PRS, session="running")

    body = web.get("/active").body.decode()

    assert "#144 (open)" in body
    assert "+1" in body


def test_the_active_listing_distinguishes_none_from_never_checked(web, conn):
    none_found = build(conn, stored="[]", session="running")
    never_asked = build(conn, issue_number=43, session="running")

    rows = web.get_json("/active").json()["items"]

    by_id = {row["id"]: row for row in rows}
    assert by_id[none_found]["pull_requests_known"] is True
    assert by_id[never_asked]["pull_requests_known"] is False


def test_the_queue_has_no_pull_request_column(web, conn):
    """SC-007. Every ``ready`` row is work that has never been dispatched, so it has no
    branch and can have no pull request. The column would be empty on every row of every
    render, forever, on the page with the most rows on it."""
    seed_item(conn, state="ready")

    body = web.get("/queue").body.decode()

    assert "<th>PR</th>" not in body


# -- the resume decision (FR-019, C5) ---------------------------------------


def test_the_interrupted_card_shows_the_stored_pull_requests(web, conn):
    build(conn, state="interrupted", stored=PR_144, session="lost")

    body = web.get("/interrupted").body.decode()

    assert "pull requests" in body
    assert 'href="https://github.com/x/demo/pull/144"' in body


def test_the_interrupted_card_reports_the_confirmation_age_not_a_cache_age(web, conn):
    """A different kind of age from the two beside it: those are cache windows, this is when
    the daemon last confirmed the answer, so it gets its own footnote rather than a share of
    theirs."""
    build(conn, state="interrupted", stored=PR_144, session="lost")

    body = web.get("/interrupted").body.decode()

    assert "pull requests confirmed" in body


def test_a_never_checked_item_says_so_in_the_signals_block(web, conn):
    build(conn, state="interrupted", session="lost")

    body = web.get("/interrupted").body.decode()

    assert "pull requests never checked" in body


# -- no page renders through GitHub (SC-004) --------------------------------


@pytest.mark.parametrize("path", ["/active", "/queue", "/interrupted"])
def test_every_listing_renders_with_github_unreachable(web, conn, path):
    """SC-004, directly. Before this feature the resume-decision block asked GitHub for a
    pull request *while the page rendered*; now nothing does, so a page's content cannot
    depend on GitHub being up."""
    build(conn, state="interrupted", stored=PR_144, session="lost")
    web.reader.raise_on_remote = TransportError("GitHub is unreachable")

    response = web.get(path)

    assert response.status == 200
    assert web.reader.pr_calls == []


def test_the_item_page_renders_with_github_unreachable(web, conn):
    item_id = build(conn, state="interrupted", stored=PR_144, session="lost")
    web.reader.raise_on_remote = TransportError("GitHub is unreachable")

    response = web.get(f"/item/{item_id}")

    assert response.status == 200
    assert "#144 (merged)" in response.body.decode(), (
        "the stored answer must still be shown when GitHub cannot be reached"
    )
    assert web.reader.pr_calls == []


def test_the_view_helper_makes_no_call_of_any_kind(conn):
    """The property C5 states, at the one function every surface goes through."""
    item_id = build(conn, stored=PR_144)
    row = db.get_work_item(conn, item_id)

    view = operations.pull_request_view(row)

    assert view["pull_requests_known"] is True
    assert view["pull_requests"][0]["number"] == 144
