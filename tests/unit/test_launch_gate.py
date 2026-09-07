"""The gate every launch passes, and what a refusal is forbidden to touch (issue #120).

RA-05: ``resume`` and ``restart`` reached the launch directly, so the session cap, the
dispatch pause and item/repo holds were enforced against the automatic dispatcher alone.
The cap that exists to protect one Claude subscription on one machine was not merely
inaccurate on those paths — it was absent.

Two halves are tested here and they fail differently, so they are kept apart:

* :func:`ordering.launch_holds` is pure, and its tests build a snapshot by hand rather than
  a process tree — the same separation ``test_ordering`` keeps, for the same reason.
* :func:`dispatch.check_launch_gate` reads the machine and the database, and its tests care
  about what is *written*, which for a refusal is nothing at all.

The second of those carries the requirement most likely to be lost to a later refactor. A
refusal must leave the item untouched (FR-010, FR-011): the moment one writes a
``failure_reason``, "the machine is busy" becomes "your work item is broken" and the author
needs ``retry`` before they can press the button again. That is a worse bug than the one
this feature closes, so it is asserted column by column rather than by reading the state.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from tests.conftest import seed_item, seed_session
from tests.unit.test_ordering import ready, snapshot

from robot_army import db, dispatch, ordering

HoldReason = ordering.HoldReason


# -- helpers ----------------------------------------------------------------


def item(conn, **kwargs):
    """One work item row, read back as the model the gate is handed."""
    item_id = seed_item(conn, **kwargs)
    return db.get_work_item(conn, item_id)


def holds(work_item, *, config, **snapshot_fields):
    """``launch_holds`` with everything defaulted to permissive."""
    paused = snapshot_fields.pop("paused", False)
    item_holds = snapshot_fields.pop("item_holds", None)
    repo_holds = snapshot_fields.pop("repo_holds", None)
    fields = {"global_cap": 9}
    fields.update(snapshot_fields)
    return ordering.launch_holds(
        work_item,
        config=config,
        capacity=snapshot(**fields),
        paused=paused,
        item_holds=item_holds,
        repo_holds=repo_holds,
    )



def records(layout, audit, action: str) -> list[dict]:
    """Every record of one action, read back from the log the way a person would."""
    audit.close()
    out = []
    for path in sorted(layout.log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["action"] == action:
                out.append(record)
    return out


def reasons(result):
    return [reason for reason, _detail in result]


def repo_capped(config, cap: int, *, repo_key: str = "demo"):
    section = config.repos[repo_key]
    return replace(
        config,
        repos={**config.repos, repo_key: replace(section, max_sessions=cap)},
    )


# -- launch_holds: one condition at a time ----------------------------------


def test_an_idle_unpaused_machine_holds_nothing(conn, config):
    assert holds(item(conn), config=config) == []


def test_a_pause_is_reported_and_names_the_command_that_lifts_it(conn, config):
    result = holds(item(conn), config=config, paused=True)

    assert reasons(result) == [HoldReason.PAUSED]
    # `unpause`, not `resume`: `robot-army resume <item>` is a different command entirely,
    # and naming it would send the author at something that cannot lift a pause.
    assert "robot-army unpause" in result[0][1]


def test_an_item_hold_is_reported_with_when_and_by_what(conn, config):
    work_item = item(conn)
    with db.transaction(conn):
        db.set_item_hold(conn, work_item.id, by="web")

    result = holds(work_item, config=config, item_holds=db.list_item_holds(conn))

    assert reasons(result) == [HoldReason.HELD]
    assert "web" in result[0][1]


def test_a_repository_hold_is_reported_for_an_item_that_is_not_itself_held(conn, config):
    work_item = item(conn)
    with db.transaction(conn):
        db.set_repo_hold(conn, "demo", by="cli")

    result = holds(work_item, config=config, repo_holds=db.list_repo_holds(conn))

    assert reasons(result) == [HoldReason.HELD]
    assert "demo" in result[0][1]


def test_both_holds_are_named_and_the_reader_is_told_releasing_one_is_not_enough(
    conn, config
):
    """FR-006. Collapsing to one reason produces the exact confusion the requirement
    exists to prevent: the author releases the item hold, expects it to run, and it does
    not, with the surface still saying ``held`` and appearing to have ignored them."""
    work_item = item(conn)
    with db.transaction(conn):
        db.set_item_hold(conn, work_item.id, by="web")
        db.set_repo_hold(conn, "demo", by="cli")

    result = holds(
        work_item,
        config=config,
        item_holds=db.list_item_holds(conn),
        repo_holds=db.list_repo_holds(conn),
    )

    assert reasons(result) == [HoldReason.HELD], "one reason, not two"
    detail = result[0][1]
    assert "web" in detail and "cli" in detail
    assert "releasing one leaves the other in force" in detail


def test_the_global_cap_is_reported_with_its_counts(conn, config):
    result = holds(item(conn), config=config, total=2, ours=("a", "b"), global_cap=2)

    assert reasons(result) == [HoldReason.GLOBAL_CAP]
    assert "2 of 2 sessions running" in result[0][1]


def test_a_degraded_count_says_it_is_a_ceiling_rather_than_a_fact(conn, config):
    result = holds(item(conn), config=config, total=2, global_cap=2, degraded=True)

    assert "ceiling rather than a fact" in result[0][1]


def test_a_repository_cap_binds_while_the_machine_has_room(conn, config):
    result = holds(
        item(conn),
        config=repo_capped(config, 1),
        total=1,
        global_cap=9,
        per_repo={"demo": 1},
    )

    assert reasons(result) == [HoldReason.REPO_CAP]
    assert "demo" in result[0][1]
    assert "configured" in result[0][1], "the author chose 1; they can raise it"


def test_a_repository_cap_holds_only_its_own_repository(conn, config):
    """FR-012 and FR-020's distinction, at the launch rather than in the queue: a per-item
    reason must leave every other repository free."""
    other = item(conn, repo_key="other", issue_number=7)

    result = holds(
        other, config=repo_capped(config, 1), total=1, global_cap=9, per_repo={"demo": 1}
    )

    assert result == []


def test_an_unobservable_snapshot_withholds_the_launch(conn, config):
    result = holds(
        item(conn), config=config, observable=False, reason="registry directory is absent"
    )

    assert reasons(result) == [HoldReason.CAPACITY_UNOBSERVABLE]
    assert "registry directory is absent" in result[0][1]


def test_an_unobservable_snapshot_with_no_reason_still_says_something_useful(conn, config):
    result = holds(item(conn), config=config, observable=False, reason=None)

    assert "could not be determined" in result[0][1]


# -- launch_holds: several at once, and the order they come back in ---------


def test_every_applicable_reason_is_returned_not_only_the_first(conn, config):
    """FR-023. An override records what it went past, and someone forcing past a cap needs
    to know they also forced past a pause."""
    work_item = item(conn)
    with db.transaction(conn):
        db.set_item_hold(conn, work_item.id, by="cli")

    result = holds(
        work_item,
        config=repo_capped(config, 1),
        paused=True,
        item_holds=db.list_item_holds(conn),
        total=2,
        global_cap=2,
        per_repo={"demo": 1},
    )

    assert reasons(result) == [
        HoldReason.PAUSED,
        HoldReason.HELD,
        HoldReason.GLOBAL_CAP,
        HoldReason.REPO_CAP,
    ]


def test_the_order_is_hold_reason_declaration_order(conn, config):
    """The precedence is the enum's declaration order and nothing re-decides it here. This
    is what makes FR-007 structural: the launch and the queue read one enum."""
    work_item = item(conn)
    with db.transaction(conn):
        db.set_repo_hold(conn, "demo", by="cli")

    result = holds(
        work_item,
        config=config,
        paused=True,
        repo_holds=db.list_repo_holds(conn),
        total=5,
        global_cap=2,
    )

    declared = list(HoldReason)
    assert reasons(result) == sorted(reasons(result), key=declared.index)


def test_a_pause_outranks_a_full_machine(conn, config):
    """US2 AS4. Freeing a slot would change nothing on a paused system, so naming the cap
    sends the author to fix the wrong thing."""
    result = holds(item(conn), config=config, paused=True, total=2, global_cap=2)

    assert result[0][0] is HoldReason.PAUSED


def test_a_hold_outranks_a_full_machine(conn, config):
    work_item = item(conn)
    with db.transaction(conn):
        db.set_item_hold(conn, work_item.id, by="cli")

    result = holds(
        work_item,
        config=config,
        item_holds=db.list_item_holds(conn),
        total=2,
        global_cap=2,
    )

    assert result[0][0] is HoldReason.HELD


def test_an_unobservable_snapshot_reports_no_cap_numbers_beside_it(conn, config):
    """The one place collecting everything would lie. An unobservable snapshot reports
    ``total=0``; appending the caps would quietly assert the machine was empty, and the
    override record would carry a fabricated count."""
    work_item = item(conn)
    with db.transaction(conn):
        db.set_item_hold(conn, work_item.id, by="cli")

    result = holds(
        work_item,
        config=config,
        item_holds=db.list_item_holds(conn),
        observable=False,
        reason="registry unreadable",
    )

    assert reasons(result) == [HoldReason.HELD, HoldReason.CAPACITY_UNOBSERVABLE]


def test_launch_holds_never_returns_a_queue_only_reason(conn, config):
    """The four that stay behind in ``_hold_for`` decide whether a *new* item may enter the
    queue. Applying them to a resume would refuse an interrupted session for being the
    second thing its own repository is working on."""
    queue_only = {
        HoldReason.AWAITING_MERGE,
        HoldReason.NOT_ONBOARDED,
        HoldReason.OFF_COLUMN,
        HoldReason.PREPARATION_FAILED,
    }
    work_item = item(conn)
    with db.transaction(conn):
        db.set_item_hold(conn, work_item.id, by="cli")

    result = holds(
        work_item,
        config=repo_capped(config, 1),
        paused=True,
        item_holds=db.list_item_holds(conn),
        observable=False,
    )

    assert not (set(reasons(result)) & queue_only)


def test_launch_holds_writes_nothing(conn, config):
    """``ordering`` is pure and must stay pure: the web calls into this module on every
    page render."""
    work_item = item(conn)
    before = conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item.id,)).fetchone()

    holds(work_item, config=config, paused=True, total=9, global_cap=1)

    after = conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item.id,)).fetchone()
    assert dict(after) == dict(before)


# -- the extraction changed nothing the queue reports -----------------------


def test_hold_for_still_reports_only_the_first_reason(conn, config):
    """``plan`` returns one reason per entry, as it always has. FR-007: two reasons shown
    at once is how a surface stops being read."""
    ids = ready(conn, 1)
    with db.transaction(conn):
        db.set_item_hold(conn, ids[0], by="cli")

    entries = ordering.plan(
        conn, config=config, capacity=snapshot(total=9, global_cap=1)
    )

    assert entries[0].hold is HoldReason.HELD
    assert isinstance(entries[0].detail, str)


# -- check_launch_gate: the reading half ------------------------------------


def gate(conn, work_item, *, config, audit, **kwargs):
    return dispatch.check_launch_gate(
        conn,
        audit=audit,
        config=config,
        item=work_item,
        surface=kwargs.pop("surface", "cli"),
        **kwargs,
    )


def live_session(conn, *, repo_key="demo", issue_number=90):
    """One running session, which is what the cap actually counts."""
    item_id = seed_item(conn, repo_key=repo_key, issue_number=issue_number)
    seed_session(conn, item_id, state="running")
    return item_id


def capped(config, *, machine=None, per_repo=None):
    if machine is not None:
        config = replace(config, daemon=replace(config.daemon, max_concurrent_sessions=machine))
    if per_repo is not None:
        config = replace(
            config, dispatch=replace(config.dispatch, default_repo_max_sessions=per_repo)
        )
    return config


def test_an_idle_machine_permits_the_launch_and_records_nothing(
    conn, config, audit, layout, idle_machine
):
    """A permitted launch is the ordinary case; the dispatch records that follow already
    say one happened, and a line per permission would be noise in a log whose standard is
    reconstruction."""
    registry, proc = idle_machine

    gate(
        conn,
        item(conn),
        config=capped(config, machine=2, per_repo=2),
        audit=audit,
        registry_dir=registry,
        proc_root=proc,
    )

    assert records(layout, audit, "dispatch.refused") == []
    assert records(layout, audit, "dispatch.forced") == []


def test_a_full_machine_refuses_and_records_why(conn, config, audit, layout, idle_machine):
    registry, proc = idle_machine
    live_session(conn, issue_number=90)
    live_session(conn, repo_key="other", issue_number=91)

    with pytest.raises(dispatch.DispatchRefused) as caught:
        gate(
            conn,
            item(conn),
            config=capped(config, machine=2, per_repo=9),
            audit=audit,
            registry_dir=registry,
            proc_root=proc,
        )

    assert caught.value.hold is HoldReason.GLOBAL_CAP
    assert "2 of 2 sessions running" in caught.value.detail
    written = records(layout, audit, "dispatch.refused")
    assert len(written) == 1
    assert written[0]["outcome"] == "error"
    assert written[0]["detail"]["hold"] == str(HoldReason.GLOBAL_CAP)
    assert written[0]["detail"]["surface"] == "cli"
    assert "2 of 2 sessions running" in written[0]["detail"]["reason"]


def test_a_repository_cap_refuses_while_the_machine_has_room(
    conn, config, audit, idle_machine
):
    registry, proc = idle_machine
    live_session(conn, repo_key="demo", issue_number=90)

    with pytest.raises(dispatch.DispatchRefused) as caught:
        gate(
            conn,
            item(conn),
            config=capped(config, machine=9, per_repo=1),
            audit=audit,
            registry_dir=registry,
            proc_root=proc,
        )

    assert caught.value.hold is HoldReason.REPO_CAP


def test_a_pause_refuses(conn, config, audit, idle_machine):
    registry, proc = idle_machine
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="cli")

    with pytest.raises(dispatch.DispatchRefused) as caught:
        gate(
            conn,
            item(conn),
            config=capped(config, machine=9, per_repo=9),
            audit=audit,
            registry_dir=registry,
            proc_root=proc,
        )

    assert caught.value.hold is HoldReason.PAUSED


def test_a_hold_refuses(conn, config, audit, idle_machine):
    registry, proc = idle_machine
    work_item = item(conn)
    with db.transaction(conn):
        db.set_item_hold(conn, work_item.id, by="web")

    with pytest.raises(dispatch.DispatchRefused) as caught:
        gate(
            conn,
            work_item,
            config=capped(config, machine=9, per_repo=9),
            audit=audit,
            registry_dir=registry,
            proc_root=proc,
        )

    assert caught.value.hold is HoldReason.HELD


def test_an_unobservable_machine_refuses_rather_than_assuming_it_is_idle(
    conn, config, audit, tmp_path
):
    """FR-004 and capacity's R4. Every unresolved doubt resolves upward: an under-count is
    the only capacity error that causes harm."""
    missing = tmp_path / "no-such-registry"

    with pytest.raises(dispatch.DispatchRefused) as caught:
        gate(
            conn,
            item(conn),
            config=capped(config, machine=9, per_repo=9),
            audit=audit,
            registry_dir=missing,
            proc_root=tmp_path / "no-such-proc",
        )

    assert caught.value.hold is HoldReason.CAPACITY_UNOBSERVABLE


# -- FR-010, FR-011: what a refusal is forbidden to touch -------------------


def test_a_refusal_writes_nothing_to_the_item(conn, config, audit, idle_machine):
    """The requirement most likely to be lost to a later refactor, so it is asserted
    column by column rather than by reading the state.

    An item failed for the machine being busy would need ``robot-army retry`` before the
    author could press the button again — "wait a minute" turned into "your work item is
    broken", which is a worse bug than the one this feature closes."""
    registry, proc = idle_machine
    work_item = item(conn)
    live_session(conn, issue_number=90)
    before = dict(
        conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item.id,)).fetchone()
    )

    with pytest.raises(dispatch.DispatchRefused):
        gate(
            conn,
            work_item,
            config=capped(config, machine=1, per_repo=9),
            audit=audit,
            registry_dir=registry,
            proc_root=proc,
        )

    after = dict(
        conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item.id,)).fetchone()
    )
    assert after == before
    assert after["failure_reason"] is None
    assert after["blocked_reason"] is None
    assert after["dispatching_at"] is None


def test_a_refusal_emits_no_state_transition(conn, config, audit, layout, idle_machine):
    """The other half of the same requirement, read from the log rather than the row: no
    ``state.work_item`` record means nothing moved, even momentarily."""
    registry, proc = idle_machine
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="cli")

    with pytest.raises(dispatch.DispatchRefused):
        gate(
            conn,
            item(conn),
            config=capped(config, machine=9, per_repo=9),
            audit=audit,
            registry_dir=registry,
            proc_root=proc,
        )

    assert records(layout, audit, "state.work_item") == []


# -- FR-009: the snapshot is taken here, every time -------------------------


def test_the_gate_observes_the_machine_on_every_call(
    conn, config, audit, idle_machine, monkeypatch
):
    """No snapshot may be passed in and none is cached. Between a planner's observation and
    this launch the author can start a session by hand, and a remembered number cannot see
    it — which is the whole reason ``capacity`` counts the machine rather than our own
    bookkeeping."""
    registry, proc = idle_machine
    calls = []
    real = dispatch.capacity.snapshot
    monkeypatch.setattr(
        dispatch.capacity,
        "snapshot",
        lambda *a, **kw: (calls.append(1), real(*a, **kw))[1],
    )
    work_item = item(conn)
    kwargs = {
        "config": capped(config, machine=9, per_repo=9),
        "audit": audit,
        "registry_dir": registry,
        "proc_root": proc,
    }

    gate(conn, work_item, **kwargs)
    gate(conn, work_item, **kwargs)

    assert len(calls) == 2, "observed again rather than remembered"


# -- US2: which reason a gate refusal names when several apply -------------


def test_the_gate_names_the_pause_ahead_of_a_full_machine(
    conn, config, audit, idle_machine
):
    """US2 AS5 at the gate rather than in the plan. Reporting the cap here would send the
    author to free a slot, which changes nothing at all while the system is paused."""
    registry, proc = idle_machine
    live_session(conn, issue_number=90)
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="cli")

    with pytest.raises(dispatch.DispatchRefused) as caught:
        gate(
            conn,
            item(conn),
            config=capped(config, machine=1, per_repo=9),
            audit=audit,
            registry_dir=registry,
            proc_root=proc,
        )

    assert caught.value.hold is HoldReason.PAUSED


def test_the_gate_names_a_hold_ahead_of_a_full_machine(conn, config, audit, idle_machine):
    """Issue #117's ranking, inherited. Every reason below ``held`` names a fix that cannot
    work while the author is holding the item."""
    registry, proc = idle_machine
    live_session(conn, issue_number=90)
    work_item = item(conn)
    with db.transaction(conn):
        db.set_item_hold(conn, work_item.id, by="cli")

    with pytest.raises(dispatch.DispatchRefused) as caught:
        gate(
            conn,
            work_item,
            config=capped(config, machine=1, per_repo=9),
            audit=audit,
            registry_dir=registry,
            proc_root=proc,
        )

    assert caught.value.hold is HoldReason.HELD


def test_the_gate_names_both_holds_when_both_apply(conn, config, audit, idle_machine):
    registry, proc = idle_machine
    work_item = item(conn)
    with db.transaction(conn):
        db.set_item_hold(conn, work_item.id, by="web")
        db.set_repo_hold(conn, "demo", by="cli")

    with pytest.raises(dispatch.DispatchRefused) as caught:
        gate(
            conn,
            work_item,
            config=capped(config, machine=9, per_repo=9),
            audit=audit,
            registry_dir=registry,
            proc_root=proc,
        )

    assert "web" in caught.value.detail and "cli" in caught.value.detail
    assert "releasing one leaves the other in force" in caught.value.detail


# -- US4: the override, and the line it must not cross ---------------------


@pytest.mark.parametrize(
    ("arrange", "config_for", "expected"),
    [
        pytest.param(
            lambda conn, work_item: db.set_dispatch_paused(conn, paused=True, by="cli"),
            lambda config: capped(config, machine=9, per_repo=9),
            HoldReason.PAUSED,
            id="paused",
        ),
        pytest.param(
            lambda conn, work_item: db.set_item_hold(conn, work_item.id, by="cli"),
            lambda config: capped(config, machine=9, per_repo=9),
            HoldReason.HELD,
            id="held",
        ),
        pytest.param(
            lambda conn, work_item: db.set_repo_hold(conn, "demo", by="cli"),
            lambda config: capped(config, machine=9, per_repo=9),
            HoldReason.HELD,
            id="repository held",
        ),
    ],
)
def test_force_proceeds_past_each_policy_condition(
    conn, config, audit, idle_machine, arrange, config_for, expected
):
    registry, proc = idle_machine
    work_item = item(conn)
    with db.transaction(conn):
        arrange(conn, work_item)

    gate(
        conn,
        work_item,
        config=config_for(config),
        audit=audit,
        force=True,
        registry_dir=registry,
        proc_root=proc,
    )  # does not raise


def test_force_proceeds_past_a_full_machine(conn, config, audit, idle_machine):
    registry, proc = idle_machine
    live_session(conn, issue_number=90)

    gate(
        conn,
        item(conn),
        config=capped(config, machine=1, per_repo=9),
        audit=audit,
        force=True,
        registry_dir=registry,
        proc_root=proc,
    )


def test_force_proceeds_past_an_unobservable_machine(conn, config, audit, tmp_path):
    gate(
        conn,
        item(conn),
        config=capped(config, machine=9, per_repo=9),
        audit=audit,
        force=True,
        registry_dir=tmp_path / "absent",
        proc_root=tmp_path / "absent-proc",
    )


def test_an_override_records_every_condition_not_only_the_first(
    conn, config, audit, layout, idle_machine
):
    """FR-023 and SC-008. The author who forced past a full machine needs to know they also
    forced past a hold they had forgotten placing — reporting one and silently passing the
    rest is how an escape hatch becomes its own surprise."""
    registry, proc = idle_machine
    live_session(conn, issue_number=90)
    work_item = item(conn)
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="cli")
        db.set_item_hold(conn, work_item.id, by="web")

    gate(
        conn,
        work_item,
        config=capped(config, machine=1, per_repo=9),
        audit=audit,
        force=True,
        registry_dir=registry,
        proc_root=proc,
    )

    written = records(layout, audit, "dispatch.forced")
    assert len(written) == 1
    assert written[0]["outcome"] == "ok"
    overridden = [entry["hold"] for entry in written[0]["detail"]["overridden"]]
    # Four, in declaration order. The repository's own limit is reached by the same live
    # session that fills the machine, and it is named too — which is the requirement: the
    # author is told everything they went past, not the first thing.
    assert overridden == [
        str(HoldReason.PAUSED),
        str(HoldReason.HELD),
        str(HoldReason.GLOBAL_CAP),
        str(HoldReason.REPO_CAP),
    ]
    assert all(entry["detail"] for entry in written[0]["detail"]["overridden"]), (
        "each one carries its specifics, not just its name"
    )
    assert records(layout, audit, "dispatch.refused") == [], "forced, not refused"


def test_force_on_an_unblocked_launch_records_nothing(
    conn, config, audit, layout, idle_machine
):
    """``--force`` on a machine with nothing to override is not an event. A record here
    would put a line in the log every time the author typed the flag out of habit."""
    registry, proc = idle_machine

    gate(
        conn,
        item(conn),
        config=capped(config, machine=9, per_repo=9),
        audit=audit,
        force=True,
        registry_dir=registry,
        proc_root=proc,
    )

    assert records(layout, audit, "dispatch.forced") == []


def test_the_gate_is_the_only_thing_force_reaches(conn, config, audit, idle_machine):
    """FR-024 and FR-025, asserted as a property of the function rather than of a launch.

    ``check_launch_gate`` evaluates exactly the five policy conditions and nothing else, so
    there is no branch inside it through which ``force`` could reach the author check,
    workspace trust, the settings fingerprint, or the claim. The launch-level proof that it
    does not is in ``test_dispatch_capacity``.
    """
    registry, proc = idle_machine
    work_item = item(conn, author="somebody-else", issue_number=5)
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="cli")

    # The gate permits it — the author check is not its business and is applied after it.
    gate(
        conn,
        work_item,
        config=capped(config, machine=9, per_repo=9),
        audit=audit,
        force=True,
        registry_dir=registry,
        proc_root=proc,
    )


# -- the gate measures against the enforced cap (issue #30) -----------------


def test_the_gate_defers_to_the_daemons_cap_rather_than_this_processs(
    conn, config, audit, layout, idle_machine
):
    """A refusal is a surface too, and it was the one still showing the stale number.

    Issue #30's own scenario, one press further on: the cap is raised 5→7, the daemon is
    restarted and `serve` is not. The header now correctly reads `6/7` and offers *Resume*
    — and without this the press came back "6 of 5 sessions running", the very number the
    header no longer shows, from a process that is not the one enforcing anything.
    """
    registry, proc = idle_machine
    live_session(conn, issue_number=90)
    live_session(conn, repo_key="other", issue_number=91)

    gate(
        conn,
        item(conn),
        config=capped(config, machine=2, per_repo=9),
        audit=audit,
        enforced_cap=4,
        registry_dir=registry,
        proc_root=proc,
    )

    assert records(layout, audit, "dispatch.refused") == [], (
        "the daemon allows four; this process's own two must not refuse the launch"
    )


def test_the_gate_tightens_to_the_daemons_cap_when_this_process_is_the_generous_one(
    conn, config, audit, layout, idle_machine
):
    """The other direction, and the one that matters for the machine rather than the page.

    A process holding a higher cap than the daemon would otherwise launch past the limit
    the daemon is enforcing — an over-dispatch, which is the harmful direction: it
    oversubscribes the one subscription the cap exists to protect.
    """
    registry, proc = idle_machine
    live_session(conn, issue_number=90)
    live_session(conn, repo_key="other", issue_number=91)

    with pytest.raises(dispatch.DispatchRefused) as caught:
        gate(
            conn,
            item(conn),
            config=capped(config, machine=9, per_repo=9),
            audit=audit,
            enforced_cap=2,
            registry_dir=registry,
            proc_root=proc,
        )

    assert caught.value.hold is HoldReason.GLOBAL_CAP
    assert "2 of 2 sessions running" in caught.value.detail


def test_the_gate_uses_this_processs_cap_when_no_enforced_one_is_given(
    conn, config, audit, layout, idle_machine
):
    """The daemon's own call, unchanged: it is the authority and consults nothing."""
    registry, proc = idle_machine
    live_session(conn, issue_number=90)
    live_session(conn, repo_key="other", issue_number=91)

    with pytest.raises(dispatch.DispatchRefused) as caught:
        gate(
            conn,
            item(conn),
            config=capped(config, machine=2, per_repo=9),
            audit=audit,
            registry_dir=registry,
            proc_root=proc,
        )

    assert "2 of 2 sessions running" in caught.value.detail
