"""Issue #182: the chrome says how much work is parked on the author, and stops saying less.

The bar used to name the effect level, the daemon, the capacity, the order and the anomaly
count, and carried no work-item information at all. Exiting a session sent the item to
``awaiting_review`` — off ``/active``, never on ``/queue`` — and no number on any page
noticed. ``robot-army status`` has printed counts by state since it existed, so this was the
web losing a fact the terminal always had.

The count covers the three states that mean the same operational thing: the work is parked
and the machine will not move it without a decision. One number, because the question is
singular.

Two things this file guards that the renderer alone cannot:

**The count and its destination must agree.** A pill promising three and a page listing two is
worse than no pill, and the failure mode is silent — it only appears when the visibility
toggle is off, or when a state is counted that the page does not list. ``test_the_pill_and_its
_page_agree_under_both_settings`` asserts it as one fact rather than as two halves that could
each pass while disagreeing.

**A count nobody took is not zero.** ``server._bare`` renders 404s and refusals with no
database behind them. It omits the key and the pill goes, rather than reporting a zero it
never counted.
"""

from __future__ import annotations

import re

import pytest
from tests.conftest import seed_item

WAITING_STATES = ("awaiting_review", "interrupted", "failed")

#: Everything the bar can render, so "the quiet bar is only these" can be asserted as an
#: absence of the others rather than as a count of substrings that might both be present.
PILL = re.compile(r'class="pill[^"]*">([^<]*)<')


def bar(body: str) -> str:
    """Just the chrome bar.

    The nav now carries the words ``needs me`` too, which is the point of renaming it — so a
    bare substring search over the document can no longer tell the pill from the link to the
    page it points at.
    """
    start = body.index('<div class="chrome">')
    return body[start : body.index("</div>", start)]


def pills(body: str) -> list[str]:
    return PILL.findall(bar(body))


# -- the count itself --------------------------------------------------------


@pytest.mark.parametrize("state", WAITING_STATES)
def test_each_parked_state_is_counted(web, conn, state: str) -> None:
    """All three, not only the one the issue was reported from. ``failed`` is in it
    deliberately: ``retry`` and ``reset`` are routes out that only a person takes."""
    seed_item(conn, issue_number=1, state=state)
    assert web.get_json("/active").json()["waiting_count"] == 1


def test_the_three_states_are_summed_into_one_number(web, conn) -> None:
    """One pill rather than three. The question — is anything waiting on me? — is singular,
    and a number per state would be three answers to it."""
    for number, state in enumerate(WAITING_STATES, start=1):
        seed_item(conn, issue_number=number, state=state)
    assert web.get_json("/active").json()["waiting_count"] == 3


@pytest.mark.parametrize("state", ["ready", "dispatching", "active", "done", "abandoned"])
def test_states_the_machine_owns_are_not_counted(web, conn, state: str) -> None:
    """``ready``, ``dispatching`` and ``active`` are the machine's to move, and ``/queue``
    already says why it has not. ``done`` and ``abandoned`` are terminal: nothing is waiting
    because there is no decision left."""
    seed_item(conn, issue_number=1, state=state)
    assert web.get_json("/active").json()["waiting_count"] == 0


def test_the_count_is_on_every_view(web, conn) -> None:
    """Chrome, not a status page — the fact is wanted from wherever the author is looking."""
    seed_item(conn, issue_number=1, state="awaiting_review")
    for path in ("/active", "/queue", "/interrupted", "/anomalies", "/log"):
        assert web.get_json(path).json()["waiting_count"] == 1, path
        assert "1 needs me" in web.get(path).text, path


# -- the pill ----------------------------------------------------------------


def test_zero_renders_quiet_and_stays_on_screen(web, conn) -> None:
    """Unlike the two pills this issue removes. A count at zero answers a question the
    author is asking; ``effect level: live`` answers one nobody asked."""
    body = web.get("/active").text
    assert '<a href="/interrupted?include_simulated=0" class="pill quiet">0 need me</a>' in body


def test_one_alarms_and_inflects(web, conn) -> None:
    seed_item(conn, issue_number=1, state="awaiting_review")
    body = web.get("/active").text
    assert 'class="pill warn">1 needs me</a>' in body


def test_many_alarm_and_inflect(web, conn) -> None:
    for number, state in enumerate(WAITING_STATES, start=1):
        seed_item(conn, issue_number=number, state=state)
    assert 'class="pill warn">3 need me</a>' in web.get("/active").text


def test_the_pill_carries_the_visibility_setting_through(web_at, conn) -> None:
    """Like every other generated link (009 FR-003). A pill that dropped the preference
    would land the reader on a page counting something other than what the pill counted."""
    harness = web_at("plan")
    assert '"/interrupted?include_simulated=1"' in harness.get("/active").text
    assert '"/interrupted?include_simulated=0"' in harness.get(
        "/active?include_simulated=0"
    ).text


def test_the_pill_sits_beside_the_anomaly_count(web, conn) -> None:
    """The two counts adjacent is what makes "the same shape as the anomaly one" legible on
    the page rather than a claim in a commit message."""
    rendered = pills(web.get("/active").text)
    assert rendered.index("0 need me") == rendered.index("0 anomalies") - 1


# -- the count and the page it points at -------------------------------------


@pytest.mark.parametrize("stated", ["0", "1"])
def test_the_pill_and_its_page_agree_under_both_settings(web_at, conn, stated: str) -> None:
    """**The property the whole feature rests on.**

    Asserted end to end rather than on the count and the listing separately, because the two
    can each be individually correct and still disagree — which is one surface printing two
    numbers, the defect the scoping exists to prevent.
    """
    seed_item(conn, issue_number=1, state="awaiting_review")
    seed_item(conn, issue_number=2, state="interrupted")
    seed_item(conn, issue_number=3, state="failed")
    seed_item(conn, issue_number=4, dry_run=True, state="failed")
    harness = web_at("plan")

    counted = harness.get_json(f"/active?include_simulated={stated}").json()["waiting_count"]
    listed = harness.get_json(f"/interrupted?include_simulated={stated}").json()
    shown = len(listed["items"]) + len(listed["awaiting_review"]) + len(listed["failed"])

    assert counted == shown
    assert counted == (4 if stated == "1" else 3), "the simulated row must move the count"


def test_the_simulated_row_is_counted_only_when_it_is_shown(web_at, conn) -> None:
    """The reason recorded for the anomaly count, in a second place: an unscoped count
    disagrees with the page it links to the moment the toggle is off."""
    seed_item(conn, issue_number=1, dry_run=True, state="interrupted")
    harness = web_at("plan")
    assert harness.get_json("/active?include_simulated=1").json()["waiting_count"] == 1
    assert harness.get_json("/active?include_simulated=0").json()["waiting_count"] == 0


# -- no database, no number --------------------------------------------------


def test_a_dead_end_page_renders_no_pill(web, conn) -> None:
    """``server._bare`` has no ``Context`` — "a 503 that cannot render is not a 503" — so it
    counted nothing. A number nobody took must not be rendered as a number."""
    assert "need me" not in bar(web.get("/no-such-page").text)


# -- what a quiet bar comes down to ------------------------------------------


def test_the_quiet_bar_is_only_what_there_is_to_know(web, conn) -> None:
    """The readability claim the whole issue rests on.

    At ``live``, healthy, unpaused, with nothing waiting and nothing anomalous, every pill
    left is either a fact about the machine or a count answering a question. The two that
    went were the two that only ever stated the value that holds unless you went out of your
    way to change it.
    """
    body = web.get("/active").text
    rendered = pills(body)

    assert rendered == [
        "DAEMON NOT RUNNING",
        f"0/{web.app.config.daemon.max_concurrent_sessions} sessions (0 ours, 0 other)",
        "order: oldest-first",
        "0 need me",
        "0 anomalies",
    ]
    assert "effect level" not in body
    assert "simulated rows" not in body
    assert "DISPATCH PAUSED" not in body
