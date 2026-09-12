"""``show`` reports what blocks a failed item now, not what blocked it then (issue #63).

The defect: ``blocked_reason`` is stored at the moment of failure and ``show`` printed it
verbatim for ever. Once the maintainer restored the moved clone, ``show`` still told them to
restore it, while ``retry`` — asked the same question at the same moment — refused for the
real reason, an untrusted workspace. The two commands disagreed and neither said it was
quoting a stored value.

Three properties are asserted here that a "does it render" test would not reach:

* ``show``'s blocker is **word for word** ``retry``'s refusal, because both ask one function;
* asking writes nothing — no anomaly for a moved clone, no column — while ``retry`` still
  raises the anomaly it always did;
* a check that cannot complete says so, and never falls back to the stored sentence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import db, dispatch, operations, repos

UNTRUSTED = "the clone has no entry in the trust file. Accept the trust dialog"


@pytest.fixture
def trust(monkeypatch):
    """Whether ``is_trusted`` passes, switchable mid-test. It reads the real
    ``~/.claude.json`` otherwise, which no test should depend on."""
    state = {"trusted": True, "raise": None}

    def fake(path: Any, trust_file: Any = None) -> tuple[bool, str]:
        if state["raise"] is not None:
            raise state["raise"]
        return (True, "trusted in test") if state["trusted"] else (False, UNTRUSTED)

    monkeypatch.setattr(dispatch, "is_trusted", fake)
    return state


@pytest.fixture
def ctx(config, conn, audit, trust):
    return operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=operations.EffectLevel.LIVE,
    )


def failed_item(conn, config, reason: str, *, blocked: str | None = None) -> int:
    item_id = seed_item(conn, state="failed", clone_path=config.repos["demo"].path)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn,
            item_id,
            failure_reason=reason,
            blocked_reason=reason if blocked is None else blocked,
        )
    return item_id


def moved(config) -> tuple[Path, Path]:
    clone = Path(config.repos["demo"].path)
    return clone, clone.with_name(clone.name + "-moved")


def missing_clone_sentence(config) -> str:
    return (
        f"the clone approved for 'demo' is no longer at {config.repos['demo'].path}. "
        "Restore it, or run `robot-army onboard demo --reapprove`"
    )


def blocked_line(result: operations.Result) -> str | None:
    found = [line for line in result.lines if line.startswith("  blocked    : ")]
    assert len(found) <= 1
    return found[0].removeprefix("  blocked    : ") if found else None


def anomaly_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]


# -- the gate's new switch ---------------------------------------------------


def test_check_gates_can_refuse_without_raising_the_anomaly(ctx, conn, config):
    seed_item(conn, clone_path=config.repos["demo"].path)
    repo = repos.resolve(conn, config, "demo")
    clone, away = moved(config)
    clone.rename(away)

    with pytest.raises(dispatch.DispatchBlocked) as quiet:
        dispatch.check_gates(
            conn, boundaries=ctx.boundaries, config=config, repo=repo, raise_anomalies=False
        )
    assert anomaly_count(conn) == 0

    with pytest.raises(dispatch.DispatchBlocked) as loud:
        dispatch.check_gates(conn, boundaries=ctx.boundaries, config=config, repo=repo)
    assert anomaly_count(conn) == 1
    assert str(quiet.value) == str(loud.value) == missing_clone_sentence(config)


# -- the incident ------------------------------------------------------------


def test_the_incident_show_names_the_blocker_retry_refuses_for(ctx, conn, config, trust):
    """Scenario 9: clone moved, item failed, clone restored, workspace untrusted."""
    item_id = failed_item(conn, config, missing_clone_sentence(config))
    trust["trusted"] = False

    shown = operations.show(ctx, item_id)
    refused = operations.retry(ctx, item_id)

    assert refused.lines[1] == f"  workspace trust check failed: {UNTRUSTED}"
    assert blocked_line(shown) == (
        f"workspace trust check failed: {UNTRUSTED} "
        "(checked now; not the reason recorded when it failed)"
    )
    # The failure is history and stays on screen as history.
    assert f"  failure    : {missing_clone_sentence(config)}" in shown.lines
    blocker = shown.data["current_blocker"]
    assert blocker["status"] == "blocked"
    assert blocker["differs_from_recorded"] is True
    assert blocker["reason"] == refused.data["blocked"]
    assert shown.data["item"]["blocked_reason"] == missing_clone_sentence(config)


def test_a_blocker_that_still_holds_is_reported_once_without_claiming_change(
    ctx, conn, config
):
    item_id = failed_item(conn, config, missing_clone_sentence(config))
    clone, away = moved(config)
    clone.rename(away)

    shown = operations.show(ctx, item_id)

    assert blocked_line(shown) == f"{missing_clone_sentence(config)} (checked now)"
    assert shown.data["current_blocker"]["differs_from_recorded"] is False
    assert not any(line.startswith("  recorded   :") for line in shown.lines)


def test_a_cleared_blocker_says_nothing_local_blocks_it_and_names_retry(ctx, conn, config):
    item_id = failed_item(conn, config, missing_clone_sentence(config))

    shown = operations.show(ctx, item_id)

    assert blocked_line(shown) == (
        "nothing on this machine blocks it now (checked now) — "
        f"`robot-army retry {item_id}` re-reads the issue before returning it to the queue"
    )
    assert shown.data["current_blocker"]["status"] == "clear"
    assert f"  failure    : {missing_clone_sentence(config)}" in shown.lines


def test_an_unresolved_repository_is_reported_in_retry_s_words(ctx, conn, config):
    item_id = seed_item(conn, repo_key="elsewhere", state="failed")
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, failure_reason="x", blocked_reason="x")

    shown = operations.show(ctx, item_id)
    refused = operations.retry(ctx, item_id)

    assert refused.lines == [shown.data["current_blocker"]["reason"]]
    assert "does not resolve to a clone any more" in blocked_line(shown)


# -- the failure path ----------------------------------------------------------


def test_a_check_that_cannot_complete_says_so_and_never_quotes_the_stored_reason(
    ctx, conn, config, trust
):
    item_id = failed_item(conn, config, missing_clone_sentence(config))
    trust["raise"] = OSError("the trust file vanished mid-read")

    shown = operations.show(ctx, item_id)

    assert blocked_line(shown) == "could not be checked now: the trust file vanished mid-read"
    assert shown.data["current_blocker"]["status"] == "unknown"
    assert shown.data["current_blocker"]["reason"] is None


# -- reading writes nothing ------------------------------------------------------


def test_show_writes_no_anomaly_and_no_column_while_retry_still_raises_it(ctx, conn, config):
    item_id = failed_item(conn, config, "something else entirely")
    clone, away = moved(config)
    clone.rename(away)
    before = db.get_work_item(conn, item_id)

    for _ in range(3):
        operations.show(ctx, item_id)

    assert anomaly_count(conn) == 0
    assert db.get_work_item(conn, item_id) == before

    operations.retry(ctx, item_id)
    assert anomaly_count(conn) == 1


# -- the stored reason, labelled as stored -----------------------------------------


def test_a_recorded_blocker_that_differs_from_the_failure_is_still_shown(ctx, conn, config):
    item_id = failed_item(conn, config, "the session died", blocked="an older gate refusal")

    shown = operations.show(ctx, item_id)

    assert "  recorded   : an older gate refusal" in shown.lines


def test_an_item_not_in_failed_is_not_checked_and_its_reason_is_labelled(
    ctx, conn, config, trust
):
    item_id = seed_item(conn, state="discovered", clone_path=config.repos["demo"].path)
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, blocked_reason="fingerprint changed")
    trust["raise"] = AssertionError("show must not run the check for a non-failed item")

    shown = operations.show(ctx, item_id)

    assert blocked_line(shown) == "fingerprint changed (recorded, not re-checked)"
    assert shown.data["current_blocker"]["status"] == "not_checked"


def test_an_item_with_nothing_stored_and_not_failed_has_no_blocked_line(ctx, conn, config):
    item_id = seed_item(conn, state="ready", clone_path=config.repos["demo"].path)

    shown = operations.show(ctx, item_id)

    assert blocked_line(shown) is None
    assert shown.data["current_blocker"]["summary"] is None


# -- the web item page ---------------------------------------------------------------


def test_the_item_page_shows_the_current_blocker_and_keeps_the_failure(
    web, conn, config, trust
):
    item_id = failed_item(conn, config, "the clone moved")
    trust["trusted"] = False

    payload = web.get_json(f"/item/{item_id}").json()
    body = web.get(f"/item/{item_id}").text

    assert payload["current_blocker"]["status"] == "blocked"
    assert payload["item"]["blocked_reason"] == "the clone moved"
    assert "not the reason recorded when it failed" in body
    assert "the clone moved" in body


def test_rendering_the_item_page_raises_no_anomaly(web, conn, config, trust):
    item_id = failed_item(conn, config, "the clone moved")
    clone, away = moved(config)
    clone.rename(away)

    web.get(f"/item/{item_id}")
    web.get_json(f"/item/{item_id}")

    assert anomaly_count(conn) == 0
