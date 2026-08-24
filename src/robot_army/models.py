"""Dataclasses for every persisted entity, per data-model.md.

These exist so the state machine's types are explicit rather than dynamic (research.md
R2). They are plain data — no behaviour, no persistence knowledge — and ``db.py``'s
row factory turns ``sqlite3.Row`` into them.

Note what is *not* here: Isolated Checkout, which collapsed into columns on
``WorkItem``, and Audit Record, which is the JSONL file rather than a table. Both
collapses are argued in data-model.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from typing import Any, Self

from robot_army.states import SessionState, WorkItemState


@dataclass(frozen=True, slots=True)
class Repo:
    """Onboarding state. A row exists only once the maintainer has onboarded (FR-001)."""

    repo_key: str
    onboarded_at: str
    fingerprint_approved_at: str
    settings_fingerprint: str | None = None
    trust_verified_at: str | None = None

    @property
    def fingerprint(self) -> dict[str, str]:
        """The approved path → SHA-256 mapping. ``NULL`` means "no committed settings"."""
        if not self.settings_fingerprint:
            return {}
        return json.loads(self.settings_fingerprint)


@dataclass(frozen=True, slots=True)
class WorkItem:
    id: int
    source: str
    source_id: str
    source_url: str
    repo_key: str
    issue_number: int
    title: str
    body: str
    labels: str
    state: WorkItemState
    dry_run: bool
    discovered_at: str
    updated_at: str
    worktree_path: str | None = None
    branch: str | None = None
    prepare_output: str | None = None
    failure_reason: str | None = None
    blocked_reason: str | None = None
    ready_at: str | None = None
    dispatching_at: str | None = None
    active_at: str | None = None
    ended_at: str | None = None
    done_at: str | None = None

    @property
    def label_list(self) -> list[str]:
        return json.loads(self.labels)


@dataclass(frozen=True, slots=True)
class Session:
    id: int
    work_item_id: int
    session_id: str
    attempt: int
    state: SessionState
    dry_run: bool
    started_at: str
    pid: int | None = None
    proc_start: str | None = None
    scope: str | None = None
    host_socket: str | None = None
    window_id: int | None = None
    launch_argv: str | None = None
    exit_code: int | None = None
    signal: int | None = None
    confirmed_at: str | None = None
    ended_at: str | None = None

    @property
    def argv(self) -> list[str]:
        return json.loads(self.launch_argv) if self.launch_argv else []


@dataclass(frozen=True, slots=True)
class Anomaly:
    """A condition detected but not resolvable by the system (FR-065)."""

    id: int
    kind: str
    detail: str
    detected_at: str
    entity_type: str | None = None
    entity_id: str | None = None
    acknowledged_at: str | None = None

    @property
    def detail_obj(self) -> dict[str, Any]:
        return json.loads(self.detail)


@dataclass(frozen=True, slots=True)
class PollState:
    """Per-repository polling bookkeeping. High churn, kept out of ``repos``."""

    repo_key: str
    consecutive_failures: int = 0
    etag: str | None = None
    last_polled_at: str | None = None
    last_status: int | None = None
    backoff_until: str | None = None


#: Every anomaly kind the system can raise. Named here so ``status`` and ``anomalies``
#: can surface all of them (FR-065, T135) rather than only the ones seen so far.
ANOMALY_KINDS: tuple[str, ...] = (
    "orphan_session",
    "dispatching_timeout",
    "no_transcript",
    "session_id_mismatch",
    "registry_version_unknown",
    "config_missing_repo",
    "prunable_worktree",
    "malformed_exit_record",
    "orphan_exit_record",
    "stale_socket",
)


def _coerce(value: Any, annotation: Any) -> Any:
    """Turn a SQLite column into the dataclass's declared type.

    Only three coercions are needed: ``bool`` (SQLite has no boolean type) and the two
    state enums. Everything else is already the right Python type.
    """
    if value is None:
        return None
    if annotation is bool or annotation == "bool":
        return bool(value)
    if annotation is WorkItemState or annotation == "WorkItemState":
        return WorkItemState(value)
    if annotation is SessionState or annotation == "SessionState":
        return SessionState(value)
    return value


def from_row(cls: type[Any], row: Any) -> Any:
    """Build a dataclass from a mapping-like row, coercing declared types."""
    kwargs = {}
    for f in fields(cls):
        if f.name in row.keys():  # noqa: SIM118 - sqlite3.Row has no __contains__
            kwargs[f.name] = _coerce(row[f.name], f.type)
    return cls(**kwargs)


class _RowMixin:
    @classmethod
    def from_row(cls, row: Any) -> Self:
        return from_row(cls, row)


#: Table name → dataclass, used by db.py to pick a row factory per query.
ROW_TYPES: dict[str, type[Any]] = {
    "repos": Repo,
    "work_items": WorkItem,
    "sessions": Session,
    "anomalies": Anomaly,
    "poll_state": PollState,
}
