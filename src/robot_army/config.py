"""TOML configuration, loaded with stdlib ``tomllib`` and validated in aggregate.

Per contracts/config.md. Two behaviours here are requirements rather than style:

* **Every problem is reported at once**, not just the first. Fixing one typo per restart
  is a poor experience at 2am, which is the audience the constitution names.
* **A literal token in this file is an error, not a warning.** The repository is public
  (Principle V), and a config that "works" with a token in it is a config that will
  eventually be pasted somewhere.

Unknown keys are a warning at the top level (so a config written for a later milestone
still starts) but an **error** inside ``[repos.*]``, because a typo there silently
disables a preparation step and produces a broken worktree.
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from robot_army.effects import EffectLevel
from robot_army.paths import Layout, default_config_path, runtime_dir, unsafe_ancestor

# The four lifecycle command names have one definition, and it is the one detection already
# uses. This edge is acyclic and stays that way under the rule recorded in ``speckit.py``'s
# docstring: that module must never import this one (milestone 039, research R2).
from robot_army.speckit import LIFECYCLE

#: Longest instruction accepted for a single Spec Kit lifecycle command. The composed prompt
#: is one ``argv`` entry, which is why ``prompt.MAX_BODY_CHARS`` exists at all; four of these
#: add at most 16,000 characters beside a body already capped at 60,000. The number is not
#: tuned for anything — it is generous for an instruction and small enough that a document
#: pasted in by accident is refused rather than dispatched.
MAX_INSTRUCTION_CHARS = 4000

#: Things that look like a credential rather than a variable name.
#:
#: The point of this list is that a `*_env` key holds the **name** of an environment
#: variable, never the value — the repository is public (Principle V), and a config that
#: "works" with a secret in it is a config that will eventually be pasted somewhere.
#:
#: Milestone 003 added the Trello shapes, and the gap they close is worth naming: until
#: they were added, this guard could not catch a real Trello credential at all. Only the
#: GitHub prefixes were listed, so a genuine 32-hex API key pasted into `[trello] key_env`
#: passed validation in silence. The test that was supposed to cover it pasted a
#: *GitHub*-shaped token into a Trello field, so it exercised the mechanism and proved
#: nothing about the property.
#:
#: The bare-hex entries are safe against the values this config legitimately carries: a
#: Trello board id is 24 hex characters and a short board link is 8, neither of which is
#: 32 or 64, and no other string these two sections hold is bare hex of any length.
_TOKEN_PATTERNS = (
    re.compile(r"^gh[pousr]_[A-Za-z0-9]{20,}$"),
    re.compile(r"^github_pat_[A-Za-z0-9_]{20,}$"),
    # Trello's API key.
    re.compile(r"^[0-9a-fA-F]{32}$"),
    # Trello's classic API token.
    re.compile(r"^[0-9a-fA-F]{64}$"),
    # Trello's newer prefixed token.
    re.compile(r"^ATTA[A-Za-z0-9_-]{20,}$"),
)

#: A Pushover application token or user key: 30 alphanumeric characters. Consulted only
#: when scanning ``[pushover]`` — see :func:`_looks_like_pushover_credential`.
_PUSHOVER_CREDENTIAL = re.compile(r"^[A-Za-z0-9]{30}$")

VALID_PERMISSION_MODES = (
    "acceptEdits",
    "auto",
    "bypassPermissions",
    "manual",
    "dontAsk",
    "plan",
)


class ConfigError(Exception):
    """Aggregate validation failure. Carries every problem found, not just the first."""

    def __init__(self, problems: list[str], warnings: list[str] | None = None) -> None:
        self.problems = problems
        self.warnings = warnings or []
        body = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"configuration is invalid ({len(problems)} problem(s)):\n{body}")


@dataclass(frozen=True, slots=True)
class HookStep:
    """One preparation step. Exactly one of ``run``, ``link``, ``copy`` is set.

    ``link`` and ``copy`` are first-class forms rather than shell commands because they
    must be idempotent and readable (FR-015).
    """

    kind: str  # "run" | "link" | "copy"
    value: str
    timeout: int

    def describe(self) -> str:
        return f"{self.kind}: {self.value}"


@dataclass(frozen=True, slots=True)
class RepoConfig:
    """A repository's settings — as a ``[repos.*]`` section states them, or as
    ``repos.resolve`` produces them from the onboarding record over that section over the
    global defaults.

    ``path`` is ``None`` **only** on the section form, and only when the author wrote no
    ``path`` — which since milestone 005 means *derive it* (FR-003). The resolved form
    always carries a real path, because a repository has no resolved form until it has
    been onboarded at a location a human approved. Every consumer other than ``onboard``
    itself reads the resolved form, so ``None`` does not reach them.
    """

    key: str
    path: Path | None
    base_branch: str
    post_create: tuple[HookStep, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    permission_mode: str | None = None
    model: str | None = None
    #: How many sessions may run in this repository at once. ``None`` falls back to
    #: ``[dispatch] default_repo_max_sessions``, and the distinction is kept rather than
    #: resolved at parse time so ``robot-army capacity`` can tell "you chose 1" from "1 is
    #: what you get" (US2 AS4).
    max_sessions: int | None = None
    #: Higher runs first under ``repo-priority`` ordering; ignored under ``oldest-first``.
    #: Zero by default, which makes that mode degrade to oldest-first — the harmless
    #: reading of an unconfigured repository.
    priority: int = 0
    #: Whether this repository's dispatch order comes from its GitHub project board
    #: (issue #48). ``None`` inherits ``[dispatch] project_ordering``, kept unresolved so a
    #: surface can say whether the author chose it. Setting it ``False`` is the escape
    #: hatch for the one behaviour here that changes by default: a repository with a
    #: linked project starts ordering by it, and starts holding cards parked elsewhere.
    project_ordering: bool | None = None
    #: Which project governs this repository, when discovery should not decide. A number,
    #: or a ``github.com/{users,orgs}/…/projects/N`` URL — the URL form exists because
    #: discovery reads only *linked* projects, so an unlinked board can be named no other
    #: way. ``None`` means discover it.
    project: str | None = None
    #: The board column to dispatch from. ``None`` means recognise it — see
    #: :data:`RECOGNISED_DISPATCH_COLUMNS`.
    project_column: str | None = None
    #: Whether this repository waits for its previous issue to land before starting the
    #: next one. ``None`` inherits ``[dispatch] wait_for_merge``, and the distinction is
    #: kept rather than resolved at parse time for the reason ``max_sessions`` keeps its
    #: own: a surface reporting "waiting" should be able to say *which* setting said so.
    wait_for_merge: bool | None = None
    #: Whether this repository's sessions are told it uses Spec Kit. ``None`` inherits
    #: ``[speckit] enabled``; the distinction is kept rather than resolved at parse time so
    #: the record can say *which* setting suppressed a dispatch (milestone 007, FR-011).
    speckit: bool | None = None
    #: This repository's overrides for what each lifecycle command is invoked with
    #: (milestone 039). Absent key inherits ``[speckit.commands]``; **an empty-string value
    #: is meaningful here** and means "no instruction for this command in this repository".
    #: Globally those two are the same state and empty is a mistake; here they differ, and
    #: empty is the only way to drop one instruction without ``speckit = false`` removing
    #: the whole guidance block.
    #:
    #: Not called ``speckit`` for the dull reason that the name is taken by the boolean
    #: above and TOML cannot make one key both a bool and a table (research R1).
    speckit_commands: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DaemonConfig:
    effect_level: EffectLevel = EffectLevel.LIVE
    tick_seconds: int = 5
    poll_seconds: int = 60
    reconcile_seconds: int = 60
    dispatching_max_age_seconds: int = 900
    confirm_timeout_seconds: int = 45
    max_concurrent_sessions: int = 2


@dataclass(frozen=True, slots=True)
class GitHubConfig:
    author: str = ""
    label: str = "robot-army"
    token_env: str | None = None
    token_file: Path | None = None
    include_owned: bool = True
    extra_repos: tuple[str, ...] = ()
    timeout_seconds: int = 20
    max_retries: int = 4
    api_base: str = "https://api.github.com"

    def read_token(self) -> str:
        """Resolve the token at the moment it is needed, never storing it in the config."""
        if self.token_env:
            value = os.environ.get(self.token_env, "")
            if not value:
                raise ConfigError([f"[github] token_env {self.token_env!r} is set but empty"])
            return value
        if self.token_file:
            return self.token_file.read_text(encoding="utf-8").strip()
        raise ConfigError(["[github] neither token_env nor token_file is configured"])


@dataclass(frozen=True, slots=True)
class TrelloConfig:
    """The board, absent by default (FR-001, contracts/config.md).

    ``config.trello is None`` is what makes an unconfigured installation *inert*: not
    "configured with empty values and skipped at the call site", but with no section to
    read, so no board request is ever constructed.

    ``label`` is Trello's own word for what spec.md calls the *tag*. The spec keeps the
    two apart to protect the reader — this project already has a `label`, the GitHub one
    that is the human gate — and this key follows the API to protect the implementer.

    ``poll_seconds`` defaults to 300 rather than GitHub's 60 because there is no
    conditional-request economy on the endpoint we need (R13): every board poll costs a
    real request, and a card the author just wrote is not urgent — nothing dispatches from
    it until a human labels the issue it becomes.
    """

    board_id: str = ""
    label: str = "AI-task"
    in_progress_list: str = "In Progress"
    done_list: str = "Done"
    #: Board column *names* whose cards are not intake (milestone 006, FR-001).
    #:
    #: Empty by default, which is what makes an unconfigured installation behaviourally
    #: identical to milestone 003: the ids resolve to an empty frozenset and every
    #: comparison against it is false, so the exclusion is inert rather than merely
    #: skipped at a call site.
    #:
    #: It gates **intake only**. A card that already has a recorded issue is never
    #: affected in either direction — which is also what makes listing
    #: ``in_progress_list`` or ``done_list`` here a harmless no-op rather than a
    #: contradiction the loader has to reject: by the time the daemon puts a card in
    #: either, the card is ``linked``.
    #:
    #: A tuple, ordered as written, because ``doctor``'s report and the failure messages
    #: read back in the author's own order — a set would reorder a report of a file they
    #: are looking at while reading it.
    ignore_lists: tuple[str, ...] = ()
    poll_seconds: int = 300
    timeout_seconds: int = 20
    max_retries: int = 4
    api_base: str = "https://api.trello.com/1"
    key_env: str | None = None
    key_file: Path | None = None
    token_env: str | None = None
    token_file: Path | None = None

    def read_key(self) -> str:
        """Resolve the API key at the moment it is needed, never storing it in the config."""
        return self._read("key", self.key_env, self.key_file)

    def read_token(self) -> str:
        return self._read("token", self.token_env, self.token_file)

    def _read(self, what: str, env: str | None, file: Path | None) -> str:
        if env:
            value = os.environ.get(env, "")
            if not value:
                raise ConfigError([f"[trello] {what}_env {env!r} is set but empty"])
            return value
        if file:
            return file.read_text(encoding="utf-8").strip()
        raise ConfigError([f"[trello] neither {what}_env nor {what}_file is configured"])


@dataclass(frozen=True, slots=True)
class PushoverConfig:
    """The second notification channel's two credential files (contracts/config.md).

    Both fields are non-optional, and that is the point: a ``PushoverConfig`` exists only
    when both keys were configured and both validated, so "a half-configured channel
    cannot send" is a property of the type rather than a check somewhere downstream.

    Files only, with no ``*_env`` twin. ``[github]`` and ``[trello]`` carry both forms
    because both were asked for; issue #106 asks for files, and a second form with no
    caller is the knob Principle I forbids.
    """

    token_file: Path
    user_key_file: Path

    def read_token(self) -> str:
        """Resolve the application token at the moment it is needed, never storing it."""
        return self._read("token_file", self.token_file)

    def read_user_key(self) -> str:
        return self._read("user_key_file", self.user_key_file)

    @staticmethod
    def _read(key: str, path: Path) -> str:
        # ``strip`` because a credential file written with ``echo`` ends in a newline, and
        # a newline in a form parameter is a 4xx nobody enjoys diagnosing.
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            # Names the *path*, never the contents — this is the one place a vanished or
            # unreadable credential file is turned into a message, and the message travels
            # into an audit record (FR-007).
            raise ConfigError(
                [f"[pushover] {key} could not be read: {path} ({exc.strerror})"]
            ) from exc


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    permission_mode: str = "auto"
    model: str = ""
    #: ``""`` means *not stated*, and that is the whole of it: since issue #150 the base ref
    #: is resolved by ``repos.base_ref``, which prefers what the clone says its default
    #: branch is and reaches this value only when the clone cannot answer. A default of
    #: ``"main"`` here would be indistinguishable from a maintainer who wrote ``"main"``
    #: because the shipped example told them to — and that copied value outranking the
    #: repository's own answer is exactly the bug.
    base_branch: str = ""
    branch_prefix: str = "robot-army"
    binary: str = "claude"


def _unsafe_socket_root(pattern: str) -> str | None:
    """Why the directory a ``socket_glob`` is rooted in cannot be trusted, if it cannot.

    "Rooted in" is the longest leading run of the pattern containing no wildcard —
    ``/tmp`` for ``/tmp/mykitty-*`` — because that is the part naming a real directory
    rather than a set of names. A directory that does not exist yet is not judged: there
    is nothing to look at, and discovery will refuse whatever appears there if it is
    unsafe.

    Returns the reason rather than the path so the warning says what was actually wrong.
    The directory judged and the directory at fault are often not the same one — the walk
    goes all the way up — and a warning naming the wrong directory sends the reader to fix
    something that is not broken.

    Note what this deliberately does *not* flag: ``/tmp`` itself. It is world-writable but
    sticky, so nobody can swap an entry in it, and warning about the setup the README
    shipped for two milestones would be crying wolf.
    """
    wildcard = min((i for i in (pattern.find("*"), pattern.find("?")) if i != -1), default=-1)
    fixed = (pattern if wildcard == -1 else pattern[:wildcard]).removeprefix("unix:")
    # Cut at the last separator in the *string*, not with ``Path(...).parent``. Path
    # normalises a trailing slash away first, so ``/srv/shared/*`` — where the wildcard is
    # the whole final segment — would climb to ``/srv`` and judge the wrong directory,
    # and ``/tmp/*/mykitty-*`` would climb all the way to ``/``. Both are exactly the
    # shapes this is supposed to catch.
    slash = fixed.rfind("/")
    if slash > 0:
        directory = Path(fixed[:slash])
    elif slash == 0:
        directory = Path("/")
    else:
        directory = Path()  # a relative pattern: the working directory is what is judged
    return unsafe_ancestor(directory) if directory.is_dir() else None


def default_socket_glob() -> str:
    """Where kitty's control socket is looked for when the config does not say.

    Under ``$XDG_RUNTIME_DIR`` (mode 0700, ours, tmpfs, gone at logout) rather than
    ``/tmp``, which is world-writable: a glob rooted there returns names any local user
    can create, and RA-15 is what happens next. ``runtime_dir()`` already answers this
    for the daemon's own sockets, and its no-login fallback — the state directory — is
    owned by us too, so the answer is never ``/tmp``.

    Computed, not a class constant: it reads the environment, and a value frozen at
    import would describe whichever environment happened to import this module first.
    """
    return f"{runtime_dir()}/mykitty-*"


@dataclass(frozen=True, slots=True)
class TerminalConfig:
    socket_glob: str = field(default_factory=default_socket_glob)
    probe_timeout_seconds: int = 2
    binary: str = "kitty"


@dataclass(frozen=True, slots=True)
class HealthConfig:
    max_age_seconds: int = 180
    webhook_url: str = ""


@dataclass(frozen=True, slots=True)
class HooksConfig:
    default_timeout_seconds: int = 300
    #: The preparation steps every repository gets unless its own section says otherwise
    #: (milestone 005, FR-020). A repository's own ``post_create`` **replaces** these — it
    #: does not extend them, and there is no way to ask for both: the repositories that
    #: need their own steps need *different* steps, not the common one plus extras, and
    #: appending would make the shared default impossible to opt out of (research R10).
    post_create: tuple[HookStep, ...] = ()


#: The two dispatch orders FR-016 names. A tuple of strings rather than an enum because
#: the value is validated against config text and rendered back to the author verbatim;
#: an enum would add a translation in each direction and remove nothing.
VALID_ORDER_MODES: tuple[str, ...] = ("oldest-first", "repo-priority")

#: Column names automatic discovery will accept as *the* dispatch column (issue #48).
#:
#: Compared after lowercasing and collapsing internal whitespace, so ``Ready``, ``Todo``
#: and ``To do`` all match. GitHub's Kanban template offers exactly one of these
#: (``Ready``) and the simpler board template exactly one (``Todo``), which is what makes
#: the common case configuration-free.
#:
#: A board offering **two or more** of them, or none, is ambiguous and is reported rather
#: than guessed at (FR-018): picking one would be choosing the author's workflow for them
#: in a way they would only discover by watching the wrong issue start.
RECOGNISED_DISPATCH_COLUMNS: tuple[str, ...] = ("ready", "todo", "to do")


def normalise_column(name: str) -> str:
    """Fold a column name for comparison. Case- and whitespace-insensitive."""
    return " ".join(name.split()).casefold()


#: ``github.com/users/<login>/projects/<n>`` and its ``orgs`` counterpart. Anchored at the
#: host so a bare path cannot match.
#:
#: What follows the number is tolerated **specifically**, not generally: an optional
#: ``/views/<n>`` and an optional query or fragment, because that is what the browser
#: address bar actually holds when the author copies a board. It used to accept any
#: trailing path at all, which is broader than the sentence above it claimed and let a
#: typo — ``/projects/3/nonsense`` — load cleanly and fail at the first poll instead of at
#: the moment it was written.
_PROJECT_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(users|orgs)/([^/]+)/projects/(\d+)"
    r"(?:/views/\d+)?/?(?:[?#].*)?$"
)


@dataclass(frozen=True, slots=True)
class ProjectRef:
    """A configured project, resolved as far as text alone allows.

    ``owner_type`` and ``login`` are ``None`` for a bare number, which means *this
    repository's owner*. They are populated from a URL, which is the only form that can
    name a project the repository is not linked to — and an unlinked project is exactly
    what discovery cannot see, so the URL form is not a convenience.
    """

    number: int
    owner_type: str | None = None
    login: str | None = None


def parse_project_reference(value: object) -> ProjectRef | None:
    """A configured ``project`` value as a reference, or ``None`` if it is neither form.

    Accepts an integer, a string of digits, or a project URL. Returns ``None`` rather than
    raising so the caller decides whether a bad value is a config problem or a runtime
    one — the loader treats it as a problem, and nothing else parses these.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return ProjectRef(number=value) if value > 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.isdigit():
        number = int(text)
        return ProjectRef(number=number) if number > 0 else None
    match = _PROJECT_URL_RE.match(text)
    if match is None:
        return None
    number = int(match.group(3))
    # The same positivity rule the two numeric forms above apply. ``\d+`` happily matches
    # ``0``, and without this a ``/projects/0`` URL loaded cleanly and then failed at the
    # first poll with "was not found" — pushing a typo the loader could have caught into a
    # runtime failure the author sees a minute later, in a different place.
    if number <= 0:
        return None
    return ProjectRef(
        number=number, owner_type=match.group(1), login=match.group(2)
    )



@dataclass(frozen=True, slots=True)
class DispatchConfig:
    """Ordering policy and the per-repository default cap (contracts/config.md).

    ``order`` defaults to ``oldest-first`` because that is the behaviour milestone 003
    already has, and FR-046 requires the previous behaviour to be recoverable by
    configuration alone — making it the default makes that recovery the no-op it should be.

    ``default_repo_max_sessions`` defaults to ``1`` because every collision risk planning
    §6 measured is per-clone: two sessions under one repository share its ports, its dev
    server, and its submodule fetches. A repository that genuinely tolerates two says so.

    ``wait_for_merge`` defaults to ``False`` because it changes when work starts, and an
    installation that never asked for it must not discover the change by watching its
    queue stop. It is the *global* value rather than a default the per-repository setting
    falls back to computing something from — which is why it is not named
    ``default_wait_for_merge``: unlike a session count, there is nothing to default.
    """

    order: str = "oldest-first"
    default_repo_max_sessions: int = 1
    wait_for_merge: bool = False
    #: Whether a cleanly resolvable project board governs dispatch order (issue #48).
    #: ``True`` because FR-019 requires a board that resolves without ambiguity to take
    #: effect without being switched on — discovery being automatic is what the issue
    #: asked for. This is the one default in this file that changes behaviour on upgrade,
    #: which is why ``[repos.*] project_ordering`` exists and why `status` reports the
    #: state rather than leaving it to be noticed.
    project_ordering: bool = True


#: The four things worth saying out loud (contracts/notifications.md). A closed set: an
#: unknown kind is refused at load rather than ignored, because an event the author asked
#: for and never receives is a channel that lies by omission.
VALID_NOTIFICATION_EVENTS: tuple[str, ...] = ("dispatch", "completion", "failure", "needs_info")


@dataclass(frozen=True, slots=True)
class NotificationsConfig:
    """What to say, and at most how often (contracts/notifications.md).

    ``events`` is empty by default, so an unconfigured installation makes no outbound
    request at all (FR-033) — the Operating Constraints' rule for outward-facing actions,
    the same one that sets ``[cleanup] on_issue_close``.

    ``max_per_cycle`` bounds one burst rather than one event. Per-``(kind, item)``
    de-duplication would not bound a backlog, because a backlog produces *different* items —
    the very case that would flood (R15).
    """

    events: tuple[str, ...] = ()
    max_per_cycle: int = 5

    def wants(self, kind: str) -> bool:
        return kind in self.events


@dataclass(frozen=True, slots=True)
class CleanupConfig:
    """Whether a closed issue reclaims its worktree and branch (contracts/cleanup.md).

    ``False`` by default, and not out of caution: the Operating Constraints require
    irreversible and outward-facing actions to be unreachable by default, and removing a
    worktree and deleting a branch are both. ``robot-army cleanup`` runs the same function
    under the same guards whether or not this is on (FR-029), so enabling it changes when
    cleanup happens rather than whether it is possible.
    """

    on_issue_close: bool = False


@dataclass(frozen=True, slots=True)
class SpecKitConfig:
    """Whether a dispatched session into a Spec Kit repository is told so (milestone 007).

    On by default, which is the opposite of ``[cleanup] on_issue_close`` and deliberately
    so: adding a paragraph to a prompt is neither irreversible nor outward-facing, and
    per-repository opt-in would reintroduce exactly the step milestone 005 spent a
    milestone removing (spec.md, Assumptions). The listing that says which repositories
    this changes is the compensation for switching it on by itself.

    It governs the **prompt block only**. Phase observation and the repositories listing
    are reads that cost nothing and mislead no one, so they keep working when this is off —
    which is what makes turning it off a safe experiment rather than a trade.

    ``commands`` is milestone 039: what each lifecycle command is invoked with, keyed by
    bare command name. Only non-empty values are ever present — globally an empty
    instruction says exactly what omitting it says, so it is refused at parse time rather
    than stored as a state with no meaning. The repository form is different; see
    :attr:`RepoConfig.speckit_commands`.
    """

    enabled: bool = True
    commands: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandInstruction:
    """One resolved Spec Kit lifecycle instruction, and the setting that produced it.

    ``text`` is never empty: a command whose effective instruction resolves to nothing is
    omitted from :meth:`Config.speckit_commands_for`'s result entirely, rather than carried
    as a present-but-blank entry that every consumer would then have to remember to skip.

    ``source`` is written to be quoted verbatim into a record or a listing —
    ``[speckit.commands] implement`` or ``[repos."jantman/x".speckit_commands] implement``.
    It exists for the same reason ``speckit_enabled_for`` returns its provenance: two
    callers need it, and computing it separately at each is how they come to disagree.
    """

    command: str
    text: str
    source: str


@dataclass(frozen=True, slots=True)
class WebConfig:
    """``robot-army serve``'s three settings (research.md R13).

    The default is loopback deliberately: under FR-003 the bind address *is* the access
    policy, so an unconfigured install must not be reachable from the network. Widening it
    is an explicit edit, and the server announces the effective address at startup.
    """

    bind: str = "127.0.0.1"
    port: int = 8420
    refresh_seconds: int = 10


@dataclass(frozen=True, slots=True)
class Config:
    path: Path
    daemon: DaemonConfig
    github: GitHubConfig
    worker: WorkerConfig
    terminal: TerminalConfig
    health: HealthConfig
    hooks: HooksConfig
    web: WebConfig
    dispatch: DispatchConfig
    cleanup: CleanupConfig
    notifications: NotificationsConfig
    speckit: SpecKitConfig
    repos: dict[str, RepoConfig]
    worktree_root: Path
    #: Where clones live. A repository's default location is ``<repo_root>/<name>`` and
    #: there is exactly one candidate — no search path, no ``<owner>/<name>`` fallback
    #: (milestone 005, contracts/config.md). Validated at load, so "your root is missing"
    #: is one message rather than one per repository (FR-001).
    repo_root: Path
    layout: Layout
    #: ``None`` when no ``[trello]`` section exists, which is the default and means the
    #: board source is inert — not merely disabled at a call site (FR-001).
    trello: TrelloConfig | None = None
    #: ``None`` when no ``[pushover]`` section exists — the default, and the same
    #: inert-when-absent shape ``trello`` has. Nothing is built, so no request to Pushover
    #: is ever constructed.
    pushover: PushoverConfig | None = None
    warnings: tuple[str, ...] = ()

    def repo(self, key: str) -> RepoConfig:
        try:
            return self.repos[key]
        except KeyError:
            raise KeyError(f"no [repos.{key}] section in {self.path}") from None

    def permission_mode_for(self, key: str) -> str:
        repo = self.repos.get(key)
        if repo and repo.permission_mode:
            return repo.permission_mode
        return self.worker.permission_mode

    def model_for(self, key: str) -> str:
        repo = self.repos.get(key)
        if repo and repo.model:
            return repo.model
        return self.worker.model

    def speckit_enabled_for(self, key: str) -> tuple[bool, str | None]:
        """Does this repository get the Spec Kit prompt block, and what decided it?

        Returns the answer **and** its provenance, because two callers need the second
        half: the audit record must name what suppressed a dispatch (FR-011) and the
        repositories listing must say the same thing in a column (FR-022). Computing the
        reason separately at each site is how the two come to disagree.

        ``None`` as the provenance means the default decided it — nothing was written down.
        """
        repo = self.repos.get(key)
        if repo is not None and repo.speckit is not None:
            return repo.speckit, f'[repos."{key}"] speckit'
        if not self.speckit.enabled:
            return False, "[speckit] enabled"
        return True, None

    def speckit_commands_for(self, key: str) -> tuple[CommandInstruction, ...]:
        """What each lifecycle command is invoked with here, and what said so (039).

        Returns only the commands that end up with text, **in lifecycle order** rather than
        in either table's insertion order (FR-011). Ordering here rather than in the
        renderer puts the guarantee at the one place it can be tested once, instead of at
        every consumer that might get it wrong.

        Each entry carries its provenance for the same reason ``speckit_enabled_for``
        returns its own: two callers need it — the ``speckit.detect`` audit record and the
        repositories listing — and computing it separately at each site is how the two come
        to disagree.

        Derived on every call and never cached. There is nothing to cache but a
        four-element tuple, and a cache would be a second answer about a file the
        maintainer edits by hand.

        Never raises: everything here survived validation at parse time.
        """
        repo = self.repos.get(key)
        overrides = repo.speckit_commands if repo is not None else {}
        resolved: list[CommandInstruction] = []
        for command in LIFECYCLE:
            if command in overrides:
                # Present-and-empty is an override to *nothing*, which is why this branch
                # is taken on membership rather than on truthiness (research R5).
                text = overrides[command]
                source = f'[repos."{key}".speckit_commands] {command}'
            else:
                text = self.speckit.commands.get(command, "")
                source = f"[speckit.commands] {command}"
            if text:
                resolved.append(
                    CommandInstruction(command=command, text=text, source=source)
                )
        return tuple(resolved)

    def effective_project_ordering(self, key: str) -> tuple[bool, bool]:
        """Whether this repository's order comes from its board, and who said so.

        Returns ``(value, explicit)``, shaped exactly like :meth:`effective_wait_for_merge`
        and for the same second-element reason: a surface reporting that a board governs
        should be able to tell the author whether they chose that or inherited it, which
        is the difference between a setting they can find and one they cannot.

        Note what this does **not** answer. It says whether the author permits board
        ordering, not whether a board was found or has ever been read — those live in
        ``repo_projects`` and are the FR-014 gate. All three must hold before anything is
        ordered or held.
        """
        repo = self.repos.get(key)
        if repo is not None and repo.project_ordering is not None:
            return repo.project_ordering, True
        return self.dispatch.project_ordering, False

    def effective_repo_cap(self, key: str, *, ceiling: int | None = None) -> tuple[int, bool]:
        """How many sessions this repository may run, and whether the author said so.

        Returns ``(cap, explicit)``. The cap is the lower of the repository's own setting
        and the global one, because a per-repository cap above the global cap is an
        over-specification that resolves cleanly rather than a contradiction worth refusing
        to start over (R17) — the config loader has already warned about it.

        The second half of the tuple exists for US2's fourth scenario: a surface reporting
        "1 of 1" should be able to say whether that 1 was chosen or merely inherited, so the
        author knows which file to edit.

        ``ceiling`` is the global cap to clamp against, and exists because *this object's*
        global cap is a claim about the configuration this process read at startup rather
        than about what is being enforced (issue #30). A surface holding a capacity snapshot
        passes ``snapshot.global_cap``, so a repository's limit is clamped by the same
        number the surface is printing — otherwise a page could read ``1/3 sessions`` above
        a row held for a repository cap that only exists because this process still thinks
        the machine allows one session. It defaults to this object's own cap, which is what
        the daemon's snapshot carries anyway, so nothing about dispatch changes.
        """
        repo = self.repos.get(key)
        explicit = repo is not None and repo.max_sessions is not None
        requested = (
            repo.max_sessions
            if repo is not None and repo.max_sessions is not None
            else self.dispatch.default_repo_max_sessions
        )
        if ceiling is None:
            ceiling = self.daemon.max_concurrent_sessions
        return min(requested, ceiling), explicit

    def effective_wait_for_merge(self, key: str) -> tuple[bool, bool]:
        """Whether this repository waits for its work to land, and whether the author said so.

        Returns ``(value, explicit)``, shaped exactly like :meth:`effective_repo_cap` and
        for the same second-element reason: a surface saying "waiting for #41" should be
        able to tell the author which file to edit to stop waiting.

        There is deliberately no ``min()`` counterpart. ``[daemon] max_concurrent_sessions``
        is a machine-wide *ceiling* that a per-repository count could over-specify, so the
        cap has two numbers to reconcile; ``[dispatch] wait_for_merge`` is simply the same
        boolean at a wider scope, and a repository that overrides it is not exceeding
        anything.
        """
        repo = self.repos.get(key)
        if repo is not None and repo.wait_for_merge is not None:
            return repo.wait_for_merge, True
        return self.dispatch.wait_for_merge, False


# -- loading ---------------------------------------------------------------

_TOP_LEVEL_SECTIONS = {
    "daemon",
    "paths",
    "github",
    "worker",
    "terminal",
    "health",
    "hooks",
    "web",
    "trello",
    "dispatch",
    "cleanup",
    "notifications",
    "speckit",
    "pushover",
    "repos",
}

#: Sections where an unknown key is a **problem** rather than the top level's warning.
#: The rule is the one ``[repos.*]`` established and this file states above: a typo in a
#: section that exists is a setting that quietly does nothing, which is worse than a
#: setting that is missing, because it looks applied.
_STRICT_KEY_SECTIONS = frozenset(
    {"trello", "dispatch", "cleanup", "notifications", "speckit", "pushover"}
)

_KNOWN_KEYS: dict[str, set[str]] = {
    "daemon": {
        "effect_level",
        "tick_seconds",
        "poll_seconds",
        "reconcile_seconds",
        "dispatching_max_age_seconds",
        "confirm_timeout_seconds",
        "max_concurrent_sessions",
    },
    "paths": {"worktree_root", "repo_root", "state_dir", "socket_dir"},
    "github": {
        "author",
        "label",
        "token_env",
        "token_file",
        "include_owned",
        "extra_repos",
        "timeout_seconds",
        "max_retries",
        "api_base",
    },
    "worker": {"permission_mode", "model", "base_branch", "branch_prefix", "binary"},
    "terminal": {"socket_glob", "probe_timeout_seconds", "binary"},
    "health": {"max_age_seconds", "webhook_url"},
    "hooks": {"default_timeout_seconds", "post_create"},
    "web": {"bind", "port", "refresh_seconds"},
    # Unknown keys here are an **error** too — see _STRICT_KEY_SECTIONS. An ordering the
    # author thought they configured and did not is the failure this prevents.
    "dispatch": {
        "order",
        "default_repo_max_sessions",
        "wait_for_merge",
        "project_ordering",
    },
    "cleanup": {"on_issue_close"},
    "notifications": {"events", "max_per_cycle"},
    "speckit": {"enabled", "commands"},
    # Unknown keys here are an **error** too, for the reason ``[trello]`` states: a typo in
    # a section that exists is a credential that quietly never loads.
    "pushover": {"token_file", "user_key_file"},
    # Unknown keys here are an **error**, not the top level's warning, and are handled
    # separately below: a typo in a board section that exists is a board that quietly
    # polls the wrong thing, which is the same class of failure as a typo in [repos.*].
    "trello": {
        "board_id",
        "label",
        "in_progress_list",
        "done_list",
        "ignore_lists",
        "poll_seconds",
        "timeout_seconds",
        "max_retries",
        "api_base",
        "key_env",
        "key_file",
        "token_env",
        "token_file",
    },
}

_REPO_KEYS = {
    "path",
    "speckit",
    "speckit_commands",
    "base_branch",
    "post_create",
    "env",
    "permission_mode",
    "model",
    "max_sessions",
    "priority",
    "wait_for_merge",
    "project_ordering",
    "project",
    "project_column",
}
_STEP_KEYS = {"run", "link", "copy", "timeout"}


def _parse_speckit_commands(
    raw: Any,
    *,
    table_label: str,
    prefix: str,
    allow_empty: bool,
    problems: list[str],
) -> dict[str, str]:
    """Validate one table of Spec Kit lifecycle instructions (milestone 039).

    Shared by the global ``[speckit.commands]`` and each repository's
    ``speckit_commands``, which differ in exactly one rule and are otherwise identical —
    so the difference is a parameter rather than a second copy of five checks.

    ``allow_empty`` is that rule. Globally an empty instruction and an absent one are the
    same state, so an empty one says nothing that omitting it does not and is a mistake
    worth reporting. In a repository section they are *different* states — absent inherits
    the global, empty overrides it with nothing — so there empty is the only way to drop
    one instruction without ``speckit = false`` removing the whole guidance block
    (research R5, FR-025).

    Text is stripped of surrounding whitespace and otherwise untouched: TOML's multi-line
    string form leaves a trailing newline that would render as a blank line, and
    ``prompt.compose`` already strips the issue body and the repository's own instructions
    for the same reason. Everything *inside* the text — markdown, backticks, quotation
    marks, blank lines between paragraphs — is carried exactly as written (FR-009).

    Never raises. Every failure becomes a problem appended to ``problems``, so a file with
    three mistakes reports three (FR-006, FR-028).
    """
    if not isinstance(raw, dict):
        problems.append(f"{table_label} must be a table")
        return {}

    parsed: dict[str, str] = {}
    for name in sorted(raw):
        if name not in LIFECYCLE:
            problems.append(
                f"{prefix} unknown command {name!r}; "
                f"valid commands are {', '.join(LIFECYCLE)}"
            )
            continue
        value = raw[name]
        if not isinstance(value, str):
            problems.append(f"{prefix} {name} must be a string, got {value!r}")
            continue
        if len(value) > MAX_INSTRUCTION_CHARS:
            problems.append(
                f"{prefix} {name} is {len(value)} characters; "
                f"the limit is {MAX_INSTRUCTION_CHARS}"
            )
            continue
        text = value.strip()
        if not text and not allow_empty:
            problems.append(f"{prefix} {name} is empty; omit the key instead")
            continue
        parsed[name] = text
    return parsed


def load(path: Path | None = None) -> Config:
    """Read and validate the config, raising ``ConfigError`` with **every** problem."""
    config_path = Path(path).expanduser() if path else default_config_path()
    if not config_path.exists():
        raise ConfigError([f"config file not found: {config_path}"])
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError([f"{config_path}: TOML parse error: {exc}"]) from exc
    return parse(raw, config_path)


def parse(raw: dict[str, Any], config_path: Path) -> Config:  # noqa: C901 - flat validation
    problems: list[str] = []
    warnings: list[str] = []

    for section in raw:
        if section not in _TOP_LEVEL_SECTIONS:
            warnings.append(f"unknown top-level section [{section}] — ignored")
    for section, known in _KNOWN_KEYS.items():
        if section in _STRICT_KEY_SECTIONS:
            continue  # an error rather than a warning; see each section's block below
        for key in raw.get(section, {}):
            if key not in known:
                warnings.append(f"unknown key [{section}].{key} — ignored")

    def _int(section: str, key: str, default: int, *, minimum: int | None = None) -> int:
        value = raw.get(section, {}).get(key, default)
        if not isinstance(value, int) or isinstance(value, bool):
            problems.append(f"[{section}] {key} must be an integer, got {value!r}")
            return default
        if minimum is not None and value < minimum:
            problems.append(f"[{section}] {key} must be >= {minimum}, got {value}")
            return default
        return value

    def _str(section: str, key: str, default: str) -> str:
        value = raw.get(section, {}).get(key, default)
        if not isinstance(value, str):
            problems.append(f"[{section}] {key} must be a string, got {value!r}")
            return default
        return value

    # -- [daemon] ----------------------------------------------------------
    daemon_raw = raw.get("daemon", {})
    level_name = daemon_raw.get("effect_level", "live")
    try:
        effect_level = EffectLevel(level_name)
    except ValueError:
        problems.append(
            f"[daemon] effect_level must be one of "
            f"{', '.join(lvl.value for lvl in EffectLevel)}; got {level_name!r}"
        )
        effect_level = EffectLevel.LIVE

    tick = _int("daemon", "tick_seconds", 5, minimum=1)
    poll = _int("daemon", "poll_seconds", 60, minimum=1)
    reconcile = _int("daemon", "reconcile_seconds", 60, minimum=1)
    if poll < tick:
        problems.append(f"[daemon] poll_seconds ({poll}) must be >= tick_seconds ({tick})")
    if reconcile < tick:
        problems.append(
            f"[daemon] reconcile_seconds ({reconcile}) must be >= tick_seconds ({tick})"
        )
    max_age = _int("daemon", "dispatching_max_age_seconds", 900, minimum=1)
    confirm = _int("daemon", "confirm_timeout_seconds", 45, minimum=1)
    concurrency = _int("daemon", "max_concurrent_sessions", 2, minimum=1)

    daemon = DaemonConfig(
        effect_level=effect_level,
        tick_seconds=tick,
        poll_seconds=poll,
        reconcile_seconds=reconcile,
        dispatching_max_age_seconds=max_age,
        confirm_timeout_seconds=confirm,
        max_concurrent_sessions=concurrency,
    )

    # -- [github] ----------------------------------------------------------
    gh_raw = raw.get("github", {})
    author = _str("github", "author", "")
    if not author.strip():
        # FR-007 calls this a security boundary. There is deliberately no "any author"
        # value and no way to disable it, so an empty value is a hard error.
        problems.append(
            "[github] author must be set — it is the FR-007 security boundary and "
            "cannot be disabled or left blank"
        )

    token_env = gh_raw.get("token_env")
    token_file_raw = gh_raw.get("token_file")
    if bool(token_env) == bool(token_file_raw):
        problems.append("[github] exactly one of token_env or token_file must be set")

    for key, value in gh_raw.items():
        if isinstance(value, str) and _looks_like_token(value):
            problems.append(
                f"[github] {key} appears to contain a literal credential. "
                "Tokens must come from token_env or a mode-0600 token_file, never this "
                "file — the repository is public"
            )

    token_file: Path | None = None
    if token_file_raw:
        token_file = Path(str(token_file_raw)).expanduser()
        if not token_file.exists():
            problems.append(f"[github] token_file does not exist: {token_file}")
        else:
            mode = stat.S_IMODE(token_file.stat().st_mode)
            if mode & 0o077:
                problems.append(
                    f"[github] token_file must be mode 0600, found {mode:04o}: {token_file}"
                )

    extra_repos_raw = gh_raw.get("extra_repos", [])
    if not isinstance(extra_repos_raw, list) or any(
        not isinstance(r, str) for r in extra_repos_raw
    ):
        problems.append("[github] extra_repos must be a list of strings")
        extra_repos_raw = []

    github = GitHubConfig(
        author=author,
        label=_str("github", "label", "robot-army"),
        token_env=str(token_env) if token_env else None,
        token_file=token_file,
        include_owned=bool(gh_raw.get("include_owned", True)),
        extra_repos=tuple(extra_repos_raw),
        timeout_seconds=_int("github", "timeout_seconds", 20, minimum=1),
        max_retries=_int("github", "max_retries", 4, minimum=0),
        api_base=_str("github", "api_base", "https://api.github.com").rstrip("/"),
    )

    # -- [worker] ----------------------------------------------------------
    permission_mode = _str("worker", "permission_mode", "auto")
    if permission_mode not in VALID_PERMISSION_MODES:
        problems.append(
            f"[worker] permission_mode must be one of {', '.join(VALID_PERMISSION_MODES)}; "
            f"got {permission_mode!r}"
        )
    worker = WorkerConfig(
        permission_mode=permission_mode,
        model=_str("worker", "model", ""),
        base_branch=_str("worker", "base_branch", ""),
        branch_prefix=_str("worker", "branch_prefix", "robot-army"),
        binary=_str("worker", "binary", "claude"),
    )

    # -- [terminal] --------------------------------------------------------
    socket_glob = _str("terminal", "socket_glob", default_socket_glob())
    if "*" not in socket_glob and "?" not in socket_glob:
        # kitty appends its PID to listen_on, so a fixed path can only ever be stale.
        warnings.append(
            f"[terminal] socket_glob {socket_glob!r} contains no wildcard; kitty appends "
            "its PID to listen_on, so this will not match a live socket after a restart"
        )
    unsafe_root = _unsafe_socket_root(socket_glob)
    if unsafe_root is not None:
        # A warning rather than an error, deliberately (RA-15). Discovery already refuses
        # a candidate it does not own, so the daemon is safe with this setting; refusing
        # to load would stop it on a machine configured exactly as the README used to
        # say, and a fix that demands an edit before the daemon runs again is a fix that
        # gets reverted.
        warnings.append(
            f"[terminal] socket_glob {socket_glob!r} is rooted somewhere another local "
            f"user could place a socket: {unsafe_root}. The daemon refuses any candidate "
            "it does not own, before and while it uses one, so nothing there will be "
            "dispatched to — but it can leave the daemon with no socket at all. Prefer "
            "$XDG_RUNTIME_DIR/mykitty-* and the matching "
            "`listen_on unix:${XDG_RUNTIME_DIR}/mykitty` in kitty.conf"
        )
    terminal = TerminalConfig(
        socket_glob=socket_glob,
        probe_timeout_seconds=_int("terminal", "probe_timeout_seconds", 2, minimum=1),
        binary=_str("terminal", "binary", "kitty"),
    )

    health = HealthConfig(
        max_age_seconds=_int("health", "max_age_seconds", 180, minimum=1),
        webhook_url=_str("health", "webhook_url", ""),
    )
    hooks = HooksConfig(
        default_timeout_seconds=_int("hooks", "default_timeout_seconds", 300, minimum=1)
    )
    # Parsed by the same ``_parse_steps`` the per-repository form uses, so it inherits the
    # same shape, the same per-step key validation, and the same ``default_timeout_seconds``
    # for a step that sets none. A second parser would be a second set of rules to keep in
    # step with the first.
    shared_steps, shared_problems = _parse_steps(
        None, raw.get("hooks", {}).get("post_create", []), hooks
    )
    problems.extend(shared_problems)
    hooks = replace(hooks, post_create=tuple(shared_steps))

    # -- [web] -------------------------------------------------------------
    # The bind address is only *parsed* here; whether it is permitted is decided at
    # startup by web.server.validate_bind, because refusing a globally routable address
    # is a serving decision and `robot-army status` must not fail over it.
    web = WebConfig(
        bind=_str("web", "bind", "127.0.0.1"),
        port=_int("web", "port", 8420, minimum=1),
        refresh_seconds=_int("web", "refresh_seconds", 10, minimum=1),
    )
    if web.port > 65535:
        problems.append(f"[web] port must be <= 65535, got {web.port}")

    # -- [dispatch] --------------------------------------------------------
    dispatch_raw = raw.get("dispatch", {})
    for unknown in sorted(set(dispatch_raw) - _KNOWN_KEYS["dispatch"]):
        problems.append(f"[dispatch] unknown key {unknown!r}")
    order = _str("dispatch", "order", "oldest-first")
    if order not in VALID_ORDER_MODES:
        # A problem rather than a warning (R17, FR-014). Falling back silently would run
        # the author's work in an order they did not choose and would not know about,
        # which is the one contradiction here that cannot be resolved by taking a side.
        problems.append(
            f"[dispatch] order must be one of {', '.join(VALID_ORDER_MODES)}; got {order!r}"
        )
        order = "oldest-first"
    wait_for_merge = dispatch_raw.get("wait_for_merge", False)
    if not isinstance(wait_for_merge, bool):
        problems.append(
            f"[dispatch] wait_for_merge must be true or false, got {wait_for_merge!r}"
        )
        wait_for_merge = False
    project_ordering = dispatch_raw.get("project_ordering", True)
    if not isinstance(project_ordering, bool):
        problems.append(
            f"[dispatch] project_ordering must be true or false, got {project_ordering!r}"
        )
        project_ordering = True
    dispatch = DispatchConfig(
        order=order,
        default_repo_max_sessions=_int("dispatch", "default_repo_max_sessions", 1, minimum=1),
        wait_for_merge=wait_for_merge,
        project_ordering=project_ordering,
    )

    # -- [cleanup] ---------------------------------------------------------
    cleanup_raw = raw.get("cleanup", {})
    for unknown in sorted(set(cleanup_raw) - _KNOWN_KEYS["cleanup"]):
        problems.append(f"[cleanup] unknown key {unknown!r}")
    on_issue_close = cleanup_raw.get("on_issue_close", False)
    if not isinstance(on_issue_close, bool):
        problems.append(
            f"[cleanup] on_issue_close must be true or false, got {on_issue_close!r}"
        )
        on_issue_close = False
    cleanup = CleanupConfig(on_issue_close=on_issue_close)

    # -- [notifications] ---------------------------------------------------
    notify_raw = raw.get("notifications", {})
    for unknown in sorted(set(notify_raw) - _KNOWN_KEYS["notifications"]):
        problems.append(f"[notifications] unknown key {unknown!r}")
    events_raw = notify_raw.get("events", [])
    events: tuple[str, ...] = ()
    if not isinstance(events_raw, list) or any(not isinstance(e, str) for e in events_raw):
        problems.append("[notifications] events must be a list of strings")
    else:
        unknown_kinds = [e for e in events_raw if e not in VALID_NOTIFICATION_EVENTS]
        if unknown_kinds:
            # A problem rather than a warning: silently ignoring a kind means an event the
            # author asked for never arrives, and a channel that is silent for the wrong
            # reason is worse than no channel.
            problems.append(
                f"[notifications] unknown event kind(s) {', '.join(sorted(unknown_kinds))}; "
                f"valid kinds are {', '.join(VALID_NOTIFICATION_EVENTS)}"
            )
        events = tuple(dict.fromkeys(events_raw))
    notifications = NotificationsConfig(
        events=events,
        max_per_cycle=_int("notifications", "max_per_cycle", 5, minimum=1),
    )
    # -- [speckit] ---------------------------------------------------------
    speckit_raw = raw.get("speckit", {})
    for unknown in sorted(set(speckit_raw) - _KNOWN_KEYS["speckit"]):
        problems.append(f"[speckit] unknown key {unknown!r}")
    speckit_enabled = speckit_raw.get("enabled", True)
    if not isinstance(speckit_enabled, bool):
        problems.append(
            f"[speckit] enabled must be true or false, got {speckit_enabled!r}"
        )
        speckit_enabled = True
    speckit_commands = _parse_speckit_commands(
        speckit_raw.get("commands", {}),
        table_label="[speckit] commands",
        prefix="[speckit.commands]",
        allow_empty=False,
        problems=problems,
    )
    speckit = SpecKitConfig(enabled=speckit_enabled, commands=speckit_commands)

    if notifications.events and not health.webhook_url and "pushover" not in raw:
        # A warning, not a problem: the intent is legible and the fix is obvious, and
        # refusing to start over a stretch feature would be disproportionate (R17).
        #
        # Either channel satisfies this (FR-015). Tested against the raw section rather
        # than the parsed ``pushover`` object on purpose: a ``[pushover]`` section that
        # failed validation has already produced a problem, and adding "and by the way
        # nothing can be sent" to it would be a second message about one mistake.
        #
        # That holds only because *every* malformed ``[pushover]`` section is a problem,
        # including a bare header — see ``_parse_pushover``. If that check is ever loosened,
        # this suppression turns into silence.
        warnings.append(
            "[notifications] events are configured but no notification channel is set, so "
            "nothing can be sent; set [health] webhook_url or [pushover], or clear the "
            "events"
        )

    # -- [trello] ----------------------------------------------------------
    # Absent by default. ``None`` here is what FR-001 means by inert: there is no section
    # to read, so no board request is ever constructed — as against a configured-but-empty
    # section that every call site would have to remember to skip.
    trello: TrelloConfig | None = None
    if "trello" in raw:
        trello = _parse_trello(raw["trello"], problems)

    # -- [pushover] --------------------------------------------------------
    # Absent by default, and ``None`` means the same thing it means for ``[trello]``: not
    # "disabled at a call site" but "there is nothing to build", so ``channels.build``
    # cannot construct a Pushover request by accident.
    pushover: PushoverConfig | None = None
    if "pushover" in raw:
        pushover = _parse_pushover(raw["pushover"], problems)

    # -- [paths] -----------------------------------------------------------
    paths_raw = raw.get("paths", {})
    worktree_root = Path(str(paths_raw.get("worktree_root", "~/worktrees"))).expanduser()
    # Validated here rather than at onboarding time (FR-001, contracts/config.md): a
    # missing clone root is one fact about the machine, so it is one message reported
    # alongside every other configuration problem — not 227 identical refusals discovered
    # one repository at a time.
    repo_root = Path(str(paths_raw.get("repo_root", "~/GIT"))).expanduser()
    if not repo_root.exists():
        problems.append(f"[paths] repo_root does not exist: {repo_root}")
    elif not repo_root.is_dir():
        problems.append(f"[paths] repo_root is not a directory: {repo_root}")
    layout = Layout.build(
        state_dir=Path(str(paths_raw["state_dir"])).expanduser()
        if paths_raw.get("state_dir")
        else None,
        socket_dir=Path(str(paths_raw["socket_dir"])).expanduser()
        if paths_raw.get("socket_dir")
        else None,
    )

    # -- [repos.*] ---------------------------------------------------------
    repos: dict[str, RepoConfig] = {}
    for key, section in raw.get("repos", {}).items():
        if not isinstance(section, dict):
            problems.append(f"[repos.{key}] must be a table")
            continue
        for unknown in set(section) - _REPO_KEYS:
            # An error, not a warning: a typo here silently disables a preparation step
            # and produces a broken worktree.
            problems.append(f"[repos.{key}] unknown key {unknown!r}")

        # Optional since milestone 005 (FR-003). Absent means *derive it* from
        # ``[paths] repo_root``; present means *use this and do not derive*. The
        # load-time existence checks are kept for the explicit case only, because they
        # are checks on something the author wrote in this file — a derived path is
        # verified at onboarding instead, where the author is reading an approval screen
        # and a refusal can name the edit that fixes it.
        path_raw = section.get("path")
        repo_path: Path | None = None
        if path_raw:
            repo_path = Path(str(path_raw)).expanduser()
            if not repo_path.exists():
                problems.append(f"[repos.{key}] path does not exist: {repo_path}")
            elif not (repo_path / ".git").exists():
                problems.append(f"[repos.{key}] path is not a git repository: {repo_path}")

        repo_mode = section.get("permission_mode")
        if repo_mode is not None and repo_mode not in VALID_PERMISSION_MODES:
            problems.append(
                f"[repos.{key}] permission_mode must be one of "
                f"{', '.join(VALID_PERMISSION_MODES)}; got {repo_mode!r}"
            )

        steps, step_problems = _parse_steps(key, section.get("post_create", []), hooks)
        problems.extend(step_problems)

        env_raw = section.get("env", {})
        if not isinstance(env_raw, dict):
            problems.append(f"[repos.{key}] env must be a table")
            env_raw = {}
        env = {str(k): str(v) for k, v in env_raw.items()}

        max_sessions = section.get("max_sessions")
        if max_sessions is not None and (
            not isinstance(max_sessions, int)
            or isinstance(max_sessions, bool)
            or max_sessions < 1
        ):
            # A problem rather than a warning: zero would disable the repository silently,
            # which is indistinguishable from a repository nobody labelled anything in.
            problems.append(
                f"[repos.{key}] max_sessions must be a positive integer, got {max_sessions!r}"
            )
            max_sessions = None

        priority = section.get("priority", 0)
        if not isinstance(priority, int) or isinstance(priority, bool):
            problems.append(
                f"[repos.{key}] priority must be an integer, got {priority!r}"
            )
            priority = 0

        repo_wait_for_merge = section.get("wait_for_merge")
        if repo_wait_for_merge is not None and not isinstance(repo_wait_for_merge, bool):
            # ``None`` rather than ``False`` on the failure path: the author wrote
            # *something*, and inheriting is the reading that does not silently pick a side
            # in a config the loader is about to refuse anyway.
            problems.append(
                f"[repos.{key}] wait_for_merge must be true or false, "
                f"got {repo_wait_for_merge!r}"
            )
            repo_wait_for_merge = None

        repo_project_ordering = section.get("project_ordering")
        if repo_project_ordering is not None and not isinstance(
            repo_project_ordering, bool
        ):
            # ``None`` rather than ``False`` on the failure path, for the reason stated
            # three lines up: inheriting does not silently pick a side in a config the
            # loader is about to refuse anyway.
            problems.append(
                f"[repos.{key}] project_ordering must be true or false, "
                f"got {repo_project_ordering!r}"
            )
            repo_project_ordering = None

        repo_project = section.get("project")
        if repo_project is not None:
            if parse_project_reference(repo_project) is None:
                problems.append(
                    f"[repos.{key}] project must be a project number or a "
                    f"https://github.com/{{users,orgs}}/…/projects/N URL, "
                    f"got {repo_project!r}"
                )
                repo_project = None
            else:
                # Stored as text in both forms. The reference is re-parsed where it is
                # used rather than stored decomposed, so the value a surface shows the
                # author is the value they wrote.
                repo_project = str(repo_project).strip()

        repo_project_column = section.get("project_column")
        if repo_project_column is not None:
            if not isinstance(repo_project_column, str) or not repo_project_column.strip():
                problems.append(
                    f"[repos.{key}] project_column must be a non-empty string, "
                    f"got {repo_project_column!r}"
                )
                repo_project_column = None
            else:
                repo_project_column = repo_project_column.strip()

        repo_speckit = section.get("speckit")
        if repo_speckit is not None and not isinstance(repo_speckit, bool):
            problems.append(
                f"[repos.{key}] speckit must be true or false, got {repo_speckit!r}"
            )
            repo_speckit = None

        # ``allow_empty=True`` is the one rule that differs from the global table: here an
        # empty value overrides the global instruction with nothing, which is the only way
        # to drop one instruction in one repository without ``speckit = false`` removing
        # the entire guidance block (research R5, FR-025).
        repo_speckit_commands = _parse_speckit_commands(
            section.get("speckit_commands", {}),
            table_label=f"[repos.{key}] speckit_commands",
            prefix=f'[repos."{key}".speckit_commands]',
            allow_empty=True,
            problems=problems,
        )

        repos[key] = RepoConfig(
            key=key,
            path=repo_path,
            # Not inherited from ``[worker]`` at parse time any more (issue #150). Copying
            # it in made "this repository chose main" and "nobody said anything" the same
            # value, and the resolution order needs to tell them apart. Every consumer
            # already reads the empty string as *inherit*, because all of them are spelled
            # ``repo.base_branch or <the next answer down>``.
            base_branch=str(section.get("base_branch", "")),
            post_create=tuple(steps),
            env=env,
            permission_mode=str(repo_mode) if repo_mode else None,
            model=str(section["model"]) if section.get("model") else None,
            max_sessions=max_sessions,
            priority=priority,
            wait_for_merge=repo_wait_for_merge,
            project_ordering=repo_project_ordering,
            project=repo_project,
            project_column=repo_project_column,
            speckit=repo_speckit,
            speckit_commands=repo_speckit_commands,
        )

    # A cross-field check that is a warning rather than an error, because the maintainer
    # may deliberately want a short leash: FR-041's sweep should not fire before the
    # longest repo's preparation could plausibly finish.
    # Inherited steps count for **every** repository that inherits them, not once
    # (FR-022, research R10). Counting the shared set a single time would under-report for
    # exactly the repositories that have no section — the majority after milestone 005 —
    # and a warning that under-reports for the common case is worse than none.
    shared_total = sum(s.timeout for s in hooks.post_create)
    longest = max(
        (
            sum(s.timeout for s in r.post_create) if r.post_create else shared_total
            for r in repos.values()
        ),
        default=shared_total,
    )
    # The second cross-field check, and a warning for the same reason as the first: it
    # resolves cleanly by taking the minimum, and it is usually a leftover from lowering the
    # global cap rather than a mistake worth refusing to start over (R17).
    for repo_config in repos.values():
        if (
            repo_config.max_sessions is not None
            and repo_config.max_sessions > daemon.max_concurrent_sessions
        ):
            warnings.append(
                f"[repos.{repo_config.key}] max_sessions ({repo_config.max_sessions}) "
                f"exceeds [daemon] max_concurrent_sessions "
                f"({daemon.max_concurrent_sessions}); the effective limit is the lower of "
                "the two"
            )

    if longest and daemon.dispatching_max_age_seconds <= longest:
        warnings.append(
            f"[daemon] dispatching_max_age_seconds ({daemon.dispatching_max_age_seconds}) "
            f"does not exceed the longest repository's preparation timeouts ({longest}s); "
            "reconciliation may fail items that were still legitimately preparing"
        )

    if problems:
        raise ConfigError(problems, warnings)

    return Config(
        path=config_path,
        daemon=daemon,
        github=github,
        worker=worker,
        terminal=terminal,
        health=health,
        hooks=hooks,
        web=web,
        dispatch=dispatch,
        cleanup=cleanup,
        notifications=notifications,
        speckit=speckit,
        repos=repos,
        worktree_root=worktree_root,
        repo_root=repo_root,
        layout=layout,
        trello=trello,
        pushover=pushover,
        warnings=tuple(warnings),
    )


def _parse_pushover(section: Any, problems: list[str]) -> PushoverConfig | None:
    """Parse and validate ``[pushover]``, appending every problem it finds.

    Returns ``None`` when the section could not be understood at all, so a caller holding
    a ``Config`` never sees a half-built channel.
    """
    if not isinstance(section, dict):
        problems.append("[pushover] must be a table")
        return None
    for unknown in sorted(set(section) - _KNOWN_KEYS["pushover"]):
        problems.append(f"[pushover] unknown key {unknown!r}")

    # Both, or neither. An **error** rather than a warning: a half-configured channel
    # cannot send, and a warning would leave a channel that silently never fires — the
    # quiet lie milestone 004's contract argues against (FR-004).
    #
    # "Neither" means *no section*, not an empty one. A bare ``[pushover]`` header, or one
    # whose values are empty strings, is an author who meant to configure the channel and
    # did not finish — and it must not load quietly, because the warning below is suppressed
    # by the section's mere presence. Getting this wrong produced exactly the silence this
    # comment claims to refuse: no channel, no problem, no warning.
    present = [key for key in ("token_file", "user_key_file") if section.get(key)]
    if len(present) != 2:
        found = f" (found only {present[0]})" if present else " (found neither)"
        problems.append(
            f"[pushover] both token_file and user_key_file must be set{found}; "
            "a half-configured channel cannot send"
        )

    for key, value in sorted(section.items()):
        if not isinstance(value, str) or not _looks_like_pushover_credential(value):
            continue
        problems.append(
            f"[pushover] {key} appears to contain a literal credential. Credentials must "
            "come from a mode-0600 file, never this file — the repository is public"
        )

    paths: dict[str, Path | None] = {}
    for key in ("token_file", "user_key_file"):
        paths[key] = _secret_file("pushover", key, section.get(key), problems)

    token_file, user_key_file = paths["token_file"], paths["user_key_file"]
    if token_file is None or user_key_file is None:
        return None
    return PushoverConfig(token_file=token_file, user_key_file=user_key_file)


def _trello_credential(section: dict[str, Any], what: str, problems: list[str]) -> Path | None:
    """Validate one ``key``/``token`` pair and return its file path if one was given.

    Split out of :func:`_parse_trello` because it is the same three checks twice — exactly
    one source, the file exists, the file is not readable by anyone else — and inlining
    both copies made the enclosing function's shape unreadable.
    """
    env_key, file_key = f"{what}_env", f"{what}_file"
    raw_file = section.get(file_key)
    if bool(section.get(env_key)) == bool(raw_file):
        problems.append(f"[trello] exactly one of {env_key} or {file_key} must be set")
    return _secret_file("trello", file_key, raw_file, problems)


def _secret_file(section_name: str, key: str, raw: Any, problems: list[str]) -> Path | None:
    """The two checks every credential file gets: it exists, and nobody else can read it.

    Shared by ``[trello]`` and ``[pushover]`` (milestone 106, research.md R6) rather than
    copied, so a third credential file gains a caller instead of a third opportunity to get
    the permission mask subtly wrong. ``[trello]``'s extra rule — exactly one of ``*_env``
    or ``*_file`` — stays with its caller, because Pushover has no ``*_env`` form.

    Returns the path even when it fails a check, so the caller can report every problem
    about it at once rather than one per load.
    """
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if not path.exists():
        problems.append(f"[{section_name}] {key} does not exist: {path}")
        return path
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        problems.append(
            f"[{section_name}] {key} must be mode 0600, found {mode:04o}: {path}"
        )
    return path


def _parse_trello(section: Any, problems: list[str]) -> TrelloConfig | None:
    """Parse and validate ``[trello]``, appending every problem it finds.

    Unknown keys are an **error** here rather than the top level's warning, for the same
    reason they are inside ``[repos.*]``: a typo in a section that exists silently changes
    what the system does — polls the wrong board, looks for a tag nobody applies — and
    looks healthy while doing it.

    Returns ``None`` when the section could not be understood at all, so a caller holding
    a ``Config`` never sees a half-built board.
    """
    if not isinstance(section, dict):
        problems.append("[trello] must be a table")
        return None

    for unknown in sorted(set(section) - _KNOWN_KEYS["trello"]):
        problems.append(f"[trello] unknown key {unknown!r}")

    def _text(key: str, default: str) -> str:
        value = section.get(key, default)
        if not isinstance(value, str):
            problems.append(f"[trello] {key} must be a string, got {value!r}")
            return default
        return value

    def _number(key: str, default: int, *, minimum: int) -> int:
        value = section.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool):
            problems.append(f"[trello] {key} must be an integer, got {value!r}")
            return default
        if value < minimum:
            problems.append(f"[trello] {key} must be >= {minimum}, got {value}")
            return default
        return value

    def _names(key: str) -> tuple[str, ...]:
        """A list of column names, deduplicated, in the order the author wrote them.

        ``dict.fromkeys`` rather than a set for the ordering ``TrelloConfig`` explains,
        and the same call ``[notifications] events`` already uses — one convention for
        "a list of names, written once each" rather than two.
        """
        value = section.get(key, [])
        if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
            problems.append(f"[trello] {key} must be a list of strings, got {value!r}")
            return ()
        if any(not name for name in value):
            # Not stripped and not skipped: an empty entry cannot match a board column,
            # so accepting it would mean the author configured an exclusion that silently
            # excludes nothing — the exact failure the startup check exists to catch.
            problems.append(f"[trello] {key} contains an empty column name")
            return ()
        return tuple(dict.fromkeys(value))

    board_id = _text("board_id", "")
    if not board_id.strip():
        problems.append(
            "[trello] board_id is required when the section is present — there is no "
            "default board, and guessing one would poll somebody else's"
        )

    # The same shape [github] uses: the *name* of the variable goes in the file, never
    # the value. The repository is public (Principle V).
    #
    # A list's *elements* are swept too, not only plain string values. The sweep predates
    # any list-valued key in this section, so it only ever looked at strings — and when
    # ``ignore_lists`` arrived it became a hole the choke point was blind to rather than a
    # key it covered. A review caught it. Anything added here that can hold a string must
    # be reachable from this loop, or the guard silently stops guarding.
    for key, raw in section.items():
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            if not (isinstance(value, str) and _looks_like_token(value)):
                continue
            problems.append(
                f"[trello] {key} appears to contain a literal credential. Credentials must "
                "come from key_env/token_env or a mode-0600 key_file/token_file, never this "
                "file — the repository is public"
            )
            break  # one problem per key, however many elements carry a secret

    files = {
        f"{what}_file": _trello_credential(section, what, problems) for what in ("key", "token")
    }

    return TrelloConfig(
        board_id=board_id,
        label=_text("label", "AI-task"),
        in_progress_list=_text("in_progress_list", "In Progress"),
        done_list=_text("done_list", "Done"),
        ignore_lists=_names("ignore_lists"),
        poll_seconds=_number("poll_seconds", 300, minimum=1),
        timeout_seconds=_number("timeout_seconds", 20, minimum=1),
        max_retries=_number("max_retries", 4, minimum=0),
        api_base=_text("api_base", "https://api.trello.com/1").rstrip("/"),
        key_env=str(section["key_env"]) if section.get("key_env") else None,
        key_file=files["key_file"],
        token_env=str(section["token_env"]) if section.get("token_env") else None,
        token_file=files["token_file"],
    )


def _parse_steps(
    repo_key: str | None, raw_steps: Any, hooks: HooksConfig
) -> tuple[list[HookStep], list[str]]:
    """Parse an array of preparation steps. ``repo_key`` of ``None`` is ``[hooks]``.

    One function for both forms, so a typo inside a shared step is the same **problem** the
    per-repository form already gives rather than a warning invented separately here.
    """
    section = f"[repos.{repo_key}]" if repo_key is not None else "[hooks]"
    problems: list[str] = []
    steps: list[HookStep] = []
    if not isinstance(raw_steps, list):
        return [], [f"{section} post_create must be an array of tables"]
    for index, step in enumerate(raw_steps):
        where = f"{section} post_create[{index}]"
        if not isinstance(step, dict):
            problems.append(f"{where} must be a table")
            continue
        for unknown in set(step) - _STEP_KEYS:
            problems.append(f"{where} unknown key {unknown!r}")
        forms = [k for k in ("run", "link", "copy") if k in step]
        if len(forms) != 1:
            problems.append(f"{where} must set exactly one of run, link, copy; found {forms}")
            continue
        kind = forms[0]
        value = step[kind]
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{where} {kind} must be a non-empty string")
            continue
        timeout = step.get("timeout", hooks.default_timeout_seconds)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
            problems.append(f"{where} timeout must be a positive integer, got {timeout!r}")
            timeout = hooks.default_timeout_seconds
        steps.append(HookStep(kind=kind, value=value, timeout=timeout))
    return steps, problems


def _looks_like_token(value: str) -> bool:
    return any(pattern.match(value.strip()) for pattern in _TOKEN_PATTERNS)


def _looks_like_pushover_credential(value: str) -> bool:
    """A Pushover application token or user key pasted where a path belongs.

    Deliberately **not** added to :data:`_TOKEN_PATTERNS`. Pushover's credentials are 30
    alphanumeric characters, which matches none of the GitHub prefixes or the 32/64-hex
    Trello shapes — the same gap milestone 003 named for Trello, where the guard could not
    match the credential it guarded and the test that was meant to cover it proved nothing.

    Widening the shared tuple would apply this rule to ``[github]`` and ``[trello]``, where
    a legitimate 30-character alphanumeric value is improbable but possible — and there the
    failure mode is an error the author *cannot* clear. Inside ``[pushover]`` the only
    legitimate values are paths, which carry a separator or an extension, so a false
    positive is not reachable (research.md R6).
    """
    return bool(_PUSHOVER_CREDENTIAL.match(value.strip())) or _looks_like_token(value)
