"""Ending the worker of a finished work item (issue #138).

The bug this closes is not in any one part. A worker never ends itself: it opens the pull
request and sits at a prompt, so the exit record that closes a session row never arrives.
Merging closes the issue, the item goes ``done``, and a live worker under a ``done`` item
is exactly the condition ``orphan_session`` was built to report — correctly. The sum of
three correct behaviours was that **the ordinary successful path terminated in an anomaly
and a capacity slot held for as long as the machine stayed up**, and three successful items
at the shipped cap of three stopped dispatch permanently.

Most of what follows tests the *refusals*, and that is the right proportion. Retirement
ends a process, which is the one thing here that cannot be undone from this side, and the
entire safety argument is that every way of failing to establish "this worker is finished
and idle" leaves it alone. A test suite for this feature that spent its length on the happy
path would be testing the least interesting half.

Contract: ``specs/20260905-121903-retire-finished-sessions/contracts/session-retirement.md``.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import make_boundaries, seed_item, seed_session, write_proc, write_registry

from robot_army import capacity, db, reconcile, sessions
from robot_army.boundaries import HostHandle, TerminationOutcome
from robot_army.states import SessionState, WorkItemState

REPO = "jantman/robot-army"
PID = 767308
SESSION = "037460ea-0969-4a44-adca-d79920557a33"

#: Comfortably past ``RETIRE_IDLE_SECONDS``. The two real sessions behind #138 had been
#: idle 84 and 198 minutes when they were measured.
LONG_IDLE_MS = reconcile.RETIRE_IDLE_SECONDS * 1000 + 60_000


def now_ms() -> int:
    return int(time.time() * 1000)


@pytest.fixture
def proc(tmp_path: Path) -> Path:
    """A /proc holding one non-worker process, so enumeration demonstrably works."""
    root = tmp_path / "proc"
    write_proc(root, 1, starttime="1", exe="/usr/lib/systemd/systemd")
    return root


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    directory = tmp_path / "registry"
    directory.mkdir()
    return directory


class KillingHost:
    """A session host that actually removes the fake process, as a real one would.

    The distinction matters: after a confirmed termination the settle step asks ``/proc``
    again, so a stub that reports success while leaving the process in place would test a
    world that cannot exist and would hide the very interaction being verified.
    """

    def __init__(
        self,
        proc_root: Path,
        *,
        confirmed: bool = True,
        refuse_reason: str | None = None,
        raises: Exception | None = None,
        on_terminate: Any = None,
    ) -> None:
        self.proc_root = proc_root
        self.confirmed = confirmed
        self.refuse_reason = refuse_reason
        self.raises = raises
        self.on_terminate = on_terminate
        self.calls: list[tuple[int | None, str | None, str | None]] = []

    def terminate(
        self,
        handle: HostHandle,
        scope: str | None = None,
        *,
        expected_start: str | None = None,
        proc_root: Any = None,
    ) -> TerminationOutcome:
        self.calls.append((handle.pid, scope, expected_start))
        if self.raises is not None:
            raise self.raises
        if self.on_terminate is not None:
            self.on_terminate()
        if self.refuse_reason is not None:
            return TerminationOutcome(
                confirmed=False,
                method="refused",
                refused_reason=self.refuse_reason,
                detail={"pid": handle.pid, "signals_sent": 0},
            )
        if not self.confirmed:
            return TerminationOutcome(
                confirmed=False, method="process_group_signal", escalated=True
            )
        shutil.rmtree(self.proc_root / str(handle.pid), ignore_errors=True)
        return TerminationOutcome(
            confirmed=True,
            method="systemd_scope" if scope else "process_group_signal",
        )

    def attach_command(self, handle: HostHandle) -> list[str]:
        return ["dtach", "-a", handle.socket_path]


def finished_item(
    conn,
    config,
    registry: Path,
    proc: Path,
    *,
    item_state: str = "done",
    idle_ms: int | None = LONG_IDLE_MS,
    status: str | None = "idle",
    pid: int | None = PID,
    live: bool = True,
    dry_run: bool = False,
    repo_key: str = REPO,
) -> int:
    """A work item whose worker is still running, in whatever shape the case needs."""
    item = seed_item(conn, repo_key=repo_key, dry_run=dry_run, state=item_state)
    seed_session(
        conn,
        item,
        state="running",
        session_id=SESSION,
        pid=pid,
        proc_start=str(pid) if pid else None,
        dry_run=dry_run,
    )
    if live and pid:
        cwd = Path(config.worktree_root) / "issue-116"
        cwd.mkdir(parents=True, exist_ok=True)
        write_registry(
            registry,
            pid=pid,
            session_id=SESSION,
            proc_start=str(pid),
            cwd=str(cwd),
            status=status,
            status_updated_at=None if idle_ms is None else now_ms() - idle_ms,
        )
        write_proc(proc, pid, starttime=str(pid), cwd=str(cwd))
    return item


def sweep(conn, audit, registry: Path, proc: Path, host: Any) -> int:
    return reconcile._retire_finished_sessions(
        conn,
        boundaries=make_boundaries(audit, host=host),
        audit=audit,
        scan=sessions.scan(registry_dir=registry, proc_root=proc),
        proc_root=proc,
    )


def records(layout, *actions: str) -> list[dict]:
    out: list[dict] = []
    for path in sorted(layout.log_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                if not actions or record.get("action") in actions:
                    out.append(record)
    return out


# -- C2: the decision table, one case per rule -------------------------------
#
# Every rule but the last means LEAVE, and "leave" is checked in three ways each time:
# the process is untouched, the row is untouched, and nothing was even asked of the host.


@pytest.mark.parametrize(
    "item_state", ["dispatching", "active", "awaiting_review", "interrupted", "failed"]
)
def test_a_worker_under_an_unfinished_item_is_never_retired(
    conn, config, audit, registry, proc, item_state
):
    """C2 rule 1. Retirement is for work that has been *accepted*, and only ``done`` says
    that. ``failed`` is in this list on purpose: it looks finished and is not."""
    item = finished_item(conn, config, registry, proc, item_state=item_state)
    host = KillingHost(proc)

    assert sweep(conn, audit, registry, proc, host) == 0
    assert host.calls == []
    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING


def test_a_worker_under_an_abandoned_item_is_never_retired(conn, config, audit, registry, proc):
    """C2 rule 1, and the clarification that shaped this feature. ``abandoned`` is terminal
    but the work is unfinished, so its session may be the reason the maintainer is about to
    attach. ``robot-army cancel`` is the route out, not this sweep."""
    item = finished_item(conn, config, registry, proc, item_state="abandoned")
    host = KillingHost(proc)

    assert sweep(conn, audit, registry, proc, host) == 0
    assert host.calls == []
    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING


@pytest.mark.parametrize("pid", [0, None])
def test_a_row_that_never_had_a_process_is_left_to_the_stale_sweep(
    conn, config, audit, registry, proc, pid
):
    """C2 rule 2. There is nothing to end, and reaching a real host with ``pid = 0`` is the
    ``getpgid(0)`` hazard that answers about *the caller* — the daemon's own process
    group."""
    item = finished_item(conn, config, registry, proc, pid=pid, dry_run=True)
    host = KillingHost(proc)

    assert sweep(conn, audit, registry, proc, host) == 0
    assert host.calls == []
    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING


def test_a_session_with_no_registry_entry_is_left_alone(conn, config, audit, registry, proc):
    """C2 rule 3. No entry means no way to know whether it is idle, and an unknown is never
    a reason to end something."""
    item = finished_item(conn, config, registry, proc, live=False)
    host = KillingHost(proc)

    assert sweep(conn, audit, registry, proc, host) == 0
    assert host.calls == []
    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING


def test_a_session_whose_process_is_already_gone_is_left_to_the_stale_sweep(
    conn, config, audit, registry, proc
):
    """C2 rule 4. There is nothing to terminate; ``_sweep_stale_sessions`` closes the row
    later in the same pass, which is its job and not this one's."""
    item = finished_item(conn, config, registry, proc)
    shutil.rmtree(proc / str(PID))
    host = KillingHost(proc)

    assert sweep(conn, audit, registry, proc, host) == 0
    assert host.calls == []
    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING


@pytest.mark.parametrize(
    ("status", "idle_ms"),
    [
        ("busy", LONG_IDLE_MS),
        ("compacting", LONG_IDLE_MS),
        (None, LONG_IDLE_MS),
        ("idle", None),
    ],
    ids=["busy", "unrecognised-status", "no-status", "no-timestamp"],
)
def test_idleness_that_cannot_be_established_never_retires(
    conn, config, audit, registry, proc, status, idle_ms
):
    """C2 rule 5, and the whole safety argument for depending on an undocumented file.

    Note the ages: every one of these has been "idle" far longer than the threshold by the
    clock. It is the *unknown* that saves them, not the clock, which is the property that
    has to hold when a worker release changes this file under us.
    """
    item = finished_item(conn, config, registry, proc, status=status, idle_ms=idle_ms)
    host = KillingHost(proc)

    assert sweep(conn, audit, registry, proc, host) == 0
    assert host.calls == []
    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING


def test_a_worker_idle_for_less_than_the_threshold_is_left_alone(
    conn, config, audit, registry, proc
):
    """C2 rule 6, at the boundary. This is the maintainer attached and thinking."""
    item = finished_item(
        conn, config, registry, proc, idle_ms=(reconcile.RETIRE_IDLE_SECONDS - 5) * 1000
    )
    host = KillingHost(proc)

    assert sweep(conn, audit, registry, proc, host) == 0
    assert host.calls == []
    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING


def test_a_finished_item_with_a_long_idle_worker_is_retired(
    conn, config, audit, layout, registry, proc
):
    """C2 rule 7 — the case #138 is about, and the only branch that ends anything."""
    item = finished_item(conn, config, registry, proc)
    host = KillingHost(proc)

    assert sweep(conn, audit, registry, proc, host) == 1

    session = db.latest_session_for_item(conn, item)
    assert session.state is SessionState.LOST
    assert session.ended_at is not None
    # The work item is untouched. Retirement is about the process, not about the work.
    assert db.get_work_item(conn, item).state is WorkItemState.DONE


def test_the_pass_asks_the_question_again_next_time(conn, config, audit, registry, proc):
    """C2 rule 6 leaves nothing behind, so a worker that goes quiet later is still caught.
    A one-shot 'we looked once' column would silently strand it."""
    item = finished_item(conn, config, registry, proc, idle_ms=60_000)
    host = KillingHost(proc)
    assert sweep(conn, audit, registry, proc, host) == 0

    write_registry(
        registry,
        pid=PID,
        session_id=SESSION,
        proc_start=str(PID),
        cwd=str(Path(config.worktree_root) / "issue-116"),
        status="idle",
        status_updated_at=now_ms() - LONG_IDLE_MS,
    )
    assert sweep(conn, audit, registry, proc, host) == 1
    assert db.latest_session_for_item(conn, item).state is SessionState.LOST


# -- C3: the order the act happens in ----------------------------------------


def test_the_intent_is_logged_before_the_signal(conn, config, audit, layout, registry, proc):
    """Principle III. Ending a process cannot be undone from this side, so the record has
    to survive the daemon dying between the decision and the kill."""
    logged: list[str] = []
    host = KillingHost(
        proc,
        on_terminate=lambda: logged.extend(
            r["action"] for r in records(layout, "session.retire")
        ),
    )
    finished_item(conn, config, registry, proc)

    assert sweep(conn, audit, registry, proc, host) == 1
    assert logged == ["session.retire"], (
        "session.retire must be on disk before terminate is called, not after"
    )


def test_the_retire_record_carries_what_a_reader_needs(
    conn, config, audit, layout, registry, proc
):
    item = finished_item(conn, config, registry, proc)

    sweep(conn, audit, registry, proc, KillingHost(proc))

    intent = records(layout, "session.retire")
    assert len(intent) == 1
    detail = intent[0]["detail"]
    assert detail["item_id"] == item
    assert detail["session_id"] == SESSION
    assert detail["pid"] == PID
    assert detail["proc_start"] == str(PID)
    assert detail["idle_s"] >= reconcile.RETIRE_IDLE_SECONDS


def test_the_recorded_process_identity_is_passed_to_the_termination(
    conn, config, audit, registry, proc
):
    """FR-005. A pid alone is not identity — the kernel recycles them, and signalling the
    stranger holding the number is an incident this project has already had."""
    finished_item(conn, config, registry, proc)
    host = KillingHost(proc)

    sweep(conn, audit, registry, proc, host)

    assert host.calls == [(PID, None, str(PID))]


def test_the_settling_reason_names_retirement(conn, config, audit, layout, registry, proc):
    """The log is the only place "the maintainer stopped this" and "this was retired
    because its work was accepted" stay distinguishable."""
    finished_item(conn, config, registry, proc)

    sweep(conn, audit, registry, proc, KillingHost(proc))

    transitions = [r for r in records(layout, "state.session") if r["detail"]["to"] == "lost"]
    assert len(transitions) == 1
    assert "retired" in transitions[0]["detail"]["reason"]
    assert "idle" in transitions[0]["detail"]["reason"]


# -- C4: the four outcomes ---------------------------------------------------


def test_a_refused_termination_signals_nothing_and_settles_nothing(
    conn, config, audit, layout, registry, proc
):
    """C4 ``refused``. The boundary declined to act; the row is a malformed record to be
    inspected, not a session that stopped. Reporting it like a failed kill would assert a
    signal that was never sent."""
    item = finished_item(conn, config, registry, proc)
    host = KillingHost(proc, refuse_reason="the recorded pid is 0")

    assert sweep(conn, audit, registry, proc, host) == 0

    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING
    assert (proc / str(PID)).exists(), "a refusal must leave the world unchanged"
    refusals = records(layout, "session.retire_refused")
    assert len(refusals) == 1
    assert refusals[0]["detail"]["refused_reason"] == "the recorded pid is 0"


def test_a_surviving_worker_keeps_its_row_its_slot_and_its_anomaly(
    conn, config, audit, layout, registry, proc
):
    """C4 ``survived``, and FR-007. "I tried and could not" is never recorded as "it is
    gone" — an under-count is the one capacity error that causes harm."""
    item = finished_item(conn, config, registry, proc)
    host = KillingHost(proc, confirmed=False)

    assert sweep(conn, audit, registry, proc, host) == 0

    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING
    after = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert after.total == 1, "the slot must stay subscribed while the worker lives"
    assert len(records(layout, "session.retire_unconfirmed")) == 1

    # And the existing sweep still reports it, because it is genuinely an orphan.
    assert reconcile._sweep_stale_sessions(conn, audit=audit, scan=sessions.scan(
        registry_dir=registry, proc_root=proc), proc_root=proc) == 0
    assert [a.kind for a in db.list_anomalies(conn)] == ["orphan_session"]


def test_a_worker_that_died_on_its_own_first_is_a_retirement_not_a_failure(
    conn, config, audit, registry, proc
):
    """C4: ``already_gone``. Nothing was signalled and the row still closes, which is the
    right outcome — the slot has to come back either way."""
    item = finished_item(conn, config, registry, proc)

    class AlreadyGone(KillingHost):
        def terminate(self, handle, scope=None, **kwargs):
            self.calls.append((handle.pid, scope, kwargs.get("expected_start")))
            shutil.rmtree(self.proc_root / str(handle.pid), ignore_errors=True)
            return TerminationOutcome(confirmed=True, method="already_gone")

    assert sweep(conn, audit, registry, proc, AlreadyGone(proc)) == 1
    assert db.latest_session_for_item(conn, item).state is SessionState.LOST


def test_a_row_settled_by_its_own_exit_record_mid_flight_is_not_a_failure(
    conn, config, audit, registry, proc
):
    """C4 ``already_settled``, and FR-008. The daemon drains the exit spool in its own
    process while this runs, so a worker killed by our own signal can record its ending
    before we get to the settle. Treating that as a failure would report a perfectly
    successful retirement as a problem."""
    item = finished_item(conn, config, registry, proc)

    def settle_it_first() -> None:
        with db.transaction(conn):
            conn.execute(
                "UPDATE sessions SET state = 'exited_clean', ended_at = '2026-09-05T00:00:00Z' "
                "WHERE session_id = ?",
                (SESSION,),
            )

    host = KillingHost(proc, on_terminate=settle_it_first)

    assert sweep(conn, audit, registry, proc, host) == 1
    session = db.latest_session_for_item(conn, item)
    assert session.state is SessionState.EXITED_CLEAN, "the exit record's answer stands"


def test_a_boundary_error_leaves_the_row_open_and_never_raises(
    conn, config, audit, layout, registry, proc
):
    """A reconciliation pass must not raise for an operational condition. The row is
    untouched, so the next pass simply tries again."""
    from robot_army.boundaries import BoundaryError

    item = finished_item(conn, config, registry, proc)
    host = KillingHost(proc, raises=BoundaryError("systemctl is not on PATH"))

    assert sweep(conn, audit, registry, proc, host) == 0
    assert db.latest_session_for_item(conn, item).state is SessionState.RUNNING
    assert any(r["outcome"] == "error" for r in records(layout, "session.retire"))


def test_one_failing_retirement_does_not_stop_the_next(conn, config, audit, registry, proc):
    """Two finished items in one pass are decided independently."""
    other_pid = 404232
    other_session = "c2673ac8-4983-4886-b4dc-16d4a3b7d780"
    first = finished_item(conn, config, registry, proc)
    second = seed_item(conn, repo_key=REPO, issue_number=136, state="done")
    seed_session(
        conn,
        second,
        state="running",
        session_id=other_session,
        pid=other_pid,
        proc_start=str(other_pid),
    )
    cwd = Path(config.worktree_root) / "issue-136"
    cwd.mkdir(parents=True, exist_ok=True)
    write_registry(
        registry,
        pid=other_pid,
        session_id=other_session,
        proc_start=str(other_pid),
        cwd=str(cwd),
        status="idle",
        status_updated_at=now_ms() - LONG_IDLE_MS,
    )
    write_proc(proc, other_pid, starttime=str(other_pid), cwd=str(cwd))

    class RefuseTheFirst(KillingHost):
        def terminate(self, handle, scope=None, **kwargs):
            if handle.pid == PID:
                self.calls.append((handle.pid, scope, None))
                return TerminationOutcome(
                    confirmed=False, method="refused", refused_reason="malformed row"
                )
            return super().terminate(handle, scope, **kwargs)

    assert sweep(conn, audit, registry, proc, RefuseTheFirst(proc)) == 1
    assert db.latest_session_for_item(conn, first).state is SessionState.RUNNING
    assert db.latest_session_for_item(conn, second).state is SessionState.LOST


# -- C6: what a non-retirement writes ----------------------------------------


def test_a_deferred_decision_writes_absolutely_nothing(
    conn, config, audit, layout, registry, proc
):
    """The documented Principle III gap, asserted rather than described.

    A 60-second loop reporting "still busy" about a session the maintainer is using would
    write ~1,440 records a day carrying one bit. The absence *is* the behaviour, so it is
    the assertion — and ten passes are run because a leak of one record per pass is exactly
    the shape this is defending against.
    """
    finished_item(conn, config, registry, proc, idle_ms=60_000)
    host = KillingHost(proc)
    before = len(records(layout))

    for _ in range(10):
        assert sweep(conn, audit, registry, proc, host) == 0

    assert len(records(layout)) == before, "a deferral must write nothing at all"
    assert db.list_anomalies(conn) == []


# -- what retirement releases (FR-012, FR-016) -------------------------------


def test_retirement_releases_the_global_and_the_per_repository_slot(
    conn, config, audit, registry, proc
):
    """SC-002. Three successful items at the shipped cap of three used to wedge the machine
    permanently, reporting only that it was full."""
    finished_item(conn, config, registry, proc)

    before = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert before.total == 1
    assert before.per_repo == {REPO: 1}

    assert sweep(conn, audit, registry, proc, KillingHost(proc)) == 1

    after = capacity.snapshot(conn, config=config, registry_dir=registry, proc_root=proc)
    assert after.total == 0
    assert after.per_repo == {}


def test_retirement_leaves_the_transcript_alone(
    conn, config, audit, registry, proc, transcripts
):
    """FR-016, and the reason no configuration key guards this. Retirement ends a process;
    it does not discard the record of what that process did, so a session ended while the
    maintainer was reading it is fully recoverable."""
    from tests.conftest import write_transcript

    written = write_transcript(transcripts, SESSION)
    finished_item(conn, config, registry, proc)

    assert sweep(conn, audit, registry, proc, KillingHost(proc)) == 1

    assert written.exists()
    assert sessions.transcript_exists(SESSION)


def test_the_worktree_is_untouched(conn, config, audit, registry, proc):
    """Cleanup decides about disk, under its own two guards. This decides about a process."""
    finished_item(conn, config, registry, proc)
    worktree = Path(config.worktree_root) / "issue-116"

    assert sweep(conn, audit, registry, proc, KillingHost(proc)) == 1

    assert worktree.is_dir()


# -- the same-pass benefit for cleanup (FR-015, SC-004) ----------------------


def test_a_retired_session_lets_cleanup_reclaim_the_worktree_in_the_same_pass(
    conn, config, audit, layout, registry, proc
):
    """The third consequence #138 reported, and the reason the sweep sits where it does.

    Cleanup's session guard sees a live row and records ``skipped`` — which means "not
    yet", and is reconsidered every pass. With a worker that never ends, "not yet" is
    forever, and two 50 MB worktrees on a machine whose /home was over 90% full could never
    be reclaimed. Retirement runs *before* the cleanup block, so the guard passes honestly
    in the very same pass rather than the next one.

    The two cleanup guards themselves are untouched by this feature; this asserts only that
    the first one now has a truthful answer to give.
    """
    from dataclasses import replace

    from tests.unit.test_cleanup import FakeVcs

    # ``demo`` is the repository the config fixture gives a clone path, and cleanup
    # retains outright for a repository that no longer resolves to one.
    item = finished_item(conn, config, registry, proc, repo_key="demo")
    worktree = Path(config.worktree_root) / "issue-116"
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item, worktree_path=str(worktree), branch="robot-army/issue-116-x"
        )

    vcs = FakeVcs(ahead={"origin/main": 0})
    cleaning = replace(config, cleanup=replace(config.cleanup, on_issue_close=True))

    reconcile.reconcile(
        conn,
        boundaries=make_boundaries(audit, host=KillingHost(proc), vcs=vcs),
        audit=audit,
        config=cleaning,
        layout=layout,
        registry_dir=registry,
        proc_root=proc,
    )

    assert db.latest_session_for_item(conn, item).state is SessionState.LOST
    assert db.get_work_item(conn, item).cleanup_state != "skipped", (
        "the worktree must stop being deferred once nothing is running in it"
    )
    assert vcs.removals, "cleanup never got as far as asking git to remove the worktree"


# -- the orphan sweep must not report what this pass just killed -------------


def test_a_full_pass_raises_no_orphan_for_the_session_it_retired(
    conn, config, audit, layout, registry, proc
):
    """Found in review of PR #140, and the tests above all missed it.

    ``_orphan_sweep`` reads the pass's ``scan`` snapshot directly and never re-checked
    liveness. None of its three guards catches a session this pass retired: the pid was
    never in ``claimed_pids`` (only ``active`` items claim), the cwd really is under the
    worktree root, and the row is ``lost`` rather than ``running``. So every ordinary
    successful retirement raised a fresh ``orphan_session`` against the worker it had just
    deliberately killed.

    It went unnoticed because ``_resolve_orphan_anomalies`` runs later in the same pass and
    resolved it, leaving ``robot-army anomalies`` clean — which is all the earlier test
    asserted. The damage was in the two places nobody was looking: ``result.orphans``
    counted a phantom, and the log gained a raise/resolve pair for every successful item,
    on exactly the path this feature exists to make quiet.

    The assertions are therefore on the counters and the records, not on the final listing.
    """
    finished_item(conn, config, registry, proc)

    result = reconcile.reconcile(
        conn,
        boundaries=make_boundaries(audit, host=KillingHost(proc)),
        audit=audit,
        config=config,
        layout=layout,
        registry_dir=registry,
        proc_root=proc,
    )

    assert result.retired == 1
    assert result.orphans == 0, "the sweep reported the worker this pass just retired"
    assert result.anomalies_resolved == 0, (
        "nothing should need resolving; nothing should have been raised"
    )
    assert records(layout, "anomaly.resolved") == []
    assert db.list_anomalies(conn, unacknowledged_only=False) == []


def test_a_genuine_orphan_is_still_reported_after_the_liveness_recheck(
    conn, config, audit, layout, registry, proc, boundaries
):
    """The property the re-check must not cost: a worker that really is running
    unaccounted for is still reported. This is M0 F17 — the wrapper died and the worker
    carried on, reparented — and it is the reason the sweep exists at all."""
    item = seed_item(conn, repo_key=REPO, state="interrupted")
    seed_session(conn, item, state="lost", session_id="ghost", pid=91234)
    cwd = Path(config.worktree_root) / "issue-99"
    cwd.mkdir(parents=True, exist_ok=True)
    write_registry(
        registry, pid=91234, session_id="ghost", proc_start="91234", cwd=str(cwd)
    )
    write_proc(proc, 91234, starttime="91234", cwd=str(cwd))

    result = reconcile.reconcile(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        registry_dir=registry,
        proc_root=proc,
    )

    assert result.orphans == 1
    assert [a.entity_id for a in db.list_anomalies(conn)] == ["ghost"]
