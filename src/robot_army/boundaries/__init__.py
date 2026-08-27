"""The six seams that touch the outside world (contracts/boundaries.md, card-source.md).

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
import rather than quietly returning fixtures. ``CardSource`` is split the same way, for
the same reason, and has no simulated reader either.

**``CardSource`` is a sixth seam, not a second ``IssueSource``** (research.md R1). A
common ``poll() -> [SourceItem]`` could be written — both sides have an id, a title, a
body and labels — but no caller ever holds one where it could just as well hold the other:
GitHub is where *dispatchable work* is read from, Trello is where *intake* is read from,
and its output is a GitHub issue. Two implementations that are never used polymorphically
are the strategy interface with one caller that Principle I forbids.
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
class Card:
    """One card as the board reports it (contracts/card-source.md).

    ``last_activity`` is carried as the **string the API returned**, not a parsed
    datetime. It is used only for equality against the stored baseline (R9), and parsing
    it would invite a timezone bug into a comparison that does not need one.

    ``closed`` is Trello's word for archived, kept rather than renamed so that anyone
    comparing this against an API response is reading the same word.
    """

    card_id: str
    board_id: str
    url: str
    title: str
    body: str
    label_ids: tuple[str, ...] = ()
    list_id: str = ""
    last_activity: str = ""
    closed: bool = False


@dataclass(frozen=True, slots=True)
class BoardInfo:
    """What the startup preconditions are checked against (R10, R11).

    ``member_ids`` is carried to be **logged, not tested**. Sole membership is deliberately
    not a precondition: who else may see the author's own private board is the author's
    decision, and a system that refused to run over it would be building the access policy
    Principle II forbids. The list is recorded so an unexpected card can be traced.

    ``labels`` and ``lists`` map name to id, because resolving the configured names once at
    startup makes the per-card filter an id comparison — cheaper, and immune to a label
    being renamed mid-run.
    """

    board_id: str
    name: str
    permission_level: str
    member_ids: tuple[str, ...] = ()
    labels: dict[str, str] = field(default_factory=dict)
    lists: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CardWriteResult:
    """What a board write returns: its own result, and the card's refreshed activity.

    Both halves travel together because R9's loop closes only if they do. Commenting on a
    card changes its ``dateLastActivity``, which is the rescan trigger — a writer that
    performed the write and left the caller to re-read would reopen the very loop this
    rule exists to close. The caller stores ``last_activity`` in the same transaction that
    records the write.
    """

    url: str | None
    last_activity: str | None


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
class RepoInfo:
    """One repository as the source system describes it (milestone 005, research R5).

    Answers three questions from **one** request — does it exist, who owns it, and what is
    its canonical name — because SC-009 caps onboarding at one additional request no matter
    how many repositories the author owns, and because a case-mismatched name is otherwise
    diagnosed as a missing directory.
    """

    exists: bool
    owner: str = ""
    name: str = ""
    default_branch: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


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

    def list_issues_since(
        self, repo_key: str, since: str, *, author: str | None = None, limit: int = 100
    ) -> list[Issue]:
        """Issues created since a timestamp — the immediately consistent recovery read.

        Listing rather than search, deliberately (R6): the search index lags by minutes,
        so an issue created seconds before a crash would be invisible to it, producing
        exactly the duplicate the recovery exists to prevent.
        """
        ...

    def get_repo(self, repo_key: str) -> RepoInfo:
        """One repository, in one request. ``RepoInfo(exists=False)`` for a 404.

        A 404 is a *fact about the repository*, not a transport failure, so it is returned
        rather than raised — the distinction milestone 005's allowlist needs in order to
        say "no such repository" differently from "you may not onboard that one".
        """
        ...


@runtime_checkable
class IssueSourceWriter(Protocol):
    """Writes to the issue source. Selected only at ``live``."""

    def comment(self, repo_key: str, number: int, body: str) -> str: ...

    def create_issue(self, repo_key: str, title: str, body: str) -> Issue:
        """Create one issue and return it **as the source reported it**.

        The mapping is written from this response, not from a request that was assumed to
        have worked, which is what makes the number and URL in the ``cards`` row facts
        rather than predictions.

        There is deliberately **no parameter that could carry a label** (FR-015). The
        dispatch label is the human gate, and the gate is absent from the interface rather
        than defended by a rule someone has to remember not to break — a caller that wants
        to label the issue it just filed cannot express the wish.
        """
        ...


@runtime_checkable
class CardSourceReader(Protocol):
    """Reads from the intake board. Real at **every** effect level (FR-038).

    A dry run that fakes its reads tells you nothing about which cards would be acted on,
    which is the main thing you want to check. As with ``IssueSourceReader`` there is no
    simulated counterpart, so a bug that tries to fake board reads fails to import.
    """

    def board_info(self) -> BoardInfo:
        """The board's name, privacy, members, labels and lists — one startup call."""
        ...

    def poll(self, board_id: str, label_id: str) -> list[Card]:
        """**All** currently tagged, unarchived cards. Not a delta.

        There is no usable conditional-request economy on this endpoint (R13), which
        argues for a longer interval rather than a cleverer mechanism — hence a 300-second
        default against GitHub's 60.
        """
        ...

    def get_card(self, card_id: str) -> Card | None:
        """One card as it is *now*. The freshness re-read a move check depends on."""
        ...

    def card_comments(self, card_id: str) -> list[str]:
        """Comment bodies, newest first. Exists only for R7's recovery path.

        **Not called when a mapping row exists.** That is §11's "don't parse comments as
        the authoritative source in normal operation" expressed as a call-site rule, with
        a test behind it.
        """
        ...


@runtime_checkable
class CardSourceWriter(Protocol):
    """Writes to the intake board. Selected only at ``live`` (FR-039).

    Neither call decides whether it is *allowed*. The check against ``placed_list_id``
    (R12) belongs to the caller, because it is policy about the author's intent rather
    than a property of the transport.
    """

    def comment(self, card_id: str, body: str) -> CardWriteResult: ...

    def move(self, card_id: str, list_id: str) -> CardWriteResult: ...


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

    def commits_ahead(self, clone_path: str, base_ref: str, branch: str) -> int | None:
        """How many commits ``branch`` has that ``base_ref`` does not, or ``None``.

        ``None`` means **could not determine**, and keeping it distinct from ``0`` is the
        whole reason this signature changed in milestone 004 (R11). The implementation used
        to swallow a failed ``rev-list`` into ``return 0``. To the resume-signal caller that
        reads as a harmless "no information"; to a branch-deletion decision the very same
        value reads as *"every commit on this branch already exists elsewhere, delete it"*.
        Same number, opposite meanings, and the difference invisible at the call site — so a
        transient git failure would have authorised destroying commits that exist nowhere
        else.

        Callers that want the old reading say so explicitly: ``worktree.condition`` maps
        ``None`` to ``0``. Callers making an irreversible decision must treat ``None`` as
        unproven.
        """
        ...

    def show_file_at_ref(self, clone_path: str, ref: str, path: str) -> bytes | None: ...

    def rev_parse(self, clone_path: str, ref: str) -> str | None:
        """Resolve a ref to a sha, or ``None`` if it does not resolve.

        Declared here in milestone 004 rather than added: both implementations have always
        had it and ``worktree.prepare`` has always called it, so the protocol was quietly
        understating what a ``VersionControl`` must provide.
        """
        ...

    def list_remotes(self, clone_path: str) -> list[str]:
        """Every configured remote name. Milestone 005: the identity check needs the
        *count*, because several remotes and none named ``origin`` is a refusal rather
        than a pick (research R3)."""
        ...

    def default_remote(self, clone_path: str) -> str | None: ...

    def remote_url(self, clone_path: str, remote: str) -> str | None:
        """One remote's configured URL, or ``None``.

        The value may embed credentials, so every caller normalises it through
        ``repos.normalise_remote`` before recording, comparing or printing (FR-032).
        """
        ...

    def worktree_exists(self, worktree_path: str) -> bool:
        """Does this worktree really exist?

        Behind the boundary rather than a bare ``Path.is_dir()`` at the call site,
        because FR-026's pre-launch path validation must stay meaningful at every effect
        level. At ``plan`` no directory was created, so asking the filesystem would fail
        every simulated item and send it down the failure branch instead of the identical
        path — the divergence contracts/boundaries.md forbids.
        """
        ...


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    """One thing worth saying out loud (contracts/notifications.md).

    Composed **only** from identifiers and state names. There is no field a secret could
    reach — no token, no header, no request body, no exception text from an authenticated
    call — and that is a property of the shape rather than of the discipline of whoever
    fills it in. FR-037 is checked against a run that includes an authentication failure,
    which is the case where a credential would otherwise ride along inside an error string.
    """

    #: ``dispatch`` | ``completion`` | ``failure`` | ``needs_info``.
    kind: str
    item_id: int | None
    repo_key: str | None
    #: One line, safe to render anywhere.
    title: str
    #: Where to look next — a state name, an item id, a command to run.
    detail: str
    #: The issue or the card, whichever exists.
    url: str | None = None


@runtime_checkable
class Notifier(Protocol):
    """Says something happened, on the channel the health signal already uses.

    A boundary rather than a direct call to ``health.post_json`` from four service modules,
    and the reason is structural: FR-040 requires sends to be simulated below the ``live``
    effect level, and ``effects.py`` is the only module in this package permitted to know an
    effect level exists. Calling the transport directly would put an effect-level check back
    at a call site — the exact pattern milestone 001 built ``effects.py`` to eliminate, and
    the one a test greps for.
    """

    def send(self, event: NotificationEvent) -> bool: ...


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
    "BoardInfo",
    "BoundaryError",
    "Card",
    "CardSourceReader",
    "CardSourceWriter",
    "CardWriteResult",
    "Display",
    "DisplayHandle",
    "HookResult",
    "HookRunner",
    "HostCapabilities",
    "HostHandle",
    "Issue",
    "IssueSourceReader",
    "IssueSourceWriter",
    "NotificationEvent",
    "Notifier",
    "PollResult",
    "PullRequest",
    "RemovalResult",
    "RepoInfo",
    "SessionHost",
    "TransportError",
    "VersionControl",
    "WorktreeHandle",
    "WorktreeInfo",
]
