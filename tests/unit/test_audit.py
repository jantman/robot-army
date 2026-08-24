"""Redaction, intent/outcome pairing, and tolerant reading (T025).

The redaction assertion is the load-bearing one: quickstart scenario 8 greps the whole log
directory for a token prefix and expects nothing. That guarantee is only real if there is
no path to the file that bypasses the choke point.
"""

from __future__ import annotations

import json

import pytest

from robot_army.audit import REDACTED, AuditLog, read_records, redact

TOKEN = "ghp_" + "S" * 36


def read_all(layout) -> list[dict]:
    return [record for record, _ in read_records(layout.log_dir) if record is not None]


def raw_text(layout) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in layout.log_dir.glob("*.jsonl"))


def test_a_token_never_reaches_the_file(layout):
    log = AuditLog(layout.log_dir)
    log.record(
        "github.request",
        outcome="ok",
        detail={
            "token": TOKEN,
            "GITHUB_TOKEN": TOKEN,
            "headers": {"Authorization": f"Bearer {TOKEN}"},
            "nested": [{"access_token": TOKEN}],
        },
    )
    log.close()
    text = raw_text(layout)
    assert TOKEN not in text, "a token reached the audit log"
    assert text.count(REDACTED) >= 4


def test_environment_dumps_redact_secret_members_but_keep_the_rest(layout):
    log = AuditLog(layout.log_dir)
    log.record(
        "kitty.launch",
        outcome="ok",
        detail={"env": {"ROBOT_ARMY_ITEM": "42", "ROBOT_ARMY_GITHUB_TOKEN": TOKEN}},
    )
    log.close()
    record = read_all(layout)[0]
    env = record["detail"]["env"]
    assert env["ROBOT_ARMY_ITEM"] == "42", "non-secret env values stay legible"
    assert env["ROBOT_ARMY_GITHUB_TOKEN"] == REDACTED


def test_issue_bodies_are_not_redacted(layout):
    """They are the prompt, and reconstruction needs them (R14)."""
    log = AuditLog(layout.log_dir)
    log.record("poll.discovered", outcome="ok", detail={"title": "Fix login", "body": "steps"})
    log.close()
    record = read_all(layout)[0]
    assert record["detail"]["body"] == "steps"


def test_redact_is_reachable_directly_for_any_caller():
    assert redact({"password": "hunter2"})["password"] == REDACTED
    assert redact({"count": 3})["count"] == 3


def test_every_record_carries_the_principle_iii_fields(layout):
    log = AuditLog(layout.log_dir, component="daemon")
    log.record(
        "git.fetch",
        outcome="ok",
        entity_type="work_item",
        entity_id=7,
        target="/clone",
        detail={"ref": "main"},
    )
    log.close()
    record = read_all(layout)[0]
    assert record["ts"].endswith("Z")
    assert record["component"] == "daemon"
    assert record["action"] == "git.fetch"
    assert record["outcome"] == "ok"
    assert record["entity_id"] == 7
    assert record["target"] == "/clone"


def test_intent_is_written_before_the_action_runs(layout):
    """An append-only log cannot amend a record after the fact, so "log before" plus
    "record the result" necessarily means two records (FR-060)."""
    log = AuditLog(layout.log_dir)
    with log.action("github.comment", target="demo#1") as outcome:
        # The intent must already be on disk while the body is still running.
        assert "intent" in raw_text(layout)
        outcome["comment_url"] = "https://example.invalid/c1"
    log.close()

    records = read_all(layout)
    assert [r["kind"] for r in records] == ["intent", "outcome"]
    assert records[0]["action_id"] == records[1]["action_id"]
    assert records[0]["outcome"] == "pending"
    assert records[1]["outcome"] == "ok"
    assert records[1]["detail"]["comment_url"] == "https://example.invalid/c1"


def test_a_failing_action_records_an_outcome_and_re_raises(layout):
    log = AuditLog(layout.log_dir)
    with pytest.raises(RuntimeError), log.action("git.fetch", target="/clone"):
        raise RuntimeError("remote unreachable")
    log.close()

    records = read_all(layout)
    assert records[1]["kind"] == "outcome"
    assert records[1]["outcome"] == "error"
    assert "remote unreachable" in records[1]["detail"]["error"]


def test_an_intent_with_no_outcome_is_the_crash_signature(layout):
    """This pairing is what makes a process killed mid-action visible on the next run."""
    log = AuditLog(layout.log_dir)
    log.record("github.comment", outcome="pending", kind="intent", action_id="abc")
    log.close()

    records = read_all(layout)
    intents = {r["action_id"] for r in records if r["kind"] == "intent"}
    outcomes = {r.get("action_id") for r in records if r["kind"] == "outcome"}
    assert intents - outcomes == {"abc"}


def test_simulated_records_are_marked(layout):
    log = AuditLog(layout.log_dir)
    log.record("git.add_worktree", outcome="ok", simulated=True, detail={})
    log.close()
    assert read_all(layout)[0]["simulated"] is True


def test_a_partial_final_line_is_skipped_and_counted_not_fatal(layout):
    """R14 flushes per record, so an interrupted final write can leave a partial line.
    Refusing to read the log because of it would be the wrong trade."""
    log = AuditLog(layout.log_dir)
    log.record("a", outcome="ok")
    log.record("b", outcome="ok")
    path = log.path_for(log._today())
    log.close()
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"ts":"2026-08-23T00:00:00Z","act')

    parsed = [record for record, _ in read_records(layout.log_dir)]
    assert len(parsed) == 3
    assert parsed[-1] is None
    assert [r["action"] for r in parsed[:2]] == ["a", "b"]


def test_records_are_flushed_per_line_not_buffered(layout):
    log = AuditLog(layout.log_dir)
    log.record("first", outcome="ok")
    # No close() yet: a record still sitting in a buffer when the process dies is not a
    # durable record, which is the whole point of flushing per line.
    assert json.loads(raw_text(layout).splitlines()[0])["action"] == "first"
    log.close()


def test_error_helper_records_the_exception_type(layout):
    log = AuditLog(layout.log_dir)
    log.error("github.poll", error=TimeoutError("too slow"), entity_id="demo")
    log.close()
    record = read_all(layout)[0]
    assert record["outcome"] == "error"
    assert record["detail"]["error_type"] == "TimeoutError"
