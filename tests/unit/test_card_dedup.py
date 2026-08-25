"""Duplicate suppression, and the order it happens in (T036, R7, §11).

§11 is explicit that the marker comment is "a **recovery marker** for rebuilding state
after DB loss — not the primary key. Don't parse comments as the authoritative source in
normal operation." That is not a style note; it is an ordering rule with an observable
consequence, and the observable consequence is what these tests assert:

**With a mapping row present, the card's comments are never fetched.** The fake board
counts the calls, so "never" is checked rather than trusted.
"""

from __future__ import annotations

from tests.conftest import FakeCardReader, make_board_boundaries, make_card

from robot_army import db, intake
from robot_army.cardstates import CardState


def board_with(audit, cards, **kwargs):
    return make_board_boundaries(audit, cards=cards, **kwargs)


def cycle(conn, board_config, audit, boundaries, *, dry_run: bool = False):
    status = intake.check_board(boundaries=boundaries, audit=audit, config=board_config)
    return intake.run_cycle(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        status=status,
        dry_run=dry_run,
    )


def resolvable_card(card_id="card-1"):
    return make_card(card_id, body="fix https://github.com/jantman/demo please")


def demo_config(board_config):
    """The default fixture keys its repository ``demo``; cards name ``jantman/demo``."""
    return board_config


def test_a_linked_card_is_a_no_op_and_its_comments_are_never_read(
    conn, board_config, audit, monkeypatch
):
    boundaries = board_with(audit, [resolvable_card()])
    cycle(conn, board_config, audit, boundaries)

    reader: FakeCardReader = boundaries.card_reader
    created_once = len(boundaries.issue_writer.created)
    assert created_once == 1
    comment_reads_after_creation = len(reader.comment_calls)

    # A second, third and fourth pass over the same card.
    for _ in range(3):
        cycle(conn, board_config, audit, boundaries)

    assert len(boundaries.issue_writer.created) == 1, "a second issue was filed"
    assert len(boundaries.card_writer.comments) == 1, "the card was commented on twice"
    # The rule with teeth: no further comment reads happened at all.
    assert len(reader.comment_calls) == comment_reads_after_creation


def test_the_mapping_is_consulted_before_the_board(conn, board_config, audit):
    """A card that is already linked must not cause *any* board read beyond the listing —
    the mapping row is the authority in normal operation."""
    boundaries = board_with(audit, [resolvable_card()])
    cycle(conn, board_config, audit, boundaries)
    reader: FakeCardReader = boundaries.card_reader
    reader.comment_calls.clear()

    cycle(conn, board_config, audit, boundaries)
    assert reader.comment_calls == []


def test_repeated_evaluation_of_a_linked_card_changes_nothing(conn, board_config, audit):
    boundaries = board_with(audit, [resolvable_card()])
    cycle(conn, board_config, audit, boundaries)
    before = db.list_cards(conn)[0]

    row_id = before.id
    for _ in range(5):
        verdict = intake.evaluate_card(
            conn,
            boundaries=boundaries,
            audit=audit,
            config=board_config,
            card_row_id=row_id,
            dry_run=False,
        )
        assert verdict.action == "already_linked"
    after = db.list_cards(conn)[0]
    assert (after.state, after.issue_number) == (CardState.LINKED, before.issue_number)


def test_a_card_with_no_mapping_does_read_its_comments(conn, board_config, audit):
    """The other half of the ordering rule. With no mapping, the marker is the only thing
    standing between a lost database and a second issue, so it *must* be read (R7)."""
    boundaries = board_with(audit, [resolvable_card()])
    cycle(conn, board_config, audit, boundaries)
    # Twice, and both are required: once before creating anything, in case a lost database
    # left a marker behind, and once before posting, so a retried step 4 cannot double-post
    # (T072). The count is not the property — that comments are read at all is.
    assert boundaries.card_reader.comment_calls == ["card-1", "card-1"]


def test_the_schema_refuses_a_second_row_even_if_the_guard_were_skipped(conn):
    """The invariant is two unique indexes, not a rule the create path follows. A create
    that skipped its mapping check raises rather than duplicating — which is loud."""
    import sqlite3

    import pytest

    with db.transaction(conn):
        db.insert_card(
            conn,
            board_id="board-1",
            card_id="card-1",
            card_url="https://trello.com/c/card-1",
            title="t",
            body="",
            dry_run=False,
        )
    with pytest.raises(sqlite3.IntegrityError), db.transaction(conn):
        conn.execute(
            """
            INSERT INTO cards (board_id, card_id, card_url, title, body, state, dry_run,
                               first_seen_at, updated_at)
            VALUES ('board-1', 'card-1', 'u', 't', '', 'discovered', 0, 'now', 'now')
            """
        )
