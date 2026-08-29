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

import io
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


# -- milestone 011: the screen arrives before the question ------------------
#
# Issue #17. The approval screen was always composed above the prompt — the code says so
# and contracts/onboarding.md specifies it — but `Result.say()` only appended to a list
# that `cli.main` printed after the command returned, so the process blocked for input
# with the whole screen still in memory.
#
# None of the tests above could have caught that. They all read `result.lines` after the
# call, where "before the prompt" and "after the prompt" are indistinguishable. The tests
# below are the shape that can: they observe the destination **from inside the prompt**,
# at the instant the run demanded an answer.


class Watcher:
    """A ``confirm`` that records what the maintainer could already have read.

    ``seen`` is the destination's contents at the moment input was demanded. Asserting on
    it rather than on the final output is the whole point: a screen that arrives second
    still "appears somewhere", which is exactly the reading that let this ship.
    """

    def __init__(self, answer: str = "y", *, read=None):
        self.answer = answer
        self._read = read
        self.prompt: str | None = None
        self.seen: str | None = None
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        self.prompt = prompt
        self.seen = self._read()
        return self.answer


def watched_onboard(ctx, key, *, trust, answer="y", stream=None, read=None, **kwargs):
    """Run onboarding with a real destination and a prompt that snapshots it."""
    stream = io.StringIO() if stream is None else stream
    watcher = Watcher(answer, read=read or (lambda: stream.getvalue()))
    result = operations.onboard(
        ctx, key, confirm=watcher, trust_file=trust, out=stream, **kwargs
    )
    return result, watcher, stream


SETTINGS = '{"permissions": {"allow": ["Bash(rm:*)"]}}'


def test_the_whole_screen_is_readable_before_the_prompt_blocks(
    conn, audit, layout, tmp_path, repo_root
):
    """US1 acceptance 1 and 2; FR-001, FR-002, FR-003. The issue itself.

    Every fact the question is about — which repository, where, verified how, against
    which base ref, trusted or not, and what it will honour without asking — had reached
    the maintainer before they were asked to approve it."""
    clone = make_repo(
        repo_root / "demo",
        files={".claude/settings.json": SETTINGS},
        origin="git@github.com:jantman/demo.git",
    )
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result, watcher, _ = watched_onboard(
        ctx, "jantman/demo", trust=trust_file(tmp_path, clone)
    )

    assert result.code == EXIT_OK
    assert watcher.calls == 1
    seen = watcher.seen
    assert "repository   : jantman/demo" in seen
    assert f"clone path   : {clone}   (derived from [paths] repo_root)" in seen
    assert "verified     : github.com/jantman/demo via origin" in seen
    assert "base ref     : main" in seen
    assert "trust        : accepted" in seen
    assert "committed tool-permission settings at the base ref:" in seen
    assert "  --- .claude/settings.json ---" in seen
    assert SETTINGS in seen, "the full text, not merely that a file exists"
    assert "jantman/demo" in watcher.prompt, "the prompt still names what it asks about"


def test_a_clone_with_no_committed_settings_says_so_before_the_prompt(
    conn, audit, layout, tmp_path, repo_root
):
    """US1 acceptance 3. "There are none" is an answer the maintainer needs before
    approving, not after."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    _, watcher, _ = watched_onboard(ctx, "jantman/demo", trust=trust_file(tmp_path, clone))

    assert "no committed .claude/settings*.json at the base ref" in watcher.seen


def test_reapproval_shows_the_recorded_path_its_marker_and_the_diff_first(
    conn, audit, layout, tmp_path, repo_root
):
    """US1 acceptance 4; FR-004. Re-approval's extra lines are the ones most likely to
    change the answer, so they are the ones that least tolerate arriving late."""
    first = make_repo(
        repo_root / "demo", origin="git@github.com:jantman/demo.git"
    )
    moved = make_repo(
        tmp_path / "moved",
        files={".claude/settings.json": SETTINGS},
        origin="git@github.com:jantman/demo.git",
    )
    trust = trust_file(tmp_path, first, moved)
    boundaries = make_boundaries(audit)

    before = context(build_config(repo_root, layout, tmp_path), conn, audit, boundaries)
    assert run_onboard(before, "jantman/demo", trust=trust).code == EXIT_OK

    after = context(
        # A second scratch root: ``build_config`` builds a throwaway fixture clone under
        # the path it is given, and committing the same tree into it twice fails.
        build_config(
            repo_root,
            layout,
            tmp_path / "second",
            repos={"jantman/demo": {"path": str(moved)}},
        ),
        conn,
        audit,
        boundaries,
    )
    result, watcher, _ = watched_onboard(
        after, "jantman/demo", trust=trust, reapprove=True
    )

    assert result.code == EXIT_OK
    seen = watcher.seen
    assert f"recorded path: {first}   ** CHANGED **" in seen
    assert "fingerprint diff against the approved version:" in seen
    assert "approved: (absent)" in seen, "the diff's contents, not only its heading"


def test_the_screen_is_flushed_to_a_redirected_destination_not_merely_buffered(
    conn, audit, layout, tmp_path, repo_root
):
    """US1 acceptance 6; FR-005. The assertion no terminal session can make.

    Read back through a **fresh handle** while the run is still blocked. A fix that writes
    without flushing passes every other test in this file and loses the screen for anyone
    watching a redirected run from another shell."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))
    path = tmp_path / "onboard.out"

    with path.open("w", encoding="utf-8") as handle:
        result, watcher, _ = watched_onboard(
            ctx,
            "jantman/demo",
            trust=trust_file(tmp_path, clone),
            stream=handle,
            read=lambda: path.read_text(encoding="utf-8"),
        )

    assert result.code == EXIT_OK
    assert f"clone path   : {clone}" in watcher.seen
    assert "trust        :" in watcher.seen


def test_declining_after_reading_costs_a_single_run_and_records_no_approval(
    conn, audit, layout, tmp_path, repo_root
):
    """US1 acceptance 5; FR-010. The case issue #17 names: the clone is not where
    `repo_root` implies. One run now learns that and refuses it — before, learning it at
    all required either approving unread or declining and running again."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result, watcher, _ = watched_onboard(
        ctx, "jantman/demo", trust=trust_file(tmp_path, clone), answer="n"
    )

    assert result.code == 4
    assert f"clone path   : {clone}" in watcher.seen
    assert db.get_repo(conn, "jantman/demo") is None


# -- milestone 011, US2: one screen, printed once, on every way out ---------
#
# The screen is now written before the prompt, and every exit below still returns
# `result.lines` to the CLI. If the flush did not also *forget* what it wrote, each of
# these paths would print the screen twice — and a maintainer scrolling back could not
# tell a duplicate from a second repository.

MARKER = "clone path   :"


def emitted(result, stream) -> str:
    """Everything the run put in front of the maintainer, in the order they saw it.

    The stream is what arrived before the prompt; `result.lines` is what `cli.main` prints
    afterwards. Counting across both together is the only way to ask "how many times was
    this said?" — either half alone would miss a duplicate that spans them."""
    return stream.getvalue() + "\n".join(result.lines)


def test_the_approved_path_emits_the_screen_once_and_one_outcome_line(
    conn, audit, layout, tmp_path, repo_root
):
    """US2 acceptance 1; FR-006, FR-007."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result, _, stream = watched_onboard(
        ctx, "jantman/demo", trust=trust_file(tmp_path, clone)
    )

    assert result.code == EXIT_OK
    assert emitted(result, stream).count(MARKER) == 1
    assert emitted(result, stream).count("onboarded jantman/demo") == 1


def test_the_declined_path_emits_the_screen_once_then_aborts(
    conn, audit, layout, tmp_path, repo_root
):
    """US2 acceptance 2; FR-007, FR-008. Exit 4 keeps saying "I decided not to" rather
    than "the system refused"."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result, _, stream = watched_onboard(
        ctx, "jantman/demo", trust=trust_file(tmp_path, clone), answer="n"
    )

    assert result.code == 4
    assert emitted(result, stream).count(MARKER) == 1
    assert result.lines == ["aborted"], "the outcome alone; the screen is already out"


def test_the_yes_refusal_emits_the_screen_once_then_the_refusal(
    conn, audit, layout, tmp_path, repo_root
):
    """US2 acceptance 3. The path whose whole purpose is that the settings above were
    read — so printing them twice, or not at all, both defeat it."""
    clone = make_repo(
        repo_root / "demo",
        files={".claude/settings.json": SETTINGS},
        origin="git@github.com:jantman/demo.git",
    )
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))
    stream = io.StringIO()

    result = operations.onboard(
        ctx,
        "jantman/demo",
        assume_yes=True,
        trust_file=trust_file(tmp_path, clone),
        out=stream,
    )

    assert result.code == EXIT_PRECONDITION
    assert emitted(result, stream).count(MARKER) == 1
    assert SETTINGS in stream.getvalue(), "the settings reached the screen before refusing"
    assert len(result.lines) == 1 and result.lines[0].startswith("refusing --yes:")


def test_an_unchanged_repository_emits_the_screen_once_and_never_asks(
    conn, audit, layout, tmp_path, repo_root
):
    """US2 acceptance 4. Nothing to decide, so nothing is asked — but the screen still
    says what is recorded, once."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))
    trust = trust_file(tmp_path, clone)
    assert run_onboard(ctx, "jantman/demo", trust=trust).code == EXIT_OK

    result, watcher, stream = watched_onboard(ctx, "jantman/demo", trust=trust)

    assert result.code == EXIT_OK
    assert watcher.calls == 0, "an unchanged fingerprint asks nothing"
    assert emitted(result, stream).count(MARKER) == 1
    assert result.lines == [
        "already onboarded and the fingerprint is unchanged; nothing to do"
    ]


def test_a_refusal_before_the_screen_exists_emits_no_screen_at_all(
    conn, audit, layout, tmp_path, repo_root
):
    """US2 acceptance 5; FR-009. Resolution failed, so there is nothing to approve and no
    screen was ever composed — only the refusal, still naming the path, how it was reached
    and the edit that fixes it."""
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))
    stream = io.StringIO()

    result = operations.onboard(ctx, "jantman/never-cloned", out=stream)

    assert result.code == EXIT_PRECONDITION
    assert stream.getvalue() == ""
    text = "\n".join(result.lines)
    assert "no clone at" in text and "repo_root" in text
    assert MARKER not in text


# -- milestone 011, US3: every way out is accountable -----------------------
#
# Two exits used to leave the log holding nothing. They were safe to ignore only while
# nobody reached this prompt informed enough to abandon it; the screen arriving first is
# exactly what changes that.


def onboard_raising(ctx, key, *, trust, error, **kwargs):
    def refuse(_prompt):
        raise error

    return operations.onboard(
        ctx, key, confirm=refuse, trust_file=trust, out=io.StringIO(), **kwargs
    )


def onboard_outcomes(layout) -> list[dict]:
    """One record per terminating path, in either shape it can take.

    Milestone 005 wrote refusals through ``audit.record`` (kind ``event``, outcome
    ``error``) and approvals through ``audit.action`` (an ``intent``/``outcome`` pair), so
    "how did this run end?" has two spellings. Both are answers to it; the invariant is
    that every run leaves exactly one."""
    return [
        r
        for r in audit_records(layout)
        if r["action"] == "repo.onboard"
        and (r["kind"] == "outcome" or r.get("detail", {}).get("refused"))
    ]


def test_interrupting_at_the_prompt_keeps_its_exit_and_gains_a_record(
    conn, audit, layout, tmp_path, repo_root
):
    """US3 acceptance 1 and 2; FR-011. Exit 1 and the word `interrupted` are what Ctrl-C
    produced before, deliberately. What changes is that the log now says it happened."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result = onboard_raising(
        ctx,
        "jantman/demo",
        trust=trust_file(tmp_path, clone),
        error=KeyboardInterrupt(),
    )
    audit.close()

    assert result.code == 1
    assert result.lines == ["interrupted"]
    assert db.get_repo(conn, "jantman/demo") is None
    detail = onboard_outcomes(layout)[-1]["detail"]
    assert detail["cause"] == "interrupted_at_prompt"
    assert detail["clone_path"] == str(clone)
    assert onboard_outcomes(layout)[-1]["entity_id"] == "jantman/demo"


def test_input_ending_before_an_answer_is_a_result_not_a_traceback(
    conn, audit, layout, tmp_path, repo_root
):
    """`robot-army onboard some/repo < /dev/null` used to raise an uncaught EOFError.
    An absent answer is not an approval, so it exits like the decline it is — with its own
    cause, because "input ran out" and "I said no" are different findings in a log."""
    clone = clone_with_origin(repo_root / "demo", "git@github.com:jantman/demo.git")
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    result = onboard_raising(
        ctx, "jantman/demo", trust=trust_file(tmp_path, clone), error=EOFError()
    )
    audit.close()

    assert result.code == 4
    assert "no answer available" in "\n".join(result.lines)
    assert db.get_repo(conn, "jantman/demo") is None
    assert onboard_outcomes(layout)[-1]["detail"]["cause"] == "no_answer_available"


def test_every_way_out_of_onboarding_leaves_exactly_one_outcome_record(
    conn, audit, layout, tmp_path, repo_root
):
    """SC-004, and the invariant this milestone establishes.

    Six exits, six records, one each. Two of them — interruption and end of input — wrote
    nothing at all before. Kept separate from the eleven-cause refusal taxonomy above:
    that test asks whether each *refusal* names its cause, this one asks whether any exit
    can leave without saying anything, which is the Principle III question."""
    trust = trust_file(
        tmp_path,
        clone_with_origin(repo_root / "approve", "git@github.com:jantman/approve.git"),
        clone_with_origin(repo_root / "decline", "git@github.com:jantman/decline.git"),
        clone_with_origin(repo_root / "stopped", "git@github.com:jantman/stopped.git"),
        clone_with_origin(repo_root / "silent", "git@github.com:jantman/silent.git"),
    )
    make_repo(
        repo_root / "unread",
        files={".claude/settings.json": SETTINGS},
        origin="git@github.com:jantman/unread.git",
    )
    config = build_config(repo_root, layout, tmp_path)
    ctx = context(config, conn, audit, make_boundaries(audit))

    exits = {
        "jantman/approve": lambda: watched_onboard(
            ctx, "jantman/approve", trust=trust
        )[0],
        "jantman/decline": lambda: watched_onboard(
            ctx, "jantman/decline", trust=trust, answer="n"
        )[0],
        "jantman/stopped": lambda: onboard_raising(
            ctx, "jantman/stopped", trust=trust, error=KeyboardInterrupt()
        ),
        "jantman/silent": lambda: onboard_raising(
            ctx, "jantman/silent", trust=trust, error=EOFError()
        ),
        "jantman/unread": lambda: operations.onboard(
            ctx, "jantman/unread", assume_yes=True, trust_file=trust, out=io.StringIO()
        ),
        "jantman/never-cloned": lambda: operations.onboard(
            ctx, "jantman/never-cloned", out=io.StringIO()
        ),
    }
    for run in exits.values():
        run()
    audit.close()

    per_repo: dict[str, list[str]] = {}
    for record in onboard_outcomes(layout):
        per_repo.setdefault(record["entity_id"], []).append(record["outcome"])
    assert sorted(per_repo) == sorted(exits), "every exit is accounted for, none twice"
    assert all(len(outcomes) == 1 for outcomes in per_repo.values()), per_repo
