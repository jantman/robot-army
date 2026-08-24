"""The five seams that touch the outside world (contracts/boundaries.md).

FR-053 requires effect levels to be enforced *here* rather than at call sites, and gives
the reason: scattered ``if dry_run:`` checks drift as new code forgets them, cannot be
tested, and let the simulated path diverge from the real one — which is the exact failure
the dry-run mode exists to prevent.

Each protocol below has exactly **two** implementations in this milestone, both mandated
by FR-051 through FR-058. That is what keeps this from being the speculative generality
Principle I forbids; see the plan's Complexity Tracking table.

Note the asymmetry in ``IssueSource``: it is split into a reader and a writer so the
effect table can treat them differently. There is deliberately **no**
``SimulatedIssueReader`` — its absence means a bug that tries to fake reads fails to
import rather than quietly returning fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# -- value types ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Issue:
    number: int
    title: str
    body: str
    url: str
    labels: tuple[str, ...]
    author: str
    state: str  # "open" | "closed"


@dataclass(frozen=True, slots=True)
class PollResult:
    """``status == 304`` returns ``items=[]`` and is the healthy steady state."""

    items: tuple[Issue, ...]
    etag: str | None
    status: int
    rate_limit_remaining: int | None = None
    rate_limit_reset: int | None = None

    @property
    def unchanged(self) -> bool:
        return self.status == 304


@dataclass(frozen=True, slots=True)
class PullRequest:
    number: int
    url: str
    state: str


@dataclass(frozen=True, slots=True)
class RepoRef:
    full_name: str
    default_branch: str


@dataclass(frozen=True, slots=True)
class WorktreeHandle:
    path: str
    branch: str
    simulated: bool = False


@dataclass(frozen=True, slots=True)
class WorktreeInfo:
    path: str
    branch: str | None
    head: str | None
    prunable: bool = False
    prunable_reason: str | None = None
    locked: bool = False


@dataclass(frozen=True, slots=True)
class RemovalResult:
    """``worktree_removed`` and ``branch_deleted`` are separate on purpose.

    Removal is two steps, and a caller that does only the first accumulates
    ``robot-army/*`` branches in every repository forever (FR-016).
    """

    worktree_removed: bool
    branch_deleted: bool
    refused_reason: str | None = None
    output: str = ""


@dataclass(frozen=True, slots=True)
class HookResult:
    ok: bool
    step_index: int | None = None
    output: str = ""
    timed_out: bool = False
    description: str = ""


@dataclass(frozen=True, slots=True)
class HostHandle:
    socket_path: str
    argv: tuple[str, ...]
    simulated: bool = False
    pid: int | None = None


@dataclass(frozen=True, slots=True)
class DisplayHandle:
    window_id: int
    title: str = ""
    user_vars: dict[str, str] = field(default_factory=dict)
    simulated: bool = False


@dataclass(frozen=True, slots=True)
class HostCapabilities:
    """All three values were **measured** in M0, not assumed. The orchestrator branches
    on them; they are not decoration."""

    survives_display_death: bool
    reattachable: bool
    multi_viewer: bool


class BoundaryError(Exception):
    """A boundary could not complete its work.

    Raised rather than returning a falsy value, because "it did not happen" and "I could
    not ask" are different facts and conflating them is the silent failure Principle III
    forbids.
    """


class TransportError(BoundaryError):
    """A network call failed. Never converted into an empty result."""


# -- protocols --------------------------------------------------------------


@runtime_checkable
class IssueSourceReader(Protocol):
    """Reads are real at every effect level (FR-052)."""

    def poll(self, repo_key: str, etag: str | None) -> PollResult: ...

    def get_issue(self, repo_key: str, number: int) -> Issue | None: ...

    def is_closed(self, repo_key: str, number: int) -> bool: ...

    def open_pr_for_branch(self, repo_key: str, branch: str) -> PullRequest | None: ...

    def list_owned_repos(self) -> list[RepoRef]: ...


@runtime_checkable
class IssueSourceWriter(Protocol):
    """``comment`` is the only write in this milestone."""

    def comment(self, repo_key: str, number: int, body: str) -> str: ...


@runtime_checkable
class VersionControl(Protocol):
    def fetch(self, clone_path: str, remote: str, ref: str) -> None: ...

    def add_worktree(
        self, clone_path: str, worktree_path: str, branch: str, base_ref: str
    ) -> WorktreeHandle: ...

    def remove_worktree(
        self, worktree_path: str, force: bool = False, clone_path: str | None = None
    ) -> RemovalResult: ...

    def delete_branch(self, clone_path: str, branch: str, force: bool = False) -> bool: ...

    def list_worktrees(self, clone_path: str) -> list[WorktreeInfo]: ...

    def prune_worktrees(self, clone_path: str) -> str: ...

    def status_porcelain(self, worktree_path: str) -> str: ...

    def commits_ahead(self, clone_path: str, base_ref: str, branch: str) -> int: ...

    def show_file_at_ref(self, clone_path: str, ref: str, path: str) -> bytes | None: ...

    def default_remote(self, clone_path: str) -> str | None: ...

    def worktree_exists(self, worktree_path: str) -> bool:
        """Does this worktree really exist?

        Behind the boundary rather than a bare ``Path.is_dir()`` at the call site,
        because FR-026's pre-launch path validation must stay meaningful at every effect
        level. At ``plan`` no directory was created, so asking the filesystem would fail
        every simulated item and send it down the failure branch instead of the identical
        path — the divergence contracts/boundaries.md forbids.
        """
        ...


@runtime_checkable
class HookRunner(Protocol):
    def run(
        self,
        steps: Any,
        worktree_path: str,
        clone_path: str,
        env: dict[str, str],
    ) -> HookResult: ...


@runtime_checkable
class SessionHost(Protocol):
    """Owns the process and its PTY. This is the axis along which *work survival*
    varies, which is why it is separate from ``Display``."""

    capabilities: HostCapabilities

    def build_argv(self, socket_path: str, argv: list[str]) -> list[str]: ...

    def spawn(self, cwd: str, argv: list[str], socket_path: str) -> HostHandle: ...

    def confirm_session(self, session_id: str, timeout_seconds: float) -> Any | None:
        """Wait for evidence that the session with this id is really running.

        On the real host that is a session-registry entry (FR-025); the simulated host
        returns a structurally valid stand-in, because at a simulated effect level no
        real entry can ever appear and letting confirmation time out would send simulated
        work down the failure branch instead of the identical path.

        Returning ``None`` means the confirmation window elapsed.
        """
        ...

    def is_alive(self, handle: HostHandle) -> bool: ...

    def terminate(self, handle: HostHandle, scope: str | None = None) -> None: ...

    def attach_command(self, handle: HostHandle) -> list[str]: ...


@runtime_checkable
class Display(Protocol):
    """An optional viewer onto a hosted session, **composed with a host, not substituted
    for one**. Kitty renders a PTY someone else may own; dtach owns one."""

    def open(
        self,
        cwd: str,
        argv: list[str],
        title: str,
        user_vars: dict[str, str],
        env: dict[str, str],
    ) -> DisplayHandle: ...

    def is_open(self, handle: DisplayHandle) -> bool: ...

    def close(self, handle: DisplayHandle) -> None: ...

    def find_by_var(self, key: str, value: str) -> DisplayHandle | None: ...

    def send_text(self, handle: DisplayHandle, text: str) -> None: ...

    def probe(self) -> str | None: ...


__all__ = [
    "BoundaryError",
    "Display",
    "DisplayHandle",
    "HookResult",
    "HookRunner",
    "HostCapabilities",
    "HostHandle",
    "Issue",
    "IssueSourceReader",
    "IssueSourceWriter",
    "PollResult",
    "PullRequest",
    "RemovalResult",
    "RepoRef",
    "SessionHost",
    "TransportError",
    "VersionControl",
    "WorktreeHandle",
    "WorktreeInfo",
]
