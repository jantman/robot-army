"""The audit view: paging, filtering, tolerance, and the bound (T061, T062).

The reader pages **backwards** through daily files, newest first, stopping the moment the
page is full (R14). Loading the whole log and filtering in memory would grow without bound
by construction, which is exactly what SC-014 forbids.

The skip-and-count behaviour is not defensive programming: a partially written final line
is *expected*, because the process can die between the write and the flush, and 001 decided
that refusing to read the log over one truncated line is the wrong trade.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import operations


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


#: The audit log's files are named by date and its records carry timestamps, and both are
#: compared against **now** by the ``since`` filter. Fixing either to a literal date makes a
#: test that passes today and fails at some midnight months later, for no reason connected
#: to the code — which is exactly what happened to
#: ``test_a_record_with_an_unparseable_timestamp_is_skipped_and_counted`` on 2026-08-25.
#: Deriving both from the clock keeps the fixtures at a fixed *offset* from now rather than
#: at a fixed point in the past.
TODAY = datetime.now(UTC).strftime("%Y-%m-%d")
YESTERDAY = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")


def write_log(layout, day: str, records: list[dict], *, truncate_last: bool = False) -> None:
    path = layout.log_dir / f"audit-{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, separators=(",", ":")) for record in records]
    text = "\n".join(lines)
    if truncate_last and lines:
        text = "\n".join(lines[:-1]) + "\n" + lines[-1][: len(lines[-1]) // 2]
    else:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def record(n: int, **overrides) -> dict:
    base = {
        "ts": f"{TODAY}T00:{n // 60:02d}:{n % 60:02d}Z",
        "component": "daemon",
        "kind": "event",
        "action": "state.work_item",
        "outcome": "ok",
        "entity_type": "work_item",
        "entity_id": 1,
    }
    base.update(overrides)
    return base


# -- FR-044: tolerance -------------------------------------------------------


def test_a_truncated_final_line_yields_records_plus_a_non_zero_skip_count(ctx, layout):
    write_log(layout, TODAY, [record(n) for n in range(5)], truncate_last=True)
    result = operations.read_log_page(ctx)
    assert len(result.data["records"]) == 4
    assert result.data["skipped_lines"] >= 1


def test_the_skipped_count_reaches_the_page(web, layout):
    write_log(layout, TODAY, [record(n) for n in range(3)], truncate_last=True)
    body = web.get("/log").text
    assert "unparseable line(s) skipped" in body
    assert web.get_json("/log").json()["skipped_lines"] >= 1


def test_a_record_with_an_unparseable_timestamp_is_skipped_and_counted(ctx, layout):
    """Skipped is not rejected: a record we cannot judge must be reported, not dropped.

    This is the only test in the module that passes ``since``, so it is the only one whose
    fixtures have to be inside a window measured from **now**. ``record()``'s default stamp
    is midnight of the day the module was imported, which is inside a one-day window right
    up until midnight and outside it a second later — so a CI run that starts at 23:59 and
    reaches this test at 00:01 fails, having filtered every record out. That happened on
    2026-08-25 and again on 2026-09-06. Deriving the day from the module-level clock, which
    is what the last fix did, does not close it: the flake is the *offset*, not the date.

    So the stamps here are a minute old by construction, and the file is named for the day
    that minute falls in, whenever the test happens to run.
    """
    minute_ago = datetime.now(UTC) - timedelta(minutes=1)
    stamp = minute_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
    write_log(
        layout,
        minute_ago.strftime("%Y-%m-%d"),
        [record(0, ts=stamp), record(1, ts="not-a-timestamp"), record(2, ts=stamp)],
    )
    result = operations.read_log_page(ctx, since="1d")
    assert result.data["skipped_lines"] == 1
    assert len(result.data["records"]) == 2


# -- FR-042: filters ---------------------------------------------------------


def test_records_come_back_newest_first(ctx, layout):
    write_log(layout, TODAY, [record(n) for n in range(5)])
    stamps = [r["ts"] for r in operations.read_log_page(ctx).data["records"]]
    assert stamps == sorted(stamps, reverse=True)


def test_the_item_filter_narrows_correctly(ctx, layout):
    write_log(
        layout,
        TODAY,
        [record(0, entity_id=1), record(1, entity_id=2), record(2, entity_id=1)],
    )
    result = operations.read_log_page(ctx, item_id=1)
    assert [r["entity_id"] for r in result.data["records"]] == [1, 1]
    assert result.data["filters"]["item"] == 1


def test_the_outcome_filter_narrows_correctly(ctx, layout):
    write_log(
        layout,
        TODAY,
        [record(0, outcome="ok"), record(1, outcome="error"), record(2, outcome="pending")],
    )
    result = operations.read_log_page(ctx, outcome="error")
    assert [r["outcome"] for r in result.data["records"]] == ["error"]


def test_an_unknown_outcome_is_a_usage_error_not_an_empty_page(ctx, layout):
    """An empty page would read as "nothing happened", which is a lie."""
    write_log(layout, TODAY, [record(0)])
    result = operations.read_log_page(ctx, outcome="fine")
    assert result.code == operations.EXIT_USAGE


def test_the_since_filter_narrows_by_time(ctx, layout):
    now = datetime.now(UTC)
    write_log(
        layout,
        TODAY,
        [
            record(0, ts=(now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")),
            record(1, ts=(now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ],
    )
    assert len(operations.read_log_page(ctx, since="10m").data["records"]) == 1
    assert len(operations.read_log_page(ctx, since="7d").data["records"]) == 2


def test_a_malformed_duration_explains_itself(ctx, layout):
    result = operations.read_log_page(ctx, since="10 fortnights")
    assert result.code == operations.EXIT_USAGE
    assert "fortnights" in "\n".join(result.lines)


def test_the_active_filter_is_always_rendered(web, layout):
    """FR-042: a page that silently narrows what it shows is worse than one showing nothing."""
    write_log(layout, TODAY, [record(0)])
    body = web.get("/log?item=1&outcome=ok").text
    assert "filters: item=1, outcome=ok" in body or (
        "item=1" in body and "outcome=ok" in body
    )


def test_a_non_numeric_item_filter_is_a_400_not_a_crash(web, layout):
    response = web.get("/log?item=abc")
    assert response.status == 400
    assert "not a work item id" in response.text


# -- FR-044 / R14: paging ----------------------------------------------------


def test_paging_returns_disjoint_pages_within_one_file(ctx, layout):
    write_log(layout, TODAY, [record(n) for n in range(10)])
    first = operations.read_log_page(ctx, limit=4)
    assert len(first.data["records"]) == 4
    assert first.data["has_more"] is True

    second = operations.read_log_page(ctx, limit=4, cursor=first.data["next_cursor"])
    assert len(second.data["records"]) == 4

    seen = [r["ts"] for r in first.data["records"] + second.data["records"]]
    assert len(set(seen)) == 8, "pages must not overlap"


def test_paging_returns_disjoint_pages_across_a_file_boundary(ctx, layout):
    """The case a naive offset gets wrong, and the reason the cursor names its file."""
    write_log(layout, YESTERDAY, [record(n, ts=f"{YESTERDAY}T00:00:{n:02d}Z") for n in range(5)])
    write_log(layout, TODAY, [record(n, ts=f"{TODAY}T00:00:{n:02d}Z") for n in range(5)])

    collected: list[str] = []
    cursor = None
    for _ in range(10):
        page = operations.read_log_page(ctx, limit=3, cursor=cursor)
        collected.extend(r["ts"] for r in page.data["records"])
        cursor = page.data["next_cursor"]
        if not page.data["has_more"]:
            break

    assert len(collected) == 10
    assert len(set(collected)) == 10, "no record appears twice"
    assert collected == sorted(collected, reverse=True), "and none is skipped or reordered"


def test_no_daily_file_is_ever_read_whole(ctx, layout, monkeypatch):
    """RA-14. ``read_text().splitlines()`` allocated the whole file plus a list of every line
    in it, to return at most a thousand records from the end.

    Breaking ``read_text`` is a blunt instrument and the right one: it fails if a single call
    site is left behind, which is the regression this asserts against.
    """
    write_log(layout, TODAY, [record(n) for n in range(50)])

    def forbidden(*args, **kwargs):
        raise AssertionError("a daily file was read whole")

    monkeypatch.setattr(operations.Path, "read_text", forbidden)
    page = operations.read_log_page(ctx, limit=5)
    assert len(page.data["records"]) == 5


def test_a_record_longer_than_one_block_is_returned_whole(ctx, layout, monkeypatch):
    """The shape a backwards block reader gets wrong: a line that spans a block boundary has
    to be carried into the next read rather than emitted as two."""
    monkeypatch.setattr(operations, "LOG_SCAN_BLOCK_BYTES", 512)
    big = record(1, detail={"note": "x" * 4000})
    write_log(layout, TODAY, [record(0), big, record(2)])

    page = operations.read_log_page(ctx)
    notes = [r.get("detail", {}).get("note") for r in page.data["records"]]
    assert "x" * 4000 in notes
    assert len(page.data["records"]) == 3


def test_a_file_whose_size_is_an_exact_multiple_of_the_block_reads_correctly(
    ctx, layout, monkeypatch
):
    """The other off-by-one: the final read must land exactly on zero without emitting an
    empty leading line or dropping the first record in the file."""
    monkeypatch.setattr(operations, "LOG_SCAN_BLOCK_BYTES", 64)
    records = [record(n) for n in range(6)]
    write_log(layout, TODAY, records)
    path = layout.log_dir / f"audit-{TODAY}.jsonl"
    # Pad the *first* record's detail until the file is an exact multiple of the block size,
    # so the last backwards read consumes precisely one block and stops.
    size = path.stat().st_size
    padding = (-size) % 64
    if padding:
        records[0] = record(0, detail={"note": "x" * padding})
        write_log(layout, TODAY, records)
        # Re-pad: the JSON grew by more than the padding (quotes, keys), so converge once.
        extra = (-path.stat().st_size) % 64
        records[0] = record(0, detail={"note": "x" * (padding + extra)})
        write_log(layout, TODAY, records)

    assert path.stat().st_size % 64 == 0, "the fixture must sit on a block boundary"
    page = operations.read_log_page(ctx)
    assert len(page.data["records"]) == 6


def test_a_truncated_final_line_is_still_counted_when_read_backwards(ctx, layout):
    """The interruption path: R14 flushes per record, so the process can die between the
    write and the flush. Reading the file from its end meets that partial line first."""
    write_log(layout, TODAY, [record(n) for n in range(5)], truncate_last=True)
    page = operations.read_log_page(ctx)
    assert len(page.data["records"]) == 4
    assert page.data["skipped_lines"] == 1


# -- RA-14: the byte budget --------------------------------------------------


def _fat_record(n: int, day: str) -> dict:
    return record(n, ts=f"{day}T00:{n // 60:02d}:{n % 60:02d}Z", detail={"note": "z" * 200})


def _fill_beyond_the_budget(layout, monkeypatch, *, budget: int) -> None:
    """A log directory bigger than the budget, without writing eight megabytes in a test."""
    monkeypatch.setattr(operations, "LOG_SCAN_BUDGET_BYTES", budget)
    for offset in range(4):
        day = (datetime.now(UTC) - timedelta(days=offset)).strftime("%Y-%m-%d")
        write_log(layout, day, [_fat_record(n, day) for n in range(400)])


def test_a_filter_matching_nothing_stops_at_the_budget_and_says_so(ctx, layout, monkeypatch):
    """Before this, ``/log?item=999999`` walked and fully read every audit file present.

    Stopping silently would be worse than not stopping: an empty page would be
    indistinguishable from an empty history, which is the one thing the audit view must never
    be ambiguous about.
    """
    _fill_beyond_the_budget(layout, monkeypatch, budget=20_000)

    page = operations.read_log_page(ctx, item_id=999999)
    assert page.data["records"] == []
    assert page.data["truncated"] is True
    assert page.data["bytes_scanned"] <= 20_000
    assert page.data["has_more"] is True
    assert page.data["next_cursor"]


def test_following_the_cursor_after_a_truncated_scan_continues_rather_than_restarting(
    ctx, layout, monkeypatch
):
    """A truncated page is a page boundary, not a dead end. Without this the older history
    would be unreachable through the interface that exists to read it."""
    _fill_beyond_the_budget(layout, monkeypatch, budget=20_000)

    seen = 0
    cursor = None
    for _ in range(200):
        page = operations.read_log_page(ctx, item_id=1, limit=50, cursor=cursor)
        seen += len(page.data["records"])
        cursor = page.data["next_cursor"]
        if not page.data["has_more"]:
            break
    assert seen == 1600, "every record is reachable, one bounded page at a time"


def test_an_untruncated_page_says_it_was_not_truncated(ctx, layout):
    write_log(layout, TODAY, [record(n) for n in range(3)])
    page = operations.read_log_page(ctx)
    assert page.data["truncated"] is False
    assert page.data["bytes_scanned"] > 0


def test_a_cursor_from_the_previous_version_restarts_from_the_newest_page(ctx, layout):
    """The cursor payload changed from "(file, matches consumed)" to "(file, byte offset)".
    An old one is exactly the case ``_decode_cursor`` already documents: a cursor naming a
    page that may legitimately no longer exist, which restarts rather than erroring.
    """
    import base64

    write_log(layout, TODAY, [record(n) for n in range(3)])
    old_shape = json.dumps({"f": f"audit-{TODAY}.jsonl", "n": 2}, separators=(",", ":"))
    cursor = base64.urlsafe_b64encode(old_shape.encode()).decode().rstrip("=")

    page = operations.read_log_page(ctx, cursor=cursor)
    assert page.code == 0
    assert len(page.data["records"]) == 3


def test_a_cursor_whose_offset_is_zero_advances_to_the_previous_file(ctx, layout):
    """Zero means "this file is finished", not "start it again" — the difference between a
    page turn and an infinite loop."""
    write_log(layout, YESTERDAY, [record(n, ts=f"{YESTERDAY}T00:00:{n:02d}Z") for n in range(3)])
    write_log(layout, TODAY, [record(n, ts=f"{TODAY}T00:00:{n:02d}Z") for n in range(3)])

    cursor = operations._encode_cursor(f"audit-{TODAY}.jsonl", 0)
    page = operations.read_log_page(ctx, cursor=cursor)
    assert [r["ts"] for r in page.data["records"]] == [
        f"{YESTERDAY}T00:00:0{n}Z" for n in (2, 1, 0)
    ]


@pytest.mark.parametrize("offset", [10**9, 2**62, -1])
def test_a_cursor_offset_outside_the_file_is_clamped(ctx, layout, offset):
    """The cursor is a URL parameter, so its byte offset is client input.

    Unclamped, an offset past the end of the file sent ``_lines_backwards`` seeking and
    reading past EOF in 64 KiB steps. Those reads yield no lines, and the byte budget only
    counts lines that *are* yielded — so the budget never fired and the cost was linear in
    the offset rather than in the file. A cursor near ``2**63``, which ``_encode_cursor``
    will happily mint, would have held the request thread and the connection and audit handle
    it carries for years. Negative is the same mistake mirrored.
    """
    write_log(layout, TODAY, [record(n) for n in range(5)])
    cursor = operations._encode_cursor(f"audit-{TODAY}.jsonl", offset)

    started = time.monotonic()
    page = operations.read_log_page(ctx, cursor=cursor)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"a crafted cursor took {elapsed:.2f}s"
    assert page.data["bytes_scanned"] <= operations.LOG_SCAN_BUDGET_BYTES
    if offset < 0:
        # Below the start of the file is a page that covers nothing, not a page that starts
        # over — the file is finished, so the scan moves on and there is nothing older.
        assert page.data["records"] == []
    else:
        # Past the end is the end: the newest records, exactly as with no cursor at all.
        assert page.data["records"] == operations.read_log_page(ctx).data["records"]


def test_an_append_between_pages_does_not_repeat_a_record(ctx, layout):
    """The daemon writes to today's file between two requests of a page turn. A cursor
    counting matches from the end names a different record after an append; one naming a byte
    position does not."""
    write_log(layout, TODAY, [record(n) for n in range(6)])
    first = operations.read_log_page(ctx, limit=3)
    assert len(first.data["records"]) == 3

    path = layout.log_dir / f"audit-{TODAY}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record(99), separators=(",", ":")) + "\n")

    second = operations.read_log_page(ctx, limit=3, cursor=first.data["next_cursor"])
    seen = [r["ts"] for r in first.data["records"] + second.data["records"]]
    assert len(set(seen)) == len(seen), "no record appears twice across the append"


def test_the_web_says_when_the_scan_stopped_early(web, layout, monkeypatch):
    _fill_beyond_the_budget(layout, monkeypatch, budget=20_000)
    body = web.get("/log?item=999999").text
    assert "scan stopped" in body
    assert "older records" in body


def test_the_web_does_not_say_it_stopped_early_when_it_did_not(web, layout):
    write_log(layout, TODAY, [record(n) for n in range(3)])
    assert "scan stopped" not in web.get("/log").text


def test_the_last_page_reports_no_more(ctx, layout):
    write_log(layout, TODAY, [record(n) for n in range(3)])
    page = operations.read_log_page(ctx, limit=10)
    assert page.data["has_more"] is False
    assert page.data["next_cursor"] is None


def test_a_hand_edited_cursor_restarts_rather_than_erroring(ctx, layout):
    """The page it names may legitimately no longer exist, and the address bar is an input."""
    write_log(layout, TODAY, [record(n) for n in range(3)])
    page = operations.read_log_page(ctx, cursor="not-a-cursor")
    assert page.code == 0
    assert len(page.data["records"]) == 3


def test_the_web_offers_the_next_page_when_there_is_one(web, layout):
    write_log(layout, TODAY, [record(n) for n in range(150)])
    body = web.get("/log").text
    assert "older records" in body
    assert "cursor=" in body


def test_paging_preserves_the_active_filters(web, layout):
    write_log(layout, TODAY, [record(n, entity_id=1) for n in range(150)])
    body = web.get("/log?item=1").text
    assert "cursor=" in body and "item=1" in body


# -- FR-043: links -----------------------------------------------------------


def test_a_github_target_becomes_a_link(web, layout):
    write_log(
        layout,
        TODAY,
        [record(0, action="github.comment", target="jantman/demo#42")],
    )
    body = web.get("/log").text
    assert 'href="https://github.com/jantman/demo/issues/42"' in body


def test_a_github_url_already_in_the_detail_becomes_a_link(web, layout):
    write_log(
        layout,
        TODAY,
        [record(0, detail={"comment_url": "https://github.com/jantman/demo/pull/7"})],
    )
    assert 'href="https://github.com/jantman/demo/pull/7"' in web.get("/log").text


def test_a_non_github_url_in_a_record_is_never_linked(web, layout):
    """A record can carry an issue body. An arbitrary URL out of one must stay text."""
    write_log(
        layout,
        TODAY,
        [record(0, detail={"note": "see https://evil.example/steal", "target": "x"})],
    )
    body = web.get("/log").text
    assert 'href="https://evil.example/steal"' not in body
    assert "evil.example" in body, "it is still shown — as text, not as a link"


def test_a_work_item_record_links_to_its_page(web, layout, conn):
    item_id = seed_item(conn, state="ready")
    write_log(layout, TODAY, [record(0, entity_id=item_id)])
    # Since 009 every internal link states the visibility preference, so the href is
    # prefix-matched rather than compared whole.
    assert f'href="/item/{item_id}?' in web.get("/log").text


def test_a_web_component_record_renders_as_such(web, conn, layout):
    """FR-039 read back: which interface did this is answerable from the page."""
    item_id = seed_item(conn, state="interrupted")
    web.post_json(f"/item/{item_id}/abandon")
    body = web.get("/log").text
    assert "web.abandon" in body
    assert ">web<" in body


# -- SC-014: the bound -------------------------------------------------------


def test_a_first_page_is_bounded_against_a_hundred_thousand_records(ctx, layout):
    """SC-014, measured rather than asserted by construction.

    A day's file is a few megabytes; reading one or two satisfies any first page. The
    rejected alternative — indexing the log into SQLite — would be a second copy of the
    record of truth plus an indexer to keep it current, to speed up a view nobody loads
    in a loop.
    """
    write_log(layout, TODAY, [record(n % 3600) for n in range(100_000)])

    started = time.monotonic()
    page = operations.read_log_page(ctx, limit=100)
    elapsed = time.monotonic() - started

    assert len(page.data["records"]) == 100
    assert page.data["has_more"] is True
    assert elapsed < 2.0, f"first page took {elapsed:.2f}s against 100,000 records"


def test_the_page_size_is_bounded_even_when_a_caller_asks_for_more(ctx, layout):
    write_log(layout, TODAY, [record(n % 3600) for n in range(5000)])
    page = operations.read_log_page(ctx, limit=10**9)
    assert len(page.data["records"]) <= 1000
