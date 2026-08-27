"""Cards in an ignored column are not intake (milestone 006).

Two tests here are worth more than the rest, and both guard a failure that would be
invisible rather than loud.

``test_a_parked_card_is_not_dropped`` is the regression test for the trap the whole design
turns on. ``_reconcile_board_contents`` drops every tracked card absent from the poll
listing, and ``dropped`` is terminal — ``CARD_TRANSITIONS`` gives it no exit. The obvious
implementation of an ignore list, filtering ignored cards out of what the reader returns,
would therefore make parking an already-tracked card destroy it permanently, and
un-parking would do nothing, silently, forever.

``test_the_gate_sits_after_linked_dropped_and_creating`` and its neighbours are one case
per row of contracts/surfaces.md's ordering table. The gate's position inside
``evaluate_card`` is a contract: each neighbour is fixed by a different requirement, so a
reordering breaks exactly one of them and nothing else notices.
"""

from __future__ import annotations

import pytest
from tests.conftest import (
    make_board_boundaries,
    make_board_info,
    make_boundaries,
    make_card,
    with_ignore_lists,
)

from robot_army import db, intake, operations
from robot_army.cardstates import CardState, transition_card
from robot_army.effects import EffectLevel

#: The board every test here runs against. ``Icebox`` is the parking column.
LISTS = {
    "Inbox": "list-inbox",
    "Icebox": "list-ice",
    "In Progress": "list-doing",
    "Done": "list-done",
}


def board(**overrides):
    overrides.setdefault("lists", LISTS)
    return make_board_info(**overrides)


def run(config, audit, conn, cards, *, forced=False):
    """One whole board cycle, returning the outcome and the boundaries it used."""
    boundaries = make_board_boundaries(audit, cards=cards, board=board())
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)
    outcome = intake.run_cycle(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        status=status,
        dry_run=False,
        forced=forced,
    )
    return outcome, boundaries


def track(conn, audit, card, *, state=None, **columns):
    """Insert a tracked row for ``card`` and walk it to ``state``.

    The walk goes through ``transition_card`` rather than writing the column directly, so
    these fixtures cannot set up a state the real machine would refuse.
    """
    with db.transaction(conn):
        row_id = db.insert_card(
            conn,
            board_id="board-1",
            card_id=card.card_id,
            card_url=card.url,
            title=card.title,
            body=card.body,
            dry_run=False,
            last_activity=card.last_activity,
            origin_list_id=card.list_id,
            current_list_id=card.list_id,
        )
    if columns:
        with db.transaction(conn):
            db.update_card_columns(conn, row_id, **columns)
    for target in _walk_to(state):
        with db.transaction(conn):
            transition_card(conn, audit, card_row_id=row_id, target=target, reason="fixture")
    return row_id


def _walk_to(state):
    """The legal route from ``discovered`` to ``state``, per ``CARD_TRANSITIONS``."""
    if state is None or state is CardState.DISCOVERED:
        return ()
    if state is CardState.LINKED:
        return (CardState.CREATING, CardState.LINKED)
    return (state,)


def listing_context(config, conn, audit):
    """A context for the read-only listing commands.

    Deliberately carries **no board reader**: `robot-army cards` must answer "is this
    parked?" with the board unreachable, and a fixture that handed it one would let a
    board request creep in unnoticed.
    """
    return operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )


def card_row(conn, card_id="card-1"):
    return db.find_card(conn, board_id="board-1", card_id=card_id, dry_run=False)


# -- the predicate (T016) ---------------------------------------------------


@pytest.mark.parametrize(
    ("list_id", "expected"),
    [
        ("list-ice", True),
        ("list-inbox", False),
        (None, False),
        ("", False),
    ],
)
def test_is_ignored_answers_only_for_a_column_we_actually_read(list_id, expected):
    """A missing or empty ``list_id`` is not ignored: Trello does not produce a card
    without a column, so that is a value we failed to read rather than a parked card, and
    the safe direction for such a value is milestone 003's behaviour."""
    status = intake.BoardStatus(checks=(), ignored_list_ids=frozenset({"list-ice"}))
    assert intake._is_ignored(list_id, status) is expected


def test_an_empty_ignored_set_makes_every_call_false():
    status = intake.BoardStatus(checks=())
    assert not intake._is_ignored("list-ice", status)
    assert not intake._is_ignored("list-inbox", status)


def test_check_board_resolves_configured_names_to_ids(board_config, audit):
    config = with_ignore_lists(board_config, "Icebox", "Done")
    boundaries = make_board_boundaries(audit, board=board())
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)

    assert status.ignored_list_ids == frozenset({"list-ice", "list-done"})


def test_an_unconfigured_ignore_list_resolves_to_nothing(board_config, audit):
    boundaries = make_board_boundaries(audit, board=board())
    status = intake.check_board(boundaries=boundaries, audit=audit, config=board_config)

    assert status.ignored_list_ids == frozenset()


# -- User Story 1: a card in an ignored column is not intake ----------------


def test_an_ignored_card_is_not_tracked_and_produces_nothing(board_config, audit, conn):
    """FR-003, FR-004, FR-006. Not tracked, so it cannot be surfaced by anything that
    reads rows — which is every listing there is."""
    config = with_ignore_lists(board_config, "Icebox")
    parked = make_card("card-1", list_id="list-ice", body="in https://github.com/jantman/demo")

    outcome, boundaries = run(config, audit, conn, [parked])

    assert card_row(conn) is None
    assert outcome.found == 1
    assert outcome.ignored == 1
    assert outcome.created == 0
    assert outcome.issues_created == 0
    assert boundaries.card_writer.comments == []
    assert boundaries.card_writer.moves == []


def test_an_ignored_card_that_names_no_repository_is_not_held_for_info(board_config, audit, conn):
    """FR-005. Exclusion is decided *before* resolvability, so a card the author parked is
    never recorded as awaiting clarification and never commented on to ask about it."""
    config = with_ignore_lists(board_config, "Icebox")
    vague = make_card("card-1", list_id="list-ice", body="no repository here at all")

    outcome, boundaries = run(config, audit, conn, [vague])

    assert card_row(conn) is None
    assert outcome.held == 0
    assert boundaries.card_writer.comments == []


def test_a_card_in_an_ordinary_column_is_untouched_by_the_feature(board_config, audit, conn):
    config = with_ignore_lists(board_config, "Icebox")
    ordinary = make_card("card-1", list_id="list-inbox", body="in https://github.com/jantman/demo")

    outcome, _ = run(config, audit, conn, [ordinary])

    assert outcome.ignored == 0
    assert outcome.issues_created == 1
    row = card_row(conn)
    assert row is not None and row.state is CardState.LINKED


def test_with_nothing_configured_the_path_is_milestone_003s(board_config, audit, conn):
    """FR-002 and SC-003, and the property everything else is built on top of.

    Asserted against the *same* board and the *same* card as the exclusion tests above, so
    the only difference is the configuration — which is what makes this a comparison
    rather than a restatement.
    """
    parked = make_card("card-1", list_id="list-ice", body="in https://github.com/jantman/demo")

    outcome, boundaries = run(board_config, audit, conn, [parked])

    assert outcome.ignored == 0
    assert outcome.created == 1
    assert outcome.issues_created == 1
    row = card_row(conn)
    assert row is not None and row.state is CardState.LINKED
    assert len(boundaries.card_writer.comments) == 1


# -- the gate's position (contracts/surfaces.md's ordering table) -----------


def test_a_linked_card_in_an_ignored_column_is_still_finished_off(board_config, audit, conn):
    """Row 1. After `linked`: FR-013, and what makes FR-015 free.

    The card is linked but its marker comment was never posted — the loose end R6 leaves
    when the process dies between writing the mapping and commenting. A gate placed above
    the `linked` branch would leave that card unfinished forever.
    """
    config = with_ignore_lists(board_config, "Icebox")
    linked = make_card("card-1", list_id="list-ice")
    boundaries = make_board_boundaries(audit, cards=[linked], board=board())
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)
    row_id = track(
        conn,
        audit,
        linked,
        state=CardState.LINKED,
        repo_key="jantman/demo",
        issue_number=7,
        issue_url="https://github.com/jantman/demo/issues/7",
    )

    verdict = intake.evaluate_card(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        card_row_id=row_id,
        dry_run=False,
        status=status,
        board_card=linked,
    )

    assert verdict.action == "comment_posted"
    assert boundaries.card_writer.comments, "the linked card's loose end was left unfinished"


def test_a_dropped_card_in_an_ignored_column_stays_dropped(board_config, audit, conn):
    """Row 2. After `dropped`: FR-012 — the ignore list is not a route back from a
    terminal state."""
    config = with_ignore_lists(board_config, "Icebox")
    gone = make_card("card-1", list_id="list-ice")
    boundaries = make_board_boundaries(audit, cards=[gone], board=board())
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)
    row_id = track(conn, audit, gone, state=CardState.DROPPED)

    verdict = intake.evaluate_card(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        card_row_id=row_id,
        dry_run=False,
        status=status,
        board_card=gone,
    )

    assert verdict.action == "dropped"


def test_a_card_parked_mid_creation_still_resumes_its_creation(board_config, audit, conn):
    """Row 3, and the spec's mid-creation edge case. After `creating`: the intent is
    recorded and an issue may already exist, so parking must not cancel it — otherwise the
    one-issue invariant becomes "one issue unless you dragged the card at the wrong
    moment"."""
    config = with_ignore_lists(board_config, "Icebox")
    mid = make_card("card-1", list_id="list-ice", body="in https://github.com/jantman/demo")
    boundaries = make_board_boundaries(audit, cards=[mid], board=board())
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)
    row_id = track(conn, audit, mid, state=CardState.CREATING, repo_key="jantman/demo")

    verdict = intake.evaluate_card(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        card_row_id=row_id,
        dry_run=False,
        status=status,
        board_card=mid,
    )

    assert verdict.action != "ignored", "parking must not cancel a recorded creation intent"
    row = card_row(conn)
    assert row is not None and row.state is CardState.LINKED
    # One issue, not zero and not two: the §11 invariant has to survive the card being
    # dragged at the worst possible moment.
    assert len(boundaries.issue_writer.created) == 1

    # And it stays one across the next poll, now that the card is linked and parked.
    again, again_boundaries = run(config, audit, conn, [mid])
    assert again.issues_created == 0
    assert again_boundaries.issue_writer.created == []


def test_an_ignored_card_costs_no_board_request(board_config, audit, conn):
    """Row 5. Before `_restore_from_marker`, which reads the card's comments.

    Deferring that read cannot create a duplicate — nothing downstream of the gate creates
    anything — and it is what keeps a parked icebox free rather than one comments request
    per card per cycle, forever.
    """
    config = with_ignore_lists(board_config, "Icebox")
    parked = make_card("card-1", list_id="list-ice", body="in https://github.com/jantman/demo")
    boundaries = make_board_boundaries(audit, cards=[parked], board=board())
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)
    row_id = track(conn, audit, parked, state=CardState.NEEDS_INFO)
    boundaries.card_reader.comment_calls.clear()

    verdict = intake.evaluate_card(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        card_row_id=row_id,
        dry_run=False,
        status=status,
        board_card=parked,
    )

    assert verdict.action == "ignored"
    assert boundaries.card_reader.comment_calls == []


def test_an_ignored_card_never_reaches_resolution(board_config, audit, conn, monkeypatch):
    """Row 7. Before resolution: FR-005, asserted at the call rather than at its effect,
    because "no issue was created" would also pass if resolution ran and found nothing."""
    config = with_ignore_lists(board_config, "Icebox")
    parked = make_card("card-1", list_id="list-ice", body="in https://github.com/jantman/demo")
    boundaries = make_board_boundaries(audit, cards=[parked], board=board())
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)
    row_id = track(conn, audit, parked, state=CardState.NEEDS_INFO)

    def _explode(*args, **kwargs):
        raise AssertionError("resolution ran for a card in an ignored column")

    monkeypatch.setattr(intake, "resolve_repository", _explode)

    verdict = intake.evaluate_card(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        card_row_id=row_id,
        dry_run=False,
        status=status,
        board_card=parked,
    )

    assert verdict.action == "ignored"
    assert verdict.reason == "parked in 'Icebox'"


# -- User Story 2: un-parking works, and works on its own -------------------


def cycle(config, audit, conn, cards, *, forced=False):
    """A second and subsequent pass over the same board, reusing one boundaries object."""
    return run(config, audit, conn, cards, forced=forced)


def test_a_parked_card_is_not_dropped(board_config, audit, conn):
    """**The regression test this whole design turns on.**

    ``_reconcile_board_contents`` drops every tracked card absent from the poll listing,
    and ``dropped`` is terminal — ``CARD_TRANSITIONS`` gives it no exit and
    ``evaluate_card`` returns early on it forever. The obvious implementation of an ignore
    list, filtering ignored cards out of what the reader returns, would therefore make
    parking an already-tracked card destroy it permanently, and un-parking would do
    nothing, silently, for good.

    If this test fails, look first at whether something "tidied" ``outcome.cards`` in
    ``poll_board`` to exclude ignored cards.
    """
    config = with_ignore_lists(board_config, "Icebox")
    vague = make_card("card-1", list_id="list-inbox", body="no repository named here")

    first, _ = run(config, audit, conn, [vague])
    assert first.held == 1
    assert card_row(conn).state is CardState.NEEDS_INFO

    parked = make_card("card-1", list_id="list-ice", body="no repository named here")
    second, _ = cycle(config, audit, conn, [parked])

    row = card_row(conn)
    assert row.state is CardState.NEEDS_INFO, "parking must not be recorded as leaving the board"
    assert row.current_list_id == "list-ice"
    assert second.dropped == 0


def test_the_park_and_unpark_round_trip_preserves_state_and_reason(board_config, audit, conn):
    """FR-008, FR-009, FR-010. The reason survives, and no second comment is written on
    the way back — the existing rule that a card is commented on only when its reason
    changes has to hold across the round trip, not merely within one pass."""
    config = with_ignore_lists(board_config, "Icebox")
    vague = make_card("card-1", list_id="list-inbox", body="no repository named here")

    run(config, audit, conn, [vague])
    reason = card_row(conn).reason
    assert reason

    parked = make_card("card-1", list_id="list-ice", body="no repository named here")
    _, parked_boundaries = cycle(config, audit, conn, [parked])
    row = card_row(conn)
    assert row.state is CardState.NEEDS_INFO
    assert row.reason == reason
    assert parked_boundaries.card_writer.comments == []

    released = make_card("card-1", list_id="list-inbox", body="no repository named here")
    _, back = cycle(config, audit, conn, [released])
    row = card_row(conn)
    assert row.state is CardState.NEEDS_INFO
    assert row.reason == reason
    assert back.card_writer.comments == [], "the reason did not change, so nothing to say again"


def test_un_parking_files_the_issue_with_no_other_action(board_config, audit, conn):
    """FR-007 and SC-002. No re-tag, no rescan, no restart — one move and one poll."""
    config = with_ignore_lists(board_config, "Icebox")
    parked = make_card("card-1", list_id="list-ice", body="in https://github.com/jantman/demo")

    first, _ = run(config, audit, conn, [parked])
    assert first.issues_created == 0
    assert card_row(conn) is None

    released = make_card("card-1", list_id="list-inbox", body="in https://github.com/jantman/demo")
    second, _ = cycle(config, audit, conn, [released])

    assert second.issues_created == 1
    assert card_row(conn).state is CardState.LINKED


def test_removing_a_column_from_the_configuration_releases_its_cards(board_config, audit, conn):
    """FR-011. The ignored set is resolved from configuration each start, and parked is
    derived rather than stored — so an edit takes effect with nothing else done."""
    parked = make_card("card-1", list_id="list-ice", body="in https://github.com/jantman/demo")

    first, _ = run(with_ignore_lists(board_config, "Icebox"), audit, conn, [parked])
    assert first.issues_created == 0

    second, _ = run(board_config, audit, conn, [parked])

    assert second.issues_created == 1
    assert card_row(conn).state is CardState.LINKED


def test_the_ignore_list_never_revives_a_card_that_left_the_board(board_config, audit, conn):
    """FR-012. ``dropped`` is terminal and this is not a route back from it."""
    config = with_ignore_lists(board_config, "Icebox")
    gone = make_card("card-1", list_id="list-inbox", body="no repository named here")

    run(config, audit, conn, [gone])
    run(config, audit, conn, [])  # untagged: absent from the listing, so dropped
    assert card_row(conn).state is CardState.DROPPED

    run(config, audit, conn, [make_card("card-1", list_id="list-ice")])
    run(config, audit, conn, [make_card("card-1", list_id="list-inbox")])

    assert card_row(conn).state is CardState.DROPPED


def test_park_and_release_are_recorded_once_each(board_config, audit, conn, layout):
    """FR-023 and FR-024. One record per *transition*, never one per poll: a card parked
    for a month must be logged once, where the answer to "why did this stop being
    evaluated?" is findable rather than buried."""
    import json

    config = with_ignore_lists(board_config, "Icebox")
    vague = make_card("card-1", list_id="list-inbox", body="no repository named here")
    run(config, audit, conn, [vague])

    parked = make_card("card-1", list_id="list-ice", body="no repository named here")
    for _ in range(3):
        cycle(config, audit, conn, [parked])

    released = make_card("card-1", list_id="list-inbox", body="no repository named here")
    cycle(config, audit, conn, [released])

    audit.close()
    actions = [
        json.loads(line)
        for path in layout.log_dir.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    parked_records = [r for r in actions if r["action"] == "trello.parked"]
    released_records = [r for r in actions if r["action"] == "trello.released"]

    assert len(parked_records) == 1, "three poll cycles with the card unmoved is one event"
    assert len(released_records) == 1
    assert parked_records[0]["detail"]["list_name"] == "Icebox"
    assert parked_records[0]["detail"]["from_list_id"] == "list-inbox"


def test_the_listing_shows_parked_alongside_needs_info(board_config, audit, conn):
    """A card can be both, which is what writing an ambiguous card and parking it
    produces. The listing must not collapse the two."""
    config = with_ignore_lists(board_config, "Icebox")
    vague = make_card("card-1", list_id="list-inbox", body="no repository named here")
    run(config, audit, conn, [vague])
    cycle(config, audit, conn, [make_card("card-1", list_id="list-ice", body=vague.body)])

    result = operations.cards(listing_context(config, conn, audit))
    row = result.data["cards"][0]

    assert row["state"] == str(CardState.NEEDS_INFO)
    assert row["parked"] is True
    assert row["parked_list"] == "Icebox"
    assert "parked in 'Icebox'" in "\n".join(result.lines)


def test_a_pre_migration_row_is_not_parked(board_config, audit, conn):
    """``current_list_name`` is NULL for a row tracked before milestone 006 and not yet
    re-polled. Not parked is milestone 003's behaviour, and the safe direction."""
    config = with_ignore_lists(board_config, "Icebox")
    old = make_card("card-1", list_id="list-ice", body="no repository named here")
    row_id = track(conn, audit, old, state=CardState.NEEDS_INFO)
    with db.transaction(conn):
        db.update_card_columns(conn, row_id, current_list_id=None, current_list_name=None)

    result = operations.cards(listing_context(config, conn, audit))

    assert result.data["cards"][0]["parked"] is False


# -- the web surface: "parked" is not "held" (T034) -------------------------


def test_the_web_page_says_parked_and_never_held_for_a_parked_card(board_config, audit, conn):
    """``CARD_STATE_HELP`` already renders ``needs_info`` as "held — the card does not say
    which repository", and ``PollOutcome.held`` counts that. A card can be awaiting
    clarification *and* parked at once, so one word cannot carry both — the page would say
    the same thing for two unrelated conditions and the author would read one as the other.
    """
    from robot_army.web import pages

    config = with_ignore_lists(board_config, "Icebox")
    vague = make_card("card-1", list_id="list-inbox", body="no repository named here")
    run(config, audit, conn, [vague])
    cycle(config, audit, conn, [make_card("card-1", list_id="list-ice", body=vague.body)])

    view = pages.cards_view(listing_context(config, conn, audit))

    from robot_army.web import pages as _pages

    # The reason cell is where the contract lives. The exact quote entity is the
    # escaper's business; the words are the contract.
    cell = str(_pages._card_reason_cell(view.data["cards"][0]))
    assert "parked in" in cell and "Icebox" in cell
    assert "held" not in cell, "'held' is needs_info's word — a parked card must not borrow it"
    # Alongside, never instead of: the card is genuinely both, and the cell says both.
    assert "no onboarded repository" in cell

    # And the state's own tooltip still reads "held", because that is what needs_info is.
    # The two words coexist on one row, which is the whole reason they had to be different.
    assert "parked in" in view.body
    assert view.data["parked"] == 1
    # Not counted as outstanding: it is not waiting on the author, it is where they put it.
    assert view.data["needs_info"] == 0
    assert "1 parked" in view.body


def test_the_web_page_still_counts_an_unparked_needs_info_card_as_outstanding(
    board_config, audit, conn
):
    from robot_army.web import pages

    config = with_ignore_lists(board_config, "Icebox")
    vague = make_card("card-1", list_id="list-inbox", body="no repository named here")
    run(config, audit, conn, [vague])

    view = pages.cards_view(listing_context(config, conn, audit))

    assert view.data["needs_info"] == 1
    assert view.data["parked"] == 0
