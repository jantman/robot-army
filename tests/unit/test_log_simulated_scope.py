"""``log`` excludes rehearsed records by default, and says how many it withheld (issue #21).

The information was never missing. Audit records have carried ``dry_run`` and ``simulated``
since milestone 001, and ``_format_record`` has always rendered either as the trailing
``[simulated]`` marker — so the reader could see which records did not really happen and had
no way to ask for the ones that did. The reported measurement was 951 rehearsed records in a
two-day window, on the one surface the constitution names as the reconstruction path.

Two readers share one predicate. ``read_log`` scans every daily file forwards; the web's
``read_log_page`` scans backwards and stops when a page is full or its byte budget is spent.
The properties that matter are different for each, and both are asserted here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from tests.conftest import make_boundaries

from robot_army import operations

FLAG = "--include-simulated"


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


def _stamp(delta: timedelta = timedelta()) -> str:
    return (datetime.now(UTC) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_log(ctx, records: list[dict], *, day: str | None = None) -> None:
    """Append records to a daily audit file, oldest first, as the daemon would.

    Written directly rather than through ``AuditLog`` because these tests need records with
    chosen timestamps and chosen marker fields, and giving the writer a knob for either would
    add a production parameter whose only caller is a test.
    """
    day = day or datetime.now(UTC).strftime("%Y-%m-%d")
    path = ctx.layout.log_dir / f"audit-{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def record(action: str, **extra) -> dict:
    base = {
        "ts": _stamp(),
        "component": "daemon",
        "kind": "event",
        "action": action,
        "outcome": "ok",
    }
    base.update(extra)
    return base


# -- the predicate ----------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [{"simulated": True}, {"dry_run": True}, {"simulated": True, "dry_run": True}],
    ids=["simulated", "dry_run", "both"],
)
def test_either_marker_means_rehearsed(marker: dict) -> None:
    """Two field names, one condition, and both have always been written.

    ``dry_run`` comes from the acting component and ``simulated`` from the ``effects``
    boundaries. A reader that honoured one and not the other would hide half the traffic.
    """
    judged = operations._judge_record(
        record("x", **marker), cutoff=None, item_id=None, outcome=None
    )
    assert judged is operations._WITHHELD

    revealed = operations._judge_record(
        record("x", **marker),
        cutoff=None,
        item_id=None,
        outcome=None,
        include_simulated=True,
    )
    assert revealed is operations._MATCH


def test_a_real_record_is_matched_either_way() -> None:
    for include in (False, True):
        assert (
            operations._judge_record(
                record("x"), cutoff=None, item_id=None, outcome=None,
                include_simulated=include,
            )
            is operations._MATCH
        )


def test_what_is_hidden_and_what_is_marked_are_the_same_question() -> None:
    """The first thing that would drift here, pinned so it cannot.

    ``_format_record`` draws its ``[simulated]`` marker from ``_is_rehearsed`` and so does the
    judge. If they ever disagreed, the default reader would show records marked ``[simulated]``
    — which is exactly the symptom the issue measured, wearing the fix's clothes.
    """
    for marker in ({"simulated": True}, {"dry_run": True}, {}):
        row = record("x", **marker)
        marked = "[simulated]" in operations._format_record(row)
        hidden = (
            operations._judge_record(row, cutoff=None, item_id=None, outcome=None)
            is operations._WITHHELD
        )
        assert marked == hidden, row


def test_a_rehearsed_record_outside_the_window_is_rejected_not_withheld() -> None:
    """The ordering that makes the count equal what the flag reveals.

    ``--include-simulated`` would still not show this record, because ``--since`` excludes it
    on its own. Counting it as withheld would state a number the flag does not produce, which
    is the equality milestone 008 built ``_work_item_filters`` to guarantee.
    """
    old = record("x", simulated=True, ts=_stamp(timedelta(days=30)))
    cutoff = datetime.now(UTC) - timedelta(hours=1)

    assert (
        operations._judge_record(old, cutoff=cutoff, item_id=None, outcome=None)
        is operations._REJECT
    )


def test_an_unreadable_timestamp_still_wins_over_the_simulated_check() -> None:
    """FR-044: a record that cannot be judged is skipped *and counted*, never miscounted."""
    broken = record("x", simulated=True, ts="not-a-timestamp")
    cutoff = datetime.now(UTC) - timedelta(hours=1)

    assert (
        operations._judge_record(broken, cutoff=cutoff, item_id=None, outcome=None)
        is operations._UNREADABLE
    )


# -- read_log ---------------------------------------------------------------


def test_the_default_reader_shows_only_real_records(ctx) -> None:
    write_log(
        ctx,
        [record("real.one"), record("real.two")]
        + [record(f"rehearsed.{n}", simulated=True) for n in range(4)],
    )

    result = operations.read_log(ctx)
    text = "\n".join(result.lines)

    assert "[simulated]" not in text, (
        "the reported symptom exactly: 951 records carrying the marker, shown either way"
    )
    assert text.count("real.") == 2
    assert result.data["withheld_simulated"] == 4
    assert f"4 simulated rows withheld — pass {FLAG} to show them" in text


def test_the_flag_reveals_exactly_what_was_withheld(ctx) -> None:
    write_log(
        ctx,
        [record("real.one")] + [record(f"rehearsed.{n}", dry_run=True) for n in range(3)],
    )

    hidden = operations.read_log(ctx)
    shown = operations.read_log(ctx, include_simulated=True)

    assert len(shown.data["records"]) - len(hidden.data["records"]) == 3
    assert hidden.data["withheld_simulated"] == 3
    assert shown.data["withheld_simulated"] == 0


def test_revealed_records_keep_the_marker_they_always_had(ctx) -> None:
    write_log(ctx, [record("rehearsed", simulated=True)])

    text = "\n".join(operations.read_log(ctx, include_simulated=True).lines)

    assert "[simulated]" in text
    assert "withheld" not in text


def test_nothing_is_said_when_nothing_was_withheld(ctx) -> None:
    write_log(ctx, [record("real.one")])

    result = operations.read_log(ctx)

    assert "withheld" not in "\n".join(result.lines)
    assert result.data["withheld_simulated"] == 0, "stated as zero, never omitted"


def test_the_window_and_the_flag_compose(ctx) -> None:
    write_log(
        ctx,
        [
            record("old.real", ts=_stamp(timedelta(days=3))),
            record("old.rehearsed", simulated=True, ts=_stamp(timedelta(days=3))),
            record("new.real"),
            record("new.rehearsed", simulated=True),
        ],
    )

    result = operations.read_log(ctx, since="1h")
    text = "\n".join(result.lines)

    assert "new.real" in text
    assert "old.real" not in text
    assert result.data["withheld_simulated"] == 1, (
        "only the recent rehearsed record; the old one is out of the window either way"
    )


def test_the_item_filter_and_the_flag_compose(ctx) -> None:
    write_log(
        ctx,
        [
            record("a", entity_type="work_item", entity_id=42),
            record("b", entity_type="work_item", entity_id=42, simulated=True),
            record("c", entity_type="work_item", entity_id=99, simulated=True),
        ],
    )

    result = operations.read_log(ctx, item_id=42)

    assert [r["action"] for r in result.data["records"]] == ["a"]
    assert result.data["withheld_simulated"] == 1, "item 99's record was never in scope"


def test_limit_counts_records_the_reader_can_actually_see(ctx) -> None:
    """``--limit 20`` means twenty visible records, not twenty of which some are hidden.

    Trimming before the filter would make the page size depend on how much rehearsal traffic
    happened to sit at the end of the log, which is not a thing the reader asked about.
    """
    write_log(
        ctx,
        [record(f"rehearsed.{n}", simulated=True) for n in range(10)]
        + [record(f"real.{n}") for n in range(5)],
    )

    result = operations.read_log(ctx, limit=3)

    assert len(result.data["records"]) == 3
    assert all(not operations._is_rehearsed(r) for r in result.data["records"])


def test_a_partial_final_line_is_still_counted_in_both_spellings(ctx) -> None:
    """The unparseable count is independent of the simulated filter (R14).

    A process can die between the write and the flush, so a truncated last line is expected —
    and refusing to read the log because of one would be exactly the wrong trade.
    """
    write_log(ctx, [record("real.one"), record("rehearsed", simulated=True)])
    path = ctx.layout.log_dir / f"audit-{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": "2026-09-06T00:00:00Z", "acti')

    for include in (False, True):
        result = operations.read_log(ctx, include_simulated=include)
        assert result.data["unparseable_lines"] == 1, include
        assert "1 unparseable line(s) skipped" in "\n".join(result.lines)


# -- read_log_page ----------------------------------------------------------


def test_the_page_fills_from_older_records_rather_than_coming_back_empty(ctx) -> None:
    """The property that requires the filter to live *inside* the backwards scan.

    Applied to a finished page instead, a page whose newest region is entirely rehearsed would
    return nothing while older matching records sat unread just below it — the reader would be
    told the log was empty because a rehearsal ran recently.
    """
    write_log(
        ctx,
        [record(f"real.{n}") for n in range(5)]
        + [record(f"rehearsed.{n}", simulated=True) for n in range(40)],
    )

    result = operations.read_log_page(ctx, limit=5)

    assert len(result.data["records"]) == 5
    assert all(r["action"].startswith("real.") for r in result.data["records"])
    assert result.data["withheld_simulated"] == 40


def test_the_page_states_what_its_own_scan_withheld(ctx) -> None:
    write_log(ctx, [record("real.one"), record("rehearsed", simulated=True)])

    result = operations.read_log_page(ctx)
    text = "\n".join(result.lines)

    assert result.data["withheld_simulated"] == 1
    assert "1 simulated record(s) on this page withheld" in text, (
        "scoped to the scan in the words it prints — a bounded reader cannot honestly count "
        "what it never read"
    )


def test_the_page_reveals_them_on_request(ctx) -> None:
    write_log(ctx, [record("real.one"), record("rehearsed", simulated=True)])

    result = operations.read_log_page(ctx, include_simulated=True)

    assert len(result.data["records"]) == 2
    assert result.data["withheld_simulated"] == 0
    assert "[simulated]" in "\n".join(result.lines)


def test_paging_across_a_boundary_does_not_double_count_what_it_withheld(ctx) -> None:
    """Each page reports its own scan, so the counts partition rather than overlap."""
    write_log(
        ctx,
        [record(f"r{n}", simulated=(n % 2 == 0)) for n in range(20)],
    )

    first = operations.read_log_page(ctx, limit=4)
    assert first.data["has_more"]
    second = operations.read_log_page(ctx, limit=4, cursor=first.data["next_cursor"])

    actions = [r["action"] for r in first.data["records"] + second.data["records"]]
    assert len(actions) == len(set(actions)), "pages must be disjoint"
    assert all(not operations._is_rehearsed(r) for r in second.data["records"])


# -- log --follow -----------------------------------------------------------


def _follow_once(ctx, lines: list[str], *, include_simulated: bool) -> list[str]:
    """Drive ``follow_log`` over a tail and return what it yielded before a sentinel.

    Two things make this awkward and both are properties of the thing under test rather than
    of the test. ``follow_log`` seeks to the *end* of the file when it starts, so records have
    to be appended after it is already reading — hence the consumer thread. And it never
    finishes on its own, because a tail has no end, so the read is terminated by a final real
    record guaranteed to pass every filter rather than by counting yields, which would
    deadlock the moment the filter hid one.

    The join timeout is what turns a hang into a readable failure instead of a stuck suite.
    """
    import threading
    import time as _time

    path = ctx.layout.log_dir / f"audit-{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()

    out: list[str] = []

    def consume() -> None:
        for line in operations.follow_log(ctx, include_simulated=include_simulated):
            if "sentinel.end" in line:
                return
            out.append(line)

    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    _time.sleep(0.3)  # let the tail open the file and seek past its (empty) end

    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")
        handle.write(json.dumps(record("sentinel.end")) + "\n")
        handle.flush()

    reader.join(timeout=10)
    assert not reader.is_alive(), "the tail never reached the sentinel"
    return out


def test_follow_hides_rehearsed_records_by_default(ctx) -> None:
    """The sub-mode the fix would most easily be left out of.

    ``--follow`` is spelled on the same verb and takes the same flag, so a tail showing
    rehearsed records either way is the original defect surviving one level down — and it is
    the mode where the rehearsal drowns the real thing most completely, because a dry run at
    speed writes far more records than live work does.
    """
    lines = [
        json.dumps(record("real.one")),
        json.dumps(record("rehearsed.one", simulated=True)),
        json.dumps(record("rehearsed.two", dry_run=True)),
    ]

    shown = _follow_once(ctx, lines, include_simulated=False)

    assert any("real.one" in line for line in shown)
    assert not any("rehearsed." in line for line in shown)


def test_follow_shows_them_when_asked(ctx) -> None:
    lines = [
        json.dumps(record("real.one")),
        json.dumps(record("rehearsed.one", dry_run=True)),
    ]

    shown = _follow_once(ctx, lines, include_simulated=True)

    assert any("real.one" in line for line in shown)
    assert any("rehearsed.one" in line and "[simulated]" in line for line in shown)


def test_follow_still_shows_a_line_it_cannot_parse(ctx) -> None:
    """A line we cannot judge is not a line we may drop — the filter must not swallow it."""
    shown = _follow_once(ctx, ["{not json at all"], include_simulated=False)

    assert shown and shown[0].startswith("(unparseable)")
