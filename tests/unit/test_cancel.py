"""``cancel`` settles only what it confirmed (014 US1/US2, K1-K5).

Two rules carry this file. A cancel that could not verify the session stopped changes
**nothing** — not the item, not the session row — because an item moved to `interrupted`
while its worker still runs is visible to no sweep the system has: reconciliation walks
only `active` items, so nothing would ever look at it again. And a cancel that did stop the
session says which way it stopped it, because "the scope reported success but the session
was still running" is the sentence that distinguishes this build from the one in issue #34.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import db, operations
from robot_army.audit import read_records
from robot_army.boundaries import TerminationOutcome
from robot_army.effects import EffectLevel
from robot_army.states import SessionState, WorkItemState, transition_session

SESSION = "ec237832-5246-40ed-bd7f-ac5885bf9cdd"
SOCKET = "/run/robot-army/demo-7.sock"
SCOPE = "kitty-5300-123.scope"
PID = 3029744
START = "998877"


class OutcomeHost:
    """A session host that returns exactly the outcome the test is about."""

    def __init__(self, outcome: TerminationOutcome) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def terminate(
        self,
        handle: Any,
        scope: str | None = None,
        *,
        expected_start: str | None = None,
        proc_root: Any = None,
    ) -> TerminationOutcome:
        self.calls.append(
            {
                "pid": handle.pid,
                "socket": handle.socket_path,
                "scope": scope,
                "expected_start": expected_start,
            }
        )
        return self.outcome

    def attach_command(self, handle: Any) -> list[str]:
        return ["dtach", "-a", handle.socket_path]


def seed_running(conn, audit, *, state: str = "active") -> tuple[int, int]:
    item_id = seed_item(conn, state=state)
    with db.transaction(conn):
        row_id = db.insert_session(
            conn,
            work_item_id=item_id,
            session_id=SESSION,
            attempt=1,
            dry_run=False,
            host_socket=SOCKET,
        )
        db.update_session_columns(conn, row_id, pid=PID, proc_start=START, scope=SCOPE)
        transition_session(
            conn, audit, session_row_id=row_id, target=SessionState.RUNNING, reason="seeded"
        )
    return item_id, row_id


def ctx_with(conn, audit, config, host: Any) -> operations.Context:
    return operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, host=host),
        effect_level=EffectLevel.LIVE,
    )


def confirmed(method: str = "systemd_scope", escalated: bool = False) -> TerminationOutcome:
    return TerminationOutcome(confirmed=True, method=method, escalated=escalated)


def unconfirmed() -> TerminationOutcome:
    return TerminationOutcome(
        confirmed=False,
        method="process_group_signal",
        escalated=True,
        detail={"pid": PID, "alive_after": True},
    )


# -- K1: nothing is settled on an unconfirmed stop --------------------------


def test_an_unconfirmed_stop_changes_nothing_at_all(conn, audit, config):
    item_id, _row_id = seed_running(conn, audit)
    before_item = db.get_work_item(conn, item_id)
    before_session = db.get_session(conn, SESSION)

    result = operations.cancel(ctx_with(conn, audit, config, OutcomeHost(unconfirmed())), item_id,
                               force=True)

    assert result.code != operations.EXIT_OK
    assert db.get_work_item(conn, item_id) == before_item
    assert db.get_session(conn, SESSION) == before_session
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE, (
        "an item left active is still visited by reconciliation's session sweep; "
        "an interrupted one with a live worker is visited by nothing"
    )


def test_an_unconfirmed_stop_names_what_is_still_running_and_how_to_reach_it(
    conn, audit, config
):
    item_id, _ = seed_running(conn, audit)
    result = operations.cancel(ctx_with(conn, audit, config, OutcomeHost(unconfirmed())), item_id,
                               force=True)
    text = "\n".join(result.lines)
    assert str(PID) in text
    assert SESSION in text
    assert f"dtach -a {SOCKET}" in text
    assert "could not confirm" in text
    assert "stopped session" not in text, "it did not stop the session; it must not say so"


def test_an_unconfirmed_stop_exits_failed_not_precondition(conn, audit, config):
    """K4. This is an action that did not take effect, not a request that was refused."""
    item_id, _ = seed_running(conn, audit)
    result = operations.cancel(ctx_with(conn, audit, config, OutcomeHost(unconfirmed())), item_id,
                               force=True)
    assert result.code == operations.EXIT_FAILED


# -- the confirmed path -----------------------------------------------------


def test_a_confirmed_stop_settles_the_item_and_the_session(conn, audit, config):
    item_id, _ = seed_running(conn, audit)
    result = operations.cancel(ctx_with(conn, audit, config, OutcomeHost(confirmed())), item_id,
                               force=True)
    assert result.code == operations.EXIT_OK
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    assert db.get_session(conn, SESSION).state is SessionState.LOST, (
        "FR-008: a stopped session must stop describing itself as running"
    )


def test_the_recorded_start_time_is_passed_to_the_boundary(conn, audit, config):
    """Without it the liveness check degrades to a bare existence test — the pid-reuse bug."""
    item_id, _ = seed_running(conn, audit)
    host = OutcomeHost(confirmed())
    operations.cancel(ctx_with(conn, audit, config, host), item_id, force=True)
    assert host.calls == [
        {"pid": PID, "socket": SOCKET, "scope": SCOPE, "expected_start": START}
    ]


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (confirmed("systemd_scope"), "confirmed gone"),
        (confirmed("already_gone"), "nothing left to stop"),
        (
            confirmed("process_group_signal", escalated=True),
            "reported success but the session was still running",
        ),
    ],
)
def test_the_report_says_which_of_the_three_things_happened(
    conn, audit, config, outcome, expected
):
    """K3. Escalation gets its own sentence: it is the evidence the fix is working."""
    item_id, _ = seed_running(conn, audit)
    result = operations.cancel(ctx_with(conn, audit, config, OutcomeHost(outcome)), item_id,
                               force=True)
    assert result.code == operations.EXIT_OK
    assert expected in "\n".join(result.lines)


def test_the_escalated_line_says_both_halves_of_what_happened(conn, audit, config):
    """K3. One sentence has to carry two facts, and neither is optional.

    "stopped via the process group" alone hides that the scope claimed to have done it —
    which is the observation that tells the maintainer this build caught issue #34. And
    the line must still say the session is stopped and confirmed, or an escalated success
    reads like a failure.
    """
    item_id, _ = seed_running(conn, audit)
    result = operations.cancel(
        ctx_with(conn, audit, config, OutcomeHost(confirmed("process_group_signal", True))),
        item_id,
        force=True,
    )
    line = "\n".join(result.lines)
    assert SCOPE in line and "reported success but the session was still running" in line
    assert "signalling the process group" in line
    assert "confirmed gone" in line
    assert f"item {item_id} is now interrupted" in line


def test_the_outcome_reaches_result_data_for_the_web_and_json_surfaces(conn, audit, config):
    item_id, _ = seed_running(conn, audit)
    result = operations.cancel(
        ctx_with(conn, audit, config, OutcomeHost(confirmed("process_group_signal", True))),
        item_id,
        force=True,
    )
    assert result.data["confirmed"] is True
    assert result.data["method"] == "process_group_signal"
    assert result.data["escalated"] is True


# -- K2: the exit spool can win the race ------------------------------------


def test_a_session_that_settled_itself_mid_cancel_is_not_transitioned_again(
    conn, audit, config
):
    """The daemon drains the exit spool in its own process while this runs.

    A worker killed by our own SIGTERM can have its exit record applied before we reach
    the settle. Forcing the transition then raises `IllegalTransition` and reports a
    perfectly successful cancel as a failure — the milestone 013 collision, in a new place.
    """
    item_id, row_id = seed_running(conn, audit)

    class SettlesDuringTerminate(OutcomeHost):
        def terminate(self, handle, scope=None, **kwargs):
            with db.transaction(conn):
                transition_session(
                    conn,
                    audit,
                    session_row_id=row_id,
                    target=SessionState.EXITED_ERROR,
                    reason="the spool drained while the cancel was in flight",
                )
                operations.transition_work_item(
                    conn,
                    audit,
                    item_id=item_id,
                    target=WorkItemState.INTERRUPTED,
                    reason="exit record applied by the daemon",
                )
            return super().terminate(handle, scope, **kwargs)

    result = operations.cancel(
        ctx_with(conn, audit, config, SettlesDuringTerminate(confirmed())), item_id, force=True
    )

    assert result.code == operations.EXIT_OK, "the session is gone; that is what was asked"
    assert db.get_session(conn, SESSION).state is SessionState.EXITED_ERROR, (
        "the exit record's own account is more informative than 'lost' and must survive"
    )
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED


def test_no_illegal_transition_is_ever_logged_by_a_cancel(conn, audit, config, layout):
    item_id, row_id = seed_running(conn, audit)
    with db.transaction(conn):
        transition_session(
            conn, audit, session_row_id=row_id, target=SessionState.LOST, reason="already gone"
        )
    operations.cancel(ctx_with(conn, audit, config, OutcomeHost(confirmed("already_gone"))),
                      item_id, force=True)
    audit.close()
    text = "\n".join(
        str(record) for record, _ in read_records(layout.log_dir) if record is not None
    )
    assert "IllegalTransition" not in text


def test_a_cancel_with_no_running_session_is_still_refused(conn, audit, config):
    item_id = seed_item(conn, state="active")
    result = operations.cancel(ctx_with(conn, audit, config, OutcomeHost(confirmed())), item_id,
                               force=True)
    assert result.code == operations.EXIT_FAILED
    assert "no running session" in "\n".join(result.lines)


def test_a_simulated_cancel_takes_the_same_branch_a_real_successful_one_does(
    conn, audit, config
):
    """FR-014, and quickstart Scenario 3.

    A simulated session has no process, so its pid is ``0`` by construction. Putting that
    through the real path's ``/proc`` observation would find nothing to confirm against
    and send every simulated cancel down the *failure* branch — the divergence between the
    simulated and real paths that contracts/boundaries.md exists to prevent. The whole
    point of this milestone is a `cancel` that fails when it cannot verify; that must not
    become "every dry-run cancel fails".
    """
    from robot_army.boundaries.dtach import SimulatedSessionHost

    item_id, _ = seed_running(conn, audit)
    host = SimulatedSessionHost(audit)
    result = operations.cancel(ctx_with(conn, audit, config, host), item_id, force=True)

    assert result.code == operations.EXIT_OK
    assert result.data["confirmed"] is True
    assert result.data["method"] == "simulated"
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED
    assert db.get_session(conn, SESSION).state is SessionState.LOST
    assert "the process group" not in "\n".join(result.lines), (
        "nothing was signalled; saying so would be the same class of claim this fixes"
    )


# -- 069 S-K1..S-K3: a refusal is not a stop, and must not be reported as one -----------
#
# A refusal is the third thing `terminate` can say. The other two are "it stopped" and
# "I signalled it and it survived"; this one is "the row named a pid I will not signal".
# Reporting it as either of the others tells the maintainer something false about what
# just happened to their machine — which, on 2026-08-31, was quite a lot.

REFUSAL = "the recorded pid is 1, which cannot be a session process: its process group is 1"


def refused(reason: str = REFUSAL) -> TerminationOutcome:
    return TerminationOutcome(
        confirmed=False,
        method="refused",
        refused_reason=reason,
        detail={"rungs": [], "signals_sent": 0},
    )


def test_a_refusal_changes_nothing_at_all(conn, audit, config):
    """S-K1. Same obligation as an unconfirmed stop, reached for a different reason."""
    item_id, _row_id = seed_running(conn, audit)
    before_item = db.get_work_item(conn, item_id)
    before_session = db.get_session(conn, SESSION)

    result = operations.cancel(
        ctx_with(conn, audit, config, OutcomeHost(refused())), item_id, force=True
    )

    assert result.code == operations.EXIT_FAILED
    assert db.get_work_item(conn, item_id) == before_item
    assert db.get_session(conn, SESSION) == before_session
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


def test_a_refusal_says_which_recorded_value_it_rejected(conn, audit, config):
    """S-K2. The next step is to inspect the row, so the message must hand it over."""
    item_id, _ = seed_running(conn, audit)
    result = operations.cancel(
        ctx_with(conn, audit, config, OutcomeHost(refused())), item_id, force=True
    )
    text = "\n".join(result.lines)

    assert SESSION in text
    assert "process group is 1" in text, "the reason travels verbatim; it is the whole message"
    assert str(item_id) in text


def test_a_refusal_is_not_reported_as_a_signal_that_failed(conn, audit, config):
    """S-K3, and the reason this branch exists at all.

    The unconfirmed-stop wording — "pid N is still running after signalling the process
    group" — is a *false statement* about a refusal: nothing was signalled, and whether
    the pid is running was never the question. Falling through to it would tell the
    maintainer their machine had just been signalled when it had not.
    """
    item_id, _ = seed_running(conn, audit)
    result = operations.cancel(
        ctx_with(conn, audit, config, OutcomeHost(refused())), item_id, force=True
    )
    text = "\n".join(result.lines)

    assert "still running after" not in text
    assert "could not confirm" not in text
    assert "stopped session" not in text


def test_a_refusal_is_visible_as_such_in_the_result_data(conn, audit, config):
    """The web action path renders from ``data``, not from the lines."""
    item_id, _ = seed_running(conn, audit)
    result = operations.cancel(
        ctx_with(conn, audit, config, OutcomeHost(refused())), item_id, force=True
    )

    assert result.data["refused"] is True
    assert result.data["refused_reason"] == REFUSAL
    assert result.data["method"] == "refused"
    assert result.data["confirmed"] is False


def test_a_refusal_writes_no_state_transition(conn, audit, config, layout):
    """Principle III from the other side: the *absence* is the evidence.

    No `state.session`, no `state.work_item`. A reader of the log alone must be able to
    see that nothing was settled, without inferring it from the lack of a later record.
    """
    item_id, _ = seed_running(conn, audit)
    operations.cancel(ctx_with(conn, audit, config, OutcomeHost(refused())), item_id, force=True)

    after = [
        record
        for record, _ in read_records(layout.log_dir)
        if record is not None and record.get("action") in ("state.session", "state.work_item")
    ]
    settled = [r for r in after if "cancel" in str(r.get("detail", {}))]
    assert settled == [], "a refusal settled something, which is the one thing it must never do"


# -- 069 S8/S-C10: a simulated session is never handed to the real host -----------------


class ExplodingHost:
    """The real host, standing in for the assertion "this must never be reached".

    A simulated row reaching here is the bug, so reaching here fails the test rather than
    returning something plausible that a later assertion would have to catch.
    """

    def terminate(self, handle, scope=None, **kwargs):
        raise AssertionError(
            f"a simulated session reached the real host: pid={handle.pid!r}. "
            "This is the path that signals the caller's own process group."
        )

    def attach_command(self, handle):
        return ["dtach", "-a", handle.socket_path]


def seed_simulated_running(conn, audit) -> int:
    """A row exactly as an ordinary simulated dispatch leaves it: pid 0, no start time."""
    item_id = seed_item(conn, state="active")
    with db.transaction(conn):
        row_id = db.insert_session(
            conn,
            work_item_id=item_id,
            session_id=SESSION,
            attempt=1,
            dry_run=True,
            host_socket=SOCKET,
        )
        db.update_session_columns(conn, row_id, pid=0, proc_start=None, scope=None)
        transition_session(
            conn, audit, session_row_id=row_id, target=SessionState.RUNNING, reason="seeded"
        )
    return item_id


def test_a_simulated_session_is_cancelled_by_the_simulated_host(conn, audit, config):
    """The go-live sequence, which needs no hand-edited database to reach.

    Dispatch at ``local`` leaves a row with ``dry_run=1`` and ``pid=0`` that no worker will
    ever close. Raise ``effect_level`` and restart — one line in config.toml — and the
    session host is now real, while the row is not. Before this, ``cancel`` routed that row
    to ``DtachHost.terminate`` with ``handle.pid = 0``, and ``getpgid(0)`` answers about
    *the caller*: the daemon's own process group, or the operator's shell.
    """
    item_id = seed_simulated_running(conn, audit)

    result = operations.cancel(
        ctx_with(conn, audit, config, ExplodingHost()), item_id, force=True
    )

    assert result.code == operations.EXIT_OK
    assert result.data["method"] == "simulated"
    assert result.data["confirmed"] is True
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED


def test_a_real_session_still_goes_to_the_real_host(conn, audit, config):
    """The routing reads the record, so it must not misread an ordinary one (SC-005)."""
    item_id, _ = seed_running(conn, audit)
    host = OutcomeHost(confirmed())

    result = operations.cancel(ctx_with(conn, audit, config, host), item_id, force=True)

    assert result.code == operations.EXIT_OK
    assert host.calls, "a non-simulated row must reach the wired session host"


def test_the_decision_comes_from_the_record_not_from_the_configured_level(conn, audit, config):
    """FR-012. The configuration at cancel time says nothing about what created the row."""
    item_id = seed_simulated_running(conn, audit)
    ctx = operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, level=EffectLevel.LIVE, host=ExplodingHost()),
        effect_level=EffectLevel.LIVE,
    )

    result = operations.cancel(ctx, item_id, force=True)

    assert result.code == operations.EXIT_OK
