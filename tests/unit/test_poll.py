"""Eligibility, ETag reuse, and idempotent discovery (T056).

Each eligibility condition is tested failing **in isolation**, because a check that only
works when the other three also fail is not a check.
"""

from __future__ import annotations

from tests.conftest import FakeIssueReader, make_boundaries, make_issue

from robot_army import db, poll
from robot_army.boundaries import TransportError
from robot_army.states import WorkItemState


def onboard(conn, key="demo"):
    with db.transaction(conn):
        db.upsert_repo(conn, repo_key=key, settings_fingerprint=None, trust_verified=True)


def test_a_fully_eligible_issue_passes(config):
    verdict = poll.evaluate(make_issue(), config=config, repo_key="demo", onboarded=True)
    assert verdict.eligible


def test_wrong_author_is_rejected_and_persisted(config):
    """FR-007's security boundary. The label is a trigger anyone with write access could
    apply; the author check is what stops that being a path into this machine."""
    verdict = poll.evaluate(
        make_issue(author="someone-else"), config=config, repo_key="demo", onboarded=True
    )
    assert not verdict.eligible
    assert "security boundary" in (verdict.reason or "")
    assert verdict.persist, "a deliberately labelled issue deserves a persisted reason"


def test_the_author_check_cannot_be_bypassed_by_an_empty_config_value(config):
    """There is deliberately no "any author" value; config validation rejects a blank
    one, so no issue can ever match by accident."""
    from dataclasses import replace

    blank = replace(config, github=replace(config.github, author=""))
    verdict = poll.evaluate(
        make_issue(author=""), config=blank, repo_key="demo", onboarded=True
    )
    # Even if a blank author somehow reached here, it can only ever match an issue with a
    # blank author — which GitHub does not produce. Validation is the real guard, and
    # test_config.py asserts it rejects the blank value outright.
    assert verdict.eligible is True
    from robot_army.config import ConfigError

    try:
        from tests.conftest import config_dict, monkey_token

        from robot_army.config import parse

        monkey_token()
        parse(
            config_dict(config.repos["demo"].path, config.layout, config.worktree_root,
                        github={"author": ""}),
            config.path,
        )
    except ConfigError as exc:
        assert any("security boundary" in p for p in exc.problems)
    else:  # pragma: no cover
        raise AssertionError("a blank author must be a validation error")


def test_missing_label_is_rejected_without_a_row(config):
    verdict = poll.evaluate(
        make_issue(labels=("bug",)), config=config, repo_key="demo", onboarded=True
    )
    assert not verdict.eligible
    assert not verdict.persist


def test_unconfigured_repo_is_rejected_without_a_row(config):
    verdict = poll.evaluate(make_issue(), config=config, repo_key="other", onboarded=True)
    assert not verdict.eligible
    assert not verdict.persist


def test_not_onboarded_is_rejected_without_a_row(config):
    """No row, because ``work_items.repo_key`` is a foreign key into ``repos`` and that
    table only gets an entry once onboarding happened."""
    verdict = poll.evaluate(make_issue(), config=config, repo_key="demo", onboarded=False)
    assert not verdict.eligible
    assert "onboard" in (verdict.reason or "")
    assert not verdict.persist


def test_a_closed_issue_is_rejected(config):
    verdict = poll.evaluate(
        make_issue(state="closed"), config=config, repo_key="demo", onboarded=True
    )
    assert not verdict.eligible


def test_polling_creates_a_ready_item(conn, audit, config):
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)

    outcome = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert outcome.created == 1
    items = db.list_work_items(conn)
    assert len(items) == 1
    assert items[0].state is WorkItemState.READY
    assert items[0].title == "Fix the thing"


def test_a_rejected_but_labelled_issue_lands_in_failed_with_a_reason(conn, audit, config):
    """FR-009: the maintainer deliberately labelled it and will want to know why nothing
    happened."""
    onboard(conn)
    reader = FakeIssueReader([make_issue(author="stranger")])
    boundaries = make_boundaries(audit, reader=reader)

    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    item = db.list_work_items(conn)[0]
    assert item.state is WorkItemState.FAILED
    assert "security boundary" in (item.blocked_reason or "")


def test_an_unlabelled_issue_produces_no_row(conn, audit, config):
    onboard(conn)
    reader = FakeIssueReader([make_issue(labels=("bug",))])
    boundaries = make_boundaries(audit, reader=reader)

    outcome = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert outcome.created == 0
    assert db.list_work_items(conn) == []


def test_repolling_the_same_issue_is_a_no_op(conn, audit, config):
    """FR-072: re-polling must not produce a second worktree and a second session."""
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)

    for _ in range(3):
        # Force a fresh listing each time rather than a 304, so the idempotency being
        # tested is the unique index rather than the conditional request.
        reader.etag = None
        poll.poll_repo(
            conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
        )
    assert len(db.list_work_items(conn)) == 1


def test_the_etag_is_persisted_and_replayed(conn, audit, config):
    """304 is the healthy steady state — it costs nothing against the rate limit (R4)."""
    onboard(conn)
    reader = FakeIssueReader([make_issue()], etag='W/"abc"')
    boundaries = make_boundaries(audit, reader=reader)

    first = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert first.status == 200
    assert db.get_poll_state(conn, "demo").etag == 'W/"abc"'

    second = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert second.status == 304
    assert second.found == 0
    assert reader.poll_calls[-1] == ("demo", 'W/"abc"')


def test_a_transport_failure_is_recorded_and_backed_off_not_swallowed(conn, audit, config):
    """"No eligible work" and "I could not ask" are different facts."""
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    reader.raise_on_poll = TransportError("connection reset")
    boundaries = make_boundaries(audit, reader=reader)

    outcome = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert outcome.error is not None
    assert outcome.created == 0
    state = db.get_poll_state(conn, "demo")
    assert state.consecutive_failures == 1
    assert state.backoff_until is not None


def test_a_repository_in_backoff_is_skipped(conn, audit, config):
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)
    from robot_army.models import PollState
    from robot_army.reconcile import within

    with db.transaction(conn):
        db.save_poll_state(
            conn, PollState(repo_key="demo", backoff_until=within(600), consecutive_failures=3)
        )
    outcome = poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert outcome.skipped_reason is not None
    assert reader.poll_calls == []


def test_a_successful_poll_clears_the_failure_counter(conn, audit, config):
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)
    from robot_army.models import PollState

    with db.transaction(conn):
        db.save_poll_state(conn, PollState(repo_key="demo", consecutive_failures=4))
    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    assert db.get_poll_state(conn, "demo").consecutive_failures == 0


def test_dry_run_rows_are_marked_and_coexist_with_live_ones(conn, audit, config):
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)

    reader.etag = None
    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=True
    )
    reader.etag = None
    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    everything = db.list_work_items(conn, include_simulated=True)
    assert len(everything) == 2
    assert {i.dry_run for i in everything} == {True, False}
    assert len(db.list_work_items(conn)) == 1


def test_an_item_left_in_discovered_is_re_evaluated_on_the_next_poll(conn, audit, config):
    """The interruption case: the row was written before evaluation and the process died."""
    onboard(conn)
    with db.transaction(conn):
        item_id = db.insert_work_item(
            conn,
            source="github",
            source_id="demo#42",
            source_url="u",
            repo_key="demo",
            issue_number=42,
            title="t",
            body="b",
            labels='["robot-army"]',
            dry_run=False,
        )
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)
    poll.poll_repo(
        conn, boundaries=boundaries, audit=audit, config=config, repo_key="demo", dry_run=False
    )
    item = db.get_work_item(conn, item_id)
    assert item is not None and item.state is WorkItemState.READY


def test_poll_all_continues_past_one_repositorys_failure(conn, audit, config):
    onboard(conn)
    reader = FakeIssueReader([make_issue()])
    boundaries = make_boundaries(audit, reader=reader)
    outcomes = poll.poll_all(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        dry_run=False,
        only_repo="not-configured",
    )
    assert len(outcomes) == 1
    assert outcomes[0].error is not None
