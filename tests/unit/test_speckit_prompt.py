"""The prompt block: where it sits, that it is fixed, and what the whole assembly looks like.

``GOLDEN`` is written out as a literal rather than re-derived from the constants that produce
it. Re-deriving it from the same code that changed would assert nothing; spelled out, it is
the only test that notices when the prompt's sections are reshaped by an innocent refactor.

Its value changed once, deliberately. Milestone 007 captured it *before* the Spec Kit
parameter existed, to hold that milestone's FR-010: a repository without Spec Kit got a
byte-identical prompt. That was a statement about that change and has been satisfied ever
since. Milestone 012 then made the delivery block unconditional, so every prompt carries it
and the pre-007 bytes are gone on purpose — see
``specs/012-prompt-branch-pr-safety/research.md`` D5. The test is kept rather than deleted
because what it is actually good for survives the change of expected value.
"""

from __future__ import annotations

from robot_army import prompt, speckit
from robot_army.boundaries import Issue

GOLDEN = (
    'Unless the issue below explicitly says otherwise, this is how the work is expected to be\n'
    'delivered.\n'
    '\n'
    "Do the work on the feature branch this session was started on, never on the repository's\n"
    'default branch. When the work is done, commit it, push that branch to `origin`, and open a\n'
    'pull request. Commits sitting on an unpushed branch are not a finished job: the worktree can\n'
    'be reclaimed, and unpushed work is the one thing that cannot be recovered from it.\n'
    '\n'
    'Deliver the work as code and file changes in this repository, arriving as commits and a pull\n'
    'request. Where this repository is the mechanism for changing something — configuration\n'
    'management, infrastructure as code, deployment or schedule definitions — an issue asking for\n'
    'that thing is asking you to write the code that produces it, not to go and do it directly. A\n'
    'change made by hand is invisible to review and gone the next time the real tool runs.\n'
    '\n'
    'This is not a limit on how you work: build, run, test, install dependencies, start things\n'
    'locally, read whatever you need to read including live systems, and push your branch and open\n'
    'the pull request at the end. It is a limit on one thing — reaching past the repository to\n'
    'change a live system, where a change to the repository is what was asked for.\n'
    '\n'
    'If the issue below explicitly asks for something else — no pull request, a commit straight to\n'
    'the default branch, or an action on a system — the issue wins. Nothing here is checked.\n'
    '\n'
    '---\n'
    '\n'
    'You are working on jantman/robot-army issue #9 in a dedicated git\n'
    'worktree on branch `robot-army/issue-9-speckit-extensions`.\n'
    '\n'
    '**Title**: Speckit Extensions\n'
    '**URL**: https://github.com/jantman/robot-army/issues/9\n'
    '**Labels**: robot-army\n'
    '\n'
    '---\n'
    '\n'
    'A body with **markdown** and a trailing line.'
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


def test_the_whole_assembly_matches_the_golden_string(self=None) -> None:
    """With no repository instructions and no Spec Kit block, this is the whole prompt."""
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
