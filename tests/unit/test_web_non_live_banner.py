"""Milestone 009: every page below ``live`` says so, and says what it means.

The polarity is the reason these tests read the way they do: the alarm goes on **non-live**,
and ``live`` gets nothing at all. ``live`` is the state the system is meant to run in and the
one the operator expects. Every level below it is a testing configuration, which is the
surprising state — and the one where every value on the page means something other than what
it appears to mean.

Issue #182 took "nothing at all" one step further. 009 rendered a calm pill at ``live``, on
the argument that decorating the expected state would train the operator to ignore the one
place the level is shown. That is an argument against *alarming*, and it survives; it did not
reach whether a calm pill belongs on screen. The rest of the bar had already answered — pause
pill, effect-mismatch banner, cap-disagreement note and consequences banner are each absent
when silent — so the pill is now absent at ``live`` too, and its absence reads as ``live`` by
the convention the bar teaches.

``unknown`` keeps its pill, in both places it arises. That is the one thing these tests exist
to hold: "we could not tell" is not the default state, it is news, and the condition that
hides the pill is inequality with ``live`` rather than membership of :data:`BELOW_LIVE`.
"""

from __future__ import annotations

import pytest
from tests.conftest import beat, seed_item

from robot_army.effects import REAL_AT, SIMULATED_CONSEQUENCES, EffectLevel, consequences

BELOW_LIVE = ("plan", "local", "no-remote")
VIEWS = ("/active", "/queue", "/interrupted", "/cards", "/anomalies", "/log")

BANNER_OPENING = "This instance is set up for testing, not real work."


# -- the banner --------------------------------------------------------------


@pytest.mark.parametrize("level", BELOW_LIVE)
@pytest.mark.parametrize("path", VIEWS)
def test_the_banner_is_on_every_view_below_live(web_at, conn, level: str, path: str) -> None:
    assert BANNER_OPENING in web_at(level).get(path).text


@pytest.mark.parametrize("path", VIEWS)
def test_the_banner_is_on_no_view_at_live(web_at, conn, path: str) -> None:
    """FR-014, and the half that keeps the other half meaningful."""
    assert BANNER_OPENING not in web_at("live").get(path).text


@pytest.mark.parametrize("level", BELOW_LIVE)
def test_the_banner_names_the_level(web_at, conn, level: str) -> None:
    assert f"At effect level {level}," in web_at(level).get("/active").text


@pytest.mark.parametrize("level", BELOW_LIVE)
def test_the_banner_states_exactly_the_consequences_that_hold(web_at, conn, level: str) -> None:
    """FR-013, driven from ``REAL_AT`` rather than from a hardcoded expected string.

    Written this way on purpose: an expected-string test would pass just as happily if the
    banner and the boundary table drifted apart in the same direction, which is the failure
    the derivation exists to prevent.
    """
    body = web_at(level).get("/active").text
    for name, phrase in SIMULATED_CONSEQUENCES.items():
        simulated_here = EffectLevel(level) not in REAL_AT[name]
        assert (phrase in body) is simulated_here, (level, name)


def test_the_prose_around_the_list_is_derived_too(web_at, conn) -> None:
    """FR-013 applies to the whole banner, not only to its bullet list.

    Getting the list right and leaving the sentences around it fixed is the same defect one
    layer quieter: at ``local`` branches and commits are really created, and at ``no-remote``
    a real session runs in a real terminal, so "nothing on this page happened" and "nothing
    reached a terminal" were both false — stated confidently, on every page, in the banner
    whose entire job is to be believed.
    """
    absolute = "nothing on this page really happened"
    partial = "parts of what these rows describe did not really happen"

    plan = web_at("plan").get("/active").text
    assert absolute in plan
    assert "Nothing here reached your repositories, GitHub, Trello, or a terminal." in plan

    for level in ("local", "no-remote"):
        body = web_at(level).get("/active").text
        assert absolute not in body, f"{level} claims nothing happened"
        assert partial in body
        assert "anything not listed was really carried out" in body


def test_no_banner_claims_absence_of_an_effect_that_is_real(web_at, conn) -> None:
    """The mechanical form of the same rule, so a reworded banner cannot reintroduce it.

    Every phrase in the table is a statement that something did *not* happen. A level at
    which that boundary is real must not carry the phrase — which the bullet list already
    guarantees, and which this asserts of the rendered page as a whole, prose included.
    """
    for level in BELOW_LIVE:
        body = web_at(level).get("/active").text
        banner = body[body.index("This instance is set up") :]
        banner = banner[: banner.index("</div></div>")]
        for name, phrase in SIMULATED_CONSEQUENCES.items():
            if EffectLevel(level) in REAL_AT[name]:
                assert phrase not in banner, (level, name)


def test_the_consequences_shrink_as_the_level_rises(web_at, conn) -> None:
    """A single message reused below ``live`` would pass every test above but this one."""
    counts = [len(consequences(EffectLevel(level))) for level in (*BELOW_LIVE, "live")]
    assert counts == sorted(counts, reverse=True)
    assert len(set(counts)) == len(counts), "two levels state the same set of consequences"
    assert counts[-1] == 0


def test_the_invented_issue_numbers_are_named(web_at, conn) -> None:
    """The issue's own example: ``#900001`` on screen, looking like a real issue link."""
    seed_item(conn, issue_number=900001, dry_run=True, state="ready")
    assert "the issue numbers shown are invented" in web_at("plan").get("/queue").text


# -- the pill ----------------------------------------------------------------


@pytest.mark.parametrize("level", BELOW_LIVE)
def test_the_pill_alarms_below_live(web_at, conn, level: str) -> None:
    body = web_at(level).get("/active").text
    assert f'class="pill level simulated">effect level: {level} — simulated' in body


def test_there_is_no_pill_at_live(web_at, conn) -> None:
    """Issue #182, reversing 009 FR-017's calm pill. Nothing at all, not a quiet something.

    The "simulated" half of the original assertion stands unchanged and is the half that
    always mattered: the word must not appear on a live bar in any form.
    """
    body = web_at("live").get("/active").text
    assert "effect level" not in body
    assert "pill level simulated" not in body


def test_an_unreadable_level_keeps_its_pill(web_at, conn, layout) -> None:
    """A daemon holds the lock and its level cannot be read, so the level resolves to
    ``unknown``. That is not ``live`` and must not be rendered as if it were — the reader
    would take the absence for a live instance, which is the one misreading that costs."""
    from robot_army.daemon import SingleInstanceLock

    with SingleInstanceLock(layout.lock_path):
        beat(layout, effect_level=None)
        body = web_at("live").get("/active").text
    assert "effect level: unknown" in body


def test_a_dead_end_page_keeps_its_pill(web_at, conn) -> None:
    """``server._bare`` renders 404s with no database and sets the level to ``unknown`` for
    the same honest reason. Same rule, same pill."""
    assert "effect level: unknown" in web_at("live").get("/no-such-page").text


# -- one rule, so the two cannot disagree ------------------------------------


def test_a_live_interface_in_front_of_a_plan_daemon_still_alarms(web_at, conn, layout) -> None:
    """The edge case that rules out using this interface's own level alone.

    The rows on the page were written by the daemon, at the daemon's level. An interface
    configured for ``live`` would otherwise render a calm pill above a table of issue numbers
    that do not exist — the page claiming to be real about work that is not.
    """
    from robot_army.daemon import SingleInstanceLock

    with SingleInstanceLock(layout.lock_path):
        beat(layout, effect_level="plan")
        body = web_at("live").get("/active").text
    assert "pill level simulated" in body
    assert BANNER_OPENING in body


def test_a_plan_interface_in_front_of_a_live_daemon_still_alarms(web_at, conn, layout) -> None:
    """The other direction: the more simulated of the two wins either way."""
    from robot_army.daemon import SingleInstanceLock

    with SingleInstanceLock(layout.lock_path):
        beat(layout, effect_level="live")
        body = web_at("plan").get("/active").text
    assert "pill level simulated" in body


def test_an_unreadable_daemon_level_alarms_but_adds_no_second_banner(web_at, conn, layout) -> None:
    """The existing ``EFFECT LEVEL UNKNOWN`` banner says more than this one could.

    Two banners describing one situation is the same defect this milestone removes from the
    interface, one layer up.
    """
    from robot_army.daemon import SingleInstanceLock

    layout.heartbeat_path.unlink(missing_ok=True)
    with SingleInstanceLock(layout.lock_path):
        body = web_at("live").get("/active").text
    assert "EFFECT LEVEL UNKNOWN" in body
    assert 'class="pill level simulated">effect level: unknown — simulated' in body
    assert BANNER_OPENING not in body


def test_no_daemon_means_our_own_level_decides(web_at, conn) -> None:
    """Nothing to disagree with, so the configured level stands alone."""
    assert BANNER_OPENING not in web_at("live").get("/active").text
    assert BANNER_OPENING in web_at("plan").get("/active").text


# -- it lives beside the banners it belongs with -----------------------------


def test_every_banner_renders_together_and_none_suppresses_another(web_at, conn, layout) -> None:
    """FR-015. Three conditions, all true at once, all reported."""
    from robot_army.daemon import SingleInstanceLock

    with SingleInstanceLock(layout.lock_path):
        beat(layout, effect_level="live")
        body = web_at("plan").post("/dispatch/pause").body.decode()
        body = web_at("plan").get("/queue?msg=paused").text

    assert "EFFECT LEVEL MISMATCH" in body
    assert BANNER_OPENING in body
    assert "Dispatch paused" in body


def test_the_banner_survives_a_daemon_that_is_not_running(web_at, conn) -> None:
    body = web_at("plan").get("/active").text
    assert "The daemon is not running." in body
    assert BANNER_OPENING in body
