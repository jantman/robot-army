"""The board poll cycle (T029).

Three properties, each of which is a requirement rather than a behaviour:

* **An unconfigured installation makes no board request at all** (FR-001, quickstart
  scenario 1). Not "makes one and discards the result" — none.
* **A failure is never an empty board** (FR-009). "I could not ask" and "there is nothing
  there" produce identical downstream behaviour if conflated, and only one of them is a
  reason to do nothing.
* **This cycle writes nothing to either system.** It is the foundational checkpoint: the
  board is visible and nothing has been created anywhere.
"""

from __future__ import annotations

from tests.conftest import FakeCardReader, make_board_boundaries, make_card

from robot_army import db, health, intake
from robot_army.boundaries import TransportError
from robot_army.cardstates import CardState
from robot_army.daemon import Daemon
from robot_army.effects import EffectLevel


def status_for(board_config, audit, boundaries):
    return intake.check_board(boundaries=boundaries, audit=audit, config=board_config)


def poll(conn, board_config, audit, boundaries, *, dry_run: bool = False):
    return intake.poll_board(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        status=status_for(board_config, audit, boundaries),
        dry_run=dry_run,
    )


# -- FR-001: an unconfigured installation is inert --------------------------


def test_an_unconfigured_installation_makes_no_board_request(conn, config, audit):
    reader = FakeCardReader([make_card()])
    boundaries = make_board_boundaries(audit, card_reader=reader)
    outcome = intake.poll_board(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        status=intake.BoardStatus(checks=()),
        dry_run=False,
    )
    assert outcome.skipped_reason == "no board configured"
    assert reader.poll_calls == []
    assert db.list_cards(conn, include_simulated=True) == []


def test_an_unconfigured_daemon_registers_no_board_job(config, audit, conn, layout):
    daemon = Daemon(
        config=config,
        layout=layout,
        boundaries=make_board_boundaries(audit),
        audit=audit,
        conn=conn,
        effect_level=EffectLevel.LIVE,
    )
    assert "board" not in {job.name for job in daemon._build_jobs()}


def test_a_configured_daemon_runs_the_board_after_poll_and_before_dispatch(
    board_config, audit, conn, layout
):
    daemon = Daemon(
        config=board_config,
        layout=layout,
        boundaries=make_board_boundaries(audit),
        audit=audit,
        conn=conn,
        effect_level=EffectLevel.LIVE,
    )
    names = [job.name for job in daemon._build_jobs()]
    assert names.index("poll") < names.index("board") < names.index("dispatch")
    board_job = next(j for j in daemon._build_jobs() if j.name == "board")
    assert board_job.interval == 300.0


# -- a successful cycle -----------------------------------------------------


def test_a_successful_poll_creates_one_row_per_tagged_card(conn, board_config, audit):
    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1"), make_card("card-2", title="Another")]
    )
    outcome = poll(conn, board_config, audit, boundaries)

    assert (outcome.found, outcome.created) == (2, 2)
    rows = db.list_cards(conn)
    assert [row.card_id for row in rows] == ["card-1", "card-2"]
    assert all(row.state == CardState.DISCOVERED for row in rows)


def test_an_untagged_card_creates_nothing(conn, board_config, audit):
    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1", label_ids=("label-other",))]
    )
    outcome = poll(conn, board_config, audit, boundaries)
    assert (outcome.found, outcome.created) == (0, 0)
    assert db.list_cards(conn) == []


def test_re_polling_the_same_card_is_a_no_op(conn, board_config, audit):
    boundaries = make_board_boundaries(audit, cards=[make_card("card-1")])
    poll(conn, board_config, audit, boundaries)
    second = poll(conn, board_config, audit, boundaries)
    assert second.created == 0
    assert len(db.list_cards(conn)) == 1


def test_the_origin_list_is_captured_at_first_sighting(conn, board_config, audit):
    """The only moment the card is guaranteed to be where the author left it. Learning it
    later would record a list *we* put it in as the place it came from (FR-029)."""
    boundaries = make_board_boundaries(audit, cards=[make_card("card-1", list_id="list-inbox")])
    poll(conn, board_config, audit, boundaries)
    assert db.list_cards(conn)[0].origin_list_id == "list-inbox"


def test_the_activity_baseline_is_stored_from_the_first_read(conn, board_config, audit):
    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1", last_activity="2026-08-24T09:00:00Z")]
    )
    poll(conn, board_config, audit, boundaries)
    assert db.list_cards(conn)[0].last_activity == "2026-08-24T09:00:00Z"


def test_the_poll_writes_nothing_to_either_system(conn, board_config, audit):
    """The foundational checkpoint: the board is visible and nothing has been created."""
    boundaries = make_board_boundaries(audit, cards=[make_card("card-1")])
    poll(conn, board_config, audit, boundaries)

    assert boundaries.card_writer.comments == []
    assert boundaries.card_writer.moves == []
    assert boundaries.issue_writer.created == []
    assert db.list_work_items(conn, include_simulated=True) == []


def test_poll_state_is_kept_under_the_synthetic_board_key(conn, board_config, audit):
    """R13: ``poll_state`` has no foreign key and no consumer that renders its rows as
    repositories, so a non-repository key is safe and a second table is not needed."""
    boundaries = make_board_boundaries(audit, cards=[make_card("card-1")])
    poll(conn, board_config, audit, boundaries)

    state = db.get_poll_state(conn, health.board_poll_key("board-1"))
    assert state.last_polled_at is not None
    assert state.consecutive_failures == 0
    # No ETag: Trello offers no usable conditional request here, which is *why* the
    # interval is 300 seconds rather than 60.
    assert state.etag is None


# -- FR-009: a failure is not an empty board --------------------------------


def failing_boundaries(audit, error=None):
    reader = FakeCardReader([make_card("card-1")])
    reader.raise_on_poll = error or TransportError("connection refused")
    return make_board_boundaries(audit, card_reader=reader)


def test_a_transport_failure_is_recorded_rather_than_reported_as_empty(
    conn, board_config, audit
):
    boundaries = failing_boundaries(audit)
    outcome = poll(conn, board_config, audit, boundaries)

    assert outcome.error is not None and "connection refused" in outcome.error
    # The distinction with teeth: `found` is not merely 0 — the outcome carries an error,
    # so no caller can mistake this for a board with nothing on it.
    assert outcome.found == 0
    state = db.get_poll_state(conn, health.board_poll_key("board-1"))
    assert state.consecutive_failures == 1
    assert state.backoff_until is not None


def test_the_failure_cause_reaches_the_audit_log(conn, board_config, audit, layout):
    poll(conn, board_config, audit, failing_boundaries(audit))
    audit.close()
    text = "".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    assert "connection refused" in text
    assert '"action":"trello.poll"' in text


def test_backoff_grows_and_suppresses_the_next_attempt(conn, board_config, audit):
    boundaries = failing_boundaries(audit)
    poll(conn, board_config, audit, boundaries)
    first = db.get_poll_state(conn, health.board_poll_key("board-1"))

    # A poll during the backoff window is skipped without touching the board at all.
    healthy = make_board_boundaries(audit, cards=[make_card("card-1")])
    skipped = poll(conn, board_config, audit, healthy)
    assert skipped.skipped_reason is not None and "backoff" in skipped.skipped_reason
    assert healthy.card_reader.poll_calls == []

    # Clear the window and fail again: the interval widens rather than repeating.
    conn.execute("UPDATE poll_state SET backoff_until = NULL")
    poll(conn, board_config, audit, boundaries)
    second = db.get_poll_state(conn, health.board_poll_key("board-1"))
    assert second.consecutive_failures == 2
    assert second.backoff_until > first.backoff_until


def test_an_anomaly_is_raised_at_the_threshold_and_not_before(conn, board_config, audit):
    boundaries = failing_boundaries(audit)
    for _ in range(intake.FAILURE_ANOMALY_THRESHOLD - 1):
        poll(conn, board_config, audit, boundaries)
        conn.execute("UPDATE poll_state SET backoff_until = NULL")
    assert db.list_anomalies(conn) == []

    poll(conn, board_config, audit, boundaries)
    anomalies = db.list_anomalies(conn)
    assert [a.kind for a in anomalies] == ["board_unreachable"]
    # The message must not be mistakable for an empty board by whoever reads it.
    assert "NOT an empty board" in anomalies[0].detail_obj["consequence"]


def test_a_recovered_board_clears_the_failure_count(conn, board_config, audit):
    poll(conn, board_config, audit, failing_boundaries(audit))
    conn.execute("UPDATE poll_state SET backoff_until = NULL")
    poll(conn, board_config, audit, make_board_boundaries(audit, cards=[make_card("card-1")]))

    state = db.get_poll_state(conn, health.board_poll_key("board-1"))
    assert state.consecutive_failures == 0
    assert state.backoff_until is None


def test_a_board_failure_leaves_github_polling_untouched(conn, board_config, audit, layout):
    """One repository's failure already does not stop the rest (``poll_all``); the board
    job follows the same rule from the other direction."""
    daemon = Daemon(
        config=board_config,
        layout=layout,
        boundaries=failing_boundaries(audit),
        audit=audit,
        conn=conn,
        effect_level=EffectLevel.LIVE,
    )
    daemon._check_board()
    daemon._jobs = daemon._build_jobs()
    ran = daemon.tick()

    assert ran["board"]["error"] is not None
    assert ran["poll"]["errors"] == 0
    assert "dispatch" in ran


# -- effect levels ----------------------------------------------------------


def test_a_simulated_poll_marks_its_rows_and_leaves_the_live_slot_free(
    conn, board_config, audit
):
    """FR-041: a ``no-remote`` run followed by a ``live`` run of the same card must
    perform the real creation rather than colliding with its own rehearsal."""
    boundaries = make_board_boundaries(audit, level=EffectLevel.NO_REMOTE, cards=[make_card()])
    poll(conn, board_config, audit, boundaries, dry_run=True)
    assert db.list_cards(conn) == []
    assert len(db.list_cards(conn, include_simulated=True)) == 1

    poll(conn, board_config, audit, boundaries, dry_run=False)
    assert len(db.list_cards(conn)) == 1
    assert len(db.list_cards(conn, include_simulated=True)) == 2


def test_the_board_job_is_skipped_when_preconditions_failed(board_config, audit, conn, layout):
    from robot_army.boundaries import BoardInfo

    boundaries = make_board_boundaries(
        audit,
        cards=[make_card()],
        board=BoardInfo(
            board_id="board-1",
            name="Intake",
            permission_level="public",
            labels={"AI-task": "label-ai"},
            lists={"In Progress": "a", "Done": "b"},
        ),
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
    result = daemon.job_board()

    assert result["skipped"] is True
    assert result["failed_checks"] == ["board is private"]
    assert boundaries.card_reader.poll_calls == []


# -- the forced rescan job (T052) -------------------------------------------


def test_the_daemon_drains_a_rescan_marker_and_runs_the_job(board_config, audit, conn, layout):
    """The mechanism 002 built, taking a third job name without modification."""
    from robot_army import control

    boundaries = make_board_boundaries(audit, cards=[make_card("card-1", body="no repo")])
    daemon = Daemon(
        config=board_config,
        layout=layout,
        boundaries=boundaries,
        audit=audit,
        conn=conn,
        effect_level=EffectLevel.LIVE,
    )
    daemon._check_board()
    daemon._jobs = daemon._build_jobs()

    # Not due of its own accord: a rescan is never periodic.
    assert "rescan" not in daemon.tick()

    control.request_job(layout, "rescan")
    ran = daemon.tick()
    assert "rescan" in ran
    assert ran["rescan"]["evaluated"] >= 1
    assert control.pending(layout) == []


def test_a_rescan_re_evaluates_a_held_card_the_ordinary_pass_would_skip(
    board_config, audit, conn, layout
):
    import dataclasses

    from robot_army import control

    boundaries = make_board_boundaries(audit, cards=[make_card("card-1", body="no repo")])
    daemon = Daemon(
        config=board_config,
        layout=layout,
        boundaries=boundaries,
        audit=audit,
        conn=conn,
        effect_level=EffectLevel.LIVE,
    )
    daemon._check_board()
    daemon._jobs = daemon._build_jobs()
    daemon.tick()
    assert db.list_cards(conn)[0].state == CardState.NEEDS_INFO

    # The repository is configured now; the card has not been touched, so an ordinary
    # pass leaves it alone.
    board_config.repos["jantman/newrepo"] = dataclasses.replace(
        next(iter(board_config.repos.values())), key="jantman/newrepo"
    )
    boundaries.card_reader.cards[0] = dataclasses.replace(
        boundaries.card_reader.cards[0], body="https://github.com/jantman/newrepo"
    )

    control.request_job(layout, "rescan")
    daemon.tick()
    assert db.list_cards(conn)[0].repo_key == "jantman/newrepo"
