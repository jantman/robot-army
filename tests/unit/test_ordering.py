"""The one producer of dispatch order (T016, T035, T042, T043).

``plan()`` is the only place in the system that decides what runs next, and both the
dispatcher and both surfaces walk what it returns. So the properties under test here are
the ones that make that single-producer claim worth anything: the order is total, it is
stable, the positions are honest, and computing it changes nothing.
"""

from __future__ import annotations

import sqlite3

import pytest
from tests.conftest import seed_item

from robot_army import capacity, db, ordering
from robot_army.states import WorkItemState


def snapshot(**overrides):
    """A capacity snapshot built by hand, so ordering tests never construct a fake /proc.

    That separation is the plan's Structure Decision made visible: ``capacity`` observes
    the machine and ``ordering`` applies configuration, and an ordering test that had to
    invent a process tree would be testing the wrong module.
    """
    fields = {
        "observable": True,
        "degraded": False,
        "total": 0,
        "ours": (),
        "others": 0,
        "global_cap": 2,
        "per_repo": {},
        "reason": None,
    }
    fields.update(overrides)
    return capacity.CapacitySnapshot(**fields)


def ready(conn, count: int, *, repo_key: str = "demo") -> list[int]:
    ids = []
    for n in range(1, count + 1):
        item = seed_item(
            conn, repo_key=repo_key, issue_number=n, state=str(WorkItemState.READY)
        )
        ids.append(item)
    return ids


# -- positions and totality -------------------------------------------------


def test_positions_are_one_based_and_contiguous(conn, config):
    ready(conn, 3)
    entries = ordering.plan(conn, config=config, capacity=snapshot())
    assert [e.position for e in entries] == [1, 2, 3]


def test_only_ready_items_appear(conn, config):
    ready(conn, 2)
    seed_item(conn, issue_number=99, state=str(WorkItemState.ACTIVE))
    entries = ordering.plan(conn, config=config, capacity=snapshot())
    assert len(entries) == 2
    assert all(e.item.state is WorkItemState.READY for e in entries)


def test_the_order_is_stable_across_repeated_calls(conn, config):
    """SC-006 checks the same question a hundred times in a row and expects the same
    answer. An order that is merely usually the same is not an order."""
    ready(conn, 5)
    first = [e.item.id for e in ordering.plan(conn, config=config, capacity=snapshot())]
    for _ in range(100):
        again = [e.item.id for e in ordering.plan(conn, config=config, capacity=snapshot())]
        assert again == first


def test_plan_writes_nothing(conn, config):
    """It runs on every web page render and on every dispatch pass. A producer that wrote
    would make reading the queue a state change, which is how a display becomes a bug."""
    ready(conn, 3)
    before = conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]
    versions = conn.execute("PRAGMA data_version").fetchone()[0]

    ordering.plan(conn, config=config, capacity=snapshot())

    assert conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == before
    assert conn.execute("PRAGMA data_version").fetchone()[0] == versions
    assert conn.in_transaction is False


def test_simulated_items_are_planned_too(conn, config):
    """They occupy a slot, so leaving them out of the queue would show an order the
    dispatcher does not walk."""
    seed_item(conn, issue_number=1, dry_run=True, state=str(WorkItemState.READY))
    entries = ordering.plan(conn, config=config, capacity=snapshot())
    assert [e.item.dry_run for e in entries] == [True]


# -- hold reasons available in this phase -----------------------------------


def test_an_empty_machine_holds_nothing(conn, config):
    ready(conn, 2)
    entries = ordering.plan(conn, config=config, capacity=snapshot())
    assert all(e.hold is None for e in entries)
    assert all(e.dispatchable for e in entries)


def test_a_full_machine_holds_every_item_on_the_global_cap(conn, config):
    ready(conn, 2)
    entries = ordering.plan(
        conn, config=config, capacity=snapshot(total=2, global_cap=2, others=2)
    )
    assert {e.hold for e in entries} == {ordering.HoldReason.GLOBAL_CAP}
    assert "2 of 2" in entries[0].detail
    # FR-003: the split is what tells the author whether to close one of their own.
    assert "2 other" in entries[0].detail


def test_an_unobservable_capacity_outranks_the_caps(conn, config):
    """When it applies, the cap numbers are not trustworthy, and showing an untrustworthy
    number is worse than showing none."""
    ready(conn, 1)
    entries = ordering.plan(
        conn,
        config=config,
        capacity=snapshot(observable=False, total=0, reason="registry vanished"),
    )
    assert entries[0].hold is ordering.HoldReason.CAPACITY_UNOBSERVABLE
    assert "registry vanished" in entries[0].detail


def test_a_pause_outranks_a_capacity_reason(conn, config):
    """US3 AS4. Reporting the cap to a paused system would send the author to free
    capacity that changes nothing."""
    ready(conn, 1)
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="test")
    entries = ordering.plan(
        conn, config=config, capacity=snapshot(total=9, global_cap=2)
    )
    assert entries[0].hold is ordering.HoldReason.PAUSED


def test_the_precedence_is_declared_in_one_readable_place():
    """Declaration order *is* the precedence, so the rule cannot be stated in two places
    and drift between them."""
    assert list(ordering.HoldReason) == [
        ordering.HoldReason.PAUSED,
        ordering.HoldReason.CAPACITY_UNOBSERVABLE,
        ordering.HoldReason.GLOBAL_CAP,
        ordering.HoldReason.REPO_CAP,
        ordering.HoldReason.NOT_ONBOARDED,
        ordering.HoldReason.PREPARATION_FAILED,
    ]


@pytest.mark.parametrize("reason", list(ordering.HoldReason))
def test_every_hold_reason_renders_as_its_own_word(reason):
    """A ``StrEnum`` so a surface can print it without a lookup table, and so a test can
    assert against the same spelling the log carries."""
    assert str(reason) == reason.value
    assert reason.value.replace("_", "").isalpha()


# -- the rest of the precedence (T035) --------------------------------------


def test_the_global_cap_outranks_the_repository_cap(conn, config):
    """The machine-wide limit binds before any one repository's, and telling the author to
    raise a repository cap that would change nothing wastes the trip."""
    ready(conn, 1)
    entries = ordering.plan(
        conn,
        config=config,
        capacity=snapshot(total=2, global_cap=2, per_repo={"demo": 5}),
    )
    assert entries[0].hold is ordering.HoldReason.GLOBAL_CAP


def test_a_repository_at_its_cap_outranks_an_unonboarded_repository(conn, config):
    ready(conn, 1, repo_key="demo")
    entries = ordering.plan(
        conn, config=config, capacity=snapshot(total=0, global_cap=9, per_repo={"demo": 1})
    )
    assert entries[0].hold is ordering.HoldReason.REPO_CAP


def test_a_work_item_cannot_outlive_its_onboarding_record(conn, config):
    """Why ``plan`` does not check onboarding: the schema already does.

    ``work_items.repo_key`` is a foreign key into ``repos``, which has a row only once
    onboarding happened. A hold reason for a state the database refuses to represent would
    be a branch that can never be taken, and one that invites the reader to believe the
    queue is watching something it is not.
    """
    seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.READY))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM repos WHERE repo_key = 'demo'")


def test_a_repository_that_does_not_resolve_is_held_rather_than_attempted(conn, config):
    """The one case the schema cannot prevent: a row exists but resolves to no clone —
    onboarded before migration 005 with no section to supply a path, or its record since
    deleted. ``dispatch_item`` already fails such an item; reporting it here means the
    author sees it in the queue instead of after an attempt."""
    seed_item(conn, repo_key="ghost", issue_number=1, state=str(WorkItemState.READY))
    entries = ordering.plan(conn, config=config, capacity=snapshot(global_cap=9))
    assert entries[0].hold is ordering.HoldReason.NOT_ONBOARDED
    assert "does not resolve to a clone" in entries[0].detail


def test_a_residual_failure_reason_is_reported_last(conn, config):
    """Ranked last because it is not a queueing condition at all: it would hold this item
    on a completely empty machine, and freeing capacity changes nothing about it."""
    item = seed_item(conn, issue_number=1, state=str(WorkItemState.READY))
    conn.execute(
        "UPDATE work_items SET blocked_reason = ? WHERE id = ?",
        ("workspace trust check failed", item),
    )
    entries = ordering.plan(conn, config=config, capacity=snapshot(global_cap=9))
    assert entries[0].hold is ordering.HoldReason.PREPARATION_FAILED
    assert entries[0].detail == "workspace trust check failed"


def test_exactly_one_reason_is_ever_reported(conn, config):
    """Two reasons shown at once is how a surface stops being read."""
    item = seed_item(conn, repo_key="ghost", issue_number=1, state=str(WorkItemState.READY))
    conn.execute("UPDATE work_items SET blocked_reason = 'also this' WHERE id = ?", (item,))
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="test")

    entry = ordering.plan(
        conn,
        config=config,
        capacity=snapshot(observable=False, total=9, global_cap=1, reason="nope"),
    )[0]
    assert entry.hold is ordering.HoldReason.PAUSED
    assert isinstance(entry.hold, ordering.HoldReason)


# -- FR-020 and FR-021 (T043) -----------------------------------------------


def test_a_held_head_does_not_prevent_later_items_being_considered(conn, config):
    """FR-020. A repository at its cap blocks its own work and nothing else — otherwise one
    busy repository stalls every other, which is the whole reason the hold is per item."""
    ready(conn, 1, repo_key="demo")
    seed_item(conn, repo_key="other", issue_number=7, state=str(WorkItemState.READY))

    entries = ordering.plan(
        conn,
        config=config,
        capacity=snapshot(total=1, global_cap=9, per_repo={"demo": 1}),
    )
    holds = {e.item.repo_key: e.hold for e in entries}
    assert holds["demo"] is ordering.HoldReason.REPO_CAP
    # "other" is not in the config, so it holds for its own reason — but it holds for a
    # *different* one, which is the point: the head's condition is not contagious.
    assert holds["other"] is not ordering.HoldReason.REPO_CAP


def test_nothing_ages_or_re_prioritises_across_repeated_passes(conn, config):
    """FR-021. Starvation under repository-priority ordering is an accepted, documented
    consequence of choosing that mode, not a bug to be papered over with a counter — and a
    counter would be state, which this module deliberately holds none of."""
    ready(conn, 4)
    first = [e.item.id for e in ordering.plan(conn, config=config, capacity=snapshot())]
    for _ in range(50):
        held = ordering.plan(
            conn, config=config, capacity=snapshot(total=9, global_cap=1)
        )
        assert [e.item.id for e in held] == first
    after = [e.item.id for e in ordering.plan(conn, config=config, capacity=snapshot())]
    assert after == first


# -- the two ordering modes (T042) ------------------------------------------


def with_order(config, mode: str, priorities: dict[str, int] | None = None):
    """A config in one ordering mode, with per-repository priorities applied."""
    from dataclasses import replace

    repos = dict(config.repos)
    for key, value in (priorities or {}).items():
        base = repos.get(key)
        repos[key] = (
            replace(base, priority=value)
            if base is not None
            else replace(config.repos["demo"], key=key, priority=value)
        )
    return replace(
        config,
        repos=repos,
        dispatch=replace(config.dispatch, order=mode, default_repo_max_sessions=9),
    )


def discovered(conn, item_id: int, stamp: str) -> None:
    conn.execute("UPDATE work_items SET discovered_at = ? WHERE id = ?", (stamp, item_id))


def order_of(conn, config) -> list[int]:
    return [
        e.item.id
        for e in ordering.plan(conn, config=config, capacity=snapshot(global_cap=99))
    ]


def test_oldest_first_ignores_the_repository_entirely(conn, config):
    late_demo = seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.READY))
    early_other = seed_item(
        conn, repo_key="other", issue_number=2, state=str(WorkItemState.READY)
    )
    discovered(conn, late_demo, "2026-01-02T00:00:00Z")
    discovered(conn, early_other, "2026-01-01T00:00:00Z")

    ordered = order_of(conn, with_order(config, "oldest-first", {"other": 99}))
    assert ordered == [early_other, late_demo], "priority must not leak into oldest-first"


def test_repo_priority_drains_the_higher_priority_repository_first(conn, config):
    """The point of the mode: a repository the author cares about more today runs before
    one they care about less, even when its work arrived later."""
    low = seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.READY))
    high = seed_item(conn, repo_key="urgent", issue_number=2, state=str(WorkItemState.READY))
    discovered(conn, low, "2026-01-01T00:00:00Z")
    discovered(conn, high, "2026-01-09T00:00:00Z")

    config = with_order(config, "repo-priority", {"demo": 0, "urgent": 10})
    assert order_of(conn, config) == [high, low]


def test_equal_priorities_fall_back_to_oldest_first(conn, config):
    """FR-016's "ties broken oldest-first", which the shared key tail gives for free."""
    first = seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.READY))
    second = seed_item(conn, repo_key="other", issue_number=2, state=str(WorkItemState.READY))
    discovered(conn, first, "2026-01-01T00:00:00Z")
    discovered(conn, second, "2026-01-02T00:00:00Z")

    config = with_order(config, "repo-priority", {"demo": 5, "other": 5})
    assert order_of(conn, config) == [first, second]


def test_an_unconfigured_repository_takes_the_default_priority(conn, config):
    """Zero, which makes the mode degrade to oldest-first for it rather than pushing it to
    either end of the queue on the strength of a value nobody wrote."""
    ranked = seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.READY))
    unranked = seed_item(conn, repo_key="ghost", issue_number=2, state=str(WorkItemState.READY))
    discovered(conn, ranked, "2026-01-05T00:00:00Z")
    discovered(conn, unranked, "2026-01-01T00:00:00Z")

    assert ordering.order_key(
        db.get_work_item(conn, unranked), None, "repo-priority"
    ) == (0, "2026-01-01T00:00:00Z", unranked)

    config = with_order(config, "repo-priority", {"demo": 3})
    assert order_of(conn, config) == [ranked, unranked]


@pytest.mark.parametrize("mode", ["oldest-first", "repo-priority"])
def test_both_keys_produce_a_total_order(conn, config, mode):
    """Total, not merely deterministic: no two distinct items may compare equal, or the
    sort's tie-break becomes the input order and SC-006's hundred checks mean nothing."""
    items = []
    for n in range(1, 7):
        item = seed_item(
            conn,
            repo_key="demo" if n % 2 else "other",
            issue_number=n,
            state=str(WorkItemState.READY),
        )
        # Every item discovered at the same instant, which is the case a key that leans on
        # timestamps alone gets wrong.
        discovered(conn, item, "2026-01-01T00:00:00Z")
        items.append(item)

    config = with_order(config, mode, {"demo": 1, "other": 1})
    keys = [
        ordering.order_key(
            db.get_work_item(conn, i), config.repos.get("demo"), mode
        )
        for i in items
    ]
    assert len(set(keys)) == len(keys)
    assert order_of(conn, config) == items


def test_a_negative_priority_sorts_a_repository_last(conn, config):
    background = seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.READY))
    normal = seed_item(conn, repo_key="other", issue_number=2, state=str(WorkItemState.READY))
    discovered(conn, background, "2026-01-01T00:00:00Z")
    discovered(conn, normal, "2026-01-02T00:00:00Z")

    config = with_order(config, "repo-priority", {"demo": -5, "other": 0})
    assert order_of(conn, config) == [normal, background]
