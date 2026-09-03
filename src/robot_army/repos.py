"""Which repositories this system watches, where their clones are, and what settings apply.

**This module answers questions. It performs no actions.**

That boundary is the whole of its design (plan.md, Structure Decision). Derivation,
URL normalisation, the origin comparison, and the join of record-over-section-over-default
live here as pure functions over ``(conn, config)``. Onboarding's *decision* to record,
dispatch's *decision* to refuse, and every audit write stay at their existing call sites.
The seam therefore adds a place to look things up without adding a place where things
happen — which is what keeps twenty-six call sites from each performing the join by hand.

Milestone 005 moved the answer to "which repositories are known" from the ``[repos.*]``
section keys to the ``repos`` table. A section is no longer evidence of anything except
that overrides exist for a key.

**One warning for whoever reads this next.** :func:`eligibility` reads ``include_owned``
and ``extra_repos`` and decides what may be onboarded. It is a **mistake guard, not a
security boundary** (FR-026): it catches a typo and a wrong owner, and the author can turn
it off by editing their own file, which is exactly what a security boundary cannot allow.
The security boundary is the issue-author check in ``poll.evaluate`` — it cannot be
disabled, this milestone did not touch it, and nothing here should ever be described as
replacing or supplementing it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from robot_army.boundaries import BoundaryError

if TYPE_CHECKING:
    from robot_army.boundaries import IssueSourceReader, VersionControl
    from robot_army.config import Config, RepoConfig

# -- identity ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepoIdentity:
    """A remote URL reduced to the three things that decide *which* repository it is.

    Lowercased, and free of any ``userinfo@`` component by construction — normalisation
    strips credentials before anything else, so an instance of this class can never carry
    a secret into a record, a message, or a terminal (FR-032).
    """

    host: str
    owner: str
    name: str

    def __str__(self) -> str:
        return f"{self.host}/{self.owner}/{self.name}"


def normalise_remote(url: str) -> RepoIdentity | None:
    """Reduce a git remote URL to ``(host, owner, name)``, or ``None`` if it is not one.

    Accepts the three forms git produces: ``git@host:owner/name.git``,
    ``https://host/owner/name(.git)`` and ``ssh://git@host/owner/name(.git)``. Anything
    that does not parse into exactly that shape returns ``None`` rather than a partial
    answer — a half-parsed identity compared against a repository key would pass or fail
    for reasons nobody could reconstruct (research R2).

    Userinfo is stripped **first**, before the host is even read, because a URL may embed
    credentials (``https://user:token@host/owner/name``) and every downstream use of the
    result — comparison, the audit detail, the approval screen — must be incapable of
    carrying one.
    """
    text = (url or "").strip()
    if not text:
        return None

    if "://" in text:
        parts = urlsplit(text)
        host = parts.hostname or ""  # hostname drops userinfo and the port
        path = parts.path
    else:
        # The scp-like form, ``[user@]host:owner/name``. urlsplit does not understand it,
        # and a Windows-style drive letter cannot occur on this project's one platform.
        head, separator, path = text.partition(":")
        if not separator:
            return None
        host = head.rpartition("@")[2]
        if "/" in host:
            return None

    if not host:
        return None
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) != 2:
        return None
    owner, name = segments
    if name.endswith(".git"):
        name = name[: -len(".git")]
    if not owner or not name:
        return None
    return RepoIdentity(host=host.lower(), owner=owner.lower(), name=name.lower())


def identity_for_key(repo_key: str, api_base: str) -> RepoIdentity | None:
    """The identity a repository key is *expected* to have, given the configured API host.

    The host comes from ``[github] api_base`` rather than being assumed to be
    ``github.com``: a clone of the same ``owner/name`` on a different forge sitting at the
    derived path fails identically to a clone of a different repository, and comparing the
    host costs nothing (research R2).
    """
    owner, separator, name = repo_key.partition("/")
    if not separator or not owner or not name or "/" in name:
        return None
    host = urlsplit(api_base).hostname or ""
    if host.startswith("api."):
        host = host[len("api.") :]
    if not host:
        return None
    return RepoIdentity(host=host.lower(), owner=owner.lower(), name=name.lower())


# -- location ---------------------------------------------------------------


def derive_path(config: Config, repo_key: str) -> Path | None:
    """The one candidate location for a repository's clone: ``<repo_root>/<name>``.

    Exactly one candidate. No search, no walk, no ``<repo_root>/<owner>/<name>`` fallback
    (FR-002). The author's nested grouping directories hold repositories they do not own
    and would not dispatch into, so a search path would be a configuration knob with one
    hypothetical user — and a rule that finds *a* directory named right is exactly the rule
    that finds the wrong repository.

    Returns ``None`` for a key that is not ``owner/name``. Touches no filesystem.
    """
    owner, separator, name = repo_key.partition("/")
    if not separator or not owner or not name or "/" in name:
        return None
    return config.repo_root / name


#: The two answers ``path_source`` can hold, and the phrase each one prints on the
#: approval screen. FR-011 requires the distinction to be visible, because the author
#: needs to know *which file to edit* when the location is wrong.
PATH_SOURCES = {
    "derived": "derived from [paths] repo_root",
    "configured": 'configured in [repos."{key}"]',
}


def describe_source(path_source: str, repo_key: str) -> str:
    template = PATH_SOURCES.get(path_source, "from an unknown source")
    return template.format(key=repo_key)


def locate(config: Config, repo_key: str) -> tuple[Path | None, str]:
    """Where this repository's clone should be, and how that answer was reached.

    The section's ``path`` when one is written, otherwise the single derived candidate.
    A configured path suppresses derivation entirely — but it is verified by exactly the
    same sequence, because a configured path can be wrong as easily as a derived one
    (FR-007).
    """
    section = config.repos.get(repo_key)
    if section is not None and section.path is not None:
        return section.path, "configured"
    return derive_path(config, repo_key), "derived"


def is_primary_clone(path: Path) -> bool:
    """Is this a primary clone rather than a linked worktree?

    A ``stat``, not a subprocess: in a linked worktree ``.git`` is a *file* holding a
    ``gitdir:`` pointer, and in a primary clone it is a directory (research R4). Onboarding
    a linked worktree would produce worktrees-of-worktrees sharing a branch namespace with
    whatever owns the primary.
    """
    return (path / ".git").is_dir()


def is_inside(path: Path, root: Path) -> bool:
    """Is ``path`` the same as, or beneath, ``root``? Lexical; no filesystem access."""
    if path == root:
        return True
    return root in path.parents


def select_remote(vcs: VersionControl, clone_path: Path) -> tuple[str | None, str | None]:
    """Pick the remote that decides identity. Returns ``(remote_name, refusal)``.

    Prefers ``origin``. Falls back to the sole remote when exactly one exists — and the
    caller records *which* was used. Refuses as ambiguous when several exist and none is
    ``origin``.

    Deliberately stricter than ``VersionControl.default_remote``, which may pick
    arbitrarily among several because it is choosing where to *fetch* and any answer is
    serviceable. Identity is not serviceable-with-any-answer: picking arbitrarily would
    make the verdict on *what repository this is* depend on git's ordering (research R3).
    """
    try:
        remotes = vcs.list_remotes(str(clone_path))
    except BoundaryError as exc:
        return None, f"could not read the remotes of {clone_path}: {exc}"
    if not remotes:
        return None, None
    if "origin" in remotes:
        return "origin", None
    if len(remotes) == 1:
        return remotes[0], None
    return None, (
        f"{clone_path} has {len(remotes)} remotes ({', '.join(sorted(remotes))}) and none "
        "is named origin, so which repository this is cannot be decided"
    )


# -- verification -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Verification:
    """What the resolution sequence found, or the one step that refused.

    A *typed* refusal rather than a boolean, because every message in
    contracts/onboarding.md names the path, how it was arrived at, and the edit that fixes
    it — and a boolean forces the caller to reconstruct which of eight steps failed in
    order to say any of that. ``cause`` is the taxonomy token that reaches the audit log
    so a refusal can be counted and compared later; ``refusal`` is what a human reads.
    """

    repo_key: str
    path: Path | None
    path_source: str
    remote: str | None = None
    identity: RepoIdentity | None = None
    owner_verdict: str | None = None
    cause: str | None = None
    refusal: str | None = None

    @property
    def ok(self) -> bool:
        return self.refusal is None

    def verified_line(self) -> str:
        """The approval screen's ``verified:`` value. Never a raw URL (FR-032)."""
        if self.identity is None:
            return "not verified"
        via = f" via {self.remote}" if self.remote else ""
        return f"{self.identity}{via}"


def _describe(identity: RepoIdentity, expected: RepoIdentity) -> str:
    """Name a found identity as briefly as remains unambiguous.

    ``owner/name`` when the host matches — the common case, and the one the author reads
    fastest — and the full ``host/owner/name`` when it does not, because "a different
    forge" is otherwise indistinguishable from "the same repository".
    """
    if identity.host == expected.host:
        return f"{identity.owner}/{identity.name}"
    return str(identity)


def verify(config: Config, repo_key: str, vcs: VersionControl) -> Verification:
    """Steps 3–9 of contracts/onboarding.md: locate the clone and confirm what is in it.

    Stops at the first refusal. Everything here is a read — nothing is written, by this
    function or by anything it calls — so a refusal leaves the machine exactly as it was
    and a success leaves the caller to decide whether to record it.

    Steps 1 and 2, the allowlist and the source-system lookup, are :func:`eligibility`'s
    business: they ask the source system rather than the disk, and keeping them apart
    means every case in here is testable without a network.
    """
    path, path_source = locate(config, repo_key)
    described = describe_source(path_source, repo_key)
    fix = f'set [repos."{repo_key}"] path'

    if path is None:
        return Verification(
            repo_key,
            None,
            path_source,
            cause="malformed_key",
            refusal=(
                f"refusing: {repo_key!r} is not a repository key. Keys are owner/name, "
                'as in [repos."jantman/demo"].'
            ),
        )

    if not path.is_dir():
        return Verification(
            repo_key,
            path,
            path_source,
            cause="no_clone",
            refusal=(
                f"refusing: no clone at {path} ({described}).\n"
                f"          Clone it there, or {fix}."
            ),
        )

    if not is_primary_clone(path):
        return Verification(
            repo_key,
            path,
            path_source,
            cause="linked_worktree",
            refusal=(
                f"refusing: {path} is a linked worktree, not a primary clone.\n"
                "          Worktrees are cut from a primary clone; onboarding this would "
                "nest them."
            ),
        )

    if is_inside(path, config.worktree_root):
        return Verification(
            repo_key,
            path,
            path_source,
            cause="inside_worktree_root",
            refusal=(
                f"refusing: {path} is inside [paths] worktree_root "
                f"({config.worktree_root}).\n"
                "          Two directories that both believe they own a tree is a "
                f"confusion worth avoiding; {fix} to a real clone."
            ),
        )

    remote, ambiguous = select_remote(vcs, path)
    if ambiguous is not None:
        return Verification(
            repo_key,
            path,
            path_source,
            cause="ambiguous_remote",
            refusal=(
                f"refusing: {ambiguous}.\n"
                "          Name one of them origin, or point [repos] path at a clone that "
                "has one."
            ),
        )
    if remote is None:
        return Verification(
            repo_key,
            path,
            path_source,
            cause="no_remote",
            refusal=(
                f"refusing: the clone at {path} ({described}) has no remote configured, "
                "so which\n"
                "          repository it is cannot be established. Add one, or "
                f"{fix} to a clone that has one."
            ),
        )

    url = vcs.remote_url(str(path), remote)
    identity = normalise_remote(url or "")
    if identity is None:
        # The URL itself is deliberately absent from this message. It is the one string in
        # the whole sequence that may embed a credential, and an unparseable URL is exactly
        # the case where echoing it back would feel most helpful (FR-032).
        return Verification(
            repo_key,
            path,
            path_source,
            remote=remote,
            cause="unparseable_url",
            refusal=(
                f"refusing: could not read the {remote!r} remote of {path} as a "
                "repository URL.\n"
                "          Expected git@host:owner/name, https://host/owner/name or "
                "ssh://host/owner/name."
            ),
        )

    expected = identity_for_key(repo_key, config.github.api_base)
    if expected is None:
        return Verification(
            repo_key,
            path,
            path_source,
            remote=remote,
            identity=identity,
            cause="malformed_key",
            refusal=(
                f"refusing: {repo_key!r} is not a repository key. Keys are owner/name, "
                'as in [repos."jantman/demo"].'
            ),
        )

    if identity != expected:
        # The refusal that fires on the five known collisions, and the only thing standing
        # between the author and an override they will write incorrectly. It names **both**
        # identities, because "that is the wrong repository" without saying which one is
        # found is an instruction to go and look.
        return Verification(
            repo_key,
            path,
            path_source,
            remote=remote,
            identity=identity,
            cause="wrong_repository",
            refusal=(
                f"refusing: the clone at {path} is {_describe(identity, expected)},\n"
                f"          not {repo_key}.\n"
                f"          The path was {described}. If your clone of\n"
                f"          {repo_key} is elsewhere, set it explicitly:\n"
                "\n"
                f'              [repos."{repo_key}"]\n'
                '              path = "/where/it/actually/is"'
            ),
        )

    return Verification(
        repo_key, path, path_source, remote=remote, identity=identity
    )


# -- eligibility (US6) ------------------------------------------------------


def eligibility(
    config: Config, repo_key: str, reader: IssueSourceReader
) -> Verification:
    """Steps 1 and 2 of contracts/onboarding.md: may this be onboarded, and does it exist?

    **This is a mistake guard, not a security boundary** (FR-026). It catches a typo and a
    wrong owner — someone fat-fingering ``jantman/tropospere`` or naming a repository they
    read about. The security boundary is and remains the issue-author check in
    ``poll.evaluate``, which cannot be disabled and is untouched by this milestone. Any
    documentation, message, or later change that implies otherwise is wrong: a setting the
    author can edit in their own file protects them from themselves, not from anyone else.

    Costs exactly **one** source-system request regardless of how many repositories the
    author owns (SC-009, research R5). Enumerating ``/user/repos`` would answer the same
    ownership question in three requests today and more later, for no extra information.
    """
    expected = identity_for_key(repo_key, config.github.api_base)
    if expected is None:
        return Verification(
            repo_key,
            None,
            "derived",
            cause="malformed_key",
            refusal=(
                f"refusing: {repo_key!r} is not a repository key. Keys are owner/name, "
                'as in [repos."jantman/demo"].'
            ),
        )

    listed = {name.lower() for name in config.github.extra_repos}
    info = reader.get_repo(repo_key)
    if not info.exists:
        return Verification(
            repo_key,
            None,
            "derived",
            cause="no_such_repository",
            refusal=(
                f"refusing: {config.github.api_base} has no repository {repo_key}. "
                "Check the spelling,\n"
                "          and check the owner — a repository you can see is not "
                "necessarily one you own."
            ),
        )

    owned = info.owner.lower() == config.github.author.lower()
    if owned and config.github.include_owned:
        return Verification(repo_key, None, "derived", owner_verdict="owned")
    if repo_key.lower() in listed or info.full_name.lower() in listed:
        return Verification(repo_key, None, "derived", owner_verdict="listed")

    # Both messages end by saying what this check is, because the person reading a refusal
    # is the person most likely to reason about what it protects — and reaching for it as a
    # security control is the misreading FR-026 exists to prevent.
    aside = (
        "          (This is a guard against a mistyped name, not a security boundary; the\n"
        "          issue-author check is that, and it cannot be disabled.)"
    )
    if owned:
        refusal = (
            f"refusing: {repo_key} is owned by {config.github.author} but "
            "[github] include_owned is false.\n"
            "          Set include_owned = true, or add it to [github] extra_repos, to "
            f"permit onboarding it.\n{aside}"
        )
    else:
        refusal = (
            f"refusing: {repo_key} is not owned by {config.github.author} and is not in "
            "[github] extra_repos.\n"
            f"          Add it to extra_repos to permit onboarding it.\n{aside}"
        )
    return Verification(
        repo_key,
        None,
        "derived",
        owner_verdict="owned" if owned else f"owned by {info.owner}",
        cause="not_permitted",
        refusal=refusal,
    )


# -- the resolved view ------------------------------------------------------


def known(conn: sqlite3.Connection) -> list[str]:
    """Every onboarded repository key, sorted.

    The replacement for ``sorted(config.repos)`` at every site that meant "which
    repositories does this system watch". A ``[repos.*]`` section for a repository that was
    never onboarded describes a repository the system does not watch, and after milestone
    005 every surface says so rather than listing it as known (FR-017).
    """
    return [
        str(row[0]) for row in conn.execute("SELECT repo_key FROM repos ORDER BY repo_key")
    ]


def resolve(conn: sqlite3.Connection, config: Config, key: str) -> RepoConfig | None:
    """Everything a dispatch needs to know about one repository, or ``None``.

    ``None`` means *not onboarded*, which is the only reading after 005: the record is what
    makes a repository known. Shaped exactly like today's ``RepoConfig`` so that call sites
    changed from ``config.repos.get(key)`` to this do not change shape.

    Precedence is record over section over global default (data-model.md). The record wins
    ``path`` and **only** ``path``, and that asymmetry is the design: every other field is
    a policy the author may change at any moment by editing a file, whereas ``path``
    decides *which repository is acted upon* and so is frozen at the moment a human
    approved it. A section whose ``path`` later disagrees does not silently win — dispatch
    blocks pending ``onboard --reapprove`` (FR-013), which is ``dispatch.check_gates``'
    business rather than this function's.
    """
    from robot_army.db import get_repo

    record = get_repo(conn, key)
    if record is None:
        return None

    section = config.repos.get(key)
    # The record wins whenever it has an answer. A ``NULL`` ``clone_path`` is a row
    # predating migration 005, and the section's explicit ``path`` is used for it —
    # because that is the location every pre-005 consumer already used, so reading it here
    # changes nothing for a repository that was working yesterday. It is emphatically
    # **not** enough to dispatch on: ``dispatch.check_gates`` refuses a ``NULL``
    # ``clone_path`` outright and names ``onboard --reapprove`` (FR-014), so a location
    # nobody verified can inform a listing but can never start a session.
    #
    # Derivation is not attempted here at all. Re-deriving after approval is the guess the
    # whole record exists to avoid (research R6).
    path = record.clone_path or (str(section.path) if section and section.path else None)
    if path is None:
        return None

    from robot_army.config import RepoConfig

    if section is not None:
        return RepoConfig(
            key=key,
            path=Path(path),
            base_branch=section.base_branch,
            # A **replacement** relationship, not an extension (FR-020, research R10). A
            # repository that writes its own steps needs different ones — a different
            # dependency manager, a different bootstrap — not the common one plus extras,
            # and appending would make the shared default impossible to opt out of.
            post_create=section.post_create or config.hooks.post_create,
            env=section.env,
            permission_mode=section.permission_mode,
            model=section.model,
            max_sessions=section.max_sessions,
            priority=section.priority,
            wait_for_merge=section.wait_for_merge,
            # All three carried rather than dropped (issue #48). ``project_ordering`` is
            # tri-state and ``None`` here means *inherit*, which is what a repository with
            # no opinion should get; dropping it would silently pin every repository to the
            # global value and make the per-repository override do nothing.
            project_ordering=section.project_ordering,
            project=section.project,
            project_column=section.project_column,
            speckit=section.speckit,
        )
    return RepoConfig(
        key=key,
        path=Path(path),
        base_branch=config.worker.base_branch,
        post_create=config.hooks.post_create,
        env={},
        permission_mode=None,
        model=None,
        max_sessions=None,
        priority=0,
    )


def resolved_all(conn: sqlite3.Connection, config: Config) -> dict[str, RepoConfig]:
    """Every onboarded repository, resolved. The replacement for ``config.repos`` itself.

    Rows that resolve to nothing — onboarded before 005, no section, no recorded path —
    are omitted rather than represented by a placeholder, because every consumer of this
    mapping asks "where is its clone", and a placeholder would answer with a guess.
    """
    resolved: dict[str, RepoConfig] = {}
    for key in known(conn):
        repo = resolve(conn, config, key)
        if repo is not None:
            resolved[key] = repo
    return resolved
