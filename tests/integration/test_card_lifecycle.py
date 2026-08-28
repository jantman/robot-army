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
    make_board_info,
    make_card,
    seed_item,
    seed_session,
    with_ignore_lists,
)

from robot_army import db, intake, operations, reconcile
from robot_army.cardstates import CardState
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
    from tests.conftest import make_board_info

    boundaries = make_board_boundaries(
        audit,
        cards=[card_named()],
        board=make_board_info(
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


# -- the ignore list never touches work already in flight (milestone 006) ---


def _ignoring(config, *names):
    return with_ignore_lists(config, *names)


def test_a_linked_cards_full_lifecycle_runs_with_every_column_ignored(
    conn, board_config, audit
):
    """FR-014 and SC-007, at its widest setting.

    Every column on the board is excluded, and the card still goes to in-progress on
    confirmation and to done on close. A move *into* an excluded column is ordinary: it is
    the daemon reporting on work, not the board offering it.
    """
    config = _ignoring(board_config, "Inbox", "In Progress", "Done")
    boundaries = make_board_boundaries(audit, cards=[card_named()])
    # Linked first, under the ordinary configuration — the card has to get past intake
    # before "already in flight" means anything.
    status = intake.check_board(boundaries=boundaries, audit=audit, config=board_config)
    intake.run_cycle(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        status=status,
        dry_run=False,
    )
    card_row = db.list_cards(conn)[0]
    assert card_row.state is CardState.LINKED

    with db.transaction(conn):
        db.upsert_repo(conn, repo_key=REPO, settings_fingerprint=None, trust_verified=True)
    item_id = seed_item(
        conn, repo_key=REPO, issue_number=card_row.issue_number, state="dispatching"
    )

    intake.on_session_active(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo_key=REPO,
        issue_number=card_row.issue_number,
        dry_run=False,
    )
    assert db.list_cards(conn)[0].placed_list_id == DOING

    conn.execute("UPDATE work_items SET state = 'active' WHERE id = ?", (item_id,))
    boundaries.issue_reader.closed[(REPO, card_row.issue_number)] = True
    reconcile.reconcile(
        conn, boundaries=boundaries, audit=audit, config=config, layout=config.layout
    )

    row = db.list_cards(conn)[0]
    assert row.state is CardState.LINKED, "the mapping must survive"
    assert row.placed_list_id == DONE
    assert boundaries.card_writer.moves == [("card-1", DOING), ("card-1", DONE)]


def test_an_abandoned_cards_return_is_unaffected_by_the_ignore_list(conn, board_config, audit):
    """FR-014 again, on the path that is easiest to break: the return to origin reads
    ``origin_list_id``, which the ignore list must not touch."""
    boundaries = board(conn, board_config, audit, [card_named()])
    card_row = db.list_cards(conn)[0]
    config = _ignoring(board_config, "Inbox", "In Progress")

    intake.on_session_active(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo_key=REPO,
        issue_number=card_row.issue_number,
        dry_run=False,
    )
    intake.on_work_abandoned(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo_key=REPO,
        issue_number=card_row.issue_number,
        reason="the session failed",
        dry_run=False,
    )

    assert boundaries.card_writer.moves == [("card-1", DOING), ("card-1", INBOX)]
    assert db.list_cards(conn)[0].placed_list_id == INBOX


def test_parking_a_linked_card_keeps_its_mapping_and_writes_no_record(
    conn, board_config, audit, layout
):
    """FR-013. Dragging a card with an issue into an excluded column changes nothing, and
    does not produce a park record either — a linked card is not parked, it is finished."""
    import json

    # The board must actually *have* the excluded column. An earlier version of this test
    # did not give it one, so `check_board` failed its own precondition, `run_cycle`
    # returned on `status.ok is False`, and every assertion below passed because the second
    # cycle did nothing at all — the test would have passed with the exclusion deleted.
    lists = {"In Progress": DOING, "Done": DONE, "Inbox": INBOX, "Icebox": "list-ice"}
    boundaries = make_board_boundaries(
        audit, cards=[card_named()], board=make_board_info(lists=lists)
    )
    status = intake.check_board(boundaries=boundaries, audit=audit, config=board_config)
    intake.run_cycle(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        status=status,
        dry_run=False,
    )
    card_row = db.list_cards(conn)[0]
    assert card_row.state is CardState.LINKED
    config = _ignoring(board_config, "Icebox")

    move_on_board(boundaries, "card-1", "list-ice")
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)
    assert status.ok, "the second cycle must really run, or this test asserts nothing"
    intake.run_cycle(
        conn, boundaries=boundaries, audit=audit, config=config, status=status, dry_run=False
    )
    assert db.list_cards(conn)[0].current_list_id == "list-ice", (
        "the refresh must have seen the move, or the guard under test was never reached"
    )

    row = db.list_cards(conn)[0]
    assert row.state is CardState.LINKED
    assert row.repo_key == REPO
    assert row.issue_number == card_row.issue_number

    audit.close()
    actions = [
        json.loads(line)
        for path in layout.log_dir.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert not [r for r in actions if r["action"] == "trello.parked"]
