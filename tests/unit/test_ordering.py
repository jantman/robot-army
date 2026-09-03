"""The one producer of dispatch order (T016, T035, T042, T043).

``plan()`` is the only place in the system that decides what runs next, and both the
dispatcher and both surfaces walk what it returns. So the properties under test here are
the ones that make that single-producer claim worth anything: the order is total, it is
stable, the positions are honest, and computing it changes nothing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

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
        ordering.HoldReason.AWAITING_MERGE,
        ordering.HoldReason.NOT_ONBOARDED,
        ordering.HoldReason.OFF_COLUMN,
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


# -- the wait-for-merge gate (milestone 047) --------------------------------


def waiting(config, *, globally: bool = False, repo_key: str = "demo"):
    """``config`` with wait-for-merge in force, either globally or for one repository."""
    if globally:
        return replace(config, dispatch=replace(config.dispatch, wait_for_merge=True))
    section = config.repos[repo_key]
    return replace(
        config, repos={**config.repos, repo_key: replace(section, wait_for_merge=True)}
    )


def test_an_unfinished_item_holds_the_rest_of_its_repository(conn, config):
    """The feature the issue asks for: the next issue does not start until the last one
    has landed."""
    seed_item(conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW))
    ready(conn, 1, repo_key="demo")

    entry = ordering.plan(
        conn, config=waiting(config), capacity=snapshot(global_cap=9)
    )[0]

    assert entry.hold is ordering.HoldReason.AWAITING_MERGE
    # SC-003: actionable without opening the log.
    assert "demo" in entry.detail
    assert "#41" in entry.detail
    assert "awaiting_review" in entry.detail


def test_the_global_setting_holds_a_repository_with_no_section_of_its_own(conn, config):
    seed_item(conn, repo_key="demo", issue_number=41, state=str(WorkItemState.ACTIVE))
    ready(conn, 1, repo_key="demo")

    entry = ordering.plan(
        conn, config=waiting(config, globally=True), capacity=snapshot(global_cap=9)
    )[0]

    assert entry.hold is ordering.HoldReason.AWAITING_MERGE


@pytest.mark.parametrize("terminal", [WorkItemState.DONE, WorkItemState.ABANDONED])
def test_the_hold_lifts_when_the_unfinished_item_reaches_a_terminal_state(
    conn, config, terminal
):
    """FR-008. Merging a pull request that says *closes #N* closes the issue, and a closed
    issue already becomes ``done`` — so the release needs no new machinery."""
    blocker = seed_item(
        conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW)
    )
    ready(conn, 1, repo_key="demo")
    held = waiting(config)
    assert ordering.plan(conn, config=held, capacity=snapshot(global_cap=9))[0].hold

    conn.execute("UPDATE work_items SET state = ? WHERE id = ?", (str(terminal), blocker))

    entry = ordering.plan(conn, config=held, capacity=snapshot(global_cap=9))[0]
    assert entry.hold is None
    assert entry.dispatchable


@pytest.mark.parametrize(
    "state",
    [
        WorkItemState.DISPATCHING,
        WorkItemState.ACTIVE,
        WorkItemState.AWAITING_REVIEW,
        WorkItemState.INTERRUPTED,
        WorkItemState.FAILED,
    ],
)
def test_every_dispatched_non_terminal_state_holds(conn, config, state):
    """``failed`` and ``interrupted`` count too: a repository asked to run one thing at a
    time has one thing in flight whatever condition it is in, and ``retry`` and ``abandon``
    are how the author says which."""
    seed_item(conn, repo_key="demo", issue_number=41, state=str(state))
    ready(conn, 1, repo_key="demo")

    entry = ordering.plan(
        conn, config=waiting(config), capacity=snapshot(global_cap=9)
    )[0]
    assert entry.hold is ordering.HoldReason.AWAITING_MERGE


def test_ready_items_do_not_hold_each_other(conn, config):
    """The load-bearing exclusion. Counting ``ready`` would make two queued issues wait on
    each other forever, which is a deadlock rather than a gate."""
    ready(conn, 3, repo_key="demo")

    entries = ordering.plan(conn, config=waiting(config), capacity=snapshot(global_cap=9))

    assert [e.hold for e in entries] == [None, None, None]


def test_a_discovered_item_does_not_hold_either(conn, config):
    """Pre-dispatch in the other direction: an item polling found but nothing has prepared
    has produced no branch and no pull request, so there is nothing to wait for."""
    seed_item(conn, repo_key="demo", issue_number=41, state=str(WorkItemState.DISCOVERED))
    ready(conn, 1, repo_key="demo")

    entry = ordering.plan(
        conn, config=waiting(config), capacity=snapshot(global_cap=9)
    )[0]
    assert entry.hold is None


def test_the_gate_holds_one_repository_and_leaves_the_others_moving(conn, config):
    """FR-007. One waiting repository must not stall every other one."""
    seed_item(conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW))
    ready(conn, 1, repo_key="demo")
    seed_item(conn, repo_key="other", issue_number=7, state=str(WorkItemState.AWAITING_REVIEW))
    seed_item(conn, repo_key="other", issue_number=8, state=str(WorkItemState.READY))

    entries = ordering.plan(
        conn, config=waiting(config), capacity=snapshot(global_cap=9)
    )
    holds = {e.item.repo_key: e.hold for e in entries}

    assert holds["demo"] is ordering.HoldReason.AWAITING_MERGE
    # "other" has no section, so it holds for its own unrelated reason — but it holds for a
    # *different* one, which is the point: demo's wait is not contagious.
    assert holds["other"] is not ordering.HoldReason.AWAITING_MERGE


def test_the_setting_off_changes_nothing(conn, config):
    """The default. An installation that never asked for this must behave exactly as it
    did before the feature existed."""
    seed_item(conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW))
    ready(conn, 1, repo_key="demo")

    entry = ordering.plan(conn, config=config, capacity=snapshot(global_cap=9))[0]
    assert entry.hold is None


def test_a_repository_may_opt_out_of_a_global_setting(conn, config):
    seed_item(conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW))
    ready(conn, 1, repo_key="demo")
    opted_out = replace(
        replace(config, dispatch=replace(config.dispatch, wait_for_merge=True)),
        repos={**config.repos, "demo": replace(config.repos["demo"], wait_for_merge=False)},
    )

    entry = ordering.plan(conn, config=opted_out, capacity=snapshot(global_cap=9))[0]
    assert entry.hold is None


def test_the_repository_cap_outranks_the_merge_gate(conn, config):
    """R4. While a session is still running there is a slot to free, and sending the author
    off to merge a pull request points at the wrong fix."""
    seed_item(conn, repo_key="demo", issue_number=41, state=str(WorkItemState.ACTIVE))
    ready(conn, 1, repo_key="demo")

    entry = ordering.plan(
        conn,
        config=waiting(config),
        capacity=snapshot(total=1, global_cap=9, per_repo={"demo": 1}),
    )[0]
    assert entry.hold is ordering.HoldReason.REPO_CAP


def test_the_merge_gate_outranks_a_residual_preparation_failure(conn, config):
    """It is a condition of the queue rather than of the item: an empty machine does not
    clear it, and the residue would still be there once it did."""
    seed_item(conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW))
    item = seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.READY))
    conn.execute(
        "UPDATE work_items SET blocked_reason = 'trust check failed' WHERE id = ?", (item,)
    )

    entry = ordering.plan(
        conn, config=waiting(config), capacity=snapshot(global_cap=9)
    )[0]
    assert entry.hold is ordering.HoldReason.AWAITING_MERGE


def test_a_pause_still_outranks_the_merge_gate(conn, config):
    seed_item(conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW))
    ready(conn, 1, repo_key="demo")
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="test")

    entry = ordering.plan(
        conn, config=waiting(config), capacity=snapshot(global_cap=9)
    )[0]
    assert entry.hold is ordering.HoldReason.PAUSED


def test_a_simulated_item_is_gated_exactly_as_a_real_one_is(conn, config):
    """Both directions. A dry run exists to rehearse the real behaviour, and no outward
    request is made either way — so there is nothing here for dry-run isolation to protect."""
    seed_item(
        conn,
        repo_key="demo",
        issue_number=41,
        dry_run=True,
        state=str(WorkItemState.AWAITING_REVIEW),
    )
    seed_item(
        conn, repo_key="demo", issue_number=1, dry_run=True, state=str(WorkItemState.READY)
    )

    entry = ordering.plan(
        conn, config=waiting(config), capacity=snapshot(global_cap=9)
    )[0]
    assert entry.hold is ordering.HoldReason.AWAITING_MERGE


def test_several_blockers_name_the_oldest_and_count_the_rest(conn, config):
    """One sentence the author can act on beats a list they have to read."""
    seed_item(conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW))
    seed_item(conn, repo_key="demo", issue_number=42, state=str(WorkItemState.FAILED))
    ready(conn, 1, repo_key="demo")

    entry = ordering.plan(
        conn, config=waiting(config), capacity=snapshot(global_cap=9)
    )[0]
    assert "#41" in entry.detail
    assert "and 1 more" in entry.detail


def test_the_gate_still_writes_nothing(conn, config):
    """FR-013. It runs on every web page render, so the extra query must stay a read."""
    seed_item(conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW))
    ready(conn, 1, repo_key="demo")
    versions = conn.execute("PRAGMA data_version").fetchone()[0]

    ordering.plan(conn, config=waiting(config), capacity=snapshot(global_cap=9))

    assert conn.execute("PRAGMA data_version").fetchone()[0] == versions
    assert conn.in_transaction is False


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


# -- board ordering (issue #48, T024) ---------------------------------------


def govern(conn, repo_key="demo", *, column="Ready", read=True, resolved=True):
    """Mark a repository as governed by a board, or partly so.

    The two timestamps are separately controllable because the gate needs both, and the
    interesting failures are the states where only one is present.
    """
    from robot_army.models import RepoProject

    with db.transaction(conn):
        db.save_repo_project(
            conn,
            RepoProject(
                repo_key=repo_key,
                project_id="PVT_3",
                project_number=3,
                column_name=column,
                resolved_at="2026-09-02T00:00:00Z" if resolved else None,
                last_read_at="2026-09-02T00:00:00Z" if read else None,
            ),
        )


def place(conn, item_id: int, column: str | None, position: int | None = None) -> None:
    conn.execute(
        "UPDATE work_items SET board_column = ?, board_position = ? WHERE id = ?",
        (column, position, item_id),
    )


def test_the_board_decides_the_order_within_a_repository(conn, config):
    first, second, third = ready(conn, 3)
    discovered(conn, first, "2026-01-01T00:00:00Z")
    discovered(conn, second, "2026-01-02T00:00:00Z")
    discovered(conn, third, "2026-01-03T00:00:00Z")
    govern(conn)
    place(conn, third, "Ready", 1)
    place(conn, first, "Ready", 2)
    place(conn, second, "Ready", 3)

    assert order_of(conn, config) == [third, first, second]


def test_an_item_the_board_does_not_mention_sorts_after_the_ranked_ones(conn, config):
    """FR-008. The board expresses no opinion about it, so it dispatches — but it does not
    jump ahead of cards the author actually arranged."""
    absent, ranked = ready(conn, 2)
    discovered(conn, absent, "2026-01-01T00:00:00Z")
    discovered(conn, ranked, "2026-01-09T00:00:00Z")
    govern(conn)
    place(conn, ranked, "Ready", 1)

    entries = ordering.plan(conn, config=config, capacity=snapshot(global_cap=99))

    assert [e.item.id for e in entries] == [ranked, absent]
    assert all(e.hold is None for e in entries)


def test_board_ordering_leaves_a_repositorys_queue_positions_alone(conn, config):
    """FR-002, and the reason board ordering is a permutation rather than a sort key.

    Under `repo-priority` the repositories interleave in a particular way. Reordering one
    repository's cards must change *which* of its items sits at each of its positions and
    nothing else — a key mixing board rank with discovery time would silently redefine
    what the mode means.
    """
    demo_a = seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.READY))
    demo_b = seed_item(conn, repo_key="demo", issue_number=2, state=str(WorkItemState.READY))
    other = seed_item(conn, repo_key="other", issue_number=3, state=str(WorkItemState.READY))
    discovered(conn, demo_a, "2026-01-01T00:00:00Z")
    discovered(conn, other, "2026-01-02T00:00:00Z")
    discovered(conn, demo_b, "2026-01-03T00:00:00Z")
    prioritised = with_order(config, "repo-priority", {"demo": 0, "other": 5})

    before = order_of(conn, prioritised)
    demo_slots = [i for i, item in enumerate(before) if item in (demo_a, demo_b)]

    govern(conn)
    place(conn, demo_b, "Ready", 1)
    place(conn, demo_a, "Ready", 2)
    after = order_of(conn, prioritised)

    assert [i for i, item in enumerate(after) if item in (demo_a, demo_b)] == demo_slots
    assert after.index(other) == before.index(other)
    assert [after[i] for i in demo_slots] == [demo_b, demo_a]


def test_another_repository_is_untouched_by_a_governed_one(conn, config):
    mine = seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.READY))
    theirs = seed_item(conn, repo_key="other", issue_number=2, state=str(WorkItemState.READY))
    discovered(conn, mine, "2026-01-02T00:00:00Z")
    discovered(conn, theirs, "2026-01-01T00:00:00Z")
    govern(conn)
    place(conn, mine, "Ready", 1)

    entries = ordering.plan(conn, config=config, capacity=snapshot(global_cap=99))

    # Order only: `other` has no section and no recorded clone, so it carries the
    # pre-existing `not_onboarded` hold that every such repository in these tests carries.
    # What matters here is that a governed repository does not disturb its position.
    assert [e.item.id for e in entries] == [theirs, mine]
    assert next(e for e in entries if e.item.id == mine).hold is None


def test_board_key_is_total(conn, config):
    """Two renders of unchanged state must produce one list. `board_position` is dense but
    not unique across the ranked and unranked groups, so the id tail is what makes it so."""
    ids = ready(conn, 4)
    govern(conn)
    place(conn, ids[0], "Ready", 1)
    place(conn, ids[1], "Ready", 1)

    keys = [ordering.board_key(db.get_work_item(conn, i)) for i in ids]

    assert len(set(keys)) == len(keys)
    assert order_of(conn, config) == order_of(conn, config)


def test_an_item_parked_in_another_column_is_held(conn, config):
    ranked, parked = ready(conn, 2)
    govern(conn)
    place(conn, ranked, "Ready", 1)
    place(conn, parked, "Backlog")

    entries = {e.item.id: e for e in ordering.plan(
        conn, config=config, capacity=snapshot(global_cap=99)
    )}

    assert entries[ranked].hold is None
    assert entries[parked].hold is ordering.HoldReason.OFF_COLUMN
    assert "'Backlog'" in entries[parked].detail
    assert "'Ready'" in entries[parked].detail


def test_nothing_is_held_before_a_board_has_ever_been_read(conn, config):
    """FR-014. With no board knowledge the system has no business inventing a hold — and a
    resolution that has never produced a read is exactly that state."""
    item = ready(conn, 1)[0]
    govern(conn, read=False)
    place(conn, item, "Backlog")

    entries = ordering.plan(conn, config=config, capacity=snapshot(global_cap=99))

    assert entries[0].hold is None


def test_nothing_is_held_when_the_repository_has_ordering_off(conn, config):
    """FR-020: switching it off restores today's behaviour exactly, holds included."""
    from dataclasses import replace as _replace

    item = ready(conn, 1)[0]
    govern(conn)
    place(conn, item, "Backlog")
    off = _replace(config, dispatch=_replace(config.dispatch, project_ordering=False))

    entries = ordering.plan(conn, config=off, capacity=snapshot(global_cap=99))

    assert entries[0].hold is None


def test_an_item_absent_from_a_read_board_is_not_held(conn, config):
    """The distinction that makes the split rule a split rule: not on the board is no
    signal, and holding it would invent an instruction the author never gave."""
    ready(conn, 1)
    govern(conn)

    entries = ordering.plan(conn, config=config, capacity=snapshot(global_cap=99))

    assert entries[0].hold is None


def test_a_missing_clone_outranks_a_parked_card(conn, config):
    """R11's precedence: telling the author to move a card while the clone is gone points
    at the wrong fix."""
    # A repository with no `[repos.*]` section and no recorded clone path resolves to
    # nothing, which is the state `not_onboarded` exists for.
    item = seed_item(conn, repo_key="ghost", issue_number=1, state=str(WorkItemState.READY))
    govern(conn, "ghost")
    place(conn, item, "Backlog")

    entries = ordering.plan(conn, config=config, capacity=snapshot(global_cap=99))

    assert entries[0].hold is ordering.HoldReason.NOT_ONBOARDED


def test_a_parked_card_outranks_stale_preparation_residue(conn, config):
    """Parking is a deliberate, more recent statement than residue from an attempt the
    author has since stepped back from."""
    item = ready(conn, 1)[0]
    govern(conn)
    place(conn, item, "Backlog")
    conn.execute(
        "UPDATE work_items SET failure_reason = 'an old failure' WHERE id = ?", (item,)
    )

    entries = ordering.plan(conn, config=config, capacity=snapshot(global_cap=99))

    assert entries[0].hold is ordering.HoldReason.OFF_COLUMN


def test_a_null_board_position_never_behaves_as_zero(conn, config):
    """The mistake `commits_ahead` records: folding "could not determine" into 0 would
    promote every item of an unread board to the head of its repository's queue."""
    unranked, ranked = ready(conn, 2)
    discovered(conn, unranked, "2026-01-01T00:00:00Z")
    discovered(conn, ranked, "2026-01-09T00:00:00Z")
    govern(conn)
    place(conn, ranked, "Ready", 1)

    assert ordering.board_key(db.get_work_item(conn, unranked))[0] == 1
    assert ordering.board_key(db.get_work_item(conn, ranked))[0] == 0
    assert order_of(conn, config)[0] == ranked


def test_plan_still_writes_nothing_with_a_board_in_play(conn, config):
    ids = ready(conn, 2)
    govern(conn)
    place(conn, ids[0], "Ready", 1)
    place(conn, ids[1], "Backlog")
    versions = conn.execute("PRAGMA data_version").fetchone()[0]

    ordering.plan(conn, config=config, capacity=snapshot(global_cap=99))

    assert conn.execute("PRAGMA data_version").fetchone()[0] == versions
    assert conn.in_transaction is False
