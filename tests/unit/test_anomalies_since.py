"""``anomalies --since`` narrows the listing to a window (milestone 012).

Two halves, matching the spec's two stories. The first is the filter itself: what it
selects, where its boundary sits, and how it refuses a duration it cannot read. The second
is the guard — that the command with no ``--since`` is the command it was before, because a
filter on an anomaly listing is a way to *miss* an anomaly unless someone checks that it is
not (US2).

Detection times are pinned by ``UPDATE`` rather than passed in: ``db.raise_anomaly`` stamps
``utcnow()`` itself, and giving it a parameter to override that would add a production knob
whose only caller is a test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.conftest import make_boundaries

from robot_army import db, operations

#: Distinct on ``(kind, entity_type, entity_id)`` — the partial unique index over
#: unacknowledged rows would otherwise collapse two seeds into one.
_KINDS = ("orphan_session", "no_transcript", "stale_socket", "prunable_worktree")


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


def _stamp(delta: timedelta) -> str:
    """A stored detection time ``delta`` in the past, in the one format the database uses."""
    return (datetime.now(UTC) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed(conn, *, ago: timedelta | None = None, detected_at: str | None = None,
         acknowledged: bool = False) -> int:
    """One anomaly at a known detection time. Returns its id."""
    nth = int(conn.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0])
    with db.transaction(conn):
        db.raise_anomaly(
            conn,
            kind=_KINDS[nth % len(_KINDS)],
            detail={"note": "seeded"},
            entity_type="work_item",
            entity_id=str(nth),
        )
        anomaly_id = int(conn.execute("SELECT MAX(id) FROM anomalies").fetchone()[0])
        conn.execute(
            "UPDATE anomalies SET detected_at = ? WHERE id = ?",
            (detected_at if detected_at is not None else _stamp(ago or timedelta()), anomaly_id),
        )
        if acknowledged:
            conn.execute(
                "UPDATE anomalies SET acknowledged_at = ? WHERE id = ?",
                ("2026-01-01T00:00:00Z", anomaly_id),
            )
    return anomaly_id


def _ids(result) -> list[int]:
    return [row["id"] for row in result.data["anomalies"]]


def _text(result) -> str:
    return "\n".join(result.lines)


# -- US1: what the window selects -------------------------------------------


def test_the_window_selects_only_recent_detections(ctx, conn):
    """Spec US1 scenarios 1 and 2: 10 minutes, 3 hours and 2 days old."""
    recent = seed(conn, ago=timedelta(minutes=10))
    middle = seed(conn, ago=timedelta(hours=3))
    ancient = seed(conn, ago=timedelta(days=2))

    assert _ids(operations.anomalies(ctx, since="1h")) == [recent]
    assert _ids(operations.anomalies(ctx, since="1d")) == [recent, middle]
    assert _ids(operations.anomalies(ctx, since="30d")) == [recent, middle, ancient]


def test_no_since_lists_everything(ctx, conn):
    """The default is inert — the filter is opt-in (FR-004)."""
    ids = {seed(conn, ago=timedelta(minutes=10)), seed(conn, ago=timedelta(days=2))}

    assert set(_ids(operations.anomalies(ctx))) == ids


def test_the_boundary_instant_is_inside_the_window():
    """Inclusive, matching ``log --since`` (research R3).

    Asserted against the predicate with an explicit cutoff rather than through the
    command, because a window derived from ``datetime.now`` at call time cannot pin a
    stored stamp to the boundary at one-second resolution — the test would be a coin toss
    about which side of the second the two calls landed on.
    """
    cutoff = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)

    assert operations._within_window("2026-08-30T12:00:00Z", cutoff) is True
    assert operations._within_window("2026-08-30T12:00:01Z", cutoff) is True
    assert operations._within_window("2026-08-30T11:59:59Z", cutoff) is False


def test_no_cutoff_keeps_every_row(ctx, conn):
    assert operations._within_window("2020-01-01T00:00:00Z", None) is True


def test_ordering_is_untouched_by_the_filter(ctx, conn):
    """Filtering removes rows; it never reorders them (data-model.md)."""
    old = seed(conn, ago=timedelta(minutes=50))
    new = seed(conn, ago=timedelta(minutes=5))
    middle = seed(conn, ago=timedelta(minutes=30))

    assert _ids(operations.anomalies(ctx, since="1h")) == [new, middle, old]


# -- US1: refusing a duration it cannot read --------------------------------


@pytest.mark.parametrize("bad", ["2 weeks", "1.5h", "-5m", "10 fortnights", "abc"])
def test_a_malformed_duration_is_a_usage_error_that_lists_nothing(ctx, conn, bad):
    seed(conn, ago=timedelta(minutes=1))

    result = operations.anomalies(ctx, since=bad)

    assert result.code == operations.EXIT_USAGE
    assert result.data.get("anomalies") is None
    assert result.lines


@pytest.mark.parametrize("bad", ["2 weeks", "1.5h", "-5m", "10 fortnights", "abc"])
def test_it_rejects_exactly_what_the_log_rejects_and_says_the_same_thing(ctx, conn, bad):
    """FR-002 is a claim about *sameness*, so assert against ``read_log`` itself."""
    from_anomalies = operations.anomalies(ctx, since=bad)
    from_log = operations.read_log(ctx, since=bad)

    assert from_anomalies.code == from_log.code == operations.EXIT_USAGE
    assert from_anomalies.lines == from_log.lines


def test_an_empty_since_means_no_window_here_because_it_does_for_the_log(ctx, conn):
    """FR-002 is a claim about *sameness*, and this is the edge where it bites: the
    duration parser rejects an empty string, but ``read_log`` never hands it one — it
    treats an empty ``--since`` as an absent one. Diverging here would be inventing a
    difference between two commands the maintainer reads side by side."""
    anomaly_id = seed(conn, ago=timedelta(days=9))

    result = operations.anomalies(ctx, since="")

    assert result.code == operations.read_log(ctx, since="").code == operations.EXIT_OK
    assert _ids(result) == [anomaly_id]


@pytest.mark.parametrize("good", ["30s", "10m", "2h", "1d"])
def test_it_accepts_every_unit_the_log_accepts(ctx, conn, good):
    seed(conn, ago=timedelta(seconds=1))

    result = operations.anomalies(ctx, since=good)

    assert result.code == operations.EXIT_OK
    assert len(result.data["anomalies"]) == 1


# -- US1: composition with the flags already there --------------------------


def test_since_narrows_whatever_all_selected(ctx, conn):
    """FR-005: ``--all`` decides eligibility, ``--since`` decides the window."""
    open_recent = seed(conn, ago=timedelta(minutes=10))
    acked_recent = seed(conn, ago=timedelta(minutes=10), acknowledged=True)
    acked_old = seed(conn, ago=timedelta(days=2), acknowledged=True)

    assert _ids(operations.anomalies(ctx, since="1h")) == [open_recent]

    both = _ids(operations.anomalies(ctx, since="1h", show_all=True))
    assert set(both) == {open_recent, acked_recent}
    assert acked_old not in both


def test_the_json_payload_holds_exactly_what_was_printed(ctx, conn):
    """FR-008: the two views cannot disagree about the window."""
    recent = seed(conn, ago=timedelta(minutes=10))
    seed(conn, ago=timedelta(days=2))

    result = operations.anomalies(ctx, since="1h")

    assert _ids(result) == [recent]
    assert f"[{recent}]" in _text(result)
    assert len(result.data["anomalies"]) == _text(result).count("detected ")


def test_an_unreadable_detection_time_is_kept_not_dropped(ctx, conn):
    """FR-010 / research R4. Silent omission from an anomaly listing is the one outcome
    Principle III forbids outright, so a row we cannot judge stays visible."""
    unreadable = seed(conn, detected_at="not-a-timestamp")
    recent = seed(conn, ago=timedelta(seconds=5))
    stale = seed(conn, ago=timedelta(days=2))

    listed = _ids(operations.anomalies(ctx, since="1m"))

    assert unreadable in listed, "a row we cannot judge was dropped"
    assert recent in listed
    assert stale not in listed, "the filter stopped filtering"


# -- US1: --since must not be the thing that acknowledges -------------------


def test_a_bad_duration_acknowledges_nothing(ctx, conn):
    """Research R5: the one irreversible step this command has must be unreachable from
    an invocation that exits with a usage error."""
    anomaly_id = seed(conn, ago=timedelta(minutes=1))

    result = operations.anomalies(ctx, since="bogus", acknowledge=anomaly_id)

    assert result.code == operations.EXIT_USAGE
    still_open = conn.execute(
        "SELECT acknowledged_at FROM anomalies WHERE id = ?", (anomaly_id,)
    ).fetchone()[0]
    assert still_open is None


def test_a_valid_duration_leaves_acknowledge_exactly_as_it_was(ctx, conn, layout):
    """FR-006: acknowledgement is not filtered — only the listing that follows is."""
    old = seed(conn, ago=timedelta(days=2))
    recent = seed(conn, ago=timedelta(minutes=10))

    result = operations.anomalies(ctx, since="1h", acknowledge=old)

    assert result.code == operations.EXIT_OK
    assert f"acknowledged anomaly {old}" in _text(result)
    assert _ids(result) == [recent]

    acked = conn.execute(
        "SELECT acknowledged_at FROM anomalies WHERE id = ?", (old,)
    ).fetchone()[0]
    assert acked is not None

    written = "".join(
        path.read_text(encoding="utf-8") for path in sorted(layout.log_dir.glob("*.jsonl"))
    )
    assert "anomaly.acknowledge" in written


def test_acknowledging_a_missing_id_still_fails_the_way_it_did(ctx, conn):
    result = operations.anomalies(ctx, since="1h", acknowledge=99999)

    assert result.code == operations.EXIT_FAILED
    assert "no unacknowledged anomaly with id 99999" in _text(result)


# -- US2: the unfiltered view is the view it was ----------------------------


def test_the_default_listing_is_unchanged(ctx, conn):
    """FR-004 / US2 scenario 1. Meaningful before this feature landed too, which is what
    makes it a regression baseline rather than an assertion written to fit new code."""
    newest = seed(conn, ago=timedelta(minutes=1))
    middle = seed(conn, ago=timedelta(hours=5))
    oldest = seed(conn, ago=timedelta(days=9))

    result = operations.anomalies(ctx)

    assert _ids(result) == [newest, middle, oldest]
    assert _text(result).rstrip().endswith(", ".join(operations.ANOMALY_KINDS))


def test_an_empty_listing_and_an_empty_window_do_not_say_the_same_thing(ctx, conn):
    """FR-009 / SC-004. ``no outstanding anomalies`` is an all-clear; a window that
    matched nothing is not one, and a reader who conflates them has been misled."""
    all_clear = _text(operations.anomalies(ctx))
    assert "no outstanding anomalies" in all_clear

    seed(conn, ago=timedelta(days=2))
    filtered_empty = _text(operations.anomalies(ctx, since="5m"))

    assert "no outstanding anomalies" not in filtered_empty
    assert "5m" in filtered_empty
    assert operations.anomalies(ctx, since="5m").code == operations.EXIT_OK


def test_both_empty_cases_still_print_the_kinds_trailer(ctx, conn):
    assert ", ".join(operations.ANOMALY_KINDS) in _text(operations.anomalies(ctx))

    seed(conn, ago=timedelta(days=2))
    assert ", ".join(operations.ANOMALY_KINDS) in _text(
        operations.anomalies(ctx, since="5m")
    )
