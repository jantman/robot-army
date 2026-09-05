"""The committed example must equal a fresh render.

This is the test that would have caught the file it replaces. ``share/config.example.toml``
existed for several milestones, was referenced from nowhere, and had fallen three sections
behind the loader before anybody noticed — because nothing ever compared it to anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from robot_army.exampleconfig import render

COMMITTED = Path(__file__).resolve().parents[2] / "share" / "config.example.toml"

REGENERATE = "uv run robot-army example-config --output share/config.example.toml --force"


def test_the_committed_example_exists():
    assert COMMITTED.is_file(), f"{COMMITTED} is missing; regenerate it with:\n  {REGENERATE}"


def test_the_committed_example_matches_the_generator():
    """Byte for byte. The failure message is the fix, because that is when it is read."""
    committed = COMMITTED.read_text(encoding="utf-8")
    fresh = render()
    if committed == fresh:
        return
    # A diff rather than a 400-line assertion dump: the useful information is which lines
    # moved, and pytest's own comparison of two long strings buries it.
    import difflib

    diff = "\n".join(
        difflib.unified_diff(
            committed.splitlines(),
            fresh.splitlines(),
            fromfile="share/config.example.toml (committed)",
            tofile="robot-army example-config (fresh)",
            lineterm="",
            n=2,
        )
    )
    pytest.fail(
        "share/config.example.toml is out of date with the generator.\n"
        f"Regenerate it:\n  {REGENERATE}\n\n{diff}"
    )


def test_the_committed_example_ends_in_exactly_one_newline():
    """A stray editor newline here would fail the comparison above with no useful message."""
    committed = COMMITTED.read_text(encoding="utf-8")
    assert committed.endswith("\n")
    assert not committed.endswith("\n\n")
