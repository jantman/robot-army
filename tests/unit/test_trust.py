"""Workspace trust and the committed-permission fingerprint (T059).

Both gates **fail closed**, and every test here is really the same assertion from a
different angle: the cost of a false negative is a clear error message, and the cost of a
false positive is a session hanging invisibly forever on a modal dialog (M0 E1.5) or
honouring tool permissions the maintainer never reviewed (M0 F9).
"""

from __future__ import annotations

import json
import subprocess

import pytest
from tests.conftest import make_boundaries, make_repo, onboard_repo

from robot_army.dispatch import (
    DispatchBlocked,
    check_gates,
    compute_fingerprint,
    is_trusted,
    read_committed_settings,
)


def write_trust(path, mapping):
    path.write_text(json.dumps({"projects": mapping}), encoding="utf-8")
    return path


def test_a_trusted_clone_passes(tmp_path, repo_clone):
    trust = write_trust(
        tmp_path / "claude.json", {str(repo_clone.resolve()): {"hasTrustDialogAccepted": True}}
    )
    trusted, why = is_trusted(repo_clone, trust_file=trust)
    assert trusted is True
    assert str(repo_clone.resolve()) in why


def test_a_missing_trust_file_fails_closed(tmp_path, repo_clone):
    trusted, why = is_trusted(repo_clone, trust_file=tmp_path / "absent.json")
    assert trusted is False
    assert "does not exist" in why


def test_malformed_json_fails_closed(tmp_path, repo_clone):
    path = tmp_path / "claude.json"
    path.write_text("{not json", encoding="utf-8")
    trusted, why = is_trusted(repo_clone, trust_file=path)
    assert trusted is False
    assert "not valid JSON" in why


def test_a_missing_projects_key_fails_closed(tmp_path, repo_clone):
    path = tmp_path / "claude.json"
    path.write_text(json.dumps({"other": {}}), encoding="utf-8")
    trusted, why = is_trusted(repo_clone, trust_file=path)
    assert trusted is False
    assert "no 'projects' object" in why


def test_a_clone_with_no_entry_fails_closed(tmp_path, repo_clone):
    trust = write_trust(tmp_path / "claude.json", {"/some/other/repo": {"hasTrustDialogAccepted": True}})
    trusted, why = is_trusted(repo_clone, trust_file=trust)
    assert trusted is False
    assert "has no entry" in why


def test_an_entry_with_the_flag_false_fails_closed(tmp_path, repo_clone):
    trust = write_trust(
        tmp_path / "claude.json", {str(repo_clone.resolve()): {"hasTrustDialogAccepted": False}}
    )
    trusted, why = is_trusted(repo_clone, trust_file=trust)
    assert trusted is False
    assert "invisible modal" in why


def test_a_truthy_but_non_true_value_is_not_accepted(tmp_path, repo_clone):
    """``is not True`` rather than ``not ...`` — ``"yes"`` and ``1`` must not pass."""
    trust = write_trust(
        tmp_path / "claude.json", {str(repo_clone.resolve()): {"hasTrustDialogAccepted": "yes"}}
    )
    assert is_trusted(repo_clone, trust_file=trust)[0] is False


def test_an_entry_that_is_not_an_object_fails_closed(tmp_path, repo_clone):
    trust = write_trust(tmp_path / "claude.json", {str(repo_clone.resolve()): "trusted"})
    assert is_trusted(repo_clone, trust_file=trust)[0] is False


# -- fingerprint ------------------------------------------------------------


@pytest.mark.requires_git
def test_the_fingerprint_reads_from_git_not_the_working_tree(tmp_path, audit):
    """R12: what matters is what a *freshly created worktree* will contain, which is the
    committed content at the base branch tip — not whatever is lying in the clone."""
    clone = make_repo(
        tmp_path / "fp", files={".claude/settings.json": '{"permissions": ["Bash"]}'}
    )
    boundaries = make_boundaries(audit)

    committed = compute_fingerprint(boundaries, str(clone), "main")
    assert set(committed) == {".claude/settings.json"}

    # An uncommitted edit must NOT move the fingerprint: a worktree created from the base
    # ref will not contain it.
    (clone / ".claude" / "settings.json").write_text('{"permissions": ["Bash", "Read"]}')
    assert compute_fingerprint(boundaries, str(clone), "main") == committed


@pytest.mark.requires_git
def test_a_committed_change_moves_the_fingerprint(tmp_path, audit):
    clone = make_repo(tmp_path / "fp", files={".claude/settings.json": '{"a": 1}'})
    boundaries = make_boundaries(audit)
    before = compute_fingerprint(boundaries, str(clone), "main")

    (clone / ".claude" / "settings.json").write_text('{"a": 2}', encoding="utf-8")
    _commit(clone, "change settings")
    assert compute_fingerprint(boundaries, str(clone), "main") != before


@pytest.mark.requires_git
def test_a_repo_with_no_committed_settings_has_an_empty_fingerprint(repo_clone, audit):
    assert compute_fingerprint(make_boundaries(audit), str(repo_clone), "main") == {}


@pytest.mark.requires_git
def test_read_committed_settings_returns_the_full_text_for_review(tmp_path, audit):
    """`onboard` prints these so the maintainer reads what will apply *without asking*."""
    body = '{"permissions": {"allow": ["Bash(rm -rf /)"]}}'
    clone = make_repo(tmp_path / "fp", files={".claude/settings.local.json": body})
    contents = read_committed_settings(make_boundaries(audit), str(clone), "main")
    assert contents[".claude/settings.local.json"] == body


# -- the review and the fingerprint below ``local`` (issue #20) --------------
#
# ``version_control`` is simulated at ``plan``, and its ``show_file_at_ref`` used to
# answer ``None`` unconditionally. Both functions above therefore returned ``{}`` for
# every repository: the review screen was structurally blank and an empty mapping was
# recorded as the approved fingerprint. These assert the property that fixes it — the
# simulated boundary answers what the real one answers.


@pytest.mark.requires_git
def test_the_fingerprint_is_the_same_under_a_simulated_boundary(tmp_path, audit):
    from robot_army.boundaries.git import SimulatedVersionControl

    clone = make_repo(
        tmp_path / "fp",
        files={
            ".claude/settings.json": '{"hooks": {"SessionStart": []}}',
            ".claude/settings.local.json": '{"permissions": {"allow": ["Bash"]}}',
        },
    )
    real = compute_fingerprint(make_boundaries(audit), str(clone), "main")
    simulated = compute_fingerprint(
        make_boundaries(audit, vcs=SimulatedVersionControl(audit)), str(clone), "main"
    )

    assert set(real) == {".claude/settings.json", ".claude/settings.local.json"}
    assert simulated == real, "a plan-level onboarding recorded an empty fingerprint"


@pytest.mark.requires_git
def test_the_review_text_is_the_same_under_a_simulated_boundary(tmp_path, audit):
    """The FR-003 screen itself: the maintainer must read the same file at every effect
    level, because approving is the same act at every effect level."""
    from robot_army.boundaries.git import SimulatedVersionControl

    body = '{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "x"}]}]}}'
    clone = make_repo(tmp_path / "fp", files={".claude/settings.json": body})

    contents = read_committed_settings(
        make_boundaries(audit, vcs=SimulatedVersionControl(audit)), str(clone), "main"
    )
    assert contents[".claude/settings.json"] == body


@pytest.mark.requires_git
def test_a_simulated_boundary_still_reports_a_repository_that_commits_nothing(
    repo_clone, audit
):
    """The fix must not turn the message into a lie in the other direction: "none" has to
    mean the files were looked for and were genuinely absent."""
    from robot_army.boundaries.git import SimulatedVersionControl

    boundaries = make_boundaries(audit, vcs=SimulatedVersionControl(audit))
    assert compute_fingerprint(boundaries, str(repo_clone), "main") == {}
    assert read_committed_settings(boundaries, str(repo_clone), "main") == {}


# -- the combined gate ------------------------------------------------------


@pytest.mark.requires_git
def test_gates_refuse_a_repository_that_is_not_onboarded(conn, audit, config, tmp_path):
    trust = write_trust(
        tmp_path / "claude.json",
        {str(config.repos["demo"].path.resolve()): {"hasTrustDialogAccepted": True}},
    )
    with pytest.raises(DispatchBlocked, match="not onboarded"):
        check_gates(
            conn,
            boundaries=make_boundaries(audit),
            config=config,
            repo=config.repos["demo"],
            trust_file=trust,
        )


@pytest.mark.requires_git
def test_gates_refuse_an_untrusted_repository(conn, audit, config, tmp_path):
    onboard_repo(conn, "demo", config.repos["demo"].path, trust_verified=False)
    with pytest.raises(DispatchBlocked, match="workspace trust check failed"):
        check_gates(
            conn,
            boundaries=make_boundaries(audit),
            config=config,
            repo=config.repos["demo"],
            trust_file=tmp_path / "absent.json",
        )


@pytest.mark.requires_git
def test_gates_refuse_when_committed_settings_appeared_since_onboarding(
    conn, audit, config, tmp_path
):
    """A file that did not exist at onboarding and now does is a difference, and FR-004
    blocks on any difference."""
    clone = config.repos["demo"].path
    trust = write_trust(
        tmp_path / "claude.json", {str(clone.resolve()): {"hasTrustDialogAccepted": True}}
    )
    onboard_repo(conn, "demo", config.repos["demo"].path)

    boundaries = make_boundaries(audit)
    check_gates(conn, boundaries=boundaries, config=config, repo=config.repos["demo"], trust_file=trust)

    settings = clone / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"permissions": {"allow": ["Bash"]}}', encoding="utf-8")
    _commit(clone, "add committed settings")

    with pytest.raises(DispatchBlocked, match="differ from what was approved"):
        check_gates(
            conn,
            boundaries=boundaries,
            config=config,
            repo=config.repos["demo"],
            trust_file=trust,
        )


@pytest.mark.requires_git
def test_gates_pass_once_the_new_fingerprint_is_approved(conn, audit, config, tmp_path):
    clone = config.repos["demo"].path
    trust = write_trust(
        tmp_path / "claude.json", {str(clone.resolve()): {"hasTrustDialogAccepted": True}}
    )
    settings = clone / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"permissions": {"allow": ["Bash"]}}', encoding="utf-8")
    _commit(clone, "add committed settings")

    boundaries = make_boundaries(audit)
    fingerprint = compute_fingerprint(boundaries, str(clone), "main")
    onboard_repo(
        conn, "demo", config.repos["demo"].path, settings_fingerprint=fingerprint
    )
    check_gates(
        conn, boundaries=boundaries, config=config, repo=config.repos["demo"], trust_file=trust
    )


@pytest.mark.requires_git
def test_an_approval_recorded_against_a_blank_review_no_longer_passes(
    conn, audit, config, tmp_path
):
    """Issue #20's remediation, and the reason the fix is not only forward-looking.

    Every repository onboarded on this installation while the read was blank holds an
    approval whose fingerprint is ``{}`` — a record asserting the repository commits no
    settings, made against a screen that showed none. Nothing backfills those rows: an
    approval means a human read exactly this and said yes, and writing hashes into one on
    the strength of a code change would forge that. So the correction is this block.

    **No production code exists for this test to exercise.** ``check_gates`` was always
    right; only its input was wrong, because ``compute_fingerprint`` read through the same
    blanked boundary the approval had been recorded through, so the two blanks matched and
    the gate passed. With the read real, they no longer match. The test is here to prove
    the gate now does what it always claimed, and it is deliberately driven through a
    *simulated* boundary, which is the configuration that was broken.
    """
    from robot_army.boundaries.git import SimulatedVersionControl

    clone = config.repos["demo"].path
    trust = write_trust(
        tmp_path / "claude.json", {str(clone.resolve()): {"hasTrustDialogAccepted": True}}
    )
    settings = clone / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"hooks": {"SessionStart": []}}', encoding="utf-8")
    _commit(clone, "add committed settings")

    # The approval as it was recorded below ``local``: empty, for a repository that is not.
    onboard_repo(conn, "demo", clone, settings_fingerprint={})

    with pytest.raises(DispatchBlocked) as blocked:
        check_gates(
            conn,
            boundaries=make_boundaries(audit, vcs=SimulatedVersionControl(audit)),
            config=config,
            repo=config.repos["demo"],
            trust_file=trust,
        )

    message = str(blocked.value)
    assert "differ from what was approved" in message
    # The operator is told which files and what to do, not merely that they are refused.
    assert ".claude/settings.json" in message
    assert "added:" in message
    assert "onboard 'demo' --reapprove" in message or "--reapprove" in message


def _commit(path, message: str) -> None:
    import os

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "add", "-A"], cwd=path, env=env, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=path, env=env, check=True, capture_output=True
    )


# -- the gate reads the branch onboarding approved (issue #150) --------------


@pytest.mark.requires_git
def test_the_gate_reads_the_detected_branch_not_the_configured_default(
    conn, audit, layout, tmp_path
):
    """Both halves of the fingerprint check resolve the base ref by one rule, so an
    unchanged ``master`` repository cannot report a changed fingerprint because onboarding
    and dispatch looked at different branches.

    Here the settings are committed on ``master`` and nowhere else. Against the old
    ``main`` default the gate computed ``{}``, which matched nothing that onboarding could
    honestly have recorded.
    """
    from tests.conftest import config_dict, monkey_token

    from robot_army import repos
    from robot_army.config import parse

    upstream = make_repo(
        tmp_path / "upstream",
        files={".claude/settings.json": '{"permissions": {"allow": ["Bash(ls:*)"]}}'},
        branch="master",
    )
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(upstream), str(clone)], check=True, capture_output=True
    )
    monkey_token()
    config = parse(
        config_dict(clone, layout, tmp_path / "worktrees", repos={"demo": {"path": str(clone)}}),
        tmp_path / "config.toml",
    )
    boundaries = make_boundaries(audit)
    approved = compute_fingerprint(
        boundaries,
        str(clone),
        repos.base_ref(config, "demo", boundaries.version_control, clone).ref,
    )
    assert ".claude/settings.json" in approved
    onboard_repo(conn, "demo", clone, settings_fingerprint=approved)
    trust = write_trust(
        tmp_path / "claude.json", {str(clone.resolve()): {"hasTrustDialogAccepted": True}}
    )

    check_gates(
        conn,
        boundaries=boundaries,
        config=config,
        repo=config.repos["demo"],
        trust_file=trust,
    )


@pytest.mark.requires_git
def test_a_configured_base_branch_still_decides_at_the_gate(conn, audit, layout, tmp_path):
    """Rung 1 beats the clone's own answer, at the gate as on the screen. The override is
    the reason the key exists, and detection is worthless if it cannot be turned off for
    the one repository that branches off something else."""
    from tests.conftest import config_dict, monkey_token

    from robot_army.config import parse

    upstream = make_repo(tmp_path / "upstream", branch="master")
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(upstream), str(clone)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "checkout", "-q", "-b", "develop"], cwd=clone, check=True, capture_output=True
    )
    (clone / ".claude").mkdir()
    (clone / ".claude" / "settings.json").write_text(
        '{"permissions": {"allow": ["Bash(ls:*)"]}}', encoding="utf-8"
    )
    _commit(clone, "settings on develop only")
    monkey_token()
    config = parse(
        config_dict(
            clone,
            layout,
            tmp_path / "worktrees",
            repos={"demo": {"path": str(clone), "base_branch": "develop"}},
        ),
        tmp_path / "config.toml",
    )
    boundaries = make_boundaries(audit)

    # The settings exist on ``develop`` and on no other branch, so a gate that had resolved
    # ``master`` — what the clone says — would compute an empty fingerprint and block.
    approved = compute_fingerprint(boundaries, str(clone), "develop")
    assert ".claude/settings.json" in approved
    onboard_repo(conn, "demo", clone, settings_fingerprint=approved)
    trust = write_trust(
        tmp_path / "claude.json", {str(clone.resolve()): {"hasTrustDialogAccepted": True}}
    )

    check_gates(
        conn,
        boundaries=boundaries,
        config=config,
        repo=config.repos["demo"],
        trust_file=trust,
    )
