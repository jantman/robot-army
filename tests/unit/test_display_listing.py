"""``list_by_var`` — every window carrying a marker, not the first (issue #138 follow-up).

The window sweep's correctness rests on three properties of this one method, and each of
them is a way the singular ``find_by_var`` would have been wrong for it:

* it returns **every** match, because a completed item may hold several windows — one per
  attempt that was resumed or restarted, all carrying the same marker;
* it returns nothing when nothing matches, rather than raising, because an idle machine is
  the ordinary case;
* it never returns a window that lacks the key, because a window this system did not open
  must not reach a decision about closing it.

Both implementations are exercised against the same cases. ``SimulatedDisplay`` is not a
test double — it is the production object wired in below ``no-remote``, which is what makes
a simulated run rehearse the whole decision path rather than skip it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from robot_army.boundaries import BoundaryError, DisplayHandle
from robot_army.boundaries.kitty import KittyDisplay, SimulatedDisplay
from robot_army.subproc import Completed


class FakeKitty(KittyDisplay):
    """A ``KittyDisplay`` whose ``kitty @ ls`` answer the test dictates.

    Subclassed at ``_ls`` rather than at ``_windows`` on purpose: ``_windows`` flattens
    kitty's os-window → tab → window nesting, and that flattening is part of what is being
    tested. A fake that returned a flat list would skip it.
    """

    def __init__(self, windows: list[dict[str, Any]]) -> None:
        self._payload = windows

    def _ls(self) -> list[dict[str, Any]]:
        # One os-window, two tabs, windows split across them — the shape kitty really
        # reports, so a caller that forgot to descend both levels fails here.
        half = len(self._payload) // 2
        return [
            {
                "tabs": [
                    {"windows": self._payload[:half]},
                    {"windows": self._payload[half:]},
                ]
            }
        ]


def window(window_id: int, **user_vars: str) -> dict[str, Any]:
    return {"id": window_id, "title": f"w{window_id}", "user_vars": dict(user_vars)}


def simulated(audit, windows: list[dict[str, Any]]) -> SimulatedDisplay:
    display = SimulatedDisplay(audit)
    for spec in windows:
        display.open(
            "/tmp",
            ["true"],
            spec["title"],
            spec["user_vars"],
            {},
        )
    return display


# -- KittyDisplay ------------------------------------------------------------


def test_every_matching_window_is_returned_not_just_the_first():
    """The property ``find_by_var`` cannot provide, and the reason this method exists.

    Two attempts of one item, both windows held open by ``--hold``, both carrying the same
    ``ra_item``. Closing only the first would leave a tab behind on every item that was
    ever resumed.
    """
    display = FakeKitty([window(1, ra_item="45"), window(2, ra_item="45")])

    found = display.list_by_var("ra_item")

    assert sorted(h.window_id for h in found) == [1, 2]
    assert {h.user_vars["ra_item"] for h in found} == {"45"}


def test_windows_with_different_values_all_come_back():
    """The sweep asks by *key* and decides on the value itself, so both must arrive."""
    display = FakeKitty([window(1, ra_item="45"), window(2, ra_item="54")])

    found = display.list_by_var("ra_item")

    assert {h.window_id: h.user_vars["ra_item"] for h in found} == {1: "45", 2: "54"}


def test_a_window_without_the_key_is_never_returned():
    """Everything the maintainer opened themselves. Filtered here rather than by the
    caller, so nothing this system did not open can reach a decision about closing it."""
    display = FakeKitty([window(1), window(2, something_else="x"), window(3, ra_item="45")])

    found = display.list_by_var("ra_item")

    assert [h.window_id for h in found] == [3]


def test_no_matches_is_an_empty_list_not_an_error():
    display = FakeKitty([window(1), window(2)])

    assert display.list_by_var("ra_item") == []


def test_no_windows_at_all_is_an_empty_list():
    display = FakeKitty([])

    assert display.list_by_var("ra_item") == []


def test_a_window_with_no_user_vars_key_at_all_is_tolerated():
    """kitty reports ``user_vars`` on every window today, but a payload without it must not
    raise — this is an undocumented interface and a worker upgrade must not take the daemon
    down."""
    display = FakeKitty([{"id": 1, "title": "w1"}, window(2, ra_item="45")])

    assert [h.window_id for h in display.list_by_var("ra_item")] == [2]


def test_the_handle_carries_the_title_and_every_user_var():
    display = FakeKitty([window(7, ra_item="45", other="keep")])

    handle = display.list_by_var("ra_item")[0]

    assert handle.window_id == 7
    assert handle.title == "w7"
    assert handle.user_vars == {"ra_item": "45", "other": "keep"}


def test_find_by_var_is_unchanged_and_still_answers_singly():
    """The other caller's contract must not have moved."""
    display = FakeKitty([window(1, ra_item="45"), window(2, ra_item="45")])

    assert display.find_by_var("ra_item", "45").window_id == 1
    assert display.find_by_var("ra_item", "99") is None


def test_the_flattening_descends_both_os_window_and_tab_levels():
    """Guards the guard: the fake splits its windows across two tabs, so a `list_by_var`
    that read only the first tab would pass every other test in this file."""
    display = FakeKitty([window(i, ra_item="45") for i in range(1, 5)])

    assert len(display.list_by_var("ra_item")) == 4


# -- SimulatedDisplay --------------------------------------------------------


def test_the_simulated_display_answers_from_the_windows_it_opened(audit):
    display = simulated(
        audit,
        [
            {"title": "a", "user_vars": {"ra_item": "45"}},
            {"title": "b", "user_vars": {"ra_item": "45"}},
            {"title": "c", "user_vars": {}},
        ],
    )

    found = display.list_by_var("ra_item")

    assert len(found) == 2
    assert {h.user_vars["ra_item"] for h in found} == {"45"}


def test_a_simulated_window_that_was_closed_is_no_longer_listed(audit):
    """Which is what makes the sweep idempotent against the simulated display too: a
    second pass finds nothing to do rather than closing the same window again."""
    display = simulated(audit, [{"title": "a", "user_vars": {"ra_item": "45"}}])
    handle = display.list_by_var("ra_item")[0]

    display.close(handle)

    assert display.list_by_var("ra_item") == []


def test_the_simulated_close_is_recorded_as_simulated(audit, layout):
    display = simulated(audit, [{"title": "a", "user_vars": {"ra_item": "45"}}])
    display.close(display.list_by_var("ra_item")[0])

    records = [
        json.loads(line)
        for path in layout.log_dir.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    closes = [r for r in records if r["action"] == "kitty.close_window"]
    assert len(closes) == 1
    assert closes[0]["simulated"] is True


@pytest.mark.parametrize("key", ["ra_item", "missing"])
def test_both_implementations_agree_on_an_empty_answer(audit, key):
    assert SimulatedDisplay(audit).list_by_var(key) == []
    assert FakeKitty([]).list_by_var(key) == []


# -- close() reports whether it acted (found in review of PR #141) -----------
#
# `windows_closed` counted every call that did not raise, so a window the maintainer had
# already closed was counted as a close the system performed. The fix was available for
# free: kitty answers this itself.


class ClosingKitty(KittyDisplay):
    """A ``KittyDisplay`` whose ``kitty @ close-window`` result the test dictates.

    Returns a real ``Completed`` rather than a stand-in object. A ``SimpleNamespace`` was
    used first and silently lacked ``timed_out``, so it could not have exercised the branch
    that tells a timeout apart from a missing window — a fake that is missing a field the
    production code reads is a fake that agrees with whatever the code happens to do.
    """

    def __init__(
        self, audit, *, returncode: int = 0, stderr: str = "", timed_out: bool = False
    ) -> None:
        self._audit = audit
        self._returncode = returncode
        self._stderr = stderr
        self._timed_out = timed_out
        self.calls: list[list[str]] = []

    def _kitty(self, args, *, timeout, action):
        self.calls.append(args)
        return Completed(
            argv=tuple(args),
            returncode=self._returncode,
            stdout="",
            stderr=self._stderr,
            duration=0.01,
            timed_out=self._timed_out,
        )


def test_close_returns_true_when_kitty_closed_a_window(audit):
    display = ClosingKitty(audit)

    assert display.close(DisplayHandle(window_id=52)) is True
    assert display.calls == [["close-window", "--match", "id:52"]]


def test_close_returns_false_when_no_window_matched(audit):
    """**Measured, not assumed**: `kitty @ close-window --match id:999999` exits **1** with
    ``No matching windows for expression``, not 0.

    ``_kitty`` passes ``check=False``, so that status was being discarded and an
    already-closed window read as a successful close. The signal was there the whole time.
    """
    display = ClosingKitty(
        audit, returncode=1, stderr="Error: No matching windows for expression: id:52"
    )

    assert display.close(DisplayHandle(window_id=52)) is False


def test_a_failed_close_records_the_terminals_own_words(audit, layout):
    """Principle III: a ``False`` with an unusual cause must stay reconstructable, so the
    reason is recorded rather than collapsed into a bare boolean."""
    ClosingKitty(
        audit, returncode=1, stderr="Error: No matching windows for expression: id:52"
    ).close(DisplayHandle(window_id=52))

    written = [
        json.loads(line)
        for path in layout.log_dir.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # ``audit.action`` writes an intent record and an outcome record; the answer is on the
    # second, and the pair is what makes an irreversible act reconstructable if the process
    # dies between them.
    closes = [
        r
        for r in written
        if r["action"] == "kitty.close_window" and r["kind"] == "outcome"
    ]
    assert len(closes) == 1
    assert closes[0]["detail"]["closed"] is False
    assert "No matching windows" in closes[0]["detail"]["output"]


def test_the_simulated_close_reports_the_same_distinction(audit):
    """The two implementations must agree, or a simulated run rehearses different arithmetic
    from a live one."""
    display = simulated(audit, [{"title": "a", "user_vars": {"ra_item": "45"}}])
    handle = display.list_by_var("ra_item")[0]

    assert display.close(handle) is True
    assert display.close(handle) is False, "closing it twice must not report two closes"


@pytest.mark.parametrize(
    ("returncode", "stderr", "timed_out"),
    [
        (1, "Error: connection refused", False),
        (2, "", False),
        (0, "", True),
        (1, "Error: No matching windows for expression: id:52", True),
    ],
    ids=["other-error", "no-output", "timeout", "timeout-with-the-right-words"],
)
def test_any_failure_that_is_not_a_missing_window_raises(audit, returncode, stderr, timed_out):
    """The distinction the caller's settling depends on, and the bug that made it necessary.

    ``close`` returning ``False`` tells the sweep "there is nothing here, mark this item
    answered for". Reporting a transient failure or a timeout that way would settle the item
    and leak its window for the life of the process, with nothing recorded as an error —
    which is exactly what happened before review caught it.

    The last case matters most: a timeout that happens to have captured the no-match message
    before the kill is still a timeout, and must not be read as a clean answer.
    """
    display = ClosingKitty(audit, returncode=returncode, stderr=stderr, timed_out=timed_out)

    with pytest.raises(BoundaryError):
        display.close(DisplayHandle(window_id=52))
