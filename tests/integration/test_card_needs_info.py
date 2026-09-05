"""User story 2: a card that doesn't say enough is held, not guessed at (T059).

The independent test from spec.md, driven end to end: add a card with no repository
reference, confirm no issue exists anywhere and that it is surfaced as awaiting
clarification; edit it to name a repository and confirm an issue appears within one poll
interval with no further human action.

The "five further polls add no second comment" case is the one that would pass a shallow
implementation and fail a real board, because R9's trap only shows up on the *second* pass.
"""

from __future__ import annotations

import dataclasses

from tests.conftest import make_board_boundaries, make_card, onboard_repo

from robot_army import db, intake, operations
from robot_army.cardstates import CardState
from robot_army.effects import EffectLevel

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


def context(board_config, conn, audit, boundaries):
    return operations.Context(
        config=board_config,
        conn=conn,
        audit=audit,
        boundaries=boundaries,
        effect_level=EffectLevel.LIVE,
    )


def edit(boundaries, index=0, **changes):
    boundaries.card_reader.cards[index] = dataclasses.replace(
        boundaries.card_reader.cards[index], **changes
    )


def test_an_unresolvable_card_creates_nothing_and_is_listed_with_its_reason(
    conn, board_config, audit
):
    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1", title="Do the thing", body="soon please")]
    )
    outcome = cycle(conn, board_config, audit, boundaries)

    assert outcome.held == 1
    assert boundaries.issue_writer.created == [], "an issue was filed for an unresolvable card"
    assert db.list_work_items(conn, include_simulated=True) == []

    row = db.list_cards(conn)[0]
    assert row.state == CardState.NEEDS_INFO
    assert row.repo_key is None and row.issue_number is None
    assert "no onboarded repository" in row.reason

    listing = operations.cards(context(board_config, conn, audit, boundaries))
    assert listing.code == 0
    assert "needs_info" in "\n".join(listing.lines)
    assert listing.data["cards"][0]["reason"] == row.reason


def test_the_card_is_commented_on_exactly_once_across_many_polls(conn, board_config, audit):
    """FR-022, and the R9 trap it sits on top of: our own comment changes the card's
    activity stamp, which is the very signal that decides whether to look again."""
    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1", body="nothing useful here")]
    )
    cycle(conn, board_config, audit, boundaries)
    assert len(boundaries.card_writer.comments) == 1
    assert "robot-army: <repo>" in boundaries.card_writer.comments[0][1]

    for _ in range(5):
        cycle(conn, board_config, audit, boundaries)
    assert len(boundaries.card_writer.comments) == 1


def test_an_edit_naming_the_repository_resolves_it_with_no_human_action(
    conn, board_config, audit
):
    """FR-023. The only thing the author does is edit the card."""
    boundaries = make_board_boundaries(audit, cards=[make_card("card-1", body="no repo")])
    cycle(conn, board_config, audit, boundaries)
    assert db.list_cards(conn)[0].state == CardState.NEEDS_INFO

    edit(
        boundaries,
        body=f"it is https://github.com/{REPO}",
        last_activity="2026-08-25T12:00:00Z",
    )
    outcome = cycle(conn, board_config, audit, boundaries)

    assert outcome.issues_created == 1
    row = db.list_cards(conn)[0]
    assert row.state == CardState.LINKED
    assert (row.repo_key, row.issue_number) == (REPO, 101)
    # And the card now carries the marker comment as well as the held-for-info one.
    assert len(boundaries.card_writer.comments) == 2
    assert intake.MARKER_PREFIX in boundaries.card_writer.comments[1][1]


def test_a_card_naming_two_configured_repositories_is_held_not_resolved_to_either(
    conn, board_config, audit, tmp_path
):
    from tests.conftest import make_repo

    other = make_repo(tmp_path / "other")
    board_config.repos["jantman/other"] = dataclasses.replace(
        board_config.repos[REPO], key="jantman/other", path=other
    )
    # Onboarded as well as configured: since milestone 005 a section alone does not make a
    # repository a candidate, so without this the card would name *one* repository and
    # resolve rather than being held — the opposite of what this test is about.
    onboard_repo(conn, "jantman/other", other)
    boundaries = make_board_boundaries(
        audit,
        cards=[
            make_card(
                "card-1",
                body=f"either https://github.com/{REPO} or https://github.com/jantman/other",
            )
        ],
    )
    outcome = cycle(conn, board_config, audit, boundaries)

    assert outcome.held == 1
    assert boundaries.issue_writer.created == []
    row = db.list_cards(conn)[0]
    assert row.state == CardState.NEEDS_INFO
    assert row.repo_key is None, "an ambiguous card was resolved to one of its candidates"
    assert "exactly one" in row.reason


def test_a_forced_rescan_re_evaluates_a_held_card_that_has_not_changed(
    conn, board_config, audit
):
    """What ``robot-army rescan`` is for: the author fixed something the card does not
    mention — configured the repository, say — and wants a look now rather than at the
    next interval."""
    unknown = make_card("card-1", body="https://github.com/jantman/newrepo")
    boundaries = make_board_boundaries(audit, cards=[unknown])
    cycle(conn, board_config, audit, boundaries)
    assert db.list_cards(conn)[0].state == CardState.NEEDS_INFO

    # The repository is configured now, but the *card* has not been touched.
    board_config.repos["jantman/newrepo"] = dataclasses.replace(
        board_config.repos[REPO], key="jantman/newrepo"
    )
    onboard_repo(conn, "jantman/newrepo", board_config.repos[REPO].path)
    assert cycle(conn, board_config, audit, boundaries).issues_created == 0

    outcome = cycle(conn, board_config, audit, boundaries, forced=True)
    assert outcome.issues_created == 1
    assert db.list_cards(conn)[0].repo_key == "jantman/newrepo"


def test_a_card_that_loses_its_tag_before_being_linked_is_dropped(conn, board_config, audit):
    """FR-025. ``poll`` returns only tagged cards, so the signal is simply absence."""
    boundaries = make_board_boundaries(audit, cards=[make_card("card-1", body="no repo")])
    cycle(conn, board_config, audit, boundaries)
    assert db.list_cards(conn)[0].state == CardState.NEEDS_INFO

    edit(boundaries, label_ids=())
    cycle(conn, board_config, audit, boundaries)

    row = db.list_cards(conn)[0]
    assert row.state == CardState.DROPPED
    assert row.archived_at is not None


def test_a_linked_card_that_is_archived_keeps_its_mapping(conn, board_config, audit):
    """The exception FR-025 carves out, and the reason for it: dropping a linked card's
    mapping would let a re-tagged card create a second issue."""
    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1", body=f"https://github.com/{REPO}")]
    )
    cycle(conn, board_config, audit, boundaries)
    linked = db.list_cards(conn)[0]
    assert linked.state == CardState.LINKED

    edit(boundaries, closed=True)
    cycle(conn, board_config, audit, boundaries)

    row = db.list_cards(conn)[0]
    assert row.state == CardState.LINKED
    assert row.archived_at is not None
    assert (row.repo_key, row.issue_number) == (linked.repo_key, linked.issue_number)

    # And re-tagging it produces no second issue.
    edit(boundaries, closed=False)
    cycle(conn, board_config, audit, boundaries)
    assert len(boundaries.issue_writer.created) == 1


def test_the_cards_verb_refuses_when_no_board_is_configured(conn, config, audit):
    """Exit 3 with an explanation, not an empty table: an empty table would misrepresent
    "not configured" as "nothing to do"."""
    result = operations.cards(context(config, conn, audit, make_board_boundaries(audit)))
    assert result.code == operations.EXIT_PRECONDITION
    assert "no [trello] section" in result.lines[0]
    assert result.data["configured"] is False


def test_rescan_refuses_an_untracked_card_and_a_linked_one(conn, board_config, audit, layout):
    boundaries = make_board_boundaries(
        audit, cards=[make_card("card-1", body=f"https://github.com/{REPO}")]
    )
    cycle(conn, board_config, audit, boundaries)
    ctx = context(board_config, conn, audit, boundaries)

    assert operations.rescan(ctx, "card-does-not-exist").code == operations.EXIT_FAILED
    # Rescanning a linked card is meaningless, and silently doing nothing would be worse
    # than refusing: the author would believe they had retried something.
    assert operations.rescan(ctx, "card-1").code == operations.EXIT_USAGE


def test_rescan_refuses_when_no_daemon_is_running_to_service_it(conn, board_config, audit):
    boundaries = make_board_boundaries(audit, cards=[make_card("card-1", body="no repo")])
    cycle(conn, board_config, audit, boundaries)
    ctx = context(board_config, conn, audit, boundaries)

    result = operations.rescan(ctx, "card-1")
    assert result.code == operations.EXIT_PRECONDITION
    assert "no daemon is running" in result.lines[0]


def test_rescan_writes_a_marker_a_running_daemon_drains(
    conn, board_config, audit, layout, running_daemon
):
    """The mechanism 002 built for ``poll`` and ``reconcile``, taking a third job name
    without modification — which is why this is a small change and not a new pathway."""
    from robot_army import control

    boundaries = make_board_boundaries(audit, cards=[make_card("card-1", body="no repo")])
    cycle(conn, board_config, audit, boundaries)
    ctx = context(board_config, conn, audit, boundaries)

    result = operations.rescan(ctx, "card-1")
    assert result.code == 0
    assert control.pending(layout) == ["rescan"]

    assert control.take_requests(layout, audit) == ["rescan"]
    assert control.pending(layout) == []


# -- held because of the `robot-army:` line (milestone 116) -----------------


def _two_onboarded(conn, board_config, tmp_path):
    from tests.conftest import make_repo

    other = make_repo(tmp_path / "other")
    board_config.repos["jantman/other"] = dataclasses.replace(
        board_config.repos[REPO], key="jantman/other", path=other
    )
    onboard_repo(conn, "jantman/other", other)


def test_a_declaration_naming_nothing_onboarded_is_held_and_quoted_back(
    conn, board_config, audit, tmp_path
):
    """SC-003, driven through the machinery that actually writes on the card. The author
    has already done what the generic message asks; the comment has to name *their* text
    or it sends them looking for a problem they have fixed."""
    _two_onboarded(conn, board_config, tmp_path)
    boundaries = make_board_boundaries(
        audit,
        cards=[
            make_card(
                "card-1",
                body=f"either {REPO} or jantman/other\nrobot-army: jantmna/demo",
            )
        ],
    )
    outcome = cycle(conn, board_config, audit, boundaries)

    assert outcome.held == 1
    assert boundaries.issue_writer.created == []
    row = db.list_cards(conn)[0]
    assert row.state == CardState.NEEDS_INFO
    assert "jantmna/demo" in row.reason
    assert len(boundaries.card_writer.comments) == 1
    assert "jantmna/demo" in boundaries.card_writer.comments[0][1]


def test_correcting_the_declaration_resolves_the_card_with_no_other_action(
    conn, board_config, audit, tmp_path
):
    """FR-023 applied to the new reasons: the existing re-evaluate-on-edit machinery is
    reused unchanged, so fixing a typo'd line is the whole of the author's work."""
    _two_onboarded(conn, board_config, tmp_path)
    boundaries = make_board_boundaries(
        audit,
        cards=[
            make_card(
                "card-1",
                body=f"either {REPO} or jantman/other\nrobot-army: jantmna/demo",
            )
        ],
    )
    cycle(conn, board_config, audit, boundaries)
    assert db.list_cards(conn)[0].state == CardState.NEEDS_INFO

    edit(
        boundaries,
        body=f"either {REPO} or jantman/other\nrobot-army: {REPO}",
        last_activity="2026-08-25T12:00:00Z",
    )
    outcome = cycle(conn, board_config, audit, boundaries)

    assert outcome.issues_created == 1
    row = db.list_cards(conn)[0]
    assert row.state == CardState.LINKED
    assert row.repo_key == REPO


def test_two_declarations_that_disagree_earn_their_own_reason(
    conn, board_config, audit, tmp_path
):
    """A distinct reason means a distinct comment: a card that moves from one failure to
    another is told about the move rather than left with the stale explanation."""
    _two_onboarded(conn, board_config, tmp_path)
    boundaries = make_board_boundaries(
        audit,
        cards=[
            make_card("card-1", body=f"robot-army: {REPO}\nrobot-army: jantman/other")
        ],
    )
    outcome = cycle(conn, board_config, audit, boundaries)

    assert outcome.held == 1
    row = db.list_cards(conn)[0]
    assert "more than one" in row.reason
    assert REPO in row.reason and "jantman/other" in row.reason
