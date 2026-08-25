"""Shared fixtures.

Two deliberate choices, both from research.md R20:

* ``/proc`` and the session registry are read through a small indirection, so tests
  supply **fixture directories** rather than mocking the filesystem globally. A global
  mock would test the mock. Those directories are *built here* into ``tmp_path`` by
  ``write_proc``, ``write_registry`` and ``write_exit_record`` rather than checked in as
  static files: a synthetic ``/proc`` needs real symlinks, a registry entry needs a
  ``procStart`` that matches a process this test just invented, and both go stale the
  moment the format they imitate changes. Generating them keeps every test hermetic and
  keeps the shape in one place.
* Real ``git`` runs against temporary repositories. Git is fast, and mocking it would
  test the mock — which matters most for the one behaviour we depend on git for, namely
  its refusal to remove a dirty worktree.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from robot_army import db
from robot_army.audit import AuditLog
from robot_army.boundaries import (
    BoardInfo,
    Card,
    CardWriteResult,
    HookResult,
    HostCapabilities,
    HostHandle,
    Issue,
    PollResult,
)
from robot_army.config import Config, parse
from robot_army.effects import Boundaries, EffectLevel
from robot_army.paths import Layout

# -- filesystem -------------------------------------------------------------


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    built = Layout(state_dir=tmp_path / "state", socket_dir=tmp_path / "run")
    built.ensure()
    return built


@pytest.fixture
def audit(layout: Layout) -> Any:
    log = AuditLog(layout.log_dir, component="test")
    yield log
    log.close()


@pytest.fixture
def conn(layout: Layout) -> Any:
    connection, _ = db.open_database(layout.db_path)
    yield connection
    connection.close()


# -- config -----------------------------------------------------------------


@pytest.fixture
def repo_clone(tmp_path: Path) -> Path:
    """A real git repository with one commit on ``main``."""
    return make_repo(tmp_path / "clones" / "demo")


def make_repo(path: Path, *, files: dict[str, str] | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=path, env=env, check=True, capture_output=True, text=True
    )
    run("init", "-q", "-b", "main")
    # Hermetic: the maintainer's own ~/.config/git/ignore excludes
    # `.claude/settings.local.json`, which would silently keep the fingerprint fixtures
    # out of the commit and make these tests pass or fail depending on whose machine
    # they run on. GIT_CONFIG_GLOBAL does not cover it — the XDG ignore file is found
    # independently of config — so the excludes file is overridden per repository.
    run("config", "core.excludesFile", "/dev/null")
    (path / "README.md").write_text("# demo\n", encoding="utf-8")
    for name, content in (files or {}).items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "initial")
    return path


def config_dict(
    repo_clone: Path,
    layout: Layout,
    worktree_root: Path,
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "daemon": {
            "effect_level": "live",
            "tick_seconds": 1,
            "poll_seconds": 1,
            "reconcile_seconds": 1,
            "dispatching_max_age_seconds": 60,
            "confirm_timeout_seconds": 1,
            "max_concurrent_sessions": 2,
        },
        "paths": {
            "worktree_root": str(worktree_root),
            "state_dir": str(layout.state_dir),
            "socket_dir": str(layout.socket_dir),
        },
        "github": {
            "author": "jantman",
            "label": "robot-army",
            "token_env": "ROBOT_ARMY_TEST_TOKEN",
        },
        "worker": {"permission_mode": "auto", "base_branch": "main", "binary": "claude"},
        "terminal": {"socket_glob": "/tmp/test-kitty-*"},
        "health": {"max_age_seconds": 60},
        "hooks": {"default_timeout_seconds": 10},
        "repos": {"demo": {"path": str(repo_clone), "base_branch": "main"}},
    }
    for section, values in overrides.items():
        if isinstance(values, dict) and isinstance(base.get(section), dict):
            base[section].update(values)
        else:
            base[section] = values
    return base


@pytest.fixture
def config(repo_clone: Path, layout: Layout, tmp_path: Path) -> Config:
    monkey_token()
    return parse(
        config_dict(repo_clone, layout, tmp_path / "worktrees"), tmp_path / "config.toml"
    )


#: A minimally valid ``[trello]`` table. Milestone 003's default config has **no** board
#: (FR-001), so every board test opts in explicitly — which is also what keeps the
#: unconfigured case honestly tested by everything else in the suite.
TRELLO_SECTION: dict[str, Any] = {
    "board_id": "board-1",
    "label": "AI-task",
    "in_progress_list": "In Progress",
    "done_list": "Done",
    "key_env": "ROBOT_ARMY_TEST_TRELLO_KEY",
    "token_env": "ROBOT_ARMY_TEST_TRELLO_TOKEN",
}


@pytest.fixture
def board_config(repo_clone: Path, layout: Layout, tmp_path: Path) -> Config:
    """A config with a board configured, for the milestone 003 paths.

    Its repository is keyed ``jantman/demo`` rather than the bare ``demo`` the other
    fixtures use, because milestone 003 *reads* repository keys out of card text: a card
    names ``owner/name``, which is what ``[repos."you/example-repo"]`` looks like in the
    shipped example and what ``GitHubReader._repo_path`` requires. A short key here would
    make every resolution test resolve nothing.
    """
    monkey_token()
    raw = config_dict(repo_clone, layout, tmp_path / "worktrees", trello=dict(TRELLO_SECTION))
    raw["repos"] = {"jantman/demo": {"path": str(repo_clone), "base_branch": "main"}}
    return parse(raw, tmp_path / "config.toml")


def monkey_token() -> None:
    os.environ.setdefault("ROBOT_ARMY_TEST_TOKEN", "not-a-real-token")
    # Distinctive values: test_trello_secrets.py greps every record for these, and a
    # placeholder that also appears in ordinary prose would make that test vacuous.
    os.environ.setdefault("ROBOT_ARMY_TEST_TRELLO_KEY", "trellokey-abcdef0123456789")
    os.environ.setdefault("ROBOT_ARMY_TEST_TRELLO_TOKEN", "trellotoken-fedcba9876543210")


# -- fake boundaries --------------------------------------------------------


class FakeIssueReader:
    """A reader whose answers the test controls. There is no *simulated* reader in the
    product — reads are always real — so tests supply their own fake instead."""

    def __init__(self, issues: list[Issue] | None = None, *, etag: str | None = "etag-1") -> None:
        self.issues = issues or []
        self.etag = etag
        self.status = 200
        self.closed: dict[tuple[str, int], bool] = {}
        self.open_prs: dict[tuple[str, int], Any] = {}
        self.poll_calls: list[tuple[str, str | None]] = []
        self.closed_calls: list[tuple[str, int]] = []
        self.pr_calls: list[tuple[str, str]] = []
        self.raise_on_poll: Exception | None = None
        self.raise_on_remote: Exception | None = None
        self.listing_calls: list[tuple[str, str, str | None]] = []
        self.created: dict[int, str] = {}

    def poll(self, repo_key: str, etag: str | None) -> PollResult:
        self.poll_calls.append((repo_key, etag))
        if self.raise_on_poll is not None:
            raise self.raise_on_poll
        if etag is not None and etag == self.etag:
            return PollResult(items=(), etag=etag, status=304)
        return PollResult(items=tuple(self.issues), etag=self.etag, status=self.status)

    def get_issue(self, repo_key: str, number: int) -> Issue | None:
        for issue in self.issues:
            if issue.number == number:
                return issue
        return None

    def is_closed(self, repo_key: str, number: int) -> bool:
        self.closed_calls.append((repo_key, number))
        if self.raise_on_remote is not None:
            raise self.raise_on_remote
        return self.closed.get((repo_key, number), False)

    def open_pr_for_branch(self, repo_key: str, branch: str) -> Any:
        # Keyed by branch, because that is what the caller asks about: milestone 002's
        # resume signals want "is there an open PR for *this* branch", not for the issue.
        self.pr_calls.append((repo_key, branch))
        return self.open_prs.get((repo_key, branch))

    def list_issues_since(
        self, repo_key: str, since: str, *, author: str | None = None, limit: int = 100
    ) -> list[Issue]:
        """Milestone 003's recovery read. ``created`` maps issue number → created-at, so a
        test can place an issue before or after the intent timestamp precisely."""
        self.listing_calls.append((repo_key, since, author))
        found = []
        for issue in self.issues:
            if author is not None and issue.author != author:
                continue
            if self.created.get(issue.number, since) < since:
                continue
            found.append(issue)
        return found[:limit]

    def list_owned_repos(self) -> list[Any]:
        return []


class RecordingWriter:
    def __init__(self, *, next_number: int = 101) -> None:
        self.comments: list[tuple[str, int, str]] = []
        self.created: list[tuple[str, str, str]] = []
        self.next_number = next_number
        #: Set to raise from ``create_issue``, for the failure branch of the four-step
        #: creation sequence — the seam where an intent row exists and no issue does.
        self.raise_on_create: Exception | None = None

    def comment(self, repo_key: str, number: int, body: str) -> str:
        self.comments.append((repo_key, number, body))
        return f"https://example.invalid/{repo_key}/{number}#c{len(self.comments)}"

    def create_issue(self, repo_key: str, title: str, body: str) -> Issue:
        if self.raise_on_create is not None:
            raise self.raise_on_create
        self.created.append((repo_key, title, body))
        number = self.next_number
        self.next_number += 1
        return Issue(
            number=number,
            title=title,
            body=body,
            url=f"https://github.com/{repo_key}/issues/{number}",
            # Empty, always: FR-015 has no parameter that could carry a label, and a fake
            # that invented one would let a test pass that the product would fail.
            labels=(),
            author="jantman",
            state="open",
        )


class FakeCardReader:
    """A board whose answers the test controls.

    There is no *simulated* card reader in the product — board reads are real at every
    effect level — so tests supply their own fake, exactly as they do for issues.

    ``comment_calls`` exists for one specific assertion: with a mapping row present,
    ``card_comments`` must **never** be reached (R7, §11). Counting the calls is how that
    ordering rule is tested rather than assumed.
    """

    def __init__(
        self,
        cards: list[Card] | None = None,
        *,
        board: BoardInfo | None = None,
        comments: dict[str, list[str]] | None = None,
    ) -> None:
        self.cards = cards or []
        self.board = board or BoardInfo(
            board_id="board-1",
            name="Intake",
            permission_level="private",
            member_ids=("member-1",),
            labels={"AI-task": "label-ai"},
            lists={"In Progress": "list-doing", "Done": "list-done", "Inbox": "list-inbox"},
        )
        self.comments = comments or {}
        self.poll_calls: list[tuple[str, str]] = []
        self.comment_calls: list[str] = []
        self.board_calls = 0
        self.raise_on_poll: Exception | None = None
        self.raise_on_board: Exception | None = None

    def board_info(self) -> BoardInfo:
        self.board_calls += 1
        if self.raise_on_board is not None:
            raise self.raise_on_board
        return self.board

    def poll(self, board_id: str, label_id: str) -> list[Card]:
        self.poll_calls.append((board_id, label_id))
        if self.raise_on_poll is not None:
            raise self.raise_on_poll
        return [c for c in self.cards if label_id in c.label_ids and not c.closed]

    def get_card(self, card_id: str) -> Card | None:
        for card in self.cards:
            if card.card_id == card_id:
                return card
        return None

    def card_comments(self, card_id: str) -> list[str]:
        self.comment_calls.append(card_id)
        return list(self.comments.get(card_id, []))


class RecordingCardWriter:
    """Records every board write and refreshes the card's activity stamp like the real one.

    The refresh is not decoration: R9's loop closes only because a writer hands the caller
    the post-write timestamp, and a fake that returned ``None`` would let a test pass while
    the product looped.
    """

    def __init__(self, reader: Any = None) -> None:
        self.reader = reader
        self.comments: list[tuple[str, str]] = []
        self.moves: list[tuple[str, str]] = []
        self.raise_on_comment: Exception | None = None
        self.raise_on_move: Exception | None = None

    def comment(self, card_id: str, body: str) -> CardWriteResult:
        if self.raise_on_comment is not None:
            raise self.raise_on_comment
        self.comments.append((card_id, body))
        return CardWriteResult(
            url=f"https://trello.com/c/{card_id}#c{len(self.comments)}",
            last_activity=self._touch(card_id),
        )

    def move(self, card_id: str, list_id: str) -> CardWriteResult:
        if self.raise_on_move is not None:
            raise self.raise_on_move
        self.moves.append((card_id, list_id))
        return CardWriteResult(url=None, last_activity=self._touch(card_id, list_id=list_id))

    def _touch(self, card_id: str, *, list_id: str | None = None) -> str:
        """Advance the fake board's clock for this card, as a real write would."""
        stamp = f"2026-08-24T00:00:{len(self.comments) + len(self.moves):02d}Z"
        if self.reader is None:
            return stamp
        import dataclasses

        for index, card in enumerate(self.reader.cards):
            if card.card_id == card_id:
                changes: dict[str, Any] = {"last_activity": stamp}
                if list_id is not None:
                    changes["list_id"] = list_id
                self.reader.cards[index] = dataclasses.replace(card, **changes)
        return stamp


def make_card(card_id: str = "card-1", **overrides: Any) -> Card:
    defaults: dict[str, Any] = {
        "card_id": card_id,
        "board_id": "board-1",
        "url": f"https://trello.com/c/{card_id}",
        "title": "Fix the thing",
        "body": "in https://github.com/x/demo",
        "label_ids": ("label-ai",),
        "list_id": "list-inbox",
        "last_activity": "2026-08-24T00:00:00Z",
        "closed": False,
    }
    defaults.update(overrides)
    return Card(**defaults)


class StubHookRunner:
    def __init__(self, result: HookResult | None = None) -> None:
        self.result = result or HookResult(ok=True)
        self.calls: list[tuple[Any, str, str]] = []

    def run(self, steps: Any, worktree_path: str, clone_path: str, env: dict[str, str]):
        self.calls.append((steps, worktree_path, clone_path))
        return self.result


class StubSessionHost:
    capabilities = HostCapabilities(
        survives_display_death=True, reattachable=True, multi_viewer=True
    )

    def __init__(self, *, confirm: bool = True) -> None:
        self.confirm_result = confirm
        self.spawned: list[tuple[str, list[str], str]] = []
        self.terminated: list[tuple[str, str | None]] = []
        self.alive: set[str] = set()

    def build_argv(self, socket_path: str, argv: list[str]) -> list[str]:
        return ["dtach", "-A", socket_path, *argv]

    def spawn(self, cwd: str, argv: list[str], socket_path: str) -> HostHandle:
        self.spawned.append((cwd, argv, socket_path))
        self.alive.add(socket_path)
        return HostHandle(socket_path=socket_path, argv=tuple(argv))

    def confirm_session(self, session_id: str, timeout_seconds: float, **_: Any) -> Any:
        if not self.confirm_result:
            return None
        from robot_army.sessions import RegistryEntry

        return RegistryEntry(
            session_id=session_id,
            pid=0,
            proc_start=None,
            cwd=None,
            status="test",
            version=1,
            source_file="<test>",
        )

    def is_alive(self, handle: HostHandle) -> bool:
        return handle.socket_path in self.alive

    def terminate(self, handle: HostHandle, scope: str | None = None) -> None:
        self.alive.discard(handle.socket_path)
        self.terminated.append((handle.socket_path, scope))

    def attach_command(self, handle: HostHandle) -> list[str]:
        return ["dtach", "-a", handle.socket_path]


class StubDisplay:
    def __init__(self, *, answers: bool = True) -> None:
        self.answers = answers
        self.opened: list[dict[str, Any]] = []
        self.next_id = 100

    def probe(self) -> str | None:
        return "unix:/tmp/test-kitty-1" if self.answers else None

    def open(self, cwd, argv, title, user_vars, env):
        from robot_army.boundaries import DisplayHandle

        self.next_id += 1
        self.opened.append(
            {"cwd": cwd, "argv": argv, "title": title, "user_vars": user_vars, "env": env}
        )
        return DisplayHandle(window_id=self.next_id, title=title, user_vars=dict(user_vars))

    def is_open(self, handle) -> bool:
        return True

    def close(self, handle) -> None:
        return None

    def find_by_var(self, key: str, value: str):
        return None

    def send_text(self, handle, text: str) -> None:
        return None

    def window_state(self, handle):
        return {"id": handle.window_id, "title": handle.title}


def make_boundaries(
    audit: AuditLog,
    *,
    level: EffectLevel = EffectLevel.LIVE,
    reader: Any = None,
    writer: Any = None,
    card_reader: Any = None,
    card_writer: Any = None,
    vcs: Any = None,
    hooks: Any = None,
    host: Any = None,
    display: Any = None,
) -> Boundaries:
    from robot_army.boundaries.git import GitVersionControl

    return Boundaries(
        level=level,
        issue_reader=reader or FakeIssueReader(),
        issue_writer=writer or RecordingWriter(),
        # ``None`` by default, mirroring an installation with no ``[trello]`` section: a
        # test that wants a board says so, and every other test proves the unconfigured
        # path stays inert.
        card_reader=card_reader,
        card_writer=card_writer,
        version_control=vcs or GitVersionControl(audit),
        hook_runner=hooks or StubHookRunner(),
        session_host=host or StubSessionHost(),
        display=display or StubDisplay(),
    )


def make_board_boundaries(
    audit: AuditLog,
    *,
    level: EffectLevel = EffectLevel.LIVE,
    cards: list[Card] | None = None,
    board: BoardInfo | None = None,
    comments: dict[str, list[str]] | None = None,
    **overrides: Any,
) -> Boundaries:
    """A wired set with a fake board attached, for the milestone 003 paths."""
    card_reader = overrides.pop("card_reader", None) or FakeCardReader(
        cards, board=board, comments=comments
    )
    card_writer = overrides.pop("card_writer", None) or RecordingCardWriter(card_reader)
    return make_boundaries(
        audit, level=level, card_reader=card_reader, card_writer=card_writer, **overrides
    )


@pytest.fixture
def boundaries(audit: AuditLog) -> Boundaries:
    return make_boundaries(audit)


# -- fixture builders -------------------------------------------------------


def write_registry(
    directory: Path,
    *,
    pid: int,
    session_id: str,
    proc_start: str = "12345",
    cwd: str = "/tmp",
    version: str | None = "2.1.239",
    truncate: bool = False,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "sessionId": session_id,
        "pid": pid,
        "procStart": proc_start,
        "cwd": cwd,
        "status": "idle",
    }
    if version is not None:
        payload["version"] = version
    text = json.dumps(payload)
    if truncate:
        text = text[: len(text) // 2]
    path = directory / f"{pid}.json"
    path.write_text(text, encoding="utf-8")
    return path


def write_proc(root: Path, pid: int, *, starttime: str = "12345", cwd: str = "/tmp",
               exe: str = "/usr/bin/claude", cgroup: str | None = None) -> Path:
    """Build a synthetic ``/proc/<pid>`` tree.

    The ``stat`` line reproduces the real format including a ``comm`` containing spaces
    and parentheses, because that is what makes the naive "split on whitespace" parse
    wrong and the ``rfind(')')`` parse right.
    """
    directory = root / str(pid)
    directory.mkdir(parents=True, exist_ok=True)
    # The line is: field1=pid, field2=comm, field3=state, then `rest`. So `rest[i]` is
    # field i+4, and starttime (field 22) lands at rest[18]. Verified against a real
    # /proc entry rather than counted from the man page alone.
    rest = ["0"] * 49
    rest[18] = starttime
    directory.joinpath("stat").write_text(
        f"{pid} (we (ird) name) S " + " ".join(rest) + "\n", encoding="utf-8"
    )
    target = directory / "cwd"
    if not target.is_symlink():
        target.symlink_to(cwd)
    exe_link = directory / "exe"
    if not exe_link.is_symlink():
        exe_link.symlink_to(exe)
    directory.joinpath("cgroup").write_text(
        cgroup or "0::/user.slice/user-1000.slice/user@1000.service/app.slice/kitty-1.scope\n",
        encoding="utf-8",
    )
    return directory


def write_exit_record(
    spool: Path,
    *,
    session_id: str,
    exit_code: int = 0,
    signal: int | None = None,
    event: str = "exit",
    schema: int = 1,
    truncate: bool = False,
    item: str = "1",
) -> Path:
    spool.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema": schema,
        "event": event,
        "item": item,
        "session_id": session_id,
        "ts": "2026-08-23T14:07:11Z",
    }
    if event == "exit":
        payload |= {
            "started": "2026-08-23T14:07:11Z",
            "ended": "2026-08-23T16:31:02Z",
            "exit": exit_code,
            "signal": signal,
        }
    else:
        payload |= {"pid": 1234, "ppid": 1233, "cwd": "/tmp", "argv": ["claude"]}
    text = json.dumps(payload)
    if truncate:
        text = text[: len(text) // 2]
    path = spool / f"{session_id}.{event}.json"
    path.write_text(text, encoding="utf-8")
    return path


def seed_item(
    conn: Any,
    *,
    repo_key: str = "demo",
    issue_number: int = 42,
    dry_run: bool = False,
    state: str | None = None,
    title: str = "Fix the thing",
) -> int:
    """Insert an onboarded repo and one work item, returning the item id."""
    with db.transaction(conn):
        if db.get_repo(conn, repo_key) is None:
            db.upsert_repo(
                conn, repo_key=repo_key, settings_fingerprint=None, trust_verified=True
            )
        item_id = db.insert_work_item(
            conn,
            source="github",
            source_id=f"{repo_key}#{issue_number}",
            source_url=f"https://github.com/x/{repo_key}/issues/{issue_number}",
            repo_key=repo_key,
            issue_number=issue_number,
            title=title,
            body="body",
            labels='["robot-army"]',
            dry_run=dry_run,
        )
    assert item_id is not None
    if state:
        conn.execute("UPDATE work_items SET state = ? WHERE id = ?", (state, item_id))
    return item_id


def make_issue(number: int = 42, **overrides: Any) -> Issue:
    defaults: dict[str, Any] = {
        "number": number,
        "title": "Fix the thing",
        "body": "Please fix it.",
        "url": f"https://github.com/x/demo/issues/{number}",
        "labels": ("robot-army",),
        "author": "jantman",
        "state": "open",
    }
    defaults.update(overrides)
    return Issue(**defaults)


# -- the web harness (milestone 002) ----------------------------------------
#
# Every web test drives ``server.handle`` directly rather than through a socket (R15).
# Routing, negotiation, rendering and every refusal are pure functions of a Request, which
# is what makes the failure cases — bad method, unknown item, illegal transition,
# effect-level mismatch, daemon down — cheap enough to write exhaustively. Exactly one
# integration test binds a real port, for the parts this cannot reach.


class WebHarness:
    """A ``WebApp`` with its boundaries stubbed and a request helper.

    The stubbed boundaries are shared instances rather than fresh ones per request, so a
    test can assert on what the display was asked to open or what the reader was asked
    about — which is the whole point of having them.
    """

    def __init__(self, app: Any, *, reader: Any, display: Any, host: Any, vcs: Any) -> None:
        self.app = app
        self.reader = reader
        self.display = display
        self.host = host
        self.vcs = vcs

    def request(
        self,
        method: str,
        path: str,
        *,
        form: dict[str, str] | None = None,
        accept: str = "text/html",
        headers: dict[str, str] | None = None,
    ) -> Any:
        from urllib.parse import urlencode

        from robot_army.web.server import handle, parse_request

        head = {"accept": accept, "host": "localhost:8420"}
        head.update({k.lower(): v for k, v in (headers or {}).items()})
        body = urlencode(form or {}).encode("utf-8") if form is not None else b""
        return handle(self.app, parse_request(method, path, head, body))

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def get_json(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, accept="application/json", **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("form", {})
        return self.request("POST", path, **kwargs)

    def post_json(self, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("form", {})
        return self.request("POST", path, accept="application/json", **kwargs)


@pytest.fixture
def web(config: Config, conn: Any, layout: Layout, monkeypatch: Any) -> WebHarness:
    """A web app whose per-request contexts get stubbed boundaries.

    ``operations.wire`` is the seam: the web builds one ``Context`` per request and would
    otherwise construct a real ``GitHubReader``, so a resume-signal render in a test would
    make a network call. Patching the wiring rather than adding a hook to ``WebApp`` keeps
    the test seam out of the product.
    """
    from robot_army import operations
    from robot_army.web.server import WebApp

    reader = FakeIssueReader()
    display = StubDisplay()
    host = StubSessionHost()
    shared: dict[str, Any] = {}

    def fake_wire(level: Any, cfg: Any, audit_log: Any) -> Any:
        from robot_army.boundaries.git import GitVersionControl

        if "vcs" not in shared:
            shared["vcs"] = GitVersionControl(audit_log)
        return make_boundaries(
            audit_log,
            level=level,
            reader=reader,
            display=display,
            host=host,
            vcs=shared["vcs"],
        )

    monkeypatch.setattr(operations, "wire", fake_wire)
    operations.clear_resume_signal_cache()
    app = WebApp(config)
    harness = WebHarness(app, reader=reader, display=display, host=host, vcs=None)
    yield harness
    operations.clear_resume_signal_cache()


@pytest.fixture
def board_web(board_config: Config, conn: Any, layout: Layout, monkeypatch: Any) -> WebHarness:
    """A web app whose config has a board, for milestone 003's views.

    Separate from ``web`` rather than a parameter on it, so every milestone 002 test keeps
    exercising the **unconfigured** path — which is the one most installations run and the
    one FR-001 is about.
    """
    from robot_army import operations
    from robot_army.web.server import WebApp

    reader = FakeIssueReader()
    display = StubDisplay()
    host = StubSessionHost()
    card_reader = FakeCardReader()
    card_writer = RecordingCardWriter(card_reader)
    shared: dict[str, Any] = {}

    def fake_wire(level: Any, cfg: Any, audit_log: Any) -> Any:
        from robot_army.boundaries.git import GitVersionControl

        if "vcs" not in shared:
            shared["vcs"] = GitVersionControl(audit_log)
        return make_boundaries(
            audit_log,
            level=level,
            reader=reader,
            display=display,
            host=host,
            vcs=shared["vcs"],
            card_reader=card_reader,
            card_writer=card_writer,
        )

    monkeypatch.setattr(operations, "wire", fake_wire)
    operations.clear_resume_signal_cache()
    harness = WebHarness(WebApp(board_config), reader=reader, display=display, host=host, vcs=None)
    harness.card_reader = card_reader
    harness.card_writer = card_writer
    yield harness
    operations.clear_resume_signal_cache()


@pytest.fixture
def running_daemon(layout: Layout) -> Any:
    """A running daemon: it holds the lock **and** it has written a heartbeat.

    ``flock`` is associated with the open file description, not the process, so a second
    descriptor on the same file conflicts even from here — which is what makes this a real
    test of the guard rather than a mock of it.

    The heartbeat is part of the fixture because a daemon that holds the lock has always
    written one: it writes it before its first tick. A lock with no heartbeat is a distinct
    and much rarer state — a daemon caught mid-startup — and the effect-level guard now
    treats it differently, so representing it by accident would be misleading. Tests that
    want that state remove the file explicitly.
    """
    from robot_army.daemon import SingleInstanceLock

    lock = SingleInstanceLock(layout.lock_path)
    lock.acquire()
    beat(layout)
    yield lock
    lock.release()


def beat(layout: Layout, *, effect_level: str = "live", **overrides: Any) -> None:
    """Write a heartbeat, so the chrome and the effect guard have something to read."""
    from robot_army import health

    health.write_heartbeat(
        layout.heartbeat_path,
        effect_level=effect_level,
        activity=overrides.pop("activity", "idle"),
        cycles=overrides.pop("cycles", 1),
        **overrides,
    )


def seed_session(
    conn: Any,
    item_id: int,
    *,
    state: str = "running",
    session_id: str | None = None,
    dry_run: bool = False,
    host_socket: str = "/tmp/ra-test.sock",
    pid: int | None = 4321,
    exit_code: int | None = None,
    signal: int | None = None,
) -> int:
    """Insert a session row directly, bypassing the state machine's launch path."""
    attempt = db.next_attempt(conn, item_id)
    with db.transaction(conn):
        row_id = db.insert_session(
            conn,
            work_item_id=item_id,
            session_id=session_id or f"sess-{item_id}-{attempt}",
            attempt=attempt,
            dry_run=dry_run,
            host_socket=host_socket,
        )
        conn.execute(
            "UPDATE sessions SET state = ?, pid = ?, exit_code = ?, signal = ? WHERE id = ?",
            (state, pid, exit_code, signal, row_id),
        )
    return row_id
