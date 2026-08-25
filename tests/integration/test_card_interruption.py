"""User story 4: one card, one issue, no matter what happened (T076, T077, T078).

FR-042 makes this the gate on believing anything else in the milestone, and the reason is
that the dangerous window is invisible after the fact: between creating the issue and
recording the mapping, the issue exists and nothing local knows it.

Each test below kills the four-step sequence at one of its three seams by reproducing the
*state* a kill at that point leaves behind, then runs the next pass and asserts no
duplicate. Reproducing the state rather than killing a real process is what makes these
run in CI at all — and quickstart scenario 5 does it with a real ``kill -9`` against a real
board, because this cannot fully replace that.
"""

from __future__ import annotations

from tests.conftest import make_board_boundaries, make_card

from robot_army import db, intake
from robot_army.cardstates import CardState

REPO = "jantman/demo"


def card(card_id="card-1"):
    return make_card(card_id, body=f"please fix https://github.com/{REPO}")


def cycle(conn, board_config, audit, boundaries, **kwargs):
    status = intake.check_board(boundaries=boundaries, audit=audit, config=board_config)
    return intake.run_cycle(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        status=status,
        dry_run=False,
        **kwargs,
    )


def issues_for(boundaries):
    return boundaries.issue_writer.created


def markers(boundaries):
    return [b for _, b in boundaries.card_writer.comments if intake.MARKER_PREFIX in b]


# -- seam one: after the intent, before the issue ---------------------------


def test_killed_after_the_intent_with_no_issue_the_next_pass_creates_one(
    conn, board_config, audit
):
    boundaries = make_board_boundaries(audit, cards=[card()])
    # The intent row, committed, and nothing else — exactly what step 1 leaves.
    with db.transaction(conn):
        row_id = db.insert_card(
            conn,
            board_id="board-1",
            card_id="card-1",
            card_url="https://trello.com/c/card-1",
            title="Fix the thing",
            body=f"https://github.com/{REPO}",
            dry_run=False,
        )
        conn.execute(
            "UPDATE cards SET state = ?, repo_key = ?, intent_at = ? WHERE id = ?",
            (str(CardState.CREATING), REPO, "2026-08-24T00:00:00Z", row_id),
        )

    cycle(conn, board_config, audit, boundaries)

    assert len(issues_for(boundaries)) == 1, "the retry did not happen, or happened twice"
    row = db.get_card_by_id(conn, row_id)
    assert row.state == CardState.LINKED
    assert len(markers(boundaries)) == 1


# -- seam two: after the issue, before the mapping --------------------------


def test_killed_after_the_issue_with_no_mapping_the_listing_adopts_it(
    conn, board_config, audit
):
    """The dangerous window, and the one R6's whole mechanism exists for. The issue is
    out there and nothing local knows its number."""
    from tests.conftest import make_issue

    boundaries = make_board_boundaries(audit, cards=[card()])
    orphan = make_issue(
        number=77,
        title="Fix the thing",
        body="Filed by robot-army from a card: https://trello.com/c/card-1",
        url=f"https://github.com/{REPO}/issues/77",
        author=board_config.github.author,
    )
    boundaries.issue_reader.issues = [orphan]
    boundaries.issue_reader.created = {77: "2026-08-24T00:00:01Z"}

    with db.transaction(conn):
        row_id = db.insert_card(
            conn,
            board_id="board-1",
            card_id="card-1",
            card_url="https://trello.com/c/card-1",
            title="Fix the thing",
            body=f"https://github.com/{REPO}",
            dry_run=False,
        )
        conn.execute(
            "UPDATE cards SET state = ?, repo_key = ?, intent_at = ? WHERE id = ?",
            (str(CardState.CREATING), REPO, "2026-08-24T00:00:00Z", row_id),
        )

    cycle(conn, board_config, audit, boundaries)

    assert issues_for(boundaries) == [], "a second issue was created for an adopted card"
    row = db.get_card_by_id(conn, row_id)
    assert row.state == CardState.LINKED
    assert row.issue_number == 77
    # The listing endpoint, never search: search lags by minutes and would miss exactly
    # the issue a crash orphaned.
    assert boundaries.issue_reader.listing_calls
    assert boundaries.issue_reader.listing_calls[0][1] == "2026-08-24T00:00:00Z"


def test_an_issue_from_before_the_intent_is_not_adopted(conn, board_config, audit):
    """The bound matters. An older issue in the same repository, even one mentioning this
    card, is not the one this attempt created."""
    from tests.conftest import make_issue

    boundaries = make_board_boundaries(audit, cards=[card()])
    boundaries.issue_reader.issues = [
        make_issue(
            number=5,
            body="https://trello.com/c/card-1",
            url=f"https://github.com/{REPO}/issues/5",
            author=board_config.github.author,
        )
    ]
    boundaries.issue_reader.created = {5: "2026-08-01T00:00:00Z"}

    with db.transaction(conn):
        row_id = db.insert_card(
            conn,
            board_id="board-1",
            card_id="card-1",
            card_url="https://trello.com/c/card-1",
            title="Fix the thing",
            body=f"https://github.com/{REPO}",
            dry_run=False,
        )
        conn.execute(
            "UPDATE cards SET state = ?, repo_key = ?, intent_at = ? WHERE id = ?",
            (str(CardState.CREATING), REPO, "2026-08-24T00:00:00Z", row_id),
        )
    cycle(conn, board_config, audit, boundaries)

    assert len(issues_for(boundaries)) == 1
    assert db.get_card_by_id(conn, row_id).issue_number == 101


# -- seam three: after the mapping, before the comment ----------------------


def test_killed_after_the_mapping_with_no_comment_the_comment_is_posted_once(
    conn, board_config, audit
):
    boundaries = make_board_boundaries(audit, cards=[card()])
    with db.transaction(conn):
        row_id = db.insert_card(
            conn,
            board_id="board-1",
            card_id="card-1",
            card_url="https://trello.com/c/card-1",
            title="Fix the thing",
            body=f"https://github.com/{REPO}",
            dry_run=False,
        )
        conn.execute(
            """UPDATE cards SET state = ?, repo_key = ?, issue_number = 55,
                                issue_url = ?, intent_at = ?
               WHERE id = ?""",
            (
                str(CardState.LINKED),
                REPO,
                f"https://github.com/{REPO}/issues/55",
                "2026-08-24T00:00:00Z",
                row_id,
            ),
        )

    cycle(conn, board_config, audit, boundaries)
    assert len(markers(boundaries)) == 1
    assert db.get_card_by_id(conn, row_id).comment_posted_at is not None

    # And a further pass posts nothing more.
    cycle(conn, board_config, audit, boundaries)
    assert len(markers(boundaries)) == 1


def test_a_marker_already_on_the_board_is_not_posted_a_second_time(
    conn, board_config, audit
):
    """The retry checks the board first, so a kill *between* posting and recording it does
    not produce two comments."""
    existing = intake.marker_comment(f"https://github.com/{REPO}/issues/55")
    boundaries = make_board_boundaries(
        audit, cards=[card()], comments={"card-1": [existing]}
    )
    with db.transaction(conn):
        row_id = db.insert_card(
            conn,
            board_id="board-1",
            card_id="card-1",
            card_url="https://trello.com/c/card-1",
            title="Fix the thing",
            body=f"https://github.com/{REPO}",
            dry_run=False,
        )
        conn.execute(
            "UPDATE cards SET state = ?, repo_key = ?, issue_number = 55 WHERE id = ?",
            (str(CardState.LINKED), REPO, row_id),
        )

    cycle(conn, board_config, audit, boundaries)
    assert markers(boundaries) == []
    assert db.get_card_by_id(conn, row_id).comment_posted_at is not None


# -- T077: total loss of the database --------------------------------------


def test_a_lost_database_is_rebuilt_from_the_marker_and_creates_no_second_issue(
    conn, board_config, audit
):
    """FR-034, and the case §11 calls the marker's actual purpose: a recovery marker for
    rebuilding state after DB loss, not the primary key."""
    boundaries = make_board_boundaries(audit, cards=[card()])
    cycle(conn, board_config, audit, boundaries)
    original = db.list_cards(conn)[0]
    assert original.issue_number == 101

    # The board keeps the marker we posted; the database does not survive.
    boundaries.card_reader.comments["card-1"] = [b for _, b in boundaries.card_writer.comments]
    with db.transaction(conn):
        conn.execute("DELETE FROM cards")
        conn.execute("DELETE FROM poll_state")
    assert db.list_cards(conn, include_simulated=True) == []

    cycle(conn, board_config, audit, boundaries)

    assert len(issues_for(boundaries)) == 1, "a lost database produced a second issue"
    restored = db.list_cards(conn)[0]
    assert restored.state == CardState.LINKED
    assert (restored.repo_key, restored.issue_number) == (REPO, 101)
    assert restored.comment_posted_at is not None


def test_the_recovery_is_visible_in_the_log(conn, board_config, audit, layout):
    """A recovery that happened silently would be indistinguishable from nothing having
    gone wrong, which is the one thing the log must never allow."""
    import json

    boundaries = make_board_boundaries(audit, cards=[card()])
    cycle(conn, board_config, audit, boundaries)
    boundaries.card_reader.comments["card-1"] = [b for _, b in boundaries.card_writer.comments]
    with db.transaction(conn):
        conn.execute("DELETE FROM cards")
    cycle(conn, board_config, audit, boundaries)
    audit.close()

    records = [
        json.loads(line)
        for path in sorted(layout.log_dir.glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    recovered = [r for r in records if r["action"] == "trello.recovered"]
    assert recovered, "the recovery left no record"
    assert any(r["detail"].get("path") == "marker comment" for r in recovered)


# -- T078: repetition -------------------------------------------------------


def test_one_hundred_polls_across_a_restart_yield_one_issue_and_one_comment(
    conn, board_config, audit
):
    """SC-002. The steady state, not the exceptional one: the daemon runs for days and
    polls the same unchanged card hundreds of times."""
    boundaries = make_board_boundaries(audit, cards=[card()])
    for index in range(100):
        if index == 50:
            # A restart: the recovery sweep runs, and must find nothing to do.
            intake.recovery_sweep(
                conn, boundaries=boundaries, audit=audit, config=board_config, dry_run=False
            )
        cycle(conn, board_config, audit, boundaries)

    assert len(issues_for(boundaries)) == 1
    assert len(markers(boundaries)) == 1
    assert len(db.list_cards(conn)) == 1


def test_a_hundred_polls_of_a_held_card_produce_one_comment_and_no_issue(
    conn, board_config, audit
):
    boundaries = make_board_boundaries(audit, cards=[make_card("card-1", body="no repo")])
    for _ in range(100):
        cycle(conn, board_config, audit, boundaries)

    assert issues_for(boundaries) == []
    assert len(boundaries.card_writer.comments) == 1


# -- T074: a linked issue that vanished ------------------------------------


def test_a_linked_card_whose_issue_vanished_raises_an_anomaly_and_keeps_the_mapping(
    conn, board_config, audit
):
    """FR-037. Both alternatives are wrong: dropping the mapping would let the next poll
    file a second issue, and filing one automatically would do it without anyone asking."""
    boundaries = make_board_boundaries(audit, cards=[card()])
    cycle(conn, board_config, audit, boundaries)
    row = db.list_cards(conn)[0]

    # The issue is gone — deleted, or transferred to another repository.
    boundaries.issue_reader.issues = []
    counts = intake.recovery_sweep(
        conn, boundaries=boundaries, audit=audit, config=board_config, dry_run=False
    )

    assert counts["missing_issue"] == 1
    anomalies = db.list_anomalies(conn)
    assert [a.kind for a in anomalies] == ["card_issue_missing"]
    assert f"{REPO}#{row.issue_number}" in anomalies[0].detail_obj["issue"]

    after = db.get_card_by_id(conn, row.id)
    assert (after.repo_key, after.issue_number) == (row.repo_key, row.issue_number)

    # And the next ordinary pass still creates nothing.
    cycle(conn, board_config, audit, boundaries)
    assert len(issues_for(boundaries)) == 1


# -- the cycle report must not overstate what it did ------------------------


def test_a_deferred_comment_retry_is_not_counted_as_an_issue_creation(
    conn, board_config, audit
):
    """`_post_marker_comment` is reachable twice over: as step 4 of a creation, and on its
    own as the retry for a card whose comment was deferred. Reporting both as ``created``
    meant a pass that filed no issue at all could claim to have filed one.

    A report that overstates an outward action is worse than one that omits it — the
    whole value of the cycle summary is that it can be believed.
    """
    from robot_army.boundaries import TransportError

    boundaries = make_board_boundaries(audit, cards=[card()])
    boundaries.card_writer.raise_on_comment = TransportError("board down")
    first = cycle(conn, board_config, audit, boundaries)
    assert first.issues_created == 1, "the issue itself was filed and should be counted"

    row = db.list_cards(conn)[0]
    assert row.state == CardState.LINKED and row.comment_posted_at is None

    # The narrow window the miscount lived in: the recovery sweep's attempt defers, and
    # the evaluate-loop retry later in the *same* cycle succeeds.
    writer = boundaries.card_writer
    real_comment = writer.comment
    attempts = {"n": 0}

    def flaky(card_id: str, body: str):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise TransportError("still flapping")
        return real_comment(card_id, body)

    writer.comment = flaky
    writer.raise_on_comment = None
    creations_before = len(issues_for(boundaries))

    second = cycle(conn, board_config, audit, boundaries)

    assert attempts["n"] >= 2, "the scenario did not exercise the deferred retry"
    assert len(issues_for(boundaries)) == creations_before, "no issue should have been filed"
    assert second.issues_created == 0, "a comment retry was reported as an issue creation"
    assert second.recovered >= 1, "finishing a deferred step 4 is a recovery, and is reported"
    assert len(markers(boundaries)) == 1, "and the comment landed exactly once"


def test_dropped_cards_are_counted_in_the_cycle_report(conn, board_config, audit):
    """``PollOutcome.dropped`` reported 0 however many cards had just left the board: the
    only producer of a ``dropped`` verdict is ``evaluate_card``, and the cycle skips rows
    already in that state before reaching it. The cards are actually dropped earlier, in
    the reconcile pass, which counted nothing."""
    import dataclasses

    boundaries = make_board_boundaries(
        audit,
        cards=[
            make_card("card-1", body="no repository named here"),
            make_card("card-2", body="nor here"),
        ],
    )
    cycle(conn, board_config, audit, boundaries)
    assert all(row.state == CardState.NEEDS_INFO for row in db.list_cards(conn))

    # Both lose their tag, which is how a card leaves: `poll` simply stops returning it.
    for index in (0, 1):
        boundaries.card_reader.cards[index] = dataclasses.replace(
            boundaries.card_reader.cards[index], label_ids=()
        )
    outcome = cycle(conn, board_config, audit, boundaries)

    assert [row.state for row in db.list_cards(conn)] == [CardState.DROPPED] * 2
    assert outcome.dropped == 2

    # And a later pass does not re-count cards that were already dropped.
    assert cycle(conn, board_config, audit, boundaries).dropped == 0


def test_an_archived_linked_card_is_not_counted_as_dropped(conn, board_config, audit):
    """It keeps its mapping, so it is a different event. Counting it as dropped would say
    the mapping had gone when it had not."""
    import dataclasses

    boundaries = make_board_boundaries(audit, cards=[card()])
    cycle(conn, board_config, audit, boundaries)

    boundaries.card_reader.cards[0] = dataclasses.replace(
        boundaries.card_reader.cards[0], closed=True
    )
    outcome = cycle(conn, board_config, audit, boundaries)

    row = db.list_cards(conn)[0]
    assert row.state == CardState.LINKED and row.archived_at is not None
    assert outcome.dropped == 0
