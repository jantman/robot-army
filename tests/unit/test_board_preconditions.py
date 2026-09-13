"""The board's startup checks, and what their failure does and does not stop (T024).

Two properties carry the design weight here:

* A precondition failure disables **ingestion only** (R10). An unrelated board
  misconfiguration must not take down dispatch of issues the author wrote themselves, and
  a test that only checked "ingestion stopped" would let that regression through.
* **Extra members never gate anything** (FR-004a). An earlier draft of R10 refused to
  ingest unless the author was the board's only member. That was removed at the author's
  direction: it substituted the system's judgement for the author's about their own board,
  and it is disproportionate to what a second member can do — put a card on the board,
  which becomes an *unlabelled* issue that only the author can turn into a session.
"""

from __future__ import annotations

import pytest
from tests.conftest import (
    FakeCardReader,
    make_board_boundaries,
    make_board_info,
    make_card,
    with_ignore_lists,
)

from robot_army import db, intake
from robot_army.boundaries import BoardInfo, TransportError


def board(**overrides) -> BoardInfo:
    overrides.setdefault("lists", {"In Progress": "list-doing", "Done": "list-done"})
    return make_board_info(**overrides)


def check(board_config, audit, **overrides):
    boundaries = make_board_boundaries(audit, board=board(**overrides))
    return intake.check_board(boundaries=boundaries, audit=audit, config=board_config)


def named(status, name: str):
    return next(c for c in status.checks if c.name == name)


def test_a_healthy_board_passes_every_check_and_resolves_its_ids(board_config, audit):
    status = check(board_config, audit)
    assert status.ok
    assert status.failures == []
    # R11's second dividend: resolving names to ids once at startup makes the per-card
    # filter an equality check that survives a rename mid-run.
    assert status.label_id == "label-ai"
    assert status.in_progress_list_id == "list-doing"
    assert status.done_list_id == "list-done"


def test_an_unreachable_board_fails_the_first_check_with_its_cause(board_config, audit):
    reader = FakeCardReader(board=board())
    reader.raise_on_board = TransportError("connection refused")
    boundaries = make_board_boundaries(audit, card_reader=reader)
    status = intake.check_board(boundaries=boundaries, audit=audit, config=board_config)

    assert not status.ok
    assert [c.name for c in status.failures] == ["board reachable"]
    assert "connection refused" in status.failures[0].detail


def test_a_public_board_is_refused_and_the_message_names_the_actual_level(board_config, audit):
    """A public board is not a person the author chose. It is a different thing entirely,
    which is why this check stayed when the membership one was removed."""
    status = check(board_config, audit, permission_level="public")
    assert not status.ok
    failure = named(status, "board is private")
    assert not failure.ok
    assert "public" in failure.detail


def test_a_renamed_tag_fails_loudly_rather_than_looking_like_an_empty_board(board_config, audit):
    """The whole reason this check exists. Zero matching cards is indistinguishable from
    an empty board, so without it the system sits there looking healthy and doing
    nothing — which is exactly what "silent failure is forbidden" is aimed at."""
    status = check(board_config, audit, labels={"AI-tasks": "label-ai"})
    assert not status.ok
    failure = named(status, "tag exists")
    assert "'AI-task'" in failure.detail
    # And it says what the board *does* have, which is what makes it fixable.
    assert "AI-tasks" in failure.detail


@pytest.mark.parametrize(
    ("missing", "check_name"),
    [("In Progress", "in-progress list exists"), ("Done", "done list exists")],
)
def test_a_missing_lifecycle_list_is_caught_at_startup(board_config, audit, missing, check_name):
    """Worse than a renamed label: a missing list is otherwise discovered halfway through
    a lifecycle, after the issue already exists."""
    lists = {"In Progress": "list-doing", "Done": "list-done"}
    del lists[missing]
    status = check(board_config, audit, lists=lists)
    assert not status.ok
    assert not named(status, check_name).ok


def test_each_check_fails_independently_and_is_reported_by_name(board_config, audit):
    """Aggregate reporting, for the reason config validation aggregates: fixing one
    problem per restart is a poor experience at 2am."""
    status = check(
        board_config,
        audit,
        permission_level="org",
        labels={},
        lists={},
    )
    assert {c.name for c in status.failures} == {
        "board is private",
        "tag exists",
        "in-progress list exists",
        "done list exists",
    }


# -- FR-004a: members are recorded, never gated on --------------------------


def test_a_private_board_with_extra_members_ingests_normally(board_config, audit):
    """The case this design was corrected into.

    Refusing here would substitute the system's judgement for the author's about who may
    see their own board — nearer to the access policy Principle II forbids than to the
    assumption check it was meant to be. The human gate bounds the damage: a second member
    can cause an issue to be *filed*, and only the author can cause one to *run*.
    """
    status = check(board_config, audit, member_ids=("member-1", "member-2", "member-3"))
    assert status.ok, "extra members must not stop ingestion"
    assert status.failures == []


def test_the_member_list_is_recorded_as_information(board_config, audit):
    status = check(board_config, audit, member_ids=("member-1", "member-2"))
    members = named(status, "board members")
    assert members.informational is True
    assert members.ok is True
    assert "member-2" in members.detail


def test_an_informational_check_can_never_decide_the_verdict(board_config, audit):
    """Guards the mechanism rather than the instance: were a future check marked
    informational and then failed, it must still not stop ingestion."""
    status = intake.BoardStatus(
        checks=(
            intake.BoardCheck("gating", ok=True, detail=""),
            intake.BoardCheck("noted", ok=False, detail="", informational=True),
        )
    )
    assert status.ok
    assert status.failures == []


# -- what a failure stops, and what it does not -----------------------------


def test_an_unconfigured_installation_reports_the_absence_rather_than_a_failure(config, audit):
    status = intake.check_board(
        boundaries=make_board_boundaries(audit), audit=audit, config=config
    )
    assert not status.ok
    assert "inert" in status.checks[0].detail


def test_a_failure_raises_an_anomaly_naming_which_check_failed(board_config, audit, conn):
    status = check(board_config, audit, permission_level="public")
    intake.board_disabled_anomaly(conn, audit, config=board_config, status=status)

    anomalies = db.list_anomalies(conn)
    assert len(anomalies) == 1
    detail = anomalies[0].detail_obj
    assert anomalies[0].kind == "board_precondition"
    assert [c["name"] for c in detail["failed_checks"]] == ["board is private"]
    # And it says what the consequence is, because "board_precondition" alone does not
    # tell a reader whether their dispatch has stopped.
    assert "dispatch" in detail["consequence"]


def test_a_repeated_failure_does_not_accumulate_anomaly_rows(board_config, audit, conn, layout):
    """The partial unique index on open anomalies, doing its job: a daemon restarting in a
    loop against a public board must not produce one row per start.

    Since issue #73 an open board anomaly *can* be rewritten, so "one row" is no longer the
    whole claim: an unchanged failure must not touch that row either, neither its text nor
    its detection time, and must leave no restatement in the log (SC-002).
    """
    anomaly_id = raise_for(board_config, audit, conn, permission_level="public")
    backdate(conn, anomaly_id)
    before = board_anomalies(conn)[0]

    for _ in range(5):
        raise_for(board_config, audit, conn, permission_level="public")

    assert len(db.list_anomalies(conn)) == 1
    after = board_anomalies(conn)[0]
    assert (after.id, after.detail, after.detected_at) == (
        before.id,
        before.detail,
        before.detected_at,
    )
    assert restated(layout) == []


# -- issue #73: the open anomaly names the board's current failure ------------
#
# Found in the issue #1 verification round: the board was made public (one anomaly, "board
# is private"), made private again, and then its label was renamed. Ingestion stopped for
# the new reason, and `robot-army anomalies` went on saying the board was public until the
# old row was acknowledged. Contract: specs/20260913-090239-board-anomaly-restate/contracts/.

OLD_STAMP = "2026-08-31T14:02:11Z"
RENAMED = {"labels": {"AI-task-RENAMED": "label-renamed"}}


def raise_for(board_config, audit, conn, **overrides) -> int:
    status = check(board_config, audit, **overrides)
    assert not status.ok
    intake.board_disabled_anomaly(conn, audit, config=board_config, status=status)
    return board_anomalies(conn)[0].id


def board_anomalies(conn):
    return [a for a in db.list_anomalies(conn) if a.kind == "board_precondition"]


def failed_names(anomaly) -> list[str]:
    return [c["name"] for c in anomaly.detail_obj["failed_checks"]]


def restated(layout) -> list[dict]:
    from robot_army.audit import read_records

    return [
        r for r, _ in read_records(layout.log_dir) if r and r["action"] == "anomaly.restated"
    ]


def backdate(conn, anomaly_id: int) -> None:
    with db.transaction(conn):
        conn.execute(
            "UPDATE anomalies SET detected_at = ? WHERE id = ?", (OLD_STAMP, anomaly_id)
        )


def test_a_different_failure_restates_the_open_anomaly(board_config, audit, conn):
    """C3, the issue itself: one row, the same row, naming only what is failing now."""
    anomaly_id = raise_for(board_config, audit, conn, permission_level="public")
    backdate(conn, anomaly_id)

    raise_for(board_config, audit, conn, **RENAMED)

    [anomaly] = board_anomalies(conn)
    assert anomaly.id == anomaly_id
    assert failed_names(anomaly) == ["tag exists"]
    assert "AI-task-RENAMED" in anomaly.detail_obj["failed_checks"][0]["detail"]
    assert "board is private" not in anomaly.detail
    # The current reason was detected now, not when the board first broke.
    assert anomaly.detected_at != OLD_STAMP


def test_a_restatement_can_name_more_than_one_failure(board_config, audit, conn):
    raise_for(board_config, audit, conn, permission_level="public")
    raise_for(board_config, audit, conn, permission_level="public", **RENAMED)

    [anomaly] = board_anomalies(conn)
    assert set(failed_names(anomaly)) == {"board is private", "tag exists"}


def test_the_same_check_with_a_different_detail_is_restated(board_config, audit, conn, layout):
    """C4. "tag exists" quotes the labels the board does have; if that list changed, the
    quotation is stale, which is this defect in a smaller font."""
    raise_for(board_config, audit, conn, labels={"Something": "label-1"})
    raise_for(board_config, audit, conn, labels={"Else": "label-2"})

    [anomaly] = board_anomalies(conn)
    assert "Else" in anomaly.detail_obj["failed_checks"][0]["detail"]
    assert len(restated(layout)) == 1


@pytest.mark.parametrize("stored", ["not json", "{}", "[]", '{"failed_checks": "x"}'])
def test_an_unreadable_stored_detail_is_restated_and_says_so(
    board_config, audit, conn, layout, stored
):
    """C5. The new text is right whatever the old one said, and the log must not invent
    the previous reason it could not read."""
    anomaly_id = raise_for(board_config, audit, conn, permission_level="public")
    with db.transaction(conn):
        conn.execute("UPDATE anomalies SET detail = ? WHERE id = ?", (stored, anomaly_id))

    raise_for(board_config, audit, conn, permission_level="public")

    [anomaly] = board_anomalies(conn)
    assert failed_names(anomaly) == ["board is private"]
    [record] = restated(layout)
    assert record["detail"]["previous_failed_checks"] is None
    assert record["detail"]["previous_detail_unreadable"] is True


def test_an_acknowledged_anomaly_is_left_alone_and_a_new_one_raised(
    board_config, audit, conn, layout
):
    """C6, unchanged behaviour: a dismissal stands as the maintainer saw it."""
    anomaly_id = raise_for(board_config, audit, conn, permission_level="public")
    with db.transaction(conn):
        db.acknowledge_anomaly(conn, anomaly_id)

    raise_for(board_config, audit, conn, **RENAMED)

    everything = db.list_anomalies(conn, unacknowledged_only=False)
    acknowledged = next(a for a in everything if a.id == anomaly_id)
    assert failed_names(acknowledged) == ["board is private"]
    [open_one] = board_anomalies(conn)
    assert open_one.id != anomaly_id
    assert failed_names(open_one) == ["tag exists"]
    assert restated(layout) == []


def test_another_boards_anomaly_is_never_touched(board_config, audit, conn):
    """C7. The lookup is by board, so only this board's row can be rewritten."""
    other = {"failed_checks": [{"name": "board is private", "detail": "elsewhere"}]}
    with db.transaction(conn):
        db.raise_anomaly(
            conn,
            kind="board_precondition",
            entity_type="board",
            entity_id="another-board",
            detail=other,
        )

    raise_for(board_config, audit, conn, permission_level="public")
    raise_for(board_config, audit, conn, **RENAMED)

    by_board = {a.entity_id: a for a in board_anomalies(conn)}
    assert by_board["another-board"].detail_obj == other
    assert failed_names(by_board[board_config.trello.board_id]) == ["tag exists"]


def test_successive_restatements_still_leave_one_row(board_config, audit, conn, layout):
    raise_for(board_config, audit, conn, permission_level="public")
    raise_for(board_config, audit, conn, **RENAMED)
    raise_for(board_config, audit, conn, permission_level="public")

    [anomaly] = board_anomalies(conn)
    assert failed_names(anomaly) == ["board is private"]
    assert len(restated(layout)) == 2


def test_a_restatement_records_what_it_overwrote(board_config, audit, conn, layout):
    """US3 and SC-003: the update destroys the database's only copy of the old reason, so
    the log must hold it — and when that reason had been detected."""
    anomaly_id = raise_for(board_config, audit, conn, permission_level="public")
    backdate(conn, anomaly_id)
    previous = board_anomalies(conn)[0].detail_obj["failed_checks"]

    raise_for(board_config, audit, conn, **RENAMED)

    [record] = restated(layout)
    assert record["outcome"] == "ok"
    assert record["entity_type"] == "anomaly"
    assert record["entity_id"] == str(anomaly_id)
    detail = record["detail"]
    assert detail["kind"] == "board_precondition"
    assert detail["anomaly_entity_id"] == board_config.trello.board_id
    assert detail["previous_failed_checks"] == previous
    assert detail["failed_checks"] == board_anomalies(conn)[0].detail_obj["failed_checks"]
    assert detail["previous_detected_at"] == OLD_STAMP
    assert "previous_detail_unreadable" not in detail


def test_a_restated_anomaly_is_inside_a_recent_window(board_config, audit, conn):
    """`anomalies --since 10m` must show a reason that changed a minute ago. Had the
    detection time stayed at the board's first failure, the window would miss it."""
    from datetime import UTC, datetime, timedelta

    from robot_army.operations import _within_window

    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    anomaly_id = raise_for(board_config, audit, conn, permission_level="public")
    backdate(conn, anomaly_id)
    assert not _within_window(board_anomalies(conn)[0].detected_at, cutoff)

    raise_for(board_config, audit, conn, **RENAMED)

    assert _within_window(board_anomalies(conn)[0].detected_at, cutoff)


def test_the_issues_sequence_through_the_daemon(board_config, audit, conn, layout):
    """SC-001, replayed through the startup seam the daemon really uses: public, then the
    label renamed with nothing acknowledged in between. One anomaly, naming the label."""
    from robot_army.daemon import Daemon
    from robot_army.effects import EffectLevel

    def start(**overrides) -> Daemon:
        daemon = Daemon(
            config=board_config,
            layout=layout,
            boundaries=make_board_boundaries(audit, board=board(**overrides)),
            audit=audit,
            conn=conn,
            effect_level=EffectLevel.LIVE,
        )
        daemon._check_board()
        return daemon

    start(permission_level="public")
    daemon = start(**RENAMED)

    assert daemon.ingesting is False
    [anomaly] = board_anomalies(conn)
    assert failed_names(anomaly) == ["tag exists"]


def test_a_board_failure_disables_ingestion_without_disabling_dispatch(
    board_config, audit, conn, layout, tmp_path
):
    """R10's central claim, tested end to end through the daemon's own startup.

    An unrelated board misconfiguration must not stop the daemon from finding and
    dispatching issues the author wrote themselves.
    """
    from robot_army.daemon import Daemon
    from robot_army.effects import EffectLevel

    boundaries = make_board_boundaries(
        audit, board=board(permission_level="public"), cards=[make_card()]
    )
    daemon = Daemon(
        config=board_config,
        layout=layout,
        boundaries=boundaries,
        audit=audit,
        conn=conn,
        effect_level=EffectLevel.LIVE,
    )
    daemon._check_board()

    assert daemon.ingesting is False
    assert daemon.board is not None and not daemon.board.ok
    assert db.list_anomalies(conn)[0].kind == "board_precondition"

    # The half that matters: the ordinary jobs still exist and still run.
    daemon._jobs = daemon._build_jobs()
    assert {job.name for job in daemon._jobs} >= {"poll", "dispatch", "reconcile", "spool"}
    assert daemon.job_dispatch() == {"dispatched": 0}
    assert daemon.job_poll()["errors"] == 0


def test_an_unconfigured_daemon_checks_no_board_at_all(config, audit, conn, layout):
    """FR-001, at the startup seam: not "checked and skipped" but never asked."""
    from robot_army.daemon import Daemon
    from robot_army.effects import EffectLevel

    reader = FakeCardReader(board=board())
    boundaries = make_board_boundaries(audit, card_reader=reader)
    daemon = Daemon(
        config=config,
        layout=layout,
        boundaries=boundaries,
        audit=audit,
        conn=conn,
        effect_level=EffectLevel.LIVE,
    )
    daemon._check_board()

    assert reader.board_calls == 0
    assert daemon.board is None
    assert daemon.ingesting is False
    assert db.list_anomalies(conn) == []


# -- `doctor` reports them without starting the daemon ----------------------


def doctor_with(board_config, audit, conn, monkeypatch, **overrides):
    from robot_army import operations
    from robot_army.effects import EffectLevel

    boundaries = make_board_boundaries(audit, board=board(**overrides))
    ctx = operations.Context(
        config=board_config,
        conn=conn,
        audit=audit,
        boundaries=boundaries,
        effect_level=EffectLevel.LIVE,
    )
    return operations.doctor(ctx)


def test_doctor_reports_all_five_board_checks_individually(
    board_config, audit, conn, monkeypatch
):
    result = doctor_with(board_config, audit, conn, monkeypatch)
    names = [c["name"] for c in result.data["checks"] if c["name"].startswith("board:")]
    assert names == [
        "board: board reachable",
        "board: board is private",
        "board: board members",
        "board: tag exists",
        "board: in-progress list exists",
        "board: done list exists",
    ]


def test_doctor_exits_four_when_a_board_check_fails(board_config, audit, conn, monkeypatch):
    """The exit table reserves 4 for "check failed", which is what `doctor` does."""
    from robot_army.operations import EXIT_CHECK_FAILED

    result = doctor_with(board_config, audit, conn, monkeypatch, permission_level="public")
    assert result.code == EXIT_CHECK_FAILED
    assert "board: board is private" in result.data["failures"]


def test_doctor_says_nothing_about_a_board_that_is_not_configured(config, audit, conn):
    """Inventing a passing check would say something about a board that does not exist."""
    from robot_army import operations
    from robot_army.effects import EffectLevel

    ctx = operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_board_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )
    result = operations.doctor(ctx)
    assert not [c for c in result.data["checks"] if c["name"].startswith("board:")]


def test_doctor_reports_extra_members_without_failing(board_config, audit, conn, monkeypatch):
    result = doctor_with(
        board_config, audit, conn, monkeypatch, member_ids=("member-1", "member-2")
    )
    members = next(c for c in result.data["checks"] if c["name"] == "board: board members")
    assert members["ok"] is True
    assert "board: board members" not in result.data["failures"]


# -- ignored columns (milestone 006, T049-T052) -----------------------------

ICEBOX_LISTS = {
    "Inbox": "list-inbox",
    "Icebox": "list-ice",
    "In Progress": "list-doing",
    "Done": "list-done",
}


def ignored_checks(status):
    return [c for c in status.checks if c.name == "ignored list exists"]


def test_a_configured_ignored_column_that_exists_passes_and_is_named(board_config, audit):
    config = with_ignore_lists(board_config, "Icebox")
    boundaries = make_board_boundaries(audit, board=board(lists=ICEBOX_LISTS))
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)

    assert status.ok
    checks = ignored_checks(status)
    assert len(checks) == 1
    assert "'Icebox' found" in checks[0].detail
    assert status.ignored_list_ids == frozenset({"list-ice"})


def test_a_missing_ignored_column_fails_and_lists_what_the_board_has(board_config, audit):
    """FR-017. Naming what is missing is half of it; listing what exists is the half that
    turns "the column is missing" into "the column is missing, and here is the one you
    renamed"."""
    config = with_ignore_lists(board_config, "Icebox", "Blocked")
    boundaries = make_board_boundaries(audit, board=board(lists=ICEBOX_LISTS))
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)

    assert not status.ok
    failed = [c for c in ignored_checks(status) if not c.ok]
    assert len(failed) == 1
    assert "'Blocked'" in failed[0].detail
    assert "Icebox" in failed[0].detail and "In Progress" in failed[0].detail


def test_a_case_mismatch_fails_rather_than_matching(board_config, audit):
    """FR-019. Exact match, including case — the same rule the tag and lifecycle columns
    already use. A near-miss is *reported*, which is what makes exactness the friendly
    choice here rather than the strict one."""
    config = with_ignore_lists(board_config, "icebox")
    boundaries = make_board_boundaries(audit, board=board(lists=ICEBOX_LISTS))
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)

    assert not status.ok
    assert status.ignored_list_ids == frozenset()
    assert "'icebox'" in ignored_checks(status)[0].detail


def test_a_failing_ignored_column_refuses_ingestion_only(board_config, audit, conn):
    """FR-018, and the half most likely to regress unnoticed. An unrelated board
    misconfiguration must not take down dispatch of issues the author wrote themselves."""
    config = with_ignore_lists(board_config, "Blocked")
    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1")], board=board(lists=ICEBOX_LISTS)
    )
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)

    outcome = intake.poll_board(
        conn, boundaries=boundaries, audit=audit, config=config, status=status, dry_run=False
    )

    assert outcome.skipped_reason == "board preconditions failed"
    assert outcome.found == 0
    assert db.list_cards(conn) == []
    # Nothing about the board was even asked, let alone written to.
    assert boundaries.card_reader.poll_calls == []
    assert boundaries.card_writer.comments == []


def test_no_ignored_columns_appends_no_checks(board_config, audit):
    """FR-002 at the check layer: the board section reports exactly what milestone 003
    reported, so an unconfigured installation cannot fail a check that did not exist."""
    boundaries = make_board_boundaries(audit, board=board(lists=ICEBOX_LISTS))
    status = intake.check_board(boundaries=boundaries, audit=audit, config=board_config)

    assert ignored_checks(status) == []
    assert status.ok


def test_the_startup_record_names_the_ignored_columns(board_config, audit, layout):
    """FR-022. The poll's aggregate count says *how many* were excluded; this says *by
    what*. Neither alone reconstructs the decision."""
    import json

    config = with_ignore_lists(board_config, "Icebox")
    boundaries = make_board_boundaries(audit, board=board(lists=ICEBOX_LISTS))
    intake.check_board(boundaries=boundaries, audit=audit, config=config)
    audit.close()

    records = [
        json.loads(line)
        for path in layout.log_dir.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    check = next(r for r in records if r["action"] == "trello.board.check" and "checks" in r["detail"])
    assert check["detail"]["ignored_lists"] == ["Icebox"]


def test_both_columns_of_a_duplicated_name_are_excluded(board_config, audit):
    """FR-019b, and the reason ``lists_by_id`` exists.

    ``lists`` is name-keyed and collapses the two, so resolving through it would leave one
    of them quietly still intake — an exclusion the author configured and did not get.
    """
    config = with_ignore_lists(board_config, "Icebox")
    boundaries = make_board_boundaries(
        audit,
        board=make_board_info(
            lists={"Icebox": "list-ice-2", "In Progress": "list-doing", "Done": "list-done"},
            lists_by_id={
                "list-ice-1": "Icebox",
                "list-ice-2": "Icebox",
                "list-doing": "In Progress",
                "list-done": "Done",
            },
        ),
    )
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)

    assert status.ok
    assert status.ignored_list_ids == frozenset({"list-ice-1", "list-ice-2"})
