"""Repository resolution against adversarial card text (T031, R8).

The naive version of this is a security-adjacent bug, and these tests are written against
that bug rather than against the happy path. A bare ``owner/name`` pattern matches
``src/robot_army``, ``docs/roadmap.md``, and any two-segment path in a pasted log — and a
card description is, by the planning document's own framing, semi-untrusted text that may
be pasted from a log.

The structural answer is that **candidates are filtered against the onboarded
repositories before they count**. An unknown reference cannot select anything, so the worst
case is ``needs_info``, which is the safe direction. The tests below try hard to get a
repository selected by something the author did not mean.

Milestone 005 narrowed that filter from "configured" to "onboarded" (research R8), which
makes it strictly stricter: a section is no longer enough for a card to select a
repository, and a repository with no section is now selectable once it has been onboarded.
"""

from __future__ import annotations

import pytest
from tests.conftest import config_dict, make_repo, monkey_token, onboard_repo

from robot_army.config import parse
from robot_army.intake import resolve_repository


@pytest.fixture
def multi_config(conn, tmp_path, layout):
    """Two onboarded repositories, keyed the way the project keys them.

    ``owner/name``, not a short nickname: that is what ``[repos."you/example-repo"]`` looks
    like in the shipped example and what ``GitHubReader._repo_path`` requires. Using short
    keys here would have tested a resolution rule the product does not have.

    One of the two deliberately has **no** ``[repos.*]`` section. That is milestone 005's
    headline case, and putting it in the shared fixture means every adversarial case below
    is run against it rather than only the one test that thought to.
    """
    monkey_token()
    demo = make_repo(tmp_path / "clones" / "demo")
    other = make_repo(tmp_path / "clones" / "other")
    raw = config_dict(demo, layout, tmp_path / "worktrees")
    raw["repos"] = {"jantman/demo": {"path": str(demo), "base_branch": "main"}}
    onboard_repo(conn, "jantman/demo", demo)
    onboard_repo(conn, "jantman/other", other)
    return parse(raw, tmp_path / "config.toml")


@pytest.fixture
def resolve(conn):
    def _resolve(config, title="", body=""):
        return resolve_repository(conn, title, body, config)

    return _resolve


# -- the adversarial cases --------------------------------------------------


def test_a_pasted_log_full_of_paths_resolves_to_nothing(resolve, multi_config):
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
    assert "no onboarded repository" in result.reason


def test_an_unonboarded_owner_name_resolves_to_nothing_and_says_which(resolve, multi_config):
    """FR-012 wants the rejection actionable: naming what was seen is what makes it so."""
    result = resolve(multi_config, "Fix it", "over in someone/other-project please")
    assert not result.resolvable
    # It names what *is* onboarded, which is the edit the author has to make.
    assert "jantman/demo" in result.reason and "jantman/other" in result.reason


@pytest.mark.parametrize(
    "text",
    [
        "check demos/demo and vendor/demo",
        "jantman/demo-staging is the one",
        "otherowner/demo has the same name",
    ],
)
def test_a_reference_that_merely_looks_like_an_onboarded_one_is_not_accepted(resolve, multi_config, text):
    """A last-segment rule would accept every one of these. It would also buy nothing:
    repository keys are ``owner/name`` throughout, so an exact match is the natural one."""
    assert not resolve(multi_config, "", text).resolvable


# -- the resolvable cases ---------------------------------------------------


def test_a_github_url_resolves(resolve, multi_config):
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
def test_every_shape_a_pasted_github_link_takes(resolve, multi_config, text):
    assert resolve(multi_config, "", text).repo_key == "jantman/demo"


def test_a_bare_owner_name_resolves_when_it_is_onboarded(resolve, multi_config):
    assert resolve(multi_config, "jantman/demo is broken", "").repo_key == "jantman/demo"


def test_a_local_path_inside_an_onboarded_clone_resolves(resolve, multi_config):
    clone = multi_config.repos["jantman/demo"].path
    result = resolve(multi_config, "", f"the failure is in {clone}/src/thing.py")
    assert result.repo_key == "jantman/demo"


def test_a_url_and_the_same_repositorys_local_path_are_one_reference(resolve, multi_config):
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


def test_the_same_repository_named_three_times_is_still_one(resolve, multi_config):
    result = resolve(
        multi_config,
        "jantman/demo is broken",
        "https://github.com/jantman/demo and also jantman/demo",
    )
    assert result.repo_key == "jantman/demo"


# -- ambiguity --------------------------------------------------------------


def test_two_different_onboarded_repositories_are_ambiguous(resolve, multi_config):
    result = resolve(
        multi_config,
        "Fix both",
        "https://github.com/jantman/demo and https://github.com/jantman/other",
    )
    assert not result.resolvable
    assert result.candidates == ("jantman/demo", "jantman/other")
    assert "exactly one" in result.reason


def test_an_ambiguous_card_is_never_resolved_to_either(resolve, multi_config):
    """The safe direction. Picking one would file an issue in a repository the author did
    not single out, which is unrecoverable in the sense that matters — it is *visible*."""
    result = resolve(multi_config, "", "jantman/demo and jantman/other both need it")
    assert result.repo_key is None


def test_an_onboarded_repository_plus_an_unknown_one_is_not_ambiguous(resolve, multi_config):
    """The unknown reference cannot select anything, so it cannot create ambiguity
    either — which is the same filter doing the same job from the other side."""
    result = resolve(multi_config, "", "jantman/demo, and maybe someone/unknown too")
    assert result.repo_key == "jantman/demo"


# -- degenerate input -------------------------------------------------------


def test_an_empty_card_resolves_to_nothing(resolve, multi_config):
    assert not resolve(multi_config, "", "").resolvable


def test_the_title_is_scanned_as_well_as_the_body(resolve, multi_config):
    assert resolve(multi_config, "jantman/demo: fix the thing", "").repo_key == "jantman/demo"


def test_an_installation_with_nothing_onboarded_resolves_nothing(
    resolve, tmp_path, layout, repo_clone
):
    monkey_token()
    raw = config_dict(repo_clone, layout, tmp_path / "worktrees")
    raw["repos"] = {}
    config = parse(raw, tmp_path / "config.toml")
    result = resolve(config, "", "https://github.com/jantman/demo")
    assert not result.resolvable
    assert "none" in result.reason


# -- the `robot-army:` declaration (milestone 116) ---------------------------
#
# The feature exists for the card that *legitimately* mentions two or three onboarded
# repositories: today that card cannot be filed at all, and the only workaround is to edit
# away the context that made it worth writing. The line is the escape hatch — so the cases
# below are mostly about the two ways an escape hatch goes wrong. It must not widen what a
# card can select (the onboarding filter still gates it), and it must not be silently
# ignored when the author gets it slightly wrong.


#: A body that is genuinely ambiguous — both onboarded repositories, named plainly — so
#: that every case below is testing the declaration against a card the scan cannot resolve.
AMBIGUOUS = "touches jantman/demo and jantman/other, see https://github.com/jantman/other"


def test_a_declaration_picks_one_of_the_repositories_the_card_names(resolve, multi_config):
    """The reported problem, stated as a test: three mentions, one line, one issue."""
    result = resolve(multi_config, "Fix both", AMBIGUOUS + "\nrobot-army: jantman/demo")
    assert result.repo_key == "jantman/demo"
    assert result.candidates == ("jantman/demo",)
    assert result.source == "declaration"


def test_a_declaration_overrides_a_card_that_would_have_resolved_on_its_own(
    resolve, multi_config
):
    """It overrides; it does not break a tie. An override that only applied when the scan
    was confused could not be tested by the author, who cannot see the confusion."""
    result = resolve(multi_config, "", "all about jantman/demo\nrobot-army: jantman/other")
    assert result.repo_key == "jantman/other"
    assert result.source == "declaration"


def test_a_declaration_in_the_title_counts(resolve, multi_config):
    """A consequence of scanning one body of text rather than two, and harmless: a title
    that is nothing but a declaration is a line that is nothing but a declaration."""
    result = resolve(multi_config, "robot-army: jantman/demo", "jantman/other too")
    assert result.repo_key == "jantman/demo"


def test_a_card_with_no_declaration_resolves_exactly_as_before(resolve, multi_config):
    """FR-010, and the reason `_resolve_declarations` returns ``None`` rather than an
    unresolvable verdict: nothing that resolves today may start being held."""
    result = resolve(multi_config, "Fix it", "https://github.com/jantman/demo")
    assert result.repo_key == "jantman/demo"
    assert result.source == "scan"


def test_a_card_with_no_declaration_that_is_held_still_says_scan(resolve, multi_config):
    result = resolve(multi_config, "", "jantman/demo and jantman/other")
    assert not result.resolvable
    assert result.source == "scan"


def test_a_declaration_naming_an_unonboarded_repository_selects_nothing(resolve, multi_config):
    """The security property, from the new direction. The parser can be fooled by a line in
    a pasted log; the onboarding filter is what means nothing comes of it."""
    body = """
    Traceback (most recent call last):
      File "src/robot_army/dispatch.py", line 412, in select_and_dispatch
    robot-army: someone/not-onboarded
    """
    result = resolve(multi_config, "Something broke", body)
    assert result.repo_key is None
    assert "someone/not-onboarded" in result.reason


# -- every spelling the rest of the card accepts (FR-004) -------------------


def test_a_declaration_accepts_a_bare_owner_name(resolve, multi_config):
    result = resolve(multi_config, "", AMBIGUOUS + "\nrobot-army: jantman/demo")
    assert result.repo_key == "jantman/demo"


@pytest.mark.parametrize(
    "reference",
    [
        "https://github.com/jantman/demo",
        "http://github.com/jantman/demo/",
        "github.com/jantman/demo.git",
        "https://www.github.com/jantman/demo",
    ],
)
def test_a_declaration_accepts_every_shape_of_pasted_link(resolve, multi_config, reference):
    result = resolve(multi_config, "", f"{AMBIGUOUS}\nrobot-army: {reference}")
    assert result.repo_key == "jantman/demo"


def test_a_declaration_accepts_the_clone_path(resolve, multi_config):
    clone = multi_config.repos["jantman/demo"].path
    result = resolve(multi_config, "", f"{AMBIGUOUS}\nrobot-army: {clone}")
    assert result.repo_key == "jantman/demo"


def test_a_declaration_accepts_a_path_inside_the_clone(resolve, multi_config):
    clone = multi_config.repos["jantman/demo"].path
    result = resolve(multi_config, "", f"{AMBIGUOUS}\nrobot-army: {clone}/src/thing.py")
    assert result.repo_key == "jantman/demo"


def test_a_declaration_accepts_a_tilde_relative_path(resolve, multi_config, monkeypatch):
    clone = multi_config.repos["jantman/demo"].path
    monkeypatch.setenv("HOME", str(clone.parent.parent))
    relative = clone.relative_to(clone.parent.parent)
    result = resolve(multi_config, "", f"{AMBIGUOUS}\nrobot-army: ~/{relative}")
    assert result.repo_key == "jantman/demo"


def test_a_declaration_selects_a_repository_that_has_no_repos_section(resolve, multi_config):
    """Onboarding, not configuration, is what makes a repository selectable everywhere
    else in this system, and the line is not allowed to be an exception."""
    result = resolve(multi_config, "", "jantman/demo too\nrobot-army: jantman/other")
    assert result.repo_key == "jantman/other"


# -- the grammar's tolerances and its refusals ------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "robot-army: jantman/demo",
        "Robot-Army: jantman/demo",
        "ROBOT-ARMY:jantman/demo",
        "   robot-army:    jantman/demo   ",
        "\trobot-army :\tjantman/demo",
        "`robot-army: jantman/demo`",
        "robot-army: `jantman/demo`",
    ],
)
def test_the_tolerances_the_grammar_deliberately_has(resolve, multi_config, line):
    """Backticks are in this list because the guide renders the line in code style, Trello
    renders markdown, and an author copying it out of the guide would otherwise write a
    line that is invisible on the saved card and does nothing."""
    result = resolve(multi_config, "", f"{AMBIGUOUS}\n{line}")
    assert result.repo_key == "jantman/demo", line


@pytest.mark.parametrize(
    "line",
    [
        "see robot-army: jantman/demo for context",
        "robot-army: jantman/demo (the new one)",
        "robot-army jantman/demo",
        "robot-army:",
        "robot-army: ",
        "-- robot-army: jantman/demo",
    ],
)
def test_a_line_that_is_not_only_a_declaration_declares_nothing(resolve, multi_config, line):
    """Prose about a repository is not an instruction to use it. Each of these leaves the
    card to the ordinary text scan, which on this body is ambiguous."""
    result = resolve(multi_config, "", f"{AMBIGUOUS}\n{line}")
    assert not result.resolvable
    assert result.source == "scan"


def test_prose_mentioning_the_prefix_still_resolves_by_the_ordinary_scan(resolve, multi_config):
    """The other half of the same point: the mention still counts as a *mention*."""
    result = resolve(multi_config, "", "see robot-army: jantman/demo for context")
    assert result.repo_key == "jantman/demo"
    assert result.source == "scan"


def test_a_declaration_cannot_straddle_a_line_break(resolve, multi_config):
    """Why the padding is ``[ \\t]`` and not ``\\s``: under MULTILINE a ``\\s*`` would
    happily cross the newline and defeat the anchors it sits between."""
    result = resolve(multi_config, "", f"{AMBIGUOUS}\nrobot-army:\njantman/demo")
    assert not result.resolvable
    assert result.source == "scan"


# -- when the line does not work --------------------------------------------


def test_a_typod_declaration_holds_the_card_and_quotes_it_back(resolve, multi_config):
    """SC-003. The author has done the thing the generic message asks for; being told to do
    it again is the specific kind of unhelpful this reason exists to avoid."""
    result = resolve(multi_config, "", "jantman/demo is the one\nrobot-army: jantmna/demo")
    assert not result.resolvable
    assert "jantmna/demo" in result.reason
    assert "not an onboarded repository" in result.reason
    assert "jantman/demo" in result.reason and "jantman/other" in result.reason
    assert result.source == "declaration"


def test_a_failed_declaration_never_falls_back_to_the_text_scan(resolve, multi_config):
    """The anti-fallback rule, which is the whole failure path this feature introduces. The
    card's text names exactly one onboarded repository, so the scan *would* have resolved —
    and resolving there would file the issue somewhere the author did not ask for."""
    result = resolve(multi_config, "", "all about jantman/demo\nrobot-army: jantmna/demo")
    assert result.repo_key is None


def test_two_declarations_that_disagree_hold_the_card(resolve, multi_config):
    result = resolve(
        multi_config, "", "robot-army: jantman/demo\nrobot-army: jantman/other"
    )
    assert not result.resolvable
    assert result.candidates == ("jantman/demo", "jantman/other")
    assert "more than one" in result.reason
    assert result.source == "declaration"


def test_two_declarations_naming_the_same_repository_two_ways_are_one(resolve, multi_config):
    """Deduplication is on the resolved key, not on the reference text — the same rule the
    text scan has always used, so a thorough card is not punished for being thorough."""
    clone = multi_config.repos["jantman/demo"].path
    result = resolve(
        multi_config, "", f"robot-army: jantman/demo\nrobot-army: {clone}"
    )
    assert result.repo_key == "jantman/demo"
    assert result.candidates == ("jantman/demo",)


def test_one_good_declaration_and_one_bad_one_holds_the_card(resolve, multi_config):
    """The author wrote both lines. Acting on one and discarding the other is the
    silent-typo failure wearing a different hat."""
    result = resolve(
        multi_config, "", "robot-army: jantman/demo\nrobot-army: jantmna/demo"
    )
    assert result.repo_key is None
    assert "jantmna/demo" in result.reason


def test_a_declaration_on_an_installation_with_nothing_onboarded_holds(
    resolve, tmp_path, layout, repo_clone
):
    monkey_token()
    raw = config_dict(repo_clone, layout, tmp_path / "worktrees")
    raw["repos"] = {}
    config = parse(raw, tmp_path / "config.toml")
    result = resolve(config, "", "robot-army: jantman/demo")
    assert not result.resolvable
    assert "none" in result.reason


def test_one_reference_naming_two_repositories_selects_neither(resolve, multi_config):
    """A comma joins two references into one run of non-whitespace. Picking the first
    would be the system choosing between two things the author wrote — the exact failure
    the line exists to end — so it selects nothing and the card is held."""
    result = resolve(multi_config, "", "robot-army: jantman/demo,jantman/other")
    assert result.repo_key is None
    assert "jantman/demo,jantman/other" in result.reason
    assert result.source == "declaration"
