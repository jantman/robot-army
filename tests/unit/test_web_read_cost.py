"""What a served read *costs*, counted rather than asserted by construction (RA-14).

The finding these tests exist for is not a disclosure. A page on another site loops
``fetch('http://127.0.0.1:8420/interrupted', {mode:'no-cors'})``; the response is opaque to
it and it never reads a byte — but the work happens on this machine anyway, and the work was
several ``git`` subprocesses per displayed item, whole audit files read into memory, and a
``/proc`` enumeration per page (twice on ``/queue``). So the assertions here are counts:
how many version-control observations a render made, how many capacity snapshots, how many
bytes of audit log.

They live in their own module because their subject cuts across the others. A test that
counts ``git`` invocations across a render of ``/interrupted`` belongs neither in the render
tests nor in the signal tests, and putting it in either would hide it from the other.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import capacity as capacity_mod
from robot_army import db, operations
from robot_army.web import pages, server


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


@pytest.fixture
def count_conditions(monkeypatch):
    """Count every ``worktree.condition`` — i.e. every fork/exec of ``git`` for a signal.

    Wraps rather than replaces, so the signals still come out real and a test asserting on
    the rendered page is asserting on the values production would show.
    """
    calls: list[tuple] = []
    real = operations.worktree.condition

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(operations.worktree, "condition", counting)
    return calls


@pytest.fixture
def count_snapshots(monkeypatch):
    """Count every ``capacity.snapshot`` — a session registry read plus a ``/proc`` walk.

    Patched in every module that holds a reference to it, because each imports the module
    under its own alias and a patch on one of them would count only some of the calls.
    """
    calls: list[str] = []
    real = capacity_mod.snapshot

    def counting(*args, **kwargs):
        calls.append("snapshot")
        return real(*args, **kwargs)

    monkeypatch.setattr(capacity_mod, "snapshot", counting)
    monkeypatch.setattr(pages.capacity_mod, "snapshot", counting)
    monkeypatch.setattr(server.capacity_mod, "snapshot", counting)
    return calls


CROSS_SITE_READ = {"sec-fetch-site": "cross-site", "origin": "https://evil.example"}


def _interrupted_item(conn, issue_number: int) -> int:
    """An interrupted item with a worktree and a branch — the shape that costs git."""
    item_id = seed_item(conn, state="interrupted", issue_number=issue_number)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item_id, worktree_path="/nowhere", branch=f"robot-army/{item_id}"
        )
    return item_id


# -- US1: a refused read does no work ---------------------------------------


def test_a_cross_site_read_does_none_of_the_work_it_asked_for(
    web, conn, count_conditions, count_snapshots
):
    """The finding, stated as a count.

    The refusal has to *precede* the work, not merely accompany it. A check that ran after
    the view was rendered would return the same 403 and would fix nothing, because the whole
    attack is the work rather than the response.
    """
    for issue_number in range(1, 4):
        _interrupted_item(conn, issue_number)

    for _ in range(5):
        assert web.get("/interrupted", headers=CROSS_SITE_READ).status == 403

    assert count_conditions == [], "a refused read must fork no git"
    assert count_snapshots == [], "a refused read must not enumerate /proc"


def test_the_same_read_without_the_header_does_do_the_work(web, conn, count_conditions):
    """The other half of the assertion above: the count is zero because the read was refused,
    not because the fixture cannot see the calls."""
    _interrupted_item(conn, 1)

    assert web.get("/interrupted").status == 200
    assert count_conditions, "an honest read still observes the checkout"


# -- US2: the interrupted view stops forking git per card -------------------


def test_two_renders_inside_the_window_cost_one_renders_worth_of_git(
    web, conn, count_conditions
):
    """SC-004. Ten items, two renders inside the window, one render's worth of subprocesses."""
    for issue_number in range(1, 11):
        _interrupted_item(conn, issue_number)

    assert web.get("/interrupted").status == 200
    after_first = len(count_conditions)
    assert after_first >= 10, "the first render observes every card"

    assert web.get("/interrupted").status == 200
    assert len(count_conditions) == after_first, "the second render adds none"


# -- US3: a log page reads bounded bytes -------------------------------------


def _write_big_log(layout, *, days: int, per_day: int) -> int:
    """Write ``days`` daily files of ``per_day`` padded records. Returns the total byte size.

    The record carries a ``detail`` block, because a real one does and the whole question is
    how many *bytes* a scan reads. One day's text is built once and re-stamped per file, so a
    hundred-megabyte fixture costs a few writes rather than a million ``json.dumps`` calls.
    """
    layout.log_dir.mkdir(parents=True, exist_ok=True)
    midnight = datetime.now(UTC)
    total = 0
    for offset in range(days):
        day = (midnight - timedelta(days=offset)).strftime("%Y-%m-%d")
        lines = [
            json.dumps(
                {
                    "ts": f"{day}T{n // 3600 % 24:02d}:{n // 60 % 60:02d}:{n % 60:02d}Z",
                    "component": "daemon",
                    "kind": "event",
                    "action": "state.work_item",
                    "outcome": "ok",
                    "entity_type": "work_item",
                    "entity_id": 1,
                    "detail": {"note": "x" * 300},
                },
                separators=(",", ":"),
            )
            for n in range(per_day)
        ]
        text = "\n".join(lines) + "\n"
        (layout.log_dir / f"audit-{day}.jsonl").write_text(text, encoding="utf-8")
        total += len(text.encode("utf-8"))
    return total


def test_a_filter_matching_nothing_reads_no_more_than_the_budget(ctx, layout):
    """SC-005. Before this bound, this request read every audit file in the directory, whole.

    The measurement is the point. SC-014 already measures the *first page* and has always
    passed, which is exactly why the unbounded case went unnoticed: the fast path was the one
    under test, and the slow path was reachable by anyone with a tab open.
    """
    written = _write_big_log(layout, days=10, per_day=27_000)
    assert written > 100_000_000, "SC-005 measures against at least 100 MB"

    started = time.monotonic()
    page = operations.read_log_page(ctx, item_id=999999)
    elapsed = time.monotonic() - started

    assert page.data["records"] == []
    assert page.data["bytes_scanned"] <= operations.LOG_SCAN_BUDGET_BYTES
    assert page.data["truncated"] is True, "and it must say it stopped early"
    assert elapsed < 2.0, f"a filter matching nothing took {elapsed:.2f}s"


# -- US4: one machine observation per rendered page --------------------------


@pytest.mark.parametrize("path", ["/queue", "/active", "/interrupted", "/anomalies"])
def test_a_rendered_view_observes_the_machine_once(web, conn, count_snapshots, path):
    """``/queue`` did it twice: once for the chrome's capacity pill, once for its own block."""
    seed_item(conn, state="active")
    assert web.get(path).status == 200
    assert len(count_snapshots) == 1, f"{path} observed the machine {len(count_snapshots)} times"


def test_an_item_view_observes_the_machine_once(web, conn, count_snapshots):
    item_id = seed_item(conn, state="interrupted")
    assert web.get(f"/item/{item_id}").status == 200
    assert len(count_snapshots) == 1


@pytest.mark.parametrize("path", ["/nowhere-at-all", "/style.css", "/app.js"])
def test_a_response_that_does_not_route_observes_the_machine_not_at_all(
    web, count_snapshots, path
):
    """404s and the static assets render with no database behind them, and must stay that way
    — a 503 that cannot render is not a 503."""
    web.get(path)
    assert count_snapshots == []
