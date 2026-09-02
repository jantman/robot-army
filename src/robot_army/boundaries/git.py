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
    FastForwardResult,
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

    def remote_branch_head(self, clone_path: str, remote: str, branch: str) -> str | None:
        """``git ls-remote`` — the remote's own answer, and nothing written to the clone.

        ``check=True`` is what makes the three answers three. ``ls-remote`` exits zero with
        no output when the remote answered and has no such ref, and non-zero when it could
        not be reached or refused us; so an empty success is ``None`` and a failure raises,
        without this code having to guess which kind of sadness a message describes.

        ``FETCH_TIMEOUT`` rather than ``QUICK_TIMEOUT``: this contacts the network. It
        transfers no objects and will normally answer in well under a second, but the
        smaller bound would turn a merely slow remote into "could not ask", and the caller
        keeps a branch every time it cannot ask.

        The branch is named as a fully-qualified ``refs/heads/<branch>`` and the returned
        ref name must equal it exactly. ``ls-remote`` treats its arguments as patterns
        matched on ``/`` boundaries from the right, and a decision this destructive should
        not rest on the argument having been pattern-free.
        """
        ref = f"refs/heads/{branch}"
        with self._audit.action(
            "git.ls_remote", target=f"{remote}/{branch}", detail={"clone": clone_path, "ref": ref}
        ) as outcome:
            result = self._run(
                ["ls-remote", remote, ref],
                cwd=clone_path,
                timeout=FETCH_TIMEOUT,
                action="git.subprocess",
            )
            for line in result.stdout.splitlines():
                sha, _, name = line.partition("\t")
                if name.strip() == ref and sha.strip():
                    outcome["sha"] = sha.strip()
                    return sha.strip()
            outcome["sha"] = None
            outcome["note"] = "the remote answered and does not have this branch"
            return None

    def fast_forward(
        self, clone_path: str, remote: str, branch: str
    ) -> FastForwardResult:
        """Advance the clone's own ``branch`` to ``remote/branch``, or say why not.

        The six preconditions are checked *before* anything is attempted, and each one that
        fails yields ``skipped`` with a reason naming it. That is not defensive
        over-engineering of ``git merge --ff-only``: the merge alone would refuse a
        divergent history, but it would refuse it as a non-zero exit carrying prose, and it
        would not have told us which branch was checked out or that the tree was dirty.
        The author wondering why their clone is still behind needs *dirty working tree*,
        not *exit 128*.

        ``--ff-only`` is still passed, as the last line of defence behind the checks.

        Nothing here raises. The caller is mid-dispatch and this is a convenience for the
        author's own clone; the session's worktree is built from ``remote/branch``
        regardless, so a failure here must not become a failed work item (FR-019).
        """
        with self._audit.action(
            "git.fast_forward",
            target=f"{clone_path}:{branch}",
            detail={"remote": remote, "branch": branch},
        ) as outcome:
            result = self._fast_forward(clone_path, remote, branch)
            outcome["outcome"] = result.outcome
            outcome["reason"] = result.reason
            outcome["before"] = result.before
            outcome["after"] = result.after
            return result

    def _fast_forward(
        self, clone_path: str, remote: str, branch: str
    ) -> FastForwardResult:
        """The decision itself, so the audit wrapper above stays one readable block."""
        # 1. A remote to advance to at all.
        if remote not in self.list_remotes(clone_path):
            return FastForwardResult(
                outcome="skipped", reason=f"the clone has no remote named {remote!r}"
            )

        # 2. On the branch, and *symbolically* — which is the check that also catches a
        #    detached HEAD and an interrupted rebase, because neither has one.
        checked_out = self._symbolic_head(clone_path)
        if checked_out is None:
            return FastForwardResult(
                outcome="skipped",
                reason="the clone has no branch checked out (detached HEAD or an "
                "interrupted rebase)",
            )
        if checked_out != branch:
            return FastForwardResult(
                outcome="skipped",
                reason=f"the clone is on {checked_out!r}, not {branch!r}",
            )

        # 3. The check that protects uncommitted work. Untracked files count: git's own
        #    `worktree remove` treats them as dirt for the same reason, and a merge that
        #    brought in a file the author already had untracked would refuse anyway —
        #    loudly, and after we had claimed we were only fast-forwarding.
        if self.status_porcelain(clone_path).strip():
            return FastForwardResult(
                outcome="skipped", reason="the clone has uncommitted changes"
            )

        # 4. Nothing half-finished. A conflicted merge leaves a dirty tree and is caught
        #    above, but a *clean* one — `merge --no-commit`, a cherry-pick paused with
        #    nothing to resolve — is not, and moving the branch under it would strand it.
        in_progress = self._operation_in_progress(clone_path)
        if in_progress is not None:
            return FastForwardResult(
                outcome="skipped", reason=f"an operation is in progress ({in_progress})"
            )

        # 5. Something to advance to. A fetch that produced nothing is not a failure.
        target = self.rev_parse(clone_path, f"{remote}/{branch}")
        if not target:
            return FastForwardResult(
                outcome="skipped",
                reason=f"{remote}/{branch} does not exist in the clone after fetching",
            )
        before = self.rev_parse(clone_path, branch)
        if before == target:
            return FastForwardResult(
                outcome="already_current", before=before, after=target
            )

        # 6. It is genuinely a fast-forward. A local branch holding commits the remote does
        #    not is the one case where "catch up" would mean discarding work, and this
        #    boundary never does that — it declines and says so.
        ancestor = self._run(
            ["merge-base", "--is-ancestor", branch, f"{remote}/{branch}"],
            cwd=clone_path,
            timeout=QUICK_TIMEOUT,
            action="git.subprocess",
            check=False,
        )
        if not ancestor.ok:
            return FastForwardResult(
                outcome="skipped",
                reason=f"{branch} has commits {remote}/{branch} does not — this would be a "
                "merge or a rebase, not a fast-forward",
                before=before,
            )

        merged = self._run(
            ["merge", "--ff-only", f"{remote}/{branch}"],
            cwd=clone_path,
            timeout=QUICK_TIMEOUT,
            action="git.subprocess",
            check=False,
        )
        if not merged.ok:
            return FastForwardResult(
                outcome="failed",
                reason=(merged.stderr or merged.stdout or "git merge --ff-only failed").strip(),
                before=before,
            )
        return FastForwardResult(
            outcome="updated", before=before, after=self.rev_parse(clone_path, branch)
        )

    def _symbolic_head(self, clone_path: str) -> str | None:
        """The checked-out branch name, or ``None`` when HEAD is not a symbolic ref."""
        result = self._run(
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=clone_path,
            timeout=QUICK_TIMEOUT,
            action="git.subprocess",
            check=False,
        )
        name = result.stdout.strip()
        return name if result.ok and name else None

    #: The marker each interrupted operation leaves in the git directory, and the word for
    #: it. A list rather than a single "is anything in progress?" query because git has no
    #: such query, and because the record is more useful naming which one.
    _IN_PROGRESS_MARKERS: tuple[tuple[str, str], ...] = (
        ("MERGE_HEAD", "merge"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("BISECT_LOG", "bisect"),
    )

    def _operation_in_progress(self, clone_path: str) -> str | None:
        result = self._run(
            ["rev-parse", "--absolute-git-dir"],
            cwd=clone_path,
            timeout=QUICK_TIMEOUT,
            action="git.subprocess",
            check=False,
        )
        if not result.ok or not result.stdout.strip():
            # Cannot tell, so decline. Every unresolved doubt leaves the clone alone.
            return "the git directory could not be located"
        git_dir = Path(result.stdout.strip())
        for marker, name in self._IN_PROGRESS_MARKERS:
            if (git_dir / marker).exists():
                return name
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

    def list_remotes(self, clone_path: str) -> list[str]:
        """Every configured remote name, in git's order.

        Split out of ``default_remote`` in milestone 005 rather than added beside it: the
        identity check needs the *count* — several remotes and none named ``origin`` is a
        refusal, not a pick (research R3) — and two functions shelling out to ``git
        remote`` would be two chances for the parsing to drift.
        """
        result = self._run(
            ["remote"], cwd=clone_path, timeout=QUICK_TIMEOUT, action="git.subprocess", check=False
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def default_remote(self, clone_path: str) -> str | None:
        """The remote to fetch from, or ``None`` when the repository has none.

        ``None`` rather than a guessed ``"origin"``: a local-only repository is a real
        and legitimate case, and inventing a remote name would turn it into a confusing
        fetch failure instead of a skipped step.
        """
        remotes = self.list_remotes(clone_path)
        if "origin" in remotes:
            return "origin"
        return remotes[0] if remotes else None

    def remote_url(self, clone_path: str, remote: str) -> str | None:
        """The configured URL of one remote, or ``None`` when it has none.

        New in milestone 005 and the first time this codebase has read a remote *URL* —
        ``default_remote`` returns a name, because until identity had to be checked nothing
        needed more. The value may embed credentials; every caller normalises before
        recording, comparing or printing it (FR-032).
        """
        result = self._run(
            ["remote", "get-url", remote],
            cwd=clone_path,
            timeout=QUICK_TIMEOUT,
            action="git.subprocess",
            check=False,
        )
        url = result.stdout.strip()
        return url if result.ok and url else None


class SimulatedVersionControl:
    """Logs every intended git operation and returns structurally valid fake handles.

    Reads that are cheap and side-effect-free (``status_porcelain``, ``commits_ahead``,
    ``show_file_at_ref``) return empty/zero answers rather than touching disk, because at
    ``plan`` level no worktree was created for them to describe.
    """

    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit
        #: For the reads that must be **real** at every effect level. Delegating to the
        #: real implementation rather than re-implementing them keeps one parser for one
        #: question, and keeps those reads audited exactly as the real path audits them —
        #: they genuinely happened, so recording them as simulated would be a lie.
        self._real = GitVersionControl(audit)

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

    def remote_branch_head(self, clone_path: str, remote: str, branch: str) -> str | None:
        self._log("git.ls_remote", clone=clone_path, remote=remote, branch=branch)
        # The same forty zeroes ``rev_parse`` answers with, and for the same reason. A
        # simulated cleanup must reach the decision the real one would; answering ``None``
        # here would mean "the remote does not have this branch", so every branch would be
        # retained at ``plan`` level and the simulation would stop describing the product.
        return "0" * 40

    def fast_forward(
        self, clone_path: str, remote: str, branch: str
    ) -> FastForwardResult:
        """Logs and changes nothing.

        Unlike ``remote_url`` and ``list_remotes`` above, this is not a cheap read that can
        answer honestly — it is the one verb in this protocol that writes to the author's
        own clone, and a simulation that performed it would be simulating nothing at all.
        ``skipped`` rather than ``updated`` because the caller records the outcome, and a
        dry run claiming it moved a branch it did not move is the kind of lie the effect
        levels exist to prevent.
        """
        self._log("git.fast_forward", clone=clone_path, remote=remote, branch=branch)
        return FastForwardResult(
            outcome="skipped", reason="simulated boundary makes no change to the clone"
        )

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

    def list_remotes(self, clone_path: str) -> list[str]:
        # A real read, for the reason ``remote_url`` states below.
        return self._real.list_remotes(clone_path)

    def default_remote(self, clone_path: str) -> str | None:
        return "origin"

    def remote_url(self, clone_path: str, remote: str) -> str | None:
        """The **real** URL, at every effect level.

        This class's rule is that cheap, side-effect-free reads answer honestly rather
        than returning a fake, and "what repository is at this path" has one true answer
        no matter what level we are simulating (research R3). Answering with an invented
        URL would let a ``plan``-level onboarding approve a location a ``live`` one would
        refuse — a divergence contracts/boundaries.md exists to prevent.
        """
        return self._real.remote_url(clone_path, remote)


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
