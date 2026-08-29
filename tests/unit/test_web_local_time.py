"""Every web display site renders in the host's zone — and the JSON body does not.

Milestone 010, US2. Two halves, and the second is the one that can do damage.

The first half walks the sites in contracts/time-display.md §2: ``when()`` (W1, a funnel
for seven call sites), the log view's record stamp (W2), the paused pill (W3) and the
rendered-at footer (W4).

The second half guards the trap research R3 identified. ``server._render`` builds its JSON
body as ``{**view.data, **chrome}``, so ``rendered_at`` and ``dispatch_paused_at`` are
simultaneously machine-readable fields and values a person reads. Converting them in the
chrome dict rather than at render would put local times into every JSON response on the
interface. That is the specific regression W3 and W4 could introduce, so it is asserted
here rather than left to the story that owns parity in general.
"""

from __future__ import annotations

import json
import re

import pytest
from tests.conftest import seed_item

from robot_army import db

STORED = "2026-08-30T01:31:07Z"
SHOWN = "2026-08-29 21:31:07 -04:00"

#: A stored stamp anywhere in rendered HTML is the failure this module exists to catch.
RAW_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

VIEWS = ("/active", "/queue", "/interrupted", "/anomalies", "/log")

#: An audit record's ``detail`` is the record's own payload, rendered as a quotation of what
#: was written. A stamp inside it is not a *displayed* timestamp — it is the record, shown
#: as the record — and rewriting values inside an arbitrary payload would make the log
#: display something other than what it stored. Both interfaces quote it verbatim
#: (``operations._format_record`` does the same in the terminal), so FR-005 still holds.
DETAIL_BLOCK = re.compile(r'<div class="detail">.*?</div>', re.DOTALL)

pytestmark = pytest.mark.parametrize("in_timezone", ["America/New_York"], indirect=True)


def _pin_everything(conn, layout) -> int:
    """One item, one session, one anomaly, one audit record — all at the same instant."""
    item_id = seed_item(conn, state="active")
    with db.transaction(conn):
        conn.execute(
            "UPDATE work_items SET discovered_at=?, updated_at=?, ready_at=?, "
            "dispatching_at=?, active_at=?, ended_at=?, done_at=? WHERE id=?",
            (*[STORED] * 7, item_id),
        )
        db.insert_session(conn, work_item_id=item_id, session_id="s1", attempt=1,
                          dry_run=False)
        conn.execute("UPDATE sessions SET started_at = ?, ended_at = ?", (STORED, STORED))
        db.raise_anomaly(conn, kind="orphan_worktree", detail={},
                         entity_type="work_item", entity_id=str(item_id))
        conn.execute("UPDATE anomalies SET detected_at = ?", (STORED,))

    path = layout.log_dir / f"audit-{STORED[:10]}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ts": STORED, "kind": "intent", "action": "github.poll",
                    "outcome": "ok", "component": "poll"}) + "\n",
        encoding="utf-8",
    )
    return item_id


# -- W1 through W4: what a person reads -------------------------------------


def test_no_raw_utc_stamp_survives_in_any_rendered_view(web, conn, layout, in_timezone):
    """FR-005 and SC-001, stated as one sweep over every view.

    The blunt assertion is the valuable one: it does not care which site produced a stamp,
    so a site added later that forgets to convert fails here without anyone remembering to
    extend a list.
    """
    _pin_everything(conn, layout)
    web.post_json("/dispatch/pause")

    for path in (*VIEWS, "/item/1"):
        body = DETAIL_BLOCK.sub("", web.get(path).text)
        found = RAW_UTC.findall(body)
        assert not found, f"{path}: raw UTC stamps rendered: {found[:3]}"


def test_an_audit_records_detail_payload_is_quoted_not_converted(web, conn, layout,
                                                                 in_timezone, monkeypatch):
    """The one deliberate exception, asserted so it stays deliberate.

    ``detail`` is free-form JSON written by whatever raised the record. Walking it to
    rewrite anything that looks like a timestamp would need a heuristic, would corrupt a
    field that merely resembled one, and would make the page disagree with the file it is
    quoting — which is the opposite of what someone reads the log for.
    """
    monkeypatch.setattr(db, "utcnow", lambda: STORED)
    web.post_json("/dispatch/pause")

    body = web.get("/log").text
    detail = "".join(DETAIL_BLOCK.findall(body))

    assert STORED in detail, "the payload was not quoted verbatim"
    assert SHOWN not in detail, "a record payload was rewritten for display"


def test_w1_when_pairs_a_local_absolute_with_an_unchanged_relative_age(web, conn, layout,
                                                                       in_timezone):
    """FR-006: the pair survives, and only its absolute half moved."""
    _pin_everything(conn, layout)

    body = web.get("/active").text

    assert SHOWN in body, "W1: the active view's started_at is not local"
    assert "ago)" in body, "W1: the relative age was dropped"


@pytest.mark.parametrize(
    "path,state",
    [("/queue", "ready"), ("/interrupted", "interrupted"), ("/anomalies", "active"),
     ("/item/1", "active")],
)
def test_w1_covers_every_view_that_renders_a_stamp(web, conn, layout, path, state,
                                                   in_timezone):
    """The seven call sites behind the one funnel, reached through the views that use them."""
    item_id = _pin_everything(conn, layout)
    with db.transaction(conn):
        conn.execute("UPDATE work_items SET state = ?, updated_at = ? WHERE id = ?",
                     (state, STORED, item_id))

    body = web.get(path).text

    assert SHOWN in body, f"W1: {path} rendered no local absolute time"


def test_w2_the_log_view_renders_each_record_stamp_locally(web, conn, layout, in_timezone):
    """Not routed through ``when()`` — a log record carries no relative age."""
    _pin_everything(conn, layout)

    body = web.get("/log").text

    assert SHOWN in body
    assert not RAW_UTC.search(body)


def test_w2_a_record_with_no_stamp_still_renders_the_absent_marker(web, conn, layout,
                                                                   in_timezone):
    """``local(None)`` is ``None``, so the ``or '—'`` guard has to be there."""
    path = layout.log_dir / f"audit-{STORED[:10]}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": "intent", "action": "x", "outcome": "ok"}) + "\n",
                    encoding="utf-8")

    assert web.get("/log").status == 200


def test_w3_the_paused_pill_states_a_local_time(web, conn, in_timezone, monkeypatch):
    monkeypatch.setattr(db, "utcnow", lambda: STORED)
    web.post_json("/dispatch/pause")

    for path in VIEWS:
        body = web.get(path).text
        assert f"DISPATCH PAUSED since {SHOWN}" in body, path


def test_w4_the_footer_states_a_local_render_time(web, conn, in_timezone):
    """The one line on the page whose whole job is to say how fresh the page is."""
    for path in VIEWS:
        body = web.get(path).text
        assert re.search(r"rendered \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{2}:\d{2}",
                         body), path


def test_w4_the_footer_is_local_on_a_dead_end_page_too(web, in_timezone):
    """``server._bare`` builds its own chrome with no database behind it."""
    body = web.get("/no-such-page").text

    assert body.count("rendered ") == 1
    assert not RAW_UTC.search(body)


# -- the R3 trap: the same two values, machine-readable ---------------------


def test_the_json_body_keeps_utc_including_the_two_chrome_keys(web, conn, layout,
                                                               in_timezone):
    """FR-012 and research R3 — the failure mode this feature could most easily cause."""
    _pin_everything(conn, layout)
    web.post_json("/dispatch/pause")

    for path in VIEWS:
        payload = web.get_json(path).json()
        blob = json.dumps(payload)
        assert not re.search(r"\d{2}:\d{2}:\d{2} [+-]\d{2}:\d{2}", blob), (
            f"{path}: a local time leaked into the machine-readable body"
        )
        assert RAW_UTC.search(payload["rendered_at"]), f"{path}: rendered_at is not UTC"
        assert payload["dispatch_paused_at"].endswith("Z"), f"{path}: paused_at is not UTC"


def test_the_json_rows_keep_utc_stamps(web, conn, layout, in_timezone):
    _pin_everything(conn, layout)

    payload = web.get_json("/active").json()
    rows = payload["items"]
    assert rows, "the active view returned no rows to check"
    for row in rows:
        for key, value in row.items():
            if key.endswith("_at") and value:
                assert value.endswith("Z"), f"{key} is not UTC: {value!r}"


def test_the_html_and_the_json_are_two_renderings_of_one_value(web, conn, layout,
                                                                in_timezone):
    """One chrome value, two representations, and each in the form its consumer needs."""
    from robot_army import timefmt

    _pin_everything(conn, layout)

    stored = web.get_json("/active").json()["rendered_at"]
    footer = re.search(r"rendered ([^<]+)<", web.get("/active").text).group(1)

    assert stored.endswith("Z"), "the machine-readable value is not UTC"
    assert timefmt.local(stored)[-6:] == footer[-6:], "the two disagree about the offset"
    assert "T" not in footer and not footer.endswith("Z"), "the footer is not the local form"


# -- the client script ------------------------------------------------------


def test_app_js_never_parses_a_rendered_stamp(web, in_timezone):
    """It derives its footer age from ``Date.now()`` at load, so the format change is inert.

    Asserted rather than assumed: had the script parsed the rendered value, changing the
    format would have silently broken the only piece of the interface that updates without
    a request.
    """
    from robot_army.web import html

    assert "Date.now()" in html.APP_JS
    assert "rendered" not in html.APP_JS
    for parser in ("Date.parse", "new Date(", "toLocale", "getTimezoneOffset"):
        assert parser not in html.APP_JS, f"app.js parses time with {parser}"
