"""Every view's payload shape against a seeded database (T026).

The payload *is* the JSON representation and the input to the renderer (R2), so asserting
its shape asserts both at once — and a field the HTML shows that the JSON does not would
mean the two had drifted, which is exactly what one renderer is meant to prevent.
"""

from __future__ import annotations

import pytest
from tests.conftest import beat, seed_item, seed_session

from robot_army import db
from robot_army.web import pages


def test_active_view_reports_everything_fr_011_names(web, conn):
    item_id = seed_item(conn, state="active", title="Fix the thing")
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item_id, worktree_path="/w/demo/issue-42", branch="robot-army/42"
        )
    seed_session(conn, item_id, state="running")

    payload = web.get_json("/active").json()
    assert payload["count"] == 1
    row = payload["items"][0]
    for field in (
        "id",
        "repo_key",
        "issue_number",
        "title",
        "source_url",
        "worktree_path",
        "branch",
        "session_id",
        "session_state",
        "started_at",
        "elapsed_seconds",
    ):
        assert field in row, field
    assert row["session_state"] == "running"
    assert row["elapsed_seconds"] >= 0

    body = web.get("/active").text
    assert "robot-army/42" in body
    assert "/w/demo/issue-42" in body
    assert 'href="https://github.com/x/demo/issues/42"' in body


def test_queue_view_orders_ready_by_dispatch_order(web, conn):
    """Position is the real order ``select_and_dispatch`` uses, not a plausible one."""
    first = seed_item(conn, issue_number=1, state="ready")
    second = seed_item(conn, issue_number=2, state="ready")
    payload = web.get_json("/queue").json()
    assert [row["id"] for row in payload["ready"]] == [first, second]
    assert [row["position"] for row in payload["ready"]] == [1, 2]


def test_queue_view_reports_dispatching_age_against_the_configured_maximum(web, conn):
    item_id = seed_item(conn, state="dispatching")
    conn.execute(
        "UPDATE work_items SET dispatching_at = ? WHERE id = ?",
        ("2020-01-01T00:00:00Z", item_id),
    )
    payload = web.get_json("/queue").json()
    row = payload["dispatching"][0]
    assert row["age_seconds"] > row["max_age_seconds"]
    assert row["overdue"] is True


def test_a_blocked_item_renders_its_specific_reason(web, conn):
    """FR-013: not "blocked", but *why*. A generic label is the thing this replaces."""
    item_id = seed_item(conn, state="failed")
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item_id, failure_reason="repository demo is not onboarded"
        )
    payload = web.get_json("/queue").json()
    assert payload["blocked"][0]["reason"] == "repository demo is not onboarded"
    assert "repository demo is not onboarded" in web.get("/queue").text


def test_an_item_blocked_before_it_ever_became_ready_is_still_shown(web, conn):
    """A ``discovered`` row carrying a blocked_reason must not vanish from every view."""
    item_id = seed_item(conn, state="discovered")
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, blocked_reason="fingerprint changed")
    payload = web.get_json("/queue").json()
    assert [row["id"] for row in payload["blocked"]] == [item_id]


def test_simulated_rows_are_absent_by_default_and_marked_when_asked_for(web, conn):
    """FR-019, asserted on the representation a script sees as well as the page."""
    seed_item(conn, issue_number=1, state="ready")
    seed_item(conn, issue_number=2, state="ready", dry_run=True)

    default = web.get_json("/queue").json()
    assert [row["simulated"] for row in default["ready"]] == [False]

    included = web.get_json("/queue?include_simulated=1").json()
    assert sorted(row["simulated"] for row in included["ready"]) == [False, True]
    assert included["include_simulated"] is True
    assert 'class="sim"' in web.get("/queue?include_simulated=1").text


def test_interrupted_view_carries_the_four_signals_and_their_age(web, conn):
    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="lost")
    payload = web.get_json("/interrupted").json()
    row = payload["items"][0]
    for field in (
        "uncommitted_changes",
        "commits_on_branch",
        "issue_closed",
        "open_pr",
        "signals_age_seconds",
        "worktree_missing",
    ):
        assert field in row, field


def test_interrupted_view_lists_awaiting_review_separately(web, conn):
    """Both are decidable from the couch, and neither may be unreachable in the UI."""
    interrupted = seed_item(conn, issue_number=1, state="interrupted")
    awaiting = seed_item(conn, issue_number=2, state="awaiting_review")
    payload = web.get_json("/interrupted").json()
    assert [row["id"] for row in payload["items"]] == [interrupted]
    assert [row["id"] for row in payload["awaiting_review"]] == [awaiting]


def test_a_missing_checkout_is_surfaced_distinctly(web, conn):
    """001 made ``worktree_missing`` a recoverable state rather than an error (FR-017)."""
    item_id = seed_item(conn, state="interrupted")
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item_id, worktree_path="/definitely/not/here", branch="robot-army/42"
        )
    payload = web.get_json("/interrupted").json()
    assert payload["items"][0]["worktree_missing"] is True
    assert "isolated checkout is missing" in web.get("/interrupted").text


def test_anomalies_view_carries_enough_detail_to_act(web, conn):
    with db.transaction(conn):
        db.raise_anomaly(
            conn,
            kind="orphan_session",
            entity_type="session",
            entity_id="sess-1",
            detail={"pid": 4321, "cwd": "/w/demo"},
        )
    payload = web.get_json("/anomalies").json()
    row = payload["anomalies"][0]
    assert row["kind"] == "orphan_session"
    assert row["detail"] == {"pid": 4321, "cwd": "/w/demo"}
    assert payload["known_kinds"]

    body = web.get("/anomalies").text
    assert "4321" in body
    assert "acknowledge" in body


def test_the_anomaly_count_is_on_every_view(web, conn):
    """FR-017: visible without navigating to a dedicated page."""
    with db.transaction(conn):
        db.raise_anomaly(conn, kind="stale_socket", detail={})
    for path in ("/active", "/queue", "/interrupted", "/log"):
        assert web.get_json(path).json()["anomaly_count"] == 1, path
        assert "1 anomaly" in web.get(path).text, path


def test_item_view_reports_history_and_every_session_attempt(web, conn):
    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="exited_error", exit_code=137, signal=9)
    seed_session(conn, item_id, state="lost")

    payload = web.get_json(f"/item/{item_id}").json()
    assert len(payload["sessions"]) == 2
    assert payload["sessions"][0]["exit_code"] == 137
    assert payload["sessions"][0]["signal"] == 9
    assert payload["history"]

    body = web.get(f"/item/{item_id}").text
    assert "137" in body
    assert "state history" in body


def test_item_view_offers_only_the_actions_that_are_legal(web, conn):
    """FR-029, on the payload and on the page."""
    active = seed_item(conn, issue_number=1, state="active")
    seed_session(conn, active, state="running")
    payload = web.get_json(f"/item/{active}").json()
    assert set(payload["actions"]) == {"cancel", "attach"}
    assert "resume" not in payload["actions"]

    body = web.get(f"/item/{active}").text
    assert f"/item/{active}/confirm/cancel" in body
    assert f"/item/{active}/confirm/resume" not in body


def test_resume_is_not_offered_when_there_is_no_previous_session(web, conn):
    """Resuming restores a prior session's context. With none, the control is a lie."""
    item_id = seed_item(conn, state="interrupted")
    payload = web.get_json(f"/item/{item_id}").json()
    assert "resume" not in payload["actions"]
    assert "restart" in payload["actions"]

    seed_session(conn, item_id, state="lost")
    assert "resume" in web.get_json(f"/item/{item_id}").json()["actions"]


def test_a_missing_item_is_a_404_page(web):
    response = web.get("/item/9999")
    assert response.status == 404
    assert "No work item with id 9999" in response.text


def test_the_chrome_reports_a_running_daemon_and_its_activity(web, layout, running_daemon):
    beat(layout, activity="polling github")
    payload = web.get_json("/active").json()
    assert payload["daemon"]["running"] is True
    assert payload["daemon"]["activity"] == "polling github"
    assert payload["daemon"]["healthy"] is True
    assert "polling github" in web.get("/active").text


def test_with_the_daemon_down_views_render_and_say_so(web, conn):
    """SC-010: 0 loads present stale data as current."""
    seed_item(conn, state="active")
    payload = web.get_json("/active").json()
    assert payload["daemon"]["running"] is False
    assert payload["count"] == 1, "the data is still readable — that is the point"

    body = web.get("/active").text
    assert "DAEMON NOT RUNNING" in body
    assert "not a description of what is happening now" in body


def test_a_stale_heartbeat_is_reported_as_stale(web, layout, running_daemon):
    beat(layout)
    conn_free_payload = web.get_json("/active").json()
    assert conn_free_payload["daemon"]["healthy"] is True

    layout.heartbeat_path.write_text(
        '{"ts":"2020-01-01T00:00:00Z","pid":1,"effect_level":"live","activity":"idle",'
        '"cycles":1}',
        encoding="utf-8",
    )
    stale = web.get_json("/active").json()
    assert stale["daemon"]["healthy"] is False
    assert stale["daemon"]["heartbeat_age_seconds"] > 0
    assert "STALE" in web.get("/active").text


def test_human_age_reads_the_way_a_person_would_say_it():
    assert pages.human_age(0) == "0s"
    assert pages.human_age(45) == "45s"
    assert pages.human_age(90) == "1m 30s"
    assert pages.human_age(3700) == "1h 1m"
    assert pages.human_age(90000) == "1d 1h"
    assert pages.human_age(None) == "—"


def test_concurrent_loads_never_invent_a_running_daemon(web, conn, layout):
    """A regression test for a bug the first real page load produced.

    ``daemon.is_locked`` probes by taking the lock and releasing it. With an *exclusive*
    probe, two requests in flight at once each saw the other's transient hold and reported
    "a daemon is running" — measured at 1,558 false positives in 2,400 probes. A page
    claiming the daemon is alive while it is dead is exactly what SC-010 forbids, and the
    web is the first caller that probes concurrently at all.
    """
    import threading

    from robot_army import daemon as daemon_mod

    assert not daemon_mod.is_locked(layout.lock_path)
    seed_item(conn, state="active")

    claims: list[bool] = []
    guard = threading.Lock()

    def load() -> None:
        for _ in range(25):
            running = web.get_json("/active").json()["daemon"]["running"]
            with guard:
                claims.append(running)

    threads = [threading.Thread(target=load) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert claims, "the loads must actually have happened"
    assert not any(claims), (
        f"{sum(claims)}/{len(claims)} concurrent loads claimed a daemon was running "
        "while none was"
    )


def test_a_real_daemon_is_still_detected_through_the_shared_probe(web, running_daemon):
    """The shared probe must not have traded a false positive for a false negative."""
    assert web.get_json("/active").json()["daemon"]["running"] is True


# -- the queue speaks the dispatcher's vocabulary (milestone 004, T037) -----


def test_the_queue_renders_a_position_and_a_reason_for_every_held_item(web, conn):
    """FR-013 at the level the queue works at: not "held", but *why*, per row, without the
    log. The rows come from ``ordering.plan``, which is the same function the dispatcher
    walks — so the position is the real one by identity rather than by agreement."""
    from dataclasses import replace

    web.app.config = replace(
        web.app.config,
        daemon=replace(web.app.config.daemon, max_concurrent_sessions=1),
    )
    running = seed_item(conn, issue_number=9, state="active")
    seed_session(conn, running, state="running")
    first = seed_item(conn, issue_number=1, state="ready")
    second = seed_item(conn, issue_number=2, state="ready")

    payload = web.get_json("/queue").json()
    rows = {row["id"]: row for row in payload["ready"]}
    assert [rows[first]["position"], rows[second]["position"]] == [1, 2]
    assert rows[first]["hold"] == "global_cap"
    assert "1 of 1 sessions" in rows[first]["hold_detail"]


def test_a_held_items_reason_is_visible_on_the_page_itself(web, conn):
    """"Without consulting the log" is the requirement, so the assertion is against the
    rendered page rather than only against the payload a script would read."""
    from dataclasses import replace

    web.app.config = replace(
        web.app.config,
        daemon=replace(web.app.config.daemon, max_concurrent_sessions=1),
    )
    running = seed_item(conn, issue_number=9, state="active")
    seed_session(conn, running, state="running")
    seed_item(conn, issue_number=1, state="ready")

    text = web.get("/queue").text
    assert "1 of 1 sessions" in text
    assert "ours" in text and "other" in text


def test_the_queue_renders_the_wait_for_merge_hold(web, conn):
    """FR-012's single-source claim, proved on the surface that does not share the
    terminal's rendering code: both read ``ordering.plan``, so a reason that appears in one
    appears in the other by identity rather than by agreement."""
    from dataclasses import replace

    section = replace(web.app.config.repos["demo"], wait_for_merge=True)
    web.app.config = replace(
        web.app.config, repos={**web.app.config.repos, "demo": section}
    )
    seed_item(conn, issue_number=41, state="awaiting_review")
    queued = seed_item(conn, issue_number=1, state="ready")

    payload = web.get_json("/queue").json()
    row = {r["id"]: r for r in payload["ready"]}[queued]
    assert row["hold"] == "awaiting_merge"
    assert "#41" in row["hold_detail"]

    text = web.get("/queue").text
    assert "#41" in text


def test_an_unheld_queue_says_which_item_is_next(web, conn):
    seed_item(conn, issue_number=1, state="ready")
    assert "next to dispatch" in web.get("/queue").text


def test_the_queue_payload_carries_the_capacity_summary(web, conn):
    seed_item(conn, issue_number=1, state="ready")
    payload = web.get_json("/queue").json()
    capacity = payload["capacity"]
    assert capacity["observable"] is True
    assert capacity["global_cap"] == web.app.config.daemon.max_concurrent_sessions
    assert capacity["order"] == "oldest-first"


def test_every_view_carries_the_capacity_pill(web, conn):
    """On every view rather than on the queue alone: "why is nothing running?" is asked
    from wherever the author happens to be looking."""
    seed_item(conn, issue_number=1, state="ready")
    for path in ("/active", "/queue", "/interrupted"):
        text = web.get(path).text
        assert "sessions (" in text, path
        assert "order: oldest-first" in text, path


def test_the_capacity_summary_is_in_every_payload_too(web, conn):
    payload = web.get_json("/queue").json()
    assert "capacity" in payload
    chrome = web.get_json("/active").json()
    assert "capacity" in chrome.get("chrome", chrome)


# -- milestone 009: a partial view says it is partial ------------------------


def _content(body: str) -> str:
    """Just the view, without the chrome.

    The chrome now says "simulated rows hidden" on any page that is withholding them — that
    is the toggle, and it is the point. But it means a bare substring search over the whole
    document can no longer tell the disclosure from the control, so these assertions look at
    the content container the view actually renders into.
    """
    start = body.index('<div id="content">')
    return body[start : body.index("</main>", start)]


WITHHELD_VIEWS = [
    ("/active", "active", "Nothing is running."),
    ("/queue", "ready", "Nothing is ready."),
    ("/interrupted", "interrupted", "Nothing is interrupted."),
]


@pytest.mark.parametrize(("path", "state", "denial"), WITHHELD_VIEWS)
def test_a_view_withholding_everything_does_not_claim_absence(
    web_at, conn, path: str, state: str, denial: str
) -> None:
    """FR-008. "Nothing is ready." and "everything ready is hidden from you" are different
    facts, and reporting the second as the first is the defect one notch quieter."""
    seed_item(conn, issue_number=26, dry_run=True, state=state)
    body = _content(web_at("plan").get(f"{path}?include_simulated=0").text)
    assert denial not in body
    assert "1 simulated row is hidden" in body
    assert "show them" in body


@pytest.mark.parametrize(("path", "state", "denial"), WITHHELD_VIEWS)
def test_a_view_withholding_some_still_discloses(
    web_at, conn, path: str, state: str, denial: str
) -> None:
    """FR-006. Not only when the listing came out empty: two visible rows beneath a six-row
    queue is the same defect, quieter still."""
    seed_item(conn, issue_number=26, dry_run=False, state=state)
    seed_item(conn, issue_number=27, dry_run=True, state=state)
    body = _content(web_at("plan").get(f"{path}?include_simulated=0").text)
    assert "1 simulated row hidden" in body
    assert denial not in body


@pytest.mark.parametrize(("path", "state", "denial"), WITHHELD_VIEWS)
def test_nothing_withheld_and_nothing_present_reads_as_empty(
    web_at, conn, path: str, state: str, denial: str
) -> None:
    """FR-009, and the pair of claims that must never both appear."""
    body = _content(web_at("plan").get(path).text)
    assert denial in body
    # The phrase, not the bare word: `type="hidden"` is on every form field on the page.
    assert "simulated row" not in body


@pytest.mark.parametrize(("path", "state", "denial"), WITHHELD_VIEWS)
def test_nothing_withheld_with_rows_present_says_nothing(
    web_at, conn, path: str, state: str, denial: str
) -> None:
    seed_item(conn, issue_number=26, dry_run=False, state=state)
    body = _content(web_at("plan").get(path).text)
    assert "simulated row" not in body


def test_the_disclosure_is_made_once_not_twice(web_at, conn) -> None:
    """A view discloses in the empty state or beneath its tables, never in both."""
    seed_item(conn, issue_number=26, dry_run=True, state="ready")
    body = _content(web_at("plan").get("/queue?include_simulated=0").text)
    assert body.count("simulated row") == 1


def test_the_interrupted_count_is_the_number_the_link_reveals(web_at, conn) -> None:
    """FR-007. Each section states its own count, and the ``ready`` row is in neither.

    A view-wide count would have named that third row, which "show them" could never
    surface on this page.
    """
    seed_item(conn, issue_number=26, dry_run=True, state="interrupted")
    seed_item(conn, issue_number=27, dry_run=True, state="awaiting_review")
    seed_item(conn, issue_number=28, dry_run=True, state="ready")
    harness = web_at("plan")
    body = _content(harness.get("/interrupted?include_simulated=0").text)
    assert body.count("1 simulated row is hidden") == 2
    assert "3 simulated" not in body
    revealed = harness.get_json("/interrupted").json()
    assert len(revealed["items"]) + len(revealed["awaiting_review"]) == 2


def test_the_cards_payload_reports_what_it_withheld(board_web, conn) -> None:
    """T017: ``operations.cards`` computed this and dropped it on the floor, so the web view
    and ``cards --json`` both had 008's absent-versus-zero ambiguity still in them."""
    from robot_army import db

    with db.transaction(conn):
        db.insert_card(
            conn,
            board_id="board-1",
            card_id="c1",
            card_url="https://trello.com/c/c1",
            title="A card",
            body="",
            dry_run=True,
        )
    hidden = board_web.get_json("/cards?include_simulated=0").json()
    assert hidden["withheld_simulated"] == 1
    assert hidden["cards"] == []
    shown = board_web.get_json("/cards?include_simulated=1").json()
    assert shown["withheld_simulated"] == 0
    assert len(shown["cards"]) == 1


def test_the_queue_counts_only_the_states_it_renders(web_at, conn) -> None:
    """FR-007, and the failure a database-wide count produces.

    ``/queue`` shows ready, dispatching and blocked. A count of every simulated work item
    also names ``active``, ``done``, ``abandoned``, ``interrupted`` and ``awaiting_review``
    rows — so the page offered to reveal four and the link revealed one, which is a subtler
    version of the contradiction 008 removed rather than an improvement on it.
    """
    seed_item(conn, issue_number=1, dry_run=True, state="ready")
    for number, state in ((2, "active"), (3, "done"), (4, "interrupted")):
        seed_item(conn, issue_number=number, dry_run=True, state=state)
    harness = web_at("plan")

    hidden = harness.get_json("/queue?include_simulated=0").json()
    revealed = harness.get_json("/queue").json()
    surfaced = sum(len(revealed[key]) for key in ("ready", "dispatching", "blocked"))
    assert hidden["withheld_simulated"] == surfaced == 1
    assert "1 simulated row is hidden" in _content(
        harness.get("/queue?include_simulated=0").text
    )


def test_a_section_with_rows_beside_one_without_still_tells_the_truth(web_at, conn) -> None:
    """FR-008 at the section level, which is where a view-wide rule leaves a hole.

    ``interrupted`` renders real rows while ``awaiting review`` has only withheld ones. A
    single view-level disclosure is satisfied by the note at the foot of the page — and the
    "Nothing is awaiting review." above it is still a plain claim of absence about rows that
    exist.
    """
    seed_item(conn, issue_number=1, dry_run=False, state="interrupted")
    seed_item(conn, issue_number=2, dry_run=True, state="awaiting_review")
    body = _content(web_at("plan").get("/interrupted?include_simulated=0").text)
    assert "Nothing is awaiting review." not in body
    assert "1 simulated row is hidden" in body


def test_each_withheld_row_is_disclosed_exactly_once(web_at, conn) -> None:
    """The two halves of the rule are disjoint and together they are the whole: an empty
    section carries its own count, and the foot of the page carries the rest."""
    seed_item(conn, issue_number=1, dry_run=False, state="interrupted")
    seed_item(conn, issue_number=2, dry_run=True, state="interrupted")
    seed_item(conn, issue_number=3, dry_run=True, state="awaiting_review")
    body = _content(web_at("plan").get("/interrupted?include_simulated=0").text)
    # One beneath the rendered interrupted cards, one in place of the awaiting empty text.
    assert body.count("1 simulated row") == 2
    assert "2 simulated" not in body


# -- project board state on the queue (issue #48, T038) ----------------------


def _govern(conn, repo_key="demo", **overrides):
    from robot_army.models import RepoProject

    fields = {
        "repo_key": repo_key,
        "project_id": "PVT_3",
        "project_number": 3,
        "project_title": "robot-army",
        "column_name": "Ready",
        "project_source": "discovered",
        "column_source": "discovered",
        "resolved_at": "2026-09-02T00:00:00Z",
        "last_read_at": "2026-09-02T00:00:00Z",
    }
    fields.update(overrides)
    with db.transaction(conn):
        db.save_repo_project(conn, RepoProject(**fields))


def test_the_queue_renders_the_off_column_reason(web, conn):
    item = seed_item(conn, issue_number=1, state="ready")
    _govern(conn)
    conn.execute(
        "UPDATE work_items SET board_column = 'Backlog' WHERE id = ?", (item,)
    )

    page = web.get("/queue").text

    assert "not the dispatch column" in page
    assert "Backlog" in page


def test_the_ready_heading_counts_items_held_off_column(web, conn):
    """FR-030. Without the count, a repository whose whole backlog is parked reads
    exactly like a repository with no work at all."""
    first = seed_item(conn, issue_number=1, state="ready")
    second = seed_item(conn, issue_number=2, state="ready")
    _govern(conn)
    conn.execute(
        "UPDATE work_items SET board_column = 'Backlog' WHERE id IN (?, ?)",
        (first, second),
    )

    page = web.get("/queue").text
    payload = web.get_json("/queue").json()

    assert "2 held off-column" in page
    assert payload["held_off_column"] == 2


def test_a_failed_board_read_is_visible_with_its_age(web, conn):
    """The order shown is still the last one read successfully, which is right — and an
    order silently frozen days ago is indistinguishable from a current one."""
    seed_item(conn, issue_number=1, state="ready")
    _govern(conn, consecutive_failures=2, last_error="GitHub is down")

    page = web.get("/queue").text

    assert "could not be read" in page
    assert "GitHub is down" in page


def test_no_banner_when_every_board_is_healthy(web, conn):
    seed_item(conn, issue_number=1, state="ready")
    _govern(conn)

    page = web.get("/queue").text

    assert "could not be read" not in page
    assert "held off-column" not in page


def test_the_json_body_carries_the_same_board_facts_as_the_page(web, conn):
    seed_item(conn, issue_number=1, state="ready")
    _govern(conn)

    payload = web.get_json("/queue").json()

    row = next(r for r in payload["projects"] if r["repo_key"] == "demo")
    assert row["governs"] is True
    assert row["project_number"] == 3
    assert row["column"] == "Ready"
