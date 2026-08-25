"""Repository resolution against adversarial card text (T031, R8).

The naive version of this is a security-adjacent bug, and these tests are written against
that bug rather than against the happy path. A bare ``owner/name`` pattern matches
``src/robot_army``, ``docs/roadmap.md``, and any two-segment path in a pasted log — and a
card description is, by the planning document's own framing, semi-untrusted text that may
be pasted from a log.

The structural answer is that **candidates are filtered against the configured
repositories before they count**. An unknown reference cannot select anything, so the worst
case is ``needs_info``, which is the safe direction. The tests below try hard to get a
repository selected by something the author did not mean.
"""

from __future__ import annotations

import pytest
from tests.conftest import config_dict, make_repo, monkey_token

from robot_army.config import parse
from robot_army.intake import resolve_repository


@pytest.fixture
def multi_config(tmp_path, layout):
    """Two configured repositories, keyed the way the project keys them.

    ``owner/name``, not a short nickname: that is what ``[repos."you/example-repo"]`` looks
    like in the shipped example and what ``GitHubReader._repo_path`` requires. Using short
    keys here would have tested a resolution rule the product does not have.
    """
    monkey_token()
    demo = make_repo(tmp_path / "clones" / "demo")
    other = make_repo(tmp_path / "clones" / "other")
    raw = config_dict(demo, layout, tmp_path / "worktrees")
    raw["repos"] = {
        "jantman/demo": {"path": str(demo), "base_branch": "main"},
        "jantman/other": {"path": str(other), "base_branch": "main"},
    }
    return parse(raw, tmp_path / "config.toml")


def resolve(config, title="", body=""):
    return resolve_repository(title, body, config)


# -- the adversarial cases --------------------------------------------------


def test_a_pasted_log_full_of_paths_resolves_to_nothing(multi_config):
    """The case R8 exists for. Every fragment here matches a bare ``owner/name``, and not
    one of them is a repository the author named."""
    body = """
    Traceback (most recent call last):
      File "src/robot_army/dispatch.py", line 412, in select_and_dispatch
      File "lib/python3.14/site-packages/httpx/_client.py", line 90
    See docs/roadmap.md and specs/003-trello-source/spec.md for context.
    Also tried usr/bin/env and etc/hosts.
    """
    result = resolve(multi_config, "Something broke", body)
    assert not result.resolvable
    assert result.candidates == ()
    assert "no configured repository" in result.reason


def test_an_unconfigured_owner_name_resolves_to_nothing_and_says_which(multi_config):
    """FR-012 wants the rejection actionable: naming what was seen is what makes it so."""
    result = resolve(multi_config, "Fix it", "over in someone/other-project please")
    assert not result.resolvable
    # It names what *is* configured, which is the edit the author has to make.
    assert "jantman/demo" in result.reason and "jantman/other" in result.reason


@pytest.mark.parametrize(
    "text",
    [
        "check demos/demo and vendor/demo",
        "jantman/demo-staging is the one",
        "otherowner/demo has the same name",
    ],
)
def test_a_reference_that_merely_looks_like_a_configured_one_is_not_accepted(multi_config, text):
    """A last-segment rule would accept every one of these. It would also buy nothing:
    repository keys are ``owner/name`` throughout, so an exact match is the natural one."""
    assert not resolve(multi_config, "", text).resolvable


# -- the resolvable cases ---------------------------------------------------


def test_a_github_url_resolves(multi_config):
    result = resolve(multi_config, "Fix the thing", "see https://github.com/jantman/demo/issues/4")
    assert result.repo_key == "jantman/demo"


@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/jantman/demo",
        "http://github.com/jantman/demo/",
        "github.com/jantman/demo.git",
        "https://www.github.com/jantman/demo/pull/12",
    ],
)
def test_every_shape_a_pasted_github_link_takes(multi_config, text):
    assert resolve(multi_config, "", text).repo_key == "jantman/demo"


def test_a_bare_owner_name_resolves_when_it_is_configured(multi_config):
    assert resolve(multi_config, "jantman/demo is broken", "").repo_key == "jantman/demo"


def test_a_local_path_inside_a_configured_clone_resolves(multi_config):
    clone = multi_config.repos["jantman/demo"].path
    result = resolve(multi_config, "", f"the failure is in {clone}/src/thing.py")
    assert result.repo_key == "jantman/demo"


def test_a_url_and_the_same_repositorys_local_path_are_one_reference(multi_config):
    """Deduplicated by *resolved key* before counting, so a thorough card is resolvable
    rather than punished for being thorough."""
    clone = multi_config.repos["jantman/demo"].path
    result = resolve(
        multi_config,
        "Fix the thing",
        f"https://github.com/jantman/demo — locally at {clone}",
    )
    assert result.repo_key == "jantman/demo"
    assert result.candidates == ("jantman/demo",)


def test_the_same_repository_named_three_times_is_still_one(multi_config):
    result = resolve(
        multi_config,
        "jantman/demo is broken",
        "https://github.com/jantman/demo and also jantman/demo",
    )
    assert result.repo_key == "jantman/demo"


# -- ambiguity --------------------------------------------------------------


def test_two_different_configured_repositories_are_ambiguous(multi_config):
    result = resolve(
        multi_config,
        "Fix both",
        "https://github.com/jantman/demo and https://github.com/jantman/other",
    )
    assert not result.resolvable
    assert result.candidates == ("jantman/demo", "jantman/other")
    assert "exactly one" in result.reason


def test_an_ambiguous_card_is_never_resolved_to_either(multi_config):
    """The safe direction. Picking one would file an issue in a repository the author did
    not single out, which is unrecoverable in the sense that matters — it is *visible*."""
    result = resolve(multi_config, "", "jantman/demo and jantman/other both need it")
    assert result.repo_key is None


def test_a_configured_repository_plus_an_unknown_one_is_not_ambiguous(multi_config):
    """The unknown reference cannot select anything, so it cannot create ambiguity
    either — which is the same filter doing the same job from the other side."""
    result = resolve(multi_config, "", "jantman/demo, and maybe someone/unknown too")
    assert result.repo_key == "jantman/demo"


# -- degenerate input -------------------------------------------------------


def test_an_empty_card_resolves_to_nothing(multi_config):
    assert not resolve(multi_config, "", "").resolvable


def test_the_title_is_scanned_as_well_as_the_body(multi_config):
    assert resolve(multi_config, "jantman/demo: fix the thing", "").repo_key == "jantman/demo"


def test_an_installation_with_no_repositories_configured_resolves_nothing(
    tmp_path, layout, repo_clone
):
    monkey_token()
    raw = config_dict(repo_clone, layout, tmp_path / "worktrees")
    raw["repos"] = {}
    config = parse(raw, tmp_path / "config.toml")
    result = resolve(config, "", "https://github.com/jantman/demo")
    assert not result.resolvable
    assert "none" in result.reason
