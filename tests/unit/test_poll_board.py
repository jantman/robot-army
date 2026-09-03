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


def test_a_repository_that_never_had_a_board_persists_nothing(conn, config, audit, layout):
    """Found in review. Most repositories have no board and never will, so this is the
    ordinary case — and it must leave no row and no record.

    A row would make `_say_projects` print "not ordered by a board" for every repository
    that has none, forever, contradicting that function's own promise to skip them. A
    record would put an error a minute in the log for the normal state of the system,
    burying the ones that mean something.
    """
    onboard(conn)
    reader = reader_with()  # the fake's default: absent, nothing linked

    stored = run(conn, config, audit, reader)

    assert stored.last_read_at is None
    assert stored.unresolved_reason is None
    assert conn.execute("SELECT COUNT(*) FROM repo_projects").fetchone()[0] == 0
    written = "".join(p.read_text() for p in layout.log_dir.glob("*.jsonl"))
    assert "poll.board" not in written


def test_a_stale_ambiguity_is_cleared_when_the_projects_are_unlinked(conn, config, audit):
    """Found in review, round three. The short-circuit's condition matched two different
    situations and only one of them was safe.

    `resolved_at` and `last_read_at` are both NULL for a genuinely untouched repository
    *and* for one carrying an `unresolved_reason` from an earlier ambiguity — nothing on
    that path ever sets them. Returning early on the second froze the stale reason in
    place, and `status` would keep printing "two projects are linked" long after the author
    had unlinked both. That is the same stale-surface failure the short-circuit exists to
    avoid, reached from a different prior state.
    """
    onboard(conn)
    reader = reader_with(res=ProjectResolution(reason="two projects are linked"))
    stale = run(conn, config, audit, reader)
    assert stale.unresolved_reason == "two projects are linked"

    reader.resolution = ProjectResolution(absent=True, reason="no project is linked")
    cleared = run(conn, config, audit, reader)

    assert cleared.unresolved_reason is None, "the reason is no longer true"
    assert not cleared.governs


def test_a_stale_transport_error_and_its_backoff_are_cleared_too(conn, config, audit):
    """Same guard, the other prior state. A repository whose reads were failing and which
    now simply has no board must not keep its error and its backoff forever."""
    onboard(conn)
    reader = reader_with(raise_on_resolve=TransportError("GitHub is down"))
    failed = run(conn, config, audit, reader)
    assert failed.last_error == "GitHub is down"
    assert failed.backoff_until is not None

    with db.transaction(conn):
        db.save_repo_project(conn, replace(failed, backoff_until=None))
    reader.raise_on_resolve = None
    reader.resolution = ProjectResolution(absent=True, reason="no project is linked")
    cleared = run(conn, config, audit, reader)

    assert cleared.last_error is None
    assert cleared.consecutive_failures == 0
    assert cleared.backoff_until is None


def test_clearing_stale_state_is_recorded_once_then_goes_quiet(
    conn, config, audit, layout
):
    """The clearing is a change and is recorded. The passes after it are not, because by
    then the repository is simply one of the many that have no board."""
    onboard(conn)
    reader = reader_with(res=ProjectResolution(reason="two projects are linked"))
    run(conn, config, audit, reader)
    reader.resolution = ProjectResolution(absent=True, reason="no project is linked")
    run(conn, config, audit, reader)

    for path in layout.log_dir.glob("*.jsonl"):
        path.write_text("")
    run(conn, config, audit, reader)

    written = "".join(p.read_text() for p in layout.log_dir.glob("*.jsonl"))
    assert "poll.board" not in written


def test_a_board_that_goes_away_is_reported_as_a_change(conn, config, audit, layout):
    """The narrow other half. A repository that *did* have a board and no longer does has
    genuinely changed, and the author should hear about it — so it is persisted and
    recorded, unlike the never-had-one case above."""
    onboard(conn)
    reader = reader_with(res=resolution(), board=snapshot([]))
    run(conn, config, audit, reader)

    reader.resolution = ProjectResolution(
        absent=True, reason="no project is linked to demo"
    )
    stored = run(conn, config, audit, reader)

    assert stored.unresolved_reason == "no project is linked to demo"
    assert not stored.governs
    written = "".join(p.read_text() for p in layout.log_dir.glob("*.jsonl"))
    assert "poll.board" in written


def test_an_absent_board_is_never_recorded_as_an_error(conn, config, audit, layout):
    """An `outcome="error"` a minute for a repository with no board is how an error grep
    stops being useful."""
    import json

    onboard(conn)
    reader = reader_with(res=resolution(), board=snapshot([]))
    run(conn, config, audit, reader)
    reader.resolution = ProjectResolution(absent=True, reason="no project is linked")
    run(conn, config, audit, reader)

    records = [
        json.loads(line)
        for path in layout.log_dir.glob("*.jsonl")
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    board = [r for r in records if r.get("action") == "poll.board"]
    assert board, "the change must still be recorded"
    assert all(r["outcome"] == "ok" for r in board)


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


def test_a_repository_that_becomes_ambiguous_is_released_not_frozen(conn, config, audit):
    """The board stops governing — it does not keep governing with a stale snapshot.

    Found in review, and the distinction is the one this branch gets wrong most easily.
    A *transport* failure keeps `resolved_at`, so its repository stays governed by an old
    snapshot (FR-025's first option). A *resolution* failure does not carry `resolved_at`
    forward, so `governs` goes false and the repository falls back to the configured order
    with nothing held (FR-025's second option). Both are permitted; recording the wrong
    one is not.

    The stored board columns survive, and that is deliberate rather than sloppy: they cost
    nothing while the repository is ungoverned, nothing reads them, and they are overwritten
    whole by the next successful read.
    """
    onboard(conn)
    seed_item(conn, issue_number=48, state=str(WorkItemState.READY))
    reader = reader_with(res=resolution(), board=snapshot([48]))
    first = run(conn, config, audit, reader)
    assert first.governs

    reader.resolution = ProjectResolution(reason="two projects are linked")
    stored = run(conn, config, audit, reader)

    assert stored.last_read_at == first.last_read_at
    assert stored.unresolved_reason == "two projects are linked"
    assert not stored.governs, "an unresolvable board governs nothing"
    assert db.list_work_items(conn)[0].board_position == 1


def test_losing_a_resolution_releases_the_order_and_the_holds(conn, config, audit):
    """The consequence the log now claims, asserted through `ordering.plan` itself rather
    than through the row — because the row is not what the author experiences."""
    from robot_army import capacity, ordering

    onboard(conn)
    ranked = seed_item(conn, issue_number=48, state=str(WorkItemState.READY))
    parked = seed_item(conn, issue_number=20, state=str(WorkItemState.READY))
    reader = reader_with(res=resolution(), board=snapshot([48], {20: "Backlog"}))
    run(conn, config, audit, reader)

    snap = capacity.CapacitySnapshot(
        observable=True, degraded=False, total=0, ours=(), others=0, global_cap=9
    )
    held = ordering.plan(conn, config=config, capacity=snap)
    assert any(e.hold is ordering.HoldReason.OFF_COLUMN for e in held)

    reader.resolution = ProjectResolution(reason="two projects are linked")
    run(conn, config, audit, reader)

    entries = ordering.plan(conn, config=config, capacity=snap)
    assert all(e.hold is not ordering.HoldReason.OFF_COLUMN for e in entries), (
        "an ungoverned repository holds nothing for its column"
    )
    assert [e.item.id for e in entries] == [ranked, parked], (
        "and orders by discovery time again, not by the board it can no longer identify"
    )


def test_the_fallback_record_names_the_right_consequence(conn, config, audit, layout):
    """Two failure paths, two different consequences, and the log must not confuse them."""
    onboard(conn)
    reader = reader_with(res=resolution(), board=snapshot([]))
    run(conn, config, audit, reader)

    reader.resolution = ProjectResolution(reason="two projects are linked")
    run(conn, config, audit, reader)
    written = "".join(p.read_text() for p in layout.log_dir.glob("*.jsonl"))
    assert "now ungoverned" in written
    assert "stays in force" not in written

    reader.resolution = resolution()
    run(conn, config, audit, reader)
    reader.raise_on_board = TransportError("down")
    run(conn, config, audit, reader)
    written = "".join(p.read_text() for p in layout.log_dir.glob("*.jsonl"))
    assert "stays in force" in written


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
