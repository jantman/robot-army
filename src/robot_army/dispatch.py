"""Turning a ``ready`` work item into a confirmed live session.

The single most important rule in this module: **a work item is never marked ``active``
on the strength of the launch call returning successfully** (FR-025). ``kitty @ launch``
returns ``0`` and a valid window id even when nothing started — demonstrated three times
in M0 (F16), with no diagnostic anywhere. The only observation that distinguishes a
healthy session from a convincing imitation of one is a registry entry carrying the
``session_id`` we generated, so that is what confirmation waits for.

Two gates run before any of that, both failing *closed*:

* **Trust** (FR-003). Trust is keyed on the primary clone, not the worktree (M0 E1.5), so
  a worktree of an untrusted repository blocks forever on an invisible modal dialog. A
  missing file or missing key is treated as untrusted, because the cost of a false
  negative is a clear error message and the cost of a false positive is a session hanging
  invisibly forever.
* **The committed-settings fingerprint** (FR-004). The trust dialog also accepts whatever
  tool permissions a repository has *committed* — "This folder pre-approves 3 tool
  permissions … These will apply without asking" (M0 F9). On a repository the maintainer
  does not control, anyone with commit access could pre-approve tools a dispatched session
  honours silently. Any difference from the approved fingerprint blocks dispatch.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from robot_army import (
    capacity,
    db,
    intake,
    notifications,
    ordering,
    procinfo,
    prompt,
    sessions,
    worktree,
)
from robot_army.audit import utc_now_iso
from robot_army.boundaries import BoundaryError, DisplayHandle, Issue
from robot_army.ordering import HoldReason
from robot_army.paths import claude_trust_file
from robot_army.states import (
    SessionState,
    WorkItemState,
    transition_session,
    transition_work_item,
)

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config, RepoConfig
    from robot_army.effects import Boundaries
    from robot_army.paths import Layout

#: The committed files whose bytes make up the fingerprint. Their *contents* are never
#: interpreted — a change is a change regardless of what it means, and parsing them would
#: mean tracking upstream's settings schema for no benefit.
SETTINGS_PATHS: tuple[str, ...] = (".claude/settings.json", ".claude/settings.local.json")

WRAPPER_NAME = "robot-army-session-wrapper"

#: Hold reasons that end the pass rather than skipping one item.
#:
#: ``break`` versus ``continue`` is the entire distinction FR-012 and FR-020 draw. A paused
#: system and an unobservable capacity stop everything; a full machine stops everything
#: because no *later* item could fit into a slot this one could not. Anything else is a
#: condition of one item, and a queue that stops on one item's condition is a queue where
#: one blocked repository stalls every other.
_GLOBAL_HOLDS: frozenset[HoldReason] = frozenset(
    {HoldReason.PAUSED, HoldReason.CAPACITY_UNOBSERVABLE, HoldReason.GLOBAL_CAP}
)


class DispatchBlocked(Exception):
    """A precondition failed. Carries a message the maintainer can act on."""


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    #: The full chain kitty launches: ``dtach -A <socket> <wrapper> <item> -- <worker...>``.
    argv: list[str]
    #: What the host is asked to host: the wrapper and everything after it.
    payload_argv: list[str]
    #: The worker invocation alone, for diagnosis.
    worker_argv: list[str]
    session_id: str
    socket_path: str
    title: str
    user_vars: dict[str, str]
    env: dict[str, str]


# -- gates ------------------------------------------------------------------


def is_trusted(clone_path: str | Path, *, trust_file: Path | None = None) -> tuple[bool, str]:
    """Read ``~/.claude.json`` → ``projects[<clone>].hasTrustDialogAccepted``.

    Fails closed on every unexpected shape. Returns ``(trusted, explanation)``.
    """
    path = Path(trust_file) if trust_file else claude_trust_file()
    resolved = str(Path(clone_path).expanduser().resolve())
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, f"{path} does not exist, so no repository has been trusted"
    except OSError as exc:
        return False, f"could not read {path}: {exc}"

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"{path} is not valid JSON ({exc}); treating as untrusted"
    if not isinstance(payload, dict):
        return False, f"{path} is not a JSON object; treating as untrusted"

    projects = payload.get("projects")
    if not isinstance(projects, dict):
        return False, f"{path} has no 'projects' object; treating as untrusted"

    entry = projects.get(resolved)
    if entry is None:
        # Try the unresolved spelling too: the worker records whatever path it was given.
        entry = projects.get(str(Path(clone_path).expanduser()))
    if not isinstance(entry, dict):
        return False, (
            f"{resolved} has no entry in {path}. Open the repository in the worker once "
            "and accept the trust dialog"
        )
    if entry.get("hasTrustDialogAccepted") is not True:
        return False, (
            f"{resolved} exists in {path} but hasTrustDialogAccepted is not true. "
            "A session there would block on an invisible modal dialog forever"
        )
    return True, f"{resolved} is trusted"


def compute_fingerprint(
    boundaries: Boundaries, clone_path: str, base_ref: str
) -> dict[str, str]:
    """SHA-256 over each committed settings file **as it exists at the base ref**.

    Read from the git object store rather than the filesystem, because what matters is
    what a freshly created worktree will contain (R12). A file that does not exist at the
    ref is simply absent from the mapping — and its later *appearance* is therefore a
    difference, which is exactly the case FR-004 wants to block on.
    """
    fingerprint: dict[str, str] = {}
    for relative in SETTINGS_PATHS:
        content = boundaries.version_control.show_file_at_ref(clone_path, base_ref, relative)
        if content is None:
            continue
        fingerprint[relative] = hashlib.sha256(content).hexdigest()
    return fingerprint


def read_committed_settings(
    boundaries: Boundaries, clone_path: str, base_ref: str
) -> dict[str, str]:
    """The full text of each committed settings file, for ``onboard`` to display."""
    contents: dict[str, str] = {}
    for relative in SETTINGS_PATHS:
        content = boundaries.version_control.show_file_at_ref(clone_path, base_ref, relative)
        if content is not None:
            contents[relative] = content.decode("utf-8", errors="replace")
    return contents


def check_gates(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    config: Config,
    repo: RepoConfig,
    trust_file: Path | None = None,
) -> None:
    """Raise ``DispatchBlocked`` unless onboarding, trust, and fingerprint all pass."""
    record = db.get_repo(conn, repo.key)
    if record is None:
        raise DispatchBlocked(
            f"repository {repo.key!r} is not onboarded — run `robot-army onboard {repo.key}`"
        )

    trusted, explanation = is_trusted(repo.path, trust_file=trust_file)
    if not trusted:
        raise DispatchBlocked(f"workspace trust check failed: {explanation}")

    base_ref = repo.base_branch or config.worker.base_branch
    current = compute_fingerprint(boundaries, str(repo.path), base_ref)
    approved = record.fingerprint
    if current != approved:
        added = sorted(set(current) - set(approved))
        removed = sorted(set(approved) - set(current))
        changed = sorted(k for k in set(current) & set(approved) if current[k] != approved[k])
        raise DispatchBlocked(
            f"committed tool-permission settings at {base_ref} differ from what was "
            f"approved at onboarding "
            f"(added: {added or 'none'}; removed: {removed or 'none'}; "
            f"changed: {changed or 'none'}). "
            f"Review them and run `robot-army onboard {repo.key} --reapprove`"
        )


# -- launch -----------------------------------------------------------------


def validate_before_launch(
    *,
    boundaries: Boundaries,
    worktree_path: str,
    settings_path: str | None,
    permission_mode: str,
    config: Config,
) -> list[str]:
    """Check everything the worker tolerates *silently* (FR-026).

    M0 measured two silent-tolerance hazards: ``--add-dir`` pointing at a nonexistent
    path, and a malformed ``--settings`` file — **both exit 0 and proceed**. Upstream
    documentation notes invalid settings are "silently ignored" in print mode, implying
    interactive mode may show a blocking dialog instead, which is the same invisible-hang
    hazard as the trust dialog. Validating here converts either into a clear failure.
    """
    problems: list[str] = []
    if not boundaries.version_control.worktree_exists(worktree_path):
        problems.append(
            f"worktree path does not exist or is not a directory: {worktree_path}"
        )
    if settings_path:
        settings = Path(settings_path)
        if not settings.is_file():
            problems.append(f"--settings file does not exist: {settings}")
        else:
            try:
                json.loads(settings.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(f"--settings file is not valid JSON ({settings}): {exc}")
    from robot_army.config import VALID_PERMISSION_MODES

    if permission_mode not in VALID_PERMISSION_MODES:
        problems.append(
            f"permission_mode {permission_mode!r} is not one of "
            f"{', '.join(VALID_PERMISSION_MODES)}"
        )
    if not config.worker.binary:
        problems.append("[worker] binary is empty")
    return problems


def build_launch_plan(
    *,
    config: Config,
    layout: Layout,
    boundaries: Boundaries,
    repo_key: str,
    item_id: int,
    issue: Issue,
    worktree_path: str,
    branch: str,
    session_id: str,
    env: dict[str, str] | None = None,
    resume_session_id: str | None = None,
    wrapper: str = WRAPPER_NAME,
) -> LaunchPlan:
    """Assemble the verified chain from R19.

    Every element is a measured M0 finding rather than a preference; the ones that would
    look like cruft to a later reader are commented where they appear.
    """
    name = prompt.session_name(repo_key, issue.number)
    socket_path = str(layout.socket_for(item_id))

    worker_argv: list[str] = [config.worker.binary, "--session-id", session_id]
    if resume_session_id:
        # A resume is a *new attempt* restoring the prior session's context (FR-047).
        worker_argv += ["--resume", resume_session_id]
    # Both name flags are set because they surface in different places and the
    # auto-derived default is not identifiable (R19).
    worker_argv += ["-n", name, "--remote-control", name]
    mode = config.permission_mode_for(repo_key)
    worker_argv += ["--permission-mode", mode]
    model = config.model_for(repo_key)
    if model:
        worker_argv += ["--model", model]
    # --bare is NEVER used: it skips CLAUDE.md, hooks, skills, plugins, and MCP
    # auto-discovery — exactly the accumulated per-repository context that makes these
    # repositories work well.

    instructions = prompt.read_instructions(worktree_path)
    body = prompt.compose(issue, repo_key=repo_key, branch=branch, instructions=instructions)
    worker_argv.append(body)

    # The wrapper takes its own `--` before the payload. dtach, which precedes it, takes
    # NO `--` at all — it rejects one outright with `Invalid option '--'` (M0 F10).
    wrapped = [wrapper, str(item_id), "--", *worker_argv]
    host_handle_argv = boundaries.session_host.build_argv(socket_path, wrapped)

    session_env = {
        "ROBOT_ARMY_ITEM": str(item_id),
        "ROBOT_ARMY_SESSION_ID": session_id,
        "ROBOT_ARMY_SPOOL_DIR": str(layout.spool_dir),
        "ROBOT_ARMY_LOG_DIR": str(layout.session_log_dir),
        # M0 F19: a stray CLAUDE_CODE_CHILD_SESSION in the terminal daemon's environment
        # silently disables transcript saving, producing sessions that look perfect,
        # exit 0, and can never be resumed. Forcing persistence is the cheap defence.
        "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE": "1",
    }
    if env:
        session_env.update(env)

    return LaunchPlan(
        argv=host_handle_argv,
        payload_argv=wrapped,
        worker_argv=worker_argv,
        session_id=session_id,
        socket_path=socket_path,
        title=name,
        user_vars={"ra_item": str(item_id)},
        env=session_env,
    )


def dispatch_item(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    layout: Layout,
    item_id: int,
    trust_file: Path | None = None,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
    resume_session_id: str | None = None,
    skip_gates: bool = False,
) -> bool:
    """Prepare and launch one item. Returns ``True`` when it reached ``active``.

    Failure at any point leaves the item ``failed`` with a reason, never ``active`` and
    never silently stuck.
    """
    item = db.get_work_item(conn, item_id)
    if item is None:
        raise LookupError(f"no work item {item_id}")
    dry_run = item.dry_run
    repo = config.repos.get(item.repo_key)
    if repo is None:
        _fail(
            conn,
            audit,
            item_id,
            f"repository {item.repo_key!r} is no longer in the config",
            boundaries=boundaries,
            config=config,
            item=item,
        )
        return False

    with db.transaction(conn):
        transition_work_item(
            conn,
            audit,
            item_id=item_id,
            target=WorkItemState.DISPATCHING,
            reason="dispatcher selected it and capacity exists",
        )

    # -- gates -------------------------------------------------------------
    if not skip_gates:
        try:
            check_gates(
                conn,
                boundaries=boundaries,
                config=config,
                repo=repo,
                trust_file=trust_file,
            )
        except DispatchBlocked as exc:
            _fail(
                conn,
                audit,
                item_id,
                str(exc),
                blocked=True,
                boundaries=boundaries,
                config=config,
                item=item,
            )
            _comment_failure(boundaries, audit, config, item, str(exc))
            return False

    # -- worktree ----------------------------------------------------------
    reuse_worktree = item.worktree_path and boundaries.version_control.worktree_exists(
        item.worktree_path
    )
    if reuse_worktree:
        worktree_path = item.worktree_path or ""
        branch = item.branch or ""
        env = worktree.resolve_env(repo)
    else:
        preparation = worktree.prepare(
            boundaries=boundaries,
            audit=audit,
            config=config,
            repo=repo,
            item_id=item_id,
            issue_number=item.issue_number,
            title=item.title,
            dry_run=dry_run,
        )
        with db.transaction(conn):
            db.update_work_item_columns(
                conn,
                item_id,
                worktree_path=preparation.worktree_path,
                branch=preparation.branch,
                prepare_output=preparation.output or None,
            )
        if not preparation.ok:
            _fail(
                conn,
                audit,
                item_id,
                preparation.failure_reason or "preparation failed",
                boundaries=boundaries,
                config=config,
                item=item,
            )
            _comment_failure(
                boundaries, audit, config, item, preparation.failure_reason or "preparation failed"
            )
            return False
        worktree_path = preparation.worktree_path
        branch = preparation.branch
        env = preparation.env or {}

    # -- launch ------------------------------------------------------------
    issue = Issue(
        number=item.issue_number,
        title=item.title,
        body=item.body,
        url=item.source_url,
        labels=tuple(item.label_list),
        author=config.github.author,
        state="open",
    )
    session_id = str(uuid.uuid4())
    plan = build_launch_plan(
        config=config,
        layout=layout,
        boundaries=boundaries,
        repo_key=item.repo_key,
        item_id=item_id,
        issue=issue,
        worktree_path=worktree_path,
        branch=branch,
        session_id=session_id,
        env=env,
        resume_session_id=resume_session_id,
    )

    problems = validate_before_launch(
        boundaries=boundaries,
        worktree_path=worktree_path,
        settings_path=None,
        permission_mode=config.permission_mode_for(item.repo_key),
        config=config,
    )
    if problems:
        reason = "pre-launch validation failed: " + "; ".join(problems)
        _fail(conn, audit, item_id, reason, boundaries=boundaries, config=config, item=item)
        _comment_failure(boundaries, audit, config, item, reason)
        return False

    # The session row is written BEFORE the process exists (FR-020). A process that dies
    # before writing anything still has a row naming it, so reconciliation has something
    # to reason about rather than a gap.
    with db.transaction(conn):
        attempt = db.next_attempt(conn, item_id)
        session_row_id = db.insert_session(
            conn,
            work_item_id=item_id,
            session_id=session_id,
            attempt=attempt,
            dry_run=dry_run,  # denormalised so session queries need no join
            host_socket=plan.socket_path,
            launch_argv=plan.argv,
        )

    boundaries.session_host.spawn(
        worktree_path, plan.payload_argv, plan.socket_path
    )
    try:
        display_handle: DisplayHandle = boundaries.display.open(
            worktree_path, plan.argv, plan.title, plan.user_vars, plan.env
        )
    except BoundaryError as exc:
        reason = f"launch failed: {exc}"
        with db.transaction(conn):
            transition_session(
                conn,
                audit,
                session_row_id=session_row_id,
                target=SessionState.LOST,
                reason=reason,
            )
        _fail(conn, audit, item_id, reason, boundaries=boundaries, config=config, item=item)
        _comment_failure(boundaries, audit, config, item, reason)
        return False

    with db.transaction(conn):
        db.update_session_columns(conn, session_row_id, window_id=display_handle.window_id)

    # -- confirmation (FR-025) ---------------------------------------------
    # Confirmation goes through the host boundary, which is what keeps the real/simulated
    # choice inside effects.py's wiring. A branch here on the effect level would be
    # exactly the drift FR-053 exists to prevent.
    entry = boundaries.session_host.confirm_session(
        session_id,
        float(config.daemon.confirm_timeout_seconds),
        registry_dir=registry_dir,
        proc_root=proc_root,
    )

    if entry is None:
        window_state = None
        try:
            window_state = boundaries.display.window_state(display_handle)
        except Exception:  # noqa: BLE001 - diagnosis is best-effort; the failure stands
            window_state = None
        reason = (
            f"launch was not confirmed within {config.daemon.confirm_timeout_seconds}s: no "
            f"session registry entry appeared for session id {session_id}. The launch call "
            "itself returned success, which M0 F16 measured as meaningless on its own"
        )
        # A session *did* start in our worktree, but carrying an id we did not ask for.
        # That is an anomaly, not a success (R10's corollary, FR-065): it means something
        # overrode --session-id, and the session we launched is not the one we can track.
        _detect_session_id_mismatch(
            conn,
            audit,
            item_id=item_id,
            expected=session_id,
            worktree_path=worktree_path,
            registry_dir=registry_dir,
            proc_root=proc_root,
        )
        with db.transaction(conn):
            transition_session(
                conn,
                audit,
                session_row_id=session_row_id,
                target=SessionState.LOST,
                reason="confirmation window elapsed",
            )
        audit.record(
            "dispatch.unconfirmed",
            outcome="error",
            entity_type="work_item",
            entity_id=item_id,
            detail={
                "session_id": session_id,
                "launch_argv": plan.argv,
                "window_id": display_handle.window_id,
                "window_state": window_state,
            },
            dry_run=dry_run,
        )
        _fail(conn, audit, item_id, reason, boundaries=boundaries, config=config, item=item)
        _comment_failure(boundaries, audit, config, item, reason)
        return False

    # M0 F18: kitty places each launched window in its own scope, so this is the handle
    # that lets us stop exactly this session's process tree later. Read once, at
    # confirmation, and treated as opaque thereafter — never recomputed.
    scope = procinfo.systemd_scope(entry.pid, root=proc_root) if entry.pid else None

    with db.transaction(conn):
        db.update_session_columns(
            conn,
            session_row_id,
            pid=entry.pid,
            proc_start=entry.proc_start,
            scope=scope,
        )
        transition_session(
            conn,
            audit,
            session_row_id=session_row_id,
            target=SessionState.RUNNING,
            reason="registry entry with our session id was observed",
        )
        transition_work_item(
            conn,
            audit,
            item_id=item_id,
            target=WorkItemState.ACTIVE,
            reason="session confirmed present",
            extra_columns={"failure_reason": None},
        )

    # Outside the transaction above, deliberately (R14). Also *after* confirmation rather
    # than after the launch call: M0 F16 measured that call's success as meaningless on its
    # own, and a message saying a session started when none did is worse than silence.
    notifications.emit(
        boundaries=boundaries,
        audit=audit,
        config=config,
        kind="dispatch",
        item_id=item_id,
        repo_key=item.repo_key,
        title=f"robot-army: session running for {item.repo_key}#{item.issue_number}",
        detail=f"{item.title} — worktree {worktree_path}",
        url=item.source_url,
    )

    # The board follows reality, and reality is a *confirmed* session (FR-027). Placed
    # here rather than at the launch call because M0 F16 measured that call's success as
    # meaningless on its own — a card reading "in progress" for a session that never
    # started is the lie milestone 003 exists to remove.
    #
    # A board failure must never fail a dispatch that already succeeded, so this is
    # deliberately best-effort and says so in the log when it does not work.
    try:
        intake.on_session_active(
            conn,
            boundaries=boundaries,
            audit=audit,
            config=config,
            repo_key=item.repo_key,
            issue_number=item.issue_number,
            dry_run=dry_run,
        )
    except Exception as exc:  # noqa: BLE001 - the session is running; the board is cosmetic
        audit.error(
            "trello.card.move",
            error=exc,
            entity_type="work_item",
            entity_id=item_id,
            detail={"stage": "moving the card to the in-progress list after confirmation"},
            dry_run=dry_run,
        )

    audit.record(
        "dispatch.confirmed",
        outcome="ok",
        entity_type="work_item",
        entity_id=item_id,
        detail={
            "session_id": session_id,
            "pid": entry.pid,
            "scope": scope,
            "window_id": display_handle.window_id,
            "socket": plan.socket_path,
            "worktree": worktree_path,
            "branch": branch,
        },
        dry_run=dry_run,
    )

    # A transcript that never appears means the session is permanently unresumable
    # despite looking healthy (M0 F19). Worth an anomaly, not a failure.
    #
    # The dry_run guard here is FR-055, not FR-053: a simulated session never wrote a
    # transcript because it never ran, and reporting that as an anomaly would be noise.
    # This is a decision about *reporting on a simulated row*, not about which
    # implementation to call — that choice lives in effects.py and nowhere else.
    if not dry_run and not sessions.transcript_exists(session_id):
        with db.transaction(conn):
            db.raise_anomaly(
                conn,
                kind="no_transcript",
                entity_type="session",
                entity_id=session_id,
                detail={
                    "item_id": item_id,
                    "note": (
                        "session confirmed but no resumable transcript found; check for "
                        "CLAUDE_CODE_* variables in the terminal daemon's environment "
                        "(robot-army doctor)"
                    ),
                },
            )

    _comment_dispatch(boundaries, audit, config, item, worktree_path, branch, session_id)
    return True



def _detect_session_id_mismatch(
    conn: sqlite3.Connection,
    audit: AuditLog,
    *,
    item_id: int,
    expected: str,
    worktree_path: str,
    registry_dir: Path | None,
    proc_root: Path | None,
) -> bool:
    """Raise ``session_id_mismatch`` if a live session occupies our worktree under a
    different id. Returns whether a new anomaly was recorded."""
    try:
        scan = sessions.scan(registry_dir=registry_dir, proc_root=proc_root)
    except OSError as exc:
        audit.error("dispatch.mismatch_scan", error=exc, entity_type="work_item", entity_id=item_id)
        return False

    target = Path(worktree_path).resolve()
    for entry in scan.entries:
        if not entry.cwd or entry.session_id == expected:
            continue
        try:
            if Path(entry.cwd).resolve() != target:
                continue
        except OSError:
            continue
        with db.transaction(conn):
            created = db.raise_anomaly(
                conn,
                kind="session_id_mismatch",
                entity_type="work_item",
                entity_id=str(item_id),
                detail={
                    "expected_session_id": expected,
                    "found_session_id": entry.session_id,
                    "pid": entry.pid,
                    "cwd": entry.cwd,
                    "note": (
                        "a live session is running in this item's worktree under an id we "
                        "did not request, so we cannot track or resume it"
                    ),
                },
            )
        return created
    return False


def _fail(
    conn: sqlite3.Connection,
    audit: AuditLog,
    item_id: int,
    reason: str,
    *,
    blocked: bool = False,
    boundaries: Boundaries | None = None,
    config: Config | None = None,
    item: Any = None,
) -> None:
    """The single funnel every dispatch failure passes through.

    The notification lives here rather than at each of the six call sites for the reason
    ``states.transition`` was *rejected* as a hook and this was not: this function is one
    module's gate rather than the whole system's, and its transaction closes before the
    send. One place to get right, and nothing held open while a webhook thinks about it.
    """
    columns: dict[str, Any] = {"failure_reason": reason}
    if blocked:
        columns["blocked_reason"] = reason
    with db.transaction(conn):
        transition_work_item(
            conn,
            audit,
            item_id=item_id,
            target=WorkItemState.FAILED,
            reason=reason,
            extra_columns=columns,
        )
    if boundaries is not None and config is not None:
        notifications.emit(
            boundaries=boundaries,
            audit=audit,
            config=config,
            kind="failure",
            item_id=item_id,
            repo_key=getattr(item, "repo_key", None),
            title=f"robot-army: dispatch failed for item {item_id}",
            detail=reason,
            url=getattr(item, "source_url", None),
        )


def _comment_dispatch(
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    item: Any,
    worktree_path: str,
    branch: str,
    session_id: str,
) -> None:
    body = (
        f"🤖 robot-army dispatched a session for this issue.\n\n"
        f"- Branch: `{branch}`\n"
        f"- Worktree: `{worktree_path}`\n"
        f"- Session: `{session_id}`\n"
    )
    _safe_comment(boundaries, audit, item, body)


def _comment_failure(
    boundaries: Boundaries, audit: AuditLog, config: Config, item: Any, reason: str
) -> None:
    body = f"🤖 robot-army could not start a session for this issue.\n\n```\n{reason}\n```\n"
    _safe_comment(boundaries, audit, item, body)


def _safe_comment(boundaries: Boundaries, audit: AuditLog, item: Any, body: str) -> None:
    """Post a comment; a comment failure must not turn a live session into a failed item.

    The failure is logged rather than swallowed — Principle III forbids the silent
    version of this — but it does not propagate, because the session's fate and GitHub's
    availability are unrelated facts.
    """
    try:
        boundaries.issue_writer.comment(item.repo_key, item.issue_number, body)
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see docstring
        audit.error(
            "github.comment",
            error=exc,
            entity_type="work_item",
            entity_id=item.id,
            detail={"note": "comment failed; the work item's state is unaffected"},
            dry_run=item.dry_run,
        )


#: The hold currently in force, held in process memory (R16).
#:
#: ``dispatch.at_capacity`` used to be written once per pass. At a five-second tick that is
#: 17,280 identical records a day, which does not make the log more reconstructible — it
#: makes it less, by burying the records that carry information under records that carry
#: none. So the record is written when the hold's *signature* changes and once more when it
#: ends, carrying the duration and how many passes it spanned.
#:
#: This is a change of representation, not of content: the hold's existence, cause, counts,
#: start, end, and extent are all still recorded. It is a documented summarisation under
#: Principle III's retention clause, and the same judgement already lives in this codebase
#: as ``raise_anomaly``'s partial unique index.
#:
#: Deliberately volatile. Losing it across a restart costs exactly one extra record, which
#: is far less than a table costs to keep correct.
_HOLD: dict[str, Any] = {}


def _hold_signature(entry: Any, snap: Any) -> tuple[Any, ...]:
    """What makes this hold *the same hold* as the last pass's.

    The counts, the cap, and which item is at the head. Any of those changing is news; none
    of them changing is the same sentence repeated.
    """
    return (snap.total, snap.others, snap.global_cap, entry.item.id)


def _note_hold(audit: AuditLog, entry: Any, snap: Any) -> None:
    signature = _hold_signature(entry, snap)
    if _HOLD.get("signature") == signature:
        _HOLD["passes"] = _HOLD.get("passes", 0) + 1
        return
    _HOLD.clear()
    _HOLD.update(
        signature=signature,
        passes=1,
        started_at=utc_now_iso(),
        reason=str(entry.hold),
    )
    audit.record(
        "dispatch.at_capacity",
        outcome="ok",
        detail={
            "reason": str(entry.hold),
            "detail": entry.detail,
            "live_sessions": snap.total,
            "cap": snap.global_cap,
            "ours": len(snap.ours),
            "others": snap.others,
            "degraded": snap.degraded,
            "held_in_ready": entry.item.id,
        },
    )


def _clear_hold(audit: AuditLog, snap: Any, *, freed_by: str) -> None:
    """Write ``dispatch.hold_ended`` if a hold was in force, and forget it."""
    if not _HOLD:
        return
    started = _HOLD.get("started_at")
    audit.record(
        "dispatch.hold_ended",
        outcome="ok",
        detail={
            "reason": _HOLD.get("reason"),
            "duration_seconds": _elapsed_seconds(started),
            "started_at": started,
            "passes_spanned": _HOLD.get("passes", 0),
            "freed_by": freed_by,
            "live_sessions": snap.total,
            "cap": snap.global_cap,
        },
    )
    _HOLD.clear()


def _elapsed_seconds(started_at: str | None) -> float | None:
    if not started_at:
        return None
    try:
        began = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    return round((datetime.now(UTC) - began).total_seconds(), 3)


def select_and_dispatch(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    layout: Layout,
    trust_file: Path | None = None,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
) -> int:
    """Dispatch as many ``ready`` items as the machine has room for.

    Two things changed in milestone 004, and both are about the cap being honest.

    **The count is of the machine, not of our own bookkeeping.** ``capacity.snapshot``
    counts every live worker session running as this user, the author's own included, so
    the cap protects the subscription rather than merely rationing the daemon against
    itself. Simulated sessions still count (FR-004, and FR-055 before it) — that reasoning
    now lives in ``capacity.snapshot``'s docstring, where the counting does.

    **The order is not this function's to decide.** ``ordering.plan`` produces it, and the
    queue view and ``robot-army status`` render the very same list, which is what makes
    SC-006 structural rather than a claim (R8).

    The selection itself is the whole of FR-012 and FR-020: a *global* condition ends the
    pass, because no later item could fit into a slot this one could not, while a *per-item*
    one skips that item and leaves the rest of the queue moving.

    **The snapshot is taken once per candidate, not once per pass** (FR-009). A plan built
    from a stale snapshot would carry hold reasons computed before the previous dispatch
    took its slot, so a batch could collectively exceed the cap while every individual
    decision looked correct — and the same staleness is what would let two overlapping
    passes each see the same free slot. Re-observing is cheap (a directory listing and a
    handful of ``/proc`` reads) and subtracting one from a remembered number is not
    observing at all.
    """
    dispatched = 0
    # An item leaves ``ready`` the moment ``dispatch_item`` starts — it transitions to
    # ``dispatching`` before anything else — so re-planning always shrinks and this set is
    # belt to that braces. It costs one integer per pass and forecloses a spin.
    attempted: set[int] = set()

    while True:
        snap = capacity.snapshot(
            conn,
            config=config,
            audit=audit,
            registry_dir=registry_dir,
            proc_root=proc_root,
        )
        selected = None
        blocked = None
        for entry in ordering.plan(conn, config=config, capacity=snap):
            if entry.item.id in attempted:
                continue
            if entry.hold in _GLOBAL_HOLDS:
                blocked = entry
                break
            if entry.hold is not None:
                continue
            selected = entry
            break

        if blocked is not None:
            _note_hold(audit, blocked, snap)
            return dispatched
        if selected is None:
            _clear_hold(audit, snap, freed_by="the queue drained")
            return dispatched

        _clear_hold(audit, snap, freed_by=f"item {selected.item.id} became dispatchable")
        attempted.add(selected.item.id)
        if dispatch_item(
            conn,
            boundaries=boundaries,
            audit=audit,
            config=config,
            layout=layout,
            item_id=selected.item.id,
            trust_file=trust_file,
            registry_dir=registry_dir,
            proc_root=proc_root,
        ):
            dispatched += 1
