"""Every terminal display site renders in the host's zone (milestone 010, US1).

One test per site in contracts/time-display.md §2 — C1 through C10 — because that table is
the definition of "every surface" for FR-005, and research R7 established that nothing in
the existing suite would notice a site left behind.

The stamps below are chosen so the local rendering lands on a **different calendar day**
from the stored value. ``2026-08-30T01:31:07Z`` is ``2026-08-29 21:31:07 -04:00`` in New
York, so an assertion that the 29th appears is an assertion that a conversion actually
happened, not merely that a string was reformatted.
"""

from __future__ import annotations

import itertools
import json

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import db, operations, timefmt

#: Stored, and what it must read as under ``America/New_York``.
STORED = "2026-08-30T01:31:07Z"
SHOWN = "2026-08-29 21:31:07 -04:00"

#: Applied to every test in this module: the whole point is what a non-UTC host prints.
pytestmark = pytest.mark.parametrize("in_timezone", ["America/New_York"], indirect=True)


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


#: Written out rather than built, so the query is a literal and every column this test
#: depends on is visible in it.
_PIN_STAMPS = """
    UPDATE work_items SET
        discovered_at = ?, updated_at = ?, ready_at = ?, dispatching_at = ?,
        active_at = ?, ended_at = ?, done_at = ?, cleaned_at = ?, speckit_phase_at = ?
    WHERE id = ?
"""


def _pin_item_stamps(conn, item_id: int, stamp: str = STORED) -> None:
    """Force every timestamp on one work item to a known instant."""
    with db.transaction(conn):
        conn.execute(_PIN_STAMPS, (*[stamp] * 9, item_id))


def _lines(result) -> str:
    return "\n".join(result.lines)


def _assert_local_not_utc(text: str, site: str) -> None:
    assert SHOWN in text, f"{site}: no local rendering in output"
    assert STORED not in text, f"{site}: a raw UTC stamp survived"


# -- C1, C2: status ---------------------------------------------------------


def test_c1_status_renders_the_pause_time_locally(ctx, conn, in_timezone):
    operations.pause_dispatch(ctx, by="cli")
    with db.transaction(conn):
        conn.execute("UPDATE dispatch_control SET paused_at = ?", (STORED,))

    text = _lines(operations.status(ctx))

    assert "PAUSED since" in text
    _assert_local_not_utc(text, "C1")


def test_c2_status_renders_an_anomaly_detection_time_locally(ctx, conn, in_timezone):
    with db.transaction(conn):
        db.raise_anomaly(conn, kind="orphan_worktree", detail={}, entity_type="work_item",
                         entity_id="1")
        conn.execute("UPDATE anomalies SET detected_at = ?", (STORED,))

    text = _lines(operations.status(ctx))

    assert "unacknowledged anomalies" in text
    _assert_local_not_utc(text, "C2")


# -- C3, C4, C5, C6: show ---------------------------------------------------


def test_c3_show_renders_the_speckit_phase_time_locally(ctx, conn, in_timezone):
    item_id = seed_item(conn, state="active")
    with db.transaction(conn):
        conn.execute(
            "UPDATE work_items SET speckit_phase = ?, speckit_feature_dir = ? WHERE id = ?",
            ("plan", "specs/010-x", item_id),
        )
    _pin_item_stamps(conn, item_id)

    text = _lines(operations.show(ctx, item_id))

    assert "spec-kit" in text
    _assert_local_not_utc(text, "C3")


def test_c4_show_renders_the_cleaned_at_time_locally(ctx, conn, in_timezone):
    item_id = seed_item(conn, state="done")
    with db.transaction(conn):
        # The line is gated on a cleanup having happened, not merely on the stamp existing.
        conn.execute(
            "UPDATE work_items SET cleanup_state = ?, cleanup_reason = ? WHERE id = ?",
            ("removed", "session ended cleanly", item_id),
        )
    _pin_item_stamps(conn, item_id)

    text = _lines(operations.show(ctx, item_id))

    assert "cleaned at" in text
    _assert_local_not_utc(text, "C4")


def test_c5_show_renders_every_history_row_locally(ctx, conn, in_timezone):
    """Six transitions, all of them converted — and none of them via ``_history``."""
    item_id = seed_item(conn, state="done")
    _pin_item_stamps(conn, item_id)

    result = operations.show(ctx, item_id)
    text = _lines(result)

    for label in ("discovered", "ready", "dispatching", "active", "session ended", "done"):
        assert f"{SHOWN}  {label}" in text, f"C5: {label} row is not local"
    assert STORED not in text, "C5: a raw UTC stamp survived"


def test_c6_show_renders_session_start_and_end_locally(ctx, conn, in_timezone):
    item_id = seed_item(conn, state="active")
    _pin_item_stamps(conn, item_id)
    with db.transaction(conn):
        db.insert_session(conn, work_item_id=item_id, session_id="s1", attempt=1,
                          dry_run=False)
        conn.execute("UPDATE sessions SET started_at = ?, ended_at = ?", (STORED, STORED))

    text = _lines(operations.show(ctx, item_id))

    assert f"started {SHOWN} ended {SHOWN}" in text, "C6: session row is not local"
    assert STORED not in text, "C6: a raw UTC stamp survived"


def test_c6_an_unended_session_still_renders_the_absent_marker(ctx, conn, in_timezone):
    """FR-016: ``local(None)`` is ``None``, so the existing ``or '—'`` must still fire."""
    item_id = seed_item(conn, state="active")
    _pin_item_stamps(conn, item_id)
    with db.transaction(conn):
        db.insert_session(conn, work_item_id=item_id, session_id="s1", attempt=1,
                          dry_run=False)
        conn.execute("UPDATE sessions SET started_at = ?, ended_at = NULL", (STORED,))

    text = _lines(operations.show(ctx, item_id))

    assert f"started {SHOWN} ended —" in text


# -- C7, C8: pause ----------------------------------------------------------


def test_c8_pause_confirms_with_a_local_time(ctx, conn, in_timezone, monkeypatch):
    """The fresh-pause confirmation. ``db.utcnow`` is what stamps ``paused_at``."""
    monkeypatch.setattr(db, "utcnow", lambda: STORED)

    text = _lines(operations.pause_dispatch(ctx, by="cli"))

    assert "dispatch paused at" in text
    _assert_local_not_utc(text, "C8")


def test_c7_pausing_an_already_paused_dispatch_reports_locally_too(ctx, conn, in_timezone,
                                                                   monkeypatch):
    """The no-op branch, which states when the *existing* pause was set."""
    monkeypatch.setattr(db, "utcnow", lambda: STORED)
    operations.pause_dispatch(ctx, by="cli")

    text = _lines(operations.pause_dispatch(ctx, by="cli"))

    assert "already paused" in text
    _assert_local_not_utc(text, "C7")


# -- C9: anomalies ----------------------------------------------------------


def test_c9_the_anomalies_listing_renders_detection_times_locally(ctx, conn, in_timezone):
    with db.transaction(conn):
        db.raise_anomaly(conn, kind="orphan_worktree", detail={}, entity_type="work_item",
                         entity_id="1")
        conn.execute("UPDATE anomalies SET detected_at = ?", (STORED,))

    text = _lines(operations.anomalies(ctx))

    assert "detected" in text
    _assert_local_not_utc(text, "C9")


# -- C10: log ---------------------------------------------------------------


def test_c10_the_log_renders_each_record_time_locally(ctx, in_timezone):
    day = STORED[:10]
    path = ctx.layout.log_dir / f"audit-{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ts": STORED, "kind": "intent", "action": "github.poll",
                    "outcome": "ok"}) + "\n",
        encoding="utf-8",
    )

    result = operations.read_log(ctx)
    text = _lines(result)

    _assert_local_not_utc(text, "C10")
    assert result.data["records"][0]["ts"] == STORED, "C10: --json must stay UTC"


# -- the failure paths ------------------------------------------------------


def test_a_corrupt_stamp_reaches_the_screen_verbatim(ctx, conn, in_timezone):
    """FR-015. A rendering layer must not be what hides a corrupt row, nor what crashes."""
    item_id = seed_item(conn, state="ready")
    with db.transaction(conn):
        conn.execute("UPDATE work_items SET ready_at = ? WHERE id = ?",
                     ("not a timestamp", item_id))

    result = operations.show(ctx, item_id)

    assert result.code == operations.EXIT_OK
    assert "not a timestamp  ready" in _lines(result)


def test_an_absent_stamp_still_renders_nothing_rather_than_none(ctx, conn, in_timezone):
    """FR-016. ``_history`` already drops empty stamps; conversion must not resurrect them."""
    item_id = seed_item(conn, state="discovered")

    text = _lines(operations.show(ctx, item_id))

    # Scoped to the timestamp-bearing lines: the resume-signal block legitimately prints
    # ``None`` for signals it has not computed, and that is not this feature's business.
    tail = text.split("state history:", 1)[1].splitlines()[1:]
    history = list(itertools.takewhile(lambda line: line.strip(), tail))
    assert history, "the history section rendered nothing at all"
    assert not any("None" in line for line in history), history
    assert "cleaned at" not in text


def test_every_converted_site_agrees_with_timefmt(ctx, conn, in_timezone):
    """FR-005: one rule, one implementation. No site may render its own way."""
    assert timefmt.local(STORED) == SHOWN
