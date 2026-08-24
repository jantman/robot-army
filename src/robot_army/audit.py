"""The audit log: JSON Lines, append-only, one record per line (research.md R14).

Three things here are load-bearing rather than convenient:

1. **A single redaction choke point.** ``_redact`` is applied to every record on its way
   out, keyed on field name. There is no path to the file that bypasses it, which is
   what makes "the token never reaches the log" testable rather than aspirational
   (Principle II, quickstart scenario 8).
2. **Intent/outcome pairing.** An append-only log cannot amend a record after the fact,
   so the Operating Constraints rule that outward-facing actions be logged *before*
   execution necessarily means two records sharing an ``action_id`` (FR-060). An
   ``intent`` with no ``outcome`` is the signature of a process killed mid-action.
3. **Flush per record.** A record that is still in a buffer when the process dies is not
   a durable record.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

#: Field names whose values never appear in the log, at any depth. Matched
#: case-insensitively on a substring, because upstream JSON uses several spellings
#: (``token``, ``access_token``, ``GITHUB_TOKEN``) and a whitelist would need updating
#: every time one is added — the wrong direction for a secret.
_REDACT_KEY_SUBSTRINGS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)

#: Keys whose values are whole environment dumps. Redacted wholesale rather than
#: per-variable: an environment is an open-ended namespace and we cannot enumerate
#: which of its members are secret.
_ENVIRONMENT_KEYS: tuple[str, ...] = ("env", "environ", "environment")

REDACTED = "<redacted>"


def _key_is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _REDACT_KEY_SUBSTRINGS)


def _redact(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact secret-looking fields.

    Called on every record. Issue titles and bodies pass through untouched — they are
    the prompt, and reconstruction needs them (R14).
    """
    if key is not None and _key_is_secret(key):
        return REDACTED
    if key is not None and key.lower() in _ENVIRONMENT_KEYS and isinstance(value, dict):
        return {k: (REDACTED if _key_is_secret(k) else v) for k, v in value.items()}
    if isinstance(value, dict):
        return {k: _redact(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def redact(record: dict[str, Any]) -> dict[str, Any]:
    """Public name for the choke point, so tests can exercise it directly."""
    return _redact(record)


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class AuditLog:
    """Append-only JSONL writer with a daily file.

    Daily files are never deleted automatically. That satisfies "any rotation policy
    MUST NOT discard records silently" in the simplest way available: nothing is
    discarded, and the maintainer prunes by hand.
    """

    def __init__(self, log_dir: Path, *, component: str = "daemon") -> None:
        self.log_dir = Path(log_dir)
        self.component = component
        self._handle: TextIO | None = None
        self._handle_date: str | None = None

    # -- file management ----------------------------------------------------

    def _today(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def path_for(self, day: str) -> Path:
        return self.log_dir / f"audit-{day}.jsonl"

    def _stream(self) -> TextIO:
        day = self._today()
        if self._handle is None or self._handle_date != day:
            self.close()
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._handle = self.path_for(day).open("a", encoding="utf-8")
            self._handle_date = day
        return self._handle

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.flush()
                os.fsync(self._handle.fileno())
            finally:
                self._handle.close()
            self._handle = None
            self._handle_date = None

    def __enter__(self) -> AuditLog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- writing ------------------------------------------------------------

    def _write(self, record: dict[str, Any]) -> None:
        safe = redact(record)
        stream = self._stream()
        stream.write(json.dumps(safe, separators=(",", ":"), default=str) + "\n")
        stream.flush()

    def record(
        self,
        action: str,
        *,
        outcome: str,
        entity_type: str | None = None,
        entity_id: object = None,
        detail: dict[str, Any] | None = None,
        target: str | None = None,
        dry_run: bool = False,
        simulated: bool = False,
        action_id: str | None = None,
        kind: str = "event",
    ) -> dict[str, Any]:
        """Write one record. Every field Principle III enumerates is present."""
        rec: dict[str, Any] = {
            "ts": utc_now_iso(),
            "component": self.component,
            "kind": kind,
            "action": action,
            "outcome": outcome,
        }
        if action_id is not None:
            rec["action_id"] = action_id
        if entity_type is not None:
            rec["entity_type"] = entity_type
        if entity_id is not None:
            rec["entity_id"] = entity_id
        if target is not None:
            rec["target"] = target
        if dry_run:
            rec["dry_run"] = True
        if simulated:
            rec["simulated"] = True
        if detail:
            rec["detail"] = detail
        self._write(rec)
        return rec

    def error(
        self,
        action: str,
        *,
        error: BaseException | str,
        entity_type: str | None = None,
        entity_id: object = None,
        detail: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> None:
        """Record a failure. There is no path in this codebase that swallows one silently."""
        payload = dict(detail or {})
        if isinstance(error, BaseException):
            payload["error_type"] = type(error).__name__
            payload["error"] = str(error)
        else:
            payload["error"] = error
        self.record(
            action,
            outcome="error",
            entity_type=entity_type,
            entity_id=entity_id,
            detail=payload,
            dry_run=dry_run,
        )

    # -- intent / outcome pairing (FR-060) ----------------------------------

    @contextmanager
    def action(
        self,
        action: str,
        *,
        entity_type: str | None = None,
        entity_id: object = None,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
        dry_run: bool = False,
        simulated: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Wrap an outward-facing action in an ``intent``/``outcome`` pair.

        The ``intent`` record is written *and flushed* before the body runs, so a
        process killed mid-action leaves an intent with no outcome — which is precisely
        the crash signature Principle IV asks to be detectable on the next run.

        The yielded dict is the outcome's ``detail``: mutate it to attach whatever the
        action learned (a URL, an exit code, a window id).
        """
        action_id = uuid.uuid4().hex
        base = dict(detail or {})
        self.record(
            action,
            outcome="pending",
            kind="intent",
            action_id=action_id,
            entity_type=entity_type,
            entity_id=entity_id,
            target=target,
            detail=base,
            dry_run=dry_run,
            simulated=simulated,
        )
        result: dict[str, Any] = {}
        try:
            yield result
        except BaseException as exc:
            merged = {**base, **result, "error_type": type(exc).__name__, "error": str(exc)}
            self.record(
                action,
                outcome="error",
                kind="outcome",
                action_id=action_id,
                entity_type=entity_type,
                entity_id=entity_id,
                target=target,
                detail=merged,
                dry_run=dry_run,
                simulated=simulated,
            )
            raise
        else:
            self.record(
                action,
                outcome="ok",
                kind="outcome",
                action_id=action_id,
                entity_type=entity_type,
                entity_id=entity_id,
                target=target,
                detail={**base, **result},
                dry_run=dry_run,
                simulated=simulated,
            )


def read_records(log_dir: Path) -> Iterator[tuple[dict[str, Any] | None, str]]:
    """Yield ``(record, raw_line)`` for every line in every daily file, oldest first.

    A record is ``None`` when the line does not parse. Readers skip and *count* those
    rather than failing: R14 flushes per line, so an interrupted final write can leave a
    partial line, and refusing to read the log because of it would be the wrong trade.
    """
    for path in sorted(Path(log_dir).glob("audit-*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    yield json.loads(line), line
                except json.JSONDecodeError:
                    yield None, line
