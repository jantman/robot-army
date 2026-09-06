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


# -- fast_forward (milestone 047, T026-T029) --------------------------------
#
# The refusals are the point of these tests, not the success. This is the one verb in the
# protocol that writes to the author's own working clone, so the property being defended is
# not "it advances the branch" — it is that in every state a clone can be in, nothing the
# author has is lost and the reason is on the record.


@pytest.fixture
def behind(tmp_path):
    """A clone on ``main``, clean, with ``origin/main`` one commit ahead of it.

    Built by pushing from a *second* clone, because that is how the real case arises: the
    author merges a pull request on GitHub and their own clone knows nothing about it until
    something fetches.
    """
    from tests.conftest import make_repo

    bare = tmp_path / "remote.git"
    bare.mkdir()
    git(bare, "init", "--bare", "-q", "-b", "main")

    clone = make_repo(tmp_path / "clones" / "demo")
    git(clone, "remote", "add", "origin", str(bare))
    git(clone, "push", "-q", "-u", "origin", "main")

    other = tmp_path / "clones" / "other"
    other.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True, capture_output=True)
    (other / "landed.txt").write_text("merged work\n", encoding="utf-8")
    git(other, "add", "-A")
    git(
        other,
        "-c",
        "user.email=t@e.com",
        "-c",
        "user.name=T",
        "commit",
        "-q",
        "-m",
        "the previous issue",
    )
    git(other, "push", "-q", "origin", "main")

    git(clone, "fetch", "-q", "origin", "main")
    return clone


def head_of(path: Path, ref: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def status_of(path: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=path, check=True, capture_output=True, text=True
    ).stdout


def test_a_clean_clone_behind_its_remote_is_fast_forwarded(behind, audit):
    before = head_of(behind)
    target = head_of(behind, "origin/main")

    result = GitVersionControl(audit).fast_forward(str(behind), "origin", "main")

    assert result.outcome == "updated"
    assert result.before == before
    assert result.after == target
    assert head_of(behind) == target
    assert (behind / "landed.txt").exists()


def test_a_clone_already_at_the_remote_head_reports_already_current(behind, audit):
    """"Declined, and here is why" and "there was nothing to do" are different facts, and
    only one of them should make the author look at their clone."""
    vcs = GitVersionControl(audit)
    vcs.fast_forward(str(behind), "origin", "main")

    result = vcs.fast_forward(str(behind), "origin", "main")

    assert result.outcome == "already_current"
    assert result.before == result.after == head_of(behind)
    assert result.reason is None


def test_an_uncommitted_change_is_a_skip_and_the_clone_is_untouched(behind, audit):
    """The check that protects the author's work. Everything about this feature is a
    convenience; losing an edit to deliver it would be a bad trade at any price."""
    (behind / "README.md").write_text("# mine, unsaved\n", encoding="utf-8")
    before, status = head_of(behind), status_of(behind)

    result = GitVersionControl(audit).fast_forward(str(behind), "origin", "main")

    assert result.outcome == "skipped"
    assert "uncommitted" in result.reason
    assert head_of(behind) == before
    assert status_of(behind) == status


def test_an_untracked_file_is_a_skip_too(behind, audit):
    """Untracked counts as dirt, for the reason ``worktree remove`` treats it that way: the
    merge would refuse anyway if it brought in a file of that name, and it would refuse
    after we had claimed we were only fast-forwarding."""
    (behind / "scratch.txt").write_text("notes\n", encoding="utf-8")
    before = head_of(behind)

    result = GitVersionControl(audit).fast_forward(str(behind), "origin", "main")

    assert result.outcome == "skipped"
    assert "uncommitted" in result.reason
    assert head_of(behind) == before
    assert (behind / "scratch.txt").exists()


def test_being_on_another_branch_is_a_skip_that_names_the_branch(behind, audit):
    git(behind, "checkout", "-q", "-b", "elsewhere")
    before = head_of(behind)

    result = GitVersionControl(audit).fast_forward(str(behind), "origin", "main")

    assert result.outcome == "skipped"
    assert "elsewhere" in result.reason
    assert head_of(behind) == before
    assert head_of(behind, "main") == before


def test_a_detached_head_is_a_skip(behind, audit):
    git(behind, "checkout", "-q", "--detach", "HEAD")
    before = head_of(behind)

    result = GitVersionControl(audit).fast_forward(str(behind), "origin", "main")

    assert result.outcome == "skipped"
    assert "detached" in result.reason
    assert head_of(behind) == before


def test_an_interrupted_operation_is_a_skip_even_with_a_clean_tree(behind, audit):
    """A conflicted merge leaves a dirty tree and is caught by the check above. A *clean*
    one is not, and moving the branch out from under it would strand it."""
    git_dir = behind / ".git"
    (git_dir / "MERGE_HEAD").write_text(head_of(behind) + "\n", encoding="utf-8")
    before = head_of(behind)

    result = GitVersionControl(audit).fast_forward(str(behind), "origin", "main")

    assert result.outcome == "skipped"
    assert "merge" in result.reason
    assert head_of(behind) == before


def test_a_local_commit_the_remote_lacks_is_a_skip_not_a_rebase(behind, audit):
    """The one case where "catch up" would mean discarding work. Every unresolved doubt
    leaves the clone alone, and this is not even a doubt."""
    (behind / "mine.txt").write_text("my own commit\n", encoding="utf-8")
    git(behind, "add", "-A")
    git(behind, "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-q", "-m", "mine")
    before = head_of(behind)

    result = GitVersionControl(audit).fast_forward(str(behind), "origin", "main")

    assert result.outcome == "skipped"
    assert "not a fast-forward" in result.reason
    assert head_of(behind) == before
    assert (behind / "mine.txt").exists()


def test_a_clone_with_no_such_remote_is_a_skip(behind, audit):
    git(behind, "remote", "remove", "origin")
    before = head_of(behind)

    result = GitVersionControl(audit).fast_forward(str(behind), "origin", "main")

    assert result.outcome == "skipped"
    assert "no remote" in result.reason
    assert head_of(behind) == before


def test_a_remote_tracking_ref_that_does_not_exist_is_a_skip(behind, audit):
    """A fetch that produced nothing to advance to is not a failure.

    The branch is checked out here — otherwise the *previous* check would answer first, and
    the test would prove nothing about this one.
    """
    git(behind, "checkout", "-q", "-b", "local-only")

    result = GitVersionControl(audit).fast_forward(str(behind), "origin", "local-only")

    assert result.outcome == "skipped"
    assert "does not exist" in result.reason


def test_a_failing_merge_is_reported_rather_than_raised(behind, audit, monkeypatch):
    """FR-019: the caller is mid-dispatch and must proceed. ``--ff-only`` is the last line
    of defence behind the six checks, and when it fires the result is an outcome, not an
    exception."""
    vcs = GitVersionControl(audit)
    real = vcs._run

    def refuse(args, **kwargs):
        if args[:1] == ["merge"]:
            return real(["merge", "--ff-only", "refs/heads/no-such-thing"], **kwargs)
        return real(args, **kwargs)

    monkeypatch.setattr(vcs, "_run", refuse)
    before = head_of(behind)

    result = vcs.fast_forward(str(behind), "origin", "main")

    assert result.outcome == "failed"
    assert result.reason
    assert head_of(behind) == before


def test_the_outcome_is_on_the_record(behind, audit, layout):
    """Principle III: the clone's default branch moving is a change to state outside this
    process, so it is written down with the shas that bracket it."""
    import json

    GitVersionControl(audit).fast_forward(str(behind), "origin", "main")
    audit.close()

    records = [
        json.loads(line)
        for path in sorted(layout.log_dir.glob("audit-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    # The *outcome* half of the intent/outcome pair: the intent is written before the work
    # happens and so cannot carry what happened.
    entry = [
        r
        for r in records
        if r["action"] == "git.fast_forward" and r["outcome"] != "pending"
    ][-1]
    assert entry["detail"]["outcome"] == "updated"
    assert entry["detail"]["before"] != entry["detail"]["after"]


def test_the_simulated_implementation_changes_nothing(behind, audit):
    """Unlike ``remote_url``, this cannot answer honestly by doing the real thing — it is
    the one verb that writes to the author's clone, and a simulation that performed it
    would be simulating nothing."""
    before = head_of(behind)

    result = SimulatedVersionControl(audit).fast_forward(str(behind), "origin", "main")

    assert result.outcome == "skipped"
    assert "simulated" in result.reason
    assert head_of(behind) == before


# -- show_file_at_ref and default_remote (issue #20) -------------------------
#
# Both were answered by invention in the simulated class, and both are questions about
# the operator's *real* clone. ``show_file_at_ref`` is the read the onboarding security
# review depends on, so at ``plan`` that review was blank for every repository and an
# empty fingerprint was recorded as approved. ``default_remote`` is the same defect with
# a different name: it answered ``"origin"`` whatever the clone actually had.
#
# These live beside the ``remote_url`` tests above because they are the same assertion —
# the simulated implementation answers what the real one answers — and the rule they now
# share is in contracts/simulated-reads.md.


@pytest.fixture
def committed_settings(tmp_path):
    """A clone with both reviewed settings files committed at ``main``."""
    from tests.conftest import make_repo

    return make_repo(
        tmp_path / "clones" / "settings",
        files={
            ".claude/settings.json": '{"hooks": {"SessionStart": []}}',
            ".claude/settings.local.json": '{"permissions": {"allow": ["Bash"]}}',
        },
        origin="git@github.com:jantman/settings.git",
    )


def test_show_file_at_ref_answers_the_same_in_both_implementations(committed_settings, audit):
    """Issue #20. The simulated implementation returned ``None`` unconditionally, so
    ``onboard`` below ``local`` printed "no committed .claude/settings*.json at the base
    ref" for every repository and recorded an empty fingerprint as the approved one."""
    real, simulated = GitVersionControl(audit), SimulatedVersionControl(audit)
    clone = str(committed_settings)

    for path in (".claude/settings.json", ".claude/settings.local.json"):
        content = simulated.show_file_at_ref(clone, "main", path)
        assert content == real.show_file_at_ref(clone, "main", path)
        assert content is not None, f"{path} is committed at main and must be readable"


def test_default_remote_answers_the_same_in_both_implementations(bare_clone, audit):
    """The same defect as ``show_file_at_ref``, with a different name: the simulated
    implementation answered ``"origin"`` for every clone, so a local-only repository
    looked remote-backed and a clone whose only remote is ``gh`` was misreported."""
    real, simulated = GitVersionControl(audit), SimulatedVersionControl(audit)
    clone = str(bare_clone)

    assert simulated.default_remote(clone) == real.default_remote(clone) is None

    git(bare_clone, "remote", "add", "gh", "https://github.com/jantman/demo")
    assert simulated.default_remote(clone) == real.default_remote(clone) == "gh"

    git(bare_clone, "remote", "add", "origin", "git@github.com:jantman/demo.git")
    assert simulated.default_remote(clone) == real.default_remote(clone) == "origin"


# The failure paths, asserted of *both* implementations now that they are the same code
# path. Each of these was previously answered identically by accident — the simulated one
# returned ``None`` for everything, including for the cases that are genuinely ``None``.


def test_a_ref_that_does_not_exist_answers_none_rather_than_raising(committed_settings, audit):
    """A missing base ref is indistinguishable from a missing file, and must be: the
    onboarding screen prints the base ref immediately above the review so the operator can
    tell "not committed" from "you named a branch that is not here"."""
    for vcs in implementations(audit):
        assert vcs.show_file_at_ref(str(committed_settings), "no-such-ref", ".claude/settings.json") is None


def test_a_path_not_committed_at_the_ref_answers_none(committed_settings, audit):
    for vcs in implementations(audit):
        assert vcs.show_file_at_ref(str(committed_settings), "main", ".claude/absent.json") is None


def test_a_directory_that_is_not_a_repository_answers_rather_than_raising(tmp_path, audit):
    """This read must not become the thing that reports "there is no clone there" —
    onboarding checks existence and primary-clone-ness before it ever gets here."""
    plain = tmp_path / "plain"
    plain.mkdir()
    for vcs in implementations(audit):
        assert vcs.show_file_at_ref(str(plain), "main", ".claude/settings.json") is None
        assert vcs.default_remote(str(plain)) is None


def test_a_committed_settings_file_of_zero_bytes_is_present_not_absent(tmp_path, audit):
    """An empty file is a fact about the repository. It must be listed and hashed, because
    ``b""`` and "the file is not there" are different approvals."""
    from tests.conftest import make_repo

    clone = make_repo(tmp_path / "empty-settings", files={".claude/settings.json": ""})
    for vcs in implementations(audit):
        assert vcs.show_file_at_ref(str(clone), "main", ".claude/settings.json") == b""


def test_a_settings_file_that_is_not_valid_utf8_does_not_crash_the_review(tmp_path, audit):
    """A traceback here would be a traceback *instead of* the security screen, which is the
    one place this project must not fail closed by exploding.

    Note what this does **not** assert. ``subproc.run`` decodes with ``errors="replace"``
    and ``show_file_at_ref`` re-encodes, so undecodable bytes do not survive the round trip
    in *either* implementation — the fingerprint of such a file is the hash of its
    replacement-charred form, and two files differing only in invalid byte sequences would
    hash alike. That is a pre-existing property of the real read, not something issue #20
    introduced or this change alters, and it is left alone here rather than widened into.
    It matters little in practice because these files are JSON, which is UTF-8 by
    definition; it is written down so the next reader does not mistake this test for a
    promise that the bytes are exact.
    """
    from tests.conftest import make_boundaries, make_repo

    from robot_army.dispatch import read_committed_settings

    clone = make_repo(tmp_path / "bad-utf8")
    settings = clone / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_bytes(b'{"a": "\xff\xfe"}')
    subprocess.run(["git", "add", "-A"], cwd=clone, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e.x", "commit", "-qm", "bad"],
        cwd=clone,
        check=True,
        capture_output=True,
    )

    for vcs in implementations(audit):
        raw = vcs.show_file_at_ref(str(clone), "main", ".claude/settings.json")
        assert raw is not None
        contents = read_committed_settings(
            make_boundaries(audit, vcs=vcs), str(clone), "main"
        )
        assert "�" in contents[".claude/settings.json"]


# -- which reads may be invented at all (issue #20) --------------------------
#
# The rule is in contracts/simulated-reads.md and restated on the class: *the subject of
# the question decides, not the verb*. It is enforced here rather than in ``src/`` because
# the table has exactly one consumer, and that consumer is this test.
#
# ``show_file_at_ref`` and ``default_remote`` were both questions about the operator's real
# clone answered by invention, and the first of them was the read the onboarding security
# review depends on. Nothing structural caught either, because there was no rule to catch
# them against. Now a protocol member added without a decision fails here, by name.

#: Every member of ``VersionControl``, mapped to ``REAL`` or to the reason it answers as-if.
REAL = "real"
SUBJECT_VERDICTS: dict[str, str] = {
    # Real: the subject exists whatever level we simulate.
    "show_file_at_ref": REAL,
    "list_remotes": REAL,
    "remote_url": REAL,
    "default_remote": REAL,
    # Writes. Inert regardless of subject.
    "fetch": "writes refs, and reaches the network",
    "add_worktree": "creates the artifact; returns a structurally valid fake handle",
    "remove_worktree": "removes an artifact the simulation never created",
    "delete_branch": "deletes a branch the simulation never cut",
    "prune_worktrees": "writes to the clone's administrative files",
    "fast_forward": "the one verb that writes to the author's own working clone",
    # As-if: the subject is something the simulation only pretended to create.
    "worktree_exists": "a worktree never created; False would fail pre-launch validation",
    "status_porcelain": "that same worktree; nothing was checked out to be dirty",
    "commits_ahead": "a branch never cut; None would retain every simulated branch",
    "remote_branch_head": "a branch never pushed",
    # Mixed subjects, deliberately left as-if — see the docstrings on each.
    "rev_parse": "mixed: a real ref for one caller, the pretended branch for another",
    "list_worktrees": "mixed: the real clone, but used to judge a worktree never created",
}


def test_every_protocol_member_has_a_decision():
    """FR-007. A member added to ``VersionControl`` without a verdict fails here rather
    than silently picking a side, which is how ``show_file_at_ref`` stayed wrong."""
    from robot_army.boundaries import VersionControl

    members = set(VersionControl.__protocol_attrs__)
    assert members == set(SUBJECT_VERDICTS), (
        f"undecided: {sorted(members - set(SUBJECT_VERDICTS))}; "
        f"stale: {sorted(set(SUBJECT_VERDICTS) - members)}. "
        "Add the member to SUBJECT_VERDICTS and to contracts/simulated-reads.md."
    )


def test_every_real_verdict_really_delegates(committed_settings, audit):
    """The behavioural half, driven off the same table rather than a second hand-written
    list: for each member marked ``REAL``, both implementations answer alike."""
    real, simulated = GitVersionControl(audit), SimulatedVersionControl(audit)
    clone = str(committed_settings)
    #: One representative call per real-answering member.
    calls = {
        "show_file_at_ref": lambda vcs: vcs.show_file_at_ref(clone, "main", ".claude/settings.json"),
        "list_remotes": lambda vcs: vcs.list_remotes(clone),
        "remote_url": lambda vcs: vcs.remote_url(clone, "origin"),
        "default_remote": lambda vcs: vcs.default_remote(clone),
    }
    expected = {name for name, verdict in SUBJECT_VERDICTS.items() if verdict == REAL}
    assert set(calls) == expected, "a REAL verdict with no call here is untested"

    for name, call in calls.items():
        assert call(simulated) == call(real), f"{name} diverges between implementations"
