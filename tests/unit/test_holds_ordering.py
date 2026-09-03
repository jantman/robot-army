"""Where a hold sits in the queue's precedence, and what it does not disturb (issue #117).

The precedence is the whole of the design that is invisible in the code and immediately
visible to the author. Every reason below ``held`` names a fix that cannot work while the
author is holding the item — free a session slot, merge a pull request, re-onboard a clone,
move a card — so the tests here are written as *outranking* assertions rather than as one
list, because the list already exists in ``test_ordering`` and a second copy would drift.
"""

from __future__ import annotations

import pytest
from tests.conftest import seed_item
from tests.unit.test_ordering import govern, place, ready, snapshot, waiting

from robot_army import db, dispatch, ordering
from robot_army.states import WorkItemState


def hold_item(conn, item_id: int, *, by: str = "cli") -> None:
    with db.transaction(conn):
        db.set_item_hold(conn, item_id, by=by)


def hold_repo(conn, repo_key: str = "demo", *, by: str = "cli") -> None:
    with db.transaction(conn):
        db.set_repo_hold(conn, repo_key, by=by)


def plan(conn, config, **overrides):
    fields = {"global_cap": 9}
    fields.update(overrides)
    return ordering.plan(conn, config=config, capacity=snapshot(**fields))


# -- the reason itself ------------------------------------------------------


def test_a_held_item_reports_held_with_when_and_by_what(conn, config):
    item_id = ready(conn, 1)[0]
    hold_item(conn, item_id, by="web")

    entry = plan(conn, config)[0]

    assert entry.hold is ordering.HoldReason.HELD
    assert entry.dispatchable is False
    assert "held" in entry.detail
    assert "web" in entry.detail, "the surface that placed it, as paused_by already records"


def test_an_unheld_item_beside_a_held_one_is_dispatchable(conn, config):
    first, _second = ready(conn, 2)
    hold_item(conn, first)

    entries = plan(conn, config)

    assert entries[0].hold is ordering.HoldReason.HELD
    assert entries[1].hold is None


# -- precedence: what held outranks ----------------------------------------


def test_a_pause_outranks_a_hold(conn, config):
    """The only reason that does. A paused system dispatches nothing at all, so naming
    one item's hold would understate what is stopping the queue."""
    item_id = ready(conn, 1)[0]
    hold_item(conn, item_id)
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="test")

    assert plan(conn, config)[0].hold is ordering.HoldReason.PAUSED


def test_a_hold_outranks_an_unobservable_capacity(conn, config):
    """That reason's justification is that the cap *numbers* are untrustworthy. A hold is
    not a number and is not derived from the observation — a held item is held whether or
    not ``/proc`` could be read."""
    item_id = ready(conn, 1)[0]
    hold_item(conn, item_id)

    entry = plan(conn, config, observable=False, reason="could not read /proc")[0]

    assert entry.hold is ordering.HoldReason.HELD


def test_a_hold_outranks_the_global_cap(conn, config):
    item_id = ready(conn, 1)[0]
    hold_item(conn, item_id)

    assert plan(conn, config, total=9, global_cap=2)[0].hold is ordering.HoldReason.HELD


def test_a_hold_outranks_the_repository_cap(conn, config):
    item_id = ready(conn, 1)[0]
    hold_item(conn, item_id)

    entry = plan(conn, config, per_repo={"demo": 5}, global_cap=99)[0]

    assert entry.hold is ordering.HoldReason.HELD


def test_a_hold_outranks_the_wait_for_merge_gate(conn, config):
    """Sending the author off to merge a pull request when they are the one holding the
    item is the archetype of a fix that cannot work."""
    seed_item(conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW))
    item_id = ready(conn, 1, repo_key="demo")[0]
    hold_item(conn, item_id)

    assert plan(conn, waiting(config, globally=True))[0].hold is ordering.HoldReason.HELD


def test_a_hold_outranks_an_unresolvable_repository(conn, config):
    item_id = seed_item(
        conn, repo_key="ghost", issue_number=1, state=str(WorkItemState.READY)
    )
    hold_item(conn, item_id)

    assert plan(conn, config)[0].hold is ordering.HoldReason.HELD


def test_a_hold_outranks_a_card_parked_off_the_dispatch_column(conn, config):
    item_id = ready(conn, 1)[0]
    govern(conn)
    place(conn, item_id, "Backlog")
    hold_item(conn, item_id)

    assert plan(conn, config)[0].hold is ordering.HoldReason.HELD


def test_a_hold_outranks_stale_preparation_residue(conn, config):
    item_id = ready(conn, 1)[0]
    conn.execute(
        "UPDATE work_items SET blocked_reason = ? WHERE id = ?",
        ("workspace trust check failed", item_id),
    )
    hold_item(conn, item_id)

    assert plan(conn, config)[0].hold is ordering.HoldReason.HELD


def test_releasing_a_hold_reveals_the_reason_underneath(conn, config):
    """The complement of the tests above, and the one that proves ``held`` *masks* rather
    than *replaces*: the condition below it is untouched and reappears intact."""
    item_id = ready(conn, 1)[0]
    conn.execute(
        "UPDATE work_items SET blocked_reason = ? WHERE id = ?", ("trust failed", item_id)
    )
    hold_item(conn, item_id)
    assert plan(conn, config)[0].hold is ordering.HoldReason.HELD

    with db.transaction(conn):
        db.clear_item_hold(conn, item_id)

    assert plan(conn, config)[0].hold is ordering.HoldReason.PREPARATION_FAILED


# -- purity and position ----------------------------------------------------


def test_planning_with_holds_in_force_still_writes_nothing(conn, config):
    """``plan`` runs on every dispatch tick *and* every web page render. A write here
    would be a write on every page load."""
    items = ready(conn, 3)
    hold_item(conn, items[0])
    before = conn.execute("SELECT * FROM work_items ORDER BY id").fetchall()
    holds_before = db.list_item_holds(conn)

    plan(conn, config)

    after = conn.execute("SELECT * FROM work_items ORDER BY id").fetchall()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
    assert db.list_item_holds(conn) == holds_before


def test_a_held_item_keeps_its_position_and_renumbers_nothing(conn, config):
    """FR-014. A surface that silently omitted held work is the failure the queue view
    exists to prevent, and moving it would be reordering — which this feature excludes."""
    items = ready(conn, 3)
    unheld = plan(conn, config)
    hold_item(conn, items[1])
    held = plan(conn, config)

    assert [e.item.id for e in held] == [e.item.id for e in unheld]
    assert [e.position for e in held] == [1, 2, 3]
    assert held[1].hold is ordering.HoldReason.HELD


def test_holding_then_releasing_returns_an_item_to_the_identical_position(conn, config):
    """FR-013. Releasing restores; it does not reorder. True by construction because
    ``plan`` sorts first and assigns hold reasons second — but the construction is exactly
    what a future refactor could lose."""
    items = ready(conn, 4)
    before = [(e.item.id, e.position) for e in plan(conn, config)]

    hold_item(conn, items[2])
    with db.transaction(conn):
        db.clear_item_hold(conn, items[2])

    assert [(e.item.id, e.position) for e in plan(conn, config)] == before


# -- selection: per-item, never global -------------------------------------


def test_held_is_not_a_global_hold(conn, config):
    """``_GLOBAL_HOLDS`` is the difference between solving issue #117's scenario and
    reproducing it. A global hold would stop the pass at the first held item, leaving the
    four items the author does not want in front of the one they do."""
    assert ordering.HoldReason.HELD not in dispatch._GLOBAL_HOLDS
    assert set(dispatch._GLOBAL_HOLDS) == {
        ordering.HoldReason.PAUSED,
        ordering.HoldReason.CAPACITY_UNOBSERVABLE,
        ordering.HoldReason.GLOBAL_CAP,
    }


def test_four_held_items_at_the_head_do_not_stop_the_fifth(conn, config):
    """The issue's reported situation, as an assertion. The first dispatchable entry the
    selection loop would reach is the unheld one behind them."""
    items = ready(conn, 5)
    for item_id in items[:4]:
        hold_item(conn, item_id)

    entries = plan(conn, config)
    first_dispatchable = next(e for e in entries if e.dispatchable)

    assert [e.hold for e in entries[:4]] == [ordering.HoldReason.HELD] * 4
    assert first_dispatchable.item.id == items[4]


# -- FR-022: honoured on the first pass after the daemon starts -------------


def test_a_hold_written_with_no_daemon_running_is_honoured_by_the_very_next_plan(
    conn, config
):
    """Nothing caches holds — ``plan`` reads them on every pass rather than at startup —
    so the assertion is that the *first* plan after the write already reports it. There is
    no in-memory copy to go stale and nothing for a restart to invalidate."""
    item_id = ready(conn, 1)[0]
    assert plan(conn, config)[0].hold is None

    hold_item(conn, item_id)

    assert plan(conn, config)[0].hold is ordering.HoldReason.HELD


@pytest.mark.parametrize("by", ["cli", "web"])
def test_either_surface_is_recorded_and_rendered(conn, config, by):
    item_id = ready(conn, 1)[0]
    hold_item(conn, item_id, by=by)
    assert by in plan(conn, config)[0].detail


# -- US2: the repository scope ---------------------------------------------


def test_a_repository_hold_holds_every_item_in_it(conn, config):
    items = ready(conn, 3, repo_key="demo")
    hold_repo(conn, "demo")

    entries = plan(conn, config)

    assert [e.hold for e in entries] == [ordering.HoldReason.HELD] * 3
    assert all("demo" in e.detail for e in entries)
    assert {e.item.id for e in entries} == set(items)


def test_only_the_named_repository_is_held(conn, config):
    """Two repositories, one held. The other must be untouched in the same plan."""
    seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.READY))
    seed_item(conn, repo_key="ghost", issue_number=2, state=str(WorkItemState.READY))
    hold_repo(conn, "demo")

    holds = {e.item.repo_key: e.hold for e in plan(conn, config)}

    assert holds["demo"] is ordering.HoldReason.HELD
    # `ghost` is not onboarded, so it carries its own reason — the point is only that it is
    # not *held*, i.e. the repository hold did not leak across the boundary.
    assert holds["ghost"] is not ordering.HoldReason.HELD


# -- FR-017: both holds at once --------------------------------------------


def test_an_item_hold_alone_names_only_the_item(conn, config):
    item_id = ready(conn, 1)[0]
    hold_item(conn, item_id, by="cli")

    detail = plan(conn, config)[0].detail

    assert detail.startswith("held since")
    assert "repository" not in detail
    assert "releasing one" not in detail


def test_a_repository_hold_alone_names_only_the_repository(conn, config):
    ready(conn, 1, repo_key="demo")
    hold_repo(conn, "demo", by="web")

    detail = plan(conn, config)[0].detail

    assert "repository demo is held" in detail
    assert "releasing one" not in detail


def test_both_holds_at_once_name_both_and_say_so(conn, config):
    """The failure this prevents: the author releases the item hold, expects the item to
    run, and it does not — with the surface still saying ``held`` and looking like it
    ignored the release."""
    item_id = ready(conn, 1, repo_key="demo")[0]
    hold_item(conn, item_id, by="cli")
    hold_repo(conn, "demo", by="web")

    entry = plan(conn, config)[0]

    assert entry.hold is ordering.HoldReason.HELD, "still exactly one reason (FR-015)"
    assert "held since" in entry.detail
    assert "repository demo is held" in entry.detail
    assert "releasing one leaves the other in force" in entry.detail


def test_releasing_the_item_hold_leaves_the_repository_hold_reported_alone(conn, config):
    item_id = ready(conn, 1, repo_key="demo")[0]
    hold_item(conn, item_id)
    hold_repo(conn, "demo")

    with db.transaction(conn):
        db.clear_item_hold(conn, item_id)

    entry = plan(conn, config)[0]
    assert entry.hold is ordering.HoldReason.HELD
    assert "repository demo is held" in entry.detail
    assert "releasing one" not in entry.detail


def test_releasing_both_holds_frees_the_item(conn, config):
    item_id = ready(conn, 1, repo_key="demo")[0]
    hold_item(conn, item_id)
    hold_repo(conn, "demo")

    with db.transaction(conn):
        db.clear_item_hold(conn, item_id)
        db.clear_repo_hold(conn, "demo")

    assert plan(conn, config)[0].hold is None


# -- FR-012: work discovered later is held on arrival ----------------------


def test_an_item_discovered_after_the_hold_is_held_on_arrival(conn, config):
    """The half a per-item hold cannot express, and the reason the issue asked for
    repository scope: nothing backfills, and no event is hooked. The hold is a fact about
    the repository, so the *first* plan that sees the new item already reports it.

    The finished item is what onboards the repository — a hold cannot be placed on one the
    system does not watch (FR-006), which the foreign key enforces — and being ``done`` it
    keeps the queue empty until the newcomer arrives.
    """
    seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.DONE))
    hold_repo(conn, "demo")
    assert plan(conn, config) == []

    newcomer = seed_item(
        conn, repo_key="demo", issue_number=99, state=str(WorkItemState.READY)
    )

    entries = plan(conn, config)
    assert [e.item.id for e in entries] == [newcomer]
    assert entries[0].hold is ordering.HoldReason.HELD


def test_a_repository_hold_survives_and_still_applies_after_release_and_replacement(
    conn, config
):
    """Releasing and re-holding is a fresh statement, not a resurrection of the old one."""
    ready(conn, 1, repo_key="demo")
    hold_repo(conn, "demo")
    with db.transaction(conn):
        db.clear_repo_hold(conn, "demo")
    assert plan(conn, config)[0].hold is None

    hold_repo(conn, "demo", by="web")
    assert "by web" in plan(conn, config)[0].detail

