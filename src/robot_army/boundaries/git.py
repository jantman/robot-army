"""Version control, via the ``git`` binary.

Three details here are measured M0 findings that a well-meaning later edit would undo.
Each is commented at the point it matters:

* ``remove_worktree`` **never passes ``--force`` on its own.** Git refuses to remove a
  dirty worktree — including one with merely untracked files — and that refusal is the
  free guard FR-016 relies on (M0 E6.5).
* Removing a worktree does **not** remove its branch. Two steps; a caller that does only
  the first accumulates ``robot-army/*`` branches forever.
* Every call is timeout-bounded. ``git fetch`` against an unreachable remote hangs
  otherwise, and a hang is worse than a failure because nothing observes it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from robot_army.boundaries import (
    BoundaryError,
    RemovalResult,
    WorktreeHandle,
    WorktreeInfo,
)
from robot_army.subproc import run

if TYPE_CHECKING:
    from robot_army.audit import AuditLog

#: Generous enough for a large fetch, short enough that an unreachable remote surfaces
#: as a failure within a tick or two rather than wedging the loop.
FETCH_TIMEOUT = 300.0
QUICK_TIMEOUT = 30.0


class GitVersionControl:
    def __init__(self, audit: AuditLog, *, binary: str = "git") -> None:
        self._audit = audit
        self._git = binary

    def _run(
        self,
        args: list[str],
        *,
        cwd: str | None,
        timeout: float,
        action: str,
        check: bool = True,
    ) -> Any:
        return run(
            [self._git, *args],
            cwd=cwd,
            timeout=timeout,
            audit=self._audit,
            action=action,
            check=check,
        )

    # -- fetch / worktree lifecycle ----------------------------------------

    def fetch(self, clone_path: str, remote: str, ref: str) -> None:
        with self._audit.action(
            "git.fetch", target=f"{clone_path}:{remote}/{ref}", detail={"ref": ref}
        ):
            self._run(
                ["fetch", "--prune", remote, ref],
                cwd=clone_path,
                timeout=FETCH_TIMEOUT,
                action="git.subprocess",
            )

    def add_worktree(
        self, clone_path: str, worktree_path: str, branch: str, base_ref: str
    ) -> WorktreeHandle:
        """Create the worktree on a new branch from ``base_ref`` (FR-012).

        ``-b`` fails if the branch already exists, which is the behaviour we want: a
        collision means a previous attempt left state behind, and silently reusing it
        would launch a session into a worktree whose contents we cannot vouch for.
        """
        with self._audit.action(
            "git.add_worktree",
            target=worktree_path,
            detail={"branch": branch, "base_ref": base_ref, "clone": clone_path},
        ):
            Path(worktree_path).parent.mkdir(parents=True, exist_ok=True)
            self._run(
                ["worktree", "add", "-b", branch, worktree_path, base_ref],
                cwd=clone_path,
                timeout=FETCH_TIMEOUT,
                action="git.subprocess",
            )
        return WorktreeHandle(path=worktree_path, branch=branch)

    def remove_worktree(
        self, worktree_path: str, force: bool = False, clone_path: str | None = None
    ) -> RemovalResult:
        """Remove a worktree. ``force`` is never defaulted on — see the module docstring.

        Returns rather than raises on git's refusal, because the refusal is an expected
        and *useful* outcome the caller reports to the maintainer.

        ``clone_path`` is not decoration: ``git worktree remove`` resolves the repository
        from its working directory, so run from anywhere else it reports "is not a
        working tree" and the removal silently does nothing. The worktree itself is the
        fallback, but it is only usable while the directory still exists — which is
        exactly not the case for a prunable one.
        """
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(worktree_path)
        cwd = clone_path or (worktree_path if Path(worktree_path).is_dir() else None)
        with self._audit.action(
            "git.remove_worktree", target=worktree_path, detail={"force": force, "cwd": cwd}
        ) as outcome:
            result = self._run(
                args, cwd=cwd, timeout=QUICK_TIMEOUT, action="git.subprocess", check=False
            )
            outcome["exit"] = result.returncode
            if not result.ok:
                outcome["refused"] = result.output
                return RemovalResult(
                    worktree_removed=False,
                    branch_deleted=False,
                    refused_reason=result.output or f"git exited {result.returncode}",
                    output=result.output,
                )
            return RemovalResult(worktree_removed=True, branch_deleted=False, output=result.output)

    def delete_branch(self, clone_path: str, branch: str, force: bool = False) -> bool:
        """The second half of removal. Skipping it accumulates branches forever."""
        with self._audit.action(
            "git.delete_branch", target=branch, detail={"clone": clone_path, "force": force}
        ) as outcome:
            result = self._run(
                ["branch", "-D" if force else "-d", branch],
                cwd=clone_path,
                timeout=QUICK_TIMEOUT,
                action="git.subprocess",
                check=False,
            )
            outcome["exit"] = result.returncode
            if not result.ok:
                outcome["output"] = result.output
            return result.ok

    def list_worktrees(self, clone_path: str) -> list[WorktreeInfo]:
        result = self._run(
            ["worktree", "list", "--porcelain"],
            cwd=clone_path,
            timeout=QUICK_TIMEOUT,
            action="git.subprocess",
        )
        return _parse_worktree_porcelain(result.stdout)

    def prune_worktrees(self, clone_path: str) -> str:
        with self._audit.action("git.prune_worktrees", target=clone_path):
            result = self._run(
                ["worktree", "prune", "-v"],
                cwd=clone_path,
                timeout=QUICK_TIMEOUT,
                action="git.subprocess",
            )
            return result.output

    # -- derived values (computed, never stored) ---------------------------

    def status_porcelain(self, worktree_path: str) -> str:
        """``--porcelain`` reports untracked files too, which is deliberate: FR-016 and
        quickstart scenario 9 both treat merely-untracked as dirty enough to refuse."""
        result = self._run(
            ["status", "--porcelain"],
            cwd=worktree_path,
            timeout=QUICK_TIMEOUT,
            action="git.subprocess",
            check=False,
        )
        if not result.ok:
            raise BoundaryError(f"git status failed in {worktree_path}: {result.output}")
        return result.stdout

    def commits_ahead(self, clone_path: str, base_ref: str, branch: str) -> int | None:
        """``None`` when git could not answer — a missing ref, a broken repository, a
        timeout. Never ``0``, which is a real answer meaning "contained elsewhere" (R11)."""
        result = self._run(
            ["rev-list", "--count", f"{base_ref}..{branch}"],
            cwd=clone_path,
            timeout=QUICK_TIMEOUT,
            action="git.subprocess",
            check=False,
        )
        if not result.ok:
            return None
        try:
            return int(result.stdout.strip())
        except ValueError:
            return None

    def show_file_at_ref(self, clone_path: str, ref: str, path: str) -> bytes | None:
        """Read a file from the git object store, not the filesystem.

        This is the correct nuance for the fingerprint check (R12): what matters is what
        a freshly created worktree will contain, which is the committed content at the
        base branch tip — not whatever happens to be in the primary clone's working tree.
        """
        result = self._run(
            ["show", f"{ref}:{path}"],
            cwd=clone_path,
            timeout=QUICK_TIMEOUT,
            action="git.subprocess",
            check=False,
        )
        if not result.ok:
            return None  # the file does not exist at that ref, which is a normal answer
        return result.stdout.encode("utf-8")

    def worktree_exists(self, worktree_path: str) -> bool:
        return Path(worktree_path).is_dir()

    def rev_parse(self, clone_path: str, ref: str) -> str | None:
        result = self._run(
            ["rev-parse", "--verify", ref],
            cwd=clone_path,
            timeout=QUICK_TIMEOUT,
            action="git.subprocess",
            check=False,
        )
        return result.stdout.strip() if result.ok else None

    def default_remote(self, clone_path: str) -> str | None:
        """The remote to fetch from, or ``None`` when the repository has none.

        ``None`` rather than a guessed ``"origin"``: a local-only repository is a real
        and legitimate case, and inventing a remote name would turn it into a confusing
        fetch failure instead of a skipped step.
        """
        result = self._run(
            ["remote"], cwd=clone_path, timeout=QUICK_TIMEOUT, action="git.subprocess", check=False
        )
        remotes = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if "origin" in remotes:
            return "origin"
        return remotes[0] if remotes else None


class SimulatedVersionControl:
    """Logs every intended git operation and returns structurally valid fake handles.

    Reads that are cheap and side-effect-free (``status_porcelain``, ``commits_ahead``,
    ``show_file_at_ref``) return empty/zero answers rather than touching disk, because at
    ``plan`` level no worktree was created for them to describe.
    """

    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit

    def _log(self, action: str, **detail: Any) -> None:
        self._audit.record(action, outcome="ok", simulated=True, detail=detail)

    def fetch(self, clone_path: str, remote: str, ref: str) -> None:
        self._log("git.fetch", clone=clone_path, remote=remote, ref=ref)

    def add_worktree(
        self, clone_path: str, worktree_path: str, branch: str, base_ref: str
    ) -> WorktreeHandle:
        self._log(
            "git.add_worktree",
            clone=clone_path,
            worktree=worktree_path,
            branch=branch,
            base_ref=base_ref,
        )
        return WorktreeHandle(path=worktree_path, branch=branch, simulated=True)

    def remove_worktree(
        self, worktree_path: str, force: bool = False, clone_path: str | None = None
    ) -> RemovalResult:
        self._log(
            "git.remove_worktree", worktree=worktree_path, force=force, clone=clone_path
        )
        return RemovalResult(worktree_removed=True, branch_deleted=False)

    def delete_branch(self, clone_path: str, branch: str, force: bool = False) -> bool:
        self._log("git.delete_branch", clone=clone_path, branch=branch, force=force)
        return True

    def list_worktrees(self, clone_path: str) -> list[WorktreeInfo]:
        self._log("git.list_worktrees", clone=clone_path)
        return []

    def prune_worktrees(self, clone_path: str) -> str:
        self._log("git.prune_worktrees", clone=clone_path)
        return ""

    def status_porcelain(self, worktree_path: str) -> str:
        self._log("git.status_porcelain", worktree=worktree_path)
        return ""

    def commits_ahead(self, clone_path: str, base_ref: str, branch: str) -> int | None:
        self._log("git.commits_ahead", clone=clone_path, base_ref=base_ref, branch=branch)
        # ``0`` rather than ``None``: the simulation answers the question it was asked, and
        # answering "I could not determine" would make every simulated cleanup retain its
        # branch — a divergence from the real path, which is what the simulated boundaries
        # exist to avoid.
        return 0

    def show_file_at_ref(self, clone_path: str, ref: str, path: str) -> bytes | None:
        self._log("git.show_file_at_ref", clone=clone_path, ref=ref, path=path)
        return None

    def worktree_exists(self, worktree_path: str) -> bool:
        # It "exists" as far as the simulation is concerned. Answering False would fail
        # every simulated item at pre-launch validation.
        self._log("git.worktree_exists", worktree=worktree_path)
        return True

    def rev_parse(self, clone_path: str, ref: str) -> str | None:
        self._log("git.rev_parse", clone=clone_path, ref=ref)
        return "0" * 40

    def default_remote(self, clone_path: str) -> str | None:
        return "origin"


def _parse_worktree_porcelain(text: str) -> list[WorktreeInfo]:
    """Parse ``git worktree list --porcelain``.

    Records are separated by blank lines. ``prunable`` carries a reason on the same line
    and is how FR-017 detects a checkout whose directory no longer exists.
    """
    infos: list[WorktreeInfo] = []
    current: dict[str, Any] = {}

    def flush() -> None:
        if current.get("path"):
            infos.append(
                WorktreeInfo(
                    path=current["path"],
                    branch=current.get("branch"),
                    head=current.get("head"),
                    prunable=current.get("prunable", False),
                    prunable_reason=current.get("prunable_reason"),
                    locked=current.get("locked", False),
                )
            )
        current.clear()

    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            flush()
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            flush()
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "prunable":
            current["prunable"] = True
            current["prunable_reason"] = value or None
        elif key == "locked":
            current["locked"] = True
        elif key == "detached":
            current["branch"] = None
    flush()
    return infos
