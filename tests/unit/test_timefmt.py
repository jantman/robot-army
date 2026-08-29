"""The one conversion in the system, and the edges it must not fall off.

Milestone 010. ``timefmt.local`` is the only producer of a displayed timestamp, so every
row of contracts/time-display.md §1 is pinned here rather than left to the sites that call
it. The failure paths matter more than the success ones: this function is reached while
rendering a page or a line of terminal output, where raising is never a useful answer.
"""

from __future__ import annotations

import pytest

from robot_army import timefmt

#: Chosen so the local rendering lands on a *different calendar day* in New York, which is
#: the clearest possible evidence that a conversion actually happened.
SUMMER = "2026-08-30T01:31:07Z"
WINTER = "2026-01-15T01:31:07Z"


@pytest.mark.parametrize("in_timezone", ["America/New_York"], indirect=True)
def test_a_stored_instant_reads_in_the_hosts_zone(in_timezone):
    """The feature, in one assertion: the 30th in UTC is the 29th in New York."""
    assert timefmt.local(SUMMER) == "2026-08-29 21:31:07 -04:00"


@pytest.mark.parametrize("in_timezone", ["America/New_York"], indirect=True)
def test_the_offset_is_resolved_for_the_instant_not_for_today(in_timezone):
    """A January stamp carries January's offset even when read in August.

    ``astimezone()`` asks per instant rather than per process. Anything that cached one
    offset at import time would get this wrong for half the year.
    """
    assert timefmt.local(WINTER) == "2026-01-14 20:31:07 -05:00"


@pytest.mark.parametrize("in_timezone", ["Asia/Kolkata"], indirect=True)
def test_a_half_hour_offset_renders_correctly(in_timezone):
    """+05:30. A format that assumed whole hours, or four offset characters, fails here."""
    assert timefmt.local(SUMMER) == "2026-08-30 07:01:07 +05:30"


@pytest.mark.parametrize("in_timezone", ["UTC"], indirect=True)
def test_a_utc_host_still_states_its_zone(in_timezone):
    """The value is unchanged but the label is not optional (FR-003).

    A displayed time with no zone cannot be reconciled against a stored record by a reader
    who does not already know where the machine stands.
    """
    assert timefmt.local(SUMMER) == "2026-08-30 01:31:07 +00:00"


@pytest.mark.parametrize("in_timezone", ["Bogus/Nowhere"], indirect=True)
def test_an_unresolvable_zone_falls_back_to_utc_and_says_so(in_timezone):
    """FR-009, obtained by writing nothing.

    The C library resolves an unknown zone to UTC, so there is no branch to take and no
    exception to catch. ``+00:00`` is the honest statement that the zone is unknown rather
    than a silent pretence that the machine is in UTC.
    """
    assert timefmt.local(SUMMER) == "2026-08-30 01:31:07 +00:00"


@pytest.mark.parametrize("in_timezone", ["America/New_York"], indirect=True)
def test_the_daylight_saving_fold_is_told_apart_only_by_the_offset(in_timezone):
    """The evidence that the offset belongs on every stamp rather than once per page.

    Two instants an hour apart render to the same wall clock. Had the design stated the
    zone once in a page footer and printed bare local times, these two events would have
    been indistinguishable for one hour every autumn — SC-007 failing silently, and only
    ever noticed by someone reconstructing an incident.
    """
    earlier = timefmt.local("2026-11-01T05:00:00Z")
    later = timefmt.local("2026-11-01T06:00:00Z")

    assert earlier == "2026-11-01 01:00:00 -04:00"
    assert later == "2026-11-01 01:00:00 -05:00"
    assert earlier != later
    assert earlier[:19] == later[:19], "the wall clocks really are identical"


@pytest.mark.parametrize("in_timezone", ["America/New_York"], indirect=True)
def test_the_spring_gap_has_exactly_one_rendering(in_timezone):
    """Conversion runs one way, so a skipped local hour is not a problem to solve.

    2026-03-08 07:00Z is 02:00 EST, an hour that local wall-clock time skips. Going
    UTC → local there is unambiguous; only the reverse direction is not, and this module
    never goes in the reverse direction.
    """
    assert timefmt.local("2026-03-08T07:00:00Z") == "2026-03-08 03:00:00 -04:00"


@pytest.mark.parametrize(
    "value",
    [None, "", "not a timestamp", "2026-08-30T01:31:07", "2026-08-30", "0", "Z"],
)
def test_anything_that_is_not_a_stored_stamp_passes_straight_through(value):
    """FR-015 and FR-016. A rendering layer must not be what hides a corrupt row.

    Returning the input verbatim rather than a placeholder is deliberate: a value the
    database should not have contained has to reach the screen as the value it is.
    """
    assert timefmt.local(value) == value


def test_local_never_raises():
    """It is called mid-render. There is no useful response to an exception there."""
    for value in (None, "", "x" * 500, "2026-13-45T99:99:99Z", "\x00", "2026-08-30T01:31:07Z"):
        timefmt.local(value)


@pytest.mark.parametrize("in_timezone", ["America/New_York"], indirect=True)
def test_local_returns_none_only_for_none(in_timezone):
    """So a caller may interpolate the result directly without a second guard."""
    assert timefmt.local(None) is None
    for value in ("", "junk", SUMMER):
        assert timefmt.local(value) is not None


def test_parse_stamp_reads_the_stored_format_and_refuses_everything_else():
    parsed = timefmt.parse_stamp(SUMMER)
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert parsed.strftime(timefmt.STORED) == SUMMER

    for value in (None, "", "not a timestamp", "2026-08-30T01:31:07", "2026-08-30"):
        assert timefmt.parse_stamp(value) is None


@pytest.mark.parametrize("in_timezone", ["Asia/Kolkata"], indirect=True)
def test_parse_stamp_is_indifferent_to_the_host_zone(in_timezone):
    """It reads a record. Only :func:`local` is allowed to care where the reader is."""
    assert timefmt.parse_stamp(SUMMER) == timefmt.parse_stamp(SUMMER)
    assert timefmt.parse_stamp(SUMMER).strftime(timefmt.STORED) == SUMMER


@pytest.mark.parametrize("in_timezone", ["America/New_York"], indirect=True)
def test_the_displayed_form_is_never_mistakable_for_the_stored_one(in_timezone):
    """The ``T`` is gone and the ``Z`` is gone, on purpose (research R8)."""
    rendered = timefmt.local(SUMMER)
    assert "T" not in rendered
    assert not rendered.endswith("Z")
    assert timefmt.parse_stamp(rendered) is None, "a rendering must not parse as a record"
