"""The pull-request refresh pass (issue #143, contracts C2 and C3).

Two things here are worth more than the happy path, and both are what the constitution asks
of a pass that writes persistent state from external input: what a failed lookup does to what
is already stored, and what a pass killed halfway through leaves behind.
"""

from __future__ import annotations

import json

import pytest
from tests.conftest import FakeIssueReader, make_boundaries, seed_item, seed_session

from robot_army import db, reconcile
from robot_army.boundaries import PullRequest, TransportError
from robot_army.states import WorkItemState

PR_7_OPEN = PullRequest(number=7, url="https://github.com/x/demo/pull/7", state="open")
PR_7_MERGED = PullRequest(number=7, url="https://github.com/x/demo/pull/7", state="merged")
PR_9_OPEN = PullRequest(number=9, url="https://github.com/x/demo/pull/9", state="open")


def item(conn, *, state=WorkItemState.ACTIVE, branch="robot-army/42", stored=None, **kwargs):
    """One work item with a branch, and optionally a pull-request set already stored."""
    item_id = seed_item(conn, state=str(state), **kwargs)
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, branch=branch)
        if stored is not None:
            db.record_pull_requests(
                conn, item_id, found=stored, at="2026-09-05T12:00:00Z"
            )
    return item_id


def refresh(conn, audit, reader):
    return reconcile._refresh_pull_requests(
        conn, boundaries=make_boundaries(audit, reader=reader), audit=audit
    )


def reader_with(*prs, branch="robot-army/42", repo="demo"):
    reader = FakeIssueReader()
    reader.pull_requests[(repo, branch)] = list(prs)
    return reader


def stored_on(conn, item_id):
    return db.get_work_item(conn, item_id).pull_request_list


# -- which items are asked about (C2) ---------------------------------------


@pytest.mark.parametrize(
    "state",
    [WorkItemState.ACTIVE, WorkItemState.AWAITING_REVIEW, WorkItemState.INTERRUPTED],
)
def test_a_live_item_is_refreshed(conn, audit, state):
    item_id = item(conn, state=state)

    assert refresh(conn, audit, reader_with(PR_7_OPEN)) == 1
    assert stored_on(conn, item_id) == [
        {"number": 7, "url": "https://github.com/x/demo/pull/7", "state": "open"}
    ]


def test_a_simulated_item_never_reaches_github(conn, audit):
    """FR-006. A simulated row exists to exercise the local machinery; asking GitHub about
    it would be the dry-run mode causing exactly the outward effect it avoids."""
    item(conn, dry_run=True, issue_number=99)
    reader = reader_with(PR_7_OPEN)

    assert refresh(conn, audit, reader) == 0
    assert reader.pr_calls == []


def test_an_item_with_no_branch_is_not_asked_about(conn, audit):
    """FR-007. Nothing was dispatched, so no pull request can exist — there is no question
    to ask, which is different from asking and getting no answer."""
    item(conn, branch=None)
    reader = reader_with(PR_7_OPEN)

    assert refresh(conn, audit, reader) == 0
    assert reader.pr_calls == []


def test_a_done_item_whose_stored_pull_request_is_open_is_still_refreshed(conn, audit):
    """The second clause, and the reason it exists. ``_resolve_closed_issues`` makes an item
    ``done`` the moment its issue closes, and an issue can be closed by hand while its pull
    request is still open — without this the page would read ``open`` forever."""
    item_id = item(
        conn,
        state=WorkItemState.DONE,
        stored='[{"number":7,"url":"https://github.com/x/demo/pull/7","state":"open"}]',
    )

    assert refresh(conn, audit, reader_with(PR_7_MERGED)) == 1
    assert [pr["state"] for pr in stored_on(conn, item_id)] == ["merged"]


def test_a_done_item_whose_pull_requests_are_all_settled_is_never_asked_again(conn, audit):
    """Which is what makes the rule terminate itself, and why it needs no interval and no
    cap: once nothing can change, nothing is spent."""
    item(
        conn,
        state=WorkItemState.DONE,
        stored='[{"number":7,"url":"https://github.com/x/demo/pull/7","state":"merged"}]',
    )
    reader = reader_with(PR_7_MERGED)

    assert refresh(conn, audit, reader) == 0
    assert reader.pr_calls == []


def test_a_done_item_with_an_empty_set_is_rechecked_while_a_session_still_runs(conn, audit):
    """The race the "all settled" rule alone would lose. Close the issue by hand while the
    session is still working: the pass stores ``[]``, ``_resolve_closed_issues`` makes the
    item ``done`` in the same pass, and the session then opens its pull request. Without
    this clause the page would render a confident "none" for ever — exactly the failure the
    feature exists to prevent."""
    item_id = item(conn, state=WorkItemState.DONE, stored="[]")
    seed_session(conn, item_id, state="running")

    assert refresh(conn, audit, reader_with(PR_7_OPEN)) == 1
    assert [pr["number"] for pr in stored_on(conn, item_id)] == [7]


def test_a_done_item_with_an_empty_set_and_no_live_session_is_settled(conn, audit):
    """And the clause still runs out. Once no session could open a pull request, an empty
    set is as settled as a merged one, and the item stops costing anything."""
    item_id = item(conn, state=WorkItemState.DONE, stored="[]")
    seed_session(conn, item_id, state="exited_clean")
    reader = reader_with(PR_7_OPEN)

    assert refresh(conn, audit, reader) == 0
    assert reader.pr_calls == []
    assert stored_on(conn, item_id) == []


def test_a_terminal_item_that_was_never_checked_is_not_backfilled(conn, audit):
    """Nothing before migration 013 is looked up. A NULL column reads as "not checked",
    which is true; the alternative is one API call per item of history, in one pass."""
    item_id = item(conn, state=WorkItemState.DONE)
    reader = reader_with(PR_7_OPEN)

    assert refresh(conn, audit, reader) == 0
    assert reader.pr_calls == []
    assert db.get_work_item(conn, item_id).pull_requests is None


def test_each_candidate_costs_exactly_one_lookup(conn, audit):
    """There is no per-pass cache, deliberately: ``idx_work_items_identity`` is unique on
    ``(source, source_id, dry_run)`` with ``source_id`` of ``repo#issue``, so two
    non-simulated items cannot share an issue and a cache could never hit. This is what
    pins that — one call per candidate, and the ineligible ones cost nothing."""
    item(conn, issue_number=42, branch="robot-army/42")
    item(conn, issue_number=43, branch="robot-army/43")
    item(conn, issue_number=44, branch=None)

    reader = FakeIssueReader()
    refresh(conn, audit, reader)

    assert [call[1] for call in reader.pr_calls] == [42, 43]


# -- writing what was learned (C3) ------------------------------------------


def test_the_first_check_finding_nothing_records_an_empty_set_not_a_null(conn, audit):
    """"Looked up, and there are none" is a real answer and must be stored as one — it is
    the whole distinction the interface renders."""
    item_id = item(conn)

    assert refresh(conn, audit, FakeIssueReader()) == 1
    row = db.get_work_item(conn, item_id)
    assert row.pull_requests == "[]"
    assert row.pull_requests_at is not None


def test_an_unchanged_set_advances_the_confirmation_time_and_writes_no_record(
    conn, audit, layout
):
    """The omission the plan enumerates under Principle III. With a 60-second cycle and
    sessions that run for hours, recording every unchanged check would fill the log with
    lines saying a pull request did not change — but the confirmation time must still move,
    or the page would report a fresh answer as hours old."""
    item_id = item(
        conn,
        stored='[{"number":7,"url":"https://github.com/x/demo/pull/7","state":"open"}]',
    )

    assert refresh(conn, audit, reader_with(PR_7_OPEN)) == 0

    row = db.get_work_item(conn, item_id)
    assert row.pull_requests_at != "2026-09-05T12:00:00Z", "the confirmation must advance"
    assert records(audit, layout, "work_item.pull_requests") == []


def test_a_change_is_recorded_with_what_it_changed_from(conn, audit, layout):
    item(
        conn,
        stored='[{"number":7,"url":"https://github.com/x/demo/pull/7","state":"open"}]',
    )

    refresh(conn, audit, reader_with(PR_7_MERGED, PR_9_OPEN))

    detail = records(audit, layout, "work_item.pull_requests")[0]["detail"]
    assert detail["from"] == ["7:open"]
    assert detail["to"] == ["7:merged", "9:open"]
    assert detail["first_check"] is False


def test_the_first_check_is_marked_as_such_in_the_record(conn, audit, layout):
    """NULL → ``[]`` is the one transition whose "before" is not a set at all, and saying so
    is what lets the log tell "we first looked and found none" from "the one it had went
    away"."""
    item(conn)

    refresh(conn, audit, FakeIssueReader())

    assert records(audit, layout, "work_item.pull_requests")[0]["detail"]["first_check"]


def test_the_refresh_does_not_move_updated_at(conn, audit):
    """It runs every pass for every live item. Routed through the general updater it would
    push ``updated_at`` forward once a minute for every item in the system, making a column
    that means "when this item changed" mean "when the daemon last looked"."""
    item_id = item(conn)
    before = db.get_work_item(conn, item_id).updated_at

    refresh(conn, audit, reader_with(PR_7_OPEN))

    assert db.get_work_item(conn, item_id).updated_at == before


# -- the failure path (FR-011, FR-020) --------------------------------------


def test_a_failed_lookup_leaves_both_columns_exactly_as_they_were(conn, audit, layout):
    """"I could not ask" is not "there are none". The stored answer stands and its age keeps
    growing, which is the truth and which the interface shows."""
    item_id = item(
        conn,
        stored='[{"number":7,"url":"https://github.com/x/demo/pull/7","state":"open"}]',
    )
    reader = reader_with(PR_9_OPEN)
    reader.raise_on_remote = TransportError("GitHub is unreachable")

    assert refresh(conn, audit, reader) == 0

    row = db.get_work_item(conn, item_id)
    assert row.pull_request_list == [
        {"number": 7, "url": "https://github.com/x/demo/pull/7", "state": "open"}
    ]
    assert row.pull_requests_at == "2026-09-05T12:00:00Z", (
        "a failed attempt must not advance the age of an answer it did not get"
    )
    failures = records(audit, layout, "reconcile.pull_requests_check")
    assert failures and "GitHub is unreachable" in json.dumps(failures[0])


def test_a_failure_on_one_item_does_not_stop_the_pass(conn, audit):
    """A pass that abandoned everything after the first unreachable repository would make
    one broken item hide every other item's pull request."""
    item(conn, repo_key="demo", issue_number=42)
    item(conn, repo_key="other", issue_number=43, branch="robot-army/43")

    class OneBadRepo(FakeIssueReader):
        def pull_requests_for(self, repo_key, issue_number, branch):
            if repo_key == "demo":
                raise TransportError("that one is unreachable")
            return [PR_9_OPEN]

    assert refresh(conn, audit, OneBadRepo()) == 1


def test_an_unexpected_exception_is_not_swallowed(conn, audit):
    """Only ``TransportError`` is something this knows how to be honest about. Anything else
    is a bug and must reach the pass's own handler rather than be caught and counted."""
    item(conn)

    class Broken(FakeIssueReader):
        def pull_requests_for(self, repo_key, issue_number, branch):
            raise RuntimeError("a real bug")

    with pytest.raises(RuntimeError):
        refresh(conn, audit, Broken())


# -- the interruption path (FR-012) -----------------------------------------


def test_a_pass_killed_partway_leaves_earlier_items_written_and_later_ones_untouched(
    conn, audit
):
    """The legitimate resting state: indistinguishable from a pass that has not run yet, and
    repaired by the next one. Nothing may hold a half-written set."""
    first = item(conn, issue_number=42, branch="robot-army/42")
    second = item(conn, issue_number=43, branch="robot-army/43")

    class DiesOnTheSecond(FakeIssueReader):
        def pull_requests_for(self, repo_key, issue_number, branch):
            if branch == "robot-army/43":
                raise KeyboardInterrupt("killed mid-pass")
            return [PR_7_OPEN]

    with pytest.raises(KeyboardInterrupt):
        refresh(conn, audit, DiesOnTheSecond())

    assert stored_on(conn, first) == [
        {"number": 7, "url": "https://github.com/x/demo/pull/7", "state": "open"}
    ]
    assert db.get_work_item(conn, second).pull_requests is None


def test_a_failure_between_the_record_and_the_write_commits_neither_column(
    conn, audit, layout, monkeypatch
):
    """The record and the write share one transaction, and the ordering inside it is chosen
    for the failure it can survive.

    The audit log is an append-only file rather than a table, so a rollback cannot unwrite
    the record — the surviving failure is a record for a change that did not land, which the
    next pass corrects by writing the same change again. The other order would risk a
    committed change with no record at all, which Principle III does not tolerate. This pins
    the half that matters: **no column is written**.
    """
    item_id = item(conn)

    def explode(*args, **kwargs):
        raise RuntimeError("killed between the record and the write")

    monkeypatch.setattr(db, "record_pull_requests", explode)

    with pytest.raises(RuntimeError):
        refresh(conn, audit, reader_with(PR_7_OPEN))

    row = db.get_work_item(conn, item_id)
    assert row.pull_requests is None
    assert row.pull_requests_at is None


# -- the pass's place in reconcile (C2) -------------------------------------


def test_the_pass_summary_counts_changes_rather_than_checks(conn, audit):
    """With a 60-second cycle almost every check finds nothing new, so a check count would
    tick up every pass and say nothing."""
    item(conn)
    reader = reader_with(PR_7_OPEN)

    assert refresh(conn, audit, reader) == 1
    assert refresh(conn, audit, reader) == 0


def records(audit, layout, action):
    audit.close()
    return [
        record
        for path in sorted(layout.log_dir.glob("audit-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if (record := json.loads(line))["action"] == action
    ]


# -- a column we cannot read (models.pull_request_list) ---------------------


def test_a_column_holding_the_wrong_shape_does_not_abort_the_pass(conn, audit):
    """A column we cannot read is a column we do not have — and the guard has to reach the
    *elements*, not stop at "is it a list". Every reader calls ``.get`` on what comes out, so
    ``[144]`` would raise ``AttributeError`` inside the pass and take down the whole
    reconciliation, not one item."""
    item_id = item(conn, state=WorkItemState.DONE, stored="[144]")
    other = item(conn, issue_number=43, branch="robot-army/43")

    assert db.get_work_item(conn, item_id).pull_request_list == []
    assert refresh(conn, audit, reader_with(PR_7_OPEN, branch="robot-army/43")) == 1
    assert [pr["number"] for pr in stored_on(conn, other)] == [7]


def test_unparseable_json_reads_as_no_pull_requests_rather_than_raising(conn, audit):
    item_id = item(conn, stored="not json at all")

    assert db.get_work_item(conn, item_id).pull_request_list == []
