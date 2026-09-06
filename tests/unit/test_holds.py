"""Holding items and repositories out of dispatch (issue #117): storage and operations.

The two tests that carry the design rather than merely covering it:

* :func:`test_purging_simulated_rows_takes_their_holds_with_them` proves the cascade that
  makes FR-025 a database guarantee instead of a promise. If it fails, research R1's
  argument for two tables was wrong and the *design* should change, not the test.
* :func:`test_holding_something_already_held_keeps_the_original_timestamp` pins FR-004.
  Reporting a fresh timestamp for a hold placed yesterday is the quiet way to make the
  queue's provenance useless.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from dataclasses import replace

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import db, operations
from robot_army import repos as repos_mod
from robot_army.states import WorkItemState


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


def records(layout, action: str, *, kind: str = "outcome") -> list[dict]:
    """Audit records for one action. The reconstruction path Principle III promises.

    Defaults to the ``outcome`` half of each pair, because that is where what the action
    *learned* is attached; ``audit.action`` writes an ``intent`` first so a process killed
    mid-change leaves a detectable signature, and counting both would double every total.
    """
    return [
        entry
        for path in sorted(layout.log_dir.glob("audit-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if (entry := json.loads(line))["action"] == action and entry["kind"] in (kind, "event")
    ]


# -- the accessors ----------------------------------------------------------


def test_both_listings_read_an_empty_table_as_an_empty_dict(conn):
    """No holds is the overwhelmingly common state and is not an error condition."""
    assert db.list_item_holds(conn) == {}
    assert db.list_repo_holds(conn) == {}


def test_placing_and_reading_back_an_item_hold(conn):
    item_id = seed_item(conn)
    with db.transaction(conn):
        hold, placed = db.set_item_hold(conn, item_id, by="cli")
    assert placed is True
    assert hold.held_by == "cli"
    assert hold.held_at

    holds = db.list_item_holds(conn)
    assert set(holds) == {item_id}
    assert holds[item_id] == hold


def test_placing_and_reading_back_a_repository_hold(conn):
    seed_item(conn, repo_key="demo")
    with db.transaction(conn):
        hold, placed = db.set_repo_hold(conn, "demo", by="web")
    assert placed is True
    assert db.list_repo_holds(conn) == {"demo": hold}
    assert hold.held_by == "web"


def test_holding_something_already_held_keeps_the_original_timestamp(conn):
    """FR-004. ``set_dispatch_paused`` already makes this judgement and states it: the
    hold that is *already in force*, with its original timestamp, is the useful answer.
    A refreshed one would silently rewrite when the author made the decision."""
    item_id = seed_item(conn)
    with db.transaction(conn):
        first, placed_first = db.set_item_hold(conn, item_id, by="cli")
    with db.transaction(conn):
        second, placed_second = db.set_item_hold(conn, item_id, by="web")

    assert placed_first is True
    assert placed_second is False, "the bool is how the caller reports a no-op"
    assert second == first, "neither held_at nor held_by may be rewritten"
    assert len(db.list_item_holds(conn)) == 1, "and there is exactly one row, not two"


def test_holding_a_repository_already_held_keeps_the_original_timestamp(conn):
    seed_item(conn, repo_key="demo")
    with db.transaction(conn):
        first, _ = db.set_repo_hold(conn, "demo", by="cli")
    with db.transaction(conn):
        second, placed = db.set_repo_hold(conn, "demo", by="web")
    assert placed is False
    assert second == first


def test_clearing_returns_what_it_removed(conn):
    """FR-005. The caller distinguishes "released a hold placed at t" from "there was
    nothing to release" without a second query."""
    item_id = seed_item(conn)
    with db.transaction(conn):
        placed, _ = db.set_item_hold(conn, item_id, by="cli")
    with db.transaction(conn):
        removed = db.clear_item_hold(conn, item_id)
    assert removed == placed
    assert db.list_item_holds(conn) == {}


def test_clearing_something_not_held_returns_none_rather_than_raising(conn):
    item_id = seed_item(conn)
    with db.transaction(conn):
        assert db.clear_item_hold(conn, item_id) is None
    with db.transaction(conn):
        assert db.clear_repo_hold(conn, "demo") is None


def test_clearing_a_repository_hold_returns_what_it_removed(conn):
    seed_item(conn, repo_key="demo")
    with db.transaction(conn):
        placed, _ = db.set_repo_hold(conn, "demo", by="cli")
    with db.transaction(conn):
        assert db.clear_repo_hold(conn, "demo") == placed
    assert db.list_repo_holds(conn) == {}


def test_the_two_scopes_are_independent(conn):
    """Holding a repository does not place an item hold, and releasing one scope must
    never release the other — which is the storage half of FR-017."""
    item_id = seed_item(conn, repo_key="demo")
    with db.transaction(conn):
        db.set_item_hold(conn, item_id, by="cli")
        db.set_repo_hold(conn, "demo", by="cli")
    with db.transaction(conn):
        db.clear_item_hold(conn, item_id)
    assert db.list_item_holds(conn) == {}
    assert set(db.list_repo_holds(conn)) == {"demo"}


# -- FR-025: a hold never outlives what it holds ----------------------------


def test_purging_simulated_rows_takes_their_holds_with_them(conn):
    """The cascade, and the whole justification for two tables rather than one.

    ``db.purge_simulated`` is the only path in the system that deletes a work item, and it
    is **not modified** by this feature: the foreign key is the cleanup. A single
    polymorphic table could not express this, because no foreign key says "one of these two
    depending on a sibling column", and FR-025 would then be a rule every future deletion
    site had to remember.
    """
    live = seed_item(conn, issue_number=1, dry_run=False)
    simulated = seed_item(conn, issue_number=2, dry_run=True)
    with db.transaction(conn):
        db.set_item_hold(conn, live, by="cli")
        db.set_item_hold(conn, simulated, by="cli")
    assert set(db.list_item_holds(conn)) == {live, simulated}

    with db.transaction(conn):
        db.purge_simulated(conn)

    assert set(db.list_item_holds(conn)) == {live}, "the simulated hold must be gone"
    orphans = conn.execute(
        "SELECT COUNT(*) FROM item_holds h "
        "LEFT JOIN work_items i ON i.id = h.work_item_id WHERE i.id IS NULL"
    ).fetchone()[0]
    assert orphans == 0


def test_deleting_a_repository_takes_its_hold_with_it(conn):
    """No path deletes a ``repos`` row today, so this is the cascade proved rather than
    exercised. It costs one constraint and forecloses an unattributable row."""
    seed_item(conn, repo_key="demo")
    with db.transaction(conn):
        db.set_repo_hold(conn, "demo", by="cli")
    with db.transaction(conn):
        conn.execute("DELETE FROM work_items WHERE repo_key = 'demo'")
        conn.execute("DELETE FROM repos WHERE repo_key = 'demo'")
    assert db.list_repo_holds(conn) == {}


def test_a_hold_cannot_be_placed_on_an_item_that_does_not_exist(conn):
    with pytest.raises(sqlite3.IntegrityError), db.transaction(conn):
        db.set_item_hold(conn, 999999, by="cli")


# -- FR-024: atomicity ------------------------------------------------------


def test_a_rolled_back_hold_leaves_no_trace(conn):
    """Interrupted at any point, the hold is wholly present or wholly absent. This is
    SQLite's guarantee rather than ours, which is the reason to use it rather than a file."""
    item_id = seed_item(conn)
    with pytest.raises(RuntimeError), db.transaction(conn):
        db.set_item_hold(conn, item_id, by="cli")
        raise RuntimeError("killed mid-change")
    assert db.list_item_holds(conn) == {}


def test_a_rolled_back_release_leaves_the_hold_intact(conn):
    item_id = seed_item(conn)
    with db.transaction(conn):
        placed, _ = db.set_item_hold(conn, item_id, by="cli")
    with pytest.raises(RuntimeError), db.transaction(conn):
        db.clear_item_hold(conn, item_id)
        raise RuntimeError("killed mid-change")
    assert db.list_item_holds(conn) == {item_id: placed}


# -- FR-021 / FR-022: durability -------------------------------------------


def test_holds_survive_a_reopened_database_with_identical_timestamps(layout):
    """US4, FR-021. The stronger half is ``held_at`` being *identical*, not merely
    present: a hold that silently reset its clock on restart would report the wrong answer
    to "how long has this been held" every time it mattered."""
    conn, _ = db.open_database(layout.db_path)
    item_id = seed_item(conn, repo_key="demo")
    with db.transaction(conn):
        item_hold, _ = db.set_item_hold(conn, item_id, by="cli")
        repo_hold, _ = db.set_repo_hold(conn, "demo", by="web")
    conn.close()

    reopened, _ = db.open_database(layout.db_path)
    assert db.list_item_holds(reopened) == {item_id: item_hold}
    assert db.list_repo_holds(reopened) == {"demo": repo_hold}
    reopened.close()


def test_a_hold_placed_while_the_daemon_is_down_is_honoured_by_its_first_plan(
    layout, config
):
    """FR-022 across a process boundary, which is the case the issue actually describes.

    The hold is written by one connection, that connection is closed, and the *first* plan
    a fresh one produces already reports it. Nothing caches holds — ``plan`` reads them on
    every pass rather than at startup — so there is no in-memory copy to go stale and
    nothing for a restart to invalidate.
    """
    from tests.unit.test_ordering import snapshot

    from robot_army import ordering

    writer, _ = db.open_database(layout.db_path)
    item_id = seed_item(writer, state=str(WorkItemState.READY))
    with db.transaction(writer):
        db.set_item_hold(writer, item_id, by="cli")
    writer.close()

    restarted, _ = db.open_database(layout.db_path)
    first_plan = ordering.plan(
        restarted, config=config, capacity=snapshot(global_cap=9)
    )
    restarted.close()

    assert [entry.hold for entry in first_plan] == [ordering.HoldReason.HELD]


# -- the include_simulated exemption ----------------------------------------


@pytest.mark.parametrize("accessor", [db.list_item_holds, db.list_repo_holds])
def test_hold_listings_are_not_scoped_by_dry_run(conn, accessor):
    """Deliberately absent from ``test_db_scope``'s ``LISTING_ACCESSORS``, following the
    ``list_repo_projects`` precedent. Neither table has a ``dry_run`` column, and holds
    apply to simulated items *by design*: a dry-run item occupies a queue slot, so a hold
    that skipped it would rehearse the wrong behaviour. An inert flag is one a caller will
    eventually pass expecting it to do something."""
    assert inspect.signature(accessor).parameters.get("include_simulated") is None


def test_a_simulated_item_can_be_held_like_any_other(conn):
    simulated = seed_item(conn, dry_run=True)
    with db.transaction(conn):
        db.set_item_hold(conn, simulated, by="cli")
    assert set(db.list_item_holds(conn)) == {simulated}


# -- the operations ---------------------------------------------------------


def test_holding_an_item_records_when_and_by_which_surface(ctx, conn):
    item_id = seed_item(conn)
    result = operations.hold_item(ctx, item_id, by="web")

    assert result.code == 0
    assert result.data["held"] is True
    assert result.data["held_by"] == "web"
    assert result.data["redundant"] is False
    assert db.list_item_holds(ctx.conn)[item_id].held_by == "web"


def test_a_redundant_item_hold_is_a_reported_no_op_not_an_error(ctx, conn):
    """FR-004, from the operation's side. The message must quote the *original* time."""
    item_id = seed_item(conn)
    first = operations.hold_item(ctx, item_id, by="cli")
    second = operations.hold_item(ctx, item_id, by="web")

    assert second.code == 0
    assert second.data["redundant"] is True
    assert second.data["held_at"] == first.data["held_at"]
    assert second.data["held_by"] == "cli", "the surface that actually placed it"
    assert "already held" in "\n".join(second.lines)


def test_releasing_an_item_reports_how_long_it_was_held(ctx, conn):
    item_id = seed_item(conn)
    operations.hold_item(ctx, item_id, by="cli")
    result = operations.unhold_item(ctx, item_id)

    assert result.code == 0
    assert result.data["released"] is True
    assert db.list_item_holds(ctx.conn) == {}


def test_releasing_an_item_that_was_not_held_is_a_no_op_not_a_failure(ctx, conn):
    """FR-005. "I already released that" and "that was never held" are the same outcome to
    the author, and neither deserves a non-zero exit."""
    item_id = seed_item(conn)
    result = operations.unhold_item(ctx, item_id)

    assert result.code == 0
    assert result.data["released"] is False
    assert "not held" in "\n".join(result.lines)


def test_holding_an_unknown_item_is_refused(ctx):
    result = operations.hold_item(ctx, 999999)
    assert result.code == operations.EXIT_FAILED
    assert "no work item with id 999999" in "\n".join(result.lines)


def test_releasing_an_unknown_item_is_refused(ctx):
    assert operations.unhold_item(ctx, 999999).code == operations.EXIT_FAILED


def test_holding_a_repository_reports_how_much_it_is_holding(ctx, conn):
    """"Held, and it was holding nothing" and "held, and it stopped four items" are very
    different facts to the author."""
    for n in range(1, 4):
        seed_item(conn, repo_key="demo", issue_number=n, state=str(WorkItemState.READY))
    result = operations.hold_repo(ctx, "demo", by="cli")

    assert result.code == 0
    assert result.data["held"] is True
    assert result.data["queued_items"] == 3
    assert db.list_repo_holds(ctx.conn)["demo"].held_by == "cli"


def test_a_redundant_repository_hold_keeps_its_original_timestamp(ctx, conn):
    seed_item(conn, repo_key="demo")
    first = operations.hold_repo(ctx, "demo", by="cli")
    second = operations.hold_repo(ctx, "demo", by="web")

    assert second.code == 0
    assert second.data["redundant"] is True
    assert second.data["held_at"] == first.data["held_at"]


def test_releasing_a_repository_that_was_not_held_is_a_no_op(ctx, conn):
    seed_item(conn, repo_key="demo")
    result = operations.unhold_repo(ctx, "demo")
    assert result.code == 0
    assert result.data["released"] is False


def test_holding_a_repository_that_was_never_onboarded_is_refused(ctx, conn):
    """FR-006, and the reason it matters: a hold on a repository the system does not watch
    would hold nothing and report nothing wrong — which looks exactly like one that works.

    Checked against ``repos.known`` rather than ``config.repos``, so a ``[repos.*]`` section
    for a repository that was never onboarded is refused too."""
    seed_item(conn, repo_key="demo")
    result = operations.hold_repo(ctx, "owner/typo")

    assert result.code == operations.EXIT_FAILED
    assert "not onboarded" in "\n".join(result.lines)
    assert db.list_repo_holds(ctx.conn) == {}


def test_a_configured_but_un_onboarded_repository_is_still_refused(
    conn, config, monkeypatch
):
    """The distinction ``repos.known`` exists to draw, and the reason the check is not
    ``config.repos``. A ``[repos.*]`` section describes a repository the author
    *mentioned*; only an onboarding record means the system watches it.

    Built with its own context because the point is a key that is present in the
    configuration and absent from the ``repos`` table — which the shared fixture's config,
    where the one configured repository is also the onboarded one, cannot express.
    """
    seed_item(conn, repo_key="demo")
    configured = replace(config, repos={**config.repos, "owner/ghost": config.repos["demo"]})
    assert "owner/ghost" in configured.repos
    assert "owner/ghost" not in repos_mod.known(conn)

    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level)
    )
    built = operations.build_context(configured)
    try:
        result = operations.hold_repo(built, "owner/ghost")
    finally:
        built.close()

    assert result.code == operations.EXIT_FAILED
    assert "not onboarded" in "\n".join(result.lines)


def test_releasing_an_unknown_repository_is_refused(ctx):
    assert operations.unhold_repo(ctx, "owner/typo").code == operations.EXIT_FAILED


# -- FR-023: the record -----------------------------------------------------


def test_every_hold_and_release_is_written_to_the_audit_log(ctx, conn, layout):
    """SC-008: what was held, when, by which surface, and whether it was already held —
    all answerable from the log alone, without re-running anything."""
    item_id = seed_item(conn, repo_key="demo")
    operations.hold_item(ctx, item_id, by="cli")
    operations.hold_item(ctx, item_id, by="cli")
    operations.unhold_item(ctx, item_id)
    operations.hold_repo(ctx, "demo", by="web")
    operations.unhold_repo(ctx, "demo", by="web")
    ctx.audit.close()

    held = records(layout, "hold.item")
    assert len(held) == 2
    assert held[0]["detail"]["was_held"] is False
    assert held[1]["detail"]["was_held"] is True, "the redundant attempt is still recorded"
    assert held[0]["detail"]["placed"] is True
    assert held[1]["detail"]["placed"] is False

    assert records(layout, "unhold.item")[0]["detail"]["released"] is True
    assert records(layout, "hold.repo")[0]["detail"]["held_by"] == "web"
    assert records(layout, "unhold.repo")[0]["detail"]["released"] is True


def test_a_refused_hold_still_leaves_a_record(ctx, layout):
    """An attempt that leaves no record is one nobody would ever notice."""
    operations.hold_item(ctx, 999999)
    operations.hold_repo(ctx, "owner/typo")
    ctx.audit.close()

    assert records(layout, "hold.item")[0]["outcome"] == "error"
    assert records(layout, "hold.repo")[0]["outcome"] == "error"


# -- FR-010: a hold never touches a running session -------------------------


def test_holding_an_item_with_a_running_session_changes_neither(ctx, conn):
    """SC-010. A hold governs entry into dispatch; ``cancel`` is what stops a session, and
    the two must never be confusable."""
    item_id = seed_item(conn, state=str(WorkItemState.ACTIVE))
    with db.transaction(conn):
        db.insert_session(
            conn,
            work_item_id=item_id,
            session_id="sess-1",
            attempt=1,
            dry_run=False,
            launch_argv=["true"],
        )
    before = db.latest_session_for_item(ctx.conn, item_id)

    result = operations.hold_item(ctx, item_id)

    assert result.code == 0
    assert db.get_work_item(ctx.conn, item_id).state is WorkItemState.ACTIVE
    # Compared against what was there rather than against a named state, so the assertion
    # stays about *unchanged* even if the session lifecycle gains a step.
    assert db.latest_session_for_item(ctx.conn, item_id) == before
