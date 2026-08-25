"""The self-write trap, from both sides (T045, R9).

This is a self-inflicted infinite loop waiting to happen, and no requirement forbids it
because nobody thought of it until design. The rescan trigger is "the card's last-activity
timestamp changed" (FR-023) — and **commenting on a card changes its last-activity
timestamp**.

Without the fix, the ``needs_info`` comment posted in step one makes the next poll see an
edit, re-evaluate, find nothing new, and burn an evaluation every interval for every held
card forever. A later change that made the comment conditional differently would turn that
into a true loop that posts.

Both directions are tested, because a fix that stopped re-evaluating *at all* would pass a
one-sided test while breaking FR-023 outright.
"""

from __future__ import annotations

import dataclasses

from tests.conftest import make_board_boundaries, make_card

from robot_army import db, intake
from robot_army.cardstates import CardState

REPO = "jantman/demo"


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


def unresolvable():
    return make_card("card-1", title="Do the thing", body="no repository named here")


def edit(boundaries, **changes):
    """Mutate the fake board's card, as the author editing it would."""
    boundaries.card_reader.cards[0] = dataclasses.replace(
        boundaries.card_reader.cards[0], **changes
    )


# -- the trap: our own write must not look like an edit ---------------------


def test_the_baseline_is_refreshed_from_our_own_comment(conn, board_config, audit):
    boundaries = make_board_boundaries(audit, cards=[unresolvable()])
    cycle(conn, board_config, audit, boundaries)

    row = db.list_cards(conn)[0]
    assert row.state == CardState.NEEDS_INFO
    assert len(boundaries.card_writer.comments) == 1
    # The stored baseline is what the board said *after* our comment, not before it.
    assert row.last_activity == boundaries.card_reader.cards[0].last_activity


def test_a_poll_immediately_after_our_own_comment_triggers_no_re_evaluation(
    conn, board_config, audit
):
    boundaries = make_board_boundaries(audit, cards=[unresolvable()])
    cycle(conn, board_config, audit, boundaries)
    assert len(boundaries.card_writer.comments) == 1

    outcome = cycle(conn, board_config, audit, boundaries)
    assert outcome.held == 0, "the card was re-evaluated on the strength of our own write"
    assert len(boundaries.card_writer.comments) == 1


def test_a_card_held_for_many_polls_accumulates_exactly_one_comment(
    conn, board_config, audit
):
    """FR-022, over time. This is the shape the loop would actually take in the field: a
    card nobody has got round to editing, polled every five minutes for a week."""
    boundaries = make_board_boundaries(audit, cards=[unresolvable()])
    for _ in range(20):
        cycle(conn, board_config, audit, boundaries)
    assert len(boundaries.card_writer.comments) == 1


# -- the other side: a real edit must still be noticed ----------------------


def test_an_edit_by_the_author_does_trigger_a_re_evaluation(conn, board_config, audit):
    boundaries = make_board_boundaries(audit, cards=[unresolvable()])
    cycle(conn, board_config, audit, boundaries)
    assert boundaries.issue_writer.created == []

    edit(
        boundaries,
        body=f"actually it is https://github.com/{REPO}",
        last_activity="2026-08-25T10:00:00Z",
    )
    outcome = cycle(conn, board_config, audit, boundaries)

    assert outcome.issues_created == 1
    row = db.list_cards(conn)[0]
    assert row.state == CardState.LINKED
    assert row.repo_key == REPO


def test_an_edit_that_still_does_not_resolve_is_re_evaluated_and_held(
    conn, board_config, audit
):
    boundaries = make_board_boundaries(audit, cards=[unresolvable()])
    cycle(conn, board_config, audit, boundaries)

    edit(boundaries, body="still nothing useful", last_activity="2026-08-25T10:00:00Z")
    outcome = cycle(conn, board_config, audit, boundaries)

    assert outcome.held == 1, "the edit was not noticed"
    # But the reason has not changed, so no second comment (FR-022).
    assert len(boundaries.card_writer.comments) == 1


def test_a_changed_reason_does_earn_a_second_comment(conn, board_config, audit):
    """The case a naive "have we commented?" flag gets wrong. The card's problem changed,
    so what we told the author is now out of date, and silence would leave them fixing the
    wrong thing."""
    boundaries = make_board_boundaries(audit, cards=[unresolvable()])
    cycle(conn, board_config, audit, boundaries)
    assert "no configured repository" in boundaries.card_writer.comments[0][1]

    edit(
        boundaries,
        body=f"either https://github.com/{REPO} or https://github.com/jantman/other",
        last_activity="2026-08-25T10:00:00Z",
    )
    # A second configured repository, so the card becomes ambiguous rather than empty.
    board_config.repos["jantman/other"] = board_config.repos[REPO]
    cycle(conn, board_config, audit, boundaries)

    assert len(boundaries.card_writer.comments) == 2
    assert "exactly one" in boundaries.card_writer.comments[1][1]


def test_the_baseline_advances_even_when_no_comment_is_posted(conn, board_config, audit):
    """Otherwise the edit that was already handled looks like a fresh one forever, and the
    card is re-evaluated on every poll for the rest of its life."""
    boundaries = make_board_boundaries(audit, cards=[unresolvable()])
    cycle(conn, board_config, audit, boundaries)

    edit(boundaries, body="still nothing", last_activity="2026-08-25T10:00:00Z")
    cycle(conn, board_config, audit, boundaries)
    assert db.list_cards(conn)[0].last_activity == "2026-08-25T10:00:00Z"

    outcome = cycle(conn, board_config, audit, boundaries)
    assert outcome.held == 0


def test_a_writer_that_cannot_re_read_leaves_the_baseline_alone(conn, board_config, audit):
    """One redundant re-evaluation is idempotent and posts no comment. Recording a stamp
    the board does not have would be a lie in the database, which is worse."""
    from robot_army.boundaries import CardWriteResult

    boundaries = make_board_boundaries(audit, cards=[unresolvable()])
    writer = boundaries.card_writer
    original = writer.comment

    def comment_without_refresh(card_id, body):
        original(card_id, body)
        return CardWriteResult(url="https://trello.com/c/card-1#c1", last_activity=None)

    writer.comment = comment_without_refresh
    cycle(conn, board_config, audit, boundaries)

    row = db.list_cards(conn)[0]
    assert row.commented_reason is not None
    assert row.last_activity == "2026-08-24T00:00:00Z", "a stamp was invented"
