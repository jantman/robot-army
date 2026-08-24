"""No response served by the interface contains a secret (T072, FR-020, SC-012).

Enforced at the boundary of what may be rendered rather than by filtering output: payloads
originate from ``operations.*``, which pass through ``audit.redact`` on their way to the
log, and the audit view renders records that were redacted at write time. The token is
never in a payload to begin with — this asserts that stays true, across every view
**including the error paths**, which is where a leak would actually happen.
"""

from __future__ import annotations

import json
import os

import pytest
from tests.conftest import config_dict, monkey_token, seed_item, seed_session

from robot_army import audit, db
from robot_army.config import parse

#: A value shaped like a real credential, distinctive enough that finding it anywhere in a
#: response is unambiguous.
TOKEN = "ghp_" + "S3cr3tT0k3nV4lu3" + "0" * 20


@pytest.fixture
def secret_config(repo_clone, layout, tmp_path, monkeypatch):
    """A config whose token is resolvable, exactly as it is in production."""
    monkeypatch.setenv("ROBOT_ARMY_SECRET_TEST_TOKEN", TOKEN)
    monkey_token()
    return parse(
        config_dict(
            repo_clone,
            layout,
            tmp_path / "worktrees",
            github={"token_env": "ROBOT_ARMY_SECRET_TEST_TOKEN"},
        ),
        tmp_path / "config.toml",
    )


@pytest.fixture
def secret_web(secret_config, conn, layout, monkeypatch):
    from tests.conftest import (
        FakeIssueReader,
        StubDisplay,
        StubSessionHost,
        WebHarness,
        make_boundaries,
    )

    from robot_army import operations
    from robot_army.web.server import WebApp

    reader, display, host = FakeIssueReader(), StubDisplay(), StubSessionHost()
    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log: make_boundaries(
            log, level=level, reader=reader, display=display, host=host
        ),
    )
    operations.clear_resume_signal_cache()
    return WebHarness(
        WebApp(secret_config), reader=reader, display=display, host=host, vcs=None
    )


EVERY_VIEW = (
    "/",
    "/active",
    "/queue",
    "/interrupted",
    "/anomalies",
    "/log",
    "/item/1",
    "/item/1/confirm/abandon",
    "/static/app.css",
    "/static/app.js",
)


def _populate(conn, layout):
    """Rows in every state, an anomaly, and a log record that *tried* to carry the token."""
    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="lost")
    seed_item(conn, issue_number=2, state="failed")
    seed_item(conn, issue_number=3, state="ready", dry_run=True)
    with db.transaction(conn):
        # An anomaly's detail is stored rather than only logged, so the audit log's
        # redaction never saw it. Rendering it is a real path to a leak, which is why it
        # is here rather than in a comment.
        db.raise_anomaly(
            conn, kind="orphan_session", entity_id="s-1", detail={"pid": 1, "token": TOKEN}
        )
        db.update_work_item_columns(conn, item_id, failure_reason="auth failed")
    return item_id


def _assert_clean(response, where: str) -> None:
    assert TOKEN not in response.text, f"{where} leaked the configured token"


def test_no_view_contains_the_token(secret_web, conn, layout):
    _populate(conn, layout)
    for path in EVERY_VIEW:
        _assert_clean(secret_web.get(path), path)
        if path.startswith("/static"):
            continue
        _assert_clean(secret_web.get_json(path), f"{path} (json)")


def test_no_error_path_contains_the_token(secret_web, conn, layout):
    """The error paths are where a leak would actually happen: a message built from a
    reason built from a config value."""
    _populate(conn, layout)
    attempts = [
        ("GET", "/nope"),
        ("GET", "/item/9999"),
        ("GET", "/item/9999/confirm/resume"),
        ("GET", "/log?item=abc"),
        ("POST", "/item/9999/abandon"),
        ("POST", "/item/1/resume"),
        ("POST", "/anomalies/999/acknowledge"),
        ("POST", "/poll"),
    ]
    for method, path in attempts:
        response = secret_web.request(method, path, form={} if method == "POST" else None)
        assert response.status >= 300 or response.status == 200
        _assert_clean(response, f"{method} {path}")
        _assert_clean(
            secret_web.request(
                method, path, form={} if method == "POST" else None, accept="application/json"
            ),
            f"{method} {path} (json)",
        )


def test_an_anomaly_detail_is_redacted_before_either_front_end_renders_it(
    secret_web, conn, layout
):
    """The one path the audit log's choke point could not cover.

    Anomaly details are *stored*, so nothing redacted them on the way in. Both front ends
    render that dict, so it passes through ``audit.redact`` in ``_anomaly_dict`` — once,
    rather than being filtered separately in each.
    """
    _populate(conn, layout)
    payload = secret_web.get_json("/anomalies").json()
    assert payload["anomalies"][0]["detail"]["token"] == audit.REDACTED
    assert payload["anomalies"][0]["detail"]["pid"] == 1, "the legible part stays legible"
    _assert_clean(secret_web.get("/anomalies"), "/anomalies")


def test_a_secret_in_a_submitted_form_is_redacted_before_it_reaches_the_log(
    secret_web, conn, layout
):
    """Every POST records its form in the audit detail, and that detail goes through the
    same choke point on its way to the file."""
    _populate(conn, layout)
    secret_web.post_json("/poll", form={"repo": "demo", "token": TOKEN})

    written = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(layout.log_dir.glob("audit-*.jsonl"))
    )
    assert TOKEN not in written, "the token reached the log"
    assert "<redacted>" in written
    _assert_clean(secret_web.get("/log"), "/log")


def test_the_honest_limit_is_free_text_the_author_typed_themselves(secret_web, conn, layout):
    """Stated rather than papered over.

    Redaction is keyed on **field name**. A credential pasted into an issue body or a
    failure reason is free text, and ``docs/logging.md`` already records the decision not
    to redact issue titles and bodies — they are the prompt, and reconstruction needs them.
    What FR-020 forbids is the interface *introducing* the configured secret, and that is
    what this asserts: it is nowhere in a response unless the database already held it.
    """
    item_id = _populate(conn, layout)
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, blocked_reason=f"typed by hand: {TOKEN}")

    assert TOKEN in secret_web.get(f"/item/{item_id}").text, "free text renders as stored"

    # But nothing the interface itself assembles carries it.
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, blocked_reason=None)
    for path in ("/active", "/queue", "/interrupted", "/anomalies", "/log", f"/item/{item_id}"):
        _assert_clean(secret_web.get_json(path), path)
        _assert_clean(secret_web.get(path), path)


def test_the_config_object_never_holds_the_token(secret_config):
    """It is resolved at the moment it is needed and never stored, which is why no payload
    can carry it by accident."""
    assert TOKEN not in json.dumps(str(secret_config))
    assert secret_config.github.read_token() == TOKEN
    assert os.environ["ROBOT_ARMY_SECRET_TEST_TOKEN"] == TOKEN
