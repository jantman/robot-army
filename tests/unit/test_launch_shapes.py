"""The launch shapes this system composes, and whether the worker binary accepts them.

Two kinds of check live here, and the second exists because the first is not enough.

``build_launch_plan`` was asserted for milestones by tests that compared its output to the
list the code intended to build. Those tests passed for the entire life of ``resume``, on a
command the worker rejects before it runs anything: ``--session-id`` together with
``--resume`` is refused outright unless ``--fork-session`` is also given. The list was
"correct" by the only definition the suite had.

So correctness here means *the binary accepts it*, and the second half of this module hands
every shape to the real binary to find out. See ``contracts/worker-launch-shapes.md`` in
specs/013-fix-resume-fork-session.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from typing import Any

import pytest
from tests.conftest import make_boundaries, make_issue

from robot_army import dispatch, prompt
from robot_army.config import VALID_PERMISSION_MODES

PRIOR = "11111111-1111-4111-8111-111111111111"
CHOSEN = "22222222-2222-4222-8222-222222222222"


def plan_for(config, layout, audit, **overrides: Any):
    """One launch plan, with the arguments every test here shares."""
    kwargs: dict[str, Any] = dict(
        config=config,
        layout=layout,
        boundaries=make_boundaries(audit),
        audit=audit,
        repo_key="demo",
        item_id=7,
        issue=make_issue(),
        worktree_path="/tmp/wt",  # never written to: this composes a plan, it does not run one
        branch="robot-army/issue-42-fix",
        session_id=CHOSEN,
    )
    kwargs.update(overrides)
    return dispatch.build_launch_plan(**kwargs)


def flags_of(plan) -> list[str]:
    """The argv without the prompt body, which is composed elsewhere and tested there."""
    return list(plan.worker_argv[:-1])


# -- composition ------------------------------------------------------------


def test_a_restoring_launch_asks_for_a_fork(config, layout, audit):
    """FR-001. Without ``--fork-session`` the binary refuses the combination outright, so a
    resume launched without it has never once started a session."""
    argv = plan_for(config, layout, audit, resume_session_id=PRIOR).worker_argv

    assert "--fork-session" in argv, (
        "--session-id with --resume is rejected by the worker unless --fork-session is "
        "given; a resume composed without it exits 1 before doing anything"
    )
    resume_at = argv.index("--resume")
    assert argv[resume_at + 1] == PRIOR
    assert argv[resume_at + 2] == "--fork-session", (
        "the flag belongs with the --resume it qualifies, so the pair reads as one intent"
    )
    assert argv[argv.index("--session-id") + 1] == CHOSEN


def test_a_fresh_launch_is_composed_exactly_as_before(config, layout, audit):
    """FR-004. The non-restoring shape is the one that has always worked. This asserts the
    whole list rather than the absence of one flag: nothing may drift into it."""
    plan = plan_for(config, layout, audit)
    name = prompt.session_name("demo", make_issue().number)

    assert flags_of(plan) == [
        config.worker.binary,
        "--session-id",
        CHOSEN,
        "-n",
        name,
        "--remote-control",
        name,
        "--permission-mode",
        config.permission_mode_for("demo"),
    ]


def test_a_fresh_launch_never_asks_for_a_fork(config, layout, audit):
    """``--fork-session`` is meaningless without ``--resume`` and the binary says so."""
    assert "--fork-session" not in plan_for(config, layout, audit).worker_argv


def test_the_restoring_shape_differs_by_exactly_two_tokens(config, layout, audit):
    """The whole fix, stated as a property: restoring adds ``--resume <id>`` and
    ``--fork-session`` and changes nothing else."""
    fresh = flags_of(plan_for(config, layout, audit))
    restoring = flags_of(plan_for(config, layout, audit, resume_session_id=PRIOR))

    assert [token for token in restoring if token not in ("--resume", PRIOR, "--fork-session")] == (
        fresh
    )


def test_the_launch_environment_carries_the_session_id(config, layout, audit):
    """RA-16. Since the wrapper stopped recovering the id from argv, ``ROBOT_ARMY_SESSION_ID``
    is its *only* source --- and nothing else in the suite fails if it goes missing, because
    every other consumer reads the id from the plan directly.

    So this is a guard rather than a behaviour test: without it, deleting one line of
    ``build_launch_plan`` would leave a system that launches sessions which then refuse to
    start, and the first evidence would be a session that never reaches ``active``."""
    for overrides in ({}, {"resume_session_id": PRIOR}):
        plan = plan_for(config, layout, audit, **overrides)
        assert plan.env["ROBOT_ARMY_SESSION_ID"] == plan.session_id == CHOSEN, (
            "the wrapper reads the session id from this variable and nowhere else"
        )


def test_a_repositorys_env_cannot_displace_the_session_id(config, layout, audit):
    """Per-repository ``[repos.*] env`` is merged into the launch environment, and it is
    configuration rather than untrusted input --- but the merge order decides whether a
    stray key could unset the wrapper's one source of truth. Pinned, because the answer is
    not visible from the call site."""
    plan = plan_for(
        config, layout, audit, env={"ROBOT_ARMY_SESSION_ID": "nonsense", "OTHER": "kept"}
    )

    assert plan.env["OTHER"] == "kept", "a repository's own variables still arrive"
    assert plan.env["ROBOT_ARMY_SESSION_ID"] == CHOSEN


# -- the real binary --------------------------------------------------------
#
# Everything above asserts what `build_launch_plan` composes. That is exactly the kind of
# test that passed for the entire life of `resume` on a command the binary refuses, so it
# cannot be the last word. These hand every shape the system can compose to the actual
# worker and ask whether it would accept it.
#
# The probe substitutes `-p` with empty stdin for the prompt body: the binary validates its
# arguments before it does anything, so reaching its complaint about the missing prompt
# proves every other argument was accepted --- in about nine tenths of a second, with no
# model call, no worktree, and no session left behind.

WORKER = "claude"

#: Reaching one of these means argument validation passed. See
#: specs/013-fix-resume-fork-session/contracts/worker-launch-shapes.md.
GOT_PAST_VALIDATION = "Input must be provided either through stdin or as a prompt argument"
NO_SUCH_CONVERSATION = "No conversation found with session ID:"

#: The defect. If this ever appears again, a launch shape has been composed that cannot run.
REJECTED_COMBINATION = "--session-id can only be used with"

needs_worker = pytest.mark.skipif(
    shutil.which(WORKER) is None,
    reason=f"the worker binary {WORKER!r} is not installed; the launch shapes cannot be "
    "checked against it here. This reports as SKIPPED and never as passed, because an "
    "unrun check must not read as a green one",
)

SHAPES = [
    pytest.param(mode, model, restoring, id=f"{mode}-{'model' if model else 'nomodel'}-"
                 f"{'resume' if restoring else 'fresh'}")
    for mode in VALID_PERMISSION_MODES
    for model in ("", "sonnet")
    for restoring in (False, True)
]


@pytest.mark.requires_worker
@needs_worker
@pytest.mark.parametrize(("mode", "model", "restoring"), SHAPES)
def test_the_worker_accepts_every_shape_we_compose(
    config, layout, audit, mode, model, restoring
):
    """FR-013/FR-014. The shapes are the ones the system can actually produce, and no more:
    the value is in that fixed set, not in a general facility for running real workers."""
    varied = dataclasses.replace(
        config, worker=dataclasses.replace(config.worker, permission_mode=mode, model=model)
    )
    plan = plan_for(
        varied, layout, audit, resume_session_id=PRIOR if restoring else None
    )

    # `-p` with empty stdin, in place of the prompt body, is what keeps this cheap.
    argv = [plan.worker_argv[0], "-p", *plan.worker_argv[1:-1]]
    # argv the system itself composed, run without a shell.
    result = subprocess.run(
        argv, input="", capture_output=True, text=True, timeout=60, check=False
    )
    output = result.stdout + result.stderr

    assert REJECTED_COMBINATION not in output, (
        f"the worker refuses this shape before running anything:\n"
        f"  argv:  {' '.join(argv)}\n"
        f"  says:  {output.strip().splitlines()[0] if output.strip() else '(nothing)'}"
    )
    expected = NO_SUCH_CONVERSATION if restoring else GOT_PAST_VALIDATION
    assert expected in output, (
        "the worker's wording has changed, so this check no longer proves what it claims. "
        "Re-measure it against the binary rather than relaxing the assertion --- assuming "
        "instead of measuring is what produced this whole milestone.\n"
        f"  argv:  {' '.join(argv)}\n"
        f"  says:  {output.strip()[:400]}"
    )
