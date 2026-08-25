"""Every CLI verb as a plain callable.

This module exists so that no operation's logic lives inside argument parsing. Milestone
002's HTTP API is then a *second caller of the same functions* rather than a
reimplementation — the single cheapest thing this milestone can do for the next one, at
essentially zero cost now (plan.md, Project Structure).

Each operation returns a ``Result``: an exit code, a human-readable rendering, and a
machine-readable payload. The CLI chooses which to print; it makes no decisions of its own.
"""

from __future__ import annotations

import base64
import json
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from robot_army import audit as audit_mod
from robot_army import capacity as capacity_mod
from robot_army import cleanup as cleanup_mod
from robot_army import (
    control,
    db,
    dispatch,
    health,
    intake,
    poll,
    procinfo,
    reconcile,
    sessions,
    spool,
    worktree,
)
from robot_army import (
    daemon as daemon_mod,
)
from robot_army import ordering as ordering_mod
from robot_army.audit import AuditLog
from robot_army.boundaries import BoundaryError, HostHandle, TransportError
from robot_army.cardstates import CardState
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


class SchemaMismatch(Exception):
    """The database is not at the version this code expects, and we must not migrate.

    Raised only by contexts built with ``migrate=False`` — the web process (R11). Two
    processes racing to run the same migration is a failure mode worth removing rather
    than surviving: the daemon owns the schema and the interface follows it.
    """

    def __init__(self, found: int, expected: int, path: Path) -> None:
        super().__init__(
            f"database at {path} is schema version {found}, but this code expects "
            f"{expected}. The daemon owns the schema — start or restart "
            "`robot-army run` to migrate, then start the interface again"
        )
        self.found = found
        self.expected = expected


def build_context(
    config: Config,
    *,
    effect_level: EffectLevel | None = None,
    component: str = "cli",
    migrate: bool = True,
) -> Context:
    """Assemble everything an operation needs.

    ``migrate=False`` is the web process's mode (R11): it opens the database read-write,
    verifies ``PRAGMA user_version``, and refuses rather than upgrading. Everything else
    migrates on open, as milestone 001 established.
    """
    layout = config.layout
    layout.ensure()
    level = effect_level or config.daemon.effect_level
    audit = AuditLog(layout.log_dir, component=component)
    if migrate:
        conn, _ = db.open_database(layout.db_path)
    else:
        conn = db.connect(layout.db_path)
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.Error:
            conn.close()
            raise
        if version != SCHEMA_VERSION:
            conn.close()
            audit.close()
            raise SchemaMismatch(version, SCHEMA_VERSION, layout.db_path)
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
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
) -> Result:
    """The default view: effect level, health, counts, the queue, listings, anomalies.

    Milestone 004 added the queue section and the capacity line. Both render
    ``ordering.plan`` and ``capacity.snapshot`` directly rather than deriving anything of
    their own, so what this prints as "next" is what the next dispatch will select (R8).
    """
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
    control_state = db.get_dispatch_control(ctx.conn)
    snap = capacity_mod.snapshot(
        ctx.conn, config=ctx.config, registry_dir=registry_dir, proc_root=proc_root
    )
    queue = ordering_mod.plan(ctx.conn, config=ctx.config, capacity=snap)

    result.data = {
        "effect_level": str(ctx.effect_level),
        "health": report.to_dict(),
        "counts": counts,
        "items": [_item_dict(i) for i in items],
        "anomalies": [_anomaly_dict(a) for a in anomalies],
        "include_simulated": include_simulated,
        "dispatch_paused": control_state.paused,
        "dispatch_paused_at": control_state.paused_at,
        "dispatch_paused_by": control_state.paused_by,
        "capacity": _capacity_dict(snap, ctx.config.dispatch.order),
        "queue": [_queue_dict(entry) for entry in queue],
    }

    result.say(f"effect level : {ctx.effect_level}")
    result.say(f"health       : {'ok' if report.healthy else 'STALE'} — {report.reason}")
    # FR-036: a system that is healthy and deliberately doing nothing must not read as a
    # system that is healthy and doing nothing for no reason.
    result.say(
        "dispatch     : "
        + (
            f"PAUSED since {control_state.paused_at} (by {control_state.paused_by})"
            if control_state.paused
            else "running"
        )
    )
    result.say(f"capacity     : {snap.describe()}")
    result.say(f"order        : {ctx.config.dispatch.order}")
    result.say(f"database     : {ctx.layout.db_path} (schema {SCHEMA_VERSION})")
    result.say()

    # The queue, with a position and one reason per row. One reason, not several: two shown
    # at once is how a surface stops being read (R9).
    if queue:
        result.say(f"queue ({len(queue)} eligible) — in dispatch order:")
        rows = [
            [
                str(entry.position),
                str(entry.item.id),
                entry.item.repo_key,
                f"#{entry.item.issue_number}",
                str(entry.hold) if entry.hold else "ready to dispatch",
                entry.detail[:64],
            ]
            for entry in queue
        ]
        for line in _table(rows, ["#", "item", "repo", "issue", "hold", "why"]):
            result.say(line)
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


def capacity(
    ctx: Context,
    *,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
) -> Result:
    """How full the machine is, whose sessions those are, and in what order work sits.

    The terminal door onto the same snapshot the dispatcher gates on (FR-044). It is not a
    second computation of the numbers — it is the same function, which is the whole reason
    ``capacity.py`` exists as a module rather than as a few lines inside ``dispatch.py``.

    Exits non-zero when capacity cannot be observed (FR-045). That is not a failure of the
    command — the command worked — but the answer it produced is "I do not know how many
    sessions are running", and a script that treats that as "zero are running" is the
    precise mistake this milestone exists to make impossible. The same code ``health`` uses
    for a check that ran and did not pass.
    """
    result = Result()
    snap = capacity_mod.snapshot(
        ctx.conn,
        config=ctx.config,
        audit=ctx.audit,
        registry_dir=registry_dir,
        proc_root=proc_root,
    )
    order = ctx.config.dispatch.order

    result.data = {
        "observable": snap.observable,
        "degraded": snap.degraded,
        "total": snap.total,
        "global_cap": snap.global_cap,
        "ours": len(snap.ours),
        "others": snap.others,
        "per_repo": dict(sorted(snap.per_repo.items())),
        "order": order,
        "reason": snap.reason,
    }

    if not snap.observable:
        result.code = EXIT_CHECK_FAILED
        result.say(f"capacity     : UNOBSERVABLE — {snap.reason}")
        result.say("dispatch     : withheld until capacity can be observed again")
        result.say(f"order        : {order}")
        return result

    result.say(f"capacity     : {snap.total} of {snap.global_cap} sessions running")
    # FR-003. The split is the actionable half: "two of these are mine" tells the author to
    # close one of their own, and "two are the daemon's" tells them to wait.
    result.say(f"  ours       : {len(snap.ours)}")
    result.say(f"  others     : {snap.others} (started outside this system)")
    result.say(f"observable   : yes{' (degraded — counted via /proc)' if snap.degraded else ''}")
    if snap.degraded:
        result.say(
            "               session ids are unavailable on that path, so the total is a "
            "ceiling rather than a fact"
        )
    result.say(f"order        : {order}")
    result.say()
    if snap.per_repo:
        result.say("per repository:")
        for key in sorted(snap.per_repo):
            result.say(f"  {key:<28} {snap.per_repo[key]}")
    else:
        result.say("no repository has a live session")
    return result


def _capacity_dict(snap: Any, order: str) -> dict[str, Any]:
    """The capacity summary both the terminal and the web chrome render."""
    return {
        "observable": snap.observable,
        "degraded": snap.degraded,
        "total": snap.total,
        "global_cap": snap.global_cap,
        "ours": len(snap.ours),
        "others": snap.others,
        "per_repo": dict(sorted(snap.per_repo.items())),
        "order": order,
        "reason": snap.reason,
        "summary": snap.describe(),
    }


def _queue_dict(entry: Any) -> dict[str, Any]:
    return {
        "position": entry.position,
        "item_id": entry.item.id,
        "repo_key": entry.item.repo_key,
        "issue_number": entry.item.issue_number,
        "title": entry.item.title,
        "dry_run": entry.item.dry_run,
        "hold": str(entry.hold) if entry.hold else None,
        "detail": entry.detail,
    }


def _item_dict(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "cleanup_state": item.cleanup_state,
        "cleanup_reason": item.cleanup_reason,
        "cleaned_at": item.cleaned_at,
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
        # An anomaly's detail is an open-ended dict written by whatever raised it, and it
        # is *stored* rather than only logged — so the audit log's redaction choke point
        # never saw it. Both front ends render this dict (the CLI to stdout, the web to a
        # page and a JSON payload), so it passes through the same choke point here, once,
        # rather than being filtered separately in each (FR-020).
        "detail": audit_mod.redact(anomaly.detail_obj),
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

    card = _card_for_item(ctx, item)
    result.data = {
        "item": _item_dict(item),
        "history": _history(item),
        "sessions": [_session_dict(s) for s in attempts],
        "resume_signals": signals,
        # FR-017 and FR-048: where a work item's issue came from a card, the card is shown
        # beside the issue wherever the issue is shown. ``None`` when it did not.
        "card": card,
    }

    result.say(f"work item {item.id}{_mark(item)}")
    result.say(f"  source     : {item.source_id}  {item.source_url}")
    if card:
        result.say(f"  card       : {card['card_id']}  {card['card_url']}")
    result.say(f"  repository : {item.repo_key}")
    result.say(f"  title      : {item.title}")
    result.say(f"  state      : {item.state}")
    result.say(f"  worktree   : {item.worktree_path or '(none)'}")
    result.say(f"  branch     : {item.branch or '(none)'}")
    if item.failure_reason:
        result.say(f"  failure    : {item.failure_reason}")
    if item.blocked_reason:
        result.say(f"  blocked    : {item.blocked_reason}")
    # A retained worktree or branch is visible here rather than only in the log, which is
    # what makes "why is this 499 MB still here?" answerable without reading anything.
    if item.cleanup_state:
        result.say(f"  cleanup    : {item.cleanup_state} — {item.cleanup_reason or ''}")
        result.say(f"  cleaned at : {item.cleaned_at}")

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


#: How long a GitHub-derived resume signal may be served from memory (research.md R9).
#: An interrupted view with five items auto-refreshing every 10 seconds would otherwise
#: make 1,800 GitHub calls an hour asking a question that cannot change as a result of
#: anything happening on this machine — competing directly with the polling budget FR-008
#: exists to protect.
REMOTE_SIGNAL_TTL_SECONDS = 60.0

#: (item id, branch) -> (computed_at monotonic, signals). Per-process, non-authoritative,
#: bounded by time and by the number of interrupted items, and lost on restart. Nothing is
#: persisted, so FR-013's prohibition on a *stored* copy is honoured as written.
_REMOTE_SIGNAL_CACHE: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_REMOTE_SIGNAL_LOCK = threading.Lock()


def _monotonic() -> float:
    """Indirection so a test can move the clock without sleeping for a minute."""
    return time.monotonic()


def clear_resume_signal_cache() -> None:
    """Drop every cached remote signal. For tests, and for a front end that wants a
    guaranteed-fresh read after acting on an item."""
    with _REMOTE_SIGNAL_LOCK:
        _REMOTE_SIGNAL_CACHE.clear()


def local_resume_signals(ctx: Context, item: Any) -> dict[str, Any]:
    """The two signals that come from local git. **Recomputed on every call.**

    Volatile precisely because the maintainer may be in the worktree with an editor open —
    ``docs/state.md`` says these are computed on demand and never stored "because a stored
    copy would be wrong the moment I touched the directory", and that reasoning applies to
    these two and not to the remote pair.
    """
    signals: dict[str, Any] = {
        "worktree_present": False,
        "uncommitted_changes": None,
        "commits_on_branch": None,
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
    return signals


def remote_resume_signals(ctx: Context, item: Any) -> dict[str, Any]:
    """The two signals that reach GitHub, cached in-process for a minute (R9).

    Every returned value carries ``signals_age_seconds``, so a cached answer is *visible*
    as stale rather than implied to be current. ``0`` means it was computed just now.
    """
    empty: dict[str, Any] = {
        "issue_closed": None,
        "open_pull_request": None,
        "signals_age_seconds": 0,
    }
    repo = ctx.config.repos.get(item.repo_key)
    if repo is None or not item.branch:
        return empty
    if item.dry_run:
        # FR-055: a simulated row must not cause outward-facing effects, and asking
        # GitHub about it would be exactly that.
        return empty

    key = (int(item.id), str(item.branch))
    now = _monotonic()
    with _REMOTE_SIGNAL_LOCK:
        cached = _REMOTE_SIGNAL_CACHE.get(key)
        if cached is not None and now - cached[0] < REMOTE_SIGNAL_TTL_SECONDS:
            return {**cached[1], "signals_age_seconds": int(now - cached[0])}

    fresh: dict[str, Any] = {"issue_closed": None, "open_pull_request": None}
    try:
        fresh["issue_closed"] = ctx.boundaries.issue_reader.is_closed(
            item.repo_key, item.issue_number
        )
        pr = ctx.boundaries.issue_reader.open_pr_for_branch(item.repo_key, item.branch)
        fresh["open_pull_request"] = pr.url if pr else None
    except TransportError as exc:
        # Not cached: "I could not ask" is not an answer, and caching it would suppress
        # the next attempt for a minute — the silent failure Principle III forbids.
        return {**fresh, "github_error": str(exc), "signals_age_seconds": 0}

    with _REMOTE_SIGNAL_LOCK:
        _REMOTE_SIGNAL_CACHE[key] = (now, fresh)
    return {**fresh, "signals_age_seconds": 0}


def resume_signals(ctx: Context, item: Any) -> dict[str, Any]:
    """All four signals (FR-048, FR-013). Split by cost; see R9 and the two halves above.

    Nothing here is persisted. The local pair is always fresh; the remote pair carries its
    own age so a stale value is visible rather than implied.
    """
    return {**local_resume_signals(ctx, item), **remote_resume_signals(ctx, item)}


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


def cleanup_now(ctx: Context, item_id: int | None = None) -> Result:
    """Reclaim finished work's disk, now, under exactly the automatic pass's guards.

    Runs whether or not ``[cleanup] on_issue_close`` is enabled (FR-029) — the setting
    governs *when* cleanup happens, not whether it is possible — and calls the same
    function ``reconcile._cleanup_worktrees`` calls, so the manual path cannot drift from
    the automatic one.

    Naming an item is also the act of *reconsidering* it: ``retained`` and
    ``branch_retained`` are decisions the automatic pass will not revisit, and this is what
    revisits them.
    """
    result = Result()
    try:
        decisions = cleanup_mod.sweep(
            ctx.conn,
            boundaries=ctx.boundaries,
            audit=ctx.audit,
            config=ctx.config,
            item_id=item_id,
        )
    except LookupError as exc:
        return Result(code=EXIT_FAILED, lines=[str(exc)])

    result.data = {
        "decisions": [
            {
                "item_id": d.item_id,
                "state": d.state,
                "reason": d.reason,
                "worktree_removed": d.worktree_removed,
                "branch_deleted": d.branch_deleted,
            }
            for d in decisions
        ],
        "considered": len(decisions),
        "on_issue_close": ctx.config.cleanup.on_issue_close,
    }
    if not decisions:
        result.say("nothing eligible for cleanup")
        return result
    for decision in decisions:
        result.say(f"item {decision.item_id}: {decision.state} — {decision.reason}")
    reclaimed = sum(1 for d in decisions if d.reclaimed)
    result.say()
    result.say(f"{reclaimed} of {len(decisions)} considered item(s) had their worktree removed")
    return result


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
                item.cleanup_state or "—",
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
                "cleanup_state": item.cleanup_state,
                "cleanup_reason": item.cleanup_reason,
                "cleaned_at": item.cleaned_at,
            }
        )
    result.data = {"worktrees": payload}
    if not rows:
        return result.say("no worktrees recorded")
    for line in _table(rows, ["item", "path", "branch", "condition", "size", "cleanup"]):
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
    # FR-029: a card must not sit in the in-progress list claiming to be busy when nothing
    # is. Best-effort — the item is abandoned either way, and a board that cannot be
    # written must not turn a successful abandon into a failure.
    try:
        intake.on_work_abandoned(
            ctx.conn,
            boundaries=ctx.boundaries,
            audit=ctx.audit,
            config=ctx.config,
            repo_key=item.repo_key,
            issue_number=item.issue_number,
            reason="the work item was abandoned",
            dry_run=bool(item.dry_run),
        )
    except Exception as exc:  # noqa: BLE001 - the item is abandoned; the board is cosmetic
        ctx.audit.error(
            "trello.card.move",
            error=exc,
            entity_type="work_item",
            entity_id=item_id,
            detail={"stage": "returning the card to its origin list"},
        )
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


# -- dispatch pause (milestone 002) -----------------------------------------


def _set_pause(ctx: Context, *, paused: bool, by: str) -> Result:
    """The body both verbs share. One transaction, one audit pair, no branching outside."""
    before = db.get_dispatch_control(ctx.conn)
    action = "dispatch.pause" if paused else "dispatch.unpause"
    with (
        ctx.audit.action(
            action,
            entity_type="dispatch_control",
            entity_id=1,
            detail={"by": by, "was_paused": before.paused},
        ) as outcome,
        db.transaction(ctx.conn),
    ):
        after = db.set_dispatch_paused(ctx.conn, paused=paused, by=by)
        outcome["paused"] = after.paused
        outcome["paused_at"] = after.paused_at
        outcome["paused_by"] = after.paused_by
        outcome["redundant"] = before.paused == paused

    result = Result(
        data={
            "paused": after.paused,
            "paused_at": after.paused_at,
            "paused_by": after.paused_by,
            "redundant": before.paused == paused,
        }
    )
    if before.paused == paused:
        # A redundant pause is a reported no-op, not an error (FR-033). Pausing twice is
        # not a mistake, and the *existing* pause with its original timestamp is the
        # useful answer.
        result.say(
            f"dispatch was already {'paused' if paused else 'running'}"
            + (
                f" — paused at {after.paused_at} by {after.paused_by}"
                if after.paused
                else ""
            )
        )
        return result
    if paused:
        result.say(f"dispatch paused at {after.paused_at} by {by}")
        result.say(
            "The daemon keeps polling, evaluating eligibility, reconciling, and "
            "heartbeating. It starts no new session; eligible items accumulate in ready."
        )
        result.say("This survives a daemon restart and a reboot. `robot-army unpause` clears it.")
    else:
        result.say("dispatch resumed; held items dispatch on the next tick")
    return result


def pause_dispatch(ctx: Context, *, by: str = "cli") -> Result:
    """Suspend dispatch durably (FR-033, FR-035).

    Works whether or not the daemon is running: it writes to the database, which the
    daemon reads before each dispatch decision. Pausing a stopped daemon is meaningful —
    it takes effect when it starts.
    """
    return _set_pause(ctx, paused=True, by=by)


def unpause_dispatch(ctx: Context, *, by: str = "cli") -> Result:
    return _set_pause(ctx, paused=False, by=by)


# -- attach (milestone 002) -------------------------------------------------


def attach(ctx: Context, item_id: int) -> Result:
    """Open a terminal window onto that item's running session (R10, FR-025).

    Changes **no** state and consumes no session: M0 measured that reattachment repaints
    fully and that more than one viewer is allowed, so there is deliberately no
    "is something already attached" check — FR-025's tolerance requirement is satisfied by
    the host's measured capability rather than by logic here.
    """
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    session = db.latest_session_for_item(ctx.conn, item_id)
    if session is None or session.state is not SessionState.RUNNING:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                f"work item {item_id} has no running session to attach to"
                + (f" (latest session is {session.state})" if session else "")
            ],
            data={"item_id": item_id, "attached": False},
        )
    if not session.host_socket:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[f"session {session.session_id} has no host socket recorded"],
            data={"item_id": item_id, "attached": False},
        )

    handle = HostHandle(socket_path=session.host_socket, argv=(), pid=session.pid)
    argv = ctx.boundaries.session_host.attach_command(handle)
    title = f"robot-army #{item.issue_number} {item.repo_key} (item {item_id})"
    try:
        with ctx.audit.action(
            "web.attach" if ctx.audit.component == "web" else "session.attach",
            entity_type="work_item",
            entity_id=item_id,
            target=session.session_id,
            detail={"argv": argv, "socket": session.host_socket},
            dry_run=item.dry_run,
        ) as outcome:
            display = ctx.boundaries.display.open(
                item.worktree_path or str(ctx.config.worktree_root),
                argv,
                title,
                {"robot_army_item": str(item_id), "robot_army_session": session.session_id},
                {},
            )
            outcome["window_id"] = display.window_id
    except BoundaryError as exc:
        # A missing terminal socket is a visible refusal naming what is missing, not a
        # traceback: the maintainer's next action is "start kitty", and the message has to
        # say so.
        return Result(
            code=EXIT_FAILED,
            lines=[
                f"could not open a terminal window: {exc}",
                f"  no terminal control socket answered "
                f"{ctx.config.terminal.socket_glob!r}. Nothing about the session changed.",
            ],
            data={"item_id": item_id, "attached": False, "error": str(exc)},
        )
    return Result(
        lines=[
            f"opened a terminal window (id {display.window_id}) attached to session "
            f"{session.session_id}; the session is untouched and still running"
        ],
        data={
            "item_id": item_id,
            "attached": True,
            "session_id": session.session_id,
            "window_id": display.window_id,
        },
    )


# -- lock-aware verbs -------------------------------------------------------


def _request_job(ctx: Context, name: str, *, detail: dict[str, Any] | None = None) -> Result:
    """Ask the running daemon to run ``name`` on its next tick (research.md R5).

    The response is necessarily *"requested"* rather than *"here is what it found"*: the
    daemon reports the result into the audit log, which ``robot-army log`` and the web
    audit view then show. Saying so is the honest version of what 001's contract
    previously over-promised.
    """
    holder = daemon_mod.read_lock_holder(ctx.layout.lock_path)
    with ctx.audit.action(
        f"{name}.request",
        detail={"delegated_to_pid": holder, **(detail or {})},
    ) as outcome:
        created = control.request_job(ctx.layout, name)
        outcome["marker_created"] = created
    tick = ctx.config.daemon.tick_seconds
    lines = [
        f"requested a {name} from the running daemon (pid {holder}); it will run within "
        f"one tick ({tick}s)."
    ]
    if not created:
        lines.append(f"A {name} was already requested and is still pending — this changed nothing.")
    lines.append("What it finds is reported to the audit log: `robot-army log --since 1m`")
    return Result(
        lines=lines,
        data={
            "delegated": True,
            "daemon_pid": holder,
            "requested": name,
            "marker_created": created,
            "within_seconds": tick,
        },
    )


def poll_now(ctx: Context, *, repo: str | None = None) -> Result:
    """Force a poll. Delegates to a running daemon, or acts directly when none holds
    the lock — so the command works the same whether or not the daemon is up."""
    if daemon_mod.is_locked(ctx.layout.lock_path):
        # `repo` is deliberately not carried into the marker: the daemon's poll job polls
        # every configured repository, and inventing a per-repo marker would need a second
        # file format for a filter the daemon has no way to honour. It is recorded in the
        # audit detail so the request is still reconstructable.
        return _request_job(ctx, "poll", detail={"repo": repo})

    outcomes = poll.poll_all(
        ctx.conn,
        boundaries=ctx.boundaries,
        audit=ctx.audit,
        config=ctx.config,
        dry_run=ctx.effect_level.is_simulated,
        only_repo=repo,
    )
    # asdict(), not vars(): these outcomes are slotted dataclasses and have no __dict__.
    # The web serves this payload directly, so a TypeError here would be a 500 on a
    # control FR-023 requires to work.
    result = Result(data={"delegated": False, "outcomes": [asdict(o) for o in outcomes]})
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
        return _request_job(ctx, "reconcile")
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
    outcome = spool.drain(
        ctx.conn,
        audit=ctx.audit,
        layout=ctx.layout,
        boundaries=ctx.boundaries,
        config=ctx.config,
    )
    return Result(
        lines=[
            f"applied={outcome.applied} duplicates={outcome.duplicates} "
            f"quarantined={outcome.quarantined} orphaned={outcome.orphaned}"
        ],
        data=asdict(outcome),
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
            f"Delete {counts['work_items']} simulated work item(s), "
            f"{counts['sessions']} simulated session(s) and "
            f"{counts['cards']} simulated card(s)? [y/N] "
        )
        if str(answer).strip().lower() not in ("y", "yes"):
            return Result(code=EXIT_FAILED, lines=["aborted"])
    with ctx.audit.action("purge.simulated", detail=counts), db.transaction(ctx.conn):
        purged = db.purge_simulated(ctx.conn)
    return Result(
        lines=[
            f"purged {purged['work_items']} work item(s), {purged['sessions']} session(s) "
            f"and {purged['cards']} card(s)",
            "worktrees those rows created were NOT removed — use `worktree remove`",
        ],
        data={"purged": purged},
    )


# -- cards (milestone 003) --------------------------------------------------


def _card_dict(card: Any, *, work_item_id: int | None = None) -> dict[str, Any]:
    return {
        "id": card.id,
        "card_id": card.card_id,
        "card_url": card.card_url,
        "title": card.title,
        "state": str(card.state),
        "dry_run": card.dry_run,
        "simulated": card.dry_run,
        "repo_key": card.repo_key,
        "issue_number": card.issue_number,
        "issue_url": card.issue_url,
        "source_id": card.source_id,
        "work_item_id": work_item_id,
        "reason": card.reason,
        "create_failures": card.create_failures,
        "placed_list_id": card.placed_list_id,
        "origin_list_id": card.origin_list_id,
        "archived_at": card.archived_at,
        "first_seen_at": card.first_seen_at,
        "updated_at": card.updated_at,
        "age_seconds": _age_seconds(card.updated_at),
    }


def _age_seconds(stamp: str | None) -> int | None:
    if not stamp:
        return None
    try:
        parsed = datetime.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return int((datetime.now(UTC) - parsed).total_seconds())


def _card_work_items(ctx: Context, cards: list[Any]) -> dict[int, int]:
    """Card row id → work item id, joined on ``(repo_key, issue_number)`` (R16).

    A join rather than a column on ``work_items``: the fact is already derivable from
    ``work_items.source_id``, and storing it again would create a second place for it to be
    wrong. One query for the whole set rather than one per card, because both front ends
    render listings and a per-row query is how a listing becomes slow quietly.
    """
    wanted = {c.source_id: c.id for c in cards if c.source_id}
    if not wanted:
        return {}
    placeholders = ",".join("?" * len(wanted))
    rows = ctx.conn.execute(
        f"SELECT id, source_id FROM work_items WHERE source_id IN ({placeholders})",  # noqa: S608
        tuple(wanted),
    ).fetchall()
    return {wanted[row["source_id"]]: row["id"] for row in rows if row["source_id"] in wanted}


def _card_for_item(ctx: Context, item: Any) -> dict[str, Any] | None:
    """The card a work item's issue came from, or ``None`` (R16, FR-048).

    Derived by joining ``cards`` on ``(repo_key, issue_number)`` against the ``repo#number``
    in ``work_items.source_id``. **No column on ``work_items``**: the fact is already
    derivable, and storing it again would create a second place for it to be wrong.
    """
    if ctx.config.trello is None or not item.repo_key or item.issue_number is None:
        return None
    card = db.find_card_by_issue(
        ctx.conn,
        repo_key=item.repo_key,
        issue_number=item.issue_number,
        dry_run=bool(item.dry_run),
    )
    return _card_dict(card, work_item_id=item.id) if card else None


def cards(
    ctx: Context,
    *,
    state: str | None = None,
    include_simulated: bool = False,
) -> Result:
    """List tracked cards with their state, resolved issue, and reason (FR-026, FR-047).

    Exits ``3`` when no board is configured, with a message saying so, rather than printing
    an empty table: an empty table would misrepresent "not configured" as "nothing to do",
    and those call for very different next actions.

    An empty list on a *configured* board exits ``0`` — an empty board is not a failure.
    """
    if ctx.config.trello is None:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                "no [trello] section is configured, so no board is being read.",
                f"Add one to {ctx.config.path} to enable the card source "
                "(specs/003-trello-source/contracts/config.md).",
            ],
            data={"configured": False, "cards": []},
        )

    states = [CardState(state)] if state else None
    rows = db.list_cards(ctx.conn, include_simulated=include_simulated, states=states)
    links = _card_work_items(ctx, rows)
    payload = [_card_dict(card, work_item_id=links.get(card.id)) for card in rows]

    result = Result(
        data={
            "configured": True,
            "board_id": ctx.config.trello.board_id,
            "cards": payload,
            "include_simulated": include_simulated,
        }
    )
    if not payload:
        result.say("no cards tracked yet")
        return result

    table_rows = [
        [
            row["card_id"],
            (row["title"][:40] + "…") if len(row["title"]) > 41 else row["title"],
            row["state"] + ("*" if row["simulated"] else ""),
            row["repo_key"] or "—",
            f"#{row['issue_number']}" if row["issue_number"] else "—",
            _human_age(row["age_seconds"]),
            (row["reason"] or "")[:60],
        ]
        for row in payload
    ]
    for line in _table(
        table_rows, ["card", "title", "state", "repository", "issue", "in state", "reason"]
    ):
        result.say(line)
    return result


def _human_age(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def rescan(ctx: Context, card_id: str, *, all_needs_info: bool = False) -> Result:
    """Force re-evaluation of a card awaiting clarification (FR-024).

    A forced job request through the existing ``control.py`` marker, exactly as ``poll``
    and ``reconcile`` already are — the daemon drains it on its next tick. The response is
    therefore "requested", not "here is what it found", which is the honest thing to say
    about a cross-process request.

    The refusals are deliberate rather than permissive. Rescanning a *linked* card is
    meaningless, and silently doing nothing would be worse than saying so: the author would
    be left believing they had retried something.
    """
    if ctx.config.trello is None:
        return Result(
            code=EXIT_PRECONDITION,
            lines=["no [trello] section is configured, so there are no cards to rescan"],
        )

    if not all_needs_info:
        card = db.find_card(
            ctx.conn,
            board_id=ctx.config.trello.board_id,
            card_id=card_id,
            dry_run=ctx.effect_level.is_simulated,
        )
        if card is None:
            return Result(
                code=EXIT_FAILED,
                lines=[f"card {card_id!r} is not tracked — `robot-army cards` lists what is"],
            )
        if card.state is not CardState.NEEDS_INFO:
            return Result(
                code=EXIT_USAGE,
                lines=[
                    f"card {card_id} is {card.state}, not needs_info. Rescanning it would "
                    "mean nothing — only a card awaiting clarification has anything to "
                    "re-evaluate"
                ],
            )

    if not daemon_mod.is_locked(ctx.layout.lock_path):
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                "no daemon is running to service the request. Start `robot-army run`, or "
                "wait for its next board interval once it is up"
            ],
        )

    result = _request_job(
        ctx, "rescan", detail={"card_id": None if all_needs_info else card_id}
    )
    result.data["card_id"] = None if all_needs_info else card_id
    result.data["all_needs_info"] = all_needs_info
    return result


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


#: A record either matches the active filters, fails them, or cannot be judged. The third
#: case is not the second: an unparseable timestamp means the record is *skipped and
#: counted*, which is what FR-044 requires readers to report rather than silently drop.
_MATCH = "match"
_REJECT = "reject"
_UNREADABLE = "unreadable"


def _judge_record(
    record: dict[str, Any],
    *,
    cutoff: datetime | None,
    item_id: int | None,
    outcome: str | None,
) -> str:
    """Apply the log filters to one record. One implementation, two readers.

    ``read_log`` scans forwards and ``read_log_page`` scans backwards; sharing this keeps
    "what does ``--item 42`` mean" from having two answers.
    """
    if cutoff is not None:
        try:
            stamp = datetime.strptime(record["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except (KeyError, ValueError):
            return _UNREADABLE
        if stamp < cutoff:
            return _REJECT
    if item_id is not None and str(record.get("entity_id")) != str(item_id):
        return _REJECT
    if outcome is not None and str(record.get("outcome")) != outcome:
        return _REJECT
    return _MATCH


def read_log(
    ctx: Context,
    *,
    since: str | None = None,
    item_id: int | None = None,
    limit: int | None = None,
    outcome: str | None = None,
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
        verdict = _judge_record(record, cutoff=cutoff, item_id=item_id, outcome=outcome)
        if verdict is _UNREADABLE:
            skipped += 1
        elif verdict is _MATCH:
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


#: Default page size for the paged reader. Bounded by construction, per SC-014.
LOG_PAGE_SIZE = 100


def _encode_cursor(file_name: str, consumed: int) -> str:
    """An opaque cursor. Opaque because its shape is ours to change, not a caller's to
    depend on — FR-009 says there is no stable API here."""
    raw = json.dumps({"f": file_name, "n": consumed}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, int] | None:
    """``None`` for anything unreadable: a hand-edited cursor restarts from the newest
    page rather than erroring, because the page it names may legitimately no longer exist."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        return str(payload["f"]), int(payload["n"])
    except (ValueError, KeyError, TypeError):
        return None


def _scan_file_backwards(
    path: Path,
    *,
    judge: Callable[[dict[str, Any]], str],
    skip: int,
    want: int,
) -> tuple[list[dict[str, Any]], int, int]:
    """Read one daily file newest-record-first, returning ``(records, matched, skipped)``.

    ``matched`` counts every record that passed the filters **including** the ``skip``
    already consumed by an earlier page, because that count is exactly what the next
    cursor has to carry for pages to stay disjoint across a file boundary.
    """
    found: list[dict[str, Any]] = []
    matched = 0
    skipped = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        # A file that vanished between the glob and the read is not a reason to fail the
        # page; it is counted so the silence is not silent.
        return found, matched, 1
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        verdict = judge(record)
        if verdict is _UNREADABLE:
            skipped += 1
            continue
        if verdict is _REJECT:
            continue
        matched += 1
        if matched <= skip:
            continue
        found.append(record)
        if len(found) >= want:
            break
    return found, matched, skipped


def read_log_page(
    ctx: Context,
    *,
    since: str | None = None,
    item_id: int | None = None,
    outcome: str | None = None,
    cursor: str | None = None,
    limit: int = LOG_PAGE_SIZE,
) -> Result:
    """One bounded page of the audit log, newest first (research.md R14, FR-044).

    Daily files are read newest-first and each is scanned backwards, stopping the moment
    the page is full. SC-014 wants a bounded page against 100,000 records in under two
    seconds; reading one or two daily files satisfies any first page, and reading the whole
    history to show a hundred lines would grow without bound by construction.

    The cursor names ``(file, records already consumed from it)``, so paging across a file
    boundary produces disjoint pages rather than a repeated or a skipped record.
    """
    cutoff: datetime | None = None
    if since:
        try:
            cutoff = datetime.now(UTC) - parse_duration(since)
        except ValueError as exc:
            return Result(code=EXIT_USAGE, lines=[str(exc)])
    if outcome is not None and outcome not in ("ok", "error", "pending"):
        return Result(
            code=EXIT_USAGE,
            lines=[f"unknown outcome {outcome!r}; use ok, error, or pending"],
        )
    limit = max(1, min(int(limit), 1000))

    files = sorted(Path(ctx.layout.log_dir).glob("audit-*.jsonl"), reverse=True)
    start_index = 0
    skip_in_file = 0
    if cursor:
        decoded = _decode_cursor(cursor)
        if decoded is not None:
            name, consumed = decoded
            for index, path in enumerate(files):
                if path.name == name:
                    start_index, skip_in_file = index, consumed
                    break

    records: list[dict[str, Any]] = []
    skipped = 0
    next_cursor: str | None = None
    judge = lambda record: _judge_record(  # noqa: E731 - one binding, read once, below
        record, cutoff=cutoff, item_id=item_id, outcome=outcome
    )
    for path in files[start_index:]:
        found, matched, file_skipped = _scan_file_backwards(
            path, judge=judge, skip=skip_in_file, want=limit - len(records)
        )
        records.extend(found)
        skipped += file_skipped
        if len(records) >= limit:
            next_cursor = _encode_cursor(path.name, matched)
            break
        skip_in_file = 0

    filters = {"item": item_id, "since": since, "outcome": outcome}
    result = Result(
        data={
            "records": records,
            "filters": filters,
            "skipped_lines": skipped,
            "unparseable_lines": skipped,
            "has_more": next_cursor is not None,
            "next_cursor": next_cursor,
            "page_size": limit,
        }
    )
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

    # The board's five checks (contracts/config.md), so it can be verified without
    # starting the daemon — which is the whole reason `doctor` performs them rather than
    # leaving them to startup. Absent entirely when no board is configured: an
    # unconfigured installation has nothing to check, and inventing a passing check for it
    # would say something about a board that does not exist.
    if ctx.config.trello is not None:
        status = intake.check_board(
            boundaries=ctx.boundaries, audit=ctx.audit, config=ctx.config
        )
        for check in status.checks:
            checks.append((f"board: {check.name}", check.ok, check.detail))

    failures = [name for name, ok, _ in checks if not ok]
    result = Result(
        # 4, not 1: this is a *check* command, and the exit table reserves 4 for a check
        # that failed. `health` already uses it for the same reason.
        code=EXIT_OK if not failures else EXIT_CHECK_FAILED,
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
