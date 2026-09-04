"""One claimant wins; the rest are told so and have written nothing (issue #120).

The second half of RA-05. ``transition_work_item`` reads the state and then writes it, and
treats "already there" as a legitimate no-op — correct for reconciliation and for spool
replay, both of which re-derive a state an item already holds. But it meant
``dispatching -> dispatching`` succeeded silently, so two processes racing to launch one
item could both walk past it and start two agent sessions in one worktree on one branch.

Two properties are load-bearing here and neither is a matter of care:

* **The legal sources are derived, not written down.** The obvious implementation hard-codes
  ``('ready','interrupted','awaiting_review')``. That is today's correct answer recorded a
  second time, in the module whose whole purpose is that "is this legal?" has exactly one
  answer in exactly one place — so the derivation is asserted directly, against the
  transition table, rather than by listing the states a second time in a test.
* **``transition_work_item`` is unchanged.** FR-020's guarantee is structural: it holds
  because the function was not edited, not because a test defends it. The test is here
  anyway, because the tempting "tidy-up" is to make one function do both jobs.
"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest
from tests.conftest import seed_item

from robot_army import db
from robot_army.states import (
    WORK_ITEM_TRANSITIONS,
    ClaimLost,
    IllegalTransition,
    WorkItemState,
    claim_work_item,
    claimable_sources,
    transition_work_item,
)

DISPATCHING = WorkItemState.DISPATCHING


def claim(conn, audit, item_id, *, target=DISPATCHING, **kwargs):
    with db.transaction(conn):
        claim_work_item(
            conn, audit, item_id=item_id, target=target, reason="test", **kwargs
        )


def item_in(conn, state: WorkItemState, *, issue_number: int = 1) -> int:
    return seed_item(conn, state=str(state), issue_number=issue_number)


# -- which states may be claimed, and where that list comes from ------------


def test_the_claimable_sources_are_read_off_the_transition_table(conn):
    """Derived, so the state machine keeps one definition. Asserted against the table
    itself rather than against a second hand-written list, which would be the duplication
    the derivation exists to avoid."""
    assert claimable_sources(DISPATCHING) == {
        source for source, target in WORK_ITEM_TRANSITIONS if target is DISPATCHING
    }
    assert claimable_sources(DISPATCHING) == {
        WorkItemState.READY,
        WorkItemState.INTERRUPTED,
        WorkItemState.AWAITING_REVIEW,
    }


def test_dispatching_is_not_claimable_from_dispatching(conn):
    """FR-018, and it needs no special case: no state's self-transition is in the table,
    so an item already starting up is excluded by the derivation itself."""
    assert DISPATCHING not in claimable_sources(DISPATCHING)


@pytest.mark.parametrize(
    "state",
    [WorkItemState.READY, WorkItemState.INTERRUPTED, WorkItemState.AWAITING_REVIEW],
)
def test_a_claim_succeeds_from_every_legal_source(conn, audit, state):
    item_id = item_in(conn, state)

    claim(conn, audit, item_id)

    assert db.get_work_item(conn, item_id).state is DISPATCHING


@pytest.mark.parametrize(
    "state",
    [
        WorkItemState.DISPATCHING,
        WorkItemState.ACTIVE,
        WorkItemState.FAILED,
        WorkItemState.DONE,
        WorkItemState.ABANDONED,
        WorkItemState.DISCOVERED,
    ],
)
def test_a_claim_from_any_other_state_is_lost_and_writes_nothing(conn, audit, state):
    item_id = item_in(conn, state)
    before = dict(conn.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone())

    with pytest.raises(ClaimLost) as caught:
        claim(conn, audit, item_id)

    assert caught.value.found is state
    after = dict(conn.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone())
    assert after == before


def test_a_claim_on_a_missing_item_is_a_lookup_error_not_a_lost_race(conn, audit):
    """The two failures are told apart deliberately. Reporting "claimed by another
    dispatcher" for an item that does not exist would send the reader hunting a second
    process that was never there."""
    with pytest.raises(LookupError):
        claim(conn, audit, 999)


def test_a_target_nothing_can_reach_is_a_programming_error(conn, audit):
    """``discovered`` has no inbound transition. That is a bug in the caller, and must not
    be dressed up as a race."""
    item_id = item_in(conn, WorkItemState.READY)

    with pytest.raises(IllegalTransition):
        claim(conn, audit, item_id, target=WorkItemState.DISCOVERED)


# -- the record and the columns are indistinguishable from a transition -----


def test_a_won_claim_writes_the_same_columns_a_transition_would(conn, audit):
    """Nothing downstream may be able to tell which function moved the item — the reaper
    reads ``dispatching_at``, and a claim that skipped it would leave items it never
    collects."""
    claimed = item_in(conn, WorkItemState.READY, issue_number=1)
    transitioned = item_in(conn, WorkItemState.READY, issue_number=2)

    claim(conn, audit, claimed)
    with db.transaction(conn):
        transition_work_item(
            conn, audit, item_id=transitioned, target=DISPATCHING, reason="test"
        )

    one = db.get_work_item(conn, claimed)
    other = db.get_work_item(conn, transitioned)
    assert one.state is other.state is DISPATCHING
    assert one.dispatching_at is not None
    assert other.dispatching_at is not None
    assert one.updated_at is not None


def test_a_won_claim_writes_a_state_record_naming_what_it_accepted(conn, audit, layout):
    """The claim never reads the previous state — that is what makes it atomic — so it
    reports the set it was willing to accept rather than inventing a value it never saw."""
    item_id = item_in(conn, WorkItemState.AWAITING_REVIEW)

    claim(conn, audit, item_id)

    written = _records(layout, audit, "state.work_item")
    assert len(written) == 1
    assert written[0]["detail"]["to"] == str(DISPATCHING)
    assert written[0]["detail"]["claimed"] is True
    assert set(written[0]["detail"]["from"].split("|")) == {
        str(state) for state in claimable_sources(DISPATCHING)
    }


def test_a_lost_claim_writes_no_state_record(conn, audit, layout):
    item_id = item_in(conn, WorkItemState.ACTIVE)

    with pytest.raises(ClaimLost):
        claim(conn, audit, item_id)

    assert _records(layout, audit, "state.work_item") == []


def test_extra_columns_are_applied_by_a_claim_as_by_a_transition(conn, audit):
    item_id = item_in(conn, WorkItemState.READY)

    claim(conn, audit, item_id, extra_columns={"failure_reason": None, "branch": "b/1"})

    assert db.get_work_item(conn, item_id).branch == "b/1"


# -- FR-020: the no-op re-assertion transition_work_item still owns ---------


def test_transition_work_item_still_treats_a_held_state_as_a_no_op(conn, audit):
    """Reconciliation and spool replay both legitimately re-derive a state an item already
    holds. This change must not turn either into an error — and it does not, because
    ``transition_work_item`` was not edited."""
    item_id = item_in(conn, WorkItemState.ACTIVE)

    with db.transaction(conn):
        source = transition_work_item(
            conn,
            audit,
            item_id=item_id,
            target=WorkItemState.ACTIVE,
            reason="reconciliation re-derived a state the item already holds",
        )

    assert source is WorkItemState.ACTIVE
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


def test_transition_work_item_still_permits_dispatching_to_dispatching(conn, audit):
    """The exact pair the double dispatch rode in on. It stays a no-op *there* — the fix is
    that the launch no longer goes through that function, not that reconcile lost a
    behaviour it depends on."""
    item_id = item_in(conn, WorkItemState.DISPATCHING)

    with db.transaction(conn):
        transition_work_item(
            conn, audit, item_id=item_id, target=DISPATCHING, reason="spool replay"
        )

    assert db.get_work_item(conn, item_id).state is DISPATCHING


# -- the race itself --------------------------------------------------------


def test_exactly_one_of_many_concurrent_claims_wins(conn, audit, layout, tmp_path):
    """SC-005. Threads on separate connections to the same database file, so the contention
    is SQLite's rather than Python's — the claim's guarantee is a property of the statement,
    and a test sharing one connection would prove nothing about it."""
    item_id = item_in(conn, WorkItemState.READY)
    path = layout.db_path
    conn.commit()

    won: list[bool] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def attempt() -> None:
        own = db.connect(path)
        start.wait()
        try:
            with db.transaction(own):
                claim_work_item(
                    own, audit, item_id=item_id, target=DISPATCHING, reason="race"
                )
            outcome = True
        except (ClaimLost, sqlite3.OperationalError):
            outcome = False
        finally:
            own.close()
        with lock:
            won.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert won.count(True) == 1, f"exactly one winner, got {won.count(True)}"
    assert db.get_work_item(db.connect(path), item_id).state is DISPATCHING


def test_the_second_of_two_sequential_claims_is_lost(conn, audit):
    """The deterministic half of the same guarantee, and the one that runs identically on
    every machine."""
    item_id = item_in(conn, WorkItemState.INTERRUPTED)

    claim(conn, audit, item_id)

    with pytest.raises(ClaimLost) as caught:
        claim(conn, audit, item_id)
    assert caught.value.found is DISPATCHING
    assert "claimed by another dispatcher" in str(caught.value)


def _records(layout, audit, action: str) -> list[dict]:
    audit.close()
    out = []
    for path in sorted(layout.log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["action"] == action:
                out.append(record)
    return out
