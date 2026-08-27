"""``VersionControl.remote_url`` and ``list_remotes``, added by milestone 005 (T010).

Both implementations are exercised against **real** repositories, because the simulated
one performs a real read here on purpose: "what repository is at this path" has one true
answer at every effect level, so answering with a fake would let a ``plan``-level
onboarding approve a location a ``live`` one would refuse (research R3).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from robot_army.boundaries.git import GitVersionControl, SimulatedVersionControl

pytestmark = pytest.mark.requires_git


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


@pytest.fixture
def bare_clone(tmp_path):
    """A clone with **no** remotes, built explicitly.

    The shared ``repo_clone`` fixture grew an ``origin`` in milestone 005 because the
    product now asks every clone what repository it is. These tests are about the read
    itself, so they start from nothing and add exactly the remotes each case needs.
    """
    from tests.conftest import make_repo

    return make_repo(tmp_path / "clones" / "demo")


def implementations(audit):
    return [GitVersionControl(audit), SimulatedVersionControl(audit)]


def test_a_clone_with_origin_reports_its_url(bare_clone, audit):
    git(bare_clone, "remote", "add", "origin", "git@github.com:jantman/demo.git")

    for vcs in implementations(audit):
        assert vcs.remote_url(str(bare_clone), "origin") == "git@github.com:jantman/demo.git"


def test_a_clone_with_a_single_differently_named_remote(bare_clone, audit):
    git(bare_clone, "remote", "add", "gh", "https://github.com/jantman/demo")

    for vcs in implementations(audit):
        assert vcs.list_remotes(str(bare_clone)) == ["gh"]
        assert vcs.remote_url(str(bare_clone), "gh") == "https://github.com/jantman/demo"
        assert vcs.remote_url(str(bare_clone), "origin") is None


def test_a_clone_with_no_remotes_reports_none(bare_clone, audit):
    for vcs in implementations(audit):
        assert vcs.list_remotes(str(bare_clone)) == []
        assert vcs.remote_url(str(bare_clone), "origin") is None


def test_the_simulated_implementation_answers_the_same_as_the_real_one(bare_clone, audit):
    """The property that matters: at ``plan`` this read is not simulated, so the
    verification a simulated onboarding performs is the verification a live one performs."""
    git(bare_clone, "remote", "add", "origin", "git@github.com:jantman/demo.git")
    git(bare_clone, "remote", "add", "upstream", "git@github.com:upstream/demo.git")
    real, simulated = GitVersionControl(audit), SimulatedVersionControl(audit)

    assert simulated.list_remotes(str(bare_clone)) == real.list_remotes(str(bare_clone))
    for remote in ("origin", "upstream", "absent"):
        assert simulated.remote_url(str(bare_clone), remote) == real.remote_url(
            str(bare_clone), remote
        )


def test_a_path_that_is_not_a_repository_answers_rather_than_raising(tmp_path, audit):
    """A refusal message wants "no clone there", not a traceback: the onboarding sequence
    checks existence and primary-clone-ness before it ever asks about remotes, and this
    read must not become the thing that reports those."""
    plain = tmp_path / "plain"
    plain.mkdir()

    for vcs in implementations(audit):
        assert vcs.list_remotes(str(plain)) == []
        assert vcs.remote_url(str(plain), "origin") is None


def test_default_remote_still_prefers_origin_after_being_rebuilt_on_list_remotes(
    bare_clone, audit
):
    """``list_remotes`` was split out of ``default_remote`` rather than added beside it, so
    the behaviour ``default_remote`` had is asserted here rather than assumed."""
    vcs = GitVersionControl(audit)
    assert vcs.default_remote(str(bare_clone)) is None

    git(bare_clone, "remote", "add", "upstream", "git@github.com:upstream/demo.git")
    assert vcs.default_remote(str(bare_clone)) == "upstream"

    git(bare_clone, "remote", "add", "origin", "git@github.com:jantman/demo.git")
    assert vcs.default_remote(str(bare_clone)) == "origin"
