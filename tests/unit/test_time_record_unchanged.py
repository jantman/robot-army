"""The record did not move (milestone 010, US3).

The feature's whole risk is a local time escaping into something that is not a person. The
tests here are deliberately the boring kind: they assert that things are *identical*, and
they pass before the change as well as after — which is what makes them regression guards
rather than acceptance tests, and why the tasks file recommends writing them early.

Principle III's standard is reconstruction from the log alone. A log whose timestamps
depended on where the reader stood would not meet it, so the audit assertions below are the
constitutional half of this milestone rather than a nicety.
"""

from __future__ import annotations

import json
import re
import time

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import db, operations, timefmt

STORED = "2026-08-30T01:31:07Z"

#: Two zones that disagree with UTC and with each other, one of them by a half hour.
ZONES = ("America/New_York", "Asia/Kolkata", "UTC")

#: What a leaked local rendering looks like: a time followed by an explicit offset.
LOCAL_FORM = re.compile(r"\d{2}:\d{2}:\d{2} [+-]\d{2}:\d{2}")


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


def _in_zone(zone: str, call):
    """Run ``call`` with the host zone pinned, then put the zone back."""
    import os

    had, before = "TZ" in os.environ, os.environ.get("TZ")
    os.environ["TZ"] = zone
    time.tzset()
    try:
        return call()
    finally:
        if had:
            os.environ["TZ"] = before
        else:
            os.environ.pop("TZ", None)
        time.tzset()


def _seed(conn) -> int:
    item_id = seed_item(conn, state="active")
    with db.transaction(conn):
        conn.execute(
            "UPDATE work_items SET discovered_at=?, updated_at=?, ready_at=?, "
            "dispatching_at=?, active_at=? WHERE id=?",
            (*[STORED] * 5, item_id),
        )
        db.insert_session(conn, work_item_id=item_id, session_id="s1", attempt=1,
                          dry_run=False)
        conn.execute("UPDATE sessions SET started_at = ?", (STORED,))
        db.raise_anomaly(conn, kind="orphan_worktree", detail={},
                         entity_type="work_item", entity_id=str(item_id))
        conn.execute("UPDATE anomalies SET detected_at = ?", (STORED,))
    return item_id


# -- SC-004: machine-readable output is identical in every zone -------------


@pytest.mark.parametrize(
    "name,call",
    [
        ("status", lambda ctx, item: operations.status(ctx)),
        ("show", lambda ctx, item: operations.show(ctx, item)),
        ("anomalies", lambda ctx, item: operations.anomalies(ctx)),
        ("worktree_list", lambda ctx, item: operations.worktree_list(ctx)),
        ("capacity", lambda ctx, item: operations.capacity(ctx)),
    ],
)
def test_the_json_payload_is_byte_identical_in_every_zone(ctx, conn, name, call):
    """FR-012 and SC-004. The reason ``--json`` is documented as machine-readable."""
    item = _seed(conn)

    rendered = {
        zone: _in_zone(zone, lambda: call(ctx, item).render(as_json=True)) for zone in ZONES
    }

    first = rendered[ZONES[0]]
    for zone in ZONES[1:]:
        assert rendered[zone] == first, f"{name}: --json differs between {ZONES[0]} and {zone}"
    assert not LOCAL_FORM.search(first), f"{name}: a local time reached --json"


def test_every_stamp_in_a_json_payload_still_ends_in_z(ctx, conn):
    item = _seed(conn)

    payload = json.loads(_in_zone("Asia/Kolkata",
                                  lambda: operations.show(ctx, item).render(as_json=True)))

    stamps: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("_at") and isinstance(value, str) and value:
                    stamps.append(value)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    assert stamps, "the payload carried no timestamps to check"
    for value in stamps:
        assert value.endswith("Z"), f"{value!r} is not the stored format"

    # ``history`` carries bare stamps in pairs rather than under an ``_at`` key, and it is
    # the one the conversion had to be kept out of (contract C5).
    assert all(stamp == STORED for stamp, _ in payload["history"]), payload["history"]


def test_the_human_and_machine_renderings_of_one_result_disagree_on_purpose(ctx, conn):
    """The split, stated as one assertion: same Result, two representations."""
    item = _seed(conn)

    result = _in_zone("America/New_York", lambda: operations.show(ctx, item))
    lines = _in_zone("America/New_York", lambda: result.render(as_json=False))
    payload = result.render(as_json=True)

    assert "2026-08-29 21:31:07 -04:00" in lines, "the human rendering is not local"
    assert STORED in payload, "the machine rendering is not UTC"
    assert STORED not in lines, "a raw stored stamp reached a person"


# -- SC-005: the audit log is unchanged -------------------------------------


def test_an_audited_action_writes_utc_from_a_non_utc_host(ctx, conn, layout):
    """FR-011. Principle III's reconstruction standard depends on this and nothing else."""
    _in_zone("Asia/Kolkata", lambda: operations.pause_dispatch(ctx, by="cli"))
    ctx.audit.close()

    files = sorted(layout.log_dir.glob("audit-*.jsonl"))
    assert files, "no audit file was written"

    records = [
        json.loads(line)
        for path in files
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records, "the audit file held no records"
    for record in records:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", record["ts"]), record["ts"]


def test_the_audit_file_is_named_for_the_utc_day_not_the_local_one(ctx, conn, layout):
    """A `+05:30` host is often on tomorrow's date. The file must not follow it."""
    from datetime import UTC, datetime

    utc_day = datetime.now(UTC).strftime("%Y-%m-%d")
    _in_zone("Asia/Kolkata", lambda: operations.pause_dispatch(ctx, by="cli"))
    ctx.audit.close()

    names = {path.name for path in layout.log_dir.glob("audit-*.jsonl")}
    assert names == {f"audit-{utc_day}.jsonl"}, names


# -- FR-010: nothing stored is rewritten ------------------------------------


def test_rendering_every_view_rewrites_no_stored_value(ctx, conn):
    """Displaying a row must not be a write."""
    item = _seed(conn)
    select = (
        "SELECT discovered_at, updated_at, ready_at, dispatching_at, active_at "
        "FROM work_items WHERE id = ?"
    )
    before = conn.execute(select, (item,)).fetchone()

    def render_everything():
        operations.status(ctx)
        operations.show(ctx, item)
        operations.anomalies(ctx)

    _in_zone("Asia/Kolkata", render_everything)

    after = conn.execute(select, (item,)).fetchone()
    assert tuple(after) == tuple(before)
    assert all(value == STORED for value in after)


# -- FR-013: no decision reads a converted value ----------------------------


def test_ages_are_measured_from_the_stored_value_not_the_displayed_one(conn):
    """The same elapsed time in every zone, because a duration has no timezone."""
    from robot_army.web import pages

    ages = {zone: _in_zone(zone, lambda: pages.age_seconds(STORED)) for zone in ZONES}

    assert len(set(ages.values())) == 1, f"age varied by zone: {ages}"


def test_a_displayed_value_cannot_be_read_back_as_a_record(conn):
    """The strongest available guard against a rendering leaking into a decision.

    Every comparison in this package parses with the stored format. A local rendering does
    not parse as one, so anything that fed a displayed value into a comparison would get
    ``None`` and fail loudly rather than compare two different clocks.
    """
    displayed = _in_zone("America/New_York", lambda: timefmt.local(STORED))

    assert timefmt.parse_stamp(displayed) is None


# -- FR-014: duration arguments are untouched -------------------------------


@pytest.mark.parametrize("since", ["30s", "10m", "2h", "1d"])
def test_duration_filters_keep_their_grammar_and_meaning(ctx, conn, layout, since):
    """``--since`` describes elapsed time, not wall clock. Nothing here has a zone."""
    from robot_army.operations import parse_duration

    parsed = {zone: _in_zone(zone, lambda: parse_duration(since)) for zone in ZONES}
    assert len(set(parsed.values())) == 1, f"{since} varied by zone: {parsed}"

    for zone in ZONES:
        result = _in_zone(zone, lambda: operations.read_log(ctx, since=since))
        assert result.code == operations.EXIT_OK, f"{since} rejected under {zone}"
