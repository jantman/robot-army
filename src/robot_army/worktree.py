"""Worktree lifecycle: naming, creation, preparation, and the derived condition.

The orchestration rule that matters most: **a session is never launched into a partially
prepared worktree** (FR-014). Any preparation step that times out or exits non-zero fails
the work item with the captured output, and dispatch stops there. A half-prepared worktree
that gets a session is worse than no session at all, because the failure then surfaces as
confusing behaviour inside a real session rather than as a clear item failure.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from robot_army.boundaries import BoundaryError, HookResult, VersionControl, WorktreeHandle
from robot_army.prompt import branch_name, worktree_dir

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config, RepoConfig
    from robot_army.effects import Boundaries


@dataclass(frozen=True, slots=True)
class PreparationResult:
    ok: bool
    worktree_path: str
    branch: str
    output: str = ""
    failure_reason: str | None = None
    env: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class WorktreeCondition:
    """Derived on demand, never stored — a stored copy would be wrong the moment the
    maintainer touched the directory (data-model.md)."""

    path: str
    exists: bool
    dirty: bool
    prunable: bool
    commits_ahead: int
    status: str = ""

    @property
    def label(self) -> str:
        if self.prunable or not self.exists:
            return "missing"
        return "dirty" if self.dirty else "present"


def plan_paths(config: Config, repo_key: str, issue_number: int, title: str) -> tuple[Path, str]:
    """Where this item's worktree goes and what its branch is called (R18)."""
    path = worktree_dir(config.worktree_root, repo_key, issue_number)
    branch = branch_name(config.worker.branch_prefix, issue_number, title)
    return path, branch


def allocate_port() -> int:
    """Bind port 0, read what the kernel gave us, release it.

    The mechanism M0 E6.6 identified for per-worktree port assignment. It is inherently
    racy — nothing stops another process taking the port between our close and the
    session's bind — but the alternative is a registry of assigned ports that goes stale
    the moment a session dies uncleanly, which is worse.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def resolve_env(repo: RepoConfig, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve the repo's ``env`` table, expanding ``"auto"`` into a free port."""
    resolved: dict[str, str] = {}
    for key, value in repo.env.items():
        resolved[key] = str(allocate_port()) if value == "auto" else value
    if extra:
        resolved.update(extra)
    return resolved


def prepare(
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    repo: RepoConfig,
    item_id: int,
    issue_number: int,
    title: str,
    dry_run: bool,
) -> PreparationResult:
    """Fetch, branch, add the worktree, and run every preparation step in order.

    Every failure path returns ``ok=False`` with output attached rather than raising,
    because the caller's job is to fail the work item with a reason the maintainer can
    act on — and a traceback is not that.
    """
    vcs: VersionControl = boundaries.version_control
    path, branch = plan_paths(config, repo.key, issue_number, title)
    clone = str(repo.path)
    base_ref = repo.base_branch or config.worker.base_branch

    with audit.action(
        "worktree.prepare",
        entity_type="work_item",
        entity_id=item_id,
        target=str(path),
        detail={"branch": branch, "base_ref": base_ref, "clone": clone},
        dry_run=dry_run,
    ) as outcome:
        remote: str | None = None
        try:
            remote = vcs.default_remote(clone)
            if remote is None:
                # A local-only repository. Skipping is correct, but it is *recorded*
                # rather than silent: "there was nothing to fetch" and "the fetch was
                # skipped" must be distinguishable later.
                outcome["fetch_skipped"] = "the repository has no configured remote"
            else:
                vcs.fetch(clone, remote, base_ref)
        except Exception as exc:  # noqa: BLE001 - any boundary failure fails the item, with its reason
            outcome["stage"] = "fetch"
            return PreparationResult(
                ok=False,
                worktree_path=str(path),
                branch=branch,
                output=str(exc),
                failure_reason=f"git fetch failed for {repo.key}: {exc}",
            )

        # Prefer the remote-tracking ref: the primary clone's local base branch may be
        # behind, and a worktree created from a stale base is a subtle, silent problem.
        start_point = base_ref
        if remote is not None:
            try:
                if vcs.rev_parse(clone, f"{remote}/{base_ref}"):
                    start_point = f"{remote}/{base_ref}"
            except Exception:  # noqa: BLE001 - a missing remote ref is not fatal
                start_point = base_ref

        handle: WorktreeHandle
        try:
            handle = vcs.add_worktree(clone, str(path), branch, start_point)
        except Exception as exc:  # noqa: BLE001 - see above
            outcome["stage"] = "add_worktree"
            return PreparationResult(
                ok=False,
                worktree_path=str(path),
                branch=branch,
                output=str(exc),
                failure_reason=(
                    f"could not create worktree at {path} on branch {branch}: {exc}. "
                    "A leftover branch or directory from a previous attempt is the usual "
                    "cause; `robot-army worktree list` will show it"
                ),
            )

        env = resolve_env(repo)
        if env:
            outcome["env_keys"] = sorted(env)

        result: HookResult = boundaries.hook_runner.run(
            repo.post_create, handle.path, clone, env
        )
        outcome["steps"] = len(repo.post_create)
        if not result.ok:
            outcome["failed_step"] = result.step_index
            outcome["timed_out"] = result.timed_out
            reason = (
                f"preparation step {result.step_index} ({result.description}) "
                + ("timed out" if result.timed_out else "failed")
            )
            return PreparationResult(
                ok=False,
                worktree_path=handle.path,
                branch=handle.branch,
                output=result.output,
                failure_reason=reason,
                env=env,
            )

        return PreparationResult(
            ok=True, worktree_path=handle.path, branch=handle.branch, env=env
        )


def condition(
    vcs: VersionControl, clone_path: str, worktree_path: str, branch: str, base_ref: str
) -> WorktreeCondition:
    """Compute a worktree's condition from git, right now."""
    path = Path(worktree_path)
    prunable = False
    try:
        for info in vcs.list_worktrees(clone_path):
            if Path(info.path) == path:
                prunable = info.prunable
                break
    except BoundaryError:
        prunable = not path.exists()

    exists = path.is_dir()
    status = ""
    if exists:
        try:
            status = vcs.status_porcelain(worktree_path)
        except BoundaryError:
            status = ""
    try:
        # ``None`` means git could not answer (R11). For a *resume signal* shown to a human
        # that is the same thing as "nothing to tell you", so it maps to zero here — and it
        # is mapped rather than inherited, because the branch-deletion caller must read the
        # identical value as "unproven, keep the branch".
        ahead = vcs.commits_ahead(clone_path, base_ref, branch) or 0
    except BoundaryError:
        ahead = 0

    return WorktreeCondition(
        path=worktree_path,
        exists=exists,
        dirty=bool(status.strip()),
        prunable=prunable or not exists,
        commits_ahead=ahead,
        status=status,
    )


def directory_size(path: str | Path) -> int:
    """Bytes under a directory. Reported by ``worktree list`` — M0 measured 499 MB for
    one prepared worktree, which is why disk is a real constraint here."""
    total = 0
    root = Path(path)
    if not root.is_dir():
        return 0
    for entry in root.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            continue  # a file vanishing mid-walk is normal, not an error
    return total


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
