"""Rendering configured instructions into the guidance block.

The assertion that matters most here is about **position**, and it looks like a formatting
test until you know why it exists. ``GUIDANCE`` ends with "Where any instruction above this
paragraph conflicts with this one, the instruction above wins." That sentence is how the
block defers to a repository's own ``.claude/robot-army.md``, which ``prompt.compose`` puts
above it, and its scope is literally *above this paragraph*. Instructions appended after it
would sit outside the precedence rule the block advertises — FR-015 would be false by
construction while every other test still passed. Hence
:func:`test_closing_sentence_is_still_last`, which is cheap and is the only thing standing
between a tidy-looking refactor and a silently broken guarantee.

Milestone 039, research R4.
"""

from __future__ import annotations

from robot_army import speckit
from robot_army.config import CommandInstruction


def instruction(command: str, text: str) -> CommandInstruction:
    return CommandInstruction(command=command, text=text, source=f"[speckit.commands] {command}")


IMPLEMENT = instruction("implement", "push the branch to origin and open a PR.")


def test_no_instructions_returns_the_constant_unchanged() -> None:
    """FR-013. Not merely equal — the same object, so nothing was rebuilt."""
    assert speckit.guidance() is speckit.GUIDANCE
    assert speckit.guidance(()) is speckit.GUIDANCE


def test_guidance_constant_is_its_two_halves() -> None:
    """The split exists so rendering never has to slice the constant back apart."""
    assert speckit.GUIDANCE == speckit.GUIDANCE_BODY + "\n\n" + speckit.GUIDANCE_CLOSING


def test_instruction_is_named_against_its_command() -> None:
    rendered = speckit.guidance((IMPLEMENT,))

    assert "`/speckit-implement`:" in rendered
    assert "push the branch to origin and open a PR." in rendered


def test_closing_sentence_is_still_last() -> None:
    """FR-015 — see the module docstring. This is the load-bearing one."""
    rendered = speckit.guidance((IMPLEMENT,))

    assert rendered.endswith(speckit.GUIDANCE_CLOSING)
    assert rendered.index("`/speckit-implement`:") < rendered.index(speckit.GUIDANCE_CLOSING)


def test_body_is_unchanged_and_comes_first() -> None:
    rendered = speckit.guidance((IMPLEMENT,))

    assert rendered.startswith(speckit.GUIDANCE_BODY)


def test_lead_in_appears_once_and_frames_the_instruction_as_additional() -> None:
    """FR-012: a configured `specify` instruction must not read as replacing the issue."""
    rendered = speckit.guidance(
        (instruction("specify", "commit the spec."), IMPLEMENT),
    )
    # Collapsed, because both clauses wrap across a line and the wrapping is not the point.
    collapsed = " ".join(rendered.split())

    assert rendered.count(speckit.INSTRUCTIONS_LEAD) == 1
    assert "in addition to, not instead of" in collapsed
    # The block's own sentence about what /speckit-specify takes is untouched.
    assert "the issue below is the input to `/speckit-specify`" in collapsed


def test_text_is_carried_verbatim() -> None:
    """FR-009. Backticks, quotation marks, and two paragraphs, unwrapped and unindented."""
    text = (
        'Use `/answer-reviews` and repeat until the review says "No issues found.".\n'
        "\n"
        "Do not force-push over someone else's commit."
    )
    rendered = speckit.guidance((instruction("implement", text),))

    assert text in rendered


def test_unconfigured_commands_leave_no_trace() -> None:
    """FR-010. No placeholder, no empty heading, no "none" line."""
    rendered = speckit.guidance((IMPLEMENT,))

    for absent in ("specify", "plan", "tasks"):
        assert f"`/speckit-{absent}`:" not in rendered


def test_rendering_is_deterministic() -> None:
    both = (instruction("specify", "commit it."), IMPLEMENT)

    assert speckit.guidance(both) == speckit.guidance(both)


def test_all_four_configured_still_ends_with_the_closing_sentence() -> None:
    """FR-015 at full load — the sentence must not drift off the end as the block grows."""
    rendered = speckit.guidance(
        tuple(instruction(name, f"do {name}.") for name in ("specify", "plan", "tasks", "implement"))
    )

    assert rendered.endswith(speckit.GUIDANCE_CLOSING)
    assert [line for line in rendered.splitlines() if line.endswith("`:")] == [
        "`/speckit-specify`:",
        "`/speckit-plan`:",
        "`/speckit-tasks`:",
        "`/speckit-implement`:",
    ]


def test_spacing_is_the_same_for_one_instruction_and_for_four() -> None:
    """One blank line between every element, so multi-paragraph text reads correctly."""
    one = speckit.guidance((IMPLEMENT,))
    four = speckit.guidance(
        tuple(instruction(name, f"do {name}.") for name in ("specify", "plan", "tasks", "implement"))
    )

    for rendered in (one, four):
        assert "\n\n\n" not in rendered
        assert f"{speckit.INSTRUCTIONS_LEAD}\n\n`/speckit-" in rendered


def test_a_command_cleared_by_a_repository_is_simply_absent() -> None:
    """US4 seen from the renderer: resolution drops it, so there is nothing to render.

    The renderer has no notion of "cleared" — an override to nothing resolves to no
    instruction at all, which is why :class:`CommandInstruction` can promise a non-empty
    ``text`` and no consumer has to check for a blank one.
    """
    rendered = speckit.guidance((instruction("plan", "commit the plan."),))

    assert "`/speckit-plan`:" in rendered
    assert "`/speckit-implement`:" not in rendered
