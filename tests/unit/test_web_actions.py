"""Action handling: re-validation, double submission, and the audit invariant.

Covers T038 and T056. The two cases a phone produces constantly are a page rendered
minutes ago and acted on now, and the same action arriving twice — so both are tested
against the mechanism that actually guards them, which is ``states.transition_work_item``
under ``BEGIN IMMEDIATE`` and not anything in the web layer (R7).
"""

from __future__ import annotations

import json
import threading

import pytest
from tests.conftest import beat, make_issue, seed_item, seed_session

from robot_army import db, operations
from robot_army.states import SessionState, WorkItemState


def web_records(layout, *, action: str | None = None) -> list[dict]:
    records = []
    for path in sorted(layout.log_dir.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("component") != "web":
                continue
            if action is None or record.get("action") == action:
                records.append(record)
    return records


def state_of(conn, item_id) -> str:
    return db.get_work_item(conn, item_id).state


# -- FR-027: re-validated at submission -------------------------------------


def test_an_action_against_a_state_that_changed_is_refused_with_the_reason(web, conn):
    """The page was rendered when the item was interrupted; it is abandoned now."""
    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="lost")

    rendered = web.get(f"/item/{item_id}").text
    assert "confirm/abandon" in rendered

    # Something else acts on it — a terminal command, or another tab.
    with db.transaction(conn):
        conn.execute("UPDATE work_items SET state = 'abandoned' WHERE id = ?", (item_id,))

    response = web.post_json(f"/item/{item_id}/abandon")
    assert response.status == 409
    payload = response.json()
    assert payload["code"] == 3
    assert "abandoned" in payload["reason"]
    assert payload["state"] == "abandoned"


def test_the_confirm_page_refuses_rather_than_offering_a_form_when_the_item_moved(web, conn):
    """R8: this is where FR-027's re-validation becomes visible *before* the tap."""
    item_id = seed_item(conn, state="failed")
    offered = web.get(f"/item/{item_id}/confirm/retry")
    assert offered.status == 200
    assert f'action="/item/{item_id}/retry"' in offered.text

    with db.transaction(conn):
        conn.execute("UPDATE work_items SET state = 'abandoned' WHERE id = ?", (item_id,))

    refused = web.get(f"/item/{item_id}/confirm/retry")
    assert refused.status == 409
    assert "<form" not in refused.text, "no form at all — not a form that will fail"
    assert "not legal from that state" in refused.text


def test_a_control_is_refused_even_when_it_was_never_offered(web, conn):
    """FR-029's other half: not offering it is not enough, because the URL can be typed."""
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state="running")
    assert "resume" not in web.get_json(f"/item/{item_id}").json()["actions"]

    response = web.post_json(f"/item/{item_id}/resume")
    assert response.status in (409, 503)
    assert state_of(conn, item_id) == WorkItemState.ACTIVE


def test_an_action_against_an_item_that_does_not_exist_is_a_clean_not_found(web):
    """Purged simulated rows, or an identifier typed into the address bar by hand."""
    response = web.post_json("/item/4242/abandon")
    assert response.status == 404
    assert response.json()["reason"] == "no work item with id 4242"
    assert response.json()["code"] == 1


# -- FR-028 / SC-003: the same action twice ---------------------------------


def test_three_concurrent_identical_resumes_produce_exactly_one_session(
    web, conn, layout, running_daemon, config
):
    """The guard is the state machine under ``BEGIN IMMEDIATE``, not the web layer.

    A double tap, another tab, and a terminal command all land the same way: the first
    transition wins and the rest find an illegal transition.
    """
    beat(layout, effect_level="live")
    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="lost")

    before = len(db.list_sessions_for_item(conn, item_id))
    accepted: list[int] = []
    lock = threading.Lock()

    def submit() -> None:
        response = web.post_json(f"/item/{item_id}/resume")
        with lock:
            accepted.append(response.status)

    threads = [threading.Thread(target=submit) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    web.app._work.join()

    assert sorted(accepted).count(303) >= 1
    after = len(db.list_sessions_for_item(conn, item_id))
    assert after - before <= 1, "a double tap must not produce a second session"


def test_a_repeated_abandon_is_refused_with_a_reason_not_applied_twice(web, conn):
    item_id = seed_item(conn, state="interrupted")
    assert web.post_json(f"/item/{item_id}/abandon").status == 303
    assert state_of(conn, item_id) == WorkItemState.ABANDONED

    second = web.post_json(f"/item/{item_id}/abandon")
    assert second.status == 409
    assert state_of(conn, item_id) == WorkItemState.ABANDONED


def test_every_post_answers_303_so_a_reload_re_issues_a_get(web, conn):
    """R7's browser half. A reload after a POST must never re-post."""
    item_id = seed_item(conn, state="interrupted")
    response = web.post(f"/item/{item_id}/abandon")
    assert response.status == 303
    location = response.headers["Location"]
    assert location.startswith("/item/")
    assert "msg=abandoned" in location

    # What the browser then does is a GET, and it succeeds.
    assert web.get(location).status == 200


def test_the_redirect_returns_to_the_referring_view(web, conn):
    item_id = seed_item(conn, state="interrupted")
    response = web.post(
        f"/item/{item_id}/abandon",
        headers={"Referer": "http://localhost:8420/interrupted?include_simulated=1"},
    )
    assert response.headers["Location"].startswith("/interrupted")


def test_a_foreign_referer_is_not_used_as_a_redirect_target(web, conn):
    """An open redirect on an interface where reaching it is reaching everything.

    This is about the *redirect target* only. Whether such a request is performed at all is
    a separate question, answered by the same-origin check below — a bare ``Referer`` with
    no ``Origin`` and no ``Sec-Fetch-Site`` is not something a browser produces for a
    cross-site POST, so it is not the signal to refuse on.
    """
    item_id = seed_item(conn, state="interrupted")
    response = web.post(
        f"/item/{item_id}/abandon", headers={"Referer": "https://evil.example/steal"}
    )
    assert response.headers["Location"].startswith(f"/item/{item_id}")


# -- cross-site request forgery ---------------------------------------------
#
# The gap the spec's exposure model does not cover. FR-003 reasons about *network*
# reachability to the port and accepts full control for anything that has it. A forged
# request needs no network path to a loopback-bound port at all — only the author's own
# browser, already inside the trust boundary, with some unrelated page open.


CROSS_SITE = {
    "origin": "https://evil.example",
    "referer": "https://evil.example/page",
    "sec-fetch-site": "cross-site",
}


@pytest.mark.parametrize(
    ("path", "state"),
    [
        ("/item/{id}/abandon", "interrupted"),
        ("/item/{id}/restart", "interrupted"),
        ("/item/{id}/cancel", "active"),
        ("/item/{id}/retry", "failed"),
        ("/item/{id}/attach", "active"),
    ],
)
def test_a_cross_site_item_action_is_refused_and_changes_nothing(web, conn, path, state):
    item_id = seed_item(conn, state=state)
    seed_session(conn, item_id, state="running" if state == "active" else "lost")
    before = state_of(conn, item_id)

    response = web.post_json(path.format(id=item_id), headers=CROSS_SITE)
    assert response.status == 403
    assert response.json()["code"] == 3
    assert state_of(conn, item_id) == before


@pytest.mark.parametrize("path", ["/dispatch/pause", "/dispatch/unpause", "/poll", "/reconcile"])
def test_a_cross_site_system_action_is_refused(web, conn, path):
    """Pausing dispatch from someone else's page is not less serious for being reversible."""
    assert web.post_json(path, headers=CROSS_SITE).status == 403
    assert db.get_dispatch_control(conn).paused is False


def test_the_sec_fetch_site_header_alone_is_enough_to_refuse(web, conn):
    """Browsers send it on every request, including ones carrying no Origin."""
    item_id = seed_item(conn, state="interrupted")
    response = web.post_json(
        f"/item/{item_id}/abandon", headers={"sec-fetch-site": "cross-site"}
    )
    assert response.status == 403
    assert state_of(conn, item_id) == WorkItemState.INTERRUPTED


def test_an_origin_that_does_not_match_the_host_is_refused(web, conn):
    item_id = seed_item(conn, state="interrupted")
    response = web.post_json(
        f"/item/{item_id}/abandon", headers={"origin": "http://192.168.1.99:8420"}
    )
    assert response.status == 403
    assert "192.168.1.99" in response.json()["reason"]


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_the_interfaces_own_forms_are_allowed(web, conn, site):
    """``same-origin`` is a form on a page we served; ``none`` is a typed URL or a bookmark."""
    item_id = seed_item(conn, state="interrupted")
    response = web.post_json(
        f"/item/{item_id}/abandon",
        headers={
            "origin": "http://localhost:8420",
            "sec-fetch-site": site,
            "referer": f"http://localhost:8420/item/{item_id}/confirm/abandon",
        },
    )
    assert response.status == 303
    assert state_of(conn, item_id) == WorkItemState.ABANDONED


def test_a_client_that_sends_neither_header_is_allowed_through(web, conn):
    """``curl`` sends neither, and the quickstart drives every control with it.

    Refusing those would break the documented terminal path to protect against a client
    that has no need of forgery — it can reach the port directly, which is the model the
    spec already accepts.
    """
    item_id = seed_item(conn, state="interrupted")
    assert web.post_json(f"/item/{item_id}/abandon").status == 303
    assert state_of(conn, item_id) == WorkItemState.ABANDONED


def test_a_refused_cross_site_request_still_leaves_a_record(web, conn, layout):
    """It is the only way one would ever be noticed."""
    item_id = seed_item(conn, state="interrupted")
    web.post_json(f"/item/{item_id}/abandon", headers=CROSS_SITE)

    records = web_records(layout, action="web.abandon")
    assert [r["kind"] for r in records] == ["intent", "outcome"]
    assert records[0]["detail"]["origin"] == "https://evil.example"
    assert records[0]["detail"]["sec_fetch_site"] == "cross-site"
    assert records[-1]["outcome"] == "error"


def test_read_views_are_not_origin_checked(web, conn):
    """A GET changes nothing, and refusing one would break linking to the interface."""
    seed_item(conn, state="ready")
    for path in ("/active", "/queue", "/log"):
        assert web.get(path, headers=CROSS_SITE).status == 200, path


# -- FR-038 / FR-039 / FR-040: the audit invariant --------------------------


@pytest.mark.parametrize(
    ("path", "state"),
    [
        ("abandon", "interrupted"),
        ("retry", "failed"),
        ("cancel", "active"),
        ("attach", "active"),
        ("resume", "interrupted"),
        ("restart", "interrupted"),
    ],
)
def test_every_item_action_writes_an_intent_before_it_acts(web, conn, layout, path, state):
    item_id = seed_item(conn, state=state)
    seed_session(
        conn, item_id, state="running" if state == "active" else "lost"
    )
    web.post_json(f"/item/{item_id}/{path}")

    records = web_records(layout, action=f"web.{path}")
    assert records, f"{path} left no record at all"
    assert records[0]["kind"] == "intent"
    assert records[0]["outcome"] == "pending"
    assert records[0]["entity_type"] == "work_item"
    assert records[0]["entity_id"] == item_id


def test_no_error_response_is_returned_without_a_record(web, conn, layout):
    """FR-039, asserted across every refusal this interface can produce.

    The invariant is structural: every POST passes through ``_perform``, which writes the
    intent *before* any check runs. There is no path that refuses earlier.
    """
    missing = seed_item(conn, issue_number=1, state="done")
    attempts = [
        ("/item/4242/abandon", {}),
        (f"/item/{missing}/retry", {}),
        (f"/item/{missing}/resume", {}),
        (f"/item/{missing}/cancel", {}),
        ("/anomalies/999/acknowledge", {}),
    ]
    for path, form in attempts:
        before = len(web_records(layout))
        response = web.post_json(path, form=form)
        assert response.status >= 400, path
        after = web_records(layout)
        assert len(after) > before, f"{path} produced {response.status} with no record"
        assert after[-1]["outcome"] == "error", path
        assert after[-1]["detail"].get("error"), path


def test_the_audit_record_names_the_web_as_its_originating_component(web, conn, layout):
    """FR-039: which interface did this is answerable from the record alone."""
    item_id = seed_item(conn, state="interrupted")
    web.post_json(f"/item/{item_id}/abandon")
    records = web_records(layout, action="web.abandon")
    assert all(record["component"] == "web" for record in records)
    assert [record["kind"] for record in records] == ["intent", "outcome"]
    assert records[-1]["outcome"] == "ok"


# -- T056: cancel, retry, acknowledge ---------------------------------------


def test_cancel_stops_exactly_one_session_and_leaves_the_others_running(web, conn, layout):
    """SC-008, and FR-050 before it: the process tree of that session and no other."""
    first = seed_item(conn, issue_number=1, state="active")
    second = seed_item(conn, issue_number=2, state="active")
    seed_session(conn, first, state="running", host_socket="/tmp/one.sock")
    seed_session(conn, second, state="running", host_socket="/tmp/two.sock")

    response = web.post_json(f"/item/{first}/cancel")
    assert response.status == 303

    assert state_of(conn, first) == WorkItemState.INTERRUPTED
    assert state_of(conn, second) == WorkItemState.ACTIVE
    assert db.latest_session_for_item(conn, second).state is SessionState.RUNNING
    assert web.host.terminated == [("/tmp/one.sock", None)]


def test_abandon_from_the_web_releases_the_capacity_slot_too(web, conn, config):
    """Issue #28's second route, through the interface rather than the terminal."""
    from robot_army import capacity

    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="running")

    assert web.post_json(f"/item/{item_id}/abandon").status == 303

    assert state_of(conn, item_id) == WorkItemState.ABANDONED
    assert db.latest_session_for_item(conn, item_id).state is SessionState.LOST
    assert capacity.snapshot(conn, config=config).total == 0


def test_cancel_from_the_web_releases_the_capacity_slot_too(web, conn, config):
    """Issue #28. The web routes through the same ``operations.cancel``, so it must not be
    a second way to leak the slot — and the first session's row closing must not disturb
    the second item's, which is still genuinely running."""
    from robot_army import capacity

    first = seed_item(conn, issue_number=1, state="active")
    second = seed_item(conn, issue_number=2, state="active")
    seed_session(conn, first, state="running", host_socket="/tmp/one.sock")
    seed_session(conn, second, state="running", host_socket="/tmp/two.sock")

    assert web.post_json(f"/item/{first}/cancel").status == 303

    assert db.latest_session_for_item(conn, first).state is SessionState.LOST
    assert db.latest_session_for_item(conn, first).ended_at is not None
    assert db.latest_session_for_item(conn, second).state is SessionState.RUNNING
    assert capacity.snapshot(conn, config=config).total == 1


def test_cancel_does_not_touch_the_checkout(web, conn):
    item_id = seed_item(conn, state="active")
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, worktree_path="/w/demo/issue-42")
    seed_session(conn, item_id, state="running")

    web.post_json(f"/item/{item_id}/cancel")
    assert db.get_work_item(conn, item_id).worktree_path == "/w/demo/issue-42"


def test_cancel_needs_no_typed_confirmation_because_http_already_confirmed(web, conn):
    """``force=True`` deliberately: the confirm page already happened, and the terminal
    prompt would have nothing to read from."""
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state="running")
    assert web.post_json(f"/item/{item_id}/cancel").status == 303


def test_a_cancel_that_cannot_confirm_the_stop_is_a_failure_in_the_web_too(web, conn):
    """FR-012: the two surfaces are one behaviour, failure included.

    No change to ``server.py`` is needed for this — ``_report`` already refuses any
    non-``EXIT_OK`` result — which is exactly why it is worth pinning: the property is
    inherited rather than implemented, and inherited properties are the ones that get
    broken by accident.
    """
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state="running")
    web.host.terminate_confirmed = False

    response = web.post_json(f"/item/{item_id}/cancel")

    assert response.status != 303, "a stop that did not happen must not render as 'cancelled'"
    assert state_of(conn, item_id) == WorkItemState.ACTIVE
    assert db.latest_session_for_item(conn, item_id).state is SessionState.RUNNING
    assert "could not confirm" in response.json()["reason"]


def test_retry_is_refused_with_the_reason_while_the_block_still_holds(web, conn):
    """FR-022: the refusal names the condition, because that is what has to be fixed."""
    item_id = seed_item(conn, repo_key="ghost", state="failed")
    response = web.post_json(f"/item/{item_id}/retry")
    assert response.status == 409
    assert "ghost" in response.json()["reason"]
    assert state_of(conn, item_id) == WorkItemState.FAILED


def test_retry_refuses_an_author_rejected_item_through_the_web_too(web, conn, config, monkeypatch):
    """RA-01's worst path. The blocked section of the queue is exactly where an
    author-rejected item appears, and its `retry` control's confirmation used to promise
    the block would be re-verified while `operations.retry` re-checked only the
    repository's own conditions. Both front ends call the same function, which is why
    closing it once closes it everywhere."""
    monkeypatch.setattr(
        operations.dispatch, "is_trusted", lambda path, trust_file=None: (True, "trusted in test")
    )
    item_id = seed_item(conn, state="failed", clone_path=config.repos["demo"].path)
    web.reader.issues = [make_issue(number=42, author="mallory")]

    response = web.post_json(f"/item/{item_id}/retry")

    assert response.status == 409
    reason = response.json()["reason"]
    assert "mallory" in reason and "jantman" in reason
    assert state_of(conn, item_id) == WorkItemState.FAILED


def test_retry_moves_a_failed_item_back_to_ready_when_it_can(web, conn, config, monkeypatch):
    # The gate that would otherwise refuse here is the trust check, which reads the real
    # ~/.claude.json. The web calls `operations.retry` with exactly the arguments the CLI
    # does, so this stands in for a trusted clone rather than changing the call.
    monkeypatch.setattr(
        operations.dispatch, "is_trusted", lambda path, trust_file=None: (True, "trusted in test")
    )
    # The clone location too, since milestone 005: ``check_gates`` re-verifies the
    # recorded path before it re-verifies anything else, and a row without one is the
    # pre-005 shape FR-014 blocks.
    item_id = seed_item(conn, state="failed", clone_path=config.repos["demo"].path)
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, failure_reason="a transient thing")
    # Since issue #119 the retry re-reads the issue and re-runs `poll.evaluate`, so the
    # reader has to know about it. An eligible issue is what "when it can" now means.
    web.reader.issues = [make_issue(number=42)]
    assert web.post_json(f"/item/{item_id}/retry").status == 303
    row = db.get_work_item(conn, item_id)
    assert row.state is WorkItemState.READY
    assert row.failure_reason is None


def test_acknowledging_removes_an_anomaly_from_the_outstanding_count(web, conn):
    with db.transaction(conn):
        db.raise_anomaly(conn, kind="orphan_session", entity_id="s-1", detail={})
    anomaly_id = db.list_anomalies(conn)[0].id
    assert web.get_json("/active").json()["anomaly_count"] == 1

    assert web.post_json(f"/anomalies/{anomaly_id}/acknowledge").status == 303
    assert web.get_json("/active").json()["anomaly_count"] == 0
    assert web.get_json("/anomalies").json()["anomalies"] == []


def test_acknowledging_an_anomaly_that_is_not_outstanding_is_refused(web, conn):
    response = web.post_json("/anomalies/999/acknowledge")
    assert response.status == 409
    assert "999" in response.json()["reason"]


# -- interruption ------------------------------------------------------------


def test_a_transaction_rolled_back_mid_action_leaves_no_partial_state(web, conn, monkeypatch):
    """FR-046: no partially applied state is observable after an interrupted request."""
    item_id = seed_item(conn, state="interrupted")

    def explode(*args, **kwargs):
        raise RuntimeError("phone lost signal mid-request")

    monkeypatch.setattr(operations, "abandon", explode)
    with pytest.raises(RuntimeError):
        web.post_json(f"/item/{item_id}/abandon")

    assert state_of(conn, item_id) == WorkItemState.INTERRUPTED


def test_a_confirmed_action_never_redirects_back_to_its_own_confirm_page(web, conn):
    """The primary success path: tap → confirm → act → land somewhere that makes sense.

    A browser sends the confirm page's own URL as the ``Referer`` of the POST it submits.
    Returning that as the redirect target sent the author back to a page whose whole job is
    to re-validate the action they had just completed — which found it no longer legal and
    rendered a 409. The action succeeded and the page said it failed, which is the worst
    thing this interface could do.
    """
    item_id = seed_item(conn, state="interrupted")
    confirm = f"/item/{item_id}/confirm/abandon"

    response = web.post(
        f"/item/{item_id}/abandon",
        headers={"Referer": f"http://localhost:8420{confirm}"},
    )
    assert response.status == 303
    location = response.headers["Location"]
    assert "/confirm/" not in location
    assert location.startswith(f"/item/{item_id}")

    # And the page the browser actually lands on reports the success.
    landed = web.get(location)
    assert landed.status == 200
    assert "abandoned" in landed.text


@pytest.mark.parametrize("action", ["abandon", "cancel", "retry"])
def test_every_confirmed_action_lands_on_a_200(web, conn, config, monkeypatch, action):
    """The bug hit every confirmed action, so every confirmed action is checked."""
    monkeypatch.setattr(
        operations.dispatch, "is_trusted", lambda path, trust_file=None: (True, "trusted in test")
    )
    state = {"abandon": "interrupted", "cancel": "active", "retry": "failed"}[action]
    item_id = seed_item(conn, state=state, clone_path=config.repos["demo"].path)
    seed_session(conn, item_id, state="running" if state == "active" else "lost")
    # `retry` re-reads its issue since issue #119; the other two never touch the reader.
    web.reader.issues = [make_issue(number=42)]

    response = web.post(
        f"/item/{item_id}/{action}",
        headers={"Referer": f"http://localhost:8420/item/{item_id}/confirm/{action}"},
    )
    assert response.status == 303, action
    assert web.get(response.headers["Location"]).status == 200, action


def test_a_referer_naming_something_that_is_not_a_view_is_ignored(web, conn):
    """An asset, an unknown path, or the root redirect are not places to land."""
    item_id = seed_item(conn, state="interrupted")
    for referer in (
        "http://localhost:8420/static/app.css",
        "http://localhost:8420/nope",
        "http://localhost:8420/",
    ):
        row = seed_item(conn, issue_number=900 + len(referer), state="interrupted")
        response = web.post(f"/item/{row}/abandon", headers={"Referer": referer})
        assert response.headers["Location"].startswith(f"/item/{row}"), referer
    assert db.get_work_item(conn, item_id).state is WorkItemState.INTERRUPTED


# -- 069 T014: a refusal must not arrive in the browser wearing the success banner ------


def test_a_refused_cancel_is_reported_as_a_refusal_not_as_cancelled(web, conn):
    """The route maps a successful cancel to the ``cancelled`` banner — "Session stopped."

    A refusal must not reach that banner. Nothing was stopped and nothing was signalled, so
    telling the maintainer their session is interrupted would be false in both halves. This
    asserts the existing machinery already handles it: ``_report`` refuses on any non-zero
    code, so the refusal travels as its own reason rather than needing a banner of its own.
    """
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state="running")
    web.host.refuse_reason = "the recorded pid is 1, which cannot be a session process"

    response = web.post_json(f"/item/{item_id}/cancel")

    assert response.status == 409
    payload = response.json()
    assert "cannot be a session process" in payload["reason"]
    assert payload["refused"] is True
    assert payload["confirmed"] is False
    assert state_of(conn, item_id) == WorkItemState.ACTIVE
    assert db.latest_session_for_item(conn, item_id).state is SessionState.RUNNING


# -- issue #120: a refusal must be visible in the answer, not only in the log


def test_a_full_machine_refuses_resume_in_the_response_the_author_is_waiting_for(
    web, conn, layout, running_daemon, config, monkeypatch
):
    """FR-015, and the reason the gate is checked twice.

    ``resume`` answers ``303`` at once and prepares the worktree on a worker, because no
    phone holds a request for minutes. That shape means a refusal discovered on the worker
    reaches the author only through the log, while the page shows an item that simply did
    not change — indistinguishable from nothing having happened, which is the failure this
    whole change exists to remove. So the gate runs in the request thread too.
    """
    from robot_army import dispatch, ordering

    beat(layout, effect_level="live")
    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="lost")
    monkeypatch.setattr(
        dispatch,
        "check_launch_gate",
        _refusing(ordering.HoldReason.GLOBAL_CAP, "2 of 2 sessions running (2 ours, 0 other)"),
    )

    response = web.post_json(f"/item/{item_id}/resume")

    assert response.status == 409, "not a cheerful 303 followed by nothing happening"
    body = response.json()
    assert "2 of 2 sessions running" in json.dumps(body)
    assert state_of(conn, item_id) == WorkItemState.INTERRUPTED


def test_a_refused_resume_is_never_handed_to_the_worker(
    web, conn, layout, running_daemon, config, monkeypatch
):
    """The guard sits before ``app.submit``, so a refused action costs no worktree
    preparation and cannot leave the item mid-dispatch."""
    from robot_army import dispatch, ordering

    beat(layout, effect_level="live")
    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="lost")
    monkeypatch.setattr(
        dispatch, "check_launch_gate", _refusing(ordering.HoldReason.PAUSED, "dispatch is paused")
    )
    submitted: list[tuple[str, int]] = []
    monkeypatch.setattr(
        type(web.app), "submit", lambda self, action, item: submitted.append((action, item))
    )

    assert web.post_json(f"/item/{item_id}/restart").status == 409

    assert submitted == []


def test_a_refused_post_still_leaves_the_record_that_says_one_arrived(
    web, conn, layout, running_daemon, config, monkeypatch
):
    """FR-039/FR-040 are unchanged by the new guard: ``_perform`` writes the intent record
    before any check runs, so the refusal closes an existing pair rather than needing one
    of its own."""
    from robot_army import dispatch, ordering

    beat(layout, effect_level="live")
    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="lost")
    monkeypatch.setattr(
        dispatch, "check_launch_gate", _refusing(ordering.HoldReason.HELD, "held since ... by web")
    )

    assert web.post_json(f"/item/{item_id}/resume").status == 409

    written = web_records(layout, action="web.resume")
    assert written, "a refused POST must not be an unrecorded one"
    assert any(record.get("outcome") == "error" for record in written)


def _refusing(hold, detail):
    """A ``check_launch_gate`` that always refuses, so these tests exercise the *guard*.

    The gate's own decisions are tested against a real registry and a real ``/proc`` in
    ``test_launch_gate`` and ``test_dispatch_capacity``; what is under test here is that
    the web calls it, calls it in the request thread, and turns its refusal into an answer.
    """
    from robot_army import dispatch

    def refuse(*_args, **_kwargs):
        raise dispatch.DispatchRefused(detail, hold=hold)

    return refuse


def test_the_web_offers_no_override_of_the_dispatch_gate(web, conn, layout, running_daemon):
    """FR-026. The web's escape hatch is lifting the condition, not overriding it — and
    *Unpause*, *Release hold* and the repository's own release are each one press away.
    Lifting leaves the queue agreeing with the button instead of overridden by it.

    Asserted behaviourally rather than by reading the source: a caller who tries to smuggle
    an override through the form gets nowhere, on both the request-thread guard and the
    worker's own launch.
    """
    beat(layout, effect_level="live")
    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="lost")
    forced: list[bool] = []

    def watch(*_args, force: bool = False, **_kwargs):
        forced.append(force)

    from robot_army import dispatch

    original = dispatch.check_launch_gate
    dispatch.check_launch_gate = watch
    try:
        web.post_json(f"/item/{item_id}/resume", form={"force": "1"})
        web.app._work.join()
    finally:
        dispatch.check_launch_gate = original

    assert forced and not any(forced), "a form field must not become an override"
