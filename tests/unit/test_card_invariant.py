"""The §11 invariant, from the directions nothing builds (T079, T080).

"One work item ⇒ at most one GitHub issue ⇒ at most one Trello card." Milestone 003 builds
only the card → issue direction, and these tests assert the *other* direction is
structurally impossible anyway — because the guard that makes it so is a pair of unique
indexes rather than a rule the create path follows, and an invariant nothing exercises is
an invariant nobody notices breaking.
"""

from __future__ import annotations

import sqlite3

import pytest
from tests.conftest import make_board_boundaries, make_card

from robot_army import db, intake
from robot_army.cardstates import CardState
from robot_army.effects import EffectLevel

REPO = "jantman/demo"


def cycle(conn, board_config, audit, boundaries, *, dry_run=False):
    status = intake.check_board(boundaries=boundaries, audit=audit, config=board_config)
    return intake.run_cycle(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=board_config,
        status=status,
        dry_run=dry_run,
    )


def linked_row(conn, *, card_id="card-1", issue_number=101, dry_run=False):
    with db.transaction(conn):
        row_id = db.insert_card(
            conn,
            board_id="board-1",
            card_id=card_id,
            card_url=f"https://trello.com/c/{card_id}",
            title="Fix the thing",
            body="",
            dry_run=dry_run,
        )
        conn.execute(
            "UPDATE cards SET state = ?, repo_key = ?, issue_number = ? WHERE id = ?",
            (str(CardState.LINKED), REPO, issue_number, row_id),
        )
    return row_id


# -- FR-036: the reverse direction is refused by the mapping ---------------


def test_an_issue_that_already_has_a_card_is_found_by_the_reverse_lookup(conn):
    """Nothing in this milestone creates cards from issues. This exists so that if
    anything ever tries, it finds the existing mapping rather than making a second one."""
    linked_row(conn)
    found = db.find_card_by_issue(conn, repo_key=REPO, issue_number=101, dry_run=False)
    assert found is not None and found.card_id == "card-1"


def test_a_second_card_for_the_same_issue_is_refused_by_the_schema(conn):
    """The invariant is a unique index, not a convention. A path that skipped its check
    raises ``IntegrityError`` — which is loud — rather than quietly duplicating."""
    linked_row(conn, card_id="card-1", issue_number=101)
    with db.transaction(conn):
        second = db.insert_card(
            conn,
            board_id="board-1",
            card_id="card-2",
            card_url="https://trello.com/c/card-2",
            title="A different card",
            body="",
            dry_run=False,
        )
    with pytest.raises(sqlite3.IntegrityError), db.transaction(conn):
        conn.execute(
            "UPDATE cards SET state = ?, repo_key = ?, issue_number = ? WHERE id = ?",
            (str(CardState.LINKED), REPO, 101, second),
        )


def test_the_card_issue_card_loop_cannot_close(conn, board_config, audit):
    """The loop §11 forbids, walked as far as it goes. A card makes an issue; the issue is
    then offered back as a source of a card, and the mapping refuses it."""
    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1", body=f"https://github.com/{REPO}")]
    )
    cycle(conn, board_config, audit, boundaries)
    row = db.list_cards(conn)[0]

    existing = db.find_card_by_issue(
        conn, repo_key=row.repo_key, issue_number=row.issue_number, dry_run=False
    )
    assert existing is not None, "the issue is not traceable back to its card"
    assert existing.card_id == row.card_id


def test_two_cards_naming_the_same_repository_get_two_different_issues(
    conn, board_config, audit
):
    """The invariant is per *card*, not per repository. Two genuinely different cards are
    two different pieces of work."""
    boundaries = make_board_boundaries(
        audit,
        cards=[
            make_card("card-1", title="One", body=f"https://github.com/{REPO}"),
            make_card("card-2", title="Two", body=f"https://github.com/{REPO}"),
        ],
    )
    cycle(conn, board_config, audit, boundaries)

    numbers = {row.issue_number for row in db.list_cards(conn)}
    assert len(numbers) == 2
    assert len(boundaries.issue_writer.created) == 2


# -- FR-041: a simulated row does not occupy the live row's identity -------


def test_a_simulated_row_and_a_live_row_coexist_for_the_same_card(conn):
    linked_row(conn, dry_run=True)
    linked_row(conn, dry_run=False)
    assert len(db.list_cards(conn, include_simulated=True)) == 2
    assert len(db.list_cards(conn)) == 1


def test_a_simulated_run_does_not_suppress_the_later_real_creation(
    conn, board_config, audit
):
    """The normal workflow: rehearse at ``no-remote``, then run for real. A simulated row
    that occupied the live identity would make the real run a no-op and the rehearsal the
    only thing that ever happened."""
    from robot_army.boundaries.github import SimulatedIssueWriter
    from robot_army.boundaries.trello import SimulatedCardWriter

    card = make_card("card-1", body=f"https://github.com/{REPO}")
    rehearsal = make_board_boundaries(
        audit,
        level=EffectLevel.NO_REMOTE,
        cards=[card],
        writer=SimulatedIssueWriter(audit),
        card_writer=SimulatedCardWriter(audit),
    )
    cycle(conn, board_config, audit, rehearsal, dry_run=True)

    live = make_board_boundaries(audit, cards=[card])
    cycle(conn, board_config, audit, live, dry_run=False)

    assert len(live.issue_writer.created) == 1, "the rehearsal suppressed the real creation"
    live_rows = db.list_cards(conn)
    assert len(live_rows) == 1 and live_rows[0].dry_run is False


def test_a_simulated_row_does_not_block_the_live_issue_mapping(conn):
    """``dry_run`` is part of *both* unique indexes, not only the identity one — so a
    simulated card holding issue #101 cannot stop the live card holding it too."""
    linked_row(conn, card_id="card-1", issue_number=101, dry_run=True)
    linked_row(conn, card_id="card-1", issue_number=101, dry_run=False)
    assert len(db.list_cards(conn, include_simulated=True)) == 2


# -- FR-037: a linked row whose issue vanished creates nothing -------------


def test_a_vanished_issue_raises_an_anomaly_and_creates_nothing(conn, board_config, audit):
    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1", body=f"https://github.com/{REPO}")]
    )
    cycle(conn, board_config, audit, boundaries)
    assert len(boundaries.issue_writer.created) == 1

    boundaries.issue_reader.issues = []
    intake.recovery_sweep(
        conn, boundaries=boundaries, audit=audit, config=board_config, dry_run=False
    )
    cycle(conn, board_config, audit, boundaries)

    assert len(boundaries.issue_writer.created) == 1, "a vanished issue was re-created"
    assert [a.kind for a in db.list_anomalies(conn)] == ["card_issue_missing"]


def test_i_could_not_ask_is_not_it_is_gone(conn, board_config, audit):
    """A transport failure must not raise the anomaly FR-037 reserves for a real absence —
    raising one on every network blip is how a reader learns to ignore them."""
    from robot_army.boundaries import TransportError

    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1", body=f"https://github.com/{REPO}")]
    )
    cycle(conn, board_config, audit, boundaries)

    boundaries.issue_reader.raise_on_remote = TransportError("unreachable")

    def explode(repo_key, number):
        raise TransportError("unreachable")

    boundaries.issue_reader.get_issue = explode
    intake.recovery_sweep(
        conn, boundaries=boundaries, audit=audit, config=board_config, dry_run=False
    )

    assert db.list_anomalies(conn) == []
    assert db.list_cards(conn)[0].state == CardState.LINKED


# -- FR-055: a simulated card is not asked about against the real reader ----


def test_a_simulated_cards_fake_issue_number_raises_no_anomaly(conn, board_config, audit):
    """A simulated card's issue number comes from ``SimulatedIssueWriter`` and is a
    recognisable fake. ``issue_reader`` is real at every effect level (FR-052), so asking
    it about that number is guaranteed to 404 — which would file a ``card_issue_missing``
    anomaly for **every** simulated card, into the same operator-facing list as the real
    ones, and spend a GitHub request to do it.

    The same rule ``reconcile._resolve_closed_issues`` already applies, for the same
    reason: a dry run must not cause the outward effect it exists to avoid.
    """
    from robot_army.boundaries.github import SIMULATED_ISSUE_BASE, SimulatedIssueWriter
    from robot_army.boundaries.trello import SimulatedCardWriter

    boundaries = make_board_boundaries(
        audit,
        level=EffectLevel.NO_REMOTE,
        cards=[make_card("card-1", body=f"https://github.com/{REPO}")],
        writer=SimulatedIssueWriter(audit),
        card_writer=SimulatedCardWriter(audit),
    )
    cycle(conn, board_config, audit, boundaries, dry_run=True)

    row = db.list_cards(conn, include_simulated=True)[0]
    assert row.state == CardState.LINKED
    assert row.issue_number > SIMULATED_ISSUE_BASE, "the fake number is what makes this bite"

    # The real reader knows nothing about issue 900001, and must not be asked.
    asked: list[tuple[str, int]] = []

    def record_and_miss(repo_key, number):
        asked.append((repo_key, number))
        return None

    boundaries.issue_reader.get_issue = record_and_miss
    counts = intake.recovery_sweep(
        conn, boundaries=boundaries, audit=audit, config=board_config, dry_run=True
    )

    assert asked == [], "the real reader was asked about a simulated issue number"
    assert counts["missing_issue"] == 0
    assert db.list_anomalies(conn) == [], "a simulated card produced a false anomaly"


def test_a_live_cards_vanished_issue_still_raises(conn, board_config, audit):
    """Guards the guard: the skip above must be about *simulated* rows, not about
    switching the check off."""
    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1", body=f"https://github.com/{REPO}")]
    )
    cycle(conn, board_config, audit, boundaries)
    boundaries.issue_reader.issues = []

    counts = intake.recovery_sweep(
        conn, boundaries=boundaries, audit=audit, config=board_config, dry_run=False
    )
    assert counts["missing_issue"] == 1
    assert [a.kind for a in db.list_anomalies(conn)] == ["card_issue_missing"]
