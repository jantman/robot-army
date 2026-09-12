"""The label, re-checked at the queue rather than only at discovery (issue #62).

Changing ``[github] label`` stopped discovery under the old label and left everything
already queued under it free to dispatch. The tests here are the queue's half of the fix:
what the hold says, what it outranks, and that it is lifted by undoing its cause rather
than by any write.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.conftest import seed_item
from tests.unit.test_ordering import ready, snapshot, waiting

from robot_army import db, dispatch, ordering
from robot_army.states import WorkItemState

HoldReason = ordering.HoldReason


def relabel(config, label: str = "scratch"):
    return replace(config, github=replace(config.github, label=label))


def set_labels(conn, item_id: int, raw: str) -> None:
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, labels=raw)


def plan(conn, config, **overrides):
    fields = {"global_cap": 9}
    fields.update(overrides)
    return ordering.plan(conn, config=config, capacity=snapshot(**fields))


# -- the reason itself ------------------------------------------------------


def test_an_item_whose_issue_lacks_the_configured_label_is_held(conn, config):
    ready(conn, 1)

    entry = plan(conn, relabel(config))[0]

    assert entry.hold is HoldReason.NOT_LABELLED
    assert entry.dispatchable is False
    assert "'scratch'" in entry.detail
    assert "has: robot-army" in entry.detail, "what the issue *does* carry, as discovery says"


def test_an_item_carrying_the_configured_label_is_not_held(conn, config):
    ready(conn, 1)

    assert plan(conn, config)[0].hold is None


def test_an_issue_with_no_labels_at_all_says_none(conn, config):
    item_id = ready(conn, 1)[0]
    set_labels(conn, item_id, "[]")

    assert "(has: none)" in plan(conn, config)[0].detail


@pytest.mark.parametrize("raw", ["not json", '{"robot-army": true}', "[1, 2]", "null"])
def test_labels_that_cannot_be_read_hold_the_item_rather_than_release_it(conn, config, raw):
    """Failing open would dispatch exactly the work the check exists to stop."""
    item_id = ready(conn, 1)[0]
    set_labels(conn, item_id, raw)

    entry = plan(conn, config)[0]

    assert entry.hold is HoldReason.NOT_LABELLED
    assert "could not be read" in entry.detail


def test_simulated_items_are_held_by_the_same_rule(conn, config):
    seed_item(conn, issue_number=1, dry_run=True, state=str(WorkItemState.READY))

    assert plan(conn, relabel(config))[0].hold is HoldReason.NOT_LABELLED


# -- precedence -------------------------------------------------------------


def test_it_outranks_a_full_machine(conn, config):
    """The reported failure: a queue that says ``global_cap`` invites raising the cap, and
    raising it dispatched four items nobody meant to run."""
    ready(conn, 1)

    entry = plan(conn, relabel(config), total=9, global_cap=2)

    assert entry[0].hold is HoldReason.NOT_LABELLED


def test_it_outranks_an_unobservable_capacity(conn, config):
    ready(conn, 1)

    entry = plan(conn, relabel(config), observable=False, reason="registry vanished")

    assert entry[0].hold is HoldReason.NOT_LABELLED


def test_it_outranks_the_wait_for_a_merge(conn, config):
    seed_item(conn, issue_number=41, state=str(WorkItemState.AWAITING_REVIEW))
    ready(conn, 1)

    entry = [e for e in plan(conn, relabel(waiting(config))) if e.item.issue_number == 1]

    assert entry[0].hold is HoldReason.NOT_LABELLED


def test_it_outranks_residue_from_a_failed_preparation(conn, config):
    item_id = ready(conn, 1)[0]
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, failure_reason="the hook failed")

    assert plan(conn, relabel(config))[0].hold is HoldReason.NOT_LABELLED


def test_a_pause_outranks_it(conn, config):
    ready(conn, 1)
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="test")

    assert plan(conn, relabel(config))[0].hold is HoldReason.PAUSED


def test_an_item_hold_outranks_it(conn, config):
    item_id = ready(conn, 1)[0]
    with db.transaction(conn):
        db.set_item_hold(conn, item_id, by="cli")

    assert plan(conn, relabel(config))[0].hold is HoldReason.HELD


# -- lifting it --------------------------------------------------------------


def test_changing_the_label_back_lifts_it_and_nothing_was_written(conn, config):
    """A hold, not a state change (FR-005): nothing is abandoned on a configuration edit,
    and undoing the edit is the whole of the release."""
    ready(conn, 2)
    versions = conn.execute("PRAGMA data_version").fetchone()[0]
    before = [dict(r) for r in conn.execute("SELECT * FROM work_items ORDER BY id")]

    assert {e.hold for e in plan(conn, relabel(config))} == {HoldReason.NOT_LABELLED}
    assert {e.hold for e in plan(conn, config)} == {None}

    assert conn.execute("PRAGMA data_version").fetchone()[0] == versions
    assert [dict(r) for r in conn.execute("SELECT * FROM work_items ORDER BY id")] == before


def test_it_holds_one_item_and_leaves_the_next_dispatchable(conn, config):
    """Per-item, like ``repo_cap``: the dispatcher skips it and carries on."""
    _first, second = ready(conn, 2)
    set_labels(conn, second, '["robot-army", "scratch"]')

    entries = plan(conn, relabel(config))

    assert [e.hold for e in entries] == [HoldReason.NOT_LABELLED, None]
    assert HoldReason.NOT_LABELLED not in dispatch._GLOBAL_HOLDS
