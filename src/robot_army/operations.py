"""Every CLI verb as a plain callable.

This module exists so that no operation's logic lives inside argument parsing. Milestone
002's HTTP API is then a *second caller of the same functions* rather than a
reimplementation — the single cheapest thing this milestone can do for the next one, at
essentially zero cost now (plan.md, Project Structure).

Each operation returns a ``Result``: an exit code, a human-readable rendering, and a
machine-readable payload. The CLI chooses which to print; it makes no decisions of its own.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from robot_army import audit as audit_mod
from robot_army import (
    daemon as daemon_mod,
)
from robot_army import (
    db,
    dispatch,
    health,
    poll,
    procinfo,
    reconcile,
    sessions,
    spool,
    worktree,
)
from robot_army.audit import AuditLog
from robot_army.boundaries import BoundaryError, HostHandle, TransportError
from robot_army.config import Config
from robot_army.effects import Boundaries, EffectLevel, wire
from robot_army.migrations import SCHEMA_VERSION
from robot_army.models import ANOMALY_KINDS
from robot_army.states import SessionState, WorkItemState, transition_work_item

# Exit codes, per contracts/cli.md.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_PRECONDITION = 3
EXIT_CHECK_FAILED = 4


@dataclass(slots=True)
class Result:
    code: int = EXIT_OK
    lines: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def say(self, text: str = "") -> Result:
        self.lines.append(text)
        return self

    def render(self, *, as_json: bool) -> str:
        if as_json:
            return json.dumps(self.data, indent=2, default=str)
        return "\n".join(self.lines)


@dataclass(slots=True)
class Context:
    """Everything an operation needs, assembled once by the CLI."""

    config: Config
    conn: sqlite3.Connection
    audit: AuditLog
    boundaries: Boundaries
    effect_level: EffectLevel

    @property
    def layout(self) -> Any:
        return self.config.layout

    def close(self) -> None:
        self.conn.close()
        self.audit.close()


def build_context(
    config: Config, *, effect_level: EffectLevel | None = None, component: str = "cli"
) -> Context:
    layout = config.layout
    layout.ensure()
    level = effect_level or config.daemon.effect_level
    audit = AuditLog(layout.log_dir, component=component)
    conn, _ = db.open_database(layout.db_path)
    return Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=wire(level, config, audit),
        effect_level=level,
    )


def _mark(item: Any) -> str:
    """Simulated rows are always visibly marked when shown (FR-057)."""
    return " [simulated]" if getattr(item, "dry_run", False) else ""


def _table(rows: list[list[str]], headers: list[str]) -> Iterator[str]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    yield "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()
    yield "  ".join("-" * widths[i] for i in range(len(headers)))
    for row in rows:
        yield "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()


# -- status -----------------------------------------------------------------


def status(
    ctx: Context,
    *,
    state: str | None = None,
    repo: str | None = None,
    include_simulated: bool = False,
) -> Result:
    """The default view: effect level, health, counts and listings by state, anomalies."""
    result = Result()
    report = health.check(
        ctx.layout.heartbeat_path, max_age_seconds=ctx.config.health.max_age_seconds
    )
    counts = db.count_work_items_by_state(ctx.conn, include_simulated=include_simulated)
    states = [WorkItemState(state)] if state else None
    items = db.list_work_items(
        ctx.conn, include_simulated=include_simulated, states=states, repo_key=repo
    )
    anomalies = db.list_anomalies(ctx.conn)

    result.data = {
        "effect_level": str(ctx.effect_level),
        "health": report.to_dict(),
        "counts": counts,
        "items": [_item_dict(i) for i in items],
        "anomalies": [_anomaly_dict(a) for a in anomalies],
        "include_simulated": include_simulated,
    }

    result.say(f"effect level : {ctx.effect_level}")
    result.say(f"health       : {'ok' if report.healthy else 'STALE'} — {report.reason}")
    result.say(f"database     : {ctx.layout.db_path} (schema {SCHEMA_VERSION})")
    result.say()
    if counts:
        result.say("counts by state:")
        for name in sorted(counts):
            result.say(f"  {name:<16} {counts[name]}")
    else:
        result.say("no work items yet")
    result.say()

    if items:
        rows = [
            [
                str(i.id),
                str(i.state) + ("*" if i.dry_run else ""),
                i.repo_key,
                f"#{i.issue_number}",
                (i.title[:48] + "…") if len(i.title) > 49 else i.title,
                (i.failure_reason or i.blocked_reason or "")[:60],
            ]
            for i in items
        ]
        for line in _table(rows, ["id", "state", "repo", "issue", "title", "reason"]):
            result.say(line)
        if any(i.dry_run for i in items):
            result.say()
            result.say("* = simulated (dry-run) row")
    else:
        result.say("no matching work items")

    if anomalies:
        result.say()
        result.say(f"unacknowledged anomalies ({len(anomalies)}):")
        for anomaly in anomalies:
            result.say(
                f"  [{anomaly.id}] {anomaly.kind} "
                f"{anomaly.entity_type or ''}:{anomaly.entity_id or ''} @ {anomaly.detected_at}"
            )
    return result


def _item_dict(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "source_id": item.source_id,
        "source_url": item.source_url,
        "repo_key": item.repo_key,
        "issue_number": item.issue_number,
        "title": item.title,
        "state": str(item.state),
        "dry_run": item.dry_run,
        "simulated": item.dry_run,
        "worktree_path": item.worktree_path,
        "branch": item.branch,
        "failure_reason": item.failure_reason,
        "blocked_reason": item.blocked_reason,
        "discovered_at": item.discovered_at,
        "updated_at": item.updated_at,
    }


def _session_dict(session: Any) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "attempt": session.attempt,
        "state": str(session.state),
        "dry_run": session.dry_run,
        "pid": session.pid,
        "scope": session.scope,
        "host_socket": session.host_socket,
        "window_id": session.window_id,
        "exit_code": session.exit_code,
        "signal": session.signal,
        "started_at": session.started_at,
        "confirmed_at": session.confirmed_at,
        "ended_at": session.ended_at,
    }


def _anomaly_dict(anomaly: Any) -> dict[str, Any]:
    return {
        "id": anomaly.id,
        "kind": anomaly.kind,
        "entity_type": anomaly.entity_type,
        "entity_id": anomaly.entity_id,
        "detail": anomaly.detail_obj,
        "detected_at": anomaly.detected_at,
        "acknowledged_at": anomaly.acknowledged_at,
    }


# -- show -------------------------------------------------------------------


def show(ctx: Context, item_id: int) -> Result:
    """Everything about one work item, including the FR-048 resume-decision signals."""
    result = Result()
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])

    attempts = db.list_sessions_for_item(ctx.conn, item_id)
    signals = resume_signals(ctx, item)

    result.data = {
        "item": _item_dict(item),
        "history": _history(item),
        "sessions": [_session_dict(s) for s in attempts],
        "resume_signals": signals,
    }

    result.say(f"work item {item.id}{_mark(item)}")
    result.say(f"  source     : {item.source_id}  {item.source_url}")
    result.say(f"  repository : {item.repo_key}")
    result.say(f"  title      : {item.title}")
    result.say(f"  state      : {item.state}")
    result.say(f"  worktree   : {item.worktree_path or '(none)'}")
    result.say(f"  branch     : {item.branch or '(none)'}")
    if item.failure_reason:
        result.say(f"  failure    : {item.failure_reason}")
    if item.blocked_reason:
        result.say(f"  blocked    : {item.blocked_reason}")

    result.say()
    result.say("state history:")
    for when, what in _history(item):
        result.say(f"  {when}  {what}")

    result.say()
    if attempts:
        result.say(f"sessions ({len(attempts)} attempt(s)):")
        for session in attempts:
            exit_text = "—" if session.exit_code is None else str(session.exit_code)
            signal_text = f" signal {session.signal}" if session.signal is not None else ""
            result.say(
                f"  #{session.attempt} {session.state}{'*' if session.dry_run else ''} "
                f"id={session.session_id} pid={session.pid or '—'} "
                f"exit={exit_text}{signal_text}"
            )
            result.say(f"       started {session.started_at} ended {session.ended_at or '—'}")
            if session.host_socket:
                result.say(f"       reattach: dtach -a {session.host_socket}")
    else:
        result.say("no session attempts yet")

    result.say()
    result.say("resume-decision signals (computed now, never stored):")
    for key, value in signals.items():
        result.say(f"  {key:<22} {value}")
    if item.prepare_output:
        result.say()
        result.say("preparation output:")
        for line in item.prepare_output.splitlines()[:40]:
            result.say(f"  {line}")
    return result


def _history(item: Any) -> list[tuple[str, str]]:
    stamps = [
        (item.discovered_at, "discovered"),
        (item.ready_at, "ready"),
        (item.dispatching_at, "dispatching"),
        (item.active_at, "active"),
        (item.ended_at, "session ended"),
        (item.done_at, "done"),
    ]
    return [(when, what) for when, what in stamps if when]


def resume_signals(ctx: Context, item: Any) -> dict[str, Any]:
    """Whether resuming is worthwhile (FR-048). Every value computed on demand.

    A stored copy would be wrong the instant the maintainer touched the directory, which
    is precisely why data-model.md keeps none of these.
    """
    signals: dict[str, Any] = {
        "worktree_present": False,
        "uncommitted_changes": None,
        "commits_on_branch": None,
        "issue_closed": None,
        "open_pull_request": None,
    }
    repo = ctx.config.repos.get(item.repo_key)
    if repo is None or not item.worktree_path or not item.branch:
        return signals

    base_ref = ctx.config.base_branch_for(item.repo_key)
    try:
        condition = worktree.condition(
            ctx.boundaries.version_control,
            str(repo.path),
            item.worktree_path,
            item.branch,
            base_ref,
        )
        signals["worktree_present"] = condition.exists
        signals["uncommitted_changes"] = condition.dirty
        signals["commits_on_branch"] = condition.commits_ahead
    except BoundaryError as exc:
        signals["worktree_error"] = str(exc)

    if item.dry_run:
        # FR-055: a simulated row must not cause outward-facing effects, and asking
        # GitHub about it would be exactly that.
        return signals

    try:
        signals["issue_closed"] = ctx.boundaries.issue_reader.is_closed(
            item.repo_key, item.issue_number
        )
        pr = ctx.boundaries.issue_reader.open_pr_for_branch(item.repo_key, item.branch)
        signals["open_pull_request"] = pr.url if pr else None
    except TransportError as exc:
        signals["github_error"] = str(exc)
    return signals


# -- onboard ----------------------------------------------------------------


def onboard(
    ctx: Context,
    repo_key: str,
    *,
    reapprove: bool = False,
    assume_yes: bool = False,
    confirm: Any = input,
    trust_file: Path | None = None,
) -> Result:
    """The deliberate per-repository trust step (FR-001).

    Prints the primary clone path, the worker's trust status, and the **full contents** of
    any committed settings *as they exist at the base branch tip* — because that is what a
    dispatched session will honour (FR-004, M0 F9), not whatever is in a working tree.
    """
    result = Result()
    repo = ctx.config.repos.get(repo_key)
    if repo is None:
        return Result(
            code=EXIT_USAGE,
            lines=[f"no [repos.{repo_key}] section in {ctx.config.path}"],
        )

    base_ref = repo.base_branch or ctx.config.worker.base_branch
    trusted, explanation = dispatch.is_trusted(repo.path, trust_file=trust_file)
    committed = dispatch.read_committed_settings(ctx.boundaries, str(repo.path), base_ref)
    fingerprint = dispatch.compute_fingerprint(ctx.boundaries, str(repo.path), base_ref)
    existing = db.get_repo(ctx.conn, repo_key)

    result.data = {
        "repo_key": repo_key,
        "clone_path": str(repo.path),
        "base_ref": base_ref,
        "trusted": trusted,
        "trust_explanation": explanation,
        "committed_settings": committed,
        "fingerprint": fingerprint,
        "previously_onboarded": existing is not None,
        "previous_fingerprint": existing.fingerprint if existing else None,
    }

    result.say(f"repository   : {repo_key}")
    result.say(f"primary clone: {repo.path}")
    result.say(f"base ref     : {base_ref}")
    result.say(f"trust        : {'accepted' if trusted else 'NOT ACCEPTED'} — {explanation}")
    result.say()

    if committed:
        result.say("committed tool-permission settings at the base ref:")
        result.say(
            "  These are applied to a dispatched session WITHOUT asking. Read them."
        )
        for path, text in committed.items():
            result.say()
            result.say(f"  --- {path} ---")
            for line in text.splitlines():
                result.say(f"  {line}")
        result.say()
    else:
        result.say("no committed .claude/settings*.json at the base ref")
        result.say()

    if reapprove and existing is not None:
        previous = existing.fingerprint
        result.say("fingerprint diff against the approved version:")
        for path in sorted(set(previous) | set(fingerprint)):
            before = previous.get(path, "(absent)")
            after = fingerprint.get(path, "(absent)")
            marker = " " if before == after else "*"
            result.say(f"  {marker} {path}")
            result.say(f"      approved: {before}")
            result.say(f"      current : {after}")
        result.say()

    if existing is not None and not reapprove and existing.fingerprint == fingerprint:
        result.say("already onboarded and the fingerprint is unchanged; nothing to do")
        return result

    if assume_yes and committed and (existing is None or existing.fingerprint != fingerprint):
        # --yes refuses to skip when committed settings are present and unapproved.
        # Skipping the prompt is a convenience; skipping the *review* is the hazard.
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                *result.lines,
                "refusing --yes: this repository has committed tool-permission settings "
                "that have not been approved. Review the contents above and confirm "
                "interactively.",
            ],
            data=result.data,
        )

    if not assume_yes:
        answer = confirm(
            f"Approve {repo_key} for dispatch, recording this fingerprint? [y/N] "
        )
        if str(answer).strip().lower() not in ("y", "yes"):
            return Result(code=EXIT_FAILED, lines=[*result.lines, "aborted"], data=result.data)

    with (
        ctx.audit.action(
            "repo.onboard",
            entity_type="repo",
            entity_id=repo_key,
            detail={
                "clone_path": str(repo.path),
                "base_ref": base_ref,
                "fingerprint": fingerprint,
                "trusted": trusted,
                "reapprove": reapprove,
            },
        ),
        db.transaction(ctx.conn),
    ):
        db.upsert_repo(
                ctx.conn,
                repo_key=repo_key,
                settings_fingerprint=fingerprint or None,
                trust_verified=trusted,
            )

    result.say(f"onboarded {repo_key}")
    if not trusted:
        result.say(
            "NOTE: the trust dialog has not been accepted for this clone. Dispatch will "
            "still refuse until it is — open the repository in the worker once."
        )
    return result


# -- repos ------------------------------------------------------------------


def repos(ctx: Context, *, trust_file: Path | None = None) -> Result:
    """Where "why is nothing happening for this repo" gets answered."""
    result = Result()
    rows: list[list[str]] = []
    payload: list[dict[str, Any]] = []

    for key in sorted(ctx.config.repos):
        repo = ctx.config.repos[key]
        record = db.get_repo(ctx.conn, key)
        trusted, explanation = dispatch.is_trusted(repo.path, trust_file=trust_file)
        base_ref = repo.base_branch or ctx.config.worker.base_branch
        try:
            current = dispatch.compute_fingerprint(ctx.boundaries, str(repo.path), base_ref)
        except BoundaryError:
            current = {}
        approved = record.fingerprint if record else {}
        if record is None:
            fingerprint_state = "n/a"
        elif current == approved:
            fingerprint_state = "matches"
        else:
            fingerprint_state = "CHANGED"

        rows.append(
            [
                key,
                str(repo.path),
                "yes" if record else "NO",
                "yes" if trusted else "NO",
                fingerprint_state,
                str(len(repo.post_create)),
            ]
        )
        payload.append(
            {
                "repo_key": key,
                "path": str(repo.path),
                "onboarded": record is not None,
                "onboarded_at": record.onboarded_at if record else None,
                "trusted": trusted,
                "trust_explanation": explanation,
                "fingerprint_state": fingerprint_state,
                "approved_fingerprint": approved,
                "current_fingerprint": current,
                "post_create_steps": len(repo.post_create),
            }
        )

    result.data = {"repos": payload}
    if not rows:
        return result.say("no [repos.*] sections configured")
    for line in _table(
        rows, ["repo", "clone path", "onboarded", "trusted", "fingerprint", "steps"]
    ):
        result.say(line)
    return result


# -- worktree ---------------------------------------------------------------


def worktree_list(ctx: Context, *, include_simulated: bool = False) -> Result:
    result = Result()
    rows: list[list[str]] = []
    payload: list[dict[str, Any]] = []
    for item in db.list_work_items(ctx.conn, include_simulated=include_simulated):
        if not item.worktree_path:
            continue
        repo = ctx.config.repos.get(item.repo_key)
        base_ref = ctx.config.base_branch_for(item.repo_key)
        if repo is None:
            condition = None
        else:
            try:
                condition = worktree.condition(
                    ctx.boundaries.version_control,
                    str(repo.path),
                    item.worktree_path,
                    item.branch or "",
                    base_ref,
                )
            except BoundaryError:
                condition = None
        size = worktree.directory_size(item.worktree_path)
        rows.append(
            [
                str(item.id) + ("*" if item.dry_run else ""),
                item.worktree_path,
                item.branch or "—",
                condition.label if condition else "unknown",
                worktree.human_size(size),
            ]
        )
        payload.append(
            {
                "item_id": item.id,
                "path": item.worktree_path,
                "branch": item.branch,
                "condition": condition.label if condition else "unknown",
                "dirty": condition.dirty if condition else None,
                "commits_ahead": condition.commits_ahead if condition else None,
                "size_bytes": size,
                "simulated": item.dry_run,
            }
        )
    result.data = {"worktrees": payload}
    if not rows:
        return result.say("no worktrees recorded")
    for line in _table(rows, ["item", "path", "branch", "condition", "size"]):
        result.say(line)
    return result


def worktree_remove(
    ctx: Context, item_id: int, *, force: bool = False, confirm: Any = input
) -> Result:
    """Remove **both** the worktree and its branch (FR-016).

    Removal is two steps, and doing only the first accumulates ``robot-army/*`` branches
    in every repository forever. It **refuses** on a worktree with uncommitted *or merely
    untracked* changes — git's own refusal is the guard, and that refusal is the feature.
    """
    result = Result()
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    if not item.worktree_path:
        return Result(code=EXIT_FAILED, lines=[f"work item {item_id} has no worktree"])
    repo = ctx.config.repos.get(item.repo_key)
    if repo is None:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[f"repository {item.repo_key!r} is not in the config any more"],
        )

    vcs = ctx.boundaries.version_control
    if force:
        answer = confirm(
            f"Type the item id ({item_id}) to force-remove {item.worktree_path} "
            "and discard its uncommitted work: "
        )
        if str(answer).strip() != str(item_id):
            return Result(code=EXIT_FAILED, lines=["aborted"])

    removal = vcs.remove_worktree(
        item.worktree_path, force=force, clone_path=str(repo.path)
    )
    branch_deleted = False
    if removal.worktree_removed and item.branch:
        branch_deleted = vcs.delete_branch(str(repo.path), item.branch, force=force)

    result.data = {
        "item_id": item_id,
        "worktree_removed": removal.worktree_removed,
        "branch_deleted": branch_deleted,
        "refused_reason": removal.refused_reason,
    }

    if not removal.worktree_removed:
        result.code = EXIT_FAILED
        result.say(f"refused to remove {item.worktree_path}:")
        result.say(f"  {removal.refused_reason}")
        result.say(
            "  Git refuses to remove a worktree with uncommitted or untracked changes. "
            "That refusal is the guard; --force overrides it."
        )
        return result

    result.say(f"removed worktree {item.worktree_path}")
    if item.branch and branch_deleted:
        result.say(f"deleted branch {item.branch}")
    elif item.branch:
        result.code = EXIT_FAILED
        result.say(
            f"WARNING: removed the worktree but branch {item.branch} still exists. "
            "Removal is two steps; skipping the second accumulates robot-army/* branches"
        )
    with db.transaction(ctx.conn):
        db.update_work_item_columns(ctx.conn, item_id, worktree_path=None)
    return result


def worktree_prune(ctx: Context) -> Result:
    """Clear git's record of worktrees whose directories are gone."""
    result = Result()
    outputs: dict[str, str] = {}
    for key, repo in sorted(ctx.config.repos.items()):
        try:
            outputs[key] = ctx.boundaries.version_control.prune_worktrees(str(repo.path))
        except BoundaryError as exc:
            outputs[key] = f"error: {exc}"
            result.code = EXIT_FAILED
    result.data = {"pruned": outputs}
    for key, output in outputs.items():
        result.say(f"{key}: {output.strip() or 'nothing to prune'}")
    return result


# -- lifecycle verbs --------------------------------------------------------


def cancel(ctx: Context, item_id: int, *, force: bool = False, confirm: Any = input) -> Result:
    """Stop that item's running session and **only** that session (FR-050)."""
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    session = db.latest_session_for_item(ctx.conn, item_id)
    if session is None or session.state not in (SessionState.STARTING, SessionState.RUNNING):
        return Result(
            code=EXIT_FAILED,
            lines=[f"work item {item_id} has no running session to cancel"],
        )
    if not force:
        answer = confirm(f"Stop session {session.session_id} for item {item_id}? [y/N] ")
        if str(answer).strip().lower() not in ("y", "yes"):
            return Result(code=EXIT_FAILED, lines=["aborted"])

    handle = HostHandle(
        socket_path=session.host_socket or "", argv=(), pid=session.pid
    )
    result = Result()
    try:
        ctx.boundaries.session_host.terminate(handle, session.scope)
    except BoundaryError as exc:
        return Result(code=EXIT_FAILED, lines=[f"could not stop the session: {exc}"])

    # The item becomes `interrupted` and the worktree is left untouched: cancelling is
    # about the process, not about the work.
    with db.transaction(ctx.conn):
        transition_work_item(
            ctx.conn,
            ctx.audit,
            item_id=item_id,
            target=WorkItemState.INTERRUPTED,
            reason=f"cancelled by the maintainer (session {session.session_id})",
        )
    result.data = {"item_id": item_id, "session_id": session.session_id, "scope": session.scope}
    return result.say(
        f"stopped session {session.session_id} via "
        f"{'systemd scope ' + session.scope if session.scope else 'the process group'}; "
        f"item {item_id} is now interrupted and its worktree is untouched"
    )


def resume(ctx: Context, item_id: int, *, registry_dir: Path | None = None) -> Result:
    """Start a new session restoring the previous session's context (FR-047).

    **Never happens automatically** (FR-046). Resume is always a decision the maintainer
    makes with the FR-048 signals in front of them.
    """
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    if item.state not in (WorkItemState.INTERRUPTED, WorkItemState.AWAITING_REVIEW):
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                f"work item {item_id} is {item.state}; resume requires "
                "'interrupted' or 'awaiting_review'"
            ],
        )
    previous = db.latest_session_for_item(ctx.conn, item_id)
    if previous is None:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[f"work item {item_id} has no previous session to resume"],
        )

    ok = dispatch.dispatch_item(
        ctx.conn,
        boundaries=ctx.boundaries,
        audit=ctx.audit,
        config=ctx.config,
        layout=ctx.layout,
        item_id=item_id,
        registry_dir=registry_dir,
        resume_session_id=previous.session_id,
    )
    code = EXIT_OK if ok else EXIT_FAILED
    return Result(
        code=code,
        lines=[
            f"resumed item {item_id} from session {previous.session_id}"
            if ok
            else f"resume of item {item_id} failed; see `robot-army show {item_id}`"
        ],
        data={"item_id": item_id, "resumed_from": previous.session_id, "ok": ok},
    )


def restart(ctx: Context, item_id: int, *, registry_dir: Path | None = None) -> Result:
    """A fresh session in the existing worktree, with no prior context."""
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    if item.state not in (WorkItemState.INTERRUPTED, WorkItemState.AWAITING_REVIEW):
        return Result(
            code=EXIT_PRECONDITION,
            lines=[f"work item {item_id} is {item.state}; restart requires a rested item"],
        )
    ok = dispatch.dispatch_item(
        ctx.conn,
        boundaries=ctx.boundaries,
        audit=ctx.audit,
        config=ctx.config,
        layout=ctx.layout,
        item_id=item_id,
        registry_dir=registry_dir,
    )
    return Result(
        code=EXIT_OK if ok else EXIT_FAILED,
        lines=[f"restarted item {item_id}" if ok else f"restart of item {item_id} failed"],
        data={"item_id": item_id, "ok": ok},
    )


def abandon(ctx: Context, item_id: int) -> Result:
    """Mark the item abandoned. Deliberately **not** destructive — the worktree stays."""
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    try:
        with db.transaction(ctx.conn):
            transition_work_item(
                ctx.conn,
                ctx.audit,
                item_id=item_id,
                target=WorkItemState.ABANDONED,
                reason="abandoned by the maintainer",
            )
    except Exception as exc:  # noqa: BLE001 - an illegal transition is a usage error here
        return Result(code=EXIT_FAILED, lines=[str(exc)])
    return Result(
        lines=[
            f"item {item_id} abandoned. Its worktree at "
            f"{item.worktree_path or '(none)'} was left in place — "
            f"`robot-army worktree remove {item_id}` removes it"
        ],
        data={"item_id": item_id},
    )


def retry(ctx: Context, item_id: int, *, trust_file: Path | None = None) -> Result:
    """Move a ``failed`` item back to ``ready``, refusing if the block still holds."""
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    if item.state is not WorkItemState.FAILED:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[f"work item {item_id} is {item.state}; retry applies to failed items"],
        )
    repo = ctx.config.repos.get(item.repo_key)
    if repo is None:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[f"repository {item.repo_key!r} is not in the config any more"],
        )
    try:
        dispatch.check_gates(
            ctx.conn,
            boundaries=ctx.boundaries,
            config=ctx.config,
            repo=repo,
            trust_file=trust_file,
        )
    except dispatch.DispatchBlocked as exc:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                f"refusing to retry item {item_id}: the blocking condition still holds.",
                f"  {exc}",
            ],
            data={"item_id": item_id, "blocked": str(exc)},
        )
    with db.transaction(ctx.conn):
        transition_work_item(
            ctx.conn,
            ctx.audit,
            item_id=item_id,
            target=WorkItemState.READY,
            reason="retried by the maintainer after the blocking condition cleared",
            extra_columns={"failure_reason": None, "blocked_reason": None},
        )
    return Result(lines=[f"item {item_id} is ready again"], data={"item_id": item_id})


# -- lock-aware verbs -------------------------------------------------------


def poll_now(ctx: Context, *, repo: str | None = None) -> Result:
    """Force a poll. Delegates to a running daemon, or acts directly when none holds
    the lock — so the command works the same whether or not the daemon is up."""
    if daemon_mod.is_locked(ctx.layout.lock_path):
        holder = daemon_mod.read_lock_holder(ctx.layout.lock_path)
        ctx.audit.record(
            "cli.poll_request", outcome="ok", detail={"delegated_to_pid": holder, "repo": repo}
        )
        return Result(
            lines=[
                f"a daemon is running (pid {holder}); it polls every "
                f"{ctx.config.daemon.poll_seconds}s and will pick this up on its next cycle"
            ],
            data={"delegated": True, "daemon_pid": holder},
        )

    outcomes = poll.poll_all(
        ctx.conn,
        boundaries=ctx.boundaries,
        audit=ctx.audit,
        config=ctx.config,
        dry_run=ctx.effect_level.is_simulated,
        only_repo=repo,
    )
    result = Result(data={"delegated": False, "outcomes": [vars(o) for o in outcomes]})
    for outcome in outcomes:
        if outcome.error:
            result.code = EXIT_FAILED
            result.say(f"{outcome.repo_key}: ERROR {outcome.error}")
        elif outcome.skipped_reason:
            result.say(f"{outcome.repo_key}: skipped ({outcome.skipped_reason})")
        else:
            result.say(
                f"{outcome.repo_key}: HTTP {outcome.status} "
                f"{'(unchanged)' if outcome.status == 304 else ''} "
                f"found={outcome.found} created={outcome.created} rejected={outcome.rejected}"
            )
    return result


def reconcile_now(ctx: Context, *, registry_dir: Path | None = None) -> Result:
    if daemon_mod.is_locked(ctx.layout.lock_path):
        holder = daemon_mod.read_lock_holder(ctx.layout.lock_path)
        return Result(
            lines=[
                f"a daemon is running (pid {holder}); it reconciles every "
                f"{ctx.config.daemon.reconcile_seconds}s"
            ],
            data={"delegated": True, "daemon_pid": holder},
        )
    outcome = reconcile.reconcile(
        ctx.conn,
        boundaries=ctx.boundaries,
        audit=ctx.audit,
        config=ctx.config,
        layout=ctx.layout,
        registry_dir=registry_dir,
    )
    result = Result(data={"delegated": False, **outcome.summary()})
    for key, value in outcome.summary().items():
        if key != "notes":
            result.say(f"{key:<20} {value}")
    return result


def drain_spool(ctx: Context) -> Result:
    outcome = spool.drain(ctx.conn, audit=ctx.audit, layout=ctx.layout)
    return Result(
        lines=[
            f"applied={outcome.applied} duplicates={outcome.duplicates} "
            f"quarantined={outcome.quarantined} orphaned={outcome.orphaned}"
        ],
        data=vars(outcome),
    )


def purge_simulated(ctx: Context, *, assume_yes: bool = False, confirm: Any = input) -> Result:
    """Remove ``dry_run`` rows and their sessions (FR-058). Never touches live rows.

    Does not remove worktrees those rows created — those are real directories on disk,
    and removing them is ``worktree remove``'s job.
    """
    counts = db.count_simulated(ctx.conn)
    if not any(counts.values()):
        return Result(lines=["no simulated rows to purge"], data={"purged": counts})
    if not assume_yes:
        answer = confirm(
            f"Delete {counts['work_items']} simulated work item(s) and "
            f"{counts['sessions']} simulated session(s)? [y/N] "
        )
        if str(answer).strip().lower() not in ("y", "yes"):
            return Result(code=EXIT_FAILED, lines=["aborted"])
    with ctx.audit.action("purge.simulated", detail=counts), db.transaction(ctx.conn):
        purged = db.purge_simulated(ctx.conn)
    return Result(
        lines=[
            f"purged {purged['work_items']} work item(s) and {purged['sessions']} session(s)",
            "worktrees those rows created were NOT removed — use `worktree remove`",
        ],
        data={"purged": purged},
    )


# -- anomalies --------------------------------------------------------------


def anomalies(ctx: Context, *, acknowledge: int | None = None, show_all: bool = False) -> Result:
    result = Result()
    if acknowledge is not None:
        with db.transaction(ctx.conn):
            changed = db.acknowledge_anomaly(ctx.conn, acknowledge)
        if not changed:
            return Result(
                code=EXIT_FAILED,
                lines=[f"no unacknowledged anomaly with id {acknowledge}"],
            )
        ctx.audit.record(
            "anomaly.acknowledge", outcome="ok", entity_type="anomaly", entity_id=acknowledge
        )
        result.say(f"acknowledged anomaly {acknowledge}")

    rows = db.list_anomalies(ctx.conn, unacknowledged_only=not show_all)
    result.data = {
        "anomalies": [_anomaly_dict(a) for a in rows],
        "known_kinds": list(ANOMALY_KINDS),
    }
    if not rows:
        result.say("no outstanding anomalies")
        result.say()
        result.say("kinds this system can raise: " + ", ".join(ANOMALY_KINDS))
        return result
    for anomaly in rows:
        result.say(
            f"[{anomaly.id}] {anomaly.kind}  {anomaly.entity_type or '—'}:"
            f"{anomaly.entity_id or '—'}  detected {anomaly.detected_at}"
        )
        for key, value in anomaly.detail_obj.items():
            result.say(f"      {key}: {value}")
        result.say()
    result.say("kinds this system can raise: " + ", ".join(ANOMALY_KINDS))
    return result


# -- log --------------------------------------------------------------------


_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> timedelta:
    """``30s``, ``10m``, ``2h``, ``1d``. Every rejection explains itself (FR-069).

    The amount is validated separately from the unit because a plausible-looking value
    like ``"10 fortnights"`` ends in a valid unit character, and letting ``int()`` fail
    on its own would surface "invalid literal for int()" — true, but not an explanation.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty duration; use e.g. 30s, 10m, 2h, 1d")
    unit = text[-1].lower()
    if unit not in _DURATION_UNITS:
        raise ValueError(f"unknown duration {text!r}; use e.g. 30s, 10m, 2h, 1d")
    amount = text[:-1].strip()
    if not amount.isdigit():
        raise ValueError(
            f"unknown duration {text!r}: {amount!r} is not a whole number of "
            f"{unit!r} units; use e.g. 30s, 10m, 2h, 1d"
        )
    return timedelta(seconds=int(amount) * _DURATION_UNITS[unit])


def read_log(
    ctx: Context,
    *,
    since: str | None = None,
    item_id: int | None = None,
    limit: int | None = None,
) -> Result:
    """The FR-062 reconstruction path: what happened, when, to what, with what result.

    Unparseable lines are skipped **and counted**. R14 flushes per record, so an
    interrupted final write can leave a partial line; refusing to read the log because of
    it would be exactly the wrong trade.
    """
    cutoff: datetime | None = None
    if since:
        try:
            cutoff = datetime.now(UTC) - parse_duration(since)
        except ValueError as exc:
            return Result(code=EXIT_USAGE, lines=[str(exc)])

    records: list[dict[str, Any]] = []
    skipped = 0
    for record, _raw in audit_mod.read_records(ctx.layout.log_dir):
        if record is None:
            skipped += 1
            continue
        if cutoff is not None:
            try:
                stamp = datetime.strptime(record["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            except (KeyError, ValueError):
                skipped += 1
                continue
            if stamp < cutoff:
                continue
        if item_id is not None and str(record.get("entity_id")) != str(item_id):
            continue
        records.append(record)

    if limit:
        records = records[-limit:]

    result = Result(data={"records": records, "unparseable_lines": skipped})
    for record in records:
        result.say(_format_record(record))
    if skipped:
        result.say()
        result.say(f"({skipped} unparseable line(s) skipped)")
    return result


def _format_record(record: dict[str, Any]) -> str:
    marker = {"intent": "→", "outcome": "←"}.get(str(record.get("kind")), " ")
    outcome = record.get("outcome", "")
    sim = " [simulated]" if record.get("simulated") or record.get("dry_run") else ""
    entity = ""
    if record.get("entity_type"):
        entity = f" {record['entity_type']}:{record.get('entity_id')}"
    detail = record.get("detail")
    tail = f"  {json.dumps(detail, default=str)}" if detail else ""
    return f"{record.get('ts')} {marker} {record.get('action')} [{outcome}]{entity}{sim}{tail}"


def follow_log(ctx: Context) -> Iterator[str]:
    """Tail the current day's audit file. Used by ``log --follow``."""
    import time as _time

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    path = ctx.layout.log_dir / f"audit-{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open(encoding="utf-8") as handle:
        handle.seek(0, 2)
        while True:
            line = handle.readline()
            if not line:
                _time.sleep(0.5)
                continue
            try:
                yield _format_record(json.loads(line))
            except json.JSONDecodeError:
                yield f"(unparseable) {line.rstrip()}"


# -- health -----------------------------------------------------------------


def health_check(ctx: Context, *, max_age: float | None = None, do_notify: bool = False) -> Result:
    """Exits 0 if fresh, 4 if stale or absent. Intended to be run by a systemd timer —
    **this, not the daemon, is the dead-man's switch**."""
    threshold = (
        max_age
        if max_age is not None
        else float(ctx.config.health.max_age_seconds or 3 * ctx.config.daemon.reconcile_seconds)
    )
    report = health.check(ctx.layout.heartbeat_path, max_age_seconds=threshold)
    result = Result(
        code=EXIT_OK if report.healthy else EXIT_CHECK_FAILED,
        lines=[("ok: " if report.healthy else "STALE: ") + report.reason],
        data=report.to_dict(),
    )
    if not report.healthy and do_notify:
        sent, message = health.notify(ctx.config.health.webhook_url, report)
        result.data["notified"] = sent
        result.data["notify_message"] = message
        result.say(message)
        ctx.audit.record(
            "health.notify",
            outcome="ok" if sent else "error",
            detail={"reason": report.reason, "message": message},
        )
    return result


# -- doctor -----------------------------------------------------------------


def doctor(ctx: Context, *, trust_file: Path | None = None) -> Result:
    """Check and report, without changing anything.

    Worth running first every time. It catches the failure that cost M0 the most time: a
    kitty instance carrying ``CLAUDE_CODE_CHILD_SESSION`` in its environment silently
    disables transcript saving, producing sessions that look perfect, exit 0, and can
    never be resumed.
    """
    checks: list[tuple[str, bool, str]] = []

    checks.append(("config", True, f"loaded {ctx.config.path}"))
    for warning in ctx.config.warnings:
        checks.append(("config warning", True, warning))

    try:
        version = int(ctx.conn.execute("PRAGMA user_version").fetchone()[0])
        checks.append(
            (
                "database schema",
                version == SCHEMA_VERSION,
                f"user_version={version}, expected {SCHEMA_VERSION} ({ctx.layout.db_path})",
            )
        )
    except sqlite3.Error as exc:
        checks.append(("database schema", False, str(exc)))

    for name in (ctx.config.worker.binary, "dtach", "git", ctx.config.terminal.binary):
        found = shutil.which(name)
        checks.append((f"binary: {name}", found is not None, found or "NOT FOUND on PATH"))

    # Deliberately the REAL display, not the wired one. `doctor` reports on the machine,
    # and at a simulated effect level the wired probe would cheerfully answer with a
    # fake socket — which is the opposite of what a diagnostic command is for.
    from robot_army.boundaries.kitty import KittyDisplay

    socket = KittyDisplay(ctx.config, ctx.audit).probe()
    checks.append(
        (
            "terminal socket",
            socket is not None,
            socket or f"nothing answered {ctx.config.terminal.socket_glob!r}",
        )
    )

    import os as _os

    writable = _os.access(ctx.layout.state_dir, _os.W_OK)
    checks.append(("state directory", writable, str(ctx.layout.state_dir)))

    usage = shutil.disk_usage(
        ctx.config.worktree_root if ctx.config.worktree_root.exists() else Path.home()
    )
    free_gb = usage.free / 1024**3
    checks.append(
        (
            "worktree disk space",
            free_gb > 2.0,
            f"{free_gb:.1f} GB free under {ctx.config.worktree_root} "
            "(M0 measured 499 MB for one prepared worktree)",
        )
    )

    # The M0 F19 check. These live in the *terminal daemon's* environment, which is what a
    # session inherits — not ours — so this is a best-effort read of our own as a proxy,
    # plus an explicit note about where to look properly.
    # Names only, never values: several of these carry session tokens, and `doctor`
    # output gets pasted into issues. The audit log's redaction choke point does not
    # cover stdout, so the discipline has to be repeated here.
    present = sorted(k for k in _os.environ if k.startswith("CLAUDE_CODE_"))
    checks.append(
        (
            "CLAUDE_CODE_* environment",
            "CLAUDE_CODE_CHILD_SESSION" not in present,
            (
                f"set in this process: {', '.join(present) or 'none'}. "
                "CLAUDE_CODE_CHILD_SESSION silently disables transcript saving, producing "
                "sessions that look perfect, exit 0, and can never be resumed. Sessions "
                "inherit the TERMINAL daemon's environment, not this one — check it with "
                "`tr '\\0' '\\n' < /proc/$(pidof kitty | cut -d' ' -f1)/environ "
                "| grep CLAUDE_CODE_`"
            ),
        )
    )

    for key, repo in sorted(ctx.config.repos.items()):
        record = db.get_repo(ctx.conn, key)
        trusted, explanation = dispatch.is_trusted(repo.path, trust_file=trust_file)
        checks.append(
            (
                f"repo: {key}",
                record is not None and trusted,
                f"onboarded={record is not None} trusted={trusted} — {explanation}",
            )
        )

    failures = [name for name, ok, _ in checks if not ok]
    result = Result(
        code=EXIT_OK if not failures else EXIT_FAILED,
        data={
            "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
            "failures": failures,
            "effect_level": str(ctx.effect_level),
        },
    )
    for name, ok, detail in checks:
        result.say(f"[{'ok' if ok else 'FAIL'}] {name:<28} {detail}")
    result.say()
    result.say(
        "all checks passed" if not failures else f"{len(failures)} check(s) failed: {failures}"
    )
    return result


def sessions_snapshot(ctx: Context, *, registry_dir: Path | None = None) -> dict[str, Any]:
    """A read-only view of the live registry, used by ``status --json`` consumers."""
    scan = sessions.scan(registry_dir=registry_dir)
    return {
        "entries": [
            {
                "session_id": e.session_id,
                "pid": e.pid,
                "cwd": e.cwd,
                "status": e.status,
                "ours": sessions.under_root(e.cwd, ctx.config.worktree_root),
                "scope": procinfo.systemd_scope(e.pid) if e.pid else None,
            }
            for e in scan.entries
        ],
        **sessions.summarise(scan, ctx.config.worktree_root),
    }
