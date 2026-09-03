"""The per-repository board pass inside the poll (issue #48, T021, T030).

The properties that matter here are the ones a failure would make invisible: a snapshot
**clears** what it no longer mentions, a failed read leaves the previous snapshot in force
rather than discarding it, and a repository with the setting off is never asked about.
"""

from __future__ import annotations

from dataclasses import replace

from tests.conftest import FakeIssueReader, make_boundaries, seed_item

from robot_army import db, poll
from robot_army.boundaries import BoardEntry, BoardSnapshot, ProjectResolution, TransportError
from robot_army.models import RepoProject
from robot_army.states import WorkItemState


def onboard(conn, key="demo"):
    with db.transaction(conn):
        db.upsert_repo(conn, repo_key=key, settings_fingerprint=None, trust_verified=True)


def resolution(**overrides):
    fields = {
        "project_id": "PVT_3",
        "project_number": 3,
        "project_title": "robot-army",
        "project_url": "https://github.com/users/jantman/projects/3",
        "project_source": "discovered",
        "column_name": "Ready",
        "column_source": "discovered",
    }
    fields.update(overrides)
    return ProjectResolution(**fields)


def snapshot(ranked=(), elsewhere=None, *, repo="demo"):
    return BoardSnapshot(
        project_id="PVT_3",
        project_number=3,
        project_title="robot-army",
        project_url="https://github.com/users/jantman/projects/3",
        column_name="Ready",
        ranked=tuple(
            BoardEntry(issue_number=number, repo_key=repo, position=index)
            for index, number in enumerate(ranked, start=1)
        ),
        elsewhere=dict(elsewhere or {}),
    )


def reader_with(*, res=None, board=None, raise_on_resolve=None, raise_on_board=None):
    reader = FakeIssueReader()
    reader.resolution = res
    reader.board = board
    reader.raise_on_resolve = raise_on_resolve
    reader.raise_on_board = raise_on_board
    return reader


def run(conn, config, audit, reader, *, repo="demo", onboarded=True):
    return poll.read_board(
        conn,
        boundaries=make_boundaries(audit, reader=reader),
        audit=audit,
        config=config,
        repo_key=repo,
        onboarded=onboarded,
    )


# -- the happy path ---------------------------------------------------------


def test_a_successful_read_ranks_the_column_and_names_the_others(conn, config, audit):
    onboard(conn)
    seed_item(conn, issue_number=48, state=str(WorkItemState.READY))
    seed_item(conn, issue_number=20, state=str(WorkItemState.READY))
    reader = reader_with(res=resolution(), board=snapshot([48], {20: "Backlog"}))

    stored = run(conn, config, audit, reader)

    assert stored.governs
    assert stored.column_name == "Ready"
    assert stored.project_source == "discovered"
    items = {i.issue_number: i for i in db.list_work_items(conn)}
    assert items[48].board_position == 1
    assert items[48].board_column == "Ready"
    assert items[20].board_column == "Backlog"
    assert items[20].board_position is None


def test_a_later_snapshot_clears_what_it_no_longer_mentions(conn, config, audit):
    onboard(conn)
    seed_item(conn, issue_number=48, state=str(WorkItemState.READY))
    reader = reader_with(res=resolution(), board=snapshot([48]))
    run(conn, config, audit, reader)

    reader.board = snapshot([])
    run(conn, config, audit, reader)

    item = db.list_work_items(conn)[0]
    assert item.board_column is None
    assert item.board_position is None


# -- when the board is not read ---------------------------------------------


def test_a_repository_with_ordering_off_is_never_asked(conn, config, audit):
    onboard(conn)
    off = replace(config, dispatch=replace(config.dispatch, project_ordering=False))
    reader = reader_with(res=resolution(), board=snapshot([1]))

    stored = run(conn, off, audit, reader)

    assert reader.resolve_calls == []
    assert stored.last_read_at is None


def test_an_unonboarded_repository_is_never_asked(conn, config, audit):
    """`repo_projects` references `repos`; a resolution for a repository nobody onboarded
    is a row about nothing."""
    reader = reader_with(res=resolution(), board=snapshot([1]))

    stored = run(conn, config, audit, reader, onboarded=False)

    assert reader.resolve_calls == []
    assert stored.last_read_at is None


def test_a_repository_in_backoff_is_skipped_and_the_skip_is_recorded(
    conn, config, audit, layout
):
    onboard(conn)
    with db.transaction(conn):
        db.save_repo_project(
            conn, RepoProject(repo_key="demo", backoff_until="2999-01-01T00:00:00Z")
        )
    reader = reader_with(res=resolution(), board=snapshot([1]))

    run(conn, config, audit, reader)

    assert reader.resolve_calls == []
    written = "".join(p.read_text() for p in layout.log_dir.glob("*.jsonl"))
    assert "in backoff until" in written


# -- failure keeps the previous answer --------------------------------------


def test_a_failed_read_leaves_the_previous_snapshot_in_force(conn, config, audit):
    """FR-025. An order the author arranged yesterday beats no order at all, and a
    repository that stalled because GitHub blinked would be the worse failure."""
    onboard(conn)
    seed_item(conn, issue_number=48, state=str(WorkItemState.READY))
    reader = reader_with(res=resolution(), board=snapshot([48]))
    first = run(conn, config, audit, reader)

    reader.raise_on_board = TransportError("GitHub is down")
    stored = run(conn, config, audit, reader)

    assert stored.last_read_at == first.last_read_at
    assert stored.last_error == "GitHub is down"
    assert stored.consecutive_failures == 1
    assert stored.backoff_until is not None
    assert stored.governs, "a stale board still governs"
    assert db.list_work_items(conn)[0].board_position == 1


def test_repeated_failures_back_off_further(conn, config, audit):
    """The escalation only happens once the previous backoff has expired — which is the
    point of the backoff. A pass inside the window is skipped and does not count as a
    second failure, so a wedged board cannot inflate its own delay to the ceiling in
    seconds."""
    onboard(conn)
    reader = reader_with(raise_on_resolve=TransportError("down"))

    first = run(conn, config, audit, reader)
    assert first.consecutive_failures == 1
    assert run(conn, config, audit, reader).consecutive_failures == 1, (
        "a pass inside the backoff window must be skipped, not counted"
    )

    with db.transaction(conn):
        db.save_repo_project(conn, replace(first, backoff_until=None))
    second = run(conn, config, audit, reader)

    assert second.consecutive_failures == 2
    assert second.backoff_until is not None


def test_the_fallback_is_recorded_so_staleness_is_never_silent(
    conn, config, audit, layout
):
    onboard(conn)
    reader = reader_with(res=resolution(), board=snapshot([]))
    run(conn, config, audit, reader)

    reader.raise_on_board = TransportError("down")
    run(conn, config, audit, reader)

    written = "".join(p.read_text() for p in layout.log_dir.glob("*.jsonl"))
    assert "poll.board.fallback" in written


def test_a_first_ever_failure_leaves_the_repository_ungoverned(conn, config, audit):
    """Nothing was ever read, so nothing is ordered and nothing is held (FR-014)."""
    onboard(conn)
    reader = reader_with(raise_on_resolve=TransportError("down"))

    stored = run(conn, config, audit, reader)

    assert stored.last_read_at is None
    assert not stored.governs


# -- resolution failures are not transport failures -------------------------


def test_an_unresolved_board_is_recorded_without_backing_off(conn, config, audit):
    """There is nothing to retry away: only the author can clear an ambiguity, and backing
    off would delay recovery once they had."""
    onboard(conn)
    reader = reader_with(res=ProjectResolution(reason="two projects are linked"))

    stored = run(conn, config, audit, reader)

    assert stored.unresolved_reason == "two projects are linked"
    assert stored.consecutive_failures == 0
    assert stored.backoff_until is None
    assert not stored.governs


def test_a_repository_that_becomes_ambiguous_keeps_its_last_order(conn, config, audit):
    onboard(conn)
    seed_item(conn, issue_number=48, state=str(WorkItemState.READY))
    reader = reader_with(res=resolution(), board=snapshot([48]))
    first = run(conn, config, audit, reader)

    reader.resolution = ProjectResolution(reason="two projects are linked")
    stored = run(conn, config, audit, reader)

    assert stored.last_read_at == first.last_read_at
    assert stored.unresolved_reason == "two projects are linked"
    assert db.list_work_items(conn)[0].board_position == 1


# -- the configured values reach the reader ---------------------------------


def test_the_configured_project_and_column_are_passed_through(conn, config, audit):
    from robot_army.config import RepoConfig

    onboard(conn)
    section = RepoConfig(
        key="demo",
        path=None,
        base_branch="main",
        project="7",
        project_column="Queued",
    )
    configured = replace(config, repos={**config.repos, "demo": section})
    reader = reader_with(res=resolution(), board=snapshot([]))

    run(conn, configured, audit, reader)

    assert reader.resolve_calls == [("demo", "7", "Queued")]
