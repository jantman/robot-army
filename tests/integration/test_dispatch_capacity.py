"""Dispatch under a cap that counts the whole machine (T021, T022, T030, T036).

Milestone 001's cap counted the daemon's own bookkeeping. That was right when the daemon
was the only actor being modelled and wrong the moment the author's own Claude sessions
share the subscription — which on this machine they always do. So the fixture here is not
a database, it is a *registry*: out-of-band entries the system did not create and must not
touch, and must nevertheless count.

The case that matters most is the last one in the first group: a dispatch in flight, with
no registry file written yet, still occupying its slot. A registry-only count would offer
that same slot to a second dispatch in the same tick, and FR-009's guarantee would fail in
exactly the case it exists for.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from tests.conftest import (
    RecordingWriter,
    StubSessionHost,
    make_boundaries,
    seed_item,
    seed_session,
    write_proc,
    write_registry,
)

from robot_army import capacity, db, dispatch, operations, ordering
from robot_army.boundaries.hooks import SubprocessHookRunner
from robot_army.states import SessionState, WorkItemState

pytestmark = pytest.mark.requires_git


@pytest.fixture
def machine(tmp_path: Path):
    """A registry that exists and holds nothing, and a /proc that demonstrably enumerates."""
    registry = tmp_path / "registry"
    registry.mkdir()
    proc = tmp_path / "proc"
    write_proc(proc, 1, starttime="1", exe="/usr/lib/systemd/systemd")
    return registry, proc


def out_of_band(registry: Path, proc: Path, *, pid: int, cwd: str) -> Path:
    """One of the author's own sessions: alive, real, and none of our business.

    Its working directory is deliberately *not* under the worktree root, which is exactly
    how ``sessions.under_root`` tells the author's work from the system's — milestone 001's
    rule, reused rather than reinvented.
    """
    write_registry(registry, pid=pid, session_id=f"human-{pid}", proc_start=str(pid), cwd=cwd)
    write_proc(proc, pid, starttime=str(pid), cwd=cwd)
    return registry / f"{pid}.json"


def trust_file(tmp_path: Path, clone: Path) -> Path:
    path = tmp_path / "claude.json"
    path.write_text(
        json.dumps({"projects": {str(clone.resolve()): {"hasTrustDialogAccepted": True}}}),
        encoding="utf-8",
    )
    return path


def ready_item(conn, config=None, **kwargs) -> int:
    # The recorded clone location is part of an approval since milestone 005, and
    # ``check_gates`` re-verifies it before creating anything (FR-028). ``config`` is
    # optional only because several cases here never dispatch the row they seed.
    if config is not None:
        section = config.repos.get(kwargs.get("repo_key", "demo"))
        if section is not None:
            kwargs.setdefault("clone_path", section.path)
    return seed_item(conn, state=str(WorkItemState.READY), **kwargs)


def run(conn, audit, config, layout, tmp_path, machine, **overrides) -> int:
    registry, proc = machine
    boundaries = overrides.pop(
        "boundaries",
        make_boundaries(
            audit,
            writer=RecordingWriter(),
            host=StubSessionHost(confirm=True),
            hooks=SubprocessHookRunner(audit),
        ),
    )
    return dispatch.select_and_dispatch(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
        registry_dir=registry,
        proc_root=proc,
        **overrides,
    )


def capped_at(config, n: int, *, per_repo: int | None = None):
    """Set the global cap, and optionally lift the per-repository one out of the way.

    The default per-repository cap is 1, so a test about the *global* cap that seeded two
    items in one repository would be held by the wrong limit and prove nothing about the
    limit it names.
    """
    config = replace(config, daemon=replace(config.daemon, max_concurrent_sessions=n))
    if per_repo is not None:
        config = replace(
            config, dispatch=replace(config.dispatch, default_repo_max_sessions=per_repo)
        )
    return config


# -- the author's own sessions occupy the quota (US1) -----------------------


def test_nothing_dispatches_when_the_author_has_filled_the_machine(
    conn, audit, config, layout, tmp_path, machine
):
    """The milestone's one actively bad behaviour, fixed: a cap that protected nothing on
    the machine where the author actually works."""
    registry, proc = machine
    config = capped_at(config, 2)
    out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    out_of_band(registry, proc, pid=102, cwd=str(tmp_path / "GIT" / "two"))
    item = ready_item(conn, config, issue_number=1)

    assert run(conn, audit, config, layout, tmp_path, machine) == 0
    assert db.get_work_item(conn, item).state is WorkItemState.READY


def test_one_dispatch_follows_one_of_the_authors_sessions_ending(
    conn, audit, config, layout, tmp_path, machine
):
    """Nothing polls for the change and nothing is remembered about the hold: the next
    pass simply looks again, which is why an interrupted dispatcher is harmless."""
    registry, proc = machine
    config = capped_at(config, 2)
    first = out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    out_of_band(registry, proc, pid=102, cwd=str(tmp_path / "GIT" / "two"))
    item = ready_item(conn, config, issue_number=1)
    assert run(conn, audit, config, layout, tmp_path, machine) == 0

    first.unlink()  # the author closed one

    assert run(conn, audit, config, layout, tmp_path, machine) == 1
    assert db.get_work_item(conn, item).state is WorkItemState.ACTIVE


def test_the_hold_reason_names_the_split_so_the_author_knows_what_to_close(
    conn, config, tmp_path, machine
):
    registry, proc = machine
    config = capped_at(config, 1)
    out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    ready_item(conn, config, issue_number=1)

    snap = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    entries = ordering.plan(conn, config=config, capacity=snap)
    assert entries[0].hold is ordering.HoldReason.GLOBAL_CAP
    assert "1 other" in entries[0].detail


# -- a batch cannot collectively exceed the cap (FR-009) -------------------


def test_two_items_in_one_pass_never_exceed_the_cap(
    conn, audit, config, layout, tmp_path, machine
):
    """Capacity is re-observed before each individual dispatch. Subtracting one from a
    remembered number is not observing, and it is how a batch quietly oversubscribes."""
    config = capped_at(config, 1)
    first = ready_item(conn, config, issue_number=1)
    second = ready_item(conn, config, issue_number=2)

    assert run(conn, audit, config, layout, tmp_path, machine) == 1
    assert db.get_work_item(conn, first).state is WorkItemState.ACTIVE
    assert db.get_work_item(conn, second).state is WorkItemState.READY


def test_a_pass_fills_exactly_the_room_available_and_no_more(
    conn, audit, config, layout, tmp_path, machine
):
    registry, proc = machine
    config = capped_at(config, 3, per_repo=3)
    out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    items = [ready_item(conn, config, issue_number=n) for n in (1, 2, 3)]

    assert run(conn, audit, config, layout, tmp_path, machine) == 2
    states = [db.get_work_item(conn, i).state for i in items]
    assert states == [WorkItemState.ACTIVE, WorkItemState.ACTIVE, WorkItemState.READY]


def test_a_dispatch_in_flight_with_no_registry_file_yet_still_occupies_its_slot(
    conn, audit, config, layout, tmp_path, machine
):
    """The sharpest correctness trap in the milestone (R3).

    Between the host returning and the worker writing ``~/.claude/sessions/<pid>.json``, a
    dispatch in flight is invisible to the registry. The union by session id is what closes
    the window — and the join key exists on both sides from the beginning because the
    orchestrator generates the session id *before* the process starts.
    """
    config = capped_at(config, 1)
    launching = seed_item(conn, issue_number=1)
    seed_session(
        conn, launching, state=str(SessionState.STARTING), session_id="s-in-flight"
    )
    waiting = ready_item(conn, config, issue_number=2)

    registry, proc = machine
    snap = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert snap.total == 1, "the launch window must not read as free capacity"

    assert run(conn, audit, config, layout, tmp_path, machine) == 0
    assert db.get_work_item(conn, waiting).state is WorkItemState.READY


# -- FR-008: running work is left entirely alone (T022) --------------------


def test_exceeding_the_cap_withholds_new_dispatch_and_touches_nothing_running(
    conn, audit, config, layout, tmp_path, machine
):
    """A cap lowered under running work — or an author who opened three of their own — is
    an over-subscription that resolves itself as sessions end. Reclaiming it by stopping
    something would destroy work in progress to satisfy a number."""
    registry, proc = machine
    config = capped_at(config, 1)
    for pid in (101, 102, 103):
        out_of_band(registry, proc, pid=pid, cwd=str(tmp_path / "GIT" / str(pid)))

    running_item = seed_item(conn, issue_number=1, state=str(WorkItemState.ACTIVE))
    running_row = seed_session(
        conn, running_item, state=str(SessionState.RUNNING), session_id="s-running"
    )
    waiting = ready_item(conn, config, issue_number=2)

    host = StubSessionHost(confirm=True)
    boundaries = make_boundaries(
        audit, writer=RecordingWriter(), host=host, hooks=SubprocessHookRunner(audit)
    )
    assert run(conn, audit, config, layout, tmp_path, machine, boundaries=boundaries) == 0

    assert host.terminated == [], "FR-008 forbids reclaiming capacity from running work"
    assert db.get_work_item(conn, running_item).state is WorkItemState.ACTIVE
    assert db.get_session_by_row_id(conn, running_row).state is SessionState.RUNNING
    assert db.get_work_item(conn, waiting).state is WorkItemState.READY


def test_no_out_of_band_session_is_ever_signalled(
    conn, audit, config, layout, tmp_path, machine
):
    """FR-006, at the level of behaviour rather than of type: with the machine full of the
    author's own sessions, a whole dispatch pass touches none of them."""
    registry, proc = machine
    config = capped_at(config, 1)
    out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    ready_item(conn, config, issue_number=1)

    host = StubSessionHost(confirm=True)
    boundaries = make_boundaries(
        audit, writer=RecordingWriter(), host=host, hooks=SubprocessHookRunner(audit)
    )
    run(conn, audit, config, layout, tmp_path, machine, boundaries=boundaries)

    assert host.terminated == []
    assert (registry / "101.json").exists(), "the author's registry entry was disturbed"


# -- the hold record is summarised, not repeated (R16) ---------------------


def records(layout, audit, action: str) -> list[dict]:
    audit.close()
    out = []
    for path in sorted(layout.log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["action"] == action:
                out.append(record)
    return out


def test_an_unchanging_hold_is_recorded_once_rather_than_once_per_pass(
    conn, audit, config, layout, tmp_path, machine
):
    """At a five-second tick, one record per pass is 17,280 identical records a day. That
    does not make the log more reconstructible; it buries the records that carry
    information under records that carry none."""
    registry, proc = machine
    config = capped_at(config, 1)
    out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    ready_item(conn, config, issue_number=1)

    for _ in range(5):
        run(conn, audit, config, layout, tmp_path, machine)

    held = records(layout, audit, "dispatch.at_capacity")
    assert len(held) == 1, held
    assert held[0]["detail"]["others"] == 1
    assert held[0]["detail"]["cap"] == 1


def test_a_hold_that_ends_is_recorded_with_its_duration_and_extent(
    conn, audit, config, layout, tmp_path, machine
):
    registry, proc = machine
    config = capped_at(config, 1)
    entry = out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    ready_item(conn, config, issue_number=1)

    for _ in range(3):
        run(conn, audit, config, layout, tmp_path, machine)
    entry.unlink()
    run(conn, audit, config, layout, tmp_path, machine)

    ended = records(layout, audit, "dispatch.hold_ended")
    assert len(ended) == 1, ended
    assert ended[0]["detail"]["passes_spanned"] == 3
    assert ended[0]["detail"]["duration_seconds"] is not None
    assert ended[0]["detail"]["reason"] == "global_cap"


def test_a_changed_hold_signature_is_recorded_again(
    conn, audit, config, layout, tmp_path, machine
):
    """The boundaries are where the information is. A hold whose counts changed is news;
    one whose counts did not is the same sentence repeated."""
    registry, proc = machine
    config = capped_at(config, 2)
    out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    out_of_band(registry, proc, pid=102, cwd=str(tmp_path / "GIT" / "two"))
    ready_item(conn, config, issue_number=1)
    run(conn, audit, config, layout, tmp_path, machine)

    out_of_band(registry, proc, pid=103, cwd=str(tmp_path / "GIT" / "three"))
    run(conn, audit, config, layout, tmp_path, machine)

    held = records(layout, audit, "dispatch.at_capacity")
    assert [r["detail"]["live_sessions"] for r in held] == [2, 3]


# -- the wait-for-merge gate, end to end (milestone 047) --------------------


def waiting_for_merge(config, repo_key: str = "demo"):
    section = replace(config.repos[repo_key], wait_for_merge=True)
    return replace(config, repos={**config.repos, repo_key: section})


def test_a_repository_waiting_for_a_merge_dispatches_nothing(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """The feature the issue asks for, through the real dispatcher rather than through
    ``plan`` alone: an unfinished item stops the next one starting."""
    config = waiting_for_merge(capped_at(two_repos, 4))
    seed_item(
        conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW)
    )
    queued = ready_item(conn, config, repo_key="demo", issue_number=1)

    assert run_two(conn, audit, config, layout, tmp_path, machine) == 0
    assert db.get_work_item(conn, queued).state is WorkItemState.READY


def test_a_waiting_repository_does_not_stall_the_others(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """FR-007, and the reason ``awaiting_merge`` is deliberately absent from
    ``_GLOBAL_HOLDS``: a ``break`` here would let one repository's wait stop every other
    repository's work in the same pass."""
    config = waiting_for_merge(capped_at(two_repos, 4))
    seed_item(
        conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW)
    )
    held = ready_item(conn, config, repo_key="demo", issue_number=1)
    elsewhere = ready_item(conn, config, repo_key="other", issue_number=3)

    assert run_two(conn, audit, config, layout, tmp_path, machine) == 1
    assert db.get_work_item(conn, held).state is WorkItemState.READY
    assert db.get_work_item(conn, elsewhere).state is WorkItemState.ACTIVE


def test_the_wait_lifts_when_the_unfinished_item_finishes(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """FR-008. Merging the pull request closes the issue, a closed issue becomes ``done``,
    and ``done`` is what opens the gate — no new machinery on the release path."""
    config = waiting_for_merge(capped_at(two_repos, 4))
    blocker = seed_item(
        conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW)
    )
    queued = ready_item(conn, config, repo_key="demo", issue_number=1)
    assert run_two(conn, audit, config, layout, tmp_path, machine) == 0

    conn.execute(
        "UPDATE work_items SET state = ? WHERE id = ?", (str(WorkItemState.DONE), blocker)
    )
    conn.commit()

    assert run_two(conn, audit, config, layout, tmp_path, machine) == 1
    assert db.get_work_item(conn, queued).state is WorkItemState.ACTIVE


# -- per-item holds are recorded too (milestone 047, FR-015) ----------------


def test_a_pass_stopped_by_a_per_item_hold_is_recorded(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """Before 047 only the three global holds were recorded, so a queue stopped by a
    per-repository condition left no trace at all — ``repo_cap`` included."""
    config = waiting_for_merge(capped_at(two_repos, 4))
    seed_item(
        conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW)
    )
    ready_item(conn, config, repo_key="demo", issue_number=1)

    run_two(conn, audit, config, layout, tmp_path, machine)

    held = records(layout, audit, "dispatch.at_capacity")
    assert len(held) == 1, held
    assert held[0]["detail"]["reason"] == "awaiting_merge"
    assert held[0]["detail"]["repo_key"] == "demo"
    assert "#41" in held[0]["detail"]["detail"]


def test_an_unchanging_per_item_hold_is_recorded_once(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """The de-duplication that makes recording per-item holds affordable at a five-second
    tick, and the Principle III gap plan.md enumerates: a hold is recorded when it starts
    and when it ends, not once per pass for as long as it lasts."""
    config = waiting_for_merge(capped_at(two_repos, 4))
    seed_item(
        conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW)
    )
    ready_item(conn, config, repo_key="demo", issue_number=1)

    for _ in range(5):
        run_two(conn, audit, config, layout, tmp_path, machine)

    assert len(records(layout, audit, "dispatch.at_capacity")) == 1


def test_a_per_item_hold_that_lifts_is_recorded_as_ended(
    conn, audit, two_repos, layout, tmp_path, machine
):
    config = waiting_for_merge(capped_at(two_repos, 4))
    blocker = seed_item(
        conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW)
    )
    ready_item(conn, config, repo_key="demo", issue_number=1)
    for _ in range(3):
        run_two(conn, audit, config, layout, tmp_path, machine)

    conn.execute(
        "UPDATE work_items SET state = ? WHERE id = ?",
        (str(WorkItemState.ABANDONED), blocker),
    )
    conn.commit()
    run_two(conn, audit, config, layout, tmp_path, machine)

    ended = records(layout, audit, "dispatch.hold_ended")
    assert len(ended) == 1, ended
    assert ended[0]["detail"]["reason"] == "awaiting_merge"
    assert ended[0]["detail"]["passes_spanned"] == 3


def test_a_hold_whose_reason_changes_is_recorded_again(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """The signature carries the reason since 047. A repository whose ``repo_cap`` hold
    gives way to an ``awaiting_merge`` hold has changed what the author must do about it,
    and the machine-wide counts alone would not have noticed."""
    config = waiting_for_merge(capped_at(two_repos, 4))
    running = seed_item(
        conn, repo_key="demo", issue_number=41, state=str(WorkItemState.ACTIVE)
    )
    row = seed_session(conn, running, state=str(SessionState.RUNNING), session_id="s-demo")
    ready_item(conn, config, repo_key="demo", issue_number=1)
    run_two(conn, audit, config, layout, tmp_path, machine)

    # The session ends; the item is still unfinished. The cap frees and the gate takes over.
    conn.execute(
        "UPDATE sessions SET state = ? WHERE id = ?", (str(SessionState.EXITED_CLEAN), row)
    )
    conn.execute(
        "UPDATE work_items SET state = ? WHERE id = ?",
        (str(WorkItemState.AWAITING_REVIEW), running),
    )
    conn.commit()
    run_two(conn, audit, config, layout, tmp_path, machine)

    held = records(layout, audit, "dispatch.at_capacity")
    assert [r["detail"]["reason"] for r in held] == ["repo_cap", "awaiting_merge"]
    # And the first one is *closed* rather than left open for the reader to infer its
    # ending from the next opening.
    ended = records(layout, audit, "dispatch.hold_ended")
    assert [r["detail"]["reason"] for r in ended] == ["repo_cap"]


def test_a_dispatch_elsewhere_does_not_end_a_waiting_repositorys_hold(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """"A dispatch happened" stopped being proof that the recorded hold ended.

    It was proof while only global holds were recorded: a pass carrying one returned before
    dispatching anything, so the two facts were the same fact. A per-repository hold breaks
    that — ``demo`` stays held while ``other`` dispatches, which is the entire point of the
    hold being per-item — and clearing on that evidence would write a ``hold_ended`` for a
    repository that is still waiting, blame it on an unrelated item, and restart the
    duration from zero on the next quiet pass.
    """
    config = waiting_for_merge(capped_at(two_repos, 4))
    seed_item(
        conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW)
    )
    ready_item(conn, config, repo_key="demo", issue_number=1)
    run_two(conn, audit, config, layout, tmp_path, machine)

    ready_item(conn, config, repo_key="other", issue_number=3)
    assert run_two(conn, audit, config, layout, tmp_path, machine) == 1

    assert records(layout, audit, "dispatch.hold_ended") == []


def test_the_hold_survives_an_unrelated_dispatch_and_ends_on_its_own_terms(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """The whole arc, in the order it happens on a real machine.

    A quiet pass opens ``demo``'s hold. A later pass dispatches ``other`` — which must not
    touch it. Only when ``demo``'s own wait is over is the ending written, once, and
    attributed to ``demo``'s item rather than to whatever happened to move last.
    """
    config = waiting_for_merge(capped_at(two_repos, 4))
    blocker = seed_item(
        conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW)
    )
    queued = ready_item(conn, config, repo_key="demo", issue_number=1)
    run_two(conn, audit, config, layout, tmp_path, machine)
    assert len(records(layout, audit, "dispatch.at_capacity")) == 1

    ready_item(conn, config, repo_key="other", issue_number=3)
    assert run_two(conn, audit, config, layout, tmp_path, machine) == 1
    assert records(layout, audit, "dispatch.hold_ended") == []

    conn.execute(
        "UPDATE work_items SET state = ? WHERE id = ?", (str(WorkItemState.DONE), blocker)
    )
    conn.commit()
    assert run_two(conn, audit, config, layout, tmp_path, machine) == 1

    ended = records(layout, audit, "dispatch.hold_ended")
    assert len(ended) == 1, ended
    assert ended[0]["detail"]["reason"] == "awaiting_merge"
    assert str(queued) in ended[0]["detail"]["freed_by"]


def test_a_global_hold_does_not_hand_over_when_the_queue_head_moves(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """A global hold is a fact about the *machine*, so it carries no repository.

    The entry that reports it is merely whichever item happened to be at the head, and the
    head shifts whenever an item is abandoned or the order changes. If identity keyed on
    that item's repository, one uninterrupted "the machine is full" would be reported as a
    succession of short holds handing over to each other — each with its duration restarted
    and a ``freed_by`` naming a repository that freed nothing.
    """
    registry, proc = machine
    config = capped_at(two_repos, 1)
    out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    first = ready_item(conn, config, repo_key="demo", issue_number=1)
    ready_item(conn, config, repo_key="other", issue_number=2)
    run_two(conn, audit, config, layout, tmp_path, machine)

    conn.execute(
        "UPDATE work_items SET state = ? WHERE id = ?",
        (str(WorkItemState.ABANDONED), first),
    )
    conn.commit()
    run_two(conn, audit, config, layout, tmp_path, machine)

    assert records(layout, audit, "dispatch.hold_ended") == []
    # The machine never stopped being full, so the condition never ended — even though the
    # queue head moved to a different repository between the two passes.
    assert {r["detail"]["reason"] for r in records(layout, audit, "dispatch.at_capacity")} == {
        "global_cap"
    }


def test_a_global_hold_giving_way_to_a_per_repository_one_is_bracketed(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """The other side of the same rule: when the condition genuinely *does* change, the
    ending is written rather than left to be inferred from the next opening."""
    registry, proc = machine
    config = waiting_for_merge(capped_at(two_repos, 1))
    entry = out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    seed_item(
        conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW)
    )
    ready_item(conn, config, repo_key="demo", issue_number=1)
    run_two(conn, audit, config, layout, tmp_path, machine)

    entry.unlink()
    run_two(conn, audit, config, layout, tmp_path, machine)

    assert [r["detail"]["reason"] for r in records(layout, audit, "dispatch.at_capacity")] == [
        "global_cap",
        "awaiting_merge",
    ]
    ended = records(layout, audit, "dispatch.hold_ended")
    assert [r["detail"]["reason"] for r in ended] == ["global_cap"]
    assert ended[0]["detail"]["freed_by"] == "awaiting_merge in demo took over"


def test_a_pass_that_dispatched_something_is_not_recorded_as_held(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """A pass that started work and then ran out of candidates has not stalled. Calling
    that a hold would put a record in the log every time the machine filled up normally."""
    config = waiting_for_merge(capped_at(two_repos, 4))
    seed_item(
        conn, repo_key="demo", issue_number=41, state=str(WorkItemState.AWAITING_REVIEW)
    )
    ready_item(conn, config, repo_key="demo", issue_number=1)
    ready_item(conn, config, repo_key="other", issue_number=3)

    assert run_two(conn, audit, config, layout, tmp_path, machine) == 1
    assert records(layout, audit, "dispatch.at_capacity") == []


# -- SC-006: the queue's "next" is the dispatcher's "next" (T036) ----------


def test_the_item_at_position_one_is_the_item_the_next_dispatch_selects(
    conn, audit, config, layout, tmp_path, machine
):
    """Checked a hundred times with nothing changing in between, because an order that is
    merely usually the same is not an order. It holds structurally rather than by
    coincidence: the queue and the dispatcher are one function."""
    registry, proc = machine
    config = capped_at(config, 2)
    for n in (1, 2, 3):
        ready_item(conn, config, issue_number=n)

    def head() -> int:
        snap = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
        return ordering.plan(conn, config=config, capacity=snap)[0].item.id

    expected = head()
    for _ in range(100):
        assert head() == expected

    assert run(conn, audit, config, layout, tmp_path, machine) >= 1
    assert db.get_work_item(conn, expected).state is WorkItemState.ACTIVE


# -- a repository blocks its own work and nothing else (US2, T030) ---------


@pytest.fixture
def two_repos(config, layout, tmp_path):
    """A config naming two real repositories, so "a busy repository must not stall the
    queue" is testable at all — with one repository it is not a statement about anything."""
    from tests.conftest import config_dict, make_repo, monkey_token

    from robot_army.config import parse

    second = make_repo(tmp_path / "clones" / "other")
    raw = config_dict(config.repos["demo"].path, layout, tmp_path / "worktrees")
    raw["repos"] = {
        "demo": {"path": str(config.repos["demo"].path), "base_branch": "main"},
        "other": {"path": str(second), "base_branch": "main"},
    }
    monkey_token()
    return parse(raw, tmp_path / "config.toml")


def trust_both(tmp_path: Path, config) -> Path:
    path = tmp_path / "claude.json"
    path.write_text(
        json.dumps(
            {
                "projects": {
                    str(repo.path.resolve()): {"hasTrustDialogAccepted": True}
                    for repo in config.repos.values()
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def run_two(conn, audit, config, layout, tmp_path, machine, **overrides) -> int:
    registry, proc = machine
    boundaries = overrides.pop(
        "boundaries",
        make_boundaries(
            audit,
            writer=RecordingWriter(),
            host=StubSessionHost(confirm=True),
            hooks=SubprocessHookRunner(audit),
        ),
    )
    return dispatch.select_and_dispatch(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        trust_file=trust_both(tmp_path, config),
        registry_dir=registry,
        proc_root=proc,
        **overrides,
    )


def test_two_items_in_one_repository_yield_one_session(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """One clone, one session: they would share its ports, its dev server, and its
    submodule fetches. Every collision risk planning §6 measured is per-clone."""
    config = capped_at(two_repos, 4)
    first = ready_item(conn, config, repo_key="demo", issue_number=1)
    second = ready_item(conn, config, repo_key="demo", issue_number=2)

    assert run_two(conn, audit, config, layout, tmp_path, machine) == 1
    assert db.get_work_item(conn, first).state is WorkItemState.ACTIVE
    assert db.get_work_item(conn, second).state is WorkItemState.READY


def test_a_third_item_in_a_different_repository_dispatches_in_the_same_pass(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """``break`` versus ``continue``, and it is the whole of FR-012 and FR-020. A global
    condition ends the pass; a per-repository one skips that item and leaves the queue
    moving — otherwise one busy repository stalls every other."""
    config = capped_at(two_repos, 4)
    first = ready_item(conn, config, repo_key="demo", issue_number=1)
    blocked = ready_item(conn, config, repo_key="demo", issue_number=2)
    elsewhere = ready_item(conn, config, repo_key="other", issue_number=3)

    assert run_two(conn, audit, config, layout, tmp_path, machine) == 2
    assert db.get_work_item(conn, first).state is WorkItemState.ACTIVE
    assert db.get_work_item(conn, blocked).state is WorkItemState.READY
    assert db.get_work_item(conn, elsewhere).state is WorkItemState.ACTIVE


def test_the_hold_reason_is_repo_cap_rather_than_global_cap(
    conn, two_repos, tmp_path, machine
):
    """The two send the author to different fixes: one says close a session, the other says
    raise a number in a file. Reporting the wrong one wastes the trip."""
    registry, proc = machine
    config = capped_at(two_repos, 4)
    ready_item(conn, config, repo_key="demo", issue_number=1)
    ready_item(conn, config, repo_key="demo", issue_number=2)
    running = seed_item(conn, repo_key="demo", issue_number=3, state=str(WorkItemState.ACTIVE))
    seed_session(conn, running, state=str(SessionState.RUNNING), session_id="s-demo")

    snap = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    entries = ordering.plan(conn, config=config, capacity=snap)
    assert {e.hold for e in entries} == {ordering.HoldReason.REPO_CAP}
    assert "repository demo" in entries[0].detail
    assert "1 of 1" in entries[0].detail
    # US2 AS4: the author needs to know whether they chose this number.
    assert "the default" in entries[0].detail


def test_an_explicit_repository_cap_is_reported_as_chosen(conn, two_repos, tmp_path, machine):
    from dataclasses import replace as _replace

    registry, proc = machine
    demo = _replace(two_repos.repos["demo"], max_sessions=1)
    config = capped_at(
        _replace(two_repos, repos={**two_repos.repos, "demo": demo}), 4
    )
    ready_item(conn, config, repo_key="demo", issue_number=1)
    running = seed_item(conn, repo_key="demo", issue_number=3, state=str(WorkItemState.ACTIVE))
    seed_session(conn, running, state=str(SessionState.RUNNING), session_id="s-demo")

    snap = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    entries = ordering.plan(conn, config=config, capacity=snap)
    assert "configured" in entries[0].detail


def test_a_simulated_session_occupies_a_per_repository_slot(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """FR-004. A simulated session holds the same clone, so treating it as free would let a
    dry run and a live run collide in one worktree root — which is the collision the cap
    exists to prevent, not a bookkeeping detail."""
    config = capped_at(two_repos, 4)
    simulated = seed_item(
        conn, repo_key="demo", issue_number=1, state=str(WorkItemState.ACTIVE), dry_run=True
    )
    seed_session(
        conn, simulated, state=str(SessionState.RUNNING), session_id="s-sim", dry_run=True
    )
    waiting = ready_item(conn, config, repo_key="demo", issue_number=2)

    assert run_two(conn, audit, config, layout, tmp_path, machine) == 0
    assert db.get_work_item(conn, waiting).state is WorkItemState.READY


def test_a_repository_at_its_cap_frees_up_when_its_session_ends(
    conn, audit, two_repos, layout, tmp_path, machine
):
    config = capped_at(two_repos, 4)
    running = seed_item(conn, repo_key="demo", issue_number=1, state=str(WorkItemState.ACTIVE))
    row = seed_session(conn, running, state=str(SessionState.RUNNING), session_id="s-demo")
    waiting = ready_item(conn, config, repo_key="demo", issue_number=2)
    assert run_two(conn, audit, config, layout, tmp_path, machine) == 0

    with db.transaction(conn):
        conn.execute(
            "UPDATE sessions SET state = ? WHERE id = ?",
            (str(SessionState.EXITED_CLEAN), row),
        )

    assert run_two(conn, audit, config, layout, tmp_path, machine) == 1
    assert db.get_work_item(conn, waiting).state is WorkItemState.ACTIVE


def test_cancelling_a_simulated_session_frees_the_repository_it_was_holding(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """Issue #28, end to end and by the front door.

    The sibling case above ends a session by writing ``exited_clean`` into the row by hand,
    which is what an exit record would have done. This one ends it the way the maintainer
    does — ``robot-army cancel`` — on a *simulated* session, which has no wrapper and no
    process and so can never produce that record. Before #28 was fixed the row stayed
    ``running`` forever, the repository stayed at its cap, and the waiting item was held
    with ``repo_cap`` as the reason, which reads exactly like the cap working correctly.
    """
    config = capped_at(two_repos, 4)
    running = seed_item(
        conn, repo_key="demo", issue_number=1, state=str(WorkItemState.ACTIVE), dry_run=True
    )
    seed_session(
        conn, running, state=str(SessionState.RUNNING), session_id="s-sim", dry_run=True, pid=0
    )
    waiting = ready_item(conn, config, repo_key="demo", issue_number=2)

    assert run_two(conn, audit, config, layout, tmp_path, machine) == 0
    assert db.get_work_item(conn, waiting).state is WorkItemState.READY

    boundaries = make_boundaries(
        audit, writer=RecordingWriter(), host=StubSessionHost(confirm=True)
    )
    ctx = operations.Context(
        conn=conn,
        config=config,
        audit=audit,
        boundaries=boundaries,
        effect_level=boundaries.level,
    )
    assert operations.cancel(ctx, running, force=True).code == 0

    # No reconciliation pass runs between the cancel and the dispatch: FR-012's whole
    # point is that the CLI-only rehearsal has nothing sweeping on a timer.
    assert run_two(conn, audit, config, layout, tmp_path, machine) == 1
    assert db.get_work_item(conn, waiting).state is WorkItemState.ACTIVE


def test_reconciliation_frees_a_slot_leaked_before_the_fix_existed(
    conn, audit, two_repos, layout, tmp_path, machine
):
    """Issue #28, User Story 3: the recovery path for a database that is already leaking.

    The maintainer's own round was worked around by raising `default_repo_max_sessions`
    rather than by clearing the row, so the row is still there. Nothing but a sweep can
    reach it: the active-item sweep iterates items in `active`, and every route that leaks
    has already moved the item off that list.
    """
    from robot_army import reconcile

    registry, proc = machine
    config = capped_at(two_repos, 4)
    stranded = seed_item(
        conn,
        repo_key="demo",
        issue_number=1,
        state=str(WorkItemState.INTERRUPTED),
        dry_run=True,
    )
    seed_session(
        conn, stranded, state=str(SessionState.RUNNING), session_id="s-leak", dry_run=True, pid=0
    )
    waiting = ready_item(conn, config, repo_key="demo", issue_number=2)

    assert run_two(conn, audit, config, layout, tmp_path, machine) == 0
    assert db.get_work_item(conn, waiting).state is WorkItemState.READY

    boundaries = make_boundaries(audit, writer=RecordingWriter(), host=StubSessionHost())
    result = reconcile.reconcile(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        registry_dir=registry,
        proc_root=proc,
    )
    assert result.reclaimed == 1
    # Nothing was discarded to get the slot back (FR-004).
    assert {i.id for i in db.list_work_items(conn, include_simulated=True)} == {
        stranded,
        waiting,
    }

    assert run_two(conn, audit, config, layout, tmp_path, machine) == 1
    assert db.get_work_item(conn, waiting).state is WorkItemState.ACTIVE


# -- board order reaches the dispatcher (issue #48, T025) --------------------


def _govern(conn, repo_key="demo"):
    from robot_army.models import RepoProject

    with db.transaction(conn):
        db.save_repo_project(
            conn,
            RepoProject(
                repo_key=repo_key,
                project_id="PVT_3",
                project_number=3,
                project_title="robot-army",
                column_name="Ready",
                project_source="discovered",
                column_source="discovered",
                resolved_at="2026-09-02T00:00:00Z",
                last_read_at="2026-09-02T00:00:00Z",
            ),
        )


def _place(conn, item_id, column, position=None):
    conn.execute(
        "UPDATE work_items SET board_column = ?, board_position = ? WHERE id = ?",
        (column, position, item_id),
    )


def test_the_dispatcher_takes_the_top_card_first(
    conn, audit, config, layout, tmp_path, machine
):
    """The whole feature, end to end: the item the board puts first is the item that runs,
    even though it was filed second."""
    config = capped_at(config, 2)
    first_filed = ready_item(conn, config, issue_number=1)
    second_filed = ready_item(conn, config, issue_number=2)
    _govern(conn)
    _place(conn, second_filed, "Ready", 1)
    _place(conn, first_filed, "Ready", 2)

    assert run(conn, audit, config, layout, tmp_path, machine) == 1

    assert db.get_work_item(conn, second_filed).state is WorkItemState.ACTIVE
    assert db.get_work_item(conn, first_filed).state is WorkItemState.READY


def test_a_parked_card_is_never_selected(
    conn, audit, config, layout, tmp_path, machine
):
    """Not merely ordered last — the author said "not yet", and dispatch must honour that
    even when the machine is otherwise idle and there is nothing else to run."""
    config = capped_at(config, 2)
    parked = ready_item(conn, config, issue_number=1)
    _govern(conn)
    _place(conn, parked, "Backlog")

    assert run(conn, audit, config, layout, tmp_path, machine) == 0

    assert db.get_work_item(conn, parked).state is WorkItemState.READY
    snap = capacity.snapshot(
        conn, config=config, registry_dir=machine[0], proc_root=machine[1]
    )
    entries = ordering.plan(conn, config=config, capacity=snap)
    assert entries[0].hold is ordering.HoldReason.OFF_COLUMN


# -- issue #120: resume and restart pass the same gate ----------------------
#
# RA-05. Until this section existed, everything above tested the cap against
# ``select_and_dispatch`` alone — and ``resume`` and ``restart`` reached the launch by
# another door, past the cap, past the pause, and past every hold. The tests are written
# against ``operations.*`` rather than ``dispatch_item`` on purpose: the door that was open
# is the one the author actually uses, from the terminal and from the phone.


def rested_item(conn, config, *, issue_number: int, state=WorkItemState.AWAITING_REVIEW):
    """An item whose session has ended, which is what ``resume`` and ``restart`` accept.

    The session row is *closed*, as a real ended session is. Leaving it open would make the
    item count against its own repository's cap and refuse its own resume — correctly, but
    for a reason that has nothing to do with what these tests are about.
    """
    item_id = seed_item(
        conn,
        state=str(state),
        issue_number=issue_number,
        clone_path=config.repos["demo"].path,
    )
    seed_session(conn, item_id, state=str(SessionState.EXITED_CLEAN), exit_code=0)
    return item_id


@pytest.fixture
def trusted(monkeypatch, tmp_path, config):
    """Point the *default* trust file at a fixture one.

    ``operations.resume`` and ``operations.restart`` take no ``trust_file``, so unlike
    every dispatch test above these reach ``check_gates`` through the real
    ``~/.claude.json`` — which would make the result depend on what the person running the
    suite happens to have opened. Patching the default is the seam; adding a parameter to
    the product for a test's convenience is not.
    """
    monkeypatch.setattr(
        dispatch, "claude_trust_file", lambda: trust_file(tmp_path, config.repos["demo"].path)
    )


def context(conn, audit, config):
    boundaries = make_boundaries(
        audit,
        writer=RecordingWriter(),
        host=StubSessionHost(confirm=True),
        hooks=SubprocessHookRunner(audit),
    )
    return operations.Context(
        conn=conn,
        config=config,
        audit=audit,
        boundaries=boundaries,
        effect_level=boundaries.level,
    )


def unchanged_columns(conn, item_id: int) -> dict:
    """Everything a refusal is forbidden to touch (FR-010, FR-011)."""
    item = db.get_work_item(conn, item_id)
    return {
        "state": item.state,
        "failure_reason": item.failure_reason,
        "blocked_reason": item.blocked_reason,
        "dispatching_at": item.dispatching_at,
        "worktree_path": item.worktree_path,
    }


@pytest.mark.parametrize("action", ["resume", "restart"])
def test_a_full_machine_refuses_resume_and_restart(
    conn, audit, config, layout, tmp_path, machine, action, trusted
):
    """SC-001. Two sessions, a cap of two, and the button that used to start a third."""
    registry, proc = machine
    config = capped_at(config, 2)
    out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    out_of_band(registry, proc, pid=102, cwd=str(tmp_path / "GIT" / "two"))
    item_id = rested_item(conn, config, issue_number=1)
    ctx = context(conn, audit, config)
    before = unchanged_columns(conn, item_id)

    result = getattr(operations, action)(
        ctx, item_id, registry_dir=registry, proc_root=proc
    )

    assert result.code == operations.EXIT_PRECONDITION, "refused, not attempted-and-failed"
    assert result.data["refused"] is True
    assert result.data["hold"] == str(ordering.HoldReason.GLOBAL_CAP)
    assert "2 of 2 sessions running" in result.data["detail"]
    assert unchanged_columns(conn, item_id) == before, "a refusal writes nothing"


def test_a_freed_slot_lets_the_same_resume_succeed_first_time(
    conn, audit, config, layout, tmp_path, machine, trusted
):
    """FR-012 and SC-004: no repair step between the refusal and the success."""
    registry, proc = machine
    config = capped_at(config, 1)
    occupant = out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    item_id = rested_item(conn, config, issue_number=1)
    ctx = context(conn, audit, config)

    assert operations.resume(ctx, item_id, registry_dir=registry, proc_root=proc).code == (
        operations.EXIT_PRECONDITION
    )

    occupant.unlink()
    (proc / "101").rename(proc / "101.gone")

    assert (
        operations.resume(ctx, item_id, registry_dir=registry, proc_root=proc).code
        == operations.EXIT_OK
    )
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


def test_a_repository_cap_refuses_a_resume_while_the_machine_has_room(
    conn, audit, config, layout, tmp_path, machine, trusted
):
    """SC-002. The machine-wide limit is nowhere near; this repository's own is reached."""
    registry, _proc = machine
    config = capped_at(config, 9, per_repo=1)
    running = ready_item(conn, config, issue_number=1)
    seed_session(conn, running, state=str(SessionState.RUNNING))
    item_id = rested_item(conn, config, issue_number=2)
    ctx = context(conn, audit, config)

    result = operations.resume(ctx, item_id, registry_dir=registry)

    assert result.code == operations.EXIT_PRECONDITION
    assert result.data["hold"] == str(ordering.HoldReason.REPO_CAP)
    assert "demo" in result.data["detail"]


def test_an_idle_machine_still_resumes(conn, audit, config, layout, tmp_path, machine, trusted):
    """The gate must not become a wall. Nothing applies, so nothing is refused — and the
    ordinary case leaves no refusal record behind."""
    registry, _proc = machine
    config = capped_at(config, 2, per_repo=2)
    item_id = rested_item(conn, config, issue_number=1)
    ctx = context(conn, audit, config)

    assert operations.resume(ctx, item_id, registry_dir=registry).code == operations.EXIT_OK
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE



def test_the_dispatcher_selects_the_same_items_in_the_same_order_as_before(
    conn, audit, config, layout, tmp_path, machine
):
    """SC-006. The gate must not change what the daemon dispatches or in what order.

    ``select_and_dispatch`` now calls ``check_launch_gate`` for every item it selects, so
    it observes the machine twice per dispatch — once to plan, once to launch. Everything
    the second observation could refuse, the first has already refused, so a pass through a
    queue the planner permits must be identical to one before the gate existed: same items,
    same order, and no ``dispatch.refused`` record anywhere in the log.
    """
    config = capped_at(config, 3, per_repo=3)
    first = ready_item(conn, config, issue_number=1)
    second = ready_item(conn, config, issue_number=2)
    third = ready_item(conn, config, issue_number=3)

    assert run(conn, audit, config, layout, tmp_path, machine) == 3

    for item_id in (first, second, third):
        assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE
    order = [
        db.get_work_item(conn, item_id).active_at for item_id in (first, second, third)
    ]
    assert order == sorted(order), "oldest first, exactly as before"
    assert _records(layout, audit, "dispatch.refused") == [], (
        "the planner had already permitted every one of them"
    )


def test_a_pass_ends_rather_than_crashing_when_the_gate_disagrees_with_the_plan(
    conn, audit, config, layout, tmp_path, machine, monkeypatch
):
    """R9. The two observations are taken at different instants, and between them the
    author can start a session by hand — so disagreement is a legitimate outcome, not a
    contradiction to assert against. It must end the pass, not escape into the daemon tick.
    """
    config = capped_at(config, 3, per_repo=3)
    ready_item(conn, config, issue_number=1)
    ready_item(conn, config, issue_number=2)

    def refuse(*_args, **_kwargs):
        raise dispatch.DispatchRefused(
            "3 of 3 sessions running (0 ours, 3 other)",
            hold=ordering.HoldReason.GLOBAL_CAP,
        )

    monkeypatch.setattr(dispatch, "check_launch_gate", refuse)

    assert run(conn, audit, config, layout, tmp_path, machine) == 0


def _records(layout, audit, action: str) -> list[dict]:
    audit.close()
    out = []
    for path in sorted(layout.log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["action"] == action:
                out.append(record)
    return out


# -- issue #120, US2: the pause and the holds bind every launch path --------


@pytest.mark.parametrize("action", ["resume", "restart"])
def test_a_paused_system_refuses_resume_and_restart(
    conn, audit, config, layout, tmp_path, machine, action, trusted
):
    """The author paused the system before going out. The button used to ignore that."""
    registry, proc = machine
    config = capped_at(config, 9, per_repo=9)
    item_id = rested_item(conn, config, issue_number=1)
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="cli")
    ctx = context(conn, audit, config)
    before = unchanged_columns(conn, item_id)

    result = getattr(operations, action)(
        ctx, item_id, registry_dir=registry, proc_root=proc
    )

    assert result.code == operations.EXIT_PRECONDITION
    assert result.data["hold"] == str(ordering.HoldReason.PAUSED)
    assert "robot-army unpause" in result.data["detail"]
    assert unchanged_columns(conn, item_id) == before


@pytest.mark.parametrize("action", ["resume", "restart"])
def test_a_held_item_refuses_resume_and_restart(
    conn, audit, config, layout, tmp_path, machine, action, trusted
):
    registry, proc = machine
    config = capped_at(config, 9, per_repo=9)
    item_id = rested_item(conn, config, issue_number=1)
    with db.transaction(conn):
        db.set_item_hold(conn, item_id, by="web")
    ctx = context(conn, audit, config)

    result = getattr(operations, action)(
        ctx, item_id, registry_dir=registry, proc_root=proc
    )

    assert result.code == operations.EXIT_PRECONDITION
    assert result.data["hold"] == str(ordering.HoldReason.HELD)
    assert "web" in result.data["detail"]


def test_a_held_repository_refuses_a_resume_of_an_item_that_is_not_itself_held(
    conn, audit, config, layout, tmp_path, machine, trusted
):
    registry, proc = machine
    config = capped_at(config, 9, per_repo=9)
    item_id = rested_item(conn, config, issue_number=1)
    with db.transaction(conn):
        db.set_repo_hold(conn, "demo", by="cli")
    ctx = context(conn, audit, config)

    result = operations.resume(ctx, item_id, registry_dir=registry, proc_root=proc)

    assert result.code == operations.EXIT_PRECONDITION
    assert result.data["hold"] == str(ordering.HoldReason.HELD)
    assert "demo" in result.data["detail"]


def test_both_holds_are_named_so_releasing_one_does_not_look_ignored(
    conn, audit, config, layout, tmp_path, machine, trusted
):
    """FR-006 through the launch path. Naming one and silently keeping the other is how
    the author releases a hold, presses the button, and is told ``held`` again."""
    registry, proc = machine
    config = capped_at(config, 9, per_repo=9)
    item_id = rested_item(conn, config, issue_number=1)
    with db.transaction(conn):
        db.set_item_hold(conn, item_id, by="web")
        db.set_repo_hold(conn, "demo", by="cli")
    ctx = context(conn, audit, config)

    detail = operations.resume(
        ctx, item_id, registry_dir=registry, proc_root=proc
    ).data["detail"]

    assert "web" in detail and "cli" in detail
    assert "releasing one leaves the other in force" in detail


def test_a_pause_is_named_ahead_of_a_full_machine(
    conn, audit, config, layout, tmp_path, machine, trusted
):
    """US2 AS5. Freeing a slot changes nothing while the system is paused, so naming the
    cap would send the author to fix the wrong thing."""
    registry, proc = machine
    config = capped_at(config, 1)
    out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    item_id = rested_item(conn, config, issue_number=1)
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="cli")
    ctx = context(conn, audit, config)

    result = operations.resume(ctx, item_id, registry_dir=registry, proc_root=proc)

    assert result.data["hold"] == str(ordering.HoldReason.PAUSED)


@pytest.mark.parametrize(
    "lift",
    [
        pytest.param("unpause", id="unpausing"),
        pytest.param("unhold", id="releasing the hold"),
    ],
)
def test_lifting_the_condition_lets_the_same_resume_succeed_first_time(
    conn, audit, config, layout, tmp_path, machine, lift, trusted
):
    """SC-004, and the reason a refusal must not write a failure reason: the author's fix
    is to lift the condition, and nothing else may stand between that and the button."""
    registry, proc = machine
    config = capped_at(config, 9, per_repo=9)
    item_id = rested_item(conn, config, issue_number=1)
    with db.transaction(conn):
        if lift == "unpause":
            db.set_dispatch_paused(conn, paused=True, by="cli")
        else:
            db.set_item_hold(conn, item_id, by="cli")
    ctx = context(conn, audit, config)

    assert operations.resume(
        ctx, item_id, registry_dir=registry, proc_root=proc
    ).code == operations.EXIT_PRECONDITION

    with db.transaction(conn):
        if lift == "unpause":
            db.set_dispatch_paused(conn, paused=False, by="cli")
        else:
            db.clear_item_hold(conn, item_id)

    assert operations.resume(
        ctx, item_id, registry_dir=registry, proc_root=proc
    ).code == operations.EXIT_OK
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


# -- issue #120, US3: exactly one dispatcher wins an item ------------------
#
# The claim is what makes this exclusive, and it can only be reached the way a real racer
# reaches it: through ``dispatch_item``, on an item another claimant already holds. Driving
# it through ``operations.resume``/``restart`` proves nothing about the claim — their own
# state pre-checks refuse first, which is why the sequential double-tap was already safe
# and why the cross-process race was not.


def launch(conn, audit, config, layout, tmp_path, machine, item_id, **kwargs):
    registry, proc = machine
    boundaries = make_boundaries(
        audit,
        writer=RecordingWriter(),
        host=StubSessionHost(confirm=True),
        hooks=SubprocessHookRunner(audit),
    )
    return dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
        registry_dir=registry,
        proc_root=proc,
        **kwargs,
    )


def test_a_second_launch_of_an_item_already_starting_up_is_refused(
    conn, audit, config, layout, tmp_path, machine
):
    """The state a losing racer finds, and the pair that used to succeed silently.

    ``transition_work_item`` treats ``dispatching -> dispatching`` as a legitimate no-op —
    correctly, for reconciliation and spool replay — so before the atomic claim both
    racers walked past it and launched into one worktree on one branch.
    """
    config = capped_at(config, 9, per_repo=9)
    item_id = seed_item(
        conn,
        state=str(WorkItemState.DISPATCHING),
        issue_number=1,
        clone_path=config.repos["demo"].path,
    )
    before = unchanged_columns(conn, item_id)

    with pytest.raises(dispatch.DispatchRefused) as caught:
        launch(conn, audit, config, layout, tmp_path, machine, item_id)

    assert caught.value.hold is None, "no queueing word describes a lost race"
    assert "claimed by another dispatcher" in caught.value.detail
    assert unchanged_columns(conn, item_id) == before, "the winner's item is untouched"
    assert db.list_sessions_for_item(conn, item_id) == []


def test_a_lost_claim_is_refused_rather_than_failing_the_winners_item(
    conn, audit, config, layout, tmp_path, machine
):
    """US3 AS3. Settling here would let the loser fail work it never claimed — the double
    dispatch wearing the opposite sign, and the item would need ``retry`` to recover."""
    config = capped_at(config, 9, per_repo=9)
    item_id = seed_item(
        conn,
        state=str(WorkItemState.DISPATCHING),
        issue_number=1,
        clone_path=config.repos["demo"].path,
    )

    with pytest.raises(dispatch.DispatchRefused):
        launch(conn, audit, config, layout, tmp_path, machine, item_id)

    item = db.get_work_item(conn, item_id)
    assert item.state is WorkItemState.DISPATCHING, "not failed"
    assert item.failure_reason is None
    assert item.blocked_reason is None


def test_a_lost_claim_is_recorded_as_a_refusal_not_an_error(
    conn, audit, config, layout, tmp_path, machine
):
    """FR-013. It is also deliberately not a ``dispatch.error``: the generic handler exists
    for the unforeseen, and a lost race is neither unforeseen nor an error of the dispatch.
    Filing it there would defeat reconstruction as surely as not recording it at all."""
    config = capped_at(config, 9, per_repo=9)
    item_id = seed_item(
        conn,
        state=str(WorkItemState.ACTIVE),
        issue_number=1,
        clone_path=config.repos["demo"].path,
    )

    with pytest.raises(dispatch.DispatchRefused):
        launch(conn, audit, config, layout, tmp_path, machine, item_id)

    refused = _records(layout, audit, "dispatch.refused")
    assert refused, "a refusal that leaves no record is the bug being fixed"
    assert refused[-1]["detail"]["hold"] is None
    assert refused[-1]["detail"]["found_state"] == str(WorkItemState.ACTIVE)
    assert refused[-1]["detail"]["note"].startswith("the item was not touched")
    assert _records(layout, audit, "dispatch.error") == []


@pytest.mark.parametrize("attempt", range(50))
def test_two_concurrent_launches_of_one_item_start_exactly_one_session(
    conn, audit, config, layout, tmp_path, machine, attempt
):
    """SC-005, repeated so that "no race" is a measurement rather than an assertion.

    Two threads on two connections to one database file, released together, both past the
    gate because the machine has room for both. Exactly one may claim the item, and exactly
    one session may exist afterwards.
    """
    config = capped_at(config, 9, per_repo=9)
    item_id = ready_item(conn, config, issue_number=1)
    conn.commit()

    outcomes: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(2)

    def attempt_launch() -> None:
        own = db.connect(layout.db_path)
        start.wait()
        try:
            launch(own, audit, config, layout, tmp_path, machine, item_id)
            outcome = "won"
        except dispatch.DispatchRefused:
            outcome = "refused"
        except sqlite3.OperationalError:
            outcome = "busy"
        finally:
            own.close()
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=attempt_launch) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Exactly one, not "at most one": both threads reach the claim, so a pass in which
    # neither won would mean the contention never happened and the assertion proved
    # nothing. ``busy`` is accepted as a loss but must not be how *every* run ends.
    assert outcomes.count("won") == 1, f"expected exactly one winner, got {outcomes}"
    assert outcomes.count("refused") == 1, f"the loser must be told, got {outcomes}"
    fresh = db.connect(layout.db_path)
    assert len(db.list_sessions_for_item(fresh, item_id)) == 1, (
        "one worktree, one branch, one agent"
    )
    fresh.close()



# -- issue #120, US4: what --force reaches, and what it must never reach ---


def test_force_starts_a_session_past_every_policy_condition_at_once(
    conn, audit, config, layout, tmp_path, machine, trusted
):
    """SC-008. Paused, held, repository held, and the machine full — all at once."""
    registry, proc = machine
    config = capped_at(config, 1, per_repo=1)
    out_of_band(registry, proc, pid=101, cwd=str(tmp_path / "GIT" / "one"))
    item_id = rested_item(conn, config, issue_number=1)
    with db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="cli")
        db.set_item_hold(conn, item_id, by="web")
        db.set_repo_hold(conn, "demo", by="cli")
    ctx = context(conn, audit, config)

    assert operations.resume(
        ctx, item_id, registry_dir=registry, proc_root=proc
    ).code == operations.EXIT_PRECONDITION

    result = operations.resume(
        ctx, item_id, registry_dir=registry, proc_root=proc, force=True
    )

    assert result.code == operations.EXIT_OK
    assert result.data["force"] is True
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE
    forced = _records(layout, audit, "dispatch.forced")
    assert forced, "an override that leaves no record is the bug wearing a different hat"
    assert {entry["hold"] for entry in forced[-1]["detail"]["overridden"]} >= {
        str(ordering.HoldReason.PAUSED),
        str(ordering.HoldReason.HELD),
        str(ordering.HoldReason.GLOBAL_CAP),
    }


def test_force_does_not_reach_the_issue_author_check(
    conn, audit, config, layout, tmp_path, machine, trusted
):
    """FR-024. The author check is what stops "anyone may open an issue on a public
    repository" becoming "anyone may run an agent in the maintainer's checkout". It is not
    the author's policy to override — it is the boundary the policy sits inside."""
    registry, proc = machine
    config = capped_at(config, 9, per_repo=9)
    item_id = seed_item(
        conn,
        state=str(WorkItemState.AWAITING_REVIEW),
        issue_number=1,
        clone_path=config.repos["demo"].path,
        author="somebody-else",
    )
    seed_session(conn, item_id, state=str(SessionState.EXITED_CLEAN), exit_code=0)
    ctx = context(conn, audit, config)

    result = operations.resume(
        ctx, item_id, registry_dir=registry, proc_root=proc, force=True
    )

    assert result.code == operations.EXIT_FAILED
    item = db.get_work_item(conn, item_id)
    assert item.state is WorkItemState.FAILED
    assert "not the configured author" in (item.blocked_reason or "")


def test_force_does_not_reach_workspace_trust(
    conn, audit, config, layout, tmp_path, machine, monkeypatch
):
    """FR-024, the other half. ``--force`` is deliberately not ``skip_gates``: two
    different questions, and one flag answering both is how the cap got lost in the first
    place."""
    registry, proc = machine
    config = capped_at(config, 9, per_repo=9)
    item_id = rested_item(conn, config, issue_number=1)
    monkeypatch.setattr(dispatch, "claude_trust_file", lambda: tmp_path / "untrusted.json")
    (tmp_path / "untrusted.json").write_text("{}", encoding="utf-8")
    ctx = context(conn, audit, config)

    result = operations.resume(
        ctx, item_id, registry_dir=registry, proc_root=proc, force=True
    )

    assert result.code == operations.EXIT_FAILED
    assert "trust" in (db.get_work_item(conn, item_id).blocked_reason or "")


def test_force_does_not_reach_the_claim(
    conn, audit, config, layout, tmp_path, machine, trusted
):
    """FR-025. The override covers the author's own policy; it can never make two agents
    share one worktree, which is the harm the claim exists to prevent."""
    config = capped_at(config, 9, per_repo=9)
    item_id = seed_item(
        conn,
        state=str(WorkItemState.DISPATCHING),
        issue_number=1,
        clone_path=config.repos["demo"].path,
    )

    with pytest.raises(dispatch.DispatchRefused) as caught:
        launch(conn, audit, config, layout, tmp_path, machine, item_id, force=True)

    assert "claimed by another dispatcher" in caught.value.detail


# -- review of #129: four corrections --------------------------------------


def test_force_on_an_unblocked_launch_claims_no_override(
    conn, audit, config, layout, tmp_path, machine, trusted
):
    """`--force` on a machine with nothing to override must not say it overrode something.

    The first cut printed "the dispatch gate was overridden; see dispatch.forced in the
    log" for *any* `--force`, including one where `launch_holds` returned nothing and no
    `dispatch.forced` record was written. That is the "the interface says something other
    than what happened" failure this whole feature exists to remove, pointed at a log entry
    that does not exist. The line is gone; the record remains the only claim, and it is
    written only when a condition actually applied.
    """
    registry, proc = machine
    config = capped_at(config, 9, per_repo=9)
    item_id = rested_item(conn, config, issue_number=1)
    ctx = context(conn, audit, config)

    result = operations.resume(
        ctx, item_id, registry_dir=registry, proc_root=proc, force=True
    )

    assert result.code == operations.EXIT_OK
    assert "overridden" not in " ".join(result.lines)
    assert _records(layout, audit, "dispatch.forced") == [], (
        "nothing applied, so nothing was overridden"
    )
    # The key reports the flag the author gave, which is true, rather than asserting an
    # override that did not happen.
    assert result.data["force"] is True


def test_a_lost_claim_does_not_end_the_dispatcher_pass(
    conn, audit, config, layout, tmp_path, machine, monkeypatch
):
    """A lost claim is the most per-item condition there is — another process took *this*
    item, which says nothing about the next candidate.

    The first cut returned on any `DispatchRefused`, so one raced item stopped the whole
    pass. The split now reuses `_GLOBAL_HOLDS`, exactly as the loop above already applies
    it to `ordering.plan`'s own holds.
    """
    config = capped_at(config, 9, per_repo=9)
    first = ready_item(conn, config, issue_number=1)
    second = ready_item(conn, config, issue_number=2)
    real = dispatch.check_launch_gate
    lost: list[int] = []

    def steal_the_first(conn_, *, item, **kwargs):
        if item.id == first and not lost:
            lost.append(item.id)
            raise dispatch.DispatchRefused(
                f"item {first} is dispatching; it was claimed by another dispatcher"
            )
        return real(conn_, item=item, **kwargs)

    monkeypatch.setattr(dispatch, "check_launch_gate", steal_the_first)

    assert run(conn, audit, config, layout, tmp_path, machine) == 1, (
        "the pass continues past the item it lost"
    )
    assert db.get_work_item(conn, second).state is WorkItemState.ACTIVE


def test_a_repository_cap_refusal_does_not_end_the_dispatcher_pass(
    conn, audit, config, layout, tmp_path, machine, monkeypatch
):
    """`repo_cap` is per-item too — it holds one repository's work and must leave every
    other repository free, which is the whole of FR-012 and FR-020."""
    config = capped_at(config, 9, per_repo=9)
    first = ready_item(conn, config, issue_number=1)
    second = ready_item(conn, config, issue_number=2)
    real = dispatch.check_launch_gate
    seen: list[int] = []

    def cap_the_first(conn_, *, item, **kwargs):
        if item.id == first and not seen:
            seen.append(item.id)
            raise dispatch.DispatchRefused(
                "repository demo: 1 of 1 sessions (configured)",
                hold=ordering.HoldReason.REPO_CAP,
            )
        return real(conn_, item=item, **kwargs)

    monkeypatch.setattr(dispatch, "check_launch_gate", cap_the_first)

    assert run(conn, audit, config, layout, tmp_path, machine) == 1
    assert db.get_work_item(conn, second).state is WorkItemState.ACTIVE


@pytest.mark.parametrize("hold", [ordering.HoldReason.PAUSED, ordering.HoldReason.GLOBAL_CAP])
def test_a_global_refusal_still_ends_the_dispatcher_pass(
    conn, audit, config, layout, tmp_path, machine, monkeypatch, hold
):
    """The other side of the same split: no later item could fit into a slot this one could
    not, so continuing would be work with a known answer."""
    config = capped_at(config, 9, per_repo=9)
    ready_item(conn, config, issue_number=1)
    ready_item(conn, config, issue_number=2)

    def refuse(*_args, **_kwargs):
        raise dispatch.DispatchRefused("the machine is full", hold=hold)

    monkeypatch.setattr(dispatch, "check_launch_gate", refuse)

    assert run(conn, audit, config, layout, tmp_path, machine) == 0


# -- the daemon does not read its own heartbeat (issue #30) -----------------


def test_a_heartbeat_naming_another_cap_does_not_move_the_dispatch_line(
    conn, audit, config, layout, tmp_path, machine
):
    """Issue #30 made every *read* surface defer to the daemon's published cap. Dispatch is
    not a read surface, and this is the test that says so.

    The daemon is the authority. Asking the file it wrote what it thinks would be circular,
    would put a file read in the dispatch path, and would make a safety decision from a
    value that originates outside the process — one a stale or hand-edited heartbeat could
    move. So a heartbeat naming a cap of nine changes nothing about a daemon configured for
    one.
    """
    from robot_army import health

    registry, proc = machine
    config = capped_at(config, 1, per_repo=5)
    health.write_heartbeat(
        layout.heartbeat_path,
        effect_level="live",
        activity="idle",
        cycles=1,
        max_concurrent_sessions=9,
    )
    first = ready_item(conn, config, issue_number=1)
    second = ready_item(conn, config, issue_number=2)

    assert run(conn, audit, config, layout, tmp_path, machine) == 1, (
        "exactly one dispatch, as the daemon's own cap of one allows"
    )
    states = [db.get_work_item(conn, i).state for i in (first, second)]
    assert WorkItemState.READY in states, "the second item is still waiting for a slot"


def test_the_planners_snapshot_reports_the_daemons_own_cap(
    conn, audit, config, layout, tmp_path, machine
):
    """The value-level companion: a snapshot taken on the dispatch path carries the
    daemon's configured cap and reports no disagreement, whatever any heartbeat says."""
    from robot_army import health

    registry, proc = machine
    config = capped_at(config, 2)
    health.write_heartbeat(
        layout.heartbeat_path,
        effect_level="live",
        activity="idle",
        cycles=1,
        max_concurrent_sessions=9,
    )

    snap = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert snap.global_cap == 2
    assert snap.configured_cap is None
    assert snap.cap_disagreement is None


def test_dispatch_is_unchanged_with_no_heartbeat_at_all(
    conn, audit, config, layout, tmp_path, machine
):
    """A daemon caught before its first beat, or a heartbeat file removed underneath one."""
    registry, proc = machine
    config = capped_at(config, 1, per_repo=5)
    if layout.heartbeat_path.exists():
        layout.heartbeat_path.unlink()
    ready_item(conn, config, issue_number=1)
    ready_item(conn, config, issue_number=2)

    assert run(conn, audit, config, layout, tmp_path, machine) == 1
