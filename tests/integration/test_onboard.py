"""Onboarding end to end: resolution, the refusal taxonomy, and what gets recorded.

Milestone 005. Three of these tests are worth more than the rest and all three are in the
group CI cannot fully run against the real thing: the wrong-repository refusals (T041),
the credential that must appear nowhere (T042), and the record that is written exactly
once. They guard the only failure in this milestone that is expensive rather than annoying
— a branch created in a repository the author never named.

Every clone here is a **real** git repository with a **real** remote URL, because the
whole verification sequence is a set of questions about a real clone and a fake would only
answer them the way the test expected.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from tests.conftest import (
    FakeIssueReader,
    config_dict,
    make_boundaries,
    make_repo,
    monkey_token,
)

from robot_army import db, operations, poll, repos
from robot_army.config import parse
from robot_army.effects import EffectLevel
from robot_army.operations import EXIT_OK, EXIT_PRECONDITION

pytestmark = pytest.mark.requires_git


# -- fixtures ---------------------------------------------------------------


def clone_with_origin(path: Path, url: str) -> Path:
    """A real primary clone whose ``origin`` is whatever URL the test needs it to be."""
    repo = make_repo(path)
    subprocess.run(
        ["git", "remote", "add", "origin", url], cwd=repo, check=True, capture_output=True
    )
    return repo


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "GIT"
    root.mkdir()
    return root


def build_config(repo_root: Path, layout, tmp_path: Path, **overrides):
    monkey_token()
    raw = config_dict(
        make_repo(tmp_path / "unused-fixture-clone"), layout, tmp_path / "worktrees"
    )
    raw["paths"]["repo_root"] = str(repo_root)
    raw["repos"] = overrides.pop("repos", {})
    for section, values in overrides.items():
        # Merged rather than replaced: the base fixture supplies the credentials and the
        # author, and a test overriding one ``[github]`` key must not drop the rest.
        raw.setdefault(section, {}).update(values)
    return parse(raw, tmp_path / "config.toml")


def trust_file(tmp_path: Path, *clones: Path) -> Path:
    path = tmp_path / "claude.json"
    path.write_text(
        json.dumps(
            {"projects": {str(c.resolve()): {"hasTrustDialogAccepted": True} for c in clones}}
        ),
        encoding="utf-8",
    )
    return path


def context(config, conn, audit, boundaries):
    return operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=boundaries,
        effect_level=EffectLevel.LIVE,
    )


def run_onboard(ctx, key, *, trust: Path, **kwargs):
    return operations.onboard(
        ctx, key, confirm=lambda _: "y", trust_file=trust, **kwargs
    )


def audit_records(layout) -> list[dict]:
    lines: list[dict] = []
    for path in sorted(layout.log_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                lines.append(json.loads(line))
    return lines


# -- the headline path (T032) -----------------------------------------------


def test_a_repository_with_no_section_is_onboarded_polled_and_dispatchable(
    conn, audit, layout, tmp_path, repo_root
):
    """US1's whole claim, in one test: no file was edited, and the repository is now
    watched, resolvable and dispatchable."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    assert config.repos == {}, "the point of the test is that there is no section"
    boundaries = make_boundaries(audit)
    ctx = context(config, conn, audit, boundaries)

    result = run_onboard(ctx, "jantman/demo", trust=trust_file(tmp_path, clone))

    assert result.code == EXIT_OK
    assert repos.known(conn) == ["jantman/demo"]
    resolved = repos.resolve(conn, config, "jantman/demo")
    assert resolved is not None
    assert resolved.path == clone

    record = db.get_repo(conn, "jantman/demo")
    assert record.clone_path == str(clone)
    assert record.path_source == "derived"
    assert record.verified_origin == "github.com/jantman/demo"
    assert record.origin_verified_at is not None


def test_the_approval_screen_names_the_path_and_how_it_was_reached(
    conn, audit, layout, tmp_path, repo_root
):
    """FR-011. The three resolution lines come **first**, ahead of trust and the committed
    settings, because which repository is about to be trusted must be settled before
    anything about trust is read."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result = run_onboard(ctx, "jantman/demo", trust=trust_file(tmp_path, clone))
    text = "\n".join(result.lines)

    assert f"clone path   : {clone}   (derived from [paths] repo_root)" in text
    assert "verified     : github.com/jantman/demo via origin" in text
    assert text.index("clone path") < text.index("trust        :")
    assert text.index("verified") < text.index("trust        :")


def test_a_configured_path_is_reported_as_configured_not_derived(
    conn, audit, layout, tmp_path, repo_root
):
    elsewhere = clone_with_origin(tmp_path / "elsewhere", "git@github.com:jantman/demo.git")
    config = build_config(
        repo_root, layout, tmp_path, repos={"jantman/demo": {"path": str(elsewhere)}}
    )
    ctx = context(config, conn, audit, make_boundaries(audit))

    result = run_onboard(ctx, "jantman/demo", trust=trust_file(tmp_path, elsewhere))

    assert result.code == EXIT_OK
    assert '(configured in [repos."jantman/demo"])' in "\n".join(result.lines)
    assert db.get_repo(conn, "jantman/demo").path_source == "configured"


def test_the_onboarding_record_carries_the_resolution_into_the_audit_log(
    conn, audit, layout, tmp_path, repo_root
):
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    run_onboard(ctx, "jantman/demo", trust=trust_file(tmp_path, clone))
    audit.close()

    onboards = [r for r in audit_records(layout) if r["action"] == "repo.onboard"]
    assert onboards, "onboarding writes an intent/outcome pair"
    detail = onboards[-1]["detail"]
    assert detail["clone_path"] == str(clone)
    assert detail["path_source"] == "derived"
    assert detail["remote"] == "origin"
    assert detail["verified_origin"] == "github.com/jantman/demo"


# -- onboarding while the daemon runs (T033) --------------------------------


def test_a_repository_onboarded_between_cycles_is_polled_with_no_restart(
    conn, audit, layout, tmp_path, repo_root
):
    """Research R7's behaviour change, asserted rather than assumed. The polled set is a
    database read now, so a ``Config`` loaded at process start — which is what a running
    daemon holds — no longer decides what is polled."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    reader = FakeIssueReader()
    boundaries = make_boundaries(audit, reader=reader)

    before = poll.poll_all(
        conn, boundaries=boundaries, audit=audit, config=config, dry_run=False
    )
    assert before == [], "nothing is onboarded yet"

    # The same ``config`` object the daemon would still be holding — deliberately not
    # re-parsed, because "no restart" is the property under test.
    ctx = context(config, conn, audit, boundaries)
    run_onboard(ctx, "jantman/demo", trust=trust_file(tmp_path, clone))

    after = poll.poll_all(
        conn, boundaries=boundaries, audit=audit, config=config, dry_run=False
    )

    assert [o.repo_key for o in after] == ["jantman/demo"]
    assert after[0].error is None


def test_a_repository_appearing_between_cycles_needs_no_special_handling(
    conn, audit, layout, tmp_path, repo_root
):
    """``poll_state`` is keyed by repository, so a new key simply has no prior state. That
    is the claim research R7 makes and declines to assume."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    boundaries = make_boundaries(audit, reader=FakeIssueReader())
    ctx = context(config, conn, audit, boundaries)
    run_onboard(ctx, "jantman/demo", trust=trust_file(tmp_path, clone))

    assert db.get_poll_state(conn, "jantman/demo").etag is None
    poll.poll_all(conn, boundaries=boundaries, audit=audit, config=config, dry_run=False)

    assert db.get_poll_state(conn, "jantman/demo").last_polled_at is not None


# -- the refusal taxonomy (T040) --------------------------------------------


def refusal(ctx, key, tmp_path, **kwargs):
    result = run_onboard(ctx, key, trust=trust_file(tmp_path), **kwargs)
    return result, "\n".join(result.lines)


def test_no_clone_at_the_derived_path_is_refused_naming_the_path_and_the_edit(
    conn, audit, layout, tmp_path, repo_root
):
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result, text = refusal(ctx, "jantman/never-cloned", tmp_path)

    assert result.code == EXIT_PRECONDITION
    assert f"no clone at {repo_root / 'never-cloned'}" in text
    assert "(derived from [paths] repo_root)" in text
    assert 'set [repos."jantman/never-cloned"] path' in text
    assert db.get_repo(conn, "jantman/never-cloned") is None


def test_a_linked_worktree_is_refused_as_not_a_primary_clone(
    conn, audit, layout, tmp_path, repo_root
):
    primary = clone_with_origin(tmp_path / "primary", "git@github.com:jantman/demo.git")
    cut = repo_root / "demo"
    subprocess.run(
        ["git", "worktree", "add", "-b", "wt", str(cut)],
        cwd=primary,
        check=True,
        capture_output=True,
    )
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result, text = refusal(ctx, "jantman/demo", tmp_path)

    assert result.code == EXIT_PRECONDITION
    assert "is a linked worktree, not a primary clone" in text
    assert "would nest them" in text
    assert db.get_repo(conn, "jantman/demo") is None


def test_a_path_inside_the_worktree_root_is_refused(conn, audit, layout, tmp_path):
    """Two directories that both believe they own a tree is a class of confusion worth one
    comparison to avoid (research R4)."""
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    inside = clone_with_origin(worktree_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(worktree_root, layout, tmp_path)

    ctx = context(config, conn, audit, make_boundaries(audit))
    result, text = refusal(ctx, "jantman/demo", tmp_path)

    assert result.code == EXIT_PRECONDITION
    assert "is inside [paths] worktree_root" in text
    assert str(inside) in text


def test_a_clone_with_no_remote_is_refused(conn, audit, layout, tmp_path, repo_root):
    make_repo(repo_root / "demo")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result, text = refusal(ctx, "jantman/demo", tmp_path)

    assert result.code == EXIT_PRECONDITION
    assert "has no remote configured" in text
    assert db.get_repo(conn, "jantman/demo") is None


def test_several_remotes_and_no_origin_is_refused_as_ambiguous(
    conn, audit, layout, tmp_path, repo_root
):
    clone = make_repo(repo_root / "demo")
    for name, url in (("mine", "git@github.com:jantman/demo.git"), ("theirs", "git@github.com:x/demo.git")):
        subprocess.run(
            ["git", "remote", "add", name, url], cwd=clone, check=True, capture_output=True
        )
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result, text = refusal(ctx, "jantman/demo", tmp_path)

    assert result.code == EXIT_PRECONDITION
    assert "none\nis named origin" in text or "none is named origin" in text
    assert "mine" in text and "theirs" in text
    assert db.get_repo(conn, "jantman/demo") is None


def test_an_unparseable_remote_url_is_refused(conn, audit, layout, tmp_path, repo_root):
    clone_with_origin(repo_root / "demo", "/some/local/path")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result, text = refusal(ctx, "jantman/demo", tmp_path)

    assert result.code == EXIT_PRECONDITION
    assert "could not read the 'origin' remote" in text
    assert db.get_repo(conn, "jantman/demo") is None


def test_every_refusal_writes_an_audit_outcome_naming_its_cause(
    conn, audit, layout, tmp_path, repo_root
):
    """FR-031 and research R11's fix. Before milestone 005 the missing-section refusal
    returned before any audit action was opened, so a refusal was printed and forgotten —
    a live Principle III violation, since a refusal is a result."""
    make_repo(repo_root / "no-remote")
    clone_with_origin(repo_root / "wrong", "git@github.com:someoneelse/wrong.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    for key in ("jantman/absent", "jantman/no-remote", "jantman/wrong", "not-a-key"):
        assert refusal(ctx, key, tmp_path)[0].code == EXIT_PRECONDITION
    audit.close()

    refused = [
        r
        for r in audit_records(layout)
        if r["action"] == "repo.onboard" and r.get("detail", {}).get("refused")
    ]
    assert len(refused) == 4, "one outcome per refusal, including the ones before any prompt"
    assert {r["detail"]["cause"] for r in refused} == {
        "no_clone",
        "no_remote",
        "wrong_repository",
        "malformed_key",
    }
    assert {r["entity_id"] for r in refused} == {
        "jantman/absent",
        "jantman/no-remote",
        "jantman/wrong",
        "not-a-key",
    }


# -- the wrong repository (T041) --------------------------------------------


@pytest.mark.parametrize(
    ("key", "origin", "expect_named"),
    [
        # A different owner — the ZoneMinder/zoneminder shape, and the most common of the
        # five real cases: the author's clone is of upstream, not of their fork.
        ("jantman/zoneminder", "git@github.com:ZoneMinder/zoneminder.git", "zoneminder/zoneminder"),
        # A different name under the same owner.
        ("jantman/troposphere", "git@github.com:jantman/troposphere-fork.git", "jantman/troposphere-fork"),
        # An unrelated upstream entirely.
        ("jantman/demo", "https://gitlab.example.invalid/someone/unrelated.git",
         "gitlab.example.invalid/someone/unrelated"),
    ],
)
def test_wrong_repository_at_derived_path(
    conn, audit, layout, tmp_path, repo_root, key, origin, expect_named
):
    """The scenario that justifies the milestone's size (SC-003).

    Reproduces the shape of all five known cases on the author's machine. None may be
    recorded, and each must name the identity actually found — "that is the wrong
    repository" without saying which one is found is an instruction to go and look.
    """
    name = key.split("/", 1)[1]
    clone = clone_with_origin(repo_root / name, origin)
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result, text = refusal(ctx, key, tmp_path)

    assert result.code == EXIT_PRECONDITION
    assert db.get_repo(conn, key) is None, "nothing may be recorded"
    assert repos.known(conn) == []
    assert str(clone) in text
    assert expect_named in text, "the identity found is named"
    assert key in text, "and so is the identity expected"
    assert 'path = "/where/it/actually/is"' in text, "and the edit that fixes it"


def test_a_configured_path_pointing_at_the_wrong_repository_is_refused_too(
    conn, audit, layout, tmp_path, repo_root
):
    """FR-007: a configured path can be wrong as easily as a derived one, so it runs the
    same sequence. The refusal names the section rather than ``repo_root``."""
    elsewhere = clone_with_origin(tmp_path / "elsewhere", "git@github.com:other/thing.git")
    config = build_config(
        repo_root, layout, tmp_path, repos={"jantman/demo": {"path": str(elsewhere)}}
    )
    ctx = context(config, conn, audit, make_boundaries(audit))

    result, text = refusal(ctx, "jantman/demo", tmp_path)

    assert result.code == EXIT_PRECONDITION
    assert "other/thing" in text
    assert 'configured in [repos."jantman/demo"]' in text
    assert db.get_repo(conn, "jantman/demo") is None


# -- credentials (T042) -----------------------------------------------------

# Distinctive on purpose: the assertions below sweep every record, message and column for
# this exact string, and a placeholder that also appears in ordinary prose would make them
# vacuous. Not a credential — it authenticates against nothing.
SECRET = "ghp_averyrecognisablesecret0123"  # noqa: S105


def test_a_credential_in_the_origin_url_reaches_no_record_message_or_output(
    conn, audit, layout, tmp_path, repo_root
):
    """FR-032. This is the first milestone that reads a git remote URL at all, so it is the
    first time this exposure exists — and the assertion sweeps *everything* written rather
    than the one field the author remembered to check."""
    clone = clone_with_origin(
        repo_root / "demo", f"https://jantman:{SECRET}@github.com/jantman/demo.git"
    )
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result = run_onboard(ctx, "jantman/demo", trust=trust_file(tmp_path, clone))
    audit.close()

    assert result.code == EXIT_OK, "a credentialed URL is a valid clone, not a refusal"
    record = db.get_repo(conn, "jantman/demo")
    assert record.verified_origin == "github.com/jantman/demo"

    assert SECRET not in "\n".join(result.lines)
    assert SECRET not in json.dumps(result.data)
    assert SECRET not in json.dumps(audit_records(layout))
    everything = "\n".join(
        str(row) for row in conn.execute("SELECT * FROM repos").fetchall()
    )
    assert SECRET not in everything


def test_the_same_holds_on_the_refusal_path(conn, audit, layout, tmp_path, repo_root):
    """The refusal branch is where echoing the URL back would feel most helpful, which is
    exactly why it is the branch worth testing."""
    clone_with_origin(
        repo_root / "demo", f"https://jantman:{SECRET}@github.com/someoneelse/demo.git"
    )
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result, text = refusal(ctx, "jantman/demo", tmp_path)
    audit.close()

    assert result.code == EXIT_PRECONDITION
    assert "someoneelse/demo" in text, "the identity is still named"
    assert SECRET not in text
    assert SECRET not in json.dumps(result.data)
    assert SECRET not in json.dumps(audit_records(layout))


# -- the two non-zero exits that are not verification refusals (T039) --------


def test_aborting_at_the_prompt_exits_four_and_is_recorded(
    conn, audit, layout, tmp_path, repo_root
):
    """Exit 4, not 3. "I decided not to" and "the system would not let me" are different
    results, and a script that retries on one must not retry on the other."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result = operations.onboard(
        ctx,
        "jantman/demo",
        confirm=lambda _: "n",
        trust_file=trust_file(tmp_path, clone),
    )
    audit.close()

    assert result.code == 4
    assert db.get_repo(conn, "jantman/demo") is None
    causes = [
        r["detail"]["cause"]
        for r in audit_records(layout)
        if r["action"] == "repo.onboard" and r.get("detail", {}).get("refused")
    ]
    assert causes == ["aborted_at_prompt"]


def test_yes_refuses_unapproved_committed_settings_and_records_that_too(
    conn, audit, layout, tmp_path, repo_root
):
    clone = make_repo(
        repo_root / "demo",
        files={".claude/settings.json": '{"permissions": {"allow": ["Bash(rm:*)"]}}'},
    )
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:jantman/demo.git"],
        cwd=clone,
        check=True,
        capture_output=True,
    )
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result = operations.onboard(
        ctx, "jantman/demo", assume_yes=True, trust_file=trust_file(tmp_path, clone)
    )
    audit.close()

    assert result.code == EXIT_PRECONDITION
    assert db.get_repo(conn, "jantman/demo") is None
    causes = [
        r["detail"]["cause"]
        for r in audit_records(layout)
        if r["action"] == "repo.onboard" and r.get("detail", {}).get("refused")
    ]
    assert causes == ["unapproved_committed_settings"]


# -- what may be onboarded at all (User Story 6, T063, T064) ----------------


def reader_with(**kwargs):
    reader = FakeIssueReader()
    reader.missing_repos = set(kwargs.pop("missing", ()))
    reader.repo_owners = dict(kwargs.pop("owners", {}))
    return reader


def test_a_repository_the_author_owns_is_permitted(conn, audit, layout, tmp_path, repo_root):
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit, reader=reader_with()))

    result = run_onboard(ctx, "jantman/demo", trust=trust_file(tmp_path, clone))

    assert result.code == EXIT_OK
    assert result.data["owner_verdict"] == "owned"


def test_an_unowned_unlisted_repository_is_refused_naming_extra_repos(
    conn, audit, layout, tmp_path, repo_root
):
    clone_with_origin(repo_root / "theirs", "git@github.com:someoneelse/theirs.git")
    config = build_config(repo_root, layout, tmp_path)
    reader = reader_with(owners={"someoneelse/theirs": "someoneelse"})
    ctx = context(config, conn, audit, make_boundaries(audit, reader=reader))

    result, text = refusal(ctx, "someoneelse/theirs", tmp_path)

    assert result.code == EXIT_PRECONDITION
    assert "is not owned by jantman" in text
    assert "[github] extra_repos" in text
    assert db.get_repo(conn, "someoneelse/theirs") is None


def test_an_unowned_repository_listed_in_extra_repos_is_permitted(
    conn, audit, layout, tmp_path, repo_root
):
    clone = clone_with_origin(repo_root / "theirs", "git@github.com:someoneelse/theirs.git")
    config = build_config(
        repo_root, layout, tmp_path, github={"extra_repos": ["someoneelse/theirs"]}
    )
    reader = reader_with(owners={"someoneelse/theirs": "someoneelse"})
    ctx = context(config, conn, audit, make_boundaries(audit, reader=reader))

    result = run_onboard(ctx, "someoneelse/theirs", trust=trust_file(tmp_path, clone))

    assert result.code == EXIT_OK
    assert result.data["owner_verdict"] == "listed"


def test_an_owned_repository_is_refused_naming_include_owned_when_it_is_false(
    conn, audit, layout, tmp_path, repo_root
):
    clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path, github={"include_owned": False})
    ctx = context(config, conn, audit, make_boundaries(audit, reader=reader_with()))

    result, text = refusal(ctx, "jantman/demo", tmp_path)

    assert result.code == EXIT_PRECONDITION
    assert "include_owned is false" in text
    assert db.get_repo(conn, "jantman/demo") is None


def test_a_nonexistent_repository_is_refused_distinctly_from_an_unowned_one(
    conn, audit, layout, tmp_path, repo_root
):
    """Two different mistakes with two different fixes: check the spelling, or add it to
    a list. One message for both would send half the cases to the wrong place."""
    config = build_config(repo_root, layout, tmp_path)
    reader = reader_with(missing={"jantman/tropospere"})
    ctx = context(config, conn, audit, make_boundaries(audit, reader=reader))

    result, text = refusal(ctx, "jantman/tropospere", tmp_path)

    assert result.code == EXIT_PRECONDITION
    assert "has no repository jantman/tropospere" in text
    assert "extra_repos" not in text, "this is a typo, not a permission problem"


def test_the_allowlist_governs_onboarding_only(conn, audit, layout, tmp_path, repo_root):
    """FR-027. A repository already onboarded keeps working if the setting that permitted
    it later changes — revoking access means removing the onboarding record."""
    from dataclasses import replace as _replace

    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit, reader=reader_with()))
    assert run_onboard(ctx, "jantman/demo", trust=trust_file(tmp_path, clone)).code == EXIT_OK

    revoked = _replace(config, github=_replace(config.github, include_owned=False))

    assert repos.known(conn) == ["jantman/demo"]
    assert repos.resolve(conn, revoked, "jantman/demo").path == clone


def test_exactly_one_repository_request_per_attempt_and_no_page_walk(
    conn, audit, layout, tmp_path, repo_root
):
    """T064, SC-009. A fake account with three repositories would pass an implementation
    that enumerates 252, so what is asserted is the request count, not the verdict."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    reader = reader_with()
    ctx = context(config, conn, audit, make_boundaries(audit, reader=reader))

    run_onboard(ctx, "jantman/demo", trust=trust_file(tmp_path, clone))

    assert reader.repo_calls == ["jantman/demo"]
    assert reader.listing_calls == [], "no listing"
    assert reader.poll_calls == [], "and no poll"


def test_eligibility_is_settled_before_the_path_is_even_derived(
    conn, audit, layout, tmp_path, repo_root
):
    """FR-024. Refusing "no clone at ..." for a repository the author mistyped would send
    them looking for a directory rather than at the name they typed."""
    config = build_config(repo_root, layout, tmp_path)
    reader = reader_with(missing={"jantman/tropospere"})
    ctx = context(config, conn, audit, make_boundaries(audit, reader=reader))

    _result, text = refusal(ctx, "jantman/tropospere", tmp_path)

    assert "no clone at" not in text
    assert "has no repository" in text


def test_an_unreachable_source_system_is_a_refusal_not_a_traceback(
    conn, audit, layout, tmp_path, repo_root
):
    """Found by running the real CLI rather than by reading the contract.

    contracts/onboarding.md lists "the source system is unreachable" as a refusal of step 2.
    Onboarding is the one command that must ask the source system a question, and a bad
    token is the most ordinary way for that to fail — so it gets the same named, recorded,
    non-zero exit every other refusal gets. It exited 120 with a traceback before this."""
    from robot_army.boundaries import TransportError

    clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    reader = FakeIssueReader()

    def explode(_key):
        raise TransportError("GET /repos/jantman/demo failed with HTTP 401: Bad credentials")

    reader.get_repo = explode
    ctx = context(config, conn, audit, make_boundaries(audit, reader=reader))

    result, text = refusal(ctx, "jantman/demo", tmp_path)
    audit.close()

    assert result.code == EXIT_PRECONDITION
    assert "could not ask" in text
    assert "Bad credentials" in text
    assert "Check the token and the network" in text
    assert db.get_repo(conn, "jantman/demo") is None
    causes = [
        r["detail"]["cause"]
        for r in audit_records(layout)
        if r["action"] == "repo.onboard" and r.get("detail", {}).get("refused")
    ]
    assert causes == ["source_unreachable"]
