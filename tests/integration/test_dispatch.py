"""Dispatch: the launch chain, confirmation, and the concurrency cap (T063).

The central assertion here is FR-025's: **an unconfirmed launch yields ``failed``, never
``active``.** ``kitty @ launch`` returns ``0`` and a valid window id even when nothing
started — demonstrated three times in M0 (F16) with no diagnostic anywhere — so a test
that only checks the happy path would pass against a completely broken launcher.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import (
    RecordingWriter,
    StubDisplay,
    StubSessionHost,
    make_boundaries,
    make_issue,
    seed_item,
)

from robot_army import db, dispatch
from robot_army.boundaries import BoundaryError
from robot_army.boundaries.hooks import SubprocessHookRunner
from robot_army.config import HookStep
from robot_army.states import SessionState, WorkItemState

pytestmark = pytest.mark.requires_git


def ready_item(conn, config, **kwargs) -> int:
    # The clone location is part of an approval since milestone 005, and
    # ``dispatch.check_gates`` re-verifies it before creating anything (FR-028). A row
    # without one is the pre-005 shape FR-014 deliberately blocks.
    kwargs.setdefault("clone_path", config.repos["demo"].path)
    return seed_item(conn, state=str(WorkItemState.READY), **kwargs)


def trust_file(tmp_path: Path, *clones: Path) -> Path:
    path = tmp_path / "claude.json"
    path.write_text(
        json.dumps(
            {"projects": {str(c.resolve()): {"hasTrustDialogAccepted": True} for c in clones}}
        ),
        encoding="utf-8",
    )
    return path


def test_a_confirmed_dispatch_reaches_active(conn, audit, config, tmp_path, layout):
    writer = RecordingWriter()
    host = StubSessionHost(confirm=True)
    boundaries = make_boundaries(
        audit, writer=writer, host=host, hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)

    assert dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )

    item = db.get_work_item(conn, item_id)
    assert item is not None
    assert item.state is WorkItemState.ACTIVE
    assert item.active_at is not None
    assert Path(item.worktree_path or "").is_dir()

    session = db.latest_session_for_item(conn, item_id)
    assert session is not None
    assert session.state is SessionState.RUNNING
    assert session.confirmed_at is not None
    assert session.attempt == 1
    assert writer.comments, "a dispatch comment is posted"


def test_a_healthy_dispatch_raises_no_anomaly(conn, audit, config, tmp_path, layout):
    """Issue #58, and the assertion whose absence let it ship.

    The transcript check used to run one line after the session was confirmed, and the
    worker writes its transcript when it begins processing, not at exec. So the file
    reliably did not exist yet and ``no_transcript`` fired on **every healthy dispatch** --
    including the very first live one ever performed. Dispatch now asks nothing about
    transcripts at all; the question moved to reconciliation, where it can wait.

    Deliberately asserts the *whole* anomalies table is empty rather than the absence of one
    kind. A dispatch that succeeds has nothing anomalous to report about anything.
    """
    writer = RecordingWriter()
    host = StubSessionHost(confirm=True)
    boundaries = make_boundaries(
        audit, writer=writer, host=host, hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)

    assert dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )

    assert db.list_anomalies(conn) == []


def test_a_dispatched_session_leaves_its_transcript_question_open(
    conn, audit, config, tmp_path, layout
):
    """The other half of moving the check: dispatch must not *answer* the question either.

    A session row leaves dispatch with ``transcript_checked_at`` NULL, which is what puts it
    in front of the reconciliation sweep at all. Marking it answered here would be the same
    bug wearing the opposite sign -- silence instead of noise.
    """
    host = StubSessionHost(confirm=True)
    boundaries = make_boundaries(
        audit, writer=RecordingWriter(), host=host, hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)

    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )

    session = db.latest_session_for_item(conn, item_id)
    assert session is not None
    assert session.transcript_checked_at is None


def test_an_unconfirmed_launch_yields_failed_never_active(
    conn, audit, config, tmp_path, layout
):
    """If a broken launch produces an ``active`` item, FR-025 is not really implemented."""
    host = StubSessionHost(confirm=False)
    boundaries = make_boundaries(audit, host=host, hooks=SubprocessHookRunner(audit))
    item_id = ready_item(conn, config)

    assert not dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )

    item = db.get_work_item(conn, item_id)
    assert item is not None
    assert item.state is WorkItemState.FAILED
    assert item.active_at is None
    assert "not confirmed" in (item.failure_reason or "")

    session = db.latest_session_for_item(conn, item_id)
    assert session is not None
    assert session.state is SessionState.LOST


def test_an_unconfirmed_launch_records_the_argv_and_window_for_diagnosis(
    conn, audit, config, tmp_path, layout
):
    """FR-027: a failed launch must leave diagnosable evidence."""
    boundaries = make_boundaries(
        audit, host=StubSessionHost(confirm=False), hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)
    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    audit.close()
    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    records = [
        json.loads(line) for line in text.splitlines() if '"dispatch.unconfirmed"' in line
    ]
    assert len(records) == 1
    assert records[0]["detail"]["launch_argv"]
    assert records[0]["detail"]["window_id"]


def test_the_session_row_exists_before_the_process_is_launched(
    conn, audit, config, tmp_path, layout
):
    """FR-020: a process that dies before writing anything still has a row naming it, so
    reconciliation has something to reason about rather than a gap."""
    seen: list[int] = []

    class ObservingDisplay(StubDisplay):
        def open(self, cwd, argv, title, user_vars, env):
            seen.append(
                conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
            )
            return super().open(cwd, argv, title, user_vars, env)

    boundaries = make_boundaries(
        audit, display=ObservingDisplay(), hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)
    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    assert seen == [1], "the session row must already exist when the launch happens"


def test_a_hook_failure_launches_no_session(conn, audit, config, tmp_path, layout):
    from dataclasses import replace

    repo = replace(
        config.repos["demo"],
        post_create=(HookStep(kind="run", value="exit 9", timeout=5),),
    )
    config = replace(config, repos={"demo": repo})

    display = StubDisplay()
    boundaries = make_boundaries(
        audit, display=display, hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)

    assert not dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    assert display.opened == [], "a session was launched into a failed worktree"
    item = db.get_work_item(conn, item_id)
    assert item is not None and item.state is WorkItemState.FAILED
    assert "exit 9" in (item.prepare_output or "")


def test_an_untrusted_repository_blocks_dispatch(conn, audit, config, tmp_path, layout):
    display = StubDisplay()
    boundaries = make_boundaries(audit, display=display, hooks=SubprocessHookRunner(audit))
    item_id = ready_item(conn, config)

    assert not dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=tmp_path / "absent.json",
    )
    assert display.opened == []
    item = db.get_work_item(conn, item_id)
    assert item is not None
    assert item.state is WorkItemState.FAILED
    assert "trust check failed" in (item.blocked_reason or "")


def test_a_launch_error_leaves_the_item_failed_and_the_session_lost(
    conn, audit, config, tmp_path, layout
):
    class BrokenDisplay(StubDisplay):
        def open(self, *args, **kwargs):
            raise BoundaryError("kitty launch failed: no socket answered")

    boundaries = make_boundaries(
        audit, display=BrokenDisplay(), hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)
    assert not dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    item = db.get_work_item(conn, item_id)
    assert item is not None and item.state is WorkItemState.FAILED
    session = db.latest_session_for_item(conn, item_id)
    assert session is not None and session.state is SessionState.LOST


def test_the_launch_chain_has_no_double_dash_after_dtach(
    conn, audit, config, tmp_path, layout
):
    """M0 F10: ``dtach`` rejects a ``--`` separator outright with ``Invalid option '--'``.
    This broke the planning document's documented launch chain and is the single easiest
    thing here to get wrong."""
    host = StubSessionHost()
    display = StubDisplay()
    boundaries = make_boundaries(
        audit, host=host, display=display, hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)
    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )

    argv = display.opened[0]["argv"]
    assert argv[0] == "dtach"
    assert argv[1] == "-A"
    assert argv[3] == dispatch.WRAPPER_NAME, "no separator sits between dtach and the wrapper"
    # The wrapper takes its own `--`; dtach takes none.
    assert argv[:4].count("--") == 0
    assert "--" in argv[4:]


def test_the_launch_never_uses_bare_and_sets_both_name_flags(
    conn, audit, config, tmp_path, layout
):
    """``--bare`` skips CLAUDE.md, hooks, skills, plugins, and MCP auto-discovery —
    exactly the accumulated per-repository context that makes these repositories work."""
    display = StubDisplay()
    boundaries = make_boundaries(
        audit, display=display, hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)
    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    argv = display.opened[0]["argv"]
    assert "--bare" not in argv
    assert "-n" in argv and "--remote-control" in argv
    assert "--session-id" in argv
    assert "--permission-mode" in argv


def test_the_session_environment_forces_transcript_persistence(
    conn, audit, config, tmp_path, layout
):
    """M0 F19: a stray ``CLAUDE_CODE_CHILD_SESSION`` in the terminal daemon's environment
    silently disables transcript saving. Sessions inherit *that* environment, not ours."""
    display = StubDisplay()
    boundaries = make_boundaries(
        audit, display=display, hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)
    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    env = display.opened[0]["env"]
    assert env["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] == "1"
    assert env["ROBOT_ARMY_ITEM"] == str(item_id)
    assert env["ROBOT_ARMY_SPOOL_DIR"] == str(layout.spool_dir)
    assert display.opened[0]["user_vars"] == {"ra_item": str(item_id)}


def test_the_prompt_carries_the_issue_and_the_repo_instructions(
    conn, audit, config, tmp_path, layout
):
    clone = config.repos["demo"].path
    instructions = clone / ".claude" / "robot-army.md"
    instructions.parent.mkdir(parents=True, exist_ok=True)
    instructions.write_text("Always run the linter first.", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=clone, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "-m", "instructions"],
        cwd=clone, check=True, capture_output=True,
    )

    display = StubDisplay()
    boundaries = make_boundaries(
        audit, display=display, hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)
    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, clone),
    )
    prompt_text = display.opened[0]["argv"][-1]
    assert "Always run the linter first." in prompt_text
    assert prompt_text.index("Always run the linter first.") < prompt_text.index("Fix the thing")
    assert "#42" in prompt_text


def test_the_concurrency_cap_holds_items_in_ready(conn, audit, config, tmp_path, layout, idle_machine):
    """FR-028. Items above the cap stay in ``ready`` — not a queue, just the state they
    are already in, which is what makes an interrupted dispatcher harmless."""
    from dataclasses import replace

    config = replace(config, daemon=replace(config.daemon, max_concurrent_sessions=1))
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    first = ready_item(conn, config, issue_number=1)
    second = ready_item(conn, config, issue_number=2)

    registry, proc = idle_machine
    dispatched = dispatch.select_and_dispatch(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
        registry_dir=registry,
        proc_root=proc,
    )
    assert dispatched == 1
    assert db.get_work_item(conn, first).state is WorkItemState.ACTIVE
    assert db.get_work_item(conn, second).state is WorkItemState.READY


def test_a_simulated_session_occupies_a_slot(conn, audit, config, tmp_path, layout, idle_machine):
    """FR-055: simulated sessions burn the same subscription quota, so pretending they
    are free would make dry-run runs misleading about capacity."""
    from dataclasses import replace

    config = replace(config, daemon=replace(config.daemon, max_concurrent_sessions=1))
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    simulated = ready_item(conn, config, issue_number=1, dry_run=True)
    live = ready_item(conn, config, issue_number=2, dry_run=False)

    registry, proc = idle_machine
    dispatched = dispatch.select_and_dispatch(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
        registry_dir=registry,
        proc_root=proc,
    )
    assert dispatched == 1
    assert db.get_work_item(conn, simulated).state is WorkItemState.ACTIVE
    assert db.get_work_item(conn, live).state is WorkItemState.READY


def test_dry_run_is_denormalised_onto_the_session_row(conn, audit, config, tmp_path, layout):
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    item_id = ready_item(conn, config, dry_run=True)
    dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    session = db.latest_session_for_item(conn, item_id)
    assert session is not None and session.dry_run is True


def test_pre_launch_validation_rejects_a_missing_worktree(config, audit):
    """M0: ``--add-dir`` at a nonexistent path and a malformed ``--settings`` file both
    exit 0 and proceed, so they must be caught before launch rather than at runtime."""
    problems = dispatch.validate_before_launch(
        boundaries=make_boundaries(audit),
        worktree_path="/definitely/not/here",
        settings_path=None,
        permission_mode="auto",
        config=config,
    )
    assert any("does not exist" in p for p in problems)


def test_pre_launch_validation_rejects_malformed_settings(config, tmp_path, audit):
    bad = tmp_path / "settings.json"
    bad.write_text("{not json", encoding="utf-8")
    problems = dispatch.validate_before_launch(
        boundaries=make_boundaries(audit),
        worktree_path=str(tmp_path),
        settings_path=str(bad),
        permission_mode="auto",
        config=config,
    )
    assert any("not valid JSON" in p for p in problems)


def test_pre_launch_validation_rejects_an_unknown_permission_mode(config, tmp_path, audit):
    problems = dispatch.validate_before_launch(
        boundaries=make_boundaries(audit),
        worktree_path=str(tmp_path),
        settings_path=None,
        permission_mode="whatever",
        config=config,
    )
    assert any("permission_mode" in p for p in problems)


def test_a_comment_failure_does_not_change_the_items_state(
    conn, audit, config, tmp_path, layout
):
    """The session's fate and GitHub's availability are unrelated facts. The failure is
    logged rather than swallowed, but it does not propagate."""

    class BrokenWriter:
        def comment(self, repo_key, number, body):
            raise RuntimeError("github is down")

    boundaries = make_boundaries(
        audit, writer=BrokenWriter(), hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)
    assert dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE
    audit.close()
    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    assert "github is down" in text, "the failure must still be recorded"

    # Not merely "somewhere in the log": under the action a reader would grep for, naming
    # the item, saying the item is unaffected. Non-fatal and *attributable* are two
    # different properties and only one of them was asserted before.
    failed = [r for r in records_of(layout, audit, "github.comment") if r["outcome"] == "error"]
    assert failed, "the declined exception must surface as a github.comment error record"
    assert failed[-1]["entity_id"] == item_id
    assert "unaffected" in failed[-1]["detail"]["note"]


def test_the_build_plan_is_deterministic(config, layout, audit):
    boundaries = make_boundaries(audit)
    issue = make_issue()
    kwargs = dict(
        config=config,
        layout=layout,
        boundaries=boundaries,
        audit=audit,
        repo_key="demo",
        item_id=7,
        issue=issue,
        worktree_path="/tmp/wt",
        branch="robot-army/issue-42-fix",
        session_id="fixed-uuid",
    )
    assert dispatch.build_launch_plan(**kwargs) == dispatch.build_launch_plan(**kwargs)


def test_resume_adds_the_resume_flag(config, layout, audit):
    boundaries = make_boundaries(audit)
    plan = dispatch.build_launch_plan(
        config=config,
        layout=layout,
        boundaries=boundaries,
        audit=audit,
        repo_key="demo",
        item_id=7,
        issue=make_issue(),
        worktree_path="/tmp/wt",
        branch="b",
        session_id="new-id",
        resume_session_id="old-id",
    )
    assert "--resume" in plan.worker_argv
    assert plan.worker_argv[plan.worker_argv.index("--resume") + 1] == "old-id"
    assert plan.worker_argv[plan.worker_argv.index("--session-id") + 1] == "new-id"
    # This test passed for the entire life of `resume`, on a command the binary refuses:
    # --session-id with --resume is rejected unless --fork-session is given too. Asserting
    # the flags we meant to pass is not the same as asserting a command that runs, which
    # is why tests/unit/test_launch_shapes.py now hands the shape to the real binary.
    assert "--fork-session" in plan.worker_argv


# -- the recorded location, re-verified at dispatch (milestone 005) ---------
#
# ``check_gates`` gained a fourth precondition: the clone approved at onboarding is still
# there, is still a primary clone, and is still the same repository. All three failures
# create nothing anywhere, which is the entire point (FR-029, SC-004).


def onboarded_at(conn, key, clone, *, origin=None):
    from tests.conftest import onboard_repo

    onboard_repo(conn, key, clone, verified_origin=origin)


def gates(conn, audit, config, repo, trust):
    dispatch.check_gates(
        conn, boundaries=make_boundaries(audit), config=config, repo=repo, trust_file=trust
    )


def test_a_null_clone_path_blocks_dispatch_naming_reapprove(conn, audit, config, tmp_path):
    """FR-014. A row predating migration 005 means *onboarded, location never verified*,
    and nothing backfills it: writing a path nobody approved into an approval record is the
    one thing that table exists not to do (research R6)."""
    from robot_army import db as _db

    with _db.transaction(conn):
        _db.upsert_repo(
            conn, repo_key="demo", settings_fingerprint=None, trust_verified=True
        )

    with pytest.raises(dispatch.DispatchBlocked, match="before its clone location was recorded"):
        gates(
            conn,
            audit,
            config,
            config.repos["demo"],
            trust_file(tmp_path, config.repos["demo"].path),
        )


def test_a_configured_path_that_disagrees_with_the_record_blocks_naming_both(
    conn, audit, config, tmp_path
):
    """T048, FR-013. Editing ``path`` after onboarding does not silently take effect, and
    it does not silently lose either — it blocks pending re-approval, exactly as a changed
    settings fingerprint already does."""
    from dataclasses import replace

    from tests.conftest import make_repo

    recorded = config.repos["demo"].path
    onboarded_at(conn, "demo", recorded)
    moved = make_repo(tmp_path / "moved")
    edited = replace(config, repos={"demo": replace(config.repos["demo"], path=moved)})

    with pytest.raises(dispatch.DispatchBlocked) as caught:
        gates(conn, audit, edited, edited.repos["demo"], trust_file(tmp_path, moved, recorded))

    assert str(moved) in str(caught.value), "the configured path"
    assert str(recorded) in str(caught.value), "and the approved one"
    assert "--reapprove" in str(caught.value)


def test_recorded_clone_moved(conn, audit, config, tmp_path, layout):
    """T057, SC-005. The clone is renamed after approval. The item fails naming the
    **recorded** path, an anomaly is raised, and no worktree exists anywhere — and
    specifically nothing re-derives or finds another directory of the same name, which is
    what a re-derivation design would silently get wrong."""
    from dataclasses import replace

    from tests.conftest import make_repo

    from robot_army import db as _db

    # Approved somewhere other than the derived location, so that "did it re-derive?" is a
    # question this test can actually answer.
    approved = make_repo(tmp_path / "approved" / "demo")
    derived_root = tmp_path / "GIT"
    derived_root.mkdir()
    config = replace(
        config,
        repo_root=derived_root,
        repos={"demo": replace(config.repos["demo"], path=approved)},
    )
    onboarded_at(conn, "demo", approved)
    item_id = seed_item(
        conn, state=str(WorkItemState.READY), clone_path=approved
    )

    # The author moves the clone. A **real, valid** clone of the same name is then left
    # sitting exactly where derivation would look — so an implementation that re-derived
    # instead of reading the record would sail straight past this and cut a branch in it.
    approved.rename(tmp_path / "renamed-away")
    decoy = make_repo(config.repo_root / "demo")
    assert decoy.is_dir(), "the decoy is the whole point of this test"

    assert not dispatch.dispatch_item(
        conn,
        boundaries=make_boundaries(audit),
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, tmp_path / "renamed-away", decoy),
    )

    item = _db.get_work_item(conn, item_id)
    assert item.state is WorkItemState.FAILED
    assert str(approved) in (item.failure_reason or ""), "the recorded path is named"
    assert item.worktree_path is None
    assert not (config.worktree_root.exists() and any(config.worktree_root.iterdir()))
    assert list(decoy.glob("../*")) , "sanity: the decoy directory really is populated"
    assert not any(
        p.name.startswith("issue-") for p in decoy.parent.iterdir()
    ), "and nothing was created next to it either"

    kinds = [a.kind for a in _db.list_anomalies(conn)]
    assert "clone_path_missing" in kinds


def test_a_different_repository_at_the_recorded_path_is_refused_naming_both(
    conn, audit, config, tmp_path, layout
):
    """T058. Scenario 3's failure arriving months later, and the case a re-derivation
    design would silently get wrong: the derived answer is still this directory, and this
    directory now holds someone else's work."""
    from tests.conftest import make_repo

    from robot_army import db as _db

    clone = config.repos["demo"].path
    onboarded_at(conn, "demo", clone, origin="github.com/jantman/demo")

    # The clone is replaced by a real clone of a different repository, at the same path.
    import shutil

    shutil.rmtree(clone)
    make_repo(clone, origin="git@github.com:someoneelse/other.git")

    with pytest.raises(dispatch.DispatchBlocked) as caught:
        gates(conn, audit, config, config.repos["demo"], trust_file(tmp_path, clone))

    assert "someoneelse/other" in str(caught.value), "the identity found"
    assert "demo" in str(caught.value), "and the one approved"
    assert "clone_origin_changed" in [a.kind for a in _db.list_anomalies(conn)]
    assert not (config.worktree_root.exists() and any(config.worktree_root.iterdir()))


def test_reapprove_after_either_failure_lets_dispatch_resume(conn, audit, config, tmp_path):
    """T059. The resolution the refusals name actually resolves them."""
    import shutil

    from tests.conftest import make_repo

    from robot_army import db as _db

    clone = config.repos["demo"].path
    onboarded_at(conn, "demo", clone, origin="github.com/jantman/demo")
    shutil.rmtree(clone)
    make_repo(clone, origin="git@github.com:someoneelse/other.git")
    trust = trust_file(tmp_path, clone)

    with pytest.raises(dispatch.DispatchBlocked):
        gates(conn, audit, config, config.repos["demo"], trust)

    # `onboard --reapprove` re-records what is actually there.
    onboarded_at(conn, "demo", clone, origin="github.com/someoneelse/other")
    with _db.transaction(conn):
        _db.upsert_repo(
            conn,
            repo_key="demo",
            settings_fingerprint=None,
            trust_verified=True,
            clone_path=str(clone),
            path_source="configured",
            verified_origin="github.com/someoneelse/other",
        )

    gates(conn, audit, config, config.repos["demo"], trust)


# -- resume: the launch shape and what it records (milestone 013) -----------


def records_of(layout, audit, action: str) -> list[dict]:
    """Every audit record for one action. ``audit.close()`` first — the log is a file."""
    audit.close()
    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))
    return [
        json.loads(line)
        for line in text.splitlines()
        if json.loads(line).get("action") == action
    ]


def interrupt(conn, item_id: int, audit) -> None:
    from robot_army.states import transition_work_item

    with db.transaction(conn):
        transition_work_item(
            conn,
            audit,
            item_id=item_id,
            target=WorkItemState.INTERRUPTED,
            reason="the maintainer closed the laptop",
        )


def test_a_resume_is_a_new_attempt_naming_what_it_restored(
    conn, audit, config, tmp_path, layout
):
    """FR-002. The prior session id is in ``launch_argv`` too, but that is the whole nested
    wrapper argv with the prompt body inside it. Principle III's standard is reconstruction
    from the log, and "which session did this restore" should not require parsing that."""
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    trust = trust_file(tmp_path, config.repos["demo"].path)
    item_id = ready_item(conn, config)

    assert dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust,
    )
    first = db.latest_session_for_item(conn, item_id)
    assert first is not None and first.attempt == 1
    interrupt(conn, item_id, audit)

    assert dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust,
        resume_session_id=first.session_id,
    )

    second = db.latest_session_for_item(conn, item_id)
    assert second is not None
    assert second.attempt == 2, "a resume is a new attempt, not a continuation of the old one"
    assert second.session_id != first.session_id

    confirmed = records_of(layout, audit, "dispatch.confirmed")
    assert confirmed[-1]["detail"]["resumed_from"] == first.session_id
    assert confirmed[0]["detail"].get("resumed_from") is None, (
        "a fresh launch restored nothing and must not claim otherwise"
    )
    assert confirmed[-1]["detail"]["attempt"] == 2

    # And the issue is told the same thing (#38). Two comments that read identically would
    # leave a reader unable to say which session came first or what it kept.
    body = boundaries.issue_writer.comments[-1][2]
    assert "reassigned this issue to a new session (attempt 2)" in body
    assert f"- Continues: `{first.session_id}` (that session's context was restored)" in body


# -- the confirmation race (milestone 013) ---------------------------------
#
# A worker can die before its launch is confirmed, and when it does, the daemon applies its
# exit record from a *different process* while dispatch is still sitting in confirmation.
# The session then holds a terminal state that dispatch used to try to overwrite with LOST,
# which the state gate refuses --- the exception escaped, `_fail` never ran, and the item
# stayed in `dispatching` until the 15-minute reaper. See specs/013's research R3.
#
# `spool.apply_record` deliberately leaves a `dispatching` item alone (it only settles
# `active` ones), because dispatch owns the item until confirmation resolves. That division
# is correct; it is what makes settling the item here dispatch's job and nobody else's.


def dying_host(conn, audit, exit_code: int):
    """A host whose worker records its own exit while confirmation is still waiting."""
    from robot_army import spool

    def record_the_exit(session_id: str) -> None:
        for payload in (
            {"schema": 1, "event": "start", "session_id": session_id, "pid": 4242},
            {"schema": 1, "event": "exit", "session_id": session_id, "exit": exit_code},
        ):
            with db.transaction(conn):
                spool.apply_record(conn, audit, payload)

    return StubSessionHost(confirm=False, on_confirm=record_the_exit)


def launch_with(conn, audit, config, layout, tmp_path, host):
    boundaries = make_boundaries(audit, host=host, hooks=SubprocessHookRunner(audit))
    item_id = ready_item(conn, config)
    ok = dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    return item_id, ok


@pytest.mark.parametrize(
    ("exit_code", "session_state", "in_reason"),
    [
        (1, SessionState.EXITED_ERROR, "exited 1"),
        (0, SessionState.EXITED_CLEAN, "exited 0"),
        (143, SessionState.EXITED_ERROR, "exited 143"),
    ],
)
def test_a_session_that_already_exited_is_not_overwritten_as_lost(
    conn, audit, config, tmp_path, layout, exit_code, session_state, in_reason
):
    """FR-005. The session recorded its own ending; that ending is the answer. Declaring it
    `lost` would both contradict the record and destroy the most useful fact in it."""
    item_id, ok = launch_with(
        conn, audit, config, layout, tmp_path, dying_host(conn, audit, exit_code)
    )
    assert ok is False

    session = db.latest_session_for_item(conn, item_id)
    assert session is not None
    assert session.state is session_state, "the recorded exit stands"
    assert session.exit_code == exit_code

    item = db.get_work_item(conn, item_id)
    assert item is not None
    assert item.state is WorkItemState.FAILED, (
        "a launch that failed must be settled here, not left for the 15-minute reaper"
    )
    assert in_reason in (item.failure_reason or "")


def test_the_illegal_transition_that_wedged_the_item_never_happens(
    conn, audit, config, tmp_path, layout
):
    """The reported symptom, asserted directly: no IllegalTransition, and no `state.session`
    record moving a terminal session anywhere."""
    item_id, _ = launch_with(conn, audit, config, layout, tmp_path, dying_host(conn, audit, 1))
    audit.close()
    text = "\n".join(p.read_text(encoding="utf-8") for p in layout.log_dir.glob("*.jsonl"))

    assert "IllegalTransition" not in text
    assert "illegal session transition" not in text
    moves = [
        json.loads(line)
        for line in text.splitlines()
        if json.loads(line).get("action") == "state.session"
    ]
    assert not any(m["detail"]["to"] == "lost" for m in moves), (
        "nothing may move a session that already reported its own ending"
    )
    assert db.get_work_item(conn, item_id).state is WorkItemState.FAILED


def test_the_unconfirmed_record_says_which_outcome_it_took(
    conn, audit, config, tmp_path, layout
):
    """FR-010. From the log alone, 'never appeared' and 'already exited' must be different
    stories, because they call for different next steps."""
    launch_with(conn, audit, config, layout, tmp_path, dying_host(conn, audit, 1))
    record = records_of(layout, audit, "dispatch.unconfirmed")[-1]

    assert record["detail"]["session_state"] == "exited_error"
    assert record["detail"]["outcome"] == "already_exited"


def test_a_session_that_recorded_nothing_is_still_declared_lost(
    conn, audit, config, tmp_path, layout
):
    """FR-006. The pre-existing path, unchanged. A worker that never wrote anything really
    is lost, and this is the case the old code was written for."""
    item_id, ok = launch_with(
        conn, audit, config, layout, tmp_path, StubSessionHost(confirm=False)
    )
    assert ok is False

    session = db.latest_session_for_item(conn, item_id)
    assert session is not None and session.state is SessionState.LOST
    item = db.get_work_item(conn, item_id)
    assert item.state is WorkItemState.FAILED
    assert "not confirmed" in (item.failure_reason or "")

    record = records_of(layout, audit, "dispatch.unconfirmed")[-1]
    assert record["detail"]["outcome"] == "lost"


def test_an_exception_inside_the_launch_settles_the_item_and_re_raises(
    conn, audit, config, tmp_path, layout
):
    """FR-008. Nothing is swallowed --- the exception still reaches the caller --- but it may
    not strand the item on its way out. That combination is the whole requirement."""

    class ExplodingDisplay(StubDisplay):
        def open(self, cwd, argv, title, user_vars, env):
            # Not a BoundaryError --- that one is handled and tested above. This is the
            # unforeseen kind: a boundary raising something nobody planned for.
            raise RuntimeError("kitty fell over mid-launch")

    host = dying_host(conn, audit, 1)
    boundaries = make_boundaries(
        audit, host=host, display=ExplodingDisplay(), hooks=SubprocessHookRunner(audit)
    )
    item_id = ready_item(conn, config)

    with pytest.raises(RuntimeError, match="kitty fell over"):
        dispatch.dispatch_item(
            conn,
            boundaries=boundaries,
            audit=audit,
            config=config,
            layout=layout,
            item_id=item_id,
            trust_file=trust_file(tmp_path, config.repos["demo"].path),
        )

    item = db.get_work_item(conn, item_id)
    assert item is not None
    assert item.state is WorkItemState.FAILED, "an escaping exception may not wedge the item"
    assert "kitty fell over" in (item.failure_reason or "")
    assert records_of(layout, audit, "dispatch.error"), "and it must be in the record"


def test_a_crash_on_a_simulated_item_is_distinguishable_in_the_log(
    conn, audit, config, tmp_path, layout
):
    """A `dispatch.error` that omits `dry_run` makes a crash while dispatching a simulated
    item read exactly like a crash on a real one. Every other record in this module carries
    it; this one must too (FR-055)."""

    class ExplodingDisplay(StubDisplay):
        def open(self, cwd, argv, title, user_vars, env):
            raise RuntimeError("kitty fell over mid-launch")

    boundaries = make_boundaries(
        audit,
        host=dying_host(conn, audit, 1),
        display=ExplodingDisplay(),
        hooks=SubprocessHookRunner(audit),
    )
    item_id = seed_item(
        conn,
        state=str(WorkItemState.READY),
        dry_run=True,
        clone_path=config.repos["demo"].path,
    )

    with pytest.raises(RuntimeError, match="kitty fell over"):
        dispatch.dispatch_item(
            conn,
            boundaries=boundaries,
            audit=audit,
            config=config,
            layout=layout,
            item_id=item_id,
            trust_file=trust_file(tmp_path, config.repos["demo"].path),
        )

    record = records_of(layout, audit, "dispatch.error")[-1]
    assert record["dry_run"] is True
    assert record["detail"]["settling"] is True
    assert record["detail"]["item_state"] == "dispatching"


def test_a_crash_after_confirmation_does_not_claim_a_settle_it_did_not_make(
    conn, audit, config, tmp_path, layout, monkeypatch
):
    """Notification, the board update and the transcript check all run *after* the item
    reaches `active`. An exception in any of them lands in the same handler, where the item
    can no longer legally be moved --- so the record must say the item was left alone. An
    audit trail that claims an outcome nobody produced is worse than one that says nothing."""

    def explode(**kwargs):
        raise RuntimeError("the notifier fell over after the session was confirmed")

    monkeypatch.setattr(dispatch.notifications, "emit", explode)
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    item_id = ready_item(conn, config)

    with pytest.raises(RuntimeError, match="the notifier fell over"):
        dispatch.dispatch_item(
            conn,
            boundaries=boundaries,
            audit=audit,
            config=config,
            layout=layout,
            item_id=item_id,
            trust_file=trust_file(tmp_path, config.repos["demo"].path),
        )

    item = db.get_work_item(conn, item_id)
    assert item is not None
    assert item.state is WorkItemState.ACTIVE, (
        "the session really is running; failing the item here would be the false report"
    )

    record = records_of(layout, audit, "dispatch.error")[-1]
    assert record["detail"]["settling"] is False
    assert record["detail"]["item_state"] == "active"
    assert "left as it stands" in record["detail"]["note"]


# -- what the issue is told (issue #38) -------------------------------------
#
# The comment is how an issue, the pull request that closes it and the session logs that
# explain both are correlated months later. Until #38 it named the branch, the worktree and
# the session id -- and on a second machine none of those is an address.


def test_the_dispatch_comment_names_the_machine_and_both_session_handles(
    conn, audit, config, tmp_path, layout
):
    """FR-001 and FR-002 together: what the issue says, and that the log agrees with it."""
    writer = RecordingWriter()
    boundaries = make_boundaries(audit, writer=writer, hooks=SubprocessHookRunner(audit))
    item_id = ready_item(conn, config)

    assert dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )

    session = db.latest_session_for_item(conn, item_id)
    item = db.get_work_item(conn, item_id)
    assert session is not None and item is not None

    repo_key, number, body = writer.comments[-1]
    assert (repo_key, number) == (item.repo_key, item.issue_number)
    assert f"- Host: `{dispatch.host_name()}`" in body
    assert "- Session: `ra-demo-42`" in body
    assert f"- Session id: `{session.session_id}`" in body
    assert f"- Branch: `{item.branch}`" in body
    assert f"- Worktree: `{item.worktree_path}`" in body

    # The same three facts in the record, so a comment read on one machine can be matched
    # against a log read on another. This is the whole of FR-002 and it is why the values
    # are resolved once at the call site rather than derived twice.
    detail = records_of(layout, audit, "dispatch.confirmed")[-1]["detail"]
    assert detail["host"] == dispatch.host_name()
    assert detail["session_name"] == "ra-demo-42"
    assert detail["session_id"] == session.session_id
    assert detail["attempt"] == 1


def test_a_blocked_dispatch_says_which_machine_refused(conn, audit, config, tmp_path, layout):
    """FR-005. A failure that happens on one machine and not another is a real case here:
    trust is granted per machine, so this is exactly the failure whose host matters."""
    writer = RecordingWriter()
    boundaries = make_boundaries(audit, writer=writer, hooks=SubprocessHookRunner(audit))
    item_id = ready_item(conn, config)

    assert not dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=tmp_path / "absent.json",
    )

    body = writer.comments[-1][2]
    assert "could not start a session" in body
    assert f"- Host: `{dispatch.host_name()}`" in body
    assert "trust check failed" in body

    item = db.get_work_item(conn, item_id)
    assert item is not None and item.state is WorkItemState.FAILED
    assert "trust check failed" in (item.blocked_reason or "")


def test_a_restart_names_the_session_it_supersedes_and_says_it_kept_nothing(
    conn, audit, config, tmp_path, layout
):
    """The case that would catch a session claiming to supersede itself.

    A restart carries no ``resume_session_id``, so the predecessor has to be looked up ---
    and the session row for *this* attempt already exists by then. Asking
    ``latest_session_for_item`` would return this very session, and the comment would name
    it as its own predecessor: confident, wrong, and indistinguishable from correct to
    anyone reading the issue.
    """
    writer = RecordingWriter()
    boundaries = make_boundaries(audit, writer=writer, hooks=SubprocessHookRunner(audit))
    trust = trust_file(tmp_path, config.repos["demo"].path)
    item_id = ready_item(conn, config)

    assert dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust,
    )
    first = db.latest_session_for_item(conn, item_id)
    assert first is not None
    interrupt(conn, item_id, audit)

    assert dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust,
    )
    second = db.latest_session_for_item(conn, item_id)
    assert second is not None and second.session_id != first.session_id

    body = writer.comments[-1][2]
    assert "reassigned this issue to a new session (attempt 2)" in body
    assert f"- Supersedes: `{first.session_id}` " in body
    assert "starts without that session's context" in body
    assert second.session_id not in body.split("- Supersedes:")[1].splitlines()[0], (
        "a session must never be named as its own predecessor"
    )

    detail = records_of(layout, audit, "dispatch.confirmed")[-1]["detail"]
    assert detail["supersedes"] == first.session_id
    assert "resumed_from" not in detail, "a restart restored nothing and must not claim to"


def test_an_issue_accumulates_one_comment_per_attempt_and_none_are_edited(
    conn, audit, config, tmp_path, layout
):
    """FR-004. The ordered history is the point; an edited comment would lose it."""
    writer = RecordingWriter()
    boundaries = make_boundaries(audit, writer=writer, hooks=SubprocessHookRunner(audit))
    trust = trust_file(tmp_path, config.repos["demo"].path)
    item_id = ready_item(conn, config)

    for _ in range(3):
        assert dispatch.dispatch_item(
            conn,
            boundaries=boundaries,
            audit=audit,
            config=config,
            layout=layout,
            item_id=item_id,
            trust_file=trust,
        )
        interrupt(conn, item_id, audit)

    bodies = [body for _, _, body in writer.comments]
    assert len(bodies) == 3
    assert "dispatched a session" in bodies[0]
    assert "(attempt 2)" in bodies[1]
    assert "(attempt 3)" in bodies[2]
    assert len(set(bodies)) == 3, "three attempts must not read as three identical announcements"


def test_an_unconfirmed_launch_leaves_no_comment_claiming_a_session(
    conn, audit, config, tmp_path, layout
):
    """FR-006, pinned at the call site rather than trusted to stay there.

    The dispatch comment is the last statement of a dispatch that has already confirmed a
    session. That position is the only thing making the sentence true, and it is exactly
    the kind of line a later refactor moves without noticing.
    """
    writer = RecordingWriter()
    boundaries = make_boundaries(
        audit,
        writer=writer,
        host=StubSessionHost(confirm=False),
        hooks=SubprocessHookRunner(audit),
    )
    item_id = ready_item(conn, config)

    assert not dispatch.dispatch_item(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        item_id=item_id,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )

    bodies = [body for _, _, body in writer.comments]
    assert bodies, "a failed attempt is still reported"
    assert all("dispatched a session" not in body for body in bodies)
    assert all("reassigned this issue" not in body for body in bodies)
    assert all("could not start a session" in body for body in bodies)
