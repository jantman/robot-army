"""``cards`` and ``worktree list`` distinguish absence from withholding (milestone 008).

Neither of these commands displays a section that contradicts them, so neither reproduced
issue #13's visible self-contradiction. Both nonetheless reported "nothing is tracked" and
"nothing is recorded" while withholding rows, which leaves the reader unable to tell the
two situations apart — the same defect one step quieter.
"""

from __future__ import annotations

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import db, operations
from robot_army.effects import EffectLevel

FLAG = "--include-simulated"
NOTE = "simulated rows withheld — pass --include-simulated to show them"


def listing_context(config, conn, audit):
    return operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )


def add_card(conn, card_id, *, dry_run):
    with db.transaction(conn):
        db.insert_card(
            conn,
            board_id="board-1",
            card_id=card_id,
            card_url=f"https://trello.com/c/{card_id}",
            title="a card",
            body="",
            dry_run=dry_run,
        )


def add_worktree_item(conn, issue_number, *, dry_run, path="/tmp/wt"):
    item = seed_item(conn, issue_number=issue_number, dry_run=dry_run)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item, worktree_path=f"{path}-{issue_number}", branch=f"b/{issue_number}"
        )
    return item


# -- cards ------------------------------------------------------------------


@pytest.fixture
def cards_ctx(board_config, conn, audit):
    return listing_context(board_config, conn, audit)


def test_cards_says_what_it_withheld_instead_of_claiming_nothing_is_tracked(cards_ctx, conn):
    add_card(conn, "card-1", dry_run=True)
    add_card(conn, "card-2", dry_run=True)

    text = "\n".join(operations.cards(cards_ctx).lines)

    assert "no cards tracked yet" not in text
    assert f"no cards visible (2 {NOTE})" in text


def test_cards_discloses_withheld_rows_beside_the_ones_it_shows(cards_ctx, conn):
    add_card(conn, "card-1", dry_run=False)
    add_card(conn, "card-2", dry_run=True)

    text = "\n".join(operations.cards(cards_ctx).lines)

    assert "card-1" in text
    assert f"1 {NOTE}" in text


def test_cards_keeps_its_original_message_when_nothing_was_withheld(cards_ctx, conn):
    text = "\n".join(operations.cards(cards_ctx).lines)

    assert text.splitlines() == ["no cards tracked yet"]


def test_cards_discloses_nothing_when_simulated_rows_were_asked_for(cards_ctx, conn):
    add_card(conn, "card-1", dry_run=True)

    text = "\n".join(operations.cards(cards_ctx, include_simulated=True).lines)

    assert "withheld" not in text
    assert "card-1" in text


# -- worktree list ----------------------------------------------------------


@pytest.fixture
def wt_ctx(config, conn, audit):
    return listing_context(config, conn, audit)


def test_worktree_list_says_what_it_withheld_instead_of_claiming_nothing_recorded(wt_ctx, conn):
    add_worktree_item(conn, 26, dry_run=True)
    add_worktree_item(conn, 27, dry_run=True)

    text = "\n".join(operations.worktree_list(wt_ctx).lines)

    assert "no worktrees recorded" not in text
    assert f"no worktrees visible (2 {NOTE})" in text


def test_worktree_list_discloses_withheld_rows_beside_the_ones_it_shows(wt_ctx, conn):
    add_worktree_item(conn, 26, dry_run=True)
    add_worktree_item(conn, 31, dry_run=False)

    text = "\n".join(operations.worktree_list(wt_ctx).lines)

    assert "/tmp/wt-31" in text
    assert f"1 {NOTE}" in text


def test_worktree_list_keeps_its_original_message_when_nothing_was_withheld(wt_ctx, conn):
    text = "\n".join(operations.worktree_list(wt_ctx).lines)

    assert text.splitlines() == ["no worktrees recorded"]


def test_a_simulated_item_with_no_worktree_was_never_withheld_from_this_listing(wt_ctx, conn):
    """It was never in the listing, so it cannot have been withheld from it — counting it
    would state a number that ``--include-simulated`` does not reveal here."""
    seed_item(conn, issue_number=26, dry_run=True)
    add_worktree_item(conn, 27, dry_run=True)

    text = "\n".join(operations.worktree_list(wt_ctx).lines)

    assert f"no worktrees visible (1 {NOTE})" in text


def test_worktree_list_discloses_nothing_when_simulated_rows_were_asked_for(wt_ctx, conn):
    add_worktree_item(conn, 26, dry_run=True)

    text = "\n".join(operations.worktree_list(wt_ctx, include_simulated=True).lines)

    assert "withheld" not in text
    assert "/tmp/wt-26" in text
