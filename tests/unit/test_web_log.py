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
    """Skipped is not rejected: a record we cannot judge must be reported, not dropped."""
    write_log(
        layout,
        TODAY,
        [record(0), record(1, ts="not-a-timestamp"), record(2)],
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
    assert f'href="/item/{item_id}"' in web.get("/log").text


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
