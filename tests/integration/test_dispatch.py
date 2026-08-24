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
    item_id = seed_item(conn, state=str(WorkItemState.READY), **kwargs)
    return item_id


def trust_file(tmp_path: Path, clone: Path) -> Path:
    path = tmp_path / "claude.json"
    path.write_text(
        json.dumps({"projects": {str(clone.resolve()): {"hasTrustDialogAccepted": True}}}),
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


def test_the_concurrency_cap_holds_items_in_ready(conn, audit, config, tmp_path, layout):
    """FR-028. Items above the cap stay in ``ready`` — not a queue, just the state they
    are already in, which is what makes an interrupted dispatcher harmless."""
    from dataclasses import replace

    config = replace(config, daemon=replace(config.daemon, max_concurrent_sessions=1))
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    first = ready_item(conn, config, issue_number=1)
    second = ready_item(conn, config, issue_number=2)

    dispatched = dispatch.select_and_dispatch(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
    )
    assert dispatched == 1
    assert db.get_work_item(conn, first).state is WorkItemState.ACTIVE
    assert db.get_work_item(conn, second).state is WorkItemState.READY


def test_a_simulated_session_occupies_a_slot(conn, audit, config, tmp_path, layout):
    """FR-055: simulated sessions burn the same subscription quota, so pretending they
    are free would make dry-run runs misleading about capacity."""
    from dataclasses import replace

    config = replace(config, daemon=replace(config.daemon, max_concurrent_sessions=1))
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    simulated = ready_item(conn, config, issue_number=1, dry_run=True)
    live = ready_item(conn, config, issue_number=2, dry_run=False)

    dispatched = dispatch.select_and_dispatch(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        layout=layout,
        trust_file=trust_file(tmp_path, config.repos["demo"].path),
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


def test_the_build_plan_is_deterministic(config, layout, audit):
    boundaries = make_boundaries(audit)
    issue = make_issue()
    kwargs = dict(
        config=config,
        layout=layout,
        boundaries=boundaries,
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
