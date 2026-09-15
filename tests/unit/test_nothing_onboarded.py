"""An installation with nothing onboarded says so, rather than blaming its input (issue #83).

Losing the state database un-onboards every repository, and that is deliberate: onboarding
is consent and is not re-granted from anything recovered. What was wrong is what came after.
``doctor`` passed, and every held card was told to edit itself — a card that already named
the right repository, with nothing onboarded for it to match. These tests pin the three
halves of the fix: ``doctor`` fails, the card is told the truth, and onboarding alone is
enough to un-hold it.

They also pin the other direction, because the fix is only safe if it is narrow: with even
one repository onboarded, every existing reason and comment is exactly what it was.
"""

from __future__ import annotations

import pytest
from tests.conftest import (
    FakeIssueReader,
    make_board_boundaries,
    make_boundaries,
    make_card,
    onboard_repo,
)

from robot_army import db, intake, operations
from robot_army.cardstates import CardState
from robot_army.intake import NOTHING_ONBOARDED, _needs_info_comment, resolve_repository

REPO = "jantman/demo"


def unonboard_everything(conn):
    """What losing the database does to onboarding — the only part of it these tests need."""
    with db.transaction(conn):
        conn.execute("DELETE FROM repos")


def run_doctor(config, monkeypatch):
    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log, conn: make_boundaries(log, level=level, reader=FakeIssueReader()),
    )
    ctx = operations.build_context(config)
    try:
        return operations.doctor(ctx)
    finally:
        ctx.close()


def onboarded_check(result):
    return next(c for c in result.data["checks"] if c["name"] == "onboarded repositories")


# -- doctor -----------------------------------------------------------------


def test_doctor_fails_when_nothing_is_onboarded(config, monkeypatch):
    result = run_doctor(config, monkeypatch)

    check = onboarded_check(result)
    assert not check["ok"]
    assert "robot-army onboard" in check["detail"]
    assert "nothing can be dispatched" in check["detail"]
    # Naming the file is what tells an operator who did not delete it that it was lost.
    assert "state.db" in check["detail"]
    assert "onboarded repositories" in result.data["failures"]
    assert result.code == operations.EXIT_CHECK_FAILED


def test_doctor_passes_the_check_once_something_is_onboarded(conn, config, repo_clone, monkeypatch):
    onboard_repo(conn, "demo", repo_clone)

    check = onboarded_check(run_doctor(config, monkeypatch))

    assert check["ok"]
    assert check["detail"] == "1 onboarded"


def test_doctor_counts_what_card_resolution_counts(conn, config, monkeypatch):
    """A row with no section and no recorded path cannot take a card, so it must not make
    ``doctor`` pass while every card is held — the two must agree on "inert"."""
    with db.transaction(conn):
        db.upsert_repo(
            conn, repo_key="jantman/ghost", settings_fingerprint=None, trust_verified=True
        )

    assert not onboarded_check(run_doctor(config, monkeypatch))["ok"]


# -- resolution -------------------------------------------------------------


def test_a_card_naming_its_repository_is_told_nothing_is_onboarded(conn, board_config):
    unonboard_everything(conn)

    result = resolve_repository(conn, "Fix it", f"in https://github.com/{REPO}", board_config)

    assert not result.resolvable
    assert result.reason == NOTHING_ONBOARDED
    assert result.source == "onboarding"
    assert result.candidates == ()


def test_a_robot_army_line_gets_the_same_answer_not_a_blame_for_the_line(conn, board_config):
    unonboard_everything(conn)

    result = resolve_repository(conn, "Fix it", f"robot-army: {REPO}", board_config)

    assert result.reason == NOTHING_ONBOARDED
    assert result.source == "onboarding"


def test_with_something_onboarded_the_ordinary_reason_is_unchanged(conn, board_config):
    result = resolve_repository(conn, "Fix it", "no repository named here", board_config)

    assert result.reason == (
        "no onboarded repository could be identified from this card. Name one by its GitHub "
        "URL, its owner/name, or its local path, or by a line reading `robot-army: <repo>` "
        f"and nothing else — onboarded: {REPO}"
    )
    assert result.source == "scan"


# -- the comment ------------------------------------------------------------


def test_the_nothing_onboarded_comment_tells_nobody_to_edit_the_card():
    body = _needs_info_comment(NOTHING_ONBOARDED)

    assert "robot-army onboard" in body
    assert "Nothing on this card needs to change" in body
    assert "Add a line to this card" not in body


@pytest.mark.parametrize("reason", ["this card names 2 onboarded repositories", "anything"])
def test_every_other_comment_is_byte_for_byte_what_it_was(reason):
    assert _needs_info_comment(reason) == (
        "🤖 robot-army could not file an issue for this card yet.\n\n"
        f"{reason}\n\n"
        "Add a line to this card reading `robot-army: <repo>` — a GitHub URL, an "
        "`owner/name`, or the local clone path, alone on its own line — and it will be "
        "picked up automatically on the next pass. No other action is needed, and the rest "
        "of the card can go on mentioning whatever it needs to."
    )


# -- the round trip: held, then onboarded, then filed -----------------------


def cycle(conn, config, audit, boundaries):
    status = intake.check_board(boundaries=boundaries, audit=audit, config=config)
    return intake.run_cycle(
        conn, boundaries=boundaries, audit=audit, config=config, status=status, dry_run=False
    )


def test_onboarding_alone_un_holds_the_card(conn, board_config, audit):
    """The comment promises the card will be picked up once a repository is onboarded, and
    onboarding changes nothing on the board — so the activity gate alone would hold this card
    for good. Nobody touches the card between the second and third pass."""
    unonboard_everything(conn)
    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1", body=f"in https://github.com/{REPO}")]
    )

    cycle(conn, board_config, audit, boundaries)
    row = db.list_cards(conn)[0]
    assert row.state == CardState.NEEDS_INFO
    assert row.reason == NOTHING_ONBOARDED
    assert len(boundaries.card_writer.comments) == 1
    assert "robot-army onboard" in boundaries.card_writer.comments[0][1]

    # While nothing is onboarded the gate holds: no re-evaluation, no second comment.
    assert cycle(conn, board_config, audit, boundaries).held == 0
    assert len(boundaries.card_writer.comments) == 1

    onboard_repo(conn, REPO, board_config.repos[REPO].path)
    outcome = cycle(conn, board_config, audit, boundaries)

    assert outcome.issues_created == 1
    row = db.list_cards(conn)[0]
    assert row.state == CardState.LINKED
    assert row.repo_key == REPO
