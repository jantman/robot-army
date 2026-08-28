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


def test_a_repeated_failure_does_not_accumulate_anomaly_rows(board_config, audit, conn):
    """The partial unique index on open anomalies, doing its job: a daemon restarting in a
    loop against a public board must not produce one row per start."""
    status = check(board_config, audit, permission_level="public")
    for _ in range(5):
        intake.board_disabled_anomaly(conn, audit, config=board_config, status=status)
    assert len(db.list_anomalies(conn)) == 1


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
