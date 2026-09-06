"""The width rules, asserted against the stylesheet itself.

``APP_CSS`` is a module constant with no seam in front of it — no theme object, no
per-request assembly, nothing to inject a fake into. The rules *are* the feature, so the
string is what gets asserted. That is unusual enough to say out loud, along with its one
weakness: a test that matches text cannot see whether a browser agrees. The measurements
that answer that question were taken in one, and are recorded in ``research.md``; what these
tests defend is that the rules those measurements chose are still the rules in the file.

The pairing is the point. Half of what is asserted here is what this feature *added*
(issue #148: tables confined to a limit chosen for paragraphs). The other half is what it
must not remove — ``overflow-x: auto``, ``table { width: 100% }``, ``th { white-space:
nowrap }`` — because those three are what let a nine-column table be read on a phone, and
the change that would break them is exactly the change that looks like tidying up after
this one.
"""

from __future__ import annotations

import re

import pytest
from tests.conftest import seed_item, seed_session

from robot_army import db
from robot_army.web import html

#: The stylesheet with its comments stripped. Every assertion below runs against this, so a
#: rule that exists only inside a ``/* ... */`` cannot make a test pass.
CSS = re.sub(r"/\*.*?\*/", "", html.APP_CSS, flags=re.DOTALL)


def rule(selector: str, css: str = CSS) -> str:
    """The declarations of the first rule whose selector list is exactly ``selector``.

    Exact rather than substring, because ``main`` and ``main p`` are different rules and a
    substring match would happily read one while claiming to read the other.
    """
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if " ".join(match.group(1).split()) == selector:
            return match.group(2)
    raise AssertionError(f"no rule in the stylesheet has the selector {selector!r}")


def declared(selector: str, prop: str, css: str = CSS) -> str | None:
    """One declaration's value from a rule, or ``None`` if the rule does not set it."""
    for part in rule(selector, css).split(";"):
        name, _, value = part.partition(":")
        if name.strip() == prop:
            return value.strip()
    return None


def prose_rule() -> tuple[str, str]:
    """The rule that caps prose: its selector list and its declarations.

    Found by what it does rather than by its exact selector text, so that adding a new kind
    of prose to the list — which is expected — does not fail a test about widths.
    """
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
        selector = " ".join(match.group(1).split())
        if "var(--measure)" in match.group(2) and selector.startswith("main "):
            return selector, match.group(2)
    raise AssertionError("no rule caps anything inside main at var(--measure)")


# -- the two widths ---------------------------------------------------------


def test_prose_measure_and_page_bound_are_two_different_values():
    """One value wearing two names would be the bug this feature fixes, undetected.

    The whole change is the claim that a paragraph and a table want different widths. If
    ``--measure`` and ``--page`` were ever set to the same thing, every rule below would
    still parse, every other test here would still pass, and ``/active`` would be back to
    half a window.
    """
    root = rule(":root")
    measure = declared(":root", "--measure")
    page = declared(":root", "--page")
    assert measure, f"--measure is not defined on :root; got {root!r}"
    assert page, f"--page is not defined on :root; got {root!r}"
    assert measure != page, "the prose measure and the page bound must not be the same value"
    assert float(measure.rstrip("rem")) < float(page.rstrip("rem")), (
        "the page bound must be the larger of the two: prose is read inside the page, "
        "not the other way round"
    )


def test_the_widths_are_not_redefined_by_the_light_palette():
    """They are lengths, not colours, and the light block exists only to swap colours.

    Repeating them there would mean a monitor in a lit room laying out differently from the
    same monitor in a dark one — and the one place nobody would look for it.
    """
    light = re.search(r"@media \(prefers-color-scheme: light\) \{(.*?)\n\}", CSS, flags=re.DOTALL)
    assert light, "the light-scheme block is gone"
    assert "--measure" not in light.group(1)
    assert "--page" not in light.group(1)


# -- the content area -------------------------------------------------------


def test_main_is_bounded_by_the_page_and_not_by_the_prose_measure():
    """This one line is issue #148.

    ``main { max-width: 60rem }`` put a paragraph's limit around a nine-column table: on a
    1920-pixel window the table rendered at 928 pixels with 480 of nothing on each side.
    A regression to the measure here costs every table in the interface, on every route.
    """
    assert declared("main", "max-width") == "var(--page)"
    assert declared("main", "min-width") is None, (
        "a minimum width on the content area would push the page wider than a phone's "
        "viewport, which is the one thing this feature must not do"
    )


def test_prose_is_capped_at_the_measure():
    """Widening the page without this would trade a table problem for a text problem.

    The selector list is split into its members and compared whole. Substring matching is
    the obvious way to write this and it does not work: ``main p`` is a substring of
    ``main pre``, ``main ul`` of ``main ul.kv``, ``main .card`` of ``main .cards``. Changing
    the rule's ``main p`` to ``main pre`` drops the paragraph cap entirely — the exact
    failure this test exists to catch — and a substring version of it passes anyway. That is
    not hypothetical; it was found by mutating the stylesheet and watching all fourteen tests
    here go green (PR #153 review).
    """
    selector, declarations = prose_rule()
    assert "max-width: var(--measure)" in " ".join(declarations.split())
    capped = [part.strip() for part in selector.split(",")]
    for element in ("p", "ul", "dl", ".banner", ".card", ".record", ".filters"):
        assert f"main {element}" in capped, (
            f"{element} is prose and is not capped; on a 1920-pixel window it would be "
            f"read across the whole monitor. The rule caps: {capped}"
        )


def test_the_prose_cap_reaches_any_nesting_depth():
    """A child combinator here would cap five things and miss the rest.

    The audit records are inside ``#content``, the field list on an item page is inside a
    ``dl`` that is itself nested, and the ``/queue`` repositories block wraps its contents
    in a plain ``div``. Descendant selectors are not an accident of style.
    """
    selector, _ = prose_rule()
    assert ">" not in selector


# -- the table container ----------------------------------------------------


def test_the_table_container_takes_the_width_its_content_needs():
    """Without this, widening the page stretches every table to fill it.

    Including the two-column state-history table on an item page, which would put six
    characters at the left edge of a 1920-pixel window and eleven at the right.
    """
    assert declared(".scroll", "width") == "fit-content"
    assert declared(".scroll", "max-width") == "100%"


def test_the_table_container_still_scrolls():
    """The load-bearing half, asserted next to the change that would tempt its removal.

    ``max-width: 100%`` caps the container at the space available. On a phone that is 343
    pixels and a nine-column table does not fit. ``overflow-x: auto`` is what makes the
    table scroll inside its own box instead of the page scrolling sideways — which is
    milestone 002's SC-013, and the reason ``html.table()`` wraps anything at all.
    """
    assert declared(".scroll", "overflow-x") == "auto"


def test_tables_still_fill_their_container():
    """``width: 100%`` does two jobs, and the second one is easy to miss.

    It fills the container — which, now that the container is shrink-to-fit, means the
    table sizes to its own content. And when the content needs more than the container has,
    the table grows past it rather than compressing, which is what gives the container
    something to scroll.
    """
    assert declared("table", "width") == "100%"


def test_headers_still_refuse_to_wrap():
    """Removing this breaks the phone while every other test here still passes.

    ``white-space: nowrap`` on a header cell is part of what pushes a wide table past its
    container. Let the headers wrap and the table narrows to fit instead, the container
    stops scrolling, and nine columns are squeezed into 343 pixels with nothing to say so.
    """
    assert declared("th", "white-space") == "nowrap"


def test_nothing_this_feature_added_can_bind_below_the_prose_measure():
    """Why a string test can stand in for a measurement it cannot take.

    SC-005 asks that a 390-pixel viewport render exactly as it did before. That is a pixel
    claim, and it was checked in a browser (research.md). What can be checked here is the
    reason it holds: every width this feature introduced is an upper bound, and an upper
    bound of 60rem or 120rem cannot narrow a 390-pixel viewport that is already below both.
    The container's ``fit-content`` is bounded by ``max-width: 100%``, which is the space
    available and never more.

    If a later edit adds a ``min-width`` or a fixed ``width`` to any of the three, that
    reasoning stops holding and this test is what says so.
    """
    _, prose = prose_rule()
    assert "min-width" not in prose
    assert declared("main", "min-width") is None
    assert declared(".scroll", "min-width") is None
    assert declared(".scroll", "width") == "fit-content", (
        "a fixed width here would be a floor as well as a ceiling"
    )


# -- the markup the rules act on --------------------------------------------


@pytest.fixture
def populated(conn):
    """Rows on every view that has a table, because an empty view renders no table at all.

    Each view swaps its table for a "nothing here" note when it has no rows, so a test that
    seeded nothing would asserting nothing and pass.
    """
    active = seed_item(conn, issue_number=101, title="Fix the thing", state="active")
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, active, worktree_path="/w/demo/issue-101", branch="robot-army/issue-101"
        )
    seed_session(conn, active, state="running")
    seed_item(conn, issue_number=102, title="Queued work", state="ready")
    # ``failed`` is what /queue lists as blocked — the state name and the heading differ.
    seed_item(conn, issue_number=103, title="Failed work", state="failed")
    return active


def assert_every_table_is_wrapped(body: str, path: str) -> None:
    """The rules above reach a table only if the table is inside ``div.scroll``.

    ``html.table()`` wraps one, and it is the only thing in the interface that builds a
    ``<table>``. That is the whole basis of the layout, and it is an invariant of the
    *markup* rather than of the stylesheet — so it is asserted against rendered pages. A
    page that hand-rolled a table would obey none of this, silently, and this is what
    would catch it.
    """
    tables = body.count("<table")
    assert tables, f"{path} rendered no table, so this test asserted nothing"
    assert body.count('<div class="scroll">') >= tables, (
        f"{path} renders {tables} tables but fewer scroll containers: something built a "
        f"table outside html.table()"
    )
    # Position, not just count: a container elsewhere on the page would satisfy a count.
    for match in re.finditer(r"<table", body):
        before = body[: match.start()]
        assert before.rstrip().endswith('<div class="scroll">'), (
            f"a table on {path} is not immediately inside a scroll container"
        )


@pytest.mark.parametrize("path", ["/active", "/queue", "/item/1"])
def test_every_table_on_the_item_views_is_wrapped(web, populated, path):
    assert_every_table_is_wrapped(web.get(path).text, path)


def test_every_table_on_the_cards_view_is_wrapped(board_web, conn):
    """/cards needs its own fixture: without a board configured the view renders a note
    instead of a table, and would assert nothing at all."""
    with db.transaction(conn):
        db.insert_card(
            conn,
            board_id="board-1",
            card_id="c1",
            card_url="https://trello.com/c/c1",
            title="A card that names no repository",
            body="",
            dry_run=False,
        )
    assert_every_table_is_wrapped(board_web.get("/cards").text, "/cards")
