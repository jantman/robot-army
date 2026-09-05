"""The example ``config.toml``, rendered from the loader's own idea of what a key is.

The point of this module is not that it prints a file. It is that it *cannot* print an
incomplete one. :func:`render` walks :data:`robot_army.config._KNOWN_KEYS` and
:data:`robot_army.config._REPO_KEYS` — the tables the loader validates against — and demands
an annotation for every key it finds. Add a key to the loader and forget this file, and the
next ``robot-army example-config`` raises with the key's name in the message.

That is deliberate over the obvious alternative, which is a hand-written example plus a note
asking future changes to remember it. This repository already tried that: the file this
module replaces was referenced from nowhere and was three sections behind the loader by the
time anybody looked.

**The import edge points one way and must stay that way.** This module imports ``config``;
``config`` must never import this one. Same rule ``speckit.py``'s docstring records about
itself, same reason: the loader is used by everything, and a cycle through the thing that
documents it would be paid for at every import.

Two properties the tests pin, because both are easy to break by accident:

* **Byte-reproducible.** No clock, no version string, no environment read, nothing that
  iterates a set. Two runs on two machines produce identical bytes, which is what lets
  ``tests/unit/test_example_config_drift.py`` compare the committed copy against a fresh
  render at all.
* **Inert.** Copying the output verbatim configures no board poll, no notification and no
  cleanup. ``[trello]`` and ``[pushover]`` are commented out header and all, because
  ``config.trello is None`` is what makes an unconfigured install make no request — not a
  check at some call site that a later refactor could drop.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from robot_army.audit import AuditLog
from robot_army.config import _KNOWN_KEYS, _REPO_KEYS

#: The one ``[repos.*]`` section the example carries. Not a real repository, and rendered
#: commented out for two reasons: an unknown key inside ``[repos.*]`` is an *error* rather
#: than a warning, and ``path`` is validated against the filesystem.
EXAMPLE_REPO_SECTION = 'repos."you/example-repo"'


class ExampleConfigError(Exception):
    """The annotations and the loader's key tables disagree.

    Raised at render time, not at test time, so ``robot-army example-config`` is itself the
    thing that reports a key nobody documented.
    """


@dataclass(frozen=True, slots=True)
class KeySpec:
    """One key as the example presents it.

    ``value`` is TOML text, already spelled — ``"60"``, ``'"robot-army"'``, ``"[]"``. Not a
    Python object plus a serialiser, because the spelling *is* what is being shown, and a
    serialiser would be a TOML writer this project has no other use for (Principle I).

    ``active`` false renders ``# key = value``, which still counts as documenting the key.
    That is the concession that lets "every key appears" and "the file loads clean" both
    hold: some keys cannot be live in a generic example without making it invalid, or
    making it do something.
    """

    name: str
    value: str
    comment: str
    active: bool = True
    #: Why this key is commented out. Required when ``active`` is false and forbidden
    #: otherwise — a commented key with no reason is a line the reader cannot act on.
    why_commented: str | None = None

    def __post_init__(self) -> None:
        if not self.value:
            raise ExampleConfigError(f"{self.name}: value must not be empty")
        if not self.comment:
            raise ExampleConfigError(f"{self.name}: comment must not be empty")
        if self.active and self.why_commented is not None:
            raise ExampleConfigError(f"{self.name}: why_commented is for commented-out keys only")
        if not self.active and not self.why_commented:
            raise ExampleConfigError(
                f"{self.name}: a commented-out key must say why, or the reader cannot act on it"
            )


@dataclass(frozen=True, slots=True)
class SectionSpec:
    """One ``[section]``, its explanation, and its keys in the order they are printed."""

    name: str
    blurb: tuple[str, ...]
    keys: tuple[KeySpec, ...]
    #: False comments out the header as well as every key. For the sections whose *absence*
    #: is the behaviour: ``config.trello`` and ``config.pushover`` are ``None`` when their
    #: section is missing, and that is what makes an unconfigured install inert.
    active: bool = True

    def __post_init__(self) -> None:
        if not self.active and any(key.active for key in self.keys):
            raise ExampleConfigError(
                f"[{self.name}]: the section is commented out but "
                f"{[k.name for k in self.keys if k.active]} are not — a live key under a "
                "dead header either fails to parse or is silently read into the section above"
            )


PREAMBLE = (
    "robot-army configuration.",
    "",
    "Generated by `robot-army example-config`. Every key the loader accepts is here;",
    "the commented-out ones say why they are commented out. Copy this to",
    "~/.config/robot-army/config.toml and edit the handful that are actually yours —",
    "[github] author first, then [paths] repo_root.",
    "",
    "As it stands this file polls nothing, notifies nobody and deletes nothing. Every",
    "outward-facing behaviour is off until you turn it on.",
)


SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        name="paths",
        blurb=(
            "Where things live. Only repo_root has to exist before the daemon will start:",
            "a missing clone root is refused at load, as one message rather than one per",
            "repository.",
        ),
        keys=(
            KeySpec(
                "repo_root",
                '"~/GIT"',
                "where clones live; a repository's default location is <repo_root>/<name>",
            ),
            KeySpec(
                "worktree_root",
                '"~/worktrees"',
                "where per-issue worktrees are created",
            ),
            KeySpec(
                "state_dir",
                '"~/.local/state/robot-army"',
                "database, logs and the heartbeat",
                active=False,
                why_commented="defaults to $XDG_STATE_HOME/robot-army; set only to move it",
            ),
            KeySpec(
                "socket_dir",
                '"~/.local/state/robot-army"',
                "the daemon's own control sockets",
                active=False,
                why_commented="defaults to $XDG_RUNTIME_DIR/robot-army; set only to move it",
            ),
        ),
    ),
    SectionSpec(
        name="github",
        blurb=(
            "The issue source, and the one section you cannot leave alone: author is the",
            "security boundary — only issues opened by that login are ever dispatched — and",
            "there is deliberately no way to disable it.",
            "",
            "The token must be a *classic* personal access token with repo and project",
            "scope. Fine-grained tokens cannot read Projects v2.",
        ),
        keys=(
            KeySpec(
                "author",
                '"your-github-login"',
                "only this login's issues are dispatched; blank is a hard error",
            ),
            KeySpec("label", '"robot-army"', "the label that marks an issue as work for us"),
            KeySpec(
                "token_env",
                '"GITHUB_TOKEN"',
                "*name* of the variable holding the token, never the token",
            ),
            KeySpec(
                "token_file",
                '"~/.config/robot-army/github-token"',
                "a mode-0600 file holding the token instead",
                active=False,
                why_commented=(
                    "exactly one of token_env or token_file may be set, and the file must "
                    "already exist at mode 0600"
                ),
            ),
            KeySpec("include_owned", "true", "poll every repository you own"),
            KeySpec(
                "extra_repos",
                "[]",
                'repositories to poll beyond those, as "owner/name"',
            ),
            KeySpec("timeout_seconds", "20", "per-request timeout"),
            KeySpec("max_retries", "4", "retries per request, with backoff"),
            KeySpec("api_base", '"https://api.github.com"', "override for GitHub Enterprise"),
        ),
    ),
    SectionSpec(
        name="worker",
        blurb=(
            "How a session is launched. permission_mode is the one to think about: auto",
            "lets the session work unattended, which is the point, but it is also the",
            "setting that decides how much it can do without asking.",
        ),
        keys=(
            KeySpec(
                "permission_mode",
                '"auto"',
                "acceptEdits, auto, bypassPermissions, manual, dontAsk or plan",
            ),
            KeySpec("model", '""', "model to pass to the worker; empty means its default"),
            KeySpec("base_branch", '"main"', "branch new work branches off"),
            KeySpec("branch_prefix", '"robot-army"', "prefix for the branches it creates"),
            KeySpec("binary", '"claude"', "the worker executable"),
        ),
    ),
    SectionSpec(
        name="dispatch",
        blurb=(
            "What runs next. project_ordering is on by default, so a repository with a",
            "cleanly resolvable project board starts taking its order from that board —",
            "and starts holding cards parked in other columns. Set it false per repository",
            "to opt out.",
        ),
        keys=(
            KeySpec("order", '"oldest-first"', "oldest-first or repo-priority"),
            KeySpec(
                "default_repo_max_sessions",
                "1",
                "sessions per repository unless its own section says otherwise",
            ),
            KeySpec(
                "wait_for_merge",
                "false",
                "wait for the previous issue to land before starting the next",
            ),
            KeySpec(
                "project_ordering",
                "true",
                "let a linked project board decide the order",
            ),
        ),
    ),
    SectionSpec(
        name="daemon",
        blurb=(
            "Loop timings and the global session cap. poll_seconds and reconcile_seconds",
            "must both be at least tick_seconds.",
        ),
        keys=(
            KeySpec(
                "effect_level",
                '"live"',
                "plan, local, no-remote or live — see the guide's setup page",
            ),
            KeySpec("tick_seconds", "5", "how often the main loop wakes"),
            KeySpec("poll_seconds", "60", "how often GitHub is polled"),
            KeySpec("reconcile_seconds", "60", "how often sessions are reconciled with reality"),
            KeySpec(
                "dispatching_max_age_seconds",
                "900",
                "how long an item may sit mid-dispatch before it is an anomaly",
            ),
            KeySpec(
                "confirm_timeout_seconds",
                "45",
                "how long a confirmation prompt waits before giving up",
            ),
            KeySpec("max_concurrent_sessions", "2", "the global cap, across all repositories"),
        ),
    ),
    SectionSpec(
        name="speckit",
        blurb=(
            "Whether a session dispatched into a Spec Kit repository is told so. On by",
            "default: adding a paragraph to a prompt is neither irreversible nor",
            "outward-facing. `robot-army repos` lists which repositories this affects.",
        ),
        keys=(
            KeySpec("enabled", "true", "add the lifecycle block to prompts"),
            KeySpec(
                "commands",
                '{ implement = "open a PR when the tests pass" }',
                "extra instruction per lifecycle command, added to the prompt",
                active=False,
                why_commented=(
                    "no extra instruction by default, and a live one here would go into "
                    "every prompt. Keys are specify, plan, tasks, implement; for anything "
                    "longer than this, write it as a [speckit.commands] table instead"
                ),
            ),
        ),
    ),
    SectionSpec(
        name="trello",
        blurb=(
            "An optional intake board: cards become issues, which then need the GitHub",
            "label like any other issue.",
            "",
            "Commented out header and all, and that is the behaviour rather than tidiness.",
            "With no [trello] section there is nothing to build, so no board request is ever",
            "constructed. An empty board_id in a live section would poll nothing, forever.",
        ),
        active=False,
        keys=(
            KeySpec(
                "board_id",
                '"your-board-id"',
                "the board to read; from its URL",
                active=False,
                why_commented="the whole section is off until you have a board",
            ),
            KeySpec(
                "label",
                '"AI-task"',
                "Trello's word for the tag marking a card as intake",
                active=False,
                why_commented="section off",
            ),
            KeySpec(
                "in_progress_list",
                '"In Progress"',
                "list a card is moved to once its issue exists",
                active=False,
                why_commented="section off",
            ),
            KeySpec(
                "done_list",
                '"Done"',
                "list a card is moved to when its issue closes",
                active=False,
                why_commented="section off",
            ),
            KeySpec(
                "ignore_lists",
                '["Someday"]',
                "list names whose cards are not intake",
                active=False,
                why_commented="section off; empty by default, which ignores nothing",
            ),
            KeySpec(
                "poll_seconds",
                "300",
                "how often the board is read; every poll is a real request",
                active=False,
                why_commented="section off",
            ),
            KeySpec(
                "timeout_seconds",
                "20",
                "per-request timeout",
                active=False,
                why_commented="section off",
            ),
            KeySpec(
                "max_retries",
                "4",
                "retries per request, with backoff",
                active=False,
                why_commented="section off",
            ),
            KeySpec(
                "api_base",
                '"https://api.trello.com/1"',
                "the API root",
                active=False,
                why_commented="section off",
            ),
            KeySpec(
                "key_env",
                '"TRELLO_KEY"',
                "*name* of the variable holding the API key",
                active=False,
                why_commented="section off; exactly one of key_env or key_file",
            ),
            KeySpec(
                "key_file",
                '"~/.config/robot-army/trello-key"',
                "a file holding the API key instead",
                active=False,
                why_commented="section off; exactly one of key_env or key_file",
            ),
            KeySpec(
                "token_env",
                '"TRELLO_TOKEN"',
                "*name* of the variable holding the API token",
                active=False,
                why_commented="section off; exactly one of token_env or token_file",
            ),
            KeySpec(
                "token_file",
                '"~/.config/robot-army/trello-token"',
                "a file holding the API token instead",
                active=False,
                why_commented="section off; exactly one of token_env or token_file",
            ),
        ),
    ),
    SectionSpec(
        name="notifications",
        blurb=(
            "What is worth saying out loud. events is empty by default, so an unconfigured",
            "install makes no outbound request at all — an unknown event kind is refused at",
            "load rather than ignored, because an event you asked for and never receive is a",
            "channel that lies by omission.",
        ),
        keys=(
            KeySpec(
                "events",
                "[]",
                'any of "dispatch", "completion", "failure", "needs_info"',
            ),
            KeySpec("max_per_cycle", "5", "bound on one burst, so a backlog cannot flood"),
        ),
    ),
    SectionSpec(
        name="pushover",
        blurb=(
            "The second notification channel. Files only — there is no *_env twin, because",
            "nothing asked for one.",
            "",
            "Commented out for the reason [trello] is: with no section there is nothing to",
            "build, and a half-configured channel cannot exist rather than merely failing to",
            "send.",
        ),
        active=False,
        keys=(
            KeySpec(
                "token_file",
                '"~/.config/robot-army/pushover-token"',
                "file holding the application token",
                active=False,
                why_commented=(
                    "both keys are required together; the section is off until both exist"
                ),
            ),
            KeySpec(
                "user_key_file",
                '"~/.config/robot-army/pushover-user-key"',
                "file holding the user key",
                active=False,
                why_commented=(
                    "both keys are required together; the section is off until both exist"
                ),
            ),
        ),
    ),
    SectionSpec(
        name="cleanup",
        blurb=(
            "Whether closing an issue reclaims its worktree and branch. Off by default, and",
            "not out of caution: removing a worktree and deleting a branch are both",
            "irreversible, and irreversible things are not reachable by default.",
            "`robot-army cleanup` runs the same guards by hand whether this is on or off.",
        ),
        keys=(
            KeySpec(
                "on_issue_close",
                "false",
                "reclaim the worktree and branch when the issue closes",
            ),
        ),
    ),
    SectionSpec(
        name="hooks",
        blurb=(
            "Preparation steps run in a new worktree before the session starts. A step is",
            "one of run, link or copy — link and copy are first-class rather than shell",
            "commands so they stay idempotent and readable.",
            "",
            "A repository's own post_create *replaces* these rather than extending them.",
        ),
        keys=(
            KeySpec("default_timeout_seconds", "300", "timeout for a step that sets none"),
            KeySpec(
                "post_create",
                '[{ run = "uv sync", timeout = 300 }, { link = ".env" }]',
                "steps every repository gets unless its own section says otherwise",
                active=False,
                why_commented=(
                    "no shared steps by default; the table form is nicer for more than one"
                ),
            ),
        ),
    ),
    SectionSpec(
        name="terminal",
        blurb=(
            "How a session's window is opened, and how it is found again. kitty appends its",
            "PID to listen_on, so the glob has to be a glob: a fixed path can only ever be",
            "stale after a restart.",
        ),
        keys=(
            KeySpec("binary", '"kitty"', "the terminal executable"),
            KeySpec("probe_timeout_seconds", "2", "how long to wait when probing a socket"),
            KeySpec(
                "socket_glob",
                '"/run/user/1000/mykitty-*"',
                "where kitty's control socket is looked for",
                active=False,
                why_commented=(
                    "defaults to mykitty-* under $XDG_RUNTIME_DIR, resolved at startup; "
                    "writing it out would pin this machine's UID. Pair it with "
                    "`listen_on unix:${XDG_RUNTIME_DIR}/mykitty` in kitty.conf"
                ),
            ),
        ),
    ),
    SectionSpec(
        name="web",
        blurb=(
            "`robot-army serve`. The bind address *is* the access policy — there is no",
            "login — so it is loopback until you deliberately widen it.",
        ),
        keys=(
            KeySpec("bind", '"127.0.0.1"', "the address to listen on"),
            KeySpec("port", "8420", "the port to listen on"),
            KeySpec("refresh_seconds", "10", "how often a page refreshes itself"),
        ),
    ),
    SectionSpec(
        name="health",
        blurb=("When the heartbeat counts as stale. `robot-army health` exits 4 if it is.",),
        keys=(
            KeySpec("max_age_seconds", "180", "heartbeat older than this is stale"),
            KeySpec(
                "webhook_url",
                '""',
                "optional URL pinged by `robot-army health --notify`",
            ),
        ),
    ),
    SectionSpec(
        name=EXAMPLE_REPO_SECTION,
        blurb=(
            "Per-repository exceptions. You do not need one of these: onboarding a",
            "repository is enough, and everything below inherits from the sections above",
            "when it is absent.",
            "",
            "Commented out because it names a repository that does not exist — and because",
            "an unknown key *inside* [repos.*] is an error rather than a warning, so a typo",
            "here is a config that will not load rather than a setting that quietly does",
            "nothing.",
        ),
        active=False,
        keys=(
            KeySpec(
                "path",
                '"~/GIT/example-repo"',
                "the clone, when it is not <repo_root>/<name>",
                active=False,
                why_commented="must be an existing git repository; absent means derive it",
            ),
            KeySpec(
                "base_branch",
                '"develop"',
                "overrides [worker] base_branch here",
                active=False,
                why_commented="section off",
            ),
            KeySpec(
                "permission_mode",
                '"acceptEdits"',
                "overrides [worker] permission_mode here",
                active=False,
                why_commented="section off",
            ),
            KeySpec(
                "model",
                '"opus"',
                "overrides [worker] model here",
                active=False,
                why_commented="section off",
            ),
            KeySpec(
                "max_sessions",
                "2",
                "sessions at once in this repository; capped by the global limit",
                active=False,
                why_commented="section off",
            ),
            KeySpec(
                "priority",
                "10",
                "higher runs first under repo-priority ordering",
                active=False,
                why_commented="section off",
            ),
            KeySpec(
                "wait_for_merge",
                "true",
                "wait for the previous issue to land, here specifically",
                active=False,
                why_commented="section off; absent inherits [dispatch] wait_for_merge",
            ),
            KeySpec(
                "project_ordering",
                "false",
                "opt this repository out of board ordering",
                active=False,
                why_commented="section off; absent inherits [dispatch] project_ordering",
            ),
            KeySpec(
                "project",
                "3",
                "which project governs: a number, or a projects/N URL",
                active=False,
                why_commented="section off; absent means discover the linked project",
            ),
            KeySpec(
                "project_column",
                '"Ready"',
                "the column to dispatch from",
                active=False,
                why_commented="section off; absent recognises Ready, Todo or To do",
            ),
            KeySpec(
                "speckit",
                "false",
                "tell sessions here about the Spec Kit lifecycle, or not",
                active=False,
                why_commented="section off; absent inherits [speckit] enabled",
            ),
            KeySpec(
                "speckit_commands",
                '{ implement = "" }',
                "per-command instructions here; empty string drops one",
                active=False,
                why_commented=(
                    "section off. Unlike the global table an empty value is meaningful here: "
                    "it means no instruction for that command in this repository"
                ),
            ),
            KeySpec(
                "post_create",
                '[{ run = "make dev", timeout = 600 }]',
                "preparation steps, replacing [hooks] post_create entirely",
                active=False,
                why_commented=(
                    "section off; these replace the shared steps rather than extending them"
                ),
            ),
            KeySpec(
                "env",
                '{ DATABASE_URL = "postgres://localhost/dev" }',
                "environment variables for sessions in this repository",
                active=False,
                why_commented="section off; the table form is nicer for more than one",
            ),
        ),
    ),
)


def _expected_keys(section: str) -> set[str]:
    """The keys the loader accepts in ``section``, from the loader's own tables."""
    if section == EXAMPLE_REPO_SECTION:
        return set(_REPO_KEYS)
    return set(_KNOWN_KEYS[section])


def _check() -> None:
    """Fail if the annotations and the loader's tables have drifted apart.

    Both directions, because they fail differently. A key in the tables with no annotation
    is a key somebody added and never documented. An annotation for a key the tables do not
    contain is a key somebody *removed* while its documentation stayed — which reads as a
    supported setting and silently is not.
    """
    problems: list[str] = []
    described = {section.name for section in SECTIONS} - {EXAMPLE_REPO_SECTION}
    for missing in sorted(set(_KNOWN_KEYS) - described):
        problems.append(
            f"[{missing}] is accepted by the loader but has no SectionSpec here — "
            "add one, with a comment for every key"
        )
    for extra in sorted(described - set(_KNOWN_KEYS)):
        problems.append(f"[{extra}] has a SectionSpec but the loader does not accept it")

    for section in SECTIONS:
        if section.name not in _KNOWN_KEYS and section.name != EXAMPLE_REPO_SECTION:
            continue  # already reported above
        expected = _expected_keys(section.name)
        present = {key.name for key in section.keys}
        if len(present) != len(section.keys):
            problems.append(f"[{section.name}] has a duplicated key annotation")
        for missing in sorted(expected - present):
            problems.append(
                f"[{section.name}] {missing} is accepted by the loader but is not in the "
                "example — every key needs a value and a one-line comment here"
            )
        for extra in sorted(present - expected):
            problems.append(
                f"[{section.name}] {extra} is in the example but the loader does not "
                "accept it — was it renamed or removed?"
            )

    if problems:
        raise ExampleConfigError(
            "the example config and the loader disagree about what keys exist "
            f"({len(problems)} problem(s)):\n" + "\n".join(f"  - {p}" for p in problems)
        )


def _render_key(key: KeySpec) -> list[str]:
    line = f"{key.name} = {key.value}  # {key.comment}"
    if key.active:
        return [line]
    return [f"# {line}", f"#   ({key.why_commented})"]


def render() -> str:
    """The whole example, as one string ending in a newline.

    Deterministic by construction: no clock, no environment, no filesystem, no read of the
    author's configuration, and nothing that iterates a set. Two runs anywhere produce the
    same bytes, which is the property ``test_example_config_drift`` depends on.
    """
    _check()
    lines = [f"# {line}".rstrip() for line in PREAMBLE]
    for section in SECTIONS:
        lines.append("")
        lines.extend(f"# {line}".rstrip() for line in section.blurb)
        lines.append(f"[{section.name}]" if section.active else f"# [{section.name}]")
        for key in section.keys:
            lines.extend(_render_key(key))
    return "\n".join(lines) + "\n"


def write(path: Path, *, force: bool = False, audit: AuditLog | None = None) -> None:
    """Render to ``path`` atomically, refusing to replace an existing file unless told to.

    Atomic because of where this usually lands. The destination is the file the daemon will
    not start without, and a half-written config.toml is not an inconvenience — it is a
    daemon that refuses to start, discovered later, for a reason that looks like a TOML
    syntax error nobody typed. Rendering happens entirely before the first byte is written,
    so a drift error cannot truncate a file that was already there either.

    ``audit`` is optional and its failure is not this function's failure: the file is the
    point, and a log that cannot be opened must not cost the author their config.
    """
    text = render()
    destination = Path(path).expanduser()
    # Absolute in the record, as typed in the messages. A relative path in an audit log is
    # only interpretable if you also know the working directory the command ran in, which
    # the record does not carry — and reconstruction from the log alone is the standard.
    recorded_target = str(destination.absolute())

    def _record(outcome: str, detail: dict[str, object]) -> None:
        if audit is None:
            return
        try:
            audit.record(
                "example_config.write",
                outcome=outcome,
                target=recorded_target,
                detail=detail,
            )
        except OSError:
            # Reported by the caller on stderr. Swallowing it here would be the silent
            # failure Principle III forbids; failing the write over it would be worse.
            raise

    if destination.exists() and not force:
        _record("failure", {"force": force, "error": "file exists"})
        raise FileExistsError(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Temporary file in the destination's own directory, so the rename is within one
    # filesystem and therefore atomic.
    handle, temporary = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        Path(temporary).unlink(missing_ok=True)
        _record("failure", {"force": force, "error": str(exc)})
        raise
    _record("success", {"force": force, "bytes": len(text.encode("utf-8"))})
