"""``retry`` re-reads the issue and re-runs the poller's own verdict (issue #119, RA-01).

The defect these cover was the smallest correctness gap in the system and the largest in
consequence. ``poll.evaluate`` is the single enforcement point of a check the code calls
non-disableable — an issue written by anyone other than the configured author is refused —
and ``retry`` returned such an item to the dispatch queue having re-checked only
``dispatch.check_gates``, which takes a ``RepoConfig`` and cannot see an issue at all. The
web interface offered the button with a confirmation promising the opposite.

So the tests here are mostly about refusals, and three properties are asserted that a
"does it work" suite would not reach:

* the verdict is **re-derived from a fresh read**, never inferred from the stored
  ``blocked_reason`` — an item that failed for an unrelated reason is still re-evaluated,
  and an item whose stored reason names the author is allowed once the author matches;
* a read that fails **refuses**, and never falls back to the item's stored copy of the
  issue, which is precisely the thing that cannot be trusted;
* the refused path refreshes the item's content too, so the queue describes the issue as
  it currently is rather than as it was at discovery.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pytest
from tests.conftest import make_boundaries, make_issue, seed_item

from robot_army import db, operations
from robot_army.boundaries import TransportError
from robot_army.states import WorkItemState


@pytest.fixture
def ctx(config, conn, audit, monkeypatch):
    """A context whose reader the test controls and whose trust gate passes.

    ``is_trusted`` reads the real ``~/.claude.json``, so it is stubbed for the same reason
    ``test_web_actions`` stubs it: the gate under test here is the *author*, and a machine
    without a trusted clone would refuse before the read and prove nothing.
    """
    monkeypatch.setattr(
        operations.dispatch,
        "is_trusted",
        lambda path, trust_file=None: (True, "trusted in test"),
    )
    reader = _reader([])
    return operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, reader=reader),
        effect_level=operations.EffectLevel.LIVE,
    )


def _reader(issues: list[Any]):
    from tests.conftest import FakeIssueReader

    return FakeIssueReader(issues)


def failed_item(conn, config, **overrides: Any) -> int:
    """A ``failed`` item on a repository that passes every local precondition."""
    item_id = seed_item(
        conn, state="failed", clone_path=config.repos["demo"].path, **overrides
    )
    with db.transaction(conn):
        db.update_work_item_columns(
            conn,
            item_id,
            failure_reason="eligibility rejected",
            blocked_reason="eligibility rejected",
        )
    return item_id


def records(layout, action: str) -> list[dict]:
    found = []
    for path in sorted(layout.log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("action") == action:
                found.append(json.loads(line))
    return found


# -- check 6: the eligibility verdict ---------------------------------------


def test_an_issue_written_by_someone_else_cannot_be_retried(ctx, conn, config):
    """RA-01 itself. The author check is what stops "anyone may open an issue on a public
    repository" becoming "anyone may run an agent in my checkout", and this was the one
    path around it."""
    item_id = failed_item(conn, config)
    ctx.boundaries.issue_reader.issues = [make_issue(number=42, author="mallory")]

    result = operations.retry(ctx, item_id)

    assert result.code == operations.EXIT_PRECONDITION
    assert "not eligible" in result.lines[0]
    assert "mallory" in result.lines[1] and "jantman" in result.lines[1]
    assert "cannot be disabled" in result.lines[1]
    assert db.get_work_item(conn, item_id).state is WorkItemState.FAILED


def test_the_refusal_quotes_the_poller_s_own_reason_verbatim(ctx, conn, config):
    """The sentence on the queue page and the sentence from a retry are the same sentence,
    because both come from ``Eligibility.reason``. A second copy would be a second thing to
    keep in step."""
    from robot_army import poll

    issue = make_issue(number=42, author="mallory")
    item_id = failed_item(conn, config)
    ctx.boundaries.issue_reader.issues = [issue]

    result = operations.retry(ctx, item_id)

    expected = poll.evaluate(
        issue, config=config, repo_key="demo", onboarded=True
    ).reason
    assert result.lines[1].strip() == expected
    assert result.data["reason"] == expected


def test_a_label_removed_since_discovery_refuses_and_says_so(ctx, conn, config):
    """The refusal reports the condition failing *now*, not the one recorded before. An
    author-only check would have let this through."""
    item_id = failed_item(conn, config)
    ctx.boundaries.issue_reader.issues = [make_issue(number=42, labels=("bug",))]

    result = operations.retry(ctx, item_id)

    assert result.code == operations.EXIT_PRECONDITION
    assert "robot-army" in result.lines[1] and "label" in result.lines[1]
    assert db.get_work_item(conn, item_id).state is WorkItemState.FAILED


def test_an_issue_closed_since_discovery_refuses(ctx, conn, config):
    item_id = failed_item(conn, config)
    ctx.boundaries.issue_reader.issues = [make_issue(number=42, state="closed")]

    result = operations.retry(ctx, item_id)

    assert result.code == operations.EXIT_PRECONDITION
    assert "closed" in result.lines[1]
    assert db.get_work_item(conn, item_id).state is WorkItemState.FAILED


def test_an_item_that_failed_for_an_unrelated_reason_is_still_re_evaluated(ctx, conn, config):
    """FR-007, and the reason the stored ``blocked_reason`` is never consulted: an item
    smuggled to ``ready`` by the old bug and failed later on a worktree error carries a
    reason naming something else entirely, and a string match would wave it through."""
    item_id = seed_item(conn, state="failed", clone_path=config.repos["demo"].path)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn,
            item_id,
            failure_reason="git worktree add failed: fatal: destination exists",
            blocked_reason=None,
        )
    ctx.boundaries.issue_reader.issues = [make_issue(number=42, author="mallory")]

    result = operations.retry(ctx, item_id)

    assert result.code == operations.EXIT_PRECONDITION
    assert "mallory" in result.lines[1]
    assert db.get_work_item(conn, item_id).state is WorkItemState.FAILED


def test_a_refusal_rewrites_both_reason_columns_to_the_current_one(ctx, conn, config):
    """FR-005: the queue must describe why the item is blocked *now*.

    Both columns, and asserting only on ``blocked_reason`` is the trap this test fell into
    once already. ``/queue`` renders ``failure_reason or blocked_reason``, so a refusal that
    wrote only the second would leave the page showing the *old* sentence next to a button
    that had just refused for a different one — the interface disagreeing with what
    happened, which is the whole failure this change exists to remove.
    """
    item_id = failed_item(conn, config)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn,
            item_id,
            failure_reason="issue author 'mallory' is not the configured author",
            blocked_reason="issue author 'mallory' is not the configured author",
        )
    # A *different* condition now fails, so a stale reason is distinguishable from a fresh
    # one. Reusing the author condition here would let both columns pass by accident.
    ctx.boundaries.issue_reader.issues = [make_issue(number=42, labels=())]

    operations.retry(ctx, item_id)

    row = db.get_work_item(conn, item_id)
    assert "label" in row.blocked_reason
    assert "label" in row.failure_reason, "the column /queue actually renders"
    assert "mallory" not in (row.failure_reason or ""), "the stale reason must not survive"


def test_the_queue_page_shows_the_reason_the_retry_just_established(web, conn, config, monkeypatch):
    """The same property, asserted through the renderer rather than the columns, because
    the columns are not what the maintainer reads."""
    monkeypatch.setattr(
        operations.dispatch,
        "is_trusted",
        lambda path, trust_file=None: (True, "trusted in test"),
    )
    item_id = seed_item(conn, state="failed", clone_path=config.repos["demo"].path)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn,
            item_id,
            failure_reason="a stale reason from an earlier failure",
            blocked_reason="a stale reason from an earlier failure",
        )
    web.reader.issues = [make_issue(number=42, author="mallory")]

    web.post_json(f"/item/{item_id}/retry")
    blocked = web.get_json("/queue").json()["blocked"]

    row = next(r for r in blocked if r["id"] == item_id)
    assert "mallory" in row["reason"]
    assert "stale" not in row["reason"]


def test_an_eligible_issue_returns_to_the_queue_with_its_reasons_cleared(ctx, conn, config):
    item_id = failed_item(conn, config)
    ctx.boundaries.issue_reader.issues = [make_issue(number=42)]

    result = operations.retry(ctx, item_id)

    assert result.code == operations.EXIT_OK
    row = db.get_work_item(conn, item_id)
    assert row.state is WorkItemState.READY
    assert row.failure_reason is None
    assert row.blocked_reason is None


def test_an_item_blocked_on_the_author_clears_when_the_configured_author_changes(
    conn, config, audit, monkeypatch
):
    """The check is against the configuration in force at the moment of the retry, which is
    the other direction a stored-reason match would get wrong: it would refuse this for
    ever, because the recorded sentence still names the author."""
    from dataclasses import replace

    monkeypatch.setattr(
        operations.dispatch,
        "is_trusted",
        lambda path, trust_file=None: (True, "trusted in test"),
    )
    item_id = failed_item(conn, config)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item_id, blocked_reason="issue author 'mallory' is not the configured author"
        )
    changed = replace(config, github=replace(config.github, author="mallory"))
    ctx = operations.Context(
        config=changed,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, reader=_reader([make_issue(number=42, author="mallory")])),
        effect_level=operations.EffectLevel.LIVE,
    )

    assert operations.retry(ctx, item_id).code == operations.EXIT_OK
    assert db.get_work_item(conn, item_id).state is WorkItemState.READY


# -- check 5: the read itself -----------------------------------------------


def test_an_unreachable_source_refuses_and_never_falls_back_to_the_stored_copy(
    ctx, conn, config
):
    """FR-006. The stored copy is exactly what cannot be trusted, so a fallback would be
    the original defect with a network hiccup as its trigger — and the failure mode hardest
    to notice, because it looks identical to success."""
    item_id = failed_item(conn, config)
    ctx.boundaries.issue_reader.raise_on_get_issue = TransportError("connection reset")

    result = operations.retry(ctx, item_id)

    assert result.code == operations.EXIT_FAILED
    assert "could not read demo#42" in result.lines[0]
    assert result.data["cause"] == "issue_unreachable"
    assert db.get_work_item(conn, item_id).state is WorkItemState.FAILED


def test_an_absent_or_invisible_issue_refuses(ctx, conn, config):
    """Deleted and "the token cannot see it" are indistinguishable from outside, and the
    refusal says so in those terms rather than picking one."""
    item_id = failed_item(conn, config)
    ctx.boundaries.issue_reader.issues = []

    result = operations.retry(ctx, item_id)

    assert result.code == operations.EXIT_FAILED
    assert "does not exist, or this token cannot see it" in result.lines[0]
    assert result.data["cause"] == "issue_absent"
    assert db.get_work_item(conn, item_id).state is WorkItemState.FAILED


def test_a_repository_precondition_refuses_before_any_read_is_attempted(ctx, conn):
    """Research R4: an item that cannot dispatch for a local reason spends no rate limit
    finding out, and the log can tell "we never asked GitHub" from "we asked"."""
    item_id = seed_item(conn, repo_key="ghost", state="failed")

    result = operations.retry(ctx, item_id)

    assert result.code == operations.EXIT_PRECONDITION
    assert "ghost" in result.lines[0]
    assert ctx.boundaries.issue_reader.get_issue_calls == []


# -- FR-009: the content refresh --------------------------------------------


def test_a_successful_retry_stores_the_issue_as_it_now_stands(ctx, conn, config):
    """RA-04's stale-body problem, on the path this change already re-reads."""
    item_id = failed_item(conn, config)
    ctx.boundaries.issue_reader.issues = [
        make_issue(
            number=42,
            title="Retitled since discovery",
            body="Rewritten since discovery.",
            labels=("robot-army", "enhancement"),
        )
    ]

    operations.retry(ctx, item_id)

    row = db.get_work_item(conn, item_id)
    assert row.title == "Retitled since discovery"
    assert row.body == "Rewritten since discovery."
    assert row.label_list == ["robot-army", "enhancement"]
    assert row.author == "jantman"


def test_a_refused_retry_refreshes_the_content_too(ctx, conn, config):
    """The queue showing a blocked item should describe the issue as it currently is. The
    refresh happens before the verdict is consulted, so both outcomes get it from one
    place rather than two call sites kept in step by hand."""
    item_id = failed_item(conn, config)
    ctx.boundaries.issue_reader.issues = [
        make_issue(number=42, author="mallory", title="Now says something else")
    ]

    assert operations.retry(ctx, item_id).code == operations.EXIT_PRECONDITION

    row = db.get_work_item(conn, item_id)
    assert row.title == "Now says something else"
    assert row.author == "mallory", "the refused author is recorded, not discarded"


def test_a_read_that_failed_refreshes_nothing(ctx, conn, config):
    """There is nothing to refresh *from*. Writing anything here would mean inventing it."""
    item_id = failed_item(conn, config)
    before = db.get_work_item(conn, item_id)
    ctx.boundaries.issue_reader.raise_on_get_issue = TransportError("connection reset")

    operations.retry(ctx, item_id)

    after = db.get_work_item(conn, item_id)
    assert (after.title, after.body, after.labels) == (before.title, before.body, before.labels)


# -- FR-010: the record -----------------------------------------------------


def test_the_verdict_is_recorded_with_both_the_author_and_what_was_refreshed(
    ctx, conn, config, layout
):
    item_id = failed_item(conn, config)
    ctx.boundaries.issue_reader.issues = [make_issue(number=42, author="mallory")]

    operations.retry(ctx, item_id)

    record = records(layout, "retry.evaluate")[-1]
    assert record["outcome"] == "error"
    assert record["entity_id"] == item_id
    assert record["target"] == "demo#42"
    assert record["detail"]["eligible"] is False
    assert record["detail"]["author"] == "mallory"
    assert record["detail"]["refreshed"] == ["title", "body", "labels", "author"]
    assert "mallory" in record["detail"]["reason"]


def test_an_allowed_retry_is_recorded_as_well_as_a_refused_one(ctx, conn, config, layout):
    """Reconstruction (SC-006) has to answer "was it allowed", which needs a record on both
    outcomes rather than only on the interesting one."""
    item_id = failed_item(conn, config)
    ctx.boundaries.issue_reader.issues = [make_issue(number=42)]

    operations.retry(ctx, item_id)

    record = records(layout, "retry.evaluate")[-1]
    assert record["outcome"] == "ok"
    assert record["detail"]["eligible"] is True
    assert record["detail"]["reason"] is None


def test_a_read_failure_records_the_cause_rather_than_a_verdict(ctx, conn, config, layout):
    """"It did not happen" and "I could not ask" are different facts; a record carrying
    ``eligible`` here would assert a verdict that was never reached."""
    item_id = failed_item(conn, config)
    ctx.boundaries.issue_reader.raise_on_get_issue = TransportError("connection reset")

    operations.retry(ctx, item_id)

    record = records(layout, "retry.evaluate")[-1]
    assert record["outcome"] == "error"
    assert record["detail"]["cause"] == "issue_unreachable"
    assert record["detail"]["error"] == "connection reset"
    assert "eligible" not in record["detail"]


def test_a_refusal_before_the_read_writes_retry_blocked(ctx, conn, layout):
    item_id = seed_item(conn, repo_key="ghost", state="failed")

    operations.retry(ctx, item_id)

    record = records(layout, "retry.blocked")[-1]
    assert record["outcome"] == "error"
    assert record["entity_id"] == item_id
    assert "ghost" in record["detail"]["blocked"]
    assert records(layout, "retry.evaluate") == []


def test_the_record_carries_the_dry_run_flag_of_its_item(ctx, conn, config, layout):
    """FR-055's rule everywhere else: a simulated retry must not read in the log as a real
    one. Reads are real at every effect level, so a dry-run item is re-read like any other
    — which is the point, since a dry run that faked its reads would say nothing about
    eligibility."""
    item_id = failed_item(conn, config, dry_run=True)
    ctx.boundaries.issue_reader.issues = [make_issue(number=42, author="mallory")]

    operations.retry(ctx, item_id)

    assert records(layout, "retry.evaluate")[-1]["dry_run"] is True
    assert ctx.boundaries.issue_reader.get_issue_calls == [("demo", 42)]


# -- the first two checks, unchanged ----------------------------------------


def test_an_unknown_item_is_still_a_plain_failure(ctx):
    assert operations.retry(ctx, 999).code == operations.EXIT_FAILED


def test_an_item_that_is_not_failed_is_refused_without_a_read(ctx, conn):
    item_id = seed_item(conn, state="ready")

    result = operations.retry(ctx, item_id)

    assert result.code == operations.EXIT_PRECONDITION
    assert "retry applies to failed items" in result.lines[0]
    assert ctx.boundaries.issue_reader.get_issue_calls == []


# -- FR-011, FR-012: the interface describes the check it performs -----------


def test_both_front_ends_promise_the_re_read_and_the_author_check():
    """The false promise is what turned RA-01 into a confused-deputy attack: the operator
    pressed the button because the confirmation told them pressing it was safe. Once the
    check exists the old sentence — "refused if the condition that blocked it still holds"
    — becomes technically true, which is the trap. It would still fail to say that a retry
    now makes a network call that can itself fail, and would still leave no way to tell a
    fixed build from a broken one by reading the interface.

    Both strings are asserted together because they must agree: a maintainer reading either
    one is entitled to the same answer.
    """
    from robot_army.cli import build_parser
    from robot_army.web.pages import ITEM_ACTIONS

    # What `robot-army retry --help` prints. ``format_help`` wraps at the terminal width,
    # so the text is unwrapped before it is searched — otherwise this would pass or fail
    # depending on ``COLUMNS``.
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    cli_text = " ".join(subparsers.choices["retry"].format_help().split())

    for text in (ITEM_ACTIONS["retry"].description, cli_text):
        lowered = text.lower()
        assert "re-read" in lowered, text
        assert "eligibilit" in lowered, text
        assert "author" in lowered, text

    # And the one-line entry in `robot-army --help` names the re-read too, because that
    # listing is where a maintainer looks before they look anywhere else.
    listing = " ".join(parser.format_help().split())
    assert "retry re-read the issue, re-check eligibility" in listing
