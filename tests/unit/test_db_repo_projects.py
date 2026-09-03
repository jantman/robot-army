"""The ``repo_projects`` accessors and the board-facts writer (issue #48, T006).

The two behaviours worth pinning here are the ones a caller would otherwise have to
remember: a missing row reads as *unresolved and never read* rather than as ``None``, and
writing a snapshot **clears** the items it does not mention.
"""

from __future__ import annotations

from tests.conftest import seed_item

from robot_army import db
from robot_army.models import RepoProject
from robot_army.states import utcnow


def _onboard(conn, *keys: str) -> None:
    """The `repos` rows `repo_projects` keys off. The foreign key is deliberate: a board
    resolution for a repository nobody onboarded is a row about nothing."""
    with db.transaction(conn):
        for key in keys:
            conn.execute(
                "INSERT OR IGNORE INTO repos (repo_key, onboarded_at, "
                "fingerprint_approved_at) VALUES (?, ?, ?)",
                (key, utcnow(), utcnow()),
            )


def test_a_repository_with_no_row_reads_as_unresolved(conn):
    _onboard(conn, "jantman/demo")

    state = db.get_repo_project(conn, "jantman/demo")

    assert state.repo_key == "jantman/demo"
    assert state.project_id is None
    assert state.last_read_at is None
    assert state.consecutive_failures == 0
    assert state.governs is False


def test_saving_twice_updates_rather_than_duplicates(conn):
    _onboard(conn, "jantman/demo")
    db.save_repo_project(
        conn,
        RepoProject(
            repo_key="jantman/demo",
            project_id="PVT_1",
            project_number=3,
            project_title="robot-army",
            column_name="Ready",
            project_source="discovered",
            column_source="discovered",
            resolved_at="2026-09-02T00:00:00Z",
        ),
    )
    db.save_repo_project(
        conn,
        RepoProject(
            repo_key="jantman/demo",
            project_id="PVT_2",
            project_number=4,
            column_name="Todo",
            resolved_at="2026-09-02T01:00:00Z",
        ),
    )

    assert conn.execute("SELECT COUNT(*) FROM repo_projects").fetchone()[0] == 1
    state = db.get_repo_project(conn, "jantman/demo")
    assert state.project_id == "PVT_2"
    assert state.column_name == "Todo"


def test_saving_writes_the_nulls_too(conn):
    """A partial update would let a stale title outlive the project it named."""
    _onboard(conn, "jantman/demo")
    db.save_repo_project(
        conn,
        RepoProject(
            repo_key="jantman/demo",
            project_id="PVT_1",
            project_title="robot-army",
            column_name="Ready",
            resolved_at="2026-09-02T00:00:00Z",
        ),
    )
    db.save_repo_project(
        conn,
        RepoProject(repo_key="jantman/demo", unresolved_reason="two projects linked"),
    )

    state = db.get_repo_project(conn, "jantman/demo")
    assert state.project_title is None
    assert state.resolved_at is None
    assert state.unresolved_reason == "two projects linked"
    assert state.governs is False


def test_governs_needs_both_a_resolution_and_a_read(conn):
    """A resolved board that has never been read orders nothing and holds nothing."""
    resolved_only = RepoProject(repo_key="a", resolved_at="2026-09-02T00:00:00Z")
    read_only = RepoProject(repo_key="b", last_read_at="2026-09-02T00:00:00Z")
    both = RepoProject(
        repo_key="c", resolved_at="2026-09-02T00:00:00Z", last_read_at="2026-09-02T00:00:00Z"
    )

    assert resolved_only.governs is False
    assert read_only.governs is False
    assert both.governs is True


def test_list_returns_every_row_keyed_by_repository(conn):
    _onboard(conn, "jantman/demo", "jantman/other")
    db.save_repo_project(conn, RepoProject(repo_key="jantman/demo", project_id="PVT_1"))
    db.save_repo_project(conn, RepoProject(repo_key="jantman/other", project_id="PVT_2"))

    everything = db.list_repo_projects(conn)

    assert set(everything) == {"jantman/demo", "jantman/other"}
    assert everything["jantman/other"].project_id == "PVT_2"


def test_board_facts_rank_the_column_and_name_the_others(conn):
    first = seed_item(conn, issue_number=10)
    second = seed_item(conn, issue_number=11)
    parked = seed_item(conn, issue_number=12)

    db.apply_board_facts(
        conn,
        "demo",
        ranked={11: 1, 10: 2},
        elsewhere={12: "Backlog"},
        column_name="Ready",
    )

    assert db.get_work_item(conn, second).board_position == 1
    assert db.get_work_item(conn, second).board_column == "Ready"
    assert db.get_work_item(conn, first).board_position == 2
    assert db.get_work_item(conn, parked).board_column == "Backlog"
    assert db.get_work_item(conn, parked).board_position is None


def test_a_snapshot_clears_the_items_it_no_longer_mentions(conn):
    """Clearing is half the job. A card removed from the board must stop being ranked and
    must stop being held, and an update that only wrote what it saw would leave yesterday's
    answer in place for exactly the items whose answer changed."""
    item = seed_item(conn, issue_number=10)
    db.apply_board_facts(
        conn, "demo", ranked={10: 1}, elsewhere={}, column_name="Ready"
    )
    assert db.get_work_item(conn, item).board_position == 1

    db.apply_board_facts(
        conn, "demo", ranked={}, elsewhere={}, column_name="Ready"
    )

    assert db.get_work_item(conn, item).board_column is None
    assert db.get_work_item(conn, item).board_position is None


def test_another_repositorys_items_are_untouched(conn):
    mine = seed_item(conn, issue_number=10)
    db.apply_board_facts(
        conn, "demo", ranked={10: 1}, elsewhere={}, column_name="Ready"
    )

    db.apply_board_facts(
        conn, "elsewhere", ranked={}, elsewhere={}, column_name="Ready"
    )

    assert db.get_work_item(conn, mine).board_position == 1


def test_simulated_items_are_ordered_like_any_other(conn):
    """A dry-run item occupies a queue position, so it is ranked like any other. The read
    that produced the snapshot made no outward change, so there is nothing to withhold."""
    simulated = seed_item(conn, issue_number=10, dry_run=True)

    db.apply_board_facts(
        conn, "demo", ranked={10: 1}, elsewhere={}, column_name="Ready"
    )

    assert db.get_work_item(conn, simulated).board_position == 1
