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

from robot_army.boundaries import Issue
from robot_army.boundaries.github import SIMULATED_ISSUE_BASE, SimulatedIssueWriter
from robot_army.boundaries.trello import SimulatedCardWriter


def records(layout) -> list[dict]:
    audit_files = sorted(layout.log_dir.glob("*.jsonl"))
    return [
        json.loads(line)
        for path in audit_files
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_simulated_create_issue_returns_a_structurally_valid_issue(audit, layout):
    writer = SimulatedIssueWriter(audit)
    issue = writer.create_issue("me/demo", "Fix the thing", "body with a card URL")

    assert isinstance(issue, Issue)
    assert issue.number > SIMULATED_ISSUE_BASE, "the fake number must be unmistakable in a log"
    assert issue.url == f"https://github.com/me/demo/issues/{issue.number}"
    assert issue.title == "Fix the thing"
    assert issue.state == "open"
    # FR-015 is what the simulated path exists to rehearse, so it must rehearse it.
    assert issue.labels == ()


def test_simulated_create_issue_numbers_do_not_repeat(audit, layout):
    """Two cards in one dry run must not produce one issue number, or the mapping the run
    is rehearsing would collide with itself."""
    writer = SimulatedIssueWriter(audit)
    first = writer.create_issue("me/demo", "one", "")
    second = writer.create_issue("me/demo", "two", "")
    assert first.number != second.number


def test_simulated_create_issue_logs_its_full_arguments(audit, layout):
    writer = SimulatedIssueWriter(audit)
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


def test_every_simulated_write_is_marked_as_simulated(audit, layout):
    """FR-057. A record that did not say so would be indistinguishable from a real write
    when someone reads the log later, which is the one thing the log must never be."""
    SimulatedIssueWriter(audit).create_issue("me/demo", "t", "b")
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


def test_a_simulated_comment_records_the_whole_body_and_posts_nothing(audit, layout):
    """Issue #38's rehearsal path, and the thing that makes it worth rehearsing.

    ``robot-army run --effect-level local`` is how the comment's wording is checked without
    spending a real issue on it. That only works because the simulated writer logs the body
    it *would* have posted, in full — a record that counted the write, or truncated it,
    would leave the wording unverifiable anywhere but production.
    """
    writer = SimulatedIssueWriter(audit)
    body = "🤖 robot-army dispatched a session for this issue.\n\n- Host: `orion`\n"
    url = writer.comment("me/demo", 42, body)
    audit.close()

    assert url.startswith("https://github.com/me/demo/issues/42#issuecomment-simulated-")

    written = [r for r in records(layout) if r["action"] == "github.comment"]
    assert len(written) == 1
    assert written[0]["simulated"] is True
    assert written[0]["target"] == "me/demo#42"
    assert written[0]["detail"]["body"] == body, "the full body, not a count and not a prefix"
