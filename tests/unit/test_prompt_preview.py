"""``robot-army prompt``: the prompt a dispatch would compose, printed and nothing else.

The test that carries the design rather than merely covering it lives next door, in
``tests/integration/test_prompt_preview_matches_dispatch.py``: it asserts the preview's text
*is* the string dispatch puts in the worker's argv. Everything here is about the resolution
that gets to that call — which directory the contextual sections come from, which branch is
named, what the log says, and which stream each thing lands on.

Two things are deliberately asserted about the **absence** of output:

* stdout must carry the prompt and nothing else, because the whole of User Story 3 is that
  the output can be redirected, saved and diffed. A banner would break every such use.
* the audit record must not contain the prompt, the issue body, or the text of either
  optional section. That gap is enumerated and justified in the plan (research R4); a test
  is what keeps a later "helpful" addition from quietly closing it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import (
    config_dict,
    make_boundaries,
    make_speckit_tree,
    monkey_token,
    onboard_repo,
)

from robot_army import cli, db, operations
from robot_army.boundaries import Issue, TransportError
from robot_army.config import parse

REPO = "jantman/demo"

ISSUE = Issue(
    number=7,
    title="Add a thing to the widget",
    body="The widget needs a thing.",
    url="https://github.com/jantman/demo/issues/7",
    labels=("robot-army", "enhancement"),
    author="jantman",
    state="open",
)


def build_config(
    repo_clone: Path,
    layout: Any,
    tmp_path: Path,
    *,
    repo_settings: dict[str, Any] | None = None,
    **overrides: Any,
) -> Any:
    """A config whose repository key is a real ``owner/name``.

    The shared ``config`` fixture keys its repository ``demo``, which every other test can
    live with. This command cannot: a key with no owner is not a repository key, and the
    command refuses it (FR-015), so a fixture using one would test the refusal path by
    accident on every call.
    """
    monkey_token()
    raw = config_dict(repo_clone, layout, tmp_path / "worktrees", **overrides)
    raw["repos"] = {
        REPO: {"path": str(repo_clone), "base_branch": "main", **(repo_settings or {})}
    }
    return parse(raw, tmp_path / "config.toml")


@pytest.fixture
def preview(conn, repo_clone, layout, tmp_path, monkeypatch):
    """A built context, an onboarded repository, and a reader holding :data:`ISSUE`."""

    def build(*, issues: list[Issue] | None = None, clone: Path | None = None, **overrides: Any):
        config = build_config(repo_clone, layout, tmp_path, **overrides)
        onboard_repo(conn, REPO, clone or repo_clone)
        reader_issues = [ISSUE] if issues is None else issues
        monkeypatch.setattr(
            operations,
            "wire",
            lambda level, cfg, log, conn: make_boundaries(log, level=level, reader=_reader(reader_issues)),
        )
        ctx = operations.build_context(config)
        return ctx

    contexts: list[Any] = []

    def factory(**kwargs: Any):
        ctx = build(**kwargs)
        contexts.append(ctx)
        return ctx

    yield factory
    for ctx in contexts:
        ctx.close()


def _reader(issues: list[Issue]):
    from tests.conftest import FakeIssueReader

    return FakeIssueReader(issues)


def records(layout, action: str) -> list[dict]:
    return [
        entry
        for path in sorted(layout.log_dir.glob("audit-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and (entry := json.loads(line))["action"] == action
    ]


# -- the prompt itself ------------------------------------------------------


def test_an_untracked_issue_composes_a_full_prompt(preview, layout):
    """User Story 1: an issue nobody has dispatched still has a prompt."""
    result = preview()
    outcome = operations.prompt_preview(result, REPO, 7)

    assert outcome.code == operations.EXIT_OK
    text = "\n".join(outcome.lines)
    assert f"**Title**: {ISSUE.title}" in text
    assert f"**URL**: {ISSUE.url}" in text
    assert "**Labels**: robot-army, enhancement" in text
    assert ISSUE.body in text
    assert f"You are working on {REPO} issue #7" in text


def test_the_delivery_block_is_always_present(preview):
    """Unconditional in a dispatch, so unconditional here (FR-011)."""
    from robot_army import prompt

    outcome = operations.prompt_preview(preview(), REPO, 7)

    assert prompt.DELIVERY in "\n".join(outcome.lines)


def test_the_branch_is_the_one_a_dispatch_would_derive(preview):
    outcome = operations.prompt_preview(preview(), REPO, 7)

    assert "robot-army/issue-7-add-a-thing-to-the-widget" in "\n".join(outcome.lines)
    assert outcome.data["branch_source"] == "derived"


def test_repository_instructions_are_prepended(preview, repo_clone):
    """A repository's own words outrank everything, which position is how this encodes."""
    (repo_clone / ".claude").mkdir(exist_ok=True)
    (repo_clone / ".claude" / "robot-army.md").write_text(
        "Always run the linter.", encoding="utf-8"
    )

    outcome = operations.prompt_preview(preview(), REPO, 7)

    text = "\n".join(outcome.lines)
    assert text.startswith("Always run the linter.")
    assert outcome.data["instructions"] is True


def test_no_instructions_file_is_an_ordinary_absence(preview):
    outcome = operations.prompt_preview(preview(), REPO, 7)

    assert outcome.data["instructions"] is False


def test_a_spec_kit_clone_gets_the_guidance_block(preview, repo_clone):
    from robot_army import speckit

    make_speckit_tree(repo_clone)

    outcome = operations.prompt_preview(preview(), REPO, 7)

    assert speckit.GUIDANCE in "\n".join(outcome.lines)
    assert outcome.data["speckit"] is True


def test_a_suppressed_repository_gets_no_guidance_block(preview, repo_clone):
    """Same gate a dispatch obeys: suppression is the repository's setting, not ours."""
    from robot_army import speckit

    make_speckit_tree(repo_clone)
    ctx = preview(repo_settings={"speckit": False})

    outcome = operations.prompt_preview(ctx, REPO, 7)

    assert speckit.GUIDANCE not in "\n".join(outcome.lines)
    assert outcome.data["speckit"] is False


def test_an_empty_body_gets_the_placeholder(preview):
    """Not an empty section: a dispatch says so in words, so a preview must too."""
    blank = replace(ISSUE, body="   ")
    outcome = operations.prompt_preview(preview(issues=[blank]), REPO, 7)

    assert "_(the issue has no body)_" in "\n".join(outcome.lines)


def test_an_over_long_body_is_truncated_the_way_a_dispatch_truncates_it(preview):
    from robot_army import prompt

    huge = replace(ISSUE, body="x" * (prompt.MAX_BODY_CHARS + 500))
    outcome = operations.prompt_preview(preview(issues=[huge]), REPO, 7)

    text = "\n".join(outcome.lines)
    assert f"[truncated at {prompt.MAX_BODY_CHARS} characters]" in text
    # The notice used to name the issue's URL as somewhere to fetch the rest. On a public
    # repository that page renders every comment on the issue, so the pointer is gone and the
    # URL now appears exactly once, on its own annotated line (RA-06, FR-013).
    assert "full text at" not in text
    assert text.count(ISSUE.url) == 1


# -- refusals ---------------------------------------------------------------


def test_a_key_without_an_owner_is_a_usage_error(preview):
    outcome = operations.prompt_preview(preview(), "demo", 7)

    assert outcome.code == operations.EXIT_USAGE
    assert "not a repository key" in outcome.lines[0]


def test_a_non_positive_issue_number_is_a_usage_error(preview):
    outcome = operations.prompt_preview(preview(), REPO, 0)

    assert outcome.code == operations.EXIT_USAGE
    assert "not an issue number" in outcome.lines[0]


def test_a_repository_that_was_never_onboarded_is_a_precondition_failure(preview):
    """Onboarding is the gate, inherited rather than reinvented.

    Exit ``3`` rather than ``1`` so that "I never approved this repository" stays
    distinguishable from "GitHub would not answer" without parsing a message (SC-007).
    """
    outcome = operations.prompt_preview(preview(), "jantman/other", 7)

    assert outcome.code == operations.EXIT_PRECONDITION
    assert "not onboarded" in outcome.lines[0]


def test_an_issue_that_does_not_exist_fails(preview):
    outcome = operations.prompt_preview(preview(), REPO, 404)

    assert outcome.code == operations.EXIT_FAILED
    assert "does not exist" in outcome.lines[0]


def test_a_transport_failure_never_produces_a_partial_prompt(preview, monkeypatch):
    """A guessed prompt reads exactly like one that did not guess, which is why there is none."""
    ctx = preview()
    ctx.boundaries.issue_reader.raise_on_get_issue = TransportError("GitHub is down")

    outcome = operations.prompt_preview(ctx, REPO, 7)

    assert outcome.code == operations.EXIT_FAILED
    assert "GitHub is down" in outcome.lines[0]
    assert not any("Title" in line for line in outcome.lines)


# -- the context note -------------------------------------------------------


def test_the_clone_is_named_as_the_context_source(preview, repo_clone, capsys):
    import sys

    outcome = operations.prompt_preview(preview(), REPO, 7, notes=sys.stderr)

    assert outcome.data["context_source"] == "clone"
    assert f"context read from the clone at {repo_clone}" in capsys.readouterr().err


def test_a_missing_clone_directory_omits_the_sections_and_says_so(
    preview, repo_clone, tmp_path, capsys
):
    """The omission a reader must not mistake for the repository genuinely having none.

    Reached the way it is actually reached in life: the clone was approved and has since
    been moved or deleted. The onboarding record wins ``path``, so the command looks where
    the maintainer approved rather than re-deriving a guess.
    """
    import sys

    gone = tmp_path / "clones" / "vanished"
    ctx = preview(clone=gone)

    outcome = operations.prompt_preview(ctx, REPO, 7, notes=sys.stderr)

    assert outcome.code == operations.EXIT_OK
    assert outcome.data["context_source"] == "none"
    assert outcome.data["instructions"] is False
    assert outcome.data["speckit"] is False
    # The path is still named, in the note and in the record. Dropping it would make "this
    # repository has no instructions" indistinguishable from "the wrong directory was read",
    # which is the whole reason the note exists.
    assert outcome.data["context_root"] == str(gone)
    assert f"no readable directory at {gone}" in capsys.readouterr().err


def test_the_note_is_recorded_even_when_no_stream_was_given(preview):
    """A non-CLI caller loses nothing by not passing a stream."""
    outcome = operations.prompt_preview(preview(), REPO, 7)

    assert outcome.data["notes"] and "context read from" in outcome.data["notes"][0]


# -- the work item, when there is one ---------------------------------------


def seed(ctx, *, worktree: Path | None = None, branch: str | None = None, dry_run: bool = False):
    """One work item for :data:`ISSUE`, shaped the way the poller shapes them."""
    with db.transaction(ctx.conn):
        item_id = db.insert_work_item(
            conn=ctx.conn,
            source="github",
            source_id=f"{REPO}#7",
            source_url=ISSUE.url,
            repo_key=REPO,
            issue_number=7,
            title=ISSUE.title,
            body=ISSUE.body,
            labels='["robot-army"]',
            author=ISSUE.author,
            dry_run=dry_run,
        )
        columns = {}
        if worktree is not None:
            columns["worktree_path"] = str(worktree)
        if branch is not None:
            columns["branch"] = branch
        if columns:
            db.update_work_item_columns(ctx.conn, item_id, **columns)
    return item_id


def test_a_recorded_branch_beats_the_derived_one(preview):
    """An issue retitled after dispatch would otherwise be previewed against a branch that
    does not exist."""
    ctx = preview()
    seed(ctx, branch="robot-army/issue-7-what-it-was-called-then")

    outcome = operations.prompt_preview(ctx, REPO, 7)

    assert outcome.data["branch_source"] == "recorded"
    assert "robot-army/issue-7-what-it-was-called-then" in "\n".join(outcome.lines)


def test_an_existing_worktree_beats_the_clone(preview, tmp_path, capsys):
    """User Story 2: the worktree is literally the directory the dispatch read from."""
    import sys

    worktree = tmp_path / "worktrees" / "demo" / "issue-7"
    (worktree / ".claude").mkdir(parents=True)
    (worktree / ".claude" / "robot-army.md").write_text("Worktree rules.", encoding="utf-8")
    ctx = preview()
    item_id = seed(ctx, worktree=worktree)

    outcome = operations.prompt_preview(ctx, REPO, 7, notes=sys.stderr)

    assert outcome.data["context_source"] == "worktree"
    assert outcome.data["context_root"] == str(worktree)
    assert "\n".join(outcome.lines).startswith("Worktree rules.")
    assert f"context read from the worktree at {worktree}" in capsys.readouterr().err
    assert outcome.data["item_id"] == item_id


def test_a_worktree_that_is_gone_falls_back_to_the_clone(preview, tmp_path, repo_clone, capsys):
    """Reclaimed after the work finished, which is the ordinary end of an item's life.

    The note must **not** claim there was no worktree. Caught by review: this note exists to
    stop a reader concluding the wrong thing from a missing section, so a parenthetical that
    is false in one of its two cases is worse than no parenthetical at all.
    """
    import sys

    gone = tmp_path / "worktrees" / "demo" / "issue-7"
    ctx = preview()
    seed(ctx, worktree=gone)

    outcome = operations.prompt_preview(ctx, REPO, 7, notes=sys.stderr)

    err = capsys.readouterr().err
    assert outcome.code == operations.EXIT_OK
    assert outcome.data["context_source"] == "clone"
    assert f"context read from the clone at {repo_clone}" in err
    assert f"this issue's worktree at {gone} is gone" in err
    assert "no worktree for this issue" not in err
    assert outcome.data["recorded_worktree"] == str(gone)


def test_an_issue_that_never_had_a_worktree_says_exactly_that(preview, repo_clone, capsys):
    """The other half of the same distinction, and the reason the wording had to split."""
    import sys

    outcome = operations.prompt_preview(preview(), REPO, 7, notes=sys.stderr)

    err = capsys.readouterr().err
    assert f"context read from the clone at {repo_clone} (no worktree for this issue)" in err
    assert "recorded_worktree" not in outcome.data


def test_a_dry_run_row_is_not_consulted(preview, tmp_path):
    """Below ``live`` there is no worktree on disk and the branch is the derived one, so
    reading the row could not change the answer (research R8)."""
    worktree = tmp_path / "worktrees" / "demo" / "issue-7"
    (worktree / ".claude").mkdir(parents=True)
    (worktree / ".claude" / "robot-army.md").write_text("Simulated.", encoding="utf-8")
    ctx = preview()
    seed(ctx, worktree=worktree, branch="robot-army/simulated", dry_run=True)

    outcome = operations.prompt_preview(ctx, REPO, 7)

    assert outcome.data["branch_source"] == "derived"
    assert outcome.data["context_source"] == "clone"
    assert "item_id" not in outcome.data


def test_a_tracked_issue_names_its_item_in_the_record(preview, layout, tmp_path):
    ctx = preview()
    item_id = seed(ctx, branch="robot-army/issue-7-x")

    operations.prompt_preview(ctx, REPO, 7)

    record = records(layout, "prompt.preview")[-1]
    assert record["detail"]["item_id"] == item_id
    assert record["detail"]["branch_source"] == "recorded"


# -- the record -------------------------------------------------------------


def test_a_successful_run_is_recorded(preview, layout):
    operations.prompt_preview(preview(), REPO, 7)

    record = records(layout, "prompt.preview")[-1]
    assert record["outcome"] == "ok"
    assert record["entity_type"] == "issue"
    assert record["entity_id"] == f"{REPO}#7"
    assert record["detail"]["repo_key"] == REPO
    assert record["detail"]["issue_number"] == 7
    assert record["detail"]["branch_source"] == "derived"
    assert record["detail"]["context_source"] == "clone"
    assert "item_id" not in record["detail"]


@pytest.mark.parametrize(
    ("repo_key", "number", "cause"),
    [
        ("demo", 7, "malformed_arguments"),
        (REPO, 0, "malformed_arguments"),
        ("jantman/other", 7, "not_onboarded"),
        (REPO, 404, "issue_unavailable"),
    ],
)
def test_every_refusal_is_recorded_too(preview, layout, repo_key, number, cause):
    """SC-005: every run leaves a log entry, which is what makes the record complete."""
    operations.prompt_preview(preview(), repo_key, number)

    record = records(layout, "prompt.preview")[-1]
    assert record["outcome"] == "error"
    assert record["detail"]["refused"] is True
    assert record["detail"]["cause"] == cause
    assert record["entity_id"] == f"{repo_key}#{number}"


@pytest.mark.parametrize(
    ("repo_key", "number"),
    [(REPO, 0), ("jantman/other", 7), (REPO, 404)],
)
def test_a_refusal_records_the_fields_it_had_already_resolved(preview, layout, repo_key, number):
    """Every refusal past the key check names the repository and issue as *fields*.

    Reconstruction must not require splitting ``entity_id`` on ``#``: that string is an
    identifier, and treating it as a pair is exactly the kind of parsing the detail fields
    exist to make unnecessary. Caught by review — the code carried these on the transport
    failures only, while the contract promised them on every refusal that had resolved them.
    """
    operations.prompt_preview(preview(), repo_key, number)

    detail = records(layout, "prompt.preview")[-1]["detail"]
    assert detail["repo_key"] == repo_key
    assert detail["issue_number"] == number


def test_a_malformed_key_records_no_repository_field(preview, layout):
    """The one documented exception, and it is not an oversight.

    There is no repository key here — that is what was wrong with the invocation — so a
    ``repo_key`` field would assert something false. ``entity_id`` still carries the raw
    argument pair, so the record says what was asked for.
    """
    operations.prompt_preview(preview(), "demo", 7)

    detail = records(layout, "prompt.preview")[-1]["detail"]
    assert "repo_key" not in detail
    assert "issue_number" not in detail


def test_the_record_never_carries_the_prompt_or_the_issue_body(preview, layout, repo_clone):
    """The gap research R4 enumerates, held open on purpose.

    Dispatch does not record the composed text either. A preview whose log was richer than
    the dispatch's would describe the rehearsal better than the performance — and it would
    put a 60,000-character issue body into an append-only file.
    """
    (repo_clone / ".claude").mkdir(exist_ok=True)
    (repo_clone / ".claude" / "robot-army.md").write_text("secret sauce", encoding="utf-8")

    operations.prompt_preview(preview(), REPO, 7)

    blob = json.dumps(records(layout, "prompt.preview")[-1])
    assert ISSUE.body not in blob
    assert "secret sauce" not in blob
    assert "You are working on" not in blob


def test_a_preview_creates_nothing(preview, repo_clone, tmp_path):
    """FR-012: the only durable effect of a run is its own record."""
    ctx = preview()
    before = ctx.conn.execute("SELECT count(*) FROM work_items").fetchone()[0]

    operations.prompt_preview(ctx, REPO, 7)

    assert ctx.conn.execute("SELECT count(*) FROM work_items").fetchone()[0] == before
    assert not (tmp_path / "worktrees").exists()
    assert not (repo_clone / ".git" / "worktrees").exists()


# -- the streams, end to end through the CLI --------------------------------


def _run(monkeypatch, config_path: Path, *args: str) -> int:
    return cli.main(["--config", str(config_path), *args])


@pytest.fixture
def cli_setup(conn, repo_clone, layout, tmp_path, monkeypatch):
    """The command driven through ``cli.main``, which is where the stream split lives."""
    config = build_config(repo_clone, layout, tmp_path)
    onboard_repo(conn, REPO, repo_clone)
    conn.close()
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level, reader=_reader([ISSUE]))
    )
    monkeypatch.setattr(cli, "load_config", lambda _path: config)
    return tmp_path / "config.toml"


def test_stdout_carries_the_prompt_and_the_note_goes_to_stderr(cli_setup, capsys, monkeypatch):
    """FR-003 and FR-004 in one observation: redirect stdout and the note is not in it."""
    assert _run(monkeypatch, cli_setup, "prompt", REPO, "7") == operations.EXIT_OK

    captured = capsys.readouterr()
    assert captured.out.startswith("This is how the work is expected to be delivered.")
    assert captured.out.endswith("\n")
    assert "context read from" in captured.err
    assert "context read from" not in captured.out


def test_two_runs_produce_byte_identical_stdout(cli_setup, capsys, monkeypatch):
    """SC-003, with the fence nonce pinned.

    RA-06 made one part of the prompt deliberately random per compose, so "byte-identical"
    now means "byte-identical but for the fence delimiter". What this test is actually for
    survives that: nothing *timestamped*, ordered by a set, or otherwise incidental leaks into
    stdout. ``tests/unit/test_prompt_fence.py`` holds the nonce to being the only variation,
    which is what makes pinning it here a narrowing rather than a hole.
    """
    from robot_army import prompt

    monkeypatch.setattr(prompt, "_fence_nonce", lambda: "0" * 16)

    _run(monkeypatch, cli_setup, "prompt", REPO, "7")
    first = capsys.readouterr().out
    _run(monkeypatch, cli_setup, "prompt", REPO, "7")
    second = capsys.readouterr().out

    assert first == second
    assert first


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (("prompt", "demo", "7"), operations.EXIT_USAGE),
        (("prompt", REPO, "0"), operations.EXIT_USAGE),
        (("prompt", "jantman/other", "7"), operations.EXIT_PRECONDITION),
        (("prompt", REPO, "404"), operations.EXIT_FAILED),
    ],
)
def test_a_failing_run_says_nothing_on_stdout(cli_setup, capsys, monkeypatch, args, code):
    """SC-007 and FR-014: the code distinguishes the failure, and the file stays empty."""
    assert _run(monkeypatch, cli_setup, *args) == code

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip()


def test_the_command_has_no_json_flag(capsys):
    """Research R7: a machine-readable mode whose whole content is one opaque string."""
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["prompt", REPO, "7", "--json"])
    assert "prompt" not in cli.READ_COMMANDS
