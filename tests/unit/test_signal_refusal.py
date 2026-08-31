"""``_signal_group`` refuses to signal a pid that cannot be a session's (069 US1).

This module exists because of an absence. ``tests/unit/test_terminate_confirmation.py``
covers every case of the 014 termination contract and passes — and it never touched issue
#69, because its harness does::

    monkeypatch.setattr("robot_army.boundaries.dtach._signal_group", fake_signal_group)

Every prior test replaced the one function that actually delivers signals. So the function
that calls ``os.killpg`` had no test at all, and a ``kill(-1)`` sat inside covered,
contract-documented code until it took the maintainer's desktop session down with it.

The assertions below are therefore on the **call list**, not on the exception. That an
exception is raised proves the refusal branch is reachable; that ``killpg`` was never
called proves the signal is *un*reachable, and only the second is the requirement (SC-001).

Three values, three separate reasons, none redundant with the others (research R1):

* ``killpg(1, sig)`` is ``kill(-1, sig)`` — POSIX for "every process the caller may
  signal". Measured: ``os.getpgid(1)`` is ``1``.
* ``getpgid(0)`` does **not** mean pid 0. It means *the caller*, and returns the caller's
  own process group — an ordinary number well above 1, so a ``pgid <= 1`` test does not
  catch it. Measured on this machine: ``1743559``. Signalling it ends the daemon, or the
  operator's shell if the CLI is doing the asking.
* A live pid whose group resolves to ``1`` is the same catastrophe by a third route, and
  is why the resolved group is checked as well as the input.
"""

from __future__ import annotations

import errno
import signal
from typing import Any

import pytest

from robot_army.boundaries import BoundaryError
from robot_army.boundaries.dtach import _signal_group

#: An ordinary pid and an ordinary process group: the positive control.
LIVE_PID = 4242
LIVE_PGID = 4200


class SpyOs:
    """Stands in for ``os`` inside ``boundaries.dtach`` and records what was asked of it.

    Overrides the suite-wide ``_no_real_signals`` guard from ``conftest`` — a later
    ``monkeypatch`` wins — because these tests need to *watch* the call rather than merely
    be prevented from making it.
    """

    def __init__(self, *, pgid: int = LIVE_PGID, alive: bool = True) -> None:
        self.pgid = pgid
        self.alive = alive
        self.getpgid_calls: list[int] = []
        self.killpg_calls: list[tuple[int, int]] = []

    def getpgid(self, pid: int) -> int:
        self.getpgid_calls.append(pid)
        return self.pgid

    def killpg(self, pgid: int, sig: int) -> None:
        self.killpg_calls.append((pgid, sig))
        if sig == 0 and not self.alive:
            raise ProcessLookupError(errno.ESRCH, "No such process")
        # A real SIGTERM to a process that dies makes the *next* probe fail, not this
        # call. Modelling it that way keeps the escalation loop's shape honest.
        if sig == signal.SIGTERM:
            self.alive = False


@pytest.fixture
def spy(monkeypatch: Any) -> SpyOs:
    stand_in = SpyOs()
    monkeypatch.setattr("robot_army.boundaries.dtach.os", stand_in)
    return stand_in


@pytest.mark.parametrize("pid", [0, 1])
def test_an_impossible_pid_is_refused_and_nothing_is_signalled(spy: SpyOs, pid: int) -> None:
    """S-C1/S-C3 and contract S7: the primitive raises rather than signalling."""
    outcome: dict[str, object] = {}
    with pytest.raises(BoundaryError):
        _signal_group(pid, outcome)

    # The assertion that matters. Not "an error was raised" — "nothing was signalled".
    assert spy.killpg_calls == []
    # And the group was never even resolved: for pid 0 that call is itself the trap,
    # because getpgid(0) answers about the caller rather than about pid 0.
    assert spy.getpgid_calls == []


def test_a_process_group_of_one_is_refused(monkeypatch: Any) -> None:
    """S-C8: the input pid is ordinary but resolves to the group `kill(-1)` would use."""
    stand_in = SpyOs(pgid=1)
    monkeypatch.setattr("robot_army.boundaries.dtach.os", stand_in)

    outcome: dict[str, object] = {}
    with pytest.raises(BoundaryError):
        _signal_group(LIVE_PID, outcome)

    assert stand_in.getpgid_calls == [LIVE_PID]
    assert stand_in.killpg_calls == []


@pytest.mark.parametrize("pgid", [0, -1])
def test_a_nonpositive_process_group_is_refused(monkeypatch: Any, pgid: int) -> None:
    """``<= 1``, not ``== 1``. A zero or negative group is no more signallable."""
    stand_in = SpyOs(pgid=pgid)
    monkeypatch.setattr("robot_army.boundaries.dtach.os", stand_in)

    with pytest.raises(BoundaryError):
        _signal_group(LIVE_PID, {})

    assert stand_in.killpg_calls == []


def test_the_refusal_says_which_value_it_rejected(spy: SpyOs) -> None:
    """A refusal the maintainer cannot act on is half a refusal (FR-006)."""
    with pytest.raises(BoundaryError) as raised:
        _signal_group(1, {})

    message = str(raised.value)
    assert "1" in message
    assert spy.killpg_calls == []


def test_an_ordinary_process_group_is_still_signalled(spy: SpyOs) -> None:
    """The positive control (S-C9): the guard must not cost a legitimate stop.

    Without this, a guard that refused *everything* would pass every other test in this
    module — which is exactly the failure mode of a safety check nobody exercised from the
    other side.
    """
    outcome: dict[str, object] = {}
    _signal_group(LIVE_PID, outcome)

    assert spy.getpgid_calls == [LIVE_PID]
    assert (LIVE_PGID, signal.SIGTERM) in spy.killpg_calls
    assert outcome["signal"] == "SIGTERM"
    # SIGTERM was enough: nothing escalated to SIGKILL.
    assert (LIVE_PGID, signal.SIGKILL) not in spy.killpg_calls


def test_a_process_that_is_already_gone_is_not_an_error(monkeypatch: Any) -> None:
    """Unchanged behaviour, asserted so the guard cannot quietly absorb it."""

    class Gone(SpyOs):
        def getpgid(self, pid: int) -> int:
            self.getpgid_calls.append(pid)
            raise ProcessLookupError(errno.ESRCH, "No such process")

    stand_in = Gone()
    monkeypatch.setattr("robot_army.boundaries.dtach.os", stand_in)

    outcome: dict[str, object] = {}
    _signal_group(LIVE_PID, outcome)

    assert outcome == {"already_gone": True}
    assert stand_in.killpg_calls == []
