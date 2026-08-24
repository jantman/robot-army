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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from robot_army.effects import EffectLevel
from robot_army.paths import Layout, default_config_path

#: Things that look like a credential rather than a variable name. GitHub's own token
#: prefixes plus a generic long-opaque-string check.
_TOKEN_PATTERNS = (
    re.compile(r"^gh[pousr]_[A-Za-z0-9]{20,}$"),
    re.compile(r"^github_pat_[A-Za-z0-9_]{20,}$"),
)

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
    key: str
    path: Path
    base_branch: str
    post_create: tuple[HookStep, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    permission_mode: str | None = None
    model: str | None = None


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
class WorkerConfig:
    permission_mode: str = "auto"
    model: str = ""
    base_branch: str = "main"
    branch_prefix: str = "robot-army"
    binary: str = "claude"


@dataclass(frozen=True, slots=True)
class TerminalConfig:
    socket_glob: str = "/tmp/mykitty-*"  # noqa: S108 - kitty's listen_on convention
    probe_timeout_seconds: int = 2
    binary: str = "kitty"


@dataclass(frozen=True, slots=True)
class HealthConfig:
    max_age_seconds: int = 180
    webhook_url: str = ""


@dataclass(frozen=True, slots=True)
class HooksConfig:
    default_timeout_seconds: int = 300


@dataclass(frozen=True, slots=True)
class Config:
    path: Path
    daemon: DaemonConfig
    github: GitHubConfig
    worker: WorkerConfig
    terminal: TerminalConfig
    health: HealthConfig
    hooks: HooksConfig
    repos: dict[str, RepoConfig]
    worktree_root: Path
    layout: Layout
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

    def base_branch_for(self, key: str) -> str:
        repo = self.repos.get(key)
        if repo and repo.base_branch:
            return repo.base_branch
        return self.worker.base_branch


# -- loading ---------------------------------------------------------------

_TOP_LEVEL_SECTIONS = {
    "daemon",
    "paths",
    "github",
    "worker",
    "terminal",
    "health",
    "hooks",
    "repos",
}

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
    "paths": {"worktree_root", "state_dir", "socket_dir"},
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
    "hooks": {"default_timeout_seconds"},
}

_REPO_KEYS = {"path", "base_branch", "post_create", "env", "permission_mode", "model"}
_STEP_KEYS = {"run", "link", "copy", "timeout"}


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
        base_branch=_str("worker", "base_branch", "main"),
        branch_prefix=_str("worker", "branch_prefix", "robot-army"),
        binary=_str("worker", "binary", "claude"),
    )

    # -- [terminal] --------------------------------------------------------
    socket_glob = _str("terminal", "socket_glob", "/tmp/mykitty-*")  # noqa: S108
    if "*" not in socket_glob and "?" not in socket_glob:
        # kitty appends its PID to listen_on, so a fixed path can only ever be stale.
        warnings.append(
            f"[terminal] socket_glob {socket_glob!r} contains no wildcard; kitty appends "
            "its PID to listen_on, so this will not match a live socket after a restart"
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

    # -- [paths] -----------------------------------------------------------
    paths_raw = raw.get("paths", {})
    worktree_root = Path(str(paths_raw.get("worktree_root", "~/worktrees"))).expanduser()
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

        path_raw = section.get("path")
        if not path_raw:
            problems.append(f"[repos.{key}] path is required")
            continue
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

        repos[key] = RepoConfig(
            key=key,
            path=repo_path,
            base_branch=str(section.get("base_branch", worker.base_branch)),
            post_create=tuple(steps),
            env=env,
            permission_mode=str(repo_mode) if repo_mode else None,
            model=str(section["model"]) if section.get("model") else None,
        )

    # A cross-field check that is a warning rather than an error, because the maintainer
    # may deliberately want a short leash: FR-041's sweep should not fire before the
    # longest repo's preparation could plausibly finish.
    longest = max(
        (sum(s.timeout for s in r.post_create) for r in repos.values()),
        default=0,
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
        repos=repos,
        worktree_root=worktree_root,
        layout=layout,
        warnings=tuple(warnings),
    )


def _parse_steps(
    repo_key: str, raw_steps: Any, hooks: HooksConfig
) -> tuple[list[HookStep], list[str]]:
    problems: list[str] = []
    steps: list[HookStep] = []
    if not isinstance(raw_steps, list):
        return [], [f"[repos.{repo_key}] post_create must be an array of tables"]
    for index, step in enumerate(raw_steps):
        where = f"[repos.{repo_key}] post_create[{index}]"
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
