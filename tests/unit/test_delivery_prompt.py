"""What every dispatched session is told about how its work is delivered (milestone 012).

The feature is a fixed string, so these tests are assertions about prose — which is an unusual
thing to test and worth defending. The prose *is* the deliverable: it is the only thing that
reaches the session, nothing enforces it, and an edit that broadens the third paragraph back into
"do not change the state of any system" would turn the block into one that forbids the very
delivery it demands two paragraphs earlier. That is not hypothetical: it is what the first draft
of this milestone shipped. Each test below names the requirement it holds and would fail on
exactly that kind of edit.

Assertions run against whitespace-normalised, lowercased text. The constant is hard-wrapped and
several of the sentences that matter straddle a line break, so matching the raw string would make
an editorial reflow read as a change of meaning. This is the convention
``tests/unit/test_speckit_prompt.py`` already uses for the Spec Kit block.
"""

from __future__ import annotations

from robot_army import prompt, speckit
from robot_army.boundaries import Issue

ISSUE = Issue(
    number=29,
    title="Ensure that prompts include PR creation",
    body="The body of the issue, which the session reads after everything above it.",
    url="https://github.com/jantman/robot-army/issues/29",
    labels=("robot-army",),
    author="jantman",
    state="open",
)


def compose(**kwargs: object) -> str:
    return prompt.compose(
        ISSUE,
        repo_key="jantman/robot-army",
        branch="robot-army/issue-29-prompt-branch-pr-safety",
        **kwargs,  # type: ignore[arg-type]
    )


def flat(text: str) -> str:
    """Lowercased, whitespace-collapsed. Reflowing the constant must not fail these tests."""
    return " ".join(text.lower().split())


BLOCK = flat(prompt.DELIVERY)


# --- User Story 1: the work goes on a branch, and ends pushed with a pull request -----------


def test_the_block_is_present_with_no_instructions_and_no_speckit_block() -> None:
    """FR-001, FR-011: unconditional. No repository file, no detection, no setting."""
    assert prompt.DELIVERY in compose()


def test_the_block_survives_every_combination_of_the_optional_sections() -> None:
    """FR-011 again, from the other side: nothing a caller passes can suppress it."""
    for kwargs in (
        {},
        {"instructions": "Always run make check."},
        {"speckit_block": speckit.GUIDANCE},
        {"instructions": "Always run make check.", "speckit_block": speckit.GUIDANCE},
    ):
        assert prompt.DELIVERY in compose(**kwargs)


def test_the_block_says_the_work_happens_on_the_feature_branch() -> None:
    """FR-002. Both halves: which branch to use, and which branch not to."""
    assert "do the work on the feature branch this session was started on" in BLOCK
    assert "never on the repository's default branch" in BLOCK


def test_the_block_says_to_push_to_origin_and_open_a_pull_request() -> None:
    """FR-003. The instruction the issue was filed for."""
    assert "push that branch to `origin`" in BLOCK
    assert "open a pull request" in BLOCK


def test_the_block_says_why_an_unpushed_branch_is_not_finished() -> None:
    """Not a bare FR, but the reason FR-003 exists: unpushed work is the unrecoverable kind.

    A session that is told *why* is one that can weigh the instruction against a surprise. A
    session told only *what* has to guess, and the cleanup guards exist because the guess is
    sometimes wrong.
    """
    assert "not a finished job" in BLOCK
    assert "cannot be recovered" in BLOCK


def test_the_block_never_points_upwards_at_the_branch_name() -> None:
    """research.md D3. The branch is named in the section *below* this block.

    "the branch above" would read perfectly well and be false, which is exactly the kind of
    error that survives review. The phrasing is direction-neutral instead, and stays true
    wherever the block is positioned.
    """
    assert "branch above" not in BLOCK
    assert "named above" not in BLOCK
    assert "this session was started on" in BLOCK


# --- User Story 2: the work product is a diff, not a hand-changed system --------------------


def test_the_block_says_the_work_product_is_changes_in_this_repository() -> None:
    """FR-004."""
    assert "deliver the work as code and file changes in this repository" in BLOCK
    assert "arriving as commits and a pull request" in BLOCK


def test_the_block_says_the_repository_is_the_mechanism_not_just_the_record() -> None:
    """FR-005, and the failure it actually exists for.

    The case is a Puppet repository and an issue reading "set up and run this service". Setting
    it up by hand satisfies the sentence, is faster, and is wrong — not because a machine was
    touched, but because the repository was the thing that was supposed to touch it. A rule
    phrased as "do not change the state of any system" does not say that, and a session that
    obeyed it literally would still have to guess what to do here.
    """
    assert "where this repository is the mechanism for changing something" in BLOCK
    assert "asking you to write the code that produces it, not to go and do it directly" in BLOCK


def test_the_block_names_the_kinds_of_repository_this_bites_on() -> None:
    """FR-005. Named categories, so the rule generalises past whichever one is in front of you."""
    assert "configuration management, infrastructure as code" in BLOCK
    assert "deployment or schedule definitions" in BLOCK


def test_the_block_says_why_a_hand_made_change_is_worse_than_none() -> None:
    """FR-005. The reason, not just the rule.

    A session told only *what* has to guess when the instruction meets a surprise. Told that a
    hand-made change is unreviewable and transient, it can work out the unlisted cases itself —
    which is the whole job of this paragraph, since no list would ever be complete.
    """
    assert "invisible to review" in BLOCK
    assert "gone the next time the real tool runs" in BLOCK


def test_the_scope_line_permits_the_ordinary_working_loop() -> None:
    """FR-006 and FR-007 together, which after the rewording are one sentence rather than two.

    The earlier draft prohibited changing the state of any system and then carved the push, the
    pull request and the test suite back out of it. An exception list is the tell that a rule
    was drawn in the wrong place: nothing legitimate is prohibited here, so nothing legitimate
    needs excepting.
    """
    assert "this is not a limit on how you work" in BLOCK
    assert "build, run, test, install dependencies, start things locally" in BLOCK
    assert "read whatever you need to read including live systems" in BLOCK
    assert "push your branch and open the pull request at the end" in BLOCK


def test_the_prohibition_is_scoped_to_bypassing_the_repository() -> None:
    """FR-005, FR-006. The narrow thing, said narrowly.

    "It is a limit on one thing" has to survive editing. If this ever grows into a list, the
    paragraph has stopped being a principle a session can reason from and become rules it can
    only pattern-match against — and the unlisted case is the one that matters.
    """
    assert "it is a limit on one thing" in BLOCK
    assert "reaching past the repository to change a live system" in BLOCK
    assert "where a change to the repository is what was asked for" in BLOCK


def test_the_block_does_not_prohibit_what_it_then_requires() -> None:
    """FR-006, asserted as the property rather than as a phrase.

    The regression this guards is the one the first draft shipped: a prohibition broad enough
    to forbid the push and pull request the block demands two paragraphs earlier. Neither an
    unqualified ban nor an exceptions list should reappear.
    """
    assert "any other system" not in BLOCK
    assert "are the exceptions" not in BLOCK
    # The scope line must come after the rule it scopes; a reader who stops early is then
    # left with a rule that is narrower than they think, never one that is broader.
    assert BLOCK.index("mechanism for changing something") < BLOCK.index("not a limit on how")


# --- User Story 3: an issue that needs something else can say so ----------------------------


def test_the_block_states_that_the_issue_body_overrides_it() -> None:
    """FR-008, stated rather than implied.

    Every other precedence in ``prompt.py`` is encoded by position, earlier outranking later.
    That rule gives the wrong answer here — the issue body is below this block and outranks
    it — so the text has to say so itself.
    """
    assert "unless the issue below explicitly says otherwise" in BLOCK
    assert "the issue wins" in BLOCK


def test_the_override_names_the_cases_it_covers() -> None:
    """FR-008. A rule with no examples is one a session hesitates to use."""
    assert "no pull request" in BLOCK
    assert "a commit straight to the default branch" in BLOCK
    assert "an action on a system" in BLOCK


def test_the_block_disclaims_enforcement() -> None:
    """Nothing checks any of this, and text implying otherwise would be a false boundary.

    Same stance as the Spec Kit block's "nothing checks it" — see
    ``specs/012-prompt-branch-pr-safety/spec.md`` Out of Scope.
    """
    assert "nothing here is checked" in BLOCK


def test_the_sections_are_ordered_repository_then_speckit_then_delivery_then_issue() -> None:
    """FR-009 and research.md D2, asserted rather than asserted-in-a-comment.

    The repository's own instructions outrank everything, which is true only because they come
    first; the delivery block sits below the Spec Kit block so that block's closing "the
    instruction above wins" still covers exactly what it covered before 012.
    """
    composed = compose(instructions="Always run make check.", speckit_block=speckit.GUIDANCE)

    instructions_at = composed.index("Always run make check.")
    speckit_at = composed.index("This repository uses Spec Kit")
    delivery_at = composed.index(prompt.DELIVERY)
    issue_at = composed.index("You are working on jantman/robot-army")
    body_at = composed.index("The body of the issue")

    assert instructions_at < speckit_at < delivery_at < issue_at < body_at


def test_the_block_precedes_the_issue_even_with_no_other_sections() -> None:
    """FR-012: it is never pushed past a body that could be 60,000 characters long."""
    composed = compose()

    assert composed.index(prompt.DELIVERY) < composed.index("You are working on")


# --- Determinism and size -------------------------------------------------------------------


def test_the_block_is_not_interpolated_from_anything() -> None:
    """FR-010. A constant with no format placeholders is what makes determinism cheap."""
    assert "{" not in prompt.DELIVERY
    assert "}" not in prompt.DELIVERY


def test_the_same_issue_composed_twice_is_identical() -> None:
    """FR-010."""
    assert compose(speckit_block=speckit.GUIDANCE) == compose(speckit_block=speckit.GUIDANCE)


def test_the_block_stays_under_the_size_budget() -> None:
    """SC-004. The issue is what the session is here to read; this is the frame around it."""
    assert len(prompt.DELIVERY) < 1_500
