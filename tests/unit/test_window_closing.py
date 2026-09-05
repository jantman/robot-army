"""Closing a finished item's terminal windows (issue #138 follow-up).

Two halves, and the second is not a lesser concern than the first.

**Windows must close.** Every launch passes ``--hold``, so a window outlives its process by
design. Retirement ended the worker and the tab stayed — one per completed item, forever,
each looking alive until you click into it. `Display.close` had existed since M0 with no
caller.

**Windows must also *not* close.** ``--hold`` is there because a window that vanished
instantly destroyed the only evidence of a failed launch (M0 F11). This feature narrows that
behaviour, and narrowing it too far reintroduces the exact problem it was added to fix. So
the refusal cases below run ten passes rather than one, and the "window with no marker" case
stands in for every window the maintainer opened themselves.

The sharp decision is identity. ``sessions.window_id`` decides nothing: kitty numbers windows
per kitty process and restarts from 1 when kitty restarts, so a stored 50 can name an
unrelated window months later — the PID-reuse incident aimed at the maintainer's screen.
Identity is the ``ra_item`` marker and nothing else.

Contract: ``specs/20260905-145251-close-retired-tab/contracts/window-closing.md``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from tests.conftest import make_boundaries, seed_item, seed_session

from robot_army import reconcile
from robot_army.boundaries import BoundaryError, DisplayHandle
from robot_army.boundaries.kitty import SimulatedDisplay

REPO = "jantman/robot-army"


def open_window(display: Any, item_id: int | str | None, *, title: str = "ra-session") -> int:
    """A window as ``dispatch`` would have opened it, marked or deliberately not."""
    user_vars = {} if item_id is None else {reconcile.WINDOW_ITEM_VAR: str(item_id)}
    return display.open("/tmp", ["claude"], title, user_vars, {}).window_id


def finished_item(
    conn,
    *,
    state: str = "done",
    session_states: tuple[str, ...] = ("lost",),
    issue_number: int = 116,
) -> int:
    """A work item with however many ended (or running) attempts the case needs."""
    item = seed_item(conn, repo_key=REPO, issue_number=issue_number, state=state)
    for attempt, session_state in enumerate(session_states, start=1):
        seed_session(
            conn,
            item,
            state=session_state,
            session_id=f"sess-{item}-{attempt}",
            pid=1000 + attempt,
        )
    return item


def sweep(conn, audit, display: Any) -> int:
    return reconcile._close_finished_windows(
        conn, boundaries=make_boundaries(audit, display=display), audit=audit
    )


def full_pass(conn, audit, config, layout, display: Any):
    return reconcile.reconcile(
        conn,
        boundaries=make_boundaries(audit, display=display),
        audit=audit,
        config=config,
        layout=layout,
        registry_dir=layout.state_dir / "no-registry",
        proc_root=layout.state_dir / "no-proc",
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


# -- doubles for the failure paths -------------------------------------------


class ListRaises(SimulatedDisplay):
    def list_by_var(self, key: str) -> list[DisplayHandle]:
        raise BoundaryError("kitty ls failed: connection refused")


class CloseRaises(SimulatedDisplay):
    """Refuses to close one nominated window and closes every other normally.

    ``refuse`` is set *after* the windows exist rather than passed in, so the test never
    has to predict an id — an assertion that happens to hold because two instances number
    identically is one refactor away from silently testing nothing.
    """

    def __init__(self, audit) -> None:
        super().__init__(audit)
        self.refuse: int | None = None
        self.closed: list[int] = []

    def close(self, handle: DisplayHandle) -> None:
        if handle.window_id == self.refuse:
            raise BoundaryError("kitty close-window failed")
        self.closed.append(handle.window_id)
        super().close(handle)


class WindowVanished(SimulatedDisplay):
    """``close`` succeeds against a window the maintainer already closed.

    kitty's ``close-window --match id:`` exits zero for an id that no longer matches, so
    this is what the real boundary does — the point is that the sweep must not invent a
    failure the terminal never reported.
    """

    def close(self, handle: DisplayHandle) -> None:
        self._windows.pop(handle.window_id, None)


class CountingDisplay(SimulatedDisplay):
    def __init__(self, audit) -> None:
        super().__init__(audit)
        self.list_calls = 0

    def list_by_var(self, key: str) -> list[DisplayHandle]:
        self.list_calls += 1
        return super().list_by_var(key)


# -- US1: a finished item leaves no tabs behind ------------------------------


def test_a_done_items_window_is_closed(conn, audit):
    display = SimulatedDisplay(audit)
    item = finished_item(conn)
    open_window(display, item)

    assert sweep(conn, audit, display) == 1
    assert display.list_by_var(reconcile.WINDOW_ITEM_VAR) == []


def test_every_attempts_window_is_closed(conn, audit):
    """FR-002. An item that was resumed holds a window per attempt, all carrying the same
    marker — which is why the marker naming the *item* rather than the session is right,
    and why this needs no per-attempt logic."""
    display = SimulatedDisplay(audit)
    item = finished_item(conn, session_states=("lost", "exited_clean", "lost"))
    for _ in range(3):
        open_window(display, item)

    assert sweep(conn, audit, display) == 3
    assert display.list_by_var(reconcile.WINDOW_ITEM_VAR) == []


def test_an_item_with_a_running_session_keeps_its_windows(conn, audit):
    """FR-004. ``done`` with a live row is reachable: retirement has not fired yet, or the
    worker survived the termination attempt. Something may still be in that window."""
    display = SimulatedDisplay(audit)
    item = finished_item(conn, session_states=("lost", "running"))
    open_window(display, item)
    open_window(display, item)

    assert sweep(conn, audit, display) == 0
    assert len(display.list_by_var(reconcile.WINDOW_ITEM_VAR)) == 2


def test_a_done_item_with_no_session_rows_at_all_is_left_alone(conn, audit):
    """Contract W2's third condition. ``live_sessions`` answers the empty list both for
    "all of them finished" and for "there were never any" — a rebuilt database — and only
    the first is evidence that nothing is running."""
    display = SimulatedDisplay(audit)
    item = seed_item(conn, repo_key=REPO, state="done")
    open_window(display, item)

    assert sweep(conn, audit, display) == 0
    assert len(display.list_by_var(reconcile.WINDOW_ITEM_VAR)) == 1


def test_the_close_is_recorded(conn, audit, layout):
    """FR-012. Closing a window removes something from the maintainer's screen, so it is
    recorded — by the existing ``audit.action`` context, not by anything new."""
    display = SimulatedDisplay(audit)
    item = finished_item(conn)
    window_id = open_window(display, item)

    sweep(conn, audit, display)

    closes = records(layout, "kitty.close_window")
    assert len(closes) == 1
    assert closes[0]["target"] == str(window_id)


def test_the_sweep_is_idempotent(conn, audit, layout):
    """A closed window is simply absent from the next listing, which is what makes a sweep
    safe to run every 60 seconds forever and safe to interrupt at any point."""
    display = SimulatedDisplay(audit)
    item = finished_item(conn)
    open_window(display, item)

    assert sweep(conn, audit, display) == 1
    for _ in range(5):
        assert sweep(conn, audit, display) == 0

    assert len(records(layout, "kitty.close_window")) == 1


def test_a_full_pass_closes_the_window_and_counts_it(conn, audit, config, layout):
    display = SimulatedDisplay(audit)
    item = finished_item(conn)
    open_window(display, item)

    result = full_pass(conn, audit, config, layout, display)

    assert result.windows_closed == 1
    assert display.list_by_var(reconcile.WINDOW_ITEM_VAR) == []
    passes = records(layout, "reconcile.pass")
    assert passes[-1]["detail"]["windows_closed"] == 1


def test_a_session_retired_this_pass_loses_its_window_in_the_same_pass(
    conn, audit, config, layout
):
    """Contract W1's ordering, which is why the sweep sits after the session sweeps rather
    than before them. Retirement closes the row; this then sees an item with no live
    session and takes the window — one tick, not two."""
    from tests.conftest import write_proc
    from tests.unit.test_session_retirement import (
        KillingHost,
    )
    from tests.unit.test_session_retirement import (
        finished_item as live_finished_item,
    )

    registry = layout.state_dir / "registry"
    registry.mkdir(parents=True, exist_ok=True)
    proc = layout.state_dir / "proc"
    write_proc(proc, 1, starttime="1", exe="/usr/lib/systemd/systemd")

    display = SimulatedDisplay(audit)
    item = live_finished_item(conn, config, registry, proc)
    open_window(display, item)

    result = reconcile.reconcile(
        conn,
        boundaries=make_boundaries(audit, host=KillingHost(proc), display=display),
        audit=audit,
        config=config,
        layout=layout,
        registry_dir=registry,
        proc_root=proc,
    )

    assert result.retired == 1
    assert result.windows_closed == 1, "the window must go in the same pass as the kill"


# -- US1: the failure paths --------------------------------------------------


def test_a_listing_that_fails_is_recorded_and_the_pass_continues(
    conn, audit, config, layout
):
    """FR-013. Reconciliation never raises for an operational condition."""
    display = ListRaises(audit)
    item = finished_item(conn)
    open_window(display, item)

    result = full_pass(conn, audit, config, layout, display)

    assert result.windows_closed == 0
    errors = [r for r in records(layout, "window.list") if r["outcome"] == "error"]
    assert len(errors) == 1


def test_one_close_failing_does_not_stop_the_others(conn, audit, layout):
    """FR-013. One terminal refusing must not abandon the sweep."""
    item = finished_item(conn)
    display = CloseRaises(audit)
    stubborn = open_window(display, item)
    other = open_window(display, item)
    display.refuse = stubborn

    closed = sweep(conn, audit, display)

    assert closed == 1, "the refused window is not counted"
    assert display.closed == [other]
    failures = [r for r in records(layout, "window.close") if r["outcome"] == "error"]
    assert len(failures) == 1
    assert failures[0]["detail"]["window_id"] == stubborn


def test_a_window_that_had_already_gone_is_success_not_failure(conn, audit, layout):
    """FR-014. The maintainer closed it first; kitty exits zero for an id that no longer
    matches. Reporting a failure the terminal never reported would be its own small lie."""
    display = WindowVanished(audit)
    item = finished_item(conn)
    open_window(display, item)

    assert sweep(conn, audit, display) == 1
    assert [r for r in records(layout, "window.close") if r["outcome"] == "error"] == []


def test_the_display_is_never_consulted_when_nothing_qualifies(conn, audit):
    """Research R6, and the whole of the cost argument — a test about a call that must
    *not* happen.

    A sweep that listed unconditionally would raise on every pass on a machine with no
    kitty, writing ~1,440 failures a day, and would make the failure that *is* recorded
    meaningless. This is also what keeps the feature free on an idle machine.
    """
    display = CountingDisplay(audit)
    finished_item(conn, state="failed")
    finished_item(conn, state="active", issue_number=117)
    open_window(display, 1)

    assert sweep(conn, audit, display) == 0
    assert display.list_calls == 0, "the terminal must not be touched with nothing to close"


def test_the_display_is_consulted_once_when_something_qualifies(conn, audit):
    """One listing per pass, not one per candidate item (research R4)."""
    display = CountingDisplay(audit)
    for issue in (116, 136, 141):
        item = finished_item(conn, issue_number=issue)
        open_window(display, item)

    assert sweep(conn, audit, display) == 3
    assert display.list_calls == 1


# -- US2: failed and abandoned work keeps its window -------------------------


@pytest.mark.parametrize(
    "state", ["failed", "abandoned", "active", "interrupted", "awaiting_review", "ready"]
)
def test_only_a_done_item_ever_loses_a_window(conn, audit, state):
    """FR-003, across ten passes.

    Ten and not one: a build that closed the window on the second pass would satisfy a
    single-pass assertion, and that is exactly the shape a badly-placed "reconsider later"
    would take. ``failed`` and ``abandoned`` are the two the request named; the others are
    here because a rule written as "terminal" or "not running" rather than "done" would let
    them through.
    """
    display = SimulatedDisplay(audit)
    item = finished_item(conn, state=state)
    open_window(display, item)

    for _ in range(10):
        assert sweep(conn, audit, display) == 0

    assert len(display.list_by_var(reconcile.WINDOW_ITEM_VAR)) == 1


def test_a_failed_launchs_window_survives(conn, audit):
    """The M0 F11 case ``--hold`` exists for, asserted directly rather than inferred.

    A launch that failed leaves an item that never reaches ``done``, so its window is never
    a candidate — the hold's purpose is preserved by the ``done`` gate rather than by a
    second rule that could drift from it.
    """
    display = SimulatedDisplay(audit)
    item = seed_item(conn, repo_key=REPO, state="failed")
    seed_session(conn, item, state="lost", session_id="never-started", pid=None)
    open_window(display, item, title="ra-robot-army-116")

    for _ in range(10):
        assert sweep(conn, audit, display) == 0
    assert len(display.list_by_var(reconcile.WINDOW_ITEM_VAR)) == 1


def test_a_window_with_no_marker_is_never_touched(conn, audit):
    """FR-008. Stands in for every window the maintainer opened themselves — an editor, a
    build log, a shell in the same worktree. The sweep must not act on it under any item
    state, and `list_by_var` never even returns it."""
    display = SimulatedDisplay(audit)
    finished_item(conn)
    open_window(display, None, title="~/GIT/robot-army")

    for _ in range(10):
        assert sweep(conn, audit, display) == 0
    assert len(display._windows) == 1


def test_a_marker_naming_an_unknown_item_is_left_alone(conn, audit):
    """FR-009. A rebuilt database, or simulated rows purged. An unidentifiable window is
    never closed on a guess."""
    display = SimulatedDisplay(audit)
    finished_item(conn)
    open_window(display, 99999)

    assert sweep(conn, audit, display) == 0
    assert len(display.list_by_var(reconcile.WINDOW_ITEM_VAR)) == 1


@pytest.mark.parametrize("marker", ["not-a-number", "", "45.0", "45x"])
def test_a_marker_that_is_not_an_integer_is_left_alone_and_does_not_raise(
    conn, audit, marker
):
    display = SimulatedDisplay(audit)
    finished_item(conn)
    open_window(display, marker)

    assert sweep(conn, audit, display) == 0
    assert len(display.list_by_var(reconcile.WINDOW_ITEM_VAR)) == 1


def test_only_the_qualifying_items_windows_go(conn, audit):
    """The discriminating case: two items, two windows, one terminal state each."""
    display = SimulatedDisplay(audit)
    done = finished_item(conn, issue_number=116)
    failed = finished_item(conn, state="failed", issue_number=136)
    open_window(display, done)
    failed_window = open_window(display, failed)

    assert sweep(conn, audit, display) == 1

    remaining = display.list_by_var(reconcile.WINDOW_ITEM_VAR)
    assert [h.window_id for h in remaining] == [failed_window]


def test_an_item_that_failed_then_succeeded_loses_its_earlier_window(conn, audit):
    """The one place this feature deliberately narrows what ``--hold`` preserves.

    An item that failed, was retried, and later reached ``done`` loses the failed attempt's
    window too. Asserted rather than left to be discovered: the item finished, and the
    failure is still in the audit log and the transcript, but a reader of the spec should
    be able to find this decision recorded somewhere it is checked.
    """
    display = SimulatedDisplay(audit)
    item = finished_item(conn, session_states=("exited_error", "lost"))
    open_window(display, item)  # the failed attempt
    open_window(display, item)  # the one that succeeded

    assert sweep(conn, audit, display) == 2


# -- US3: the by-hand route converges ----------------------------------------


def test_stopping_a_done_items_session_by_hand_ends_the_same_way(conn, audit, config):
    """The rule is about the work, not the route — which is why ``operations.cancel`` needed
    no change to satisfy this."""
    from tests.unit.test_cancel import OutcomeHost

    from robot_army import operations
    from robot_army.boundaries import TerminationOutcome
    from robot_army.effects import EffectLevel

    display = SimulatedDisplay(audit)
    item = seed_item(conn, repo_key=REPO, state="done")
    seed_session(conn, item, state="running", session_id="by-hand", pid=4321)
    open_window(display, item)

    host = OutcomeHost(TerminationOutcome(confirmed=True, method="systemd_scope"))
    ctx = operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, host=host, display=display),
        effect_level=EffectLevel.LIVE,
    )
    operations.cancel(ctx, item, force=True)

    assert sweep(conn, audit, display) == 1


def test_stopping_a_failed_items_session_by_hand_keeps_its_window(conn, audit, config):
    """The route does not change the answer in either direction."""
    from tests.unit.test_cancel import OutcomeHost

    from robot_army import operations
    from robot_army.boundaries import TerminationOutcome
    from robot_army.effects import EffectLevel

    display = SimulatedDisplay(audit)
    item = seed_item(conn, repo_key=REPO, state="failed")
    seed_session(conn, item, state="running", session_id="by-hand-failed", pid=4322)
    open_window(display, item)

    host = OutcomeHost(TerminationOutcome(confirmed=True, method="systemd_scope"))
    ctx = operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, host=host, display=display),
        effect_level=EffectLevel.LIVE,
    )
    operations.cancel(ctx, item, force=True)

    assert sweep(conn, audit, display) == 0
    assert len(display.list_by_var(reconcile.WINDOW_ITEM_VAR)) == 1
