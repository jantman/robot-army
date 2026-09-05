"""``_resolve_closed_issues`` is the only thing that makes a work item ``done`` (#138).

This is not a style rule. It is the whole of retirement's precondition.

`contracts/session-retirement.md` C2 rule 1 says a session is a candidate for retirement
when its work item is ``done`` — and that is only a safe test because reaching ``done``
*means* the source issue was observed closed, which is the one fact that makes ending a
still-running worker the right thing to do. The maintainer agreed to "retire when the work
has been accepted", not to "retire when some code path decided the item was finished".

Three legal edges lead into ``done`` (from ``active``, ``awaiting_review`` and
``interrupted``), and today every one of them is travelled by the same function. A second
writer added later would silently widen retirement to whatever that new route means, with
no test failing and no line of the spec contradicted. So the invariant is asserted here
rather than trusted, in the same shape as the existing single-site assertions for the
session-host discriminator and for the effect level.

If this test fails because a second route to ``done`` was added deliberately: do not
delete it. Re-read C2 rule 1 and decide what retirement's precondition should become, then
encode that decision here.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "robot_army"

#: ``target=WorkItemState.DONE`` is how a transition names where it is going. Reads of the
#: value — comparisons, list membership, a SQL parameter — are not writes and are excluded
#: by requiring the ``target=`` keyword, which is ``transition_work_item``'s own signature.
WRITE = re.compile(r"target\s*=\s*WorkItemState\.DONE")


def write_sites() -> list[str]:
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if WRITE.search(line):
                found.append(f"{path.relative_to(SRC)}:{number}")
    return found


def test_exactly_one_place_moves_a_work_item_to_done():
    sites = write_sites()

    # Deliberately not pinned to a line number: this must fail when a *second* writer
    # appears, not every time the one writer moves down the file.
    assert len(sites) == 1, (
        f"expected exactly one writer of WorkItemState.DONE, found {len(sites)}: {sites}. "
        "See specs/20260905-121903-retire-finished-sessions/contracts/session-retirement.md "
        "C8 — retirement treats `done` as meaning 'the source issue was observed closed', "
        "and a second route to `done` widens which workers get killed without saying so."
    )


def test_that_one_place_is_the_closed_issue_pass():
    """Naming it, so the failure above is actionable rather than merely alarming."""
    source = (SRC / "reconcile.py").read_text(encoding="utf-8")
    before, _, after = source.partition("def _resolve_closed_issues(")
    assert after, "_resolve_closed_issues has been renamed or removed"

    # The next top-level def ends the function. Everything between is its body.
    body = re.split(r"\ndef ", after, maxsplit=1)[0]

    assert WRITE.search(body), (
        "the sole writer of WorkItemState.DONE is no longer inside _resolve_closed_issues"
    )
    assert not WRITE.search(before), "something above _resolve_closed_issues writes DONE"


def test_the_transition_table_still_admits_only_the_three_known_edges():
    """A fourth edge into ``done`` would be a new route even if no code took it yet."""
    from robot_army.states import WORK_ITEM_TRANSITIONS, WorkItemState

    into_done = {
        source for source, target in WORK_ITEM_TRANSITIONS if target is WorkItemState.DONE
    }

    assert into_done == {
        WorkItemState.ACTIVE,
        WorkItemState.AWAITING_REVIEW,
        WorkItemState.INTERRUPTED,
    }, f"the edges into `done` changed: {sorted(str(s) for s in into_done)}"
