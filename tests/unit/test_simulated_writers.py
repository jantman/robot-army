"""The simulated writers return usable results and log their full arguments (T018).

The rule contracts/boundaries.md sets out, and the reason it is a rule: returning ``None``
or raising would let the simulated path *diverge from the real one* at exactly the point
the dry-run feature exists to rehearse. The caller would take a different branch, and what
a dry run exercised would not be what a live run does.

FR-040 is the other half — a simulated write must be reconstructable from the log with its
full arguments, not merely counted.
"""

from __future__ import annotations

import json

from robot_army import db
from robot_army.boundaries import Issue
from robot_army.boundaries.github import SIMULATED_ISSUE_BASE, SimulatedIssueWriter
from robot_army.boundaries.trello import SimulatedCardWriter
from robot_army.cardstates import CardState


def record_mapping(conn, *, card_id: str, repo_key: str, issue_number: int) -> None:
    """A linked simulated card, written the way intake leaves one.

    The allocation reads the record, so a test about allocation has to write to the
    record — a stub counter would prove nothing about the thing that broke.
    """
    with db.transaction(conn):
        row_id = db.insert_card(
            conn,
            board_id="board-1",
            card_id=card_id,
            card_url=f"https://trello.com/c/{card_id}",
            title="Already filed",
            body="",
            dry_run=True,
        )
        conn.execute(
            "UPDATE cards SET state = ?, repo_key = ?, issue_number = ? WHERE id = ?",
            (str(CardState.LINKED), repo_key, issue_number, row_id),
        )


def records(layout) -> list[dict]:
    audit_files = sorted(layout.log_dir.glob("*.jsonl"))
    return [
        json.loads(line)
        for path in audit_files
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_simulated_create_issue_returns_a_structurally_valid_issue(audit, layout, conn):
    writer = SimulatedIssueWriter(audit, conn)
    issue = writer.create_issue("me/demo", "Fix the thing", "body with a card URL")

    assert isinstance(issue, Issue)
    assert issue.number > SIMULATED_ISSUE_BASE, "the fake number must be unmistakable in a log"
    assert issue.url == f"https://github.com/me/demo/issues/{issue.number}"
    assert issue.title == "Fix the thing"
    assert issue.state == "open"
    # FR-015 is what the simulated path exists to rehearse, so it must rehearse it.
    assert issue.labels == ()


def test_simulated_create_issue_numbers_do_not_repeat(audit, layout, conn):
    """Two cards in one dry run must not produce one issue number, or the mapping the run
    is rehearsing would collide with itself.

    The recorded row between the two calls is the point. Intake commits each card's
    mapping before it files the next one, so this is the real sequence — and it is what
    the assertion now exercises, rather than a counter that would have incremented
    whether or not anything was written.
    """
    writer = SimulatedIssueWriter(audit, conn)
    first = writer.create_issue("me/demo", "one", "")
    record_mapping(conn, card_id="card-1", repo_key="me/demo", issue_number=first.number)
    second = writer.create_issue("me/demo", "two", "")
    assert first.number != second.number


def test_simulated_create_issue_logs_its_full_arguments(audit, layout, conn):
    writer = SimulatedIssueWriter(audit, conn)
    writer.create_issue("me/demo", "Fix the thing", "the whole body")
    audit.close()

    written = [r for r in records(layout) if r["action"] == "github.issue.create"]
    assert len(written) == 1
    record = written[0]
    assert record["simulated"] is True
    assert record["detail"]["title"] == "Fix the thing"
    assert record["detail"]["body"] == "the whole body"
    assert record["detail"]["would_return"]["number"] > SIMULATED_ISSUE_BASE


def test_simulated_card_comment_returns_a_result_and_logs_its_arguments(audit, layout):
    writer = SimulatedCardWriter(audit)
    result = writer.comment("card-1", "the whole comment body")

    assert result.url and result.url.startswith("https://trello.com/c/card-1")
    # None rather than an invented timestamp: no write happened, so the card's real
    # activity stamp did not change, and a fake one would make the caller store a baseline
    # that never matches the board.
    assert result.last_activity is None

    audit.close()
    written = [r for r in records(layout) if r["action"] == "trello.card.comment"]
    assert len(written) == 1
    assert written[0]["simulated"] is True
    assert written[0]["detail"]["body"] == "the whole comment body"
    assert written[0]["detail"]["card_id"] == "card-1"


def test_simulated_card_move_returns_a_result_and_logs_its_arguments(audit, layout):
    writer = SimulatedCardWriter(audit)
    result = writer.move("card-1", "list-doing")
    assert result.url is None
    assert result.last_activity is None

    audit.close()
    written = [r for r in records(layout) if r["action"] == "trello.card.move"]
    assert len(written) == 1
    assert written[0]["detail"]["to_list"] == "list-doing"
    assert written[0]["detail"]["card_id"] == "card-1"
    assert written[0]["entity_id"] == "card-1"


def test_every_simulated_write_is_marked_as_simulated(audit, layout, conn):
    """FR-057. A record that did not say so would be indistinguishable from a real write
    when someone reads the log later, which is the one thing the log must never be."""
    SimulatedIssueWriter(audit, conn).create_issue("me/demo", "t", "b")
    card = SimulatedCardWriter(audit)
    card.comment("card-1", "b")
    card.move("card-1", "list-done")
    audit.close()

    written = [
        r
        for r in records(layout)
        if r["action"] in ("github.issue.create", "trello.card.comment", "trello.card.move")
    ]
    assert len(written) == 3
    assert all(r.get("simulated") is True for r in written)


def test_a_simulated_comment_records_the_whole_body_and_posts_nothing(audit, layout, conn):
    """Issue #38's rehearsal path, and the thing that makes it worth rehearsing.

    ``robot-army run --effect-level local`` is how the comment's wording is checked without
    spending a real issue on it. That only works because the simulated writer logs the body
    it *would* have posted, in full — a record that counted the write, or truncated it,
    would leave the wording unverifiable anywhere but production.
    """
    writer = SimulatedIssueWriter(audit, conn)
    body = "🤖 robot-army dispatched a session for this issue.\n\n- Host: `orion`\n"
    url = writer.comment("me/demo", 42, body)
    audit.close()

    assert url.startswith("https://github.com/me/demo/issues/42#issuecomment-simulated-")

    written = [r for r in records(layout) if r["action"] == "github.comment"]
    assert len(written) == 1
    assert written[0]["simulated"] is True
    assert written[0]["target"] == "me/demo#42"
    assert written[0]["detail"]["body"] == body, "the full body, not a count and not a prefix"


# -- issue #22: the number is unused when it is minted ----------------------


def test_the_first_simulated_issue_in_a_repository_is_the_base_plus_one(audit, layout, conn):
    """A repository with nothing recorded starts where it always did. The floor is what
    makes that true without a branch for the empty case."""
    issue = SimulatedIssueWriter(audit, conn).create_issue("me/demo", "one", "")
    assert issue.number == SIMULATED_ISSUE_BASE + 1


def test_a_fresh_writer_does_not_reissue_a_number_an_earlier_process_used(audit, layout, conn):
    """The defect, stated as a test. A new ``SimulatedIssueWriter`` is what a restarted
    daemon builds; under the counter it minted 900001 again, which ``idx_cards_issue``
    refuses, and the card was then retried with 900002 — the next number already taken.
    Eight recorded rows cost eight failed passes.
    """
    for offset in range(1, 9):
        record_mapping(
            conn,
            card_id=f"card-{offset}",
            repo_key="me/demo",
            issue_number=SIMULATED_ISSUE_BASE + offset,
        )

    restarted = SimulatedIssueWriter(audit, conn)
    issue = restarted.create_issue("me/demo", "the ninth card", "")

    assert issue.number == SIMULATED_ISSUE_BASE + 9, "the very first call must land"
    assert (
        db.find_card_by_issue(
            conn, repo_key="me/demo", issue_number=issue.number, dry_run=True
        )
        is None
    ), "the number handed out is already held by a row"


def test_a_gap_left_by_a_purged_card_is_not_filled(audit, layout, conn):
    """Allocation goes above the highest, not into the first hole. Reusing a number that
    once meant a different card makes the log ambiguous exactly where it should not be."""
    record_mapping(conn, card_id="card-1", repo_key="me/demo", issue_number=900_001)
    record_mapping(conn, card_id="card-4", repo_key="me/demo", issue_number=900_004)

    issue = SimulatedIssueWriter(audit, conn).create_issue("me/demo", "next", "")
    assert issue.number == 900_005


def test_two_repositories_number_independently(audit, layout, conn):
    """The unique index is per repository, so the allocation is too. One busy repository
    must not push another's numbering along."""
    for offset in range(1, 6):
        record_mapping(
            conn, card_id=f"busy-{offset}", repo_key="me/busy", issue_number=900_000 + offset
        )

    writer = SimulatedIssueWriter(audit, conn)
    assert writer.create_issue("me/busy", "one", "").number == 900_006
    assert writer.create_issue("me/quiet", "two", "").number == 900_001


def test_the_number_does_not_depend_on_how_many_comments_came_first(audit, layout, conn):
    """FR-005. The two counters were one, so a card's number depended on how much
    unrelated simulated traffic the process had already produced — two identical runs
    could file the same card under different numbers."""
    chatty = SimulatedIssueWriter(audit, conn)
    for number in range(5):
        chatty.comment("me/demo", 40 + number, "noise")
    with_noise = chatty.create_issue("me/demo", "one", "").number

    conn.execute("DELETE FROM cards")
    quiet = SimulatedIssueWriter(audit, conn)
    without_noise = quiet.create_issue("me/demo", "one", "").number

    assert with_noise == without_noise == SIMULATED_ISSUE_BASE + 1


def test_two_simulated_comments_still_get_different_urls(audit, layout, conn):
    """The comment counter survives, and this is the whole of what it is for: a fragment
    that repeats would make two logged comments look like one."""
    writer = SimulatedIssueWriter(audit, conn)
    first = writer.comment("me/demo", 42, "one")
    second = writer.comment("me/demo", 42, "two")
    assert first != second
