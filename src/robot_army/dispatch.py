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
import os
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
    repos,
    sessions,
    speckit,
    worktree,
)
from robot_army.audit import utc_now_iso
from robot_army.boundaries import BoundaryError, DisplayHandle, Issue
from robot_army.ordering import HoldReason
from robot_army.paths import claude_trust_file
from robot_army.states import (
    TERMINAL_SESSION_STATES,
    ClaimLost,
    SessionState,
    WorkItemState,
    claim_work_item,
    transition_session,
    transition_work_item,
)

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config, RepoConfig
    from robot_army.effects import Boundaries
    from robot_army.models import Repo
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
#:
#: ``awaiting_merge`` is deliberately absent for exactly that reason (milestone 047, FR-007):
#: a repository waiting for its work to land must leave every other repository free to
#: dispatch in the same pass. Being outside this set does not mean going unrecorded — see
#: ``_HOLD`` below, which since 047 records a pass stopped by a per-item hold too.
_GLOBAL_HOLDS: frozenset[HoldReason] = frozenset(
    {HoldReason.PAUSED, HoldReason.CAPACITY_UNOBSERVABLE, HoldReason.GLOBAL_CAP}
)


class DispatchBlocked(Exception):
    """A precondition failed. Carries a message the maintainer can act on."""


class DispatchRefused(Exception):
    """The launch was not attempted, and the item is untouched (issue #120, RA-05).

    Deliberately **not** a subclass of :class:`DispatchBlocked`, and the distinction is the
    whole reason this class exists rather than a reused one. ``DispatchBlocked`` means the
    item cannot run and is *failed* for it: ``_dispatch_item`` catches it and calls
    ``_fail(..., blocked=True)``, and ``operations.retry`` catches it to refuse a retry.
    Being paused, held, or at the session cap says nothing whatsoever about the item, and
    FR-010 and FR-011 forbid touching it — so subclassing would make this eligible for
    handlers written to fail items, and the bug would read as "the machine was busy, so my
    work item is now failed". That is worse than the gap being closed.

    ``hold`` is ``None`` when the refusal is a lost claim rather than a policy hold: no
    ``HoldReason`` describes "another dispatcher got there first", and inventing one would
    put a queueing vocabulary word in front of a concurrency outcome.
    """

    def __init__(self, detail: str, *, hold: HoldReason | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.hold = hold


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


def check_launch_gate(
    conn: sqlite3.Connection,
    *,
    audit: AuditLog,
    config: Config,
    item: Any,
    surface: str,
    force: bool = False,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
) -> None:
    """Raise ``DispatchRefused`` unless the cap, the pause and the holds all allow it.

    The fix for RA-05 (issue #120). Until this existed the three brakes lived only in
    ``ordering.plan``, which ``select_and_dispatch`` walks and which ``resume`` and
    ``restart`` bypass entirely — so with a cap of two and two sessions running, one press
    of *Resume* started a third, then a fourth, and a held item and a paused system both
    resumed. ``capacity``'s docstring names the stake: an under-count oversubscribes the
    author's own subscription while claiming to protect it. This was not an under-count. It
    was no count.

    The **policy** is not re-implemented here — ``ordering.launch_holds`` is called, the
    same function ``_hold_for`` calls, so the queue view and this gate cannot report
    different reasons or rank them differently. What lives here is the *reading*: a
    capacity snapshot, the pause flag and the two hold tables. ``ordering`` is pure and is
    called on every web page render; this module already launches processes.

    **The snapshot is taken here, on every call, and none may be passed in** (FR-009).
    Between a planner's snapshot and this launch the author can start a session by hand,
    and a remembered number cannot see it — which is the whole reason ``capacity`` counts
    the machine rather than our own bookkeeping. It costs a directory listing and a handful
    of ``/proc`` reads, and only on the path that actually dispatches.

    Called **before** the claim and before every other write, so a refusal leaves the item
    exactly as it was (FR-010, FR-011). That ordering is the requirement, not an
    optimisation: an item failed for the machine being busy would need ``retry`` before the
    author could press the button again, which turns "wait a minute" into "your work item
    is broken".
    """
    holds = ordering.launch_holds(
        item,
        config=config,
        capacity=capacity.snapshot(
            conn,
            config=config,
            audit=audit,
            registry_dir=registry_dir,
            proc_root=proc_root,
        ),
        paused=db.get_dispatch_control(conn).paused,
        item_holds=db.list_item_holds(conn),
        repo_holds=db.list_repo_holds(conn),
    )
    if not holds:
        # Nothing recorded. A permitted launch is the ordinary case, and the dispatch
        # records that follow already say one happened.
        return

    if force:
        # Every condition, not only the first (FR-023). The author who forced past a full
        # machine needs to know they also forced past a hold they had forgotten placing —
        # reporting one and silently passing the rest is how the escape hatch becomes its
        # own surprise.
        audit.record(
            "dispatch.forced",
            outcome="ok",
            entity_type="work_item",
            entity_id=item.id,
            detail={
                "surface": surface,
                "overridden": [
                    {"hold": str(hold), "detail": detail} for hold, detail in holds
                ],
                "note": (
                    "an operator override went past the author's own dispatch policy; the "
                    "author check, workspace trust, the settings fingerprint and the state "
                    "machine are not overridable and still applied"
                ),
            },
            dry_run=bool(getattr(item, "dry_run", False)),
        )
        return

    hold, detail = holds[0]
    audit.record(
        "dispatch.refused",
        outcome="error",
        entity_type="work_item",
        entity_id=item.id,
        detail={
            "surface": surface,
            "hold": str(hold),
            "reason": detail,
            # Not de-duplicated the way ``_note_hold`` de-duplicates the dispatcher's own
            # holds, and deliberately: each of these is an action somebody took, and a
            # button press that leaves no record is the failure this whole change exists to
            # remove. The volume is bounded by how fast a person can press a button.
            "note": "the item was not touched",
        },
        dry_run=bool(getattr(item, "dry_run", False)),
    )
    raise DispatchRefused(detail, hold=hold)


def check_gates(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    config: Config,
    repo: RepoConfig,
    trust_file: Path | None = None,
) -> None:
    """Raise ``DispatchBlocked`` unless onboarding, location, trust, and fingerprint pass.

    Milestone 005 added the second of those. It re-runs the part of onboarding's
    verification that can go stale — the recorded path still exists, is still a primary
    clone, and still normalises to the same repository — because the clone can move, be
    replaced, or be deleted between an approval and a dispatch months later (US5).

    It lives here rather than in ``dispatch_item`` for three reasons that are all about not
    duplicating existing logic (research R9): this function already loads the record the
    check needs, its exception type is already turned into a ``failed`` item with a reason
    by the caller, and every existing precondition of the same kind is already here. Three
    local reads, no fetch, and it runs before anything is created — so a failure creates
    nothing anywhere, which is the entire point (FR-029, SC-004).
    """
    record = db.get_repo(conn, repo.key)
    if record is None:
        raise DispatchBlocked(
            f"repository {repo.key!r} is not onboarded — run `robot-army onboard {repo.key}`"
        )

    _check_recorded_location(
        conn, boundaries=boundaries, config=config, repo=repo, record=record
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


def _check_recorded_location(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    config: Config,
    repo: RepoConfig,
    record: Repo,
) -> None:
    """The fourth precondition (contracts/onboarding.md). Raises, or returns silently.

    Nothing is written on success, deliberately: the worktree-creation record that follows
    on the same item milliseconds later already implies this passed, so a record here would
    be one line per dispatch answering a question the next line answers anyway. Under
    Principle III's reconstruction standard that is not a gap — "did the clone still check
    out?" is answered by the presence of the next record — and it is the only omission this
    milestone makes.
    """
    if record.clone_path is None:
        # A row predating migration 005. Nothing backfills it, and nothing guesses: writing
        # a path nobody approved into an approval record is the one thing that table exists
        # not to do (FR-014, research R6).
        raise DispatchBlocked(
            f"repository {repo.key!r} was onboarded before its clone location was "
            f"recorded — run `robot-army onboard {repo.key} --reapprove`"
        )

    section = config.repos.get(repo.key)
    if section is not None and section.path is not None and str(section.path) != record.clone_path:
        # A changed ``path`` does not silently take effect, and it does not silently lose
        # either. This mirrors how a changed settings fingerprint already behaves (FR-013).
        raise DispatchBlocked(
            f"[repos.{repo.key!r}] path is {section.path}, but {record.clone_path} was "
            f"approved at onboarding. Run `robot-army onboard {repo.key} --reapprove` to "
            "approve the new location"
        )

    recorded = Path(record.clone_path)
    if not recorded.is_dir():
        _raise_location_anomaly(
            conn,
            repo.key,
            kind="clone_path_missing",
            detail={"recorded_path": record.clone_path},
            message=(
                f"the clone approved for {repo.key!r} is no longer at {record.clone_path}. "
                f"Restore it, or run `robot-army onboard {repo.key} --reapprove`"
            ),
        )

    if not repos.is_primary_clone(recorded):
        raise DispatchBlocked(
            f"{record.clone_path} is no longer a primary clone. Restore it, or run "
            f"`robot-army onboard {repo.key} --reapprove`"
        )

    if record.verified_origin is None:
        # Nothing was approved to compare against. This cannot arise from onboarding —
        # verification produces an identity before anything is written — so it means a row
        # written by hand or by an older build. Blocking on it would refuse a repository
        # for a reason the author cannot act on, and the path checks above have already
        # confirmed a primary clone is where it was approved to be.
        return

    remote, _ambiguous = repos.select_remote(boundaries.version_control, recorded)
    found = (
        repos.normalise_remote(boundaries.version_control.remote_url(str(recorded), remote) or "")
        if remote
        else None
    )
    # Compared against what was **recorded**, not against a fresh derivation from the
    # repository key. That is the same discipline the location itself follows: onboarding
    # decided this identity with a human reading it, and a rule re-evaluated here could
    # reach a different answer than the one that was approved.
    if found is None or str(found) != record.verified_origin:
        # Scenario 3's failure arriving months later: a *different* repository cloned into
        # the recorded path. A design that re-derived the location instead of recording it
        # would get this exactly wrong, because the derived answer would still be this
        # directory and this directory now holds someone else's work.
        _raise_location_anomaly(
            conn,
            repo.key,
            kind="clone_origin_changed",
            detail={
                "recorded_path": record.clone_path,
                "approved_origin": record.verified_origin,
                # Normalised, never the raw URL, on this path as on every other (FR-032).
                "found_origin": str(found) if found else None,
            },
            message=(
                f"the clone at {record.clone_path} is "
                f"{found or 'no longer readable as a repository'}, not {repo.key}. "
                f"Run `robot-army onboard {repo.key} --reapprove` once it is right"
            ),
        )


def _raise_location_anomaly(
    conn: sqlite3.Connection,
    repo_key: str,
    *,
    kind: str,
    detail: dict[str, Any],
    message: str,
) -> None:
    """Raise an anomaly **and** ``DispatchBlocked``, in that order.

    Distinct from an ordinary gate refusal because these two mean *the machine changed
    under an approval*, not that a precondition was never met. An un-trusted clone is a
    setup step the author has not done yet; a clone that moved is a fact about the world
    that the author probably does not know, and an anomaly is how this system says so.
    """
    with db.transaction(conn):
        db.raise_anomaly(
            conn,
            kind=kind,
            entity_type="repo",
            entity_id=repo_key,
            detail=detail,
        )
    raise DispatchBlocked(message)


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


def speckit_block(
    *,
    config: Config,
    audit: AuditLog,
    repo_key: str,
    item_id: int | None,
    worktree_path: str,
) -> str | None:
    """The Spec Kit guidance for this dispatch, or ``None`` (milestone 007, FR-005).

    Records the decision either way, with the evidence that produced it and — when the
    behaviour was suppressed — which setting did it. One record per dispatch: the reads
    behind it are four ``stat`` calls and logging each of those would bury the decision
    they support.

    ``item_id`` is ``None`` for ``robot-army prompt``, which composes the same block for an
    issue that may have no work item at all. The record is then keyed on the repository
    rather than on a row that does not exist — writing a sentinel id would put a false
    statement into an append-only log. Dispatch always has a row and always passes one, so
    its records are unchanged.

    **Nothing here may fail a dispatch.** ``speckit.detect`` already promises not to raise,
    and this catches anyway: the cost of a bare handler here is one over-broad ``except``,
    and the cost of being wrong about that promise is a repository that cannot dispatch at
    all because of a paragraph of prose it was going to be sent.
    """
    entity_type = "work_item" if item_id is not None else "repo"
    entity_id: object = item_id if item_id is not None else repo_key
    try:
        detection = speckit.detect(worktree_path)
        enabled, suppressed_by = config.speckit_enabled_for(repo_key)
        # Inside the same ``try`` as everything else here: a configured instruction is
        # prose, and prose must not be able to fail a dispatch any more than a failed
        # ``stat`` can.
        instructions = config.speckit_commands_for(repo_key)
    except Exception as exc:  # noqa: BLE001 - see the docstring; a miss, never a failure
        audit.record(
            "speckit.detect",
            outcome="error",
            entity_type=entity_type,
            entity_id=entity_id,
            target=worktree_path,
            detail={"detected": False, "reason": f"detection failed: {exc}", "enabled": False},
        )
        return None

    detail: dict[str, Any] = {
        "detected": detection.detected,
        "reason": detection.reason,
        "enabled": bool(detection.detected and enabled),
        "path": worktree_path,
    }
    if detection.form:
        detail["form"] = detection.form
    if detection.detected and not enabled and suppressed_by:
        detail["suppressed_by"] = suppressed_by
    if instructions and detail["enabled"]:
        # Gated on the block actually being sent, not merely on configuration existing: a
        # suppressed repository is told nothing, and a record listing instructions it never
        # received would describe a prompt that was not composed. ``suppressed_by`` already
        # says configuration was consulted.
        #
        # Which setting supplied each instruction, never the instruction itself. The log
        # does not reconstruct a composed prompt today — the issue body, the repository's
        # own instructions and the delivery block are all absent from it — and recording
        # up to 16,000 characters of configured prose beside an omitted issue body would
        # privilege this one section for no defensible reason. This is the Principle III
        # gap milestone 039's plan enumerates and justifies; do not close it by adding the
        # text here.
        detail["instructions"] = {i.command: i.source for i in instructions}
    audit.record(
        "speckit.detect",
        outcome="ok",
        entity_type=entity_type,
        entity_id=entity_id,
        target=worktree_path,
        detail=detail,
    )
    if not (detection.detected and enabled):
        return None
    # Configured text lives *inside* the gate rather than beside it: a repository whose
    # block is suppressed receives no instructions either (FR-005, US1 scenario 4).
    return speckit.guidance(instructions)


def build_launch_plan(
    *,
    config: Config,
    layout: Layout,
    boundaries: Boundaries,
    audit: AuditLog,
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
        # A resume is a *new attempt* restoring the prior session's context (FR-047), and
        # --fork-session is what makes that sentence true rather than merely intended.
        # Without it the binary rejects the pair before running anything --- "--session-id
        # can only be used with --continue or --resume if --fork-session is also
        # specified" --- so every resume exited 1 within a second. With it, the forked
        # session carries the prior conversation and runs under the id *we* chose, which is
        # what confirmation, attach, terminate and exit correlation all address it by
        # (milestone 013, contracts/worker-launch-shapes.md G1-G5, measured not assumed).
        worker_argv += ["--resume", resume_session_id, "--fork-session"]
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
    block = speckit_block(
        config=config,
        audit=audit,
        repo_key=repo_key,
        item_id=item_id,
        worktree_path=worktree_path,
    )
    body = prompt.compose(
        issue,
        repo_key=repo_key,
        branch=branch,
        instructions=instructions,
        speckit_block=block,
    )
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
    force: bool = False,
    surface: str = "dispatcher",
) -> bool:
    """Prepare and launch one item. Returns ``True`` when it reached ``active``.

    Failure at any point leaves the item ``failed`` with a reason, never ``active`` and
    never silently stuck. This wrapper is what makes the second half of that sentence
    true for the unforeseen failures as well as the handled ones.

    An exception escaping the launch used to strand the item in ``dispatching``, where it
    reads as "starting up, be patient" until the 15-minute reaper clears it --- a failure
    detected in under three seconds and reported to nobody (milestone 013, FR-008). So the
    item is settled here and the exception is then **re-raised**: settling without
    re-raising would be the swallowing catch-all Principle III forbids, and re-raising
    without settling is the bug. Both, or neither is right.

    ``force`` overrides the author's own dispatch policy --- the cap, the pause, the holds
    --- and nothing else (issue #120). It cannot reach the author check, workspace trust,
    the committed settings fingerprint, or the state machine. ``surface`` names who asked,
    for the record a refusal writes.
    """
    try:
        return _dispatch_item(
            conn,
            boundaries=boundaries,
            audit=audit,
            config=config,
            layout=layout,
            item_id=item_id,
            trust_file=trust_file,
            registry_dir=registry_dir,
            proc_root=proc_root,
            resume_session_id=resume_session_id,
            skip_gates=skip_gates,
            force=force,
            surface=surface,
        )
    except DispatchRefused:
        # Already recorded by ``check_launch_gate``, and it is not an error of the dispatch:
        # the handler below exists for the *unforeseen* failure, and it would file a paused
        # system under ``dispatch.error`` with ``outcome="error"``. Principle III's standard
        # is reconstruction, which a misfiled record defeats as surely as a missing one.
        # There is also nothing to settle --- the gate runs before the claim, so the item is
        # not ``dispatching`` and has not been touched.
        raise
    except Exception as exc:
        # The item is read *before* the record is written, so the record can say which item
        # this was and what is about to happen to it. Only ``dispatching`` is settled here:
        # from anywhere else the transition would itself be illegal, and an exception raised
        # while handling one would bury the failure that actually matters. An item can
        # legitimately be past ``dispatching`` when this runs --- notification, the board
        # update and the transcript check all follow confirmation --- so the note must say
        # which of the two happened rather than assert a settle that never occurred.
        item = db.get_work_item(conn, item_id)
        settling = item is not None and item.state is WorkItemState.DISPATCHING
        audit.error(
            "dispatch.error",
            error=exc,
            entity_type="work_item",
            entity_id=item_id,
            detail={
                "item_state": str(item.state) if item is not None else None,
                "settling": settling,
                "note": (
                    "an exception escaped the launch; the item is being failed before the "
                    "exception is re-raised (the state.work_item record that follows is the "
                    "settle itself)"
                    if settling
                    else "an exception escaped the launch, but the item is not dispatching "
                    "and is left as it stands: this ran after the launch had already been "
                    "confirmed, and nothing here may move an item the state table does not "
                    "allow to be moved"
                ),
            },
            # Without this a crash while dispatching a simulated item is indistinguishable
            # in the log from a crash on a real one (FR-055).
            dry_run=bool(item is not None and item.dry_run),
        )
        if settling:
            _fail(
                conn,
                audit,
                item_id,
                f"dispatch raised {type(exc).__name__}: {exc}",
                boundaries=boundaries,
                config=config,
                item=item,
            )
        raise


def author_refusal(item: Any, config: Config) -> tuple[str, str] | None:
    """``(cause, reason)`` if this item may not dispatch on its author, else ``None``.

    A named function rather than a branch inside ``_dispatch_item`` for the reason
    ``check_gates`` is one: a refusal that decides whether an agent runs in the maintainer's
    checkout should be readable, and testable, without standing up a launch around it.

    ``is None`` is checked first and it is **not** redundant with the inequality after it.
    ``config.parse`` refuses an empty ``[github] author``, so today ``None`` could never
    equal it — but that guarantee lives in another module, and this is the branch where a
    future config change letting the value go missing would silently start dispatching every
    row whose provenance is unknown. Stating it here costs one comparison and depends on
    nothing outside this function.
    """
    if item.author is None:
        return (
            "unrecorded",
            f"work item {item.id} has no recorded issue author, so it cannot be verified "
            f"— run `robot-army retry {item.id}` to re-read the issue and re-check it",
        )
    if item.author != config.github.author:
        return (
            "mismatch",
            f"issue author {item.author!r} is not the configured author "
            f"{config.github.author!r} (FR-007 security boundary; this cannot be disabled)",
        )
    return None


def _claim_or_refuse(
    conn: sqlite3.Connection,
    audit: AuditLog,
    item_id: int,
    *,
    surface: str,
    dry_run: bool,
) -> None:
    """Take the item for launch, or raise ``DispatchRefused`` because somebody else did.

    Atomic, because ``transition_work_item`` is not (issue #120). That function reads the
    state and then writes it, treating "already there" as a legitimate no-op --- which is
    right for reconciliation and for spool replay, and is why it still does. But it also
    made ``dispatching -> dispatching`` succeed silently, so the web worker and a terminal
    command racing on one item could both walk past it and launch **two agents into one
    worktree on one branch**. ``web/server.py`` anticipates an ``IllegalTransition`` from
    "a concurrent terminal command"; for that one pair, none was ever raised.

    A lost claim is reported as a **refusal**, never as a failure. The winner owns the item
    now, and settling it here would let the loser fail work it never claimed --- the same
    class of bug as the double dispatch, wearing the opposite sign.
    """
    try:
        with db.transaction(conn):
            claim_work_item(
                conn,
                audit,
                item_id=item_id,
                target=WorkItemState.DISPATCHING,
                reason="dispatcher selected it and capacity exists",
            )
    except ClaimLost as exc:
        audit.record(
            "dispatch.refused",
            outcome="error",
            entity_type="work_item",
            entity_id=item_id,
            detail={
                "surface": surface,
                "hold": None,
                "reason": str(exc),
                "found_state": str(exc.found) if exc.found is not None else None,
                "note": "the item was not touched; another claimant holds it",
            },
            dry_run=dry_run,
        )
        raise DispatchRefused(str(exc)) from exc


def _dispatch_item(
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
    force: bool = False,
    surface: str = "dispatcher",
) -> bool:
    """The launch itself. Always called through ``dispatch_item``, never directly."""
    item = db.get_work_item(conn, item_id)
    if item is None:
        raise LookupError(f"no work item {item_id}")
    dry_run = item.dry_run
    repo = repos.resolve(conn, config, item.repo_key)
    if repo is None:
        _fail(
            conn,
            audit,
            item_id,
            f"repository {item.repo_key!r} is no longer onboarded, or was onboarded "
            "before its location was recorded — run "
            f"`robot-army onboard {item.repo_key} --reapprove`",
            boundaries=boundaries,
            config=config,
            item=item,
        )
        return False

    # -- the cap, the pause and the holds (issue #120, RA-05) --------------
    #
    # First, and before any write. Every path that starts a session comes through here, so
    # this is where "the dispatcher honours the cap" becomes "the system honours the cap"
    # --- ``resume`` and ``restart`` used to reach the launch below without meeting any of
    # the three.
    #
    # Placed **above** the author check on purpose, even though that check is the more
    # serious one. A refusal here says nothing about the item and writes nothing; the
    # author check *fails* the item and stores a reason. An item that is held, on a paused
    # machine, and written by somebody else should be refused rather than failed --- and
    # refusing first also means an attempt the author never authorised does not get to
    # write a ``blocked_reason`` while the system is paused.
    #
    # Deliberately outside ``skip_gates``, which is about trust and the fingerprint. Two
    # different questions, and one flag answering both would be how the cap got lost again.
    check_launch_gate(
        conn,
        audit=audit,
        config=config,
        item=item,
        surface=surface,
        force=force,
        registry_dir=registry_dir,
        proc_root=proc_root,
    )

    _claim_or_refuse(conn, audit, item_id, surface=surface, dry_run=dry_run)

    # -- the author (issue #119, FR-014, FR-015) ---------------------------
    #
    # Deliberately *outside* the ``skip_gates`` block below. No caller passes that flag
    # today, so this changes nothing now — but a check whose whole documented character is
    # "this cannot be disabled" must not sit under a flag named *skip gates*, in the file
    # where the next reader is most likely to trust the surrounding structure (research R8).
    # Placing it here also covers ``resume`` and ``restart``, which reach the launch through
    # this same function.
    #
    # This is defence in depth, not the fix: ``operations.retry`` is where the live check
    # happens. What it replaces is worse than nothing, though --- the launch used to build
    # its ``Issue`` with ``author=config.github.author``, asserting a fact it had never
    # read, which made the code *read* as though a check happened downstream and removed the
    # last natural place to notice that none did.
    refusal = author_refusal(item, config)
    if refusal is not None:
        cause, reason = refusal
        audit.record(
            "dispatch.author",
            outcome="error",
            entity_type="work_item",
            entity_id=item_id,
            detail={
                "recorded_author": item.author,
                "configured_author": config.github.author,
                "cause": cause,
            },
            dry_run=dry_run,
        )
        _fail(
            conn,
            audit,
            item_id,
            reason,
            blocked=True,
            boundaries=boundaries,
            config=config,
            item=item,
        )
        return False

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
            _comment_failure(boundaries, audit, item, str(exc))
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
                # Same transaction as the path it describes, deliberately: a baseline that
                # committed separately could survive a worktree that did not, and would
                # then describe a directory that was never created (milestone 007).
                speckit_baseline=(
                    json.dumps(list(preparation.speckit_baseline))
                    if preparation.speckit_baseline is not None
                    else None
                ),
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
                boundaries, audit, item, preparation.failure_reason or "preparation failed"
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
        # The author this item's issue actually had, recorded when the issue was read
        # (issue #119). It is provably equal to ``config.github.author`` by the time we get
        # here — but equal because it was *compared* above, not because it was assigned.
        author=item.author,
        state="open",
    )
    session_id = str(uuid.uuid4())
    plan = build_launch_plan(
        config=config,
        layout=layout,
        boundaries=boundaries,
        audit=audit,
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
        _comment_failure(boundaries, audit, item, reason)
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
        _comment_failure(boundaries, audit, item, reason)
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

        # Ask the session what it knows before concluding anything about it. The exit
        # spool is drained by the daemon, in its own process, while this call was
        # waiting: a worker that died fast has already recorded its own ending, and
        # overwriting that with LOST is both a contradiction the state gate rejects and a
        # loss of the most useful fact in the record. `reconcile` asks the same question
        # at the equivalent moment and for the same reason (milestone 013, research R3).
        recorded = db.get_session(conn, session_id)
        already = recorded.state if recorded else None

        if already in TERMINAL_SESSION_STATES:
            outcome = "already_exited"
            reason = _exited_before_confirmation(already, recorded, config)
            # No mismatch scan: the question that scan answers --- did something else
            # start under a different id? --- is already answered by this session having
            # reported its own ending. Probing would hunt a rival that cannot exist.
        else:
            outcome = "lost"
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
                # Which of the two stories this was. "Never appeared" and "already exited"
                # call for different next steps, so the log must not blur them (FR-010).
                "session_state": str(already) if already else None,
                "outcome": outcome,
                "launch_argv": plan.argv,
                "window_id": display_handle.window_id,
                "window_state": window_state,
            },
            dry_run=dry_run,
        )
        _fail(conn, audit, item_id, reason, boundaries=boundaries, config=config, item=item)
        _comment_failure(boundaries, audit, item, reason)
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

    # Which session this attempt replaces, resolved once and used by both the record below
    # and the comment at the end of this function, so the log and the issue cannot disagree.
    #
    # A resume already names what it restored, which is the fact worth reporting. Only a
    # restart has to look, and it must **not** use `latest_session_for_item`: the session row
    # for this attempt was inserted before the launch, so that function returns *our own
    # row* and the comment would say a session supersedes itself.
    previous_session_id: str | None = resume_session_id
    if previous_session_id is None and attempt > 1:
        earlier = db.previous_session_for_item(conn, item_id, attempt)
        previous_session_id = earlier.session_id if earlier else None

    host = host_name()
    audit.record(
        "dispatch.confirmed",
        outcome="ok",
        entity_type="work_item",
        entity_id=item_id,
        detail={
            "session_id": session_id,
            # The two handles the maintainer searches with, and the machine they mean
            # something on. Recorded because the standard is reconstruction from the log
            # alone, and "which host" is exactly the question a log cannot answer by
            # sitting on the host that wrote it (FR-002).
            "session_name": plan.title,
            "host": host,
            "attempt": attempt,
            # Which session's context this attempt restored, or absent when it restored
            # none. It is inside launch_argv too, but that is the whole nested wrapper
            # argv with the prompt body in it, and "what did this resume?" should not
            # cost a parse of that to answer from the log (FR-002).
            **({"resumed_from": resume_session_id} if resume_session_id else {}),
            # The predecessor a restart replaced without restoring. Distinct from
            # `resumed_from` because the difference — whether the prior conversation came
            # with it — is the whole reason to know there was one.
            **(
                {"supersedes": previous_session_id}
                if previous_session_id and not resume_session_id
                else {}
            ),
            "pid": entry.pid,
            "scope": scope,
            "window_id": display_handle.window_id,
            "socket": plan.socket_path,
            "worktree": worktree_path,
            "branch": branch,
        },
        dry_run=dry_run,
    )

    # The transcript check used to sit here, one line after confirmation. It is gone, and
    # nothing replaces it (issue #58): the worker writes its transcript when it begins
    # processing, not at exec, so asking at this moment reliably got "no" about a perfectly
    # healthy session and raised `no_transcript` on **every** dispatch. The question is
    # still asked -- by `reconcile._sweep_transcripts`, which can afford to wait, and which
    # leaves the answer on the session row rather than in this pass.
    #
    # Do not reinstate a check here. There is no point in this function at which a missing
    # transcript means anything, because the process has had no time to write one.

    # Last, and deliberately: everything above has already established that a session is
    # really running. A comment saying so is only true because of where this line sits.
    _comment_dispatch(
        boundaries,
        audit,
        item,
        session_name=plan.title,
        session_id=session_id,
        worktree_path=worktree_path,
        branch=branch,
        attempt=attempt,
        previous_session_id=previous_session_id,
        resumed=resume_session_id is not None,
    )
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


def _exited_before_confirmation(
    state: SessionState, session: Any, config: Config
) -> str:
    """Why a launch failed when the session ended before confirmation could see it.

    Every one of these settles the item as ``failed``, whatever the exit code says in
    isolation. ``classify_exit`` would send a clean exit to ``awaiting_review`` and a
    signalled one to ``interrupted``, but neither is reachable from ``dispatching`` and
    neither would be true: a worker that ended before it ever registered did not do the
    work, and putting an untouched item in the review queue would be a lie the maintainer
    acts on. The exit code still goes in the reason, because it is what says *why*.
    """
    window = config.daemon.confirm_timeout_seconds
    if state is SessionState.LOST:
        return (
            f"the session was already recorded lost before the {window}s confirmation "
            "window elapsed"
        )
    code = getattr(session, "exit_code", None)
    signal = getattr(session, "signal", None)
    detail = f"the worker exited {code}" if code is not None else "the worker exited"
    if signal is not None:
        detail += f" (signal {signal})"
    return (
        f"{detail} before the launch could be confirmed, inside the {window}s "
        "confirmation window. A session that ends this quickly never started the work"
    )


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


def host_name() -> str:
    """This machine, as the kernel names it — or ``unknown`` when it will not say.

    ``os.uname().nodename`` is already what the health signal and every notification
    publish, so the comment agrees with them rather than inventing a third answer for the
    same question. Deliberately **not** ``socket.getfqdn()``: that is a network lookup, it
    can block, and Principle IV forbids the indefinite version of that for a fact the
    kernel already holds.

    The fallback exists because the two things an empty ``nodename`` would otherwise
    produce are both worse than saying so — a line reading ``Host:`` with nothing after it,
    or no line at all — and each leaves the reader believing the system knows something it
    does not. The word ``unknown`` is itself the record: it reaches the issue *and* the
    ``dispatch.confirmed`` detail, so a machine that cannot name itself is visible in both
    places rather than silently absent from one.
    """
    try:
        name = os.uname().nodename
    except OSError:
        return "unknown"
    return name or "unknown"


def dispatch_comment_body(
    *,
    host: str,
    session_name: str,
    session_id: str,
    branch: str,
    worktree_path: str,
    attempt: int = 1,
    previous_session_id: str | None = None,
    resumed: bool = False,
) -> str:
    """What gets written on the issue when a session is confirmed.

    Pure, and taking facts rather than a database handle, because everything interesting
    here is a rule about a string: which of two openings, which of three predecessor
    lines, and what an unknown host renders as. Testing those through a dispatch would mean
    a worktree, a git binary and a stub host for each case.

    ``attempt`` is what distinguishes the two variants, and it needs no query: the session
    row's attempt number is already a local at the only call site, assigned by
    ``db.next_attempt`` before the launch.
    """
    opening = (
        f"🤖 robot-army reassigned this issue to a new session (attempt {attempt})."
        if attempt > 1
        # The attempt number is stated for a reassignment and omitted for a first
        # dispatch: on an issue carrying several of these, the ordering is the fact a
        # reader needs and comparing UUIDs is not a way to get it.
        else "🤖 robot-army dispatched a session for this issue."
    )
    lines = [
        f"- Host: `{host}`",
        f"- Session: `{session_name}`",
        f"- Session id: `{session_id}`",
    ]
    if attempt > 1:
        lines.append(_predecessor_line(previous_session_id, resumed=resumed))
    lines += [f"- Branch: `{branch}`", f"- Worktree: `{worktree_path}`"]
    return opening + "\n\n" + "\n".join(lines) + "\n"


def _predecessor_line(previous_session_id: str | None, *, resumed: bool) -> str:
    """The one line that says what this attempt replaces, and whether it kept anything.

    "Continues" and "supersedes" are not decoration. A resumed session carries the prior
    conversation and a restarted one does not, and that is the difference between reading
    the earlier session's transcript for context and reading it for a fact that no longer
    applies.
    """
    if previous_session_id is None:
        # Reachable when the database was rebuilt or history was pruned. Naming no
        # predecessor is the honest answer; inventing one would be worse than the silence
        # this feature exists to end.
        return "- Supersedes: no earlier session is on record"
    if resumed:
        return f"- Continues: `{previous_session_id}` (that session's context was restored)"
    return (
        f"- Supersedes: `{previous_session_id}` "
        "(this session starts without that session's context)"
    )


def failure_comment_body(*, host: str, reason: str) -> str:
    """The comment for an attempt that never reached a session.

    The host is here for the same reason it is on the dispatch comment, and one more: a
    failure that happens on one machine and not another is the kind this line makes
    attributable in a glance. The reason is fenced because it is machine text of unbounded
    shape — a hook's stderr, an exception, a git error.
    """
    return (
        "🤖 robot-army could not start a session for this issue.\n\n"
        f"- Host: `{host}`\n\n"
        f"```\n{reason}\n```\n"
    )


def _comment_dispatch(
    boundaries: Boundaries,
    audit: AuditLog,
    item: Any,
    *,
    session_name: str,
    session_id: str,
    worktree_path: str,
    branch: str,
    attempt: int,
    previous_session_id: str | None,
    resumed: bool,
) -> None:
    _safe_comment(
        boundaries,
        audit,
        item,
        dispatch_comment_body(
            host=host_name(),
            session_name=session_name,
            session_id=session_id,
            branch=branch,
            worktree_path=worktree_path,
            attempt=attempt,
            previous_session_id=previous_session_id,
            resumed=resumed,
        ),
    )


def _comment_failure(boundaries: Boundaries, audit: AuditLog, item: Any, reason: str) -> None:
    _safe_comment(boundaries, audit, item, failure_comment_body(host=host_name(), reason=reason))


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
#: Records **any** hold that stops a pass, not only the three global ones.
#:
#: Until milestone 047 this mechanism fired only when a ``_GLOBAL_HOLDS`` reason broke the
#: loop, which meant ``repo_cap`` — a reason that has existed since milestone 004 — never
#: appeared in the log at all. The wait-for-merge gate is per-item for the same reason
#: ``repo_cap`` is (FR-007: one waiting repository must not stall the others), so making it
#: loggable by promoting it to a global hold was never available. Widening the recorder was,
#: and it covers ``repo_cap`` on the way past.
#:
#: The single slot and its signature are what keep this affordable at a five-second tick.
#: plan.md enumerates and justifies the resulting Principle III gap: a hold is recorded when
#: it *starts* and when it *ends*, not once per pass for as long as it lasts.


def _hold_signature(entry: Any, snap: Any) -> tuple[Any, ...]:
    """What makes this hold *the same hold* as the last pass's.

    The counts, the cap, which item is at the head — and, since milestone 047, *why* it is
    held and in which repository. Any of those changing is news; none of them changing is
    the same sentence repeated.

    The reason had to join the signature once per-item holds started being recorded. A
    repository whose ``repo_cap`` hold gives way to an ``awaiting_merge`` hold has changed
    what the author must do about it, and under the old signature — same item, same
    machine-wide counts — that change would have been swallowed as a repeat.
    """
    return (
        snap.total,
        snap.others,
        snap.global_cap,
        entry.item.id,
        str(entry.hold),
        entry.item.repo_key,
    )


#: The "repository" of a hold that is not about a repository. See :func:`_hold_identity`.
MACHINE = "<machine>"


def _hold_identity(entry: Any) -> tuple[str, str]:
    """Which hold this *is*, as distinct from what its numbers were.

    ``(reason, repository)``, and it had to become a second concept the moment per-item
    holds started being recorded. The *signature* answers "has anything about this hold
    changed?" and is what suppresses a repeat. The identity answers "is this the same hold
    at all?" and is what decides whether one has **ended**.

    Before per-item holds, the two questions collapsed: only a ``_GLOBAL_HOLDS`` reason
    could be recorded, a pass carrying one returned before dispatching anything, and so "a
    dispatch happened" was proof that the recorded hold was over. That is no longer true. A
    repository waiting for its work to land stays held while an item in a *different*
    repository dispatches in the same pass — which is the entire point of the hold being
    per-item — so a dispatch is evidence about the item that moved and about nothing else.

    **A global hold carries no repository**, and that asymmetry is the whole reason this
    returns a pair rather than a reason. A paused system, an unobservable capacity and a
    full machine are facts about the *machine*; the entry carrying one is merely whichever
    item happened to be at the head of the queue, and that head shifts whenever an item is
    abandoned or the order changes. Keying on its repository would claim that one
    uninterrupted condition had *ended* every time the head moved, and blame the ending on
    a repository that freed nothing.

    What it would **not** have caused, and what this therefore does not fix, is the
    ``started_at`` reset: a head shift changes the *signature* too, and ``_note_hold`` has
    always reopened its slot on a signature change. That is pre-existing and deliberate —
    ``test_a_changed_hold_signature_is_recorded_again`` pins it — so a long global hold
    still reports as several ``dispatch.at_capacity`` records with their own durations.
    They are simply no longer punctuated by ``hold_ended`` records asserting something that
    did not happen.
    """
    if entry.hold in _GLOBAL_HOLDS:
        return (str(entry.hold), MACHINE)
    return (str(entry.hold), entry.item.repo_key)


def _note_hold(audit: AuditLog, entry: Any, snap: Any) -> None:
    signature = _hold_signature(entry, snap)
    if _HOLD.get("signature") == signature:
        _HOLD["passes"] = _HOLD.get("passes", 0) + 1
        return
    identity = _hold_identity(entry)
    if _HOLD and _HOLD.get("identity") != identity:
        # A *different* condition is holding now — the previous one ended, whether or not
        # the queue moved. Without this the log would only ever open holds and leave the
        # reader to infer each closing from the next opening.
        took_over = (
            identity[0]
            if identity[1] == MACHINE
            else f"{identity[0]} in {identity[1]}"
        )
        _clear_hold(audit, snap, freed_by=f"{took_over} took over")
    _HOLD.clear()
    _HOLD.update(
        signature=signature,
        identity=identity,
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
            "repo_key": entry.item.repo_key,
            "live_sessions": snap.total,
            "cap": snap.global_cap,
            "ours": len(snap.ours),
            "others": snap.others,
            "degraded": snap.degraded,
            "held_in_ready": entry.item.id,
        },
    )


def _resolve_hold(audit: AuditLog, entries: list[Any], snap: Any, *, selected: Any) -> None:
    """Close the recorded hold **if it is genuinely over**, and leave it alone if it is not.

    The check that replaces "a dispatch happened, therefore the hold ended". That inference
    was sound while only global holds were recorded and is not any more: since milestone
    047 a hold on one repository coexists with a dispatch in another, and clearing on that
    evidence would write a ``hold_ended`` for a repository still waiting, attribute the
    ending to an unrelated item, and restart the duration from zero on the next quiet pass.

    So the question is asked of the plan rather than of the dispatch: is anything in this
    pass still held by the *same* condition? The plan is the authority on that, and it has
    already been computed.
    """
    if not _HOLD:
        return
    identity = _HOLD.get("identity")
    if identity is not None and any(
        entry.hold is not None and _hold_identity(entry) == identity for entry in entries
    ):
        return
    if not entries:
        freed_by = "the queue drained"
    elif selected is not None:
        freed_by = f"item {selected.item.id} became dispatchable"
    else:
        # Every candidate is still held, but not by the condition that was recorded — the
        # held item was abandoned, say, and a different repository's condition is now the
        # one in force. ``_note_hold`` will open that one on the pass it stops.
        freed_by = "the recorded condition no longer applies"
    _clear_hold(audit, snap, freed_by=freed_by)


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
        # Materialised, because the terminal handling below asks the plan a second
        # question — whether the *recorded* hold is still in force — and re-planning to
        # answer it would ask a snapshot that has since moved on.
        entries = [
            entry
            for entry in ordering.plan(conn, config=config, capacity=snap)
            if entry.item.id not in attempted
        ]
        selected = None
        blocked = None
        # The first *per-item* hold seen this pass, kept only so that a pass which
        # dispatches nothing can say what stopped it. A global hold, when there is one,
        # always wins the report: it is the reason nothing else was even considered.
        first_held = None
        for entry in entries:
            if entry.hold in _GLOBAL_HOLDS:
                blocked = entry
                break
            if entry.hold is not None:
                if first_held is None:
                    first_held = entry
                continue
            selected = entry
            break

        if blocked is not None:
            _note_hold(audit, blocked, snap)
            return dispatched
        if selected is None:
            # Two different silences, and collapsing them would leave the log unable to tell
            # an idle machine from a stalled one. Nothing eligible at all is the queue
            # draining; everything eligible held is a hold, recorded as one even though it
            # skipped items rather than breaking the pass (R5, FR-015).
            #
            # ``dispatched == 0`` because a pass that started something and then ran out of
            # candidates has not stalled — it has done its work and stopped, and calling that
            # a hold would put a record in the log every time the machine filled up normally.
            if first_held is not None and dispatched == 0:
                _note_hold(audit, first_held, snap)
            else:
                _resolve_hold(audit, entries, snap, selected=None)
            return dispatched

        _resolve_hold(audit, entries, snap, selected=selected)
        attempted.add(selected.item.id)
        try:
            launched = dispatch_item(
                conn,
                boundaries=boundaries,
                audit=audit,
                config=config,
                layout=layout,
                item_id=selected.item.id,
                trust_file=trust_file,
                registry_dir=registry_dir,
                proc_root=proc_root,
            )
        except DispatchRefused:
            # The plan and the gate disagreed, and that is a *legitimate* outcome rather
            # than a contradiction to assert against. Both observe the machine, but not at
            # the same instant: between this pass's snapshot and the launch's own the
            # author can start a session by hand, and the second observation is the one
            # that counts (FR-009). ``check_launch_gate`` has already recorded why.
            #
            # The pass ends rather than moving to the next candidate. Every reason the gate
            # can give is either machine-wide or, for ``repo_cap``, one this queue is
            # already ordered by --- so continuing would mean re-planning against a snapshot
            # this pass has been told is stale. The next tick is five seconds away.
            return dispatched
        if launched:
            dispatched += 1
