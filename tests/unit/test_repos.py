"""The resolution seam: derivation, normalisation, comparison, and the resolved view.

Milestone 005 (T006, T008, T013, T022, T043). Everything here is pure or reads a real
fixture repository — there is no fake ``git`` in this file, because the two questions it
asks of git ("what is at this path", "which remotes does it have") have one true answer
and a fake would only test the fake.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from tests.conftest import config_dict, make_repo, monkey_token

from robot_army import db, repos
from robot_army.config import parse

# -- normalisation (T006) ---------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:jantman/demo.git",
        "git@github.com:jantman/demo",
        "https://github.com/jantman/demo.git",
        "https://github.com/jantman/demo",
        "ssh://git@github.com/jantman/demo.git",
        "ssh://git@github.com/jantman/demo",
    ],
)
def test_every_url_form_for_one_repository_normalises_the_same(url):
    """The author's own clones use at least three spellings of the same repository, so a
    comparison on raw strings would refuse correct clones far more often than it caught
    wrong ones (research R2)."""
    assert str(repos.normalise_remote(url)) == "github.com/jantman/demo"


def test_case_differences_compare_equal():
    """The source system treats repository names case-insensitively and a Linux
    filesystem does not, so folding is required rather than tidy."""
    assert repos.normalise_remote("git@GitHub.com:JAntman/Demo.git") == repos.normalise_remote(
        "https://github.com/jantman/demo"
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url",
        "https://github.com/jantman",
        "https://github.com/jantman/demo/extra",
        "git@github.com",
        "/home/jantman/GIT/demo",
        "file:///home/jantman/GIT/demo",
    ],
)
def test_an_unparseable_url_yields_no_result_rather_than_a_partial_one(url):
    """A half-parsed identity compared against a repository key would pass or fail for
    reasons nobody could reconstruct, so the shape is all-or-nothing."""
    assert repos.normalise_remote(url) is None


def test_a_credential_in_the_url_is_absent_from_the_result():
    """The case FR-032 exists for. This is the first milestone that reads a git remote
    URL at all, so it is the first time this exposure exists — and the identity type is
    the choke point: it cannot carry a secret, so nothing downstream of it can leak one."""
    identity = repos.normalise_remote("https://jantman:ghp_secretvalue@github.com/jantman/demo.git")

    assert str(identity) == "github.com/jantman/demo"
    assert "ghp_secretvalue" not in str(identity)
    assert "ghp_secretvalue" not in repr(identity)
    assert "jantman:" not in str(identity)


def test_the_scp_form_with_userinfo_still_normalises():
    assert str(repos.normalise_remote("jantman@github.com:jantman/demo.git")) == (
        "github.com/jantman/demo"
    )


def test_the_expected_identity_comes_from_the_configured_api_host():
    assert str(repos.identity_for_key("jantman/demo", "https://api.github.com")) == (
        "github.com/jantman/demo"
    )
    assert str(repos.identity_for_key("jantman/demo", "https://git.example.invalid/api/v3")) == (
        "git.example.invalid/jantman/demo"
    )


def test_a_same_named_repository_on_another_forge_is_a_different_repository():
    """T043: a same-named repository on another forge fails identically to a different
    repository, and the check costs nothing extra (research R2)."""
    expected = repos.identity_for_key("jantman/demo", "https://api.github.com")

    assert repos.normalise_remote("git@gitlab.com:jantman/demo.git") != expected


def test_a_malformed_key_has_no_expected_identity():
    assert repos.identity_for_key("demo", "https://api.github.com") is None
    assert repos.identity_for_key("a/b/c", "https://api.github.com") is None


# -- derivation (T008) ------------------------------------------------------


def build_config(repo_clone, layout, tmp_path, **overrides):
    monkey_token()
    return parse(
        config_dict(repo_clone, layout, tmp_path / "worktrees", **overrides),
        tmp_path / "config.toml",
    )


def test_derivation_produces_exactly_one_candidate(config):
    assert repos.derive_path(config, "jantman/demo") == config.repo_root / "demo"


@pytest.mark.parametrize("name", ["some.repo", "some-repo", "UPPER", "a.b.c-d"])
def test_derivation_carries_the_name_through_verbatim(config, name):
    assert repos.derive_path(config, f"jantman/{name}") == config.repo_root / name


def test_a_key_with_no_slash_derives_nothing(config):
    assert repos.derive_path(config, "demo") is None
    assert repos.derive_path(config, "owner/name/extra") is None


def test_derivation_touches_no_filesystem(config, monkeypatch):
    """FR-002 says one candidate, not a search. A rule that looks around is the rule that
    finds a directory named right belonging to a repository nobody named."""
    for forbidden in ("exists", "is_dir", "iterdir", "glob", "resolve"):
        monkeypatch.setattr(
            Path,
            forbidden,
            lambda *a, **k: pytest.fail("derivation must not touch the filesystem"),
        )
    assert repos.derive_path(config, "jantman/demo") is not None


def test_derivation_does_not_fall_back_to_owner_slash_name(config):
    """The rejected alternative, asserted rather than trusted: nested grouping directories
    hold repositories the author does not own and would not dispatch into."""
    assert repos.derive_path(config, "jantman/demo") != config.repo_root / "jantman" / "demo"


# -- primary clone (T013) ---------------------------------------------------


@pytest.mark.requires_git
def test_a_real_clone_is_a_primary_clone(repo_clone):
    assert repos.is_primary_clone(repo_clone)


@pytest.mark.requires_git
def test_a_real_linked_worktree_is_not_a_primary_clone(repo_clone, tmp_path):
    """In a linked worktree ``.git`` is a *file* holding a ``gitdir:`` pointer, which is
    what makes this a ``stat`` rather than a subprocess (research R4)."""
    cut = tmp_path / "cut-worktree"
    subprocess.run(
        ["git", "worktree", "add", "-b", "wt", str(cut)],
        cwd=repo_clone,
        check=True,
        capture_output=True,
    )

    assert cut.is_dir()
    assert (cut / ".git").is_file(), "the fixture is only meaningful if git did this"
    assert not repos.is_primary_clone(cut)


def test_a_directory_that_is_not_a_repository_is_not_a_primary_clone(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not repos.is_primary_clone(plain)
    assert not repos.is_primary_clone(tmp_path / "absent")


def test_is_inside_covers_the_directory_itself_and_its_children(tmp_path):
    assert repos.is_inside(tmp_path, tmp_path)
    assert repos.is_inside(tmp_path / "a" / "b", tmp_path)
    assert not repos.is_inside(tmp_path.parent, tmp_path)


# -- remote selection (T011) ------------------------------------------------


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


@pytest.fixture
def bare_clone(tmp_path):
    """A clone with **no** remotes, so each case adds exactly the ones it is about.

    The shared ``repo_clone`` fixture carries an ``origin``, because after milestone 005 a
    clone without one is a clone the product would refuse.
    """
    return make_repo(tmp_path / "clones" / "bare")


@pytest.mark.requires_git
def test_origin_is_preferred_over_every_other_remote(bare_clone, boundaries):
    git(bare_clone, "remote", "add", "upstream", "git@github.com:upstream/demo.git")
    git(bare_clone, "remote", "add", "origin", "git@github.com:jantman/demo.git")

    name, refusal = repos.select_remote(boundaries.version_control, bare_clone)

    assert (name, refusal) == ("origin", None)


@pytest.mark.requires_git
def test_a_sole_differently_named_remote_is_used_and_named(bare_clone, boundaries):
    git(bare_clone, "remote", "add", "gh", "git@github.com:jantman/demo.git")

    name, refusal = repos.select_remote(boundaries.version_control, bare_clone)

    assert (name, refusal) == ("gh", None)


@pytest.mark.requires_git
def test_several_remotes_and_no_origin_is_ambiguous_rather_than_a_pick(bare_clone, boundaries):
    """Deliberately stricter than ``default_remote``, which may pick arbitrarily because
    it is choosing where to fetch. Identity is not serviceable-with-any-answer: picking
    arbitrarily would make the verdict depend on git's ordering (research R3)."""
    git(bare_clone, "remote", "add", "mine", "git@github.com:jantman/demo.git")
    git(bare_clone, "remote", "add", "theirs", "git@github.com:someone/demo.git")

    name, refusal = repos.select_remote(boundaries.version_control, bare_clone)

    assert name is None
    assert refusal is not None
    assert "mine" in refusal and "theirs" in refusal


@pytest.mark.requires_git
def test_no_remote_at_all_is_reported_as_no_remote_not_as_ambiguity(bare_clone, boundaries):
    name, refusal = repos.select_remote(boundaries.version_control, bare_clone)

    assert (name, refusal) == (None, None)


# -- known and resolve (T022) -----------------------------------------------


def onboard_row(conn, key, clone_path, **extra):
    with db.transaction(conn):
        db.upsert_repo(
            conn,
            repo_key=key,
            settings_fingerprint=None,
            trust_verified=True,
            clone_path=str(clone_path) if clone_path else None,
            path_source=extra.get("path_source", "derived"),
            verified_origin=extra.get("verified_origin", "github.com/jantman/demo"),
        )


def test_known_reports_the_onboarded_set_and_not_the_configured_one(conn, config, repo_clone):
    """The one intentional breaking change: a ``[repos.*]`` section is no longer evidence
    of anything except that overrides exist for a key."""
    assert repos.known(conn) == []
    assert "demo" in config.repos, "the fixture still has a section for it"

    onboard_row(conn, "jantman/demo", repo_clone)

    assert repos.known(conn) == ["jantman/demo"]


def test_an_onboarded_repository_with_no_section_resolves_entirely_from_defaults(
    conn, config, repo_clone
):
    onboard_row(conn, "jantman/demo", repo_clone)

    resolved = repos.resolve(conn, config, "jantman/demo")

    assert resolved is not None
    assert resolved.path == repo_clone
    assert resolved.base_branch == config.worker.base_branch
    assert resolved.post_create == ()
    assert resolved.env == {}
    assert resolved.permission_mode is None
    assert resolved.model is None
    assert resolved.max_sessions is None
    assert resolved.priority == 0


@pytest.mark.parametrize(
    ("field", "section_value", "expected"),
    [
        ("base_branch", "trunk", "trunk"),
        ("permission_mode", "plan", "plan"),
        ("model", "sonnet", "sonnet"),
        ("max_sessions", 3, 3),
        ("priority", 7, 7),
    ],
)
def test_a_section_overrides_each_field_in_turn(
    repo_clone, layout, tmp_path, conn, field, section_value, expected
):
    config = build_config(
        repo_clone,
        layout,
        tmp_path,
        repos={"jantman/demo": {"path": str(repo_clone), field: section_value}},
    )
    onboard_row(conn, "jantman/demo", repo_clone)

    resolved = repos.resolve(conn, config, "jantman/demo")

    assert getattr(resolved, field) == expected


def test_the_records_path_wins_over_a_sections_path(repo_clone, layout, tmp_path, conn):
    """``path`` is the only field the record wins, and the asymmetry is the design: every
    other field is a policy the author may change by editing a file, whereas ``path``
    decides *which repository is acted upon*."""
    elsewhere = make_repo(tmp_path / "elsewhere")
    config = build_config(
        repo_clone,
        layout,
        tmp_path,
        repos={"jantman/demo": {"path": str(elsewhere)}},
    )
    onboard_row(conn, "jantman/demo", repo_clone)

    resolved = repos.resolve(conn, config, "jantman/demo")

    assert resolved.path == repo_clone, "the approved location, not the edited one"


def test_an_unonboarded_key_resolves_to_nothing(conn, config):
    assert repos.resolve(conn, config, "jantman/demo") is None
    assert repos.resolve(conn, config, "nobody/nothing") is None


def test_a_pre_005_row_with_no_section_resolves_to_nothing_rather_than_a_guess(conn, config):
    """A NULL ``clone_path`` means *onboarded, location never verified*. Re-deriving here
    would be the guess the record exists to avoid (research R6)."""
    onboard_row(conn, "jantman/orphan", None, path_source=None, verified_origin=None)

    assert repos.known(conn) == ["jantman/orphan"]
    assert repos.resolve(conn, config, "jantman/orphan") is None


def test_resolved_all_omits_what_does_not_resolve(conn, config, repo_clone):
    onboard_row(conn, "jantman/demo", repo_clone)
    onboard_row(conn, "jantman/orphan", None, path_source=None, verified_origin=None)

    resolved = repos.resolved_all(conn, config)

    assert set(resolved) == {"jantman/demo"}


# -- a configured path suppresses derivation entirely (T044) ----------------


def test_a_configured_path_is_used_and_derivation_is_not_consulted(
    repo_clone, layout, tmp_path, monkeypatch
):
    """FR-007's first half. ``locate`` must not compute the derived candidate at all when
    a section supplies one — not merely prefer the configured answer — because a rule that
    still runs is a rule that can still be reached by mistake later."""
    elsewhere = make_repo(tmp_path / "elsewhere")
    config = build_config(
        repo_clone, layout, tmp_path, repos={"jantman/demo": {"path": str(elsewhere)}}
    )
    monkeypatch.setattr(
        repos, "derive_path", lambda *a, **k: pytest.fail("derivation must not be consulted")
    )

    path, source = repos.locate(config, "jantman/demo")

    assert (path, source) == (elsewhere, "configured")


def test_a_section_without_a_path_still_derives(repo_clone, layout, tmp_path):
    """The other half of FR-003: a section is a set of overrides, and not writing a
    ``path`` is now a valid way to write one."""
    config = build_config(
        repo_clone, layout, tmp_path, repos={"jantman/demo": {"base_branch": "trunk"}}
    )

    path, source = repos.locate(config, "jantman/demo")

    assert (path, source) == (config.repo_root / "demo", "derived")
    assert config.repos["jantman/demo"].base_branch == "trunk", "the override still applies"


def test_a_configured_path_runs_the_same_verification_a_derived_one_does(
    repo_clone, layout, tmp_path, boundaries
):
    """FR-007's second half. A configured path can be wrong as easily as a derived one, so
    it is not trusted — it is merely not *derived*."""
    wrong = make_repo(tmp_path / "wrong", origin="git@github.com:someoneelse/other.git")
    config = build_config(
        repo_clone, layout, tmp_path, repos={"jantman/demo": {"path": str(wrong)}}
    )

    verification = repos.verify(config, "jantman/demo", boundaries.version_control)

    assert not verification.ok
    assert verification.cause == "wrong_repository"
    assert verification.path_source == "configured"


# -- the shared preparation steps (T051, T053) ------------------------------


SHARED = [{"run": "echo shared", "timeout": 7}]


def test_a_repository_with_no_section_inherits_the_shared_steps(
    repo_clone, layout, tmp_path, conn
):
    """US4. Onboarding is enough now, so "no section" must not mean "no preparation" —
    otherwise the majority of repositories would dispatch into an unprepared worktree."""
    config = build_config(repo_clone, layout, tmp_path, hooks={"post_create": SHARED})
    onboard_row(conn, "jantman/demo", repo_clone)

    resolved = repos.resolve(conn, config, "jantman/demo")

    assert [s.value for s in resolved.post_create] == ["echo shared"]
    assert resolved.post_create[0].timeout == 7


def test_a_repositorys_own_steps_replace_the_shared_ones_rather_than_appending(
    repo_clone, layout, tmp_path, conn
):
    """FR-020, research R10. A repository that writes its own steps needs *different*
    steps, not the common one plus extras — and appending would make the shared default
    impossible to opt out of, forcing every exception repository to work around it."""
    config = build_config(
        repo_clone,
        layout,
        tmp_path,
        hooks={"post_create": SHARED},
        repos={"jantman/demo": {"path": str(repo_clone), "post_create": [{"run": "npm ci"}]}},
    )
    onboard_row(conn, "jantman/demo", repo_clone)

    resolved = repos.resolve(conn, config, "jantman/demo")

    assert [s.value for s in resolved.post_create] == ["npm ci"]
    assert "echo shared" not in [s.value for s in resolved.post_create]


def test_neither_set_still_produces_no_steps_at_all(repo_clone, layout, tmp_path, conn):
    """Today's behaviour, preserved. A repository with neither runs no preparation, and
    that must stay a reachable state rather than becoming impossible."""
    config = build_config(repo_clone, layout, tmp_path)
    onboard_row(conn, "jantman/demo", repo_clone)

    assert repos.resolve(conn, config, "jantman/demo").post_create == ()


# -- the spec-kit column (milestone 007, user story 3) ----------------------
#
# The reason this column exists: milestone 007 switches the prompt guidance on by itself
# when a repository turns out to use Spec Kit, so the compensation is that the author can
# see which repositories that is *before* labelling anything.


def _speckit_ctx(conn, audit, config):
    from tests.conftest import make_boundaries

    from robot_army import operations
    from robot_army.effects import EffectLevel

    return operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )


def _speckit_rows(conn, audit, config):
    from robot_army import operations

    result = operations.repos(_speckit_ctx(conn, audit, config))
    return {entry["repo_key"]: entry for entry in result.data["repos"]}, result


SPECKIT_FILES = {
    ".specify/templates/spec-template.md": "# Feature Specification\n",
    **{
        f".claude/skills/speckit-{name}/SKILL.md": f"# speckit-{name}\n"
        for name in ("specify", "plan", "tasks", "implement")
    },
}


def test_a_speckit_clone_reports_yes(conn, audit, config, tmp_path):
    from tests.conftest import onboard_repo

    clone = make_repo(tmp_path / "clones" / "sk", files=SPECKIT_FILES)
    onboard_repo(conn, "jantman/sk", clone)

    rows, result = _speckit_rows(conn, audit, config)

    assert rows["jantman/sk"]["speckit"]["detected"] is True
    assert rows["jantman/sk"]["speckit"]["form"] == "skills"
    assert "spec-kit" in "\n".join(result.lines)


def test_a_plain_clone_reports_no_with_a_reason(conn, audit, config, repo_clone):
    from tests.conftest import onboard_repo

    onboard_repo(conn, "jantman/plain", repo_clone)

    rows, _ = _speckit_rows(conn, audit, config)

    assert rows["jantman/plain"]["speckit"]["detected"] is False
    assert "no spec kit scaffolding" in rows["jantman/plain"]["speckit"]["reason"]


def test_a_suppressed_repository_reports_detected_and_off(conn, audit, tmp_path, layout):
    """`off` is detected-and-turned-off, which is a third thing from `yes` and `no`."""
    from tests.conftest import config_dict, onboard_repo

    from robot_army.config import parse

    clone = make_repo(tmp_path / "clones" / "demo", files=SPECKIT_FILES)
    monkey_token()
    config = parse(
        config_dict(
            clone,
            layout,
            tmp_path / "worktrees",
            repos={"demo": {"path": str(clone), "base_branch": "main", "speckit": False}},
        ),
        tmp_path / "config.toml",
    )
    onboard_repo(conn, "demo", clone)

    rows, result = _speckit_rows(conn, audit, config)

    assert rows["demo"]["speckit"]["detected"] is True
    assert rows["demo"]["speckit"]["enabled"] is False
    assert rows["demo"]["speckit"]["suppressed_by"] == '[repos."demo"] speckit'
    assert "off" in "\n".join(result.lines)


def test_a_missing_clone_reports_unknown_rather_than_no(conn, audit, config, tmp_path):
    """A clone that has moved is not evidence that Spec Kit is absent, and the listing
    must not assert something it cannot see."""
    from tests.conftest import onboard_repo

    onboard_repo(conn, "jantman/gone", tmp_path / "clones" / "vanished")

    rows, _ = _speckit_rows(conn, audit, config)

    assert rows["jantman/gone"]["speckit"]["detected"] is None
    assert "could not be read" in rows["jantman/gone"]["speckit"]["reason"]


def test_the_listing_makes_no_network_call(conn, audit, config, tmp_path):
    """SC-008. The answer must be available with the machine offline, which is when the
    author is most likely to be looking at a phone rather than a terminal."""
    from tests.conftest import onboard_repo

    from robot_army import operations
    from robot_army.effects import Boundaries, EffectLevel

    class Explodes:
        def __getattr__(self, name: str):
            raise AssertionError(f"the repos listing must not touch the network ({name})")

    clone = make_repo(tmp_path / "clones" / "sk", files=SPECKIT_FILES)
    onboard_repo(conn, "jantman/sk", clone)

    from robot_army.boundaries.git import GitVersionControl

    boundaries = Boundaries(
        level=EffectLevel.LIVE,
        issue_reader=Explodes(),
        issue_writer=Explodes(),
        card_reader=None,
        card_writer=None,
        version_control=GitVersionControl(audit),
        hook_runner=None,
        session_host=None,
        display=None,
        notifier=None,
    )
    ctx = operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=boundaries,
        effect_level=EffectLevel.LIVE,
    )

    result = operations.repos(ctx)

    rows = {entry["repo_key"]: entry for entry in result.data["repos"]}
    assert rows["jantman/sk"]["speckit"]["detected"] is True
