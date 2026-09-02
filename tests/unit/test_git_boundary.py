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


# -- remote_branch_head (issue #105) ----------------------------------------
#
# The read that replaced a remote-tracking ref in cleanup's containment check. It is
# tested here rather than only through cleanup because its whole value is the *three*
# distinguishable answers: collapse two of them and the caller cannot tell "the remote
# does not have this branch" from "the remote could not be asked", and only one of those
# is allowed to sound like an answer.


@pytest.fixture
def clone_with_remote(tmp_path):
    """A clone whose ``origin`` is a real bare repository holding one branch."""
    from tests.conftest import make_repo

    bare = tmp_path / "remote.git"
    bare.mkdir()
    git(bare, "init", "--bare", "-q", "-b", "main")
    clone = make_repo(tmp_path / "clones" / "demo")
    git(clone, "remote", "add", "origin", str(bare))
    git(clone, "push", "-q", "origin", "main")
    return clone, bare


def refs_of(path: Path) -> str:
    return subprocess.run(
        ["git", "for-each-ref", "--format=%(refname) %(objectname)"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_a_branch_the_remote_has_answers_with_its_commit(clone_with_remote, audit):
    clone, bare = clone_with_remote
    expected = subprocess.run(
        ["git", "rev-parse", "main"], cwd=bare, check=True, capture_output=True, text=True
    ).stdout.strip()

    vcs = GitVersionControl(audit)
    assert vcs.remote_branch_head(str(clone), "origin", "main") == expected


def test_a_branch_the_remote_does_not_have_answers_none_rather_than_raising(
    clone_with_remote, audit
):
    """``None`` and a raise are different answers to different questions. The remote was
    reachable and said it has no such branch; that is information, and cleanup records it
    as a distinct retention reason."""
    clone, _bare = clone_with_remote

    vcs = GitVersionControl(audit)
    assert vcs.remote_branch_head(str(clone), "origin", "never-existed") is None


def test_a_remote_that_cannot_be_reached_raises(clone_with_remote, tmp_path, audit):
    clone, _bare = clone_with_remote
    git(clone, "remote", "add", "dead", str(tmp_path / "does-not-exist.git"))

    vcs = GitVersionControl(audit)
    with pytest.raises(Exception):  # noqa: B017 - any failure means "could not ask"
        vcs.remote_branch_head(str(clone), "dead", "main")


def test_asking_the_remote_writes_nothing_to_the_clone(clone_with_remote, audit):
    """FR-009, and the property the whole fix rests on.

    A read that updated ``refs/remotes/origin/<branch>`` would leave behind exactly the
    kind of ref that caused the defect, ready for the next reader to mistake for the
    remote's answer. So the assertion is not "it did the right thing" but "it left
    nothing behind at all".
    """
    clone, bare = clone_with_remote
    git(clone, "checkout", "-q", "-b", "feature")
    git(clone, "push", "-q", "origin", "feature:feature")
    git(bare, "update-ref", "-d", "refs/heads/feature")
    before = refs_of(clone)

    vcs = GitVersionControl(audit)
    assert vcs.remote_branch_head(str(clone), "origin", "feature") is None
    assert refs_of(clone) == before, "the stale tracking ref is left exactly as it was"
    assert "refs/remotes/origin/feature" in before, "...and it really was stale"


def test_the_request_is_on_the_record(clone_with_remote, audit, layout):
    """Constitution III: an outward-facing read is recorded before it runs and completed
    with its outcome, the same way the fetch beside it is."""
    import json

    clone, _bare = clone_with_remote
    GitVersionControl(audit).remote_branch_head(str(clone), "origin", "main")
    audit.close()

    records = [
        json.loads(line)
        for log in sorted(layout.log_dir.glob("audit-*.jsonl"))
        for line in log.read_text(encoding="utf-8").splitlines()
    ]
    ls_remote = [r for r in records if r["action"] == "git.ls_remote"]
    assert [r["outcome"] for r in ls_remote] == ["pending", "ok"]
    assert ls_remote[1]["detail"]["sha"]


def test_the_simulation_answers_so_a_planned_cleanup_still_decides(clone_with_remote, audit):
    """``SimulatedVersionControl`` answers with the forty zeroes its ``rev_parse`` answers
    with. Returning ``None`` would mean "the remote does not have this branch", and every
    ``plan``-level cleanup would retain every branch — a divergence from the real path,
    which is the one thing the simulated boundaries exist to avoid."""
    clone, _bare = clone_with_remote

    simulated = SimulatedVersionControl(audit)
    assert simulated.remote_branch_head(str(clone), "origin", "anything") == "0" * 40
    assert simulated.rev_parse(str(clone), "0" * 40) == "0" * 40


def test_rev_parse_does_not_prove_an_object_is_present_unless_it_is_peeled(
    clone_with_remote, audit
):
    """Why cleanup's containment check peels to ``^{commit}`` (PR #112 review).

    ``git rev-parse --verify`` validates that its argument is a single revision. For a
    bare forty-hex string that is a question about *syntax*: it echoes the string back and
    exits zero whether or not the object exists. Only peeling forces the lookup.

    This is pinned here, in the boundary's own tests, because the caller that got it wrong
    is two files away and its unit test used a fake that answered the way the author
    expected git to rather than the way git does.
    """
    clone, _bare = clone_with_remote
    absent = "1" * 40

    vcs = GitVersionControl(audit)
    assert vcs.rev_parse(str(clone), absent) == absent, "a bare sha proves nothing"
    assert vcs.rev_parse(str(clone), f"{absent}^{{commit}}") is None

    present = vcs.rev_parse(str(clone), "refs/heads/main")
    assert present is not None
    assert vcs.rev_parse(str(clone), f"{present}^{{commit}}") == present
