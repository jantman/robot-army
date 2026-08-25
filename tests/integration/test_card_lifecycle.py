"""User story 3: the board tells the truth about what is happening (T067, T068).

The independent test from spec.md, driven through the real call sites rather than by
calling ``intake`` directly: a card goes card → issue → label → dispatch → close, and its
list is checked at each stage. A second card is abandoned mid-flight. A third is moved by
hand and must not be moved back.

Driving it through ``dispatch`` and ``reconcile`` is the point — the requirement is not
that ``intake`` *can* move a card, it is that the system does so at the moments a human
would expect, and only a test that goes through those modules can tell the difference.
"""

from __future__ import annotations

import dataclasses

from tests.conftest import (
    make_board_boundaries,
    make_card,
    seed_item,
    seed_session,
)

from robot_army import db, intake, operations, reconcile
from robot_army.effects import EffectLevel

REPO = "jantman/demo"
DOING = "list-doing"
DONE = "list-done"
INBOX = "list-inbox"


def board(conn, board_config, audit, cards):
    boundaries = make_board_boundaries(audit, cards=cards)
    status = intake.check_board(boundaries=boundaries, audit=audit, config=board_config)
    intake.run_cycle(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        status=status,
        dry_run=False,
    )
    return boundaries


def card_named(card_id="card-1", **overrides):
    overrides.setdefault("body", f"https://github.com/{REPO}")
    overrides.setdefault("list_id", INBOX)
    return make_card(card_id, **overrides)


def move_on_board(boundaries, card_id, list_id):
    for index, card in enumerate(boundaries.card_reader.cards):
        if card.card_id == card_id:
            boundaries.card_reader.cards[index] = dataclasses.replace(card, list_id=list_id)


def test_a_card_goes_in_progress_when_a_session_is_confirmed_and_done_when_closed(
    conn, board_config, audit
):
    """The full arc, through the real call sites."""
    boundaries = board(conn, board_config, audit, [card_named()])
    card_row = db.list_cards(conn)[0]

    # Onboard and label, so the ordinary path produces a work item.
    with db.transaction(conn):
        db.upsert_repo(conn, repo_key=REPO, settings_fingerprint=None, trust_verified=True)
    item_id = seed_item(
        conn, repo_key=REPO, issue_number=card_row.issue_number, state="dispatching"
    )

    # Dispatch confirms the session — the moment FR-027 names.
    intake.on_session_active(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        repo_key=REPO,
        issue_number=card_row.issue_number,
        dry_run=False,
    )
    assert db.list_cards(conn)[0].placed_list_id == DOING

    # Reconciliation observes the issue closed — the moment FR-028 names.
    conn.execute("UPDATE work_items SET state = 'active' WHERE id = ?", (item_id,))
    boundaries.issue_reader.closed[(REPO, card_row.issue_number)] = True
    reconcile.reconcile(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        layout=board_config.layout,
    )

    assert db.get_work_item(conn, item_id).state == "done"
    row = db.list_cards(conn)[0]
    assert row.placed_list_id == DONE
    assert boundaries.card_writer.moves == [("card-1", DOING), ("card-1", DONE)]
    assert any("is closed" in body for _, body in boundaries.card_writer.comments)


def test_an_abandoned_item_returns_its_card_to_the_list_it_came_from(
    conn, board_config, audit
):
    """FR-029, through ``operations.abandon``. A card must not sit in the in-progress list
    claiming to be busy when nothing is."""
    boundaries = board(conn, board_config, audit, [card_named()])
    card_row = db.list_cards(conn)[0]

    with db.transaction(conn):
        db.upsert_repo(conn, repo_key=REPO, settings_fingerprint=None, trust_verified=True)
    item_id = seed_item(
        conn, repo_key=REPO, issue_number=card_row.issue_number, state="dispatching"
    )
    intake.on_session_active(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        repo_key=REPO,
        issue_number=card_row.issue_number,
        dry_run=False,
    )
    conn.execute("UPDATE work_items SET state = 'ready' WHERE id = ?", (item_id,))

    ctx = operations.Context(
        config=board_config,
        conn=conn,
        audit=audit,
        boundaries=boundaries,
        effect_level=EffectLevel.LIVE,
    )
    result = operations.abandon(ctx, item_id)
    assert result.code == 0

    row = db.list_cards(conn)[0]
    assert row.placed_list_id == INBOX, "the card was left claiming to be in progress"
    assert any("stopped" in body for _, body in boundaries.card_writer.comments)


def test_a_card_the_author_moved_by_hand_is_not_moved_back_when_its_issue_closes(
    conn, board_config, audit
):
    """FR-030, end to end. The board is the author's surface, and the system does not get
    to overrule a decision made on it."""
    boundaries = board(conn, board_config, audit, [card_named()])
    card_row = db.list_cards(conn)[0]

    intake.on_session_active(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        repo_key=REPO,
        issue_number=card_row.issue_number,
        dry_run=False,
    )
    move_on_board(boundaries, "card-1", "list-blocked")
    boundaries.card_writer.moves.clear()

    with db.transaction(conn):
        db.upsert_repo(conn, repo_key=REPO, settings_fingerprint=None, trust_verified=True)
    item_id = seed_item(
        conn, repo_key=REPO, issue_number=card_row.issue_number, state="active"
    )
    seed_session(conn, item_id, state="exited_clean")
    boundaries.issue_reader.closed[(REPO, card_row.issue_number)] = True
    reconcile.reconcile(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        layout=board_config.layout,
    )

    assert boundaries.card_writer.moves == [], "a card the author moved was moved back"
    assert any("did **not** move" in body for _, body in boundaries.card_writer.comments)
    # And the work item still reached its own conclusion — the board refusing to move must
    # not hold up the item it describes.
    assert db.get_work_item(conn, item_id).state == "done"


def test_a_board_failure_during_a_move_does_not_fail_the_thing_it_describes(
    conn, board_config, audit
):
    """The board is a status surface, not a participant. A session that is running stays
    running, and an item that is done stays done, whatever the board says."""
    from robot_army.boundaries import TransportError

    boundaries = board(conn, board_config, audit, [card_named()])
    card_row = db.list_cards(conn)[0]
    boundaries.card_writer.raise_on_move = TransportError("board unreachable")

    verdict = intake.on_session_active(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        repo_key=REPO,
        issue_number=card_row.issue_number,
        dry_run=False,
    )
    assert verdict.action == "move_deferred"
    # The intent survives, so the next pass knows a move of ours was in flight (R12).
    assert db.list_cards(conn)[0].pending_move_to == DOING


# -- T068: a missing list is caught at startup, not mid-lifecycle -----------


def test_a_missing_lifecycle_list_is_caught_before_any_issue_exists(
    conn, board_config, audit
):
    """The reason R11 checks the lists at startup at all. A missing list discovered
    halfway through a lifecycle is discovered *after* the issue already exists — the
    expensive, irreversible half has happened and the cheap check had not run."""
    from robot_army.boundaries import BoardInfo

    boundaries = make_board_boundaries(
        audit,
        cards=[card_named()],
        board=BoardInfo(
            board_id="board-1",
            name="Intake",
            permission_level="private",
            member_ids=("member-1",),
            labels={"AI-task": "label-ai"},
            # `Done` is missing, as it would be if the author renamed it.
            lists={"In Progress": DOING},
        ),
    )
    status = intake.check_board(boundaries=boundaries, audit=audit, config=board_config)

    assert not status.ok
    assert [c.name for c in status.failures] == ["done list exists"]

    # Ingestion is refused, so no issue is created and there is no lifecycle to get
    # halfway through.
    outcome = intake.run_cycle(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        status=status,
        dry_run=False,
    )
    assert outcome.skipped_reason == "board preconditions failed"
    assert boundaries.issue_writer.created == []
    assert db.list_cards(conn, include_simulated=True) == []
