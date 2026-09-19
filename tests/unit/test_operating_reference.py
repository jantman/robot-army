"""``docs/guide/operating.md`` is checked against the program it documents (issue #179).

The page is the one reached for when something is wrong, and before this it could not answer
"what can I do from ``interrupted``?" or "what are the subcommands?" — it was 593 lines of
continuous prose about *why*. Rewritten as tables, it can answer both, and this file is what
stops those tables drifting away from the code, which is the failure mode a reference page
has and a narrative page does not.

Both checks read the **program**, never a list kept here. A copy of the states or the verbs
in this file would be a second source of truth that goes stale in exactly the direction that
makes this test pass while the page is wrong.

The tables are parsed rather than the page being searched for substrings, because the
guarantee wanted is *completeness* — that no state and no command is **missing** — and a
substring search cannot see an absence.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from robot_army.cli import build_parser
from robot_army.states import WorkItemState

PAGE = Path(__file__).resolve().parents[2] / "docs" / "guide" / "operating.md"

#: The only values the ``Where`` column may hold. A fourth would be a distinction the page
#: makes and the reader has to work out.
REACHABILITY = {"Web", "Terminal", "Both"}

#: Every situation the page promises a recipe for (FR-022). Named here because these are a
#: requirement of the spec rather than a fact about the code — there is nothing to derive
#: them from, so this list *is* the source and the page must satisfy it.
RECIPES = (
    "start this item over",
    "item is stuck",
    "running that should not be",
    "daemon looks dead",
    "disk is full",
)


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def rows_under(heading: str) -> list[list[str]]:
    """Every markdown table row beneath ``heading``, up to the next heading of that level.

    Cells are returned already stripped of backticks, because the page writes commands and
    states as code spans and the comparison is with the bare name.
    """
    text = page()
    start = text.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    rest = text[start + len(heading) :]
    following = re.search(rf"^#{{1,{level}}} ", rest, re.MULTILINE)
    section = rest[: following.start()] if following else rest

    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [cell.strip().strip("*") for cell in line.strip("|").split("|")]
        if cells and cells[0].lower() in ("state", "command", "path", "kind", "it says", "on the page", "for", "mitigation"):
            continue  # a header row
        rows.append([cell.replace("`", "").strip() for cell in cells])
    return rows


def subcommands() -> set[str]:
    """Every verb the parser defines, with a nested subcommand written as ``parent child``.

    Read from :func:`build_parser` so a verb added tomorrow fails this test on the day it is
    added, which is the whole point.
    """
    names: set[str] = set()
    parser = build_parser()
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, sub in action.choices.items():
            nested = [
                child
                for inner in sub._actions
                if isinstance(inner, argparse._SubParsersAction)
                for child in inner.choices
            ]
            if nested:
                names.update(f"{name} {child}" for child in nested)
            else:
                names.add(name)
    return names


def documented_commands() -> set[str]:
    """The first cell of every command-table row, reduced to the verb it names.

    A row may document a variant — ``reset <id> --force`` — and a variant documents its verb.
    ``hold / unhold`` is one row for two verbs, because they are one decision.
    """
    documented: set[str] = set()
    for heading in ("### Work items", "### The machine", "### Repositories, disk, and the board"):
        for row in rows_under(heading):
            cell = row[0]
            for variant in cell.split(" / "):
                # Strip arguments and flags: "worktree remove <id>" → "worktree remove".
                words = [
                    word
                    for word in variant.split()
                    if not word.startswith(("<", "[", "--"))
                ]
                if words:
                    documented.add(" ".join(words))
    return documented


# -- the state table --------------------------------------------------------


def test_every_state_is_in_the_state_table():
    documented = {row[0] for row in rows_under("## States")}
    assert documented == {str(state) for state in WorkItemState}


def test_the_state_table_says_what_each_state_can_do_and_become():
    for row in rows_under("## States"):
        assert len(row) == 4, f"{row[0]} has {len(row)} cells, not state/means/can/becomes"
        state, means, can, becomes = row
        assert means, f"{state} has no meaning"
        assert can, f"{state} says nothing about what you can do from it"
        assert becomes, f"{state} says nothing about what it becomes"


def test_reset_is_offered_from_every_state_that_accepts_it():
    """The page and the operation must agree about where the new verb is legal."""
    from robot_army.operations import RESETTABLE_STATES

    offers_reset = {row[0] for row in rows_under("## States") if "reset" in row[2]}
    assert offers_reset == {str(state) for state in RESETTABLE_STATES}


def test_the_terminal_states_are_shown_as_terminal():
    becomes = {row[0]: row[3] for row in rows_under("## States")}
    assert becomes["done"] == "—"
    assert becomes["abandoned"] == "—"


# -- the command tables -----------------------------------------------------


def test_every_subcommand_is_in_a_command_table():
    missing = subcommands() - documented_commands()
    assert not missing, f"these commands exist and are not documented: {sorted(missing)}"


def test_no_command_table_row_names_something_that_does_not_exist():
    invented = documented_commands() - subcommands()
    assert not invented, f"these are documented and are not commands: {sorted(invented)}"


def test_every_command_row_says_where_it_is_reachable():
    for heading in ("### Work items", "### The machine", "### Repositories, disk, and the board"):
        for row in rows_under(heading):
            assert len(row) == 5, f"{row[0]} has {len(row)} cells, not five"
            assert row[4] in REACHABILITY, f"{row[0]} says {row[4]!r}, not one of {REACHABILITY}"


def test_a_command_row_says_what_it_refuses():
    """The column that makes the table worth opening: knowing in advance what will stop you."""
    for heading in ("### Work items", "### The machine", "### Repositories, disk, and the board"):
        for row in rows_under(heading):
            assert row[3], f"{row[0]} says nothing about what it refuses"


# -- the recipes ------------------------------------------------------------


@pytest.mark.parametrize("situation", RECIPES)
def test_the_page_has_a_recipe_for(situation):
    headings = re.findall(r"^### (.+)$", page(), re.MULTILINE)
    assert any(situation in heading for heading in headings), (
        f"no recipe heading covers {situation!r}; found {headings}"
    )


def test_the_recipes_come_before_the_tables():
    """They are why the page is open, and a reference is read from the top."""
    text = page()
    assert text.index("## Recipes") < text.index("## States") < text.index("## Commands")
