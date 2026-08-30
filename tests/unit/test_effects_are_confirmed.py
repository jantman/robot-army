"""No termination path reports success while the process is still observably alive.

FR-016, and the guard behind 001 contracts/boundaries.md's rule that an outward-facing
call's exit status is not evidence of its effect.

The other termination tests pin the *cases*: this pins the **property**, which is the part
that survives someone rearranging the ladder. It is asserted by making the world answer
"still alive" to every question, across every combination of what the rungs report, and
demanding that no combination produces ``confirmed=True``. Restore ``if result.ok: return``
and the first row goes red, because a scope stop that exits 0 would once again be enough.

The complement matters just as much and is asserted in the same shape: when the world says
the process is gone, every combination *must* confirm. A ``terminate`` that never confirms
anything would satisfy the first half of this file and be useless.
"""

from __future__ import annotations

from typing import Any

import pytest

from robot_army.boundaries import HostHandle
from robot_army.boundaries.dtach import DtachHost
from robot_army.subproc import Completed

PID = 777
START = "12345"
SOCKET = "/run/robot-army/guard.sock"


def wire(monkeypatch: Any, *, stop_exit: int, alive: bool) -> list[str]:
    """Make every observation answer ``alive`` and every action do nothing."""
    signalled: list[str] = []

    def fake_run(argv: list[str], **_: Any) -> Completed:
        signalled.append("stop")
        return Completed(argv=tuple(argv), returncode=stop_exit, stdout="", stderr="", duration=0.0)

    def fake_signal_group(pid: int, outcome: dict[str, Any]) -> None:
        signalled.append("signal")
        outcome["signal"] = "SIGKILL"

    monkeypatch.setattr("robot_army.boundaries.dtach.run", fake_run)
    monkeypatch.setattr("robot_army.boundaries.dtach._signal_group", fake_signal_group)
    monkeypatch.setattr(
        "robot_army.boundaries.dtach.procinfo.is_alive",
        lambda pid, expected_start, root=None: alive,
    )
    return signalled


def terminate(host: DtachHost, scope: str | None) -> Any:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    return host.terminate(
        HostHandle(socket_path=SOCKET, argv=(), pid=PID),
        scope,
        expected_start=START,
        sleep=sleep,
        clock=lambda: now[0],
    )


@pytest.mark.parametrize("stop_exit", [0, 1, 5])
@pytest.mark.parametrize("scope", ["kitty-1-2.scope", None])
def test_a_surviving_process_is_never_reported_as_stopped(audit, monkeypatch, stop_exit, scope):
    signalled = wire(monkeypatch, stop_exit=stop_exit, alive=True)
    outcome = terminate(DtachHost(audit), scope)
    assert outcome.confirmed is False, (
        f"exit {stop_exit} was reported as proof of death — this is issue #34 again"
    )
    assert "signal" in signalled, "a surviving process must reach the stronger rung"


@pytest.mark.parametrize("stop_exit", [0, 1, 5])
@pytest.mark.parametrize("scope", ["kitty-1-2.scope", None])
def test_a_process_that_is_gone_is_always_confirmed(audit, monkeypatch, stop_exit, scope):
    wire(monkeypatch, stop_exit=stop_exit, alive=False)
    assert terminate(DtachHost(audit), scope).confirmed is True
