"""The prompt block: where it sits, that it is fixed, and that its absence changes nothing.

``GOLDEN`` was captured from ``prompt.compose()`` **before** the Spec Kit parameter existed,
and is written out here as a literal rather than re-derived. FR-010 — the prompt for a
repository without Spec Kit is byte-identical to the pre-milestone prompt — is the
requirement most likely to be broken by an innocent refactor of the surrounding sections,
and a golden string is what notices. Re-deriving it from the same code that changed would
assert nothing.
"""

from __future__ import annotations

from robot_army import prompt, speckit
from robot_army.boundaries import Issue

GOLDEN = (
    "You are working on jantman/robot-army issue #9 in a dedicated git\n"
    "worktree on branch `robot-army/issue-9-speckit-extensions`.\n"
    "\n"
    "**Title**: Speckit Extensions\n"
    "**URL**: https://github.com/jantman/robot-army/issues/9\n"
    "**Labels**: robot-army\n"
    "\n"
    "---\n"
    "\n"
    "A body with **markdown** and a trailing line."
)

ISSUE = Issue(
    number=9,
    title="Speckit Extensions",
    body="A body with **markdown** and a trailing line.",
    url="https://github.com/jantman/robot-army/issues/9",
    labels=("robot-army",),
    author="jantman",
    state="open",
)


def compose(**kwargs: object) -> str:
    return prompt.compose(
        ISSUE,
        repo_key="jantman/robot-army",
        branch="robot-army/issue-9-speckit-extensions",
        **kwargs,  # type: ignore[arg-type]
    )


def test_without_a_block_the_prompt_is_byte_identical_to_before(self=None) -> None:
    assert compose() == GOLDEN


def test_the_block_sits_between_repository_instructions_and_the_issue() -> None:
    """Position encodes precedence, which is how prompt.py already works."""
    composed = compose(instructions="Always run make check.", speckit_block=speckit.GUIDANCE)

    instructions_at = composed.index("Always run make check.")
    block_at = composed.index("This repository uses Spec Kit")
    issue_at = composed.index("You are working on jantman/robot-army")

    assert instructions_at < block_at < issue_at


def test_the_block_appears_before_the_issue_when_there_are_no_instructions() -> None:
    composed = compose(speckit_block=speckit.GUIDANCE)

    assert composed.index("This repository uses Spec Kit") < composed.index(
        "You are working on"
    )


def test_the_same_issue_composed_twice_is_identical() -> None:
    """FR-009. Trivially true only because the block is fixed text — which is the point."""
    assert compose(speckit_block=speckit.GUIDANCE) == compose(
        speckit_block=speckit.GUIDANCE
    )


def test_the_block_names_the_four_commands_in_order() -> None:
    body = speckit.GUIDANCE
    positions = [body.index(f"/speckit-{name}") for name in speckit.LIFECYCLE]

    assert positions == sorted(positions)


def test_the_block_defers_to_the_repository_and_disclaims_enforcement() -> None:
    """FR-008: the judgement is the session's, and the block must not imply otherwise."""
    # Whitespace-normalised because the constant is hard-wrapped and the sentences that
    # matter straddle line breaks — asserting the wrapping would make an editorial reflow
    # look like a change of meaning.
    body = " ".join(speckit.GUIDANCE.lower().split())

    assert "nothing checks it" in body
    assert "the instruction above wins" in body


def test_the_block_is_not_interpolated_from_anything() -> None:
    """A constant with no format placeholders is what makes determinism a one-line test."""
    assert "{" not in speckit.GUIDANCE
    assert "}" not in speckit.GUIDANCE
