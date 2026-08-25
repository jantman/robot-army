"""The manual-move refusal, and the interrupted-move case it must not mistake (T066, R12).

FR-030 exists because the board is the author's own working surface. A system that
silently drags a card back to where it thinks it belongs is fighting its user, and the
author will lose that argument slowly and annoyingly.

The subtle half is ``pending_move_to``. Killed after a move landed but before it was
recorded, the next pass finds the card in a list we do not think we put it in — and
without the intent record it would conclude the *author* moved it and freeze the card's
lifecycle permanently. Both readings are tested, because getting them backwards produces
two different bugs and neither is obvious from the code.
"""

from __future__ import annotations

import dataclasses

from tests.conftest import make_board_boundaries, make_card

from robot_army import db, intake
from robot_army.cardstates import CardState

REPO = "jantman/demo"
DOING = "list-doing"
DONE = "list-done"
INBOX = "list-inbox"


def linked_card(conn, board_config, audit, *, list_id=INBOX):
    """One card, already linked to an issue, sitting where the author left it."""
    boundaries = make_board_boundaries(
        audit,
        cards=[make_card("card-1", body=f"https://github.com/{REPO}", list_id=list_id)],
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
    return boundaries


def board_card(boundaries, **changes):
    boundaries.card_reader.cards[0] = dataclasses.replace(
        boundaries.card_reader.cards[0], **changes
    )


def active(conn, board_config, audit, boundaries):
    return intake.on_session_active(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        repo_key=REPO,
        issue_number=101,
        dry_run=False,
    )


def closed(conn, board_config, audit, boundaries):
    return intake.on_issue_closed(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        repo_key=REPO,
        issue_number=101,
        dry_run=False,
    )


# -- the ordinary move ------------------------------------------------------


def test_a_confirmed_session_moves_the_card_and_records_where_it_came_from(
    conn, board_config, audit
):
    boundaries = linked_card(conn, board_config, audit)
    verdict = active(conn, board_config, audit, boundaries)

    assert verdict.action == "moved"
    assert boundaries.card_writer.moves == [("card-1", DOING)]
    row = db.list_cards(conn)[0]
    assert row.placed_list_id == DOING
    assert row.origin_list_id == INBOX
    # The intent is cleared once the move is recorded, so it cannot be mistaken for a
    # pending one on a later pass.
    assert row.pending_move_to is None


def test_a_closed_issue_moves_the_card_to_done_with_an_outcome_comment(
    conn, board_config, audit
):
    boundaries = linked_card(conn, board_config, audit)
    active(conn, board_config, audit, boundaries)
    closed(conn, board_config, audit, boundaries)

    assert boundaries.card_writer.moves == [("card-1", DOING), ("card-1", DONE)]
    assert any("is closed" in body for _, body in boundaries.card_writer.comments)
    assert db.list_cards(conn)[0].placed_list_id == DONE


def test_moving_a_card_that_is_already_there_makes_no_call(conn, board_config, audit):
    boundaries = linked_card(conn, board_config, audit, list_id=DOING)
    verdict = active(conn, board_config, audit, boundaries)
    assert verdict.action == "move_unnecessary"
    assert boundaries.card_writer.moves == []


# -- the refusal (FR-030) ---------------------------------------------------


def test_a_card_the_author_moved_is_not_moved_back(conn, board_config, audit):
    boundaries = linked_card(conn, board_config, audit)
    active(conn, board_config, audit, boundaries)
    assert db.list_cards(conn)[0].placed_list_id == DOING

    # The author drags it somewhere of their own.
    board_card(boundaries, list_id="list-blocked")
    boundaries.card_writer.moves.clear()

    verdict = closed(conn, board_config, audit, boundaries)
    assert verdict.action == "move_refused"
    assert boundaries.card_writer.moves == [], "the system moved a card the author had moved"


def test_the_refusal_comments_with_what_it_would_have_done(conn, board_config, audit):
    boundaries = linked_card(conn, board_config, audit)
    active(conn, board_config, audit, boundaries)
    board_card(boundaries, list_id="list-blocked")
    boundaries.card_writer.comments.clear()

    closed(conn, board_config, audit, boundaries)
    bodies = [body for _, body in boundaries.card_writer.comments]
    assert bodies, "the refusal was silent"
    assert "did **not** move this card" in bodies[0]
    assert DONE in bodies[0]


def test_the_refusal_is_recorded_with_both_lists(conn, board_config, audit, layout):
    import json

    boundaries = linked_card(conn, board_config, audit)
    active(conn, board_config, audit, boundaries)
    board_card(boundaries, list_id="list-blocked")
    closed(conn, board_config, audit, boundaries)
    audit.close()

    records = [
        json.loads(line)
        for path in sorted(layout.log_dir.glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    refusals = [r for r in records if r["action"] == "trello.card.move_refused"]
    assert len(refusals) == 1
    detail = refusals[0]["detail"]
    assert detail["card_is_in"] == "list-blocked"
    assert detail["we_last_placed_it_in"] == DOING
    assert detail["would_have_moved_to"] == DONE


def test_a_card_we_have_never_placed_is_not_treated_as_moved_by_the_author(
    conn, board_config, audit
):
    """The first move of a card's life. ``placed_list_id`` is NULL, and wherever the card
    is, is where it started — so there is nothing to refuse."""
    boundaries = linked_card(conn, board_config, audit)
    assert db.list_cards(conn)[0].placed_list_id is None
    assert active(conn, board_config, audit, boundaries).action == "moved"


# -- the interrupted move (R12) ---------------------------------------------


def test_an_interrupted_move_of_ours_is_recognised_as_ours(conn, board_config, audit):
    """Killed after the move landed, before it was recorded. Without ``pending_move_to``
    this looks exactly like the author moving the card, and the card's lifecycle would
    freeze permanently at the first interruption."""
    boundaries = linked_card(conn, board_config, audit)
    row = db.list_cards(conn)[0]

    # The state a kill between the move and its record leaves behind: the intent is
    # written, the board has moved, and nothing local knows.
    with db.transaction(conn):
        db.update_card_columns(conn, row.id, pending_move_to=DOING, placed_list_id=INBOX)
    board_card(boundaries, list_id=DOING)

    verdict = active(conn, board_config, audit, boundaries)
    assert verdict.action == "move_unnecessary"
    after = db.list_cards(conn)[0]
    assert after.placed_list_id == DOING, "our own move was not adopted"
    assert after.pending_move_to is None


def test_an_interrupted_move_does_not_excuse_a_later_human_move(conn, board_config, audit):
    """The intent is cleared once adopted, so the *next* unexpected list is read as the
    author's — which it is."""
    boundaries = linked_card(conn, board_config, audit)
    row = db.list_cards(conn)[0]
    with db.transaction(conn):
        db.update_card_columns(conn, row.id, pending_move_to=DOING, placed_list_id=INBOX)
    board_card(boundaries, list_id=DOING)
    active(conn, board_config, audit, boundaries)

    board_card(boundaries, list_id="list-blocked")
    assert closed(conn, board_config, audit, boundaries).action == "move_refused"


def test_a_card_that_has_been_deleted_is_reported_rather_than_moved(
    conn, board_config, audit
):
    boundaries = linked_card(conn, board_config, audit)
    boundaries.card_reader.cards.clear()
    assert active(conn, board_config, audit, boundaries).action == "card_gone"
    assert boundaries.card_writer.moves == []


def test_an_issue_that_came_from_no_card_moves_nothing(conn, board_config, audit):
    boundaries = make_board_boundaries(audit)
    assert (
        intake.on_session_active(
            conn,
            boundaries=boundaries,
            audit=audit,
            config=board_config,
            repo_key=REPO,
            issue_number=999,
            dry_run=False,
        )
        is None
    )
    assert boundaries.card_writer.moves == []


def test_an_installation_with_no_board_moves_nothing(conn, config, audit):
    boundaries = make_board_boundaries(audit)
    assert (
        intake.on_session_active(
            conn,
            boundaries=boundaries,
            audit=audit,
            config=config,
            repo_key=REPO,
            issue_number=101,
            dry_run=False,
        )
        is None
    )


# -- abandonment (FR-029) ---------------------------------------------------


def test_abandoned_work_returns_the_card_to_where_it_came_from(conn, board_config, audit):
    boundaries = linked_card(conn, board_config, audit)
    active(conn, board_config, audit, boundaries)
    assert db.list_cards(conn)[0].placed_list_id == DOING

    verdict = intake.on_work_abandoned(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        repo_key=REPO,
        issue_number=101,
        reason="the maintainer abandoned it",
        dry_run=False,
    )
    assert verdict.action == "moved"
    assert boundaries.card_writer.moves[-1] == ("card-1", INBOX)
    assert db.list_cards(conn)[0].placed_list_id == INBOX
    assert any("abandoned it" in body for _, body in boundaries.card_writer.comments)


def test_a_linked_card_keeps_its_state_through_every_move(conn, board_config, audit):
    """The lifecycle happens on the *board*. ``linked`` is terminal in the database, and
    moving a card is not a state change (data-model.md)."""
    boundaries = linked_card(conn, board_config, audit)
    active(conn, board_config, audit, boundaries)
    closed(conn, board_config, audit, boundaries)
    assert db.list_cards(conn)[0].state == CardState.LINKED


# -- the board is not touched for work that never came from a card ---------


def test_an_issue_with_no_card_costs_no_board_request(conn, board_config, audit):
    """``on_session_active`` and ``on_issue_closed`` sit in per-item hot paths — every
    dispatched item, every closed issue — and the great majority never came from a card.

    Resolving the lifecycle list is a board round trip; finding the card is one indexed
    local query. Passing the resolved list id as a call *argument* made Python evaluate it
    before the cheap check could short-circuit, so every dispatch and every close fetched
    the board and wrote a ``trello.board.check`` record, card or no card.
    """
    boundaries = make_board_boundaries(audit)
    reader = boundaries.card_reader

    assert active(conn, board_config, audit, boundaries) is None
    assert closed(conn, board_config, audit, boundaries) is None
    assert reader.board_calls == 0, "the board was queried for an issue with no card"
    assert boundaries.card_writer.moves == []


def test_an_abandoned_item_with_no_card_costs_no_board_request(conn, board_config, audit):
    boundaries = make_board_boundaries(audit)
    result = intake.on_work_abandoned(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        repo_key=REPO,
        issue_number=999,
        reason="abandoned",
        dry_run=False,
    )
    assert result is None
    assert boundaries.card_reader.board_calls == 0


def test_the_real_reader_memoises_board_info(board_config, audit):
    """R10 fixes the frequency at once per process plus a documented restart, and the
    reader's memo is what makes that true rather than aspirational — without it, moving one
    card cost four board requests and a duplicate ``trello.board.check`` record each time.

    Asserted against the real client, because the fake cannot stand in for a memo that
    lives inside the implementation.
    """
    import httpx

    from robot_army.boundaries.trello import TrelloCardReader

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/boards/board-1"):
            return httpx.Response(200, json={"name": "Intake", "prefs": {"permissionLevel": "private"}})
        return httpx.Response(200, json=[])

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=board_config.trello.api_base
    )
    reader = TrelloCardReader(board_config, audit, client=client, sleep=lambda _s: None)

    first = reader.board_info()
    after_first = len(requests)
    assert after_first == 4, requests

    for _ in range(5):
        assert reader.board_info() is first
    assert len(requests) == after_first, "board_info was re-fetched"


def test_a_failed_board_read_is_not_memoised(board_config, audit):
    """Only a successful read is stored. A board that was unreachable at startup must be
    retried, not written off for the life of the process."""
    import httpx
    import pytest

    from robot_army.boundaries import TransportError
    from robot_army.boundaries.trello import TrelloCardReader

    state = {"fail": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path.endswith("/boards/board-1"):
            return httpx.Response(200, json={"name": "Intake", "prefs": {"permissionLevel": "private"}})
        return httpx.Response(200, json=[])

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=board_config.trello.api_base
    )
    reader = TrelloCardReader(board_config, audit, client=client, sleep=lambda _s: None)

    with pytest.raises(TransportError):
        reader.board_info()

    state["fail"] = False
    assert reader.board_info().name == "Intake"
