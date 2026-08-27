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
