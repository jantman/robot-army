"""``terminate`` confirms the effect instead of trusting the exit status (014 US1).

The case that matters is C2, and it is the whole issue: ``systemctl --user stop`` exits 0
for a unit that is already inactive, killing nothing. Before this milestone that exit
status ended the function, the process-group fallback four lines below was unreachable,
and ``cancel`` reported a stopped session that was still running twenty-six minutes later.

Every case in 014 contracts/termination-outcome.md is exercised here. Liveness is driven
off a synthetic ``/proc`` tree rather than real processes: the observation under test is
``procinfo.is_alive(pid, proc_start)``, and a fixture tree can express things real
processes cannot be made to express on demand — notably C9, a pid whose start time no
longer matches, which is a *recycled* pid rather than a live session.
"""

from __future__ import annotations

import shutil
from typing import Any

import pytest
from tests.conftest import write_proc

from robot_army.boundaries import BoundaryError, HostHandle
from robot_army.boundaries.dtach import DtachHost
from robot_army.subproc import Completed

PID = 4242
START = "998877"
SCOPE = "kitty-5300-123.scope"
SOCKET = "/run/robot-army/session.sock"


def completed(returncode: int) -> Completed:
    return Completed(
        argv=("systemctl", "--user", "stop", SCOPE),
        returncode=returncode,
        stdout="",
        stderr="" if returncode == 0 else "Failed to stop unit.",
        duration=0.004,
    )


class Harness:
    """A `DtachHost` with the two outward-facing calls replaced by observable fakes.

    ``stop_returns`` is what ``systemctl`` reports; ``stop_kills`` is whether it actually
    kills. Keeping those two independent is the entire point — conflating them is the bug.
    """

    def __init__(
        self,
        monkeypatch: Any,
        proc_root: Any,
        *,
        stop_returns: int = 0,
        stop_kills: bool = False,
        signal_kills: bool = True,
        pid: int | None = PID,
        starttime: str = START,
        real_signal_group: bool = False,
        pgid: int = 4200,
    ) -> None:
        self.proc_root = proc_root
        self.stop_calls: list[list[str]] = []
        self.signal_calls: list[int] = []
        #: Only populated when ``real_signal_group`` is set: what the genuine
        #: ``_signal_group`` asked the operating system to do (069 S-C8).
        self.killpg_calls: list[tuple[int, int]] = []
        self.pid = pid
        if pid is not None:
            write_proc(proc_root, pid, starttime=starttime)

        def fake_run(argv: list[str], **kwargs: Any) -> Completed:
            self.stop_calls.append(list(argv))
            if stop_kills:
                self._kill()
            return completed(stop_returns)

        def fake_signal_group(target_pid: int, outcome: dict[str, Any]) -> None:
            self.signal_calls.append(target_pid)
            outcome["signal"] = "SIGTERM"
            if signal_kills:
                self._kill()

        monkeypatch.setattr("robot_army.boundaries.dtach.run", fake_run)
        if real_signal_group:
            # 069 S-C8 needs the genuine primitive, because the value under test — the
            # process group the pid resolves to — is one the stub never computes. Stand in
            # for ``os`` instead, one layer further out.
            harness = self

            class SpyOs:
                def getpgid(self, target_pid: int) -> int:
                    return pgid

                def killpg(self, target_pgid: int, sig: int) -> None:
                    harness.killpg_calls.append((target_pgid, sig))

            monkeypatch.setattr("robot_army.boundaries.dtach.os", SpyOs())
        else:
            monkeypatch.setattr("robot_army.boundaries.dtach._signal_group", fake_signal_group)

    def _kill(self) -> None:
        if self.pid is not None:
            shutil.rmtree(self.proc_root / str(self.pid), ignore_errors=True)

    def terminate(
        self,
        host: DtachHost,
        *,
        scope: str | None = SCOPE,
        expected_start: str | None = START,
    ) -> Any:
        # A fake clock that only advances when the code sleeps. The bounded waits are
        # then exercised in full without spending five real seconds each on a process
        # that the test has already decided will never die.
        self.now = 0.0

        def sleep(seconds: float) -> None:
            self.now += seconds

        return host.terminate(
            HostHandle(socket_path=SOCKET, argv=(), pid=self.pid),
            scope,
            expected_start=expected_start,
            proc_root=self.proc_root,
            sleep=sleep,
            clock=lambda: self.now,
        )


@pytest.fixture
def host(audit) -> DtachHost:
    return DtachHost(audit)


def test_c1_scope_stop_kills_and_is_confirmed(host, tmp_path, monkeypatch):
    harness = Harness(monkeypatch, tmp_path, stop_returns=0, stop_kills=True)
    outcome = harness.terminate(host)
    assert outcome.confirmed is True
    assert outcome.method == "systemd_scope"
    assert outcome.escalated is False
    assert harness.signal_calls == [], "the fallback must not run when the scope really killed it"


def test_c2_a_scope_stop_that_reports_success_without_killing_escalates(
    host, tmp_path, monkeypatch
):
    """Issue #34, exactly.

    ``systemctl --user stop`` returns 0 against an already-inactive scope whose cgroup
    still holds a live process. The pre-milestone code returned here and reported the
    session stopped. Delete the confirmation step and this test is what goes red.
    """
    harness = Harness(monkeypatch, tmp_path, stop_returns=0, stop_kills=False, signal_kills=True)
    outcome = harness.terminate(host)
    assert harness.signal_calls == [PID], "the fallback must be reached despite exit 0"
    assert outcome.confirmed is True
    assert outcome.method == "process_group_signal"
    assert outcome.escalated is True


def test_c3_a_failed_scope_stop_falls_through_without_claiming_escalation(
    host, tmp_path, monkeypatch
):
    harness = Harness(monkeypatch, tmp_path, stop_returns=5, stop_kills=False, signal_kills=True)
    outcome = harness.terminate(host)
    assert outcome.confirmed is True
    assert outcome.method == "process_group_signal"
    assert outcome.escalated is False, "nothing reported success, so nothing was contradicted"


def test_c4_no_scope_recorded_goes_straight_to_the_process_group(host, tmp_path, monkeypatch):
    harness = Harness(monkeypatch, tmp_path, signal_kills=True)
    outcome = harness.terminate(host, scope=None)
    assert harness.stop_calls == []
    assert outcome.confirmed is True
    assert outcome.method == "process_group_signal"


def test_c5_a_process_already_gone_is_a_success_that_tries_nothing(host, tmp_path, monkeypatch):
    harness = Harness(monkeypatch, tmp_path)
    shutil.rmtree(tmp_path / str(PID))
    outcome = harness.terminate(host)
    assert outcome.confirmed is True
    assert outcome.method == "already_gone"
    assert harness.stop_calls == [], "nothing to stop, so nothing is asked to stop it"
    assert harness.signal_calls == []


def test_c6_a_session_that_survives_everything_is_not_confirmed(host, tmp_path, monkeypatch):
    harness = Harness(monkeypatch, tmp_path, stop_returns=0, stop_kills=False, signal_kills=False)
    outcome = harness.terminate(host)
    assert outcome.confirmed is False
    assert outcome.method == "process_group_signal"
    assert outcome.escalated is True
    assert harness.signal_calls == [PID]


def test_c8_a_scope_with_no_recorded_pid_cannot_be_confirmed(host, tmp_path, monkeypatch):
    harness = Harness(monkeypatch, tmp_path, pid=None)
    outcome = harness.terminate(host)
    assert harness.stop_calls, "the stop is still attempted"
    assert outcome.confirmed is False
    assert outcome.method == "none", "an exit status alone is not a confirmation (T5)"


def test_c7_no_scope_and_no_pid_raises_rather_than_returning_an_outcome(
    host, tmp_path, monkeypatch
):
    harness = Harness(monkeypatch, tmp_path, pid=None)
    with pytest.raises(BoundaryError, match="no systemd scope recorded and no pid known"):
        harness.terminate(host, scope=None)


def test_c9_a_recycled_pid_means_our_process_is_gone_and_is_never_signalled(
    host, tmp_path, monkeypatch
):
    """The start time no longer matches, so this pid belongs to something else.

    Two things must both hold: our session counts as gone, and the stranger holding its
    old pid is not signalled. A bare existence check gets both wrong.
    """
    harness = Harness(monkeypatch, tmp_path, starttime="11111111")
    outcome = harness.terminate(host)
    assert outcome.confirmed is True
    assert outcome.method == "already_gone"
    assert harness.signal_calls == []
    assert (tmp_path / str(PID)).exists(), "the unrelated process is still there, untouched"


def test_c10_the_simulated_host_confirms_by_construction(audit):
    from robot_army.boundaries.dtach import SimulatedSessionHost

    host = SimulatedSessionHost(audit)
    handle = host.spawn("/tmp", ["claude"], SOCKET)
    outcome = host.terminate(handle, SCOPE)
    assert outcome.confirmed is True
    assert outcome.method == "simulated"
    assert host.is_alive(handle) is False


def test_confirmation_is_bounded_and_a_timeout_is_not_a_success(host, tmp_path, monkeypatch):
    """T4: a bound that elapses yields "not confirmed", never success."""
    Harness(monkeypatch, tmp_path, stop_returns=0, stop_kills=False, signal_kills=False)
    ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 99.0])
    outcome = host.terminate(
        HostHandle(socket_path=SOCKET, argv=(), pid=PID),
        SCOPE,
        expected_start=START,
        proc_root=tmp_path,
        sleep=lambda _seconds: None,
        clock=lambda: next(ticks),
    )
    assert outcome.confirmed is False


def test_the_record_carries_both_the_reported_success_and_the_contradiction(
    host, tmp_path, monkeypatch, layout
):
    """FR-002 and FR-011: an escalated stop must not record only the success."""
    from robot_army.audit import read_records

    harness = Harness(monkeypatch, tmp_path, stop_returns=0, stop_kills=False, signal_kills=True)
    harness.terminate(host)
    host._audit.close()
    records = [r for r, _ in read_records(layout.log_dir) if r is not None]
    settled = [
        r for r in records if r["action"] == "session.terminate" and r["outcome"] != "pending"
    ]
    assert len(settled) == 1
    detail = settled[0]["detail"]
    assert detail["confirmed"] is True
    assert detail["escalated"] is True
    rungs = detail["rungs"]
    assert [rung["method"] for rung in rungs] == ["systemd_scope", "process_group_signal"]
    assert rungs[0]["exit"] == 0 and rungs[0]["alive_after"] is True
    assert rungs[1]["alive_after"] is False


def test_the_record_alone_answers_what_was_tried_and_what_was_observed(
    host, tmp_path, monkeypatch, layout
):
    """FR-011, the reconstruction standard: no re-running anything.

    This lives beside the other termination tests rather than in ``test_audit.py``,
    which is about the log's own mechanics — redaction, intent/outcome pairing, tolerant
    reading — and not about any one action's payload. The fixtures that make a rung
    observable are all here.
    """
    from robot_army.audit import read_records

    harness = Harness(monkeypatch, tmp_path, stop_returns=0, stop_kills=False, signal_kills=True)
    harness.terminate(host)
    host._audit.close()

    records = [r for r, _ in read_records(layout.log_dir) if r is not None]
    pair = [r for r in records if r["action"] == "session.terminate"]
    assert [r["kind"] for r in pair] == ["intent", "outcome"], (
        "the intent is flushed before the kill, so a process killed mid-terminate leaves "
        "an intent with no outcome — the crash signature Principle IV asks for"
    )
    detail = pair[-1]["detail"]

    # What it was asked to stop.
    assert detail["scope"] == SCOPE
    assert detail["pid"] == PID
    assert detail["proc_start"] == START
    # What it tried, in order, and what each attempt returned.
    assert [rung["method"] for rung in detail["rungs"]] == [
        "systemd_scope",
        "process_group_signal",
    ]
    assert detail["rungs"][0]["exit"] == 0 and detail["rungs"][0]["ok"] is True
    assert detail["rungs"][1]["signal"] == "SIGTERM"
    # What it observed after each attempt, and how long it waited to observe it.
    assert detail["rungs"][0]["alive_after"] is True
    assert detail["rungs"][1]["alive_after"] is False
    assert all("waited_s" in rung for rung in detail["rungs"])
    # And what it concluded.
    assert detail["confirmed"] is True
    assert detail["escalated"] is True
    assert detail["method"] == "process_group_signal"

    # The `systemctl.stop` record itself is written by `subproc.run`, which this harness
    # replaces and which is unchanged and tested elsewhere — asserting on it here would be
    # asserting on the fake. What is worth pinning is that the *right* scope was asked to
    # stop: an opaque handle read at confirmation time, never recomputed (M0 F18).
    assert harness.stop_calls == [["systemctl", "--user", "stop", SCOPE]]


# -- 069: what may be signalled ---------------------------------------------------------
#
# The rungs above answer "did it stop?". These answer the question nobody asked until
# 2026-08-31: "is this pid ours to stop at all?". `_signal_group` had no opinion, so a
# recorded pid of 1 became `killpg(1, SIGTERM)` — `kill(-1)`, every process the user owns.
#
# Every case here asserts that *nothing was attempted*: no `systemctl`, no signal. A
# refusal that still stops the scope would be a smaller catastrophe, not a fixed one.


def test_s_c1_a_recorded_pid_of_one_is_refused_before_anything_runs(host, tmp_path, monkeypatch):
    """The incident's exact row: pid 1, and no recorded start time to contradict it.

    ``procinfo.is_alive(1, None)`` is ``True`` — measured — because a missing start time
    degrades the check to "does /proc/1 exist". That degradation is what carried the
    recorded pid past the 014 pre-check and into ``killpg``.
    """
    harness = Harness(monkeypatch, tmp_path, pid=1)
    outcome = harness.terminate(host, expected_start=None)

    assert outcome.confirmed is False
    assert outcome.method == "refused"
    assert outcome.refused_reason is not None
    assert "1" in outcome.refused_reason
    assert harness.stop_calls == [], "a row we do not trust has no trustworthy scope either"
    assert harness.signal_calls == []


def test_s_c2_a_recorded_pid_of_one_is_refused_even_with_a_matching_start_time(
    host, tmp_path, monkeypatch
):
    """Identity validation alone would let this through.

    ``/proc/1`` has a real start time like any other process (measured: ``17``), so a row
    carrying pid 1 *and* a matching one satisfies every identity check we have. The flat
    rejection has to stand on its own — this is the test that fails if someone later
    "simplifies" the two guards into one.
    """
    harness = Harness(monkeypatch, tmp_path, pid=1)
    outcome = harness.terminate(host, expected_start=START)

    assert outcome.confirmed is False
    assert outcome.method == "refused"
    assert harness.stop_calls == []
    assert harness.signal_calls == []


def test_s_c3_a_recorded_pid_of_zero_is_refused(host, tmp_path, monkeypatch):
    """``getpgid(0)`` means *the caller*, not pid 0.

    It returns the caller's own process group — an ordinary number, nowhere near 1 — so a
    ``pgid <= 1`` guard does not catch this. Signalling it ends the daemon, or the
    operator's shell when the CLI is the one asking. The fixture ``/proc/0`` here exists
    deliberately: the guard must fire even when liveness says the row is alive.
    """
    harness = Harness(monkeypatch, tmp_path, pid=0)
    outcome = harness.terminate(host, expected_start=START)

    assert outcome.confirmed is False
    assert outcome.method == "refused"
    assert "0" in (outcome.refused_reason or "")
    assert harness.stop_calls == []
    assert harness.signal_calls == []


def test_s_c8_a_live_pid_whose_group_resolves_to_one_is_refused(host, tmp_path, monkeypatch):
    """The third route to the same catastrophe, and the one only the primitive can see.

    The recorded pid is ordinary and its start time matches, so both of the guards above
    pass it. Only resolving the group reveals that signalling it would be ``kill(-1)``.
    The ladder reports that as a refusal like any other; the primitive underneath raises.
    """
    harness = Harness(
        monkeypatch, tmp_path, stop_returns=5, stop_kills=False, real_signal_group=True, pgid=1
    )
    outcome = harness.terminate(host)

    assert outcome.confirmed is False
    assert outcome.method == "refused"
    assert harness.killpg_calls == [], "the whole point: the call was never made"


def test_the_guards_do_not_cost_an_ordinary_stop(host, tmp_path, monkeypatch):
    """The positive control, at the ladder level (S-C9, SC-005).

    A guard that refused everything would satisfy every assertion above. This is the test
    that says an ordinary well-formed session still stops exactly as it did before.
    """
    harness = Harness(monkeypatch, tmp_path, stop_returns=0, stop_kills=True)
    outcome = harness.terminate(host)

    assert outcome.confirmed is True
    assert outcome.method == "systemd_scope"
    assert outcome.refused_reason is None


def test_s_c6_a_pid_with_no_recorded_start_time_is_not_signalled(host, tmp_path, monkeypatch):
    """A bare number is not an identity (069 S1).

    This is the guard that generalises the three flat rejections above. The recorded pid
    is live and perfectly ordinary; what is missing is the ``proc_start`` that says it is
    *ours*. ``procinfo.is_alive`` degrades to "does /proc/<pid> exist" when the start time
    is ``None`` — documented, and fine for liveness — and that degradation is precisely
    what carried a recorded pid of 1 past the pre-check on 2026-08-31.

    Such a row is reachable rather than merely malformed: the session registry requires
    ``pid`` to be an int but treats ``procStart`` as optional, so a real session whose
    registration omitted it lands here. That is why the guard sits in front of the *signal*
    rather than in front of the whole ladder — see the scope-stop cases below.
    """
    harness = Harness(monkeypatch, tmp_path, stop_returns=5, stop_kills=False)
    outcome = harness.terminate(host, expected_start=None)

    assert outcome.confirmed is False
    assert outcome.method == "refused"
    assert "start time" in (outcome.refused_reason or "")
    assert harness.signal_calls == [], "the one thing that must not happen"


def test_a_recycled_pid_is_still_already_gone_and_not_a_refusal(host, tmp_path, monkeypatch):
    """S-C7, restated as a regression guard on the *boundary* between two verdicts.

    A start time that is present and does not match is a completely different fact from
    one that is absent: it says our process is gone and a stranger holds its number. That
    is ``already_gone`` — a confirmed stop — and folding it into the refusal above would
    make every recycled pid an operator error to investigate.
    """
    harness = Harness(monkeypatch, tmp_path, starttime="11111111")
    outcome = harness.terminate(host, expected_start=START)

    assert outcome.confirmed is True
    assert outcome.method == "already_gone"
    assert outcome.refused_reason is None
    assert harness.signal_calls == []


def test_a_matching_start_time_still_takes_the_whole_ladder(host, tmp_path, monkeypatch):
    """S-C9 / SC-005: the identity guard must cost no legitimate cancel.

    Pid recorded, start time recorded and matching, scope lying about its success — the
    exact shape of issue #34. It must still escalate to the signal and still confirm.
    """
    harness = Harness(monkeypatch, tmp_path, stop_returns=0, stop_kills=False, signal_kills=True)
    outcome = harness.terminate(host, expected_start=START)

    assert outcome.confirmed is True
    assert outcome.method == "process_group_signal"
    assert outcome.escalated is True
    assert outcome.refused_reason is None
    assert harness.signal_calls == [PID]


def test_the_record_says_what_was_refused_and_that_nothing_was_sent(
    host, tmp_path, monkeypatch, layout
):
    """069 S7/FR-007: reconstruction, which is the standard Principle III sets.

    From the log alone, without re-running anything, a reader must be able to say which
    session, which recorded value, why, and — stated rather than inferred — that no signal
    was delivered. ``signals_sent: 0`` is explicit because "we refused" and "we refused and
    sent nothing" are the same claim only to a reader who trusts the code.
    """
    from robot_army.audit import read_records

    harness = Harness(monkeypatch, tmp_path, pid=1)
    harness.terminate(host, expected_start=None)

    terminations = [
        record
        for record, _ in read_records(layout.log_dir)
        if record is not None
        and record.get("action") == "session.terminate"
        and record.get("outcome") == "ok"
    ]
    assert terminations, "a refusal that leaves no record is a Principle III violation"
    detail = terminations[-1]["detail"]
    assert detail["refused"] is True
    assert detail["signals_sent"] == 0
    assert "process group" in detail["refused_reason"]
    assert detail["pid"] == 1, "the intent already named the pid; the outcome keeps it"
    assert detail["rungs"] == [], "decided up front, so nothing was attempted at all"


def test_a_missing_start_time_still_allows_the_scope_stop(host, tmp_path, monkeypatch):
    """The identity guard must gate the *signal*, not the whole ladder.

    A row can carry a real pid, a real scope and no ``proc_start``: the session registry
    requires ``pid`` to be an int but treats ``procStart`` as optional
    (``sessions.py``), and ``dispatch`` stores whatever the entry had without backfilling.
    Refusing that row outright makes it permanently uncancellable — ``cancel`` has no force
    or override path — even though ``systemctl --user stop`` names a scope and touches no
    pid or process group at all, and so carries none of the ``kill(-1)`` risk this feature
    is about.

    Note the confirmation is still sound without an identity. Absence is conclusive where
    presence is not: if ``/proc/<pid>`` is gone then neither our process nor a stranger
    holds that number, so our session is certainly gone. A pid that is *still there* proves
    nothing, and that is the case the signal guard below refuses to act on.
    """
    harness = Harness(monkeypatch, tmp_path, stop_returns=0, stop_kills=True)
    outcome = harness.terminate(host, expected_start=None)

    assert harness.stop_calls, "the scope stop is safe and must still be attempted"
    assert outcome.confirmed is True
    assert outcome.method == "systemd_scope"
    assert harness.signal_calls == []


def test_a_missing_start_time_still_refuses_the_signal_after_the_scope_stop(
    host, tmp_path, monkeypatch
):
    """The scope stop was tried and did not take. Now identity matters, and there is none.

    So the ladder stops here rather than signalling a pid it cannot tell apart from any
    other process holding that number — and the record shows the scope rung ran, which is
    the difference between this refusal and one decided up front (069 S5).
    """
    harness = Harness(monkeypatch, tmp_path, stop_returns=0, stop_kills=False)
    outcome = harness.terminate(host, expected_start=None)

    assert harness.stop_calls, "the scope stop is attempted first"
    assert outcome.confirmed is False
    assert outcome.method == "refused"
    assert "start time" in (outcome.refused_reason or "")
    assert harness.signal_calls == []
    assert outcome.detail["rungs"], "the scope rung ran, and the record must say so"


def test_a_missing_start_time_with_no_scope_refuses_without_trying_anything(
    host, tmp_path, monkeypatch
):
    """Nothing safe to try, so nothing is tried."""
    harness = Harness(monkeypatch, tmp_path)
    outcome = harness.terminate(host, scope=None, expected_start=None)

    assert harness.stop_calls == []
    assert harness.signal_calls == []
    assert outcome.method == "refused"
